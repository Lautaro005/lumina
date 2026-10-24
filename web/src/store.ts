import { create } from "zustand";
import { api, type Assist, type Block, type Job, type Meeting, type Segment, type Status } from "./api";

type Partial_ = { you?: string; others?: string; file?: string };
type Live = {
  meetingId: string | null;
  state: "idle" | "recording" | "paused" | "processing" | "importing";
  elapsed: number;
  levels: { you: number; others: number };
  partial: Partial_;
  enhancing: boolean;
  model: string;
  sources: string[];
  sysDenied: boolean;
};

type Toast = { id: number; text: string; kind: "info" | "warn" | "error" };

type State = {
  status: Status | null;
  online: boolean;
  route: string;
  live: Live;
  meeting: Meeting | null;
  toasts: Toast[];
  refresh: () => Promise<void>;
  go: (route: string) => void;
  setMeeting: (m: Meeting | null) => void;
  toast: (text: string, kind?: Toast["kind"]) => void;
  dropToast: (id: number) => void;
  onEvent: (ev: any) => void;
  setOnline: (up: boolean) => void;
};

const initialLive: Live = {
  meetingId: null, state: "idle", elapsed: 0, levels: { you: 0, others: 0 }, partial: {}, enhancing: false,
  model: "", sources: [], sysDenied: false,
};

let toastId = 0;

export const useStore = create<State>((set, get) => ({
  status: null,
  online: false,
  route: location.hash.slice(1) || "/meetings",
  live: initialLive,
  meeting: null,
  toasts: [],

  refresh: async () => {
    try {
      const status = await api.status();
      set((s) => {
        const live = { ...s.live };
        if (status.session) {
          live.meetingId = status.session.meeting_id;
          live.state = status.session.paused ? "paused" : "recording";
          live.elapsed = status.session.elapsed;
          live.sources = status.session.sources;
          live.model = status.session.model;
        } else if (live.state === "recording" || live.state === "paused") {
          Object.assign(live, initialLive);
        }
        return { status, live };
      });
    } catch {
      /* offline; the socket handles reconnection */
    }
  },

  go: (route) => {
    if (location.hash.slice(1) !== route) location.hash = route;
    set({ route });
  },

  setMeeting: (m) => set({ meeting: m }),

  toast: (text, kind = "info") => {
    const id = ++toastId;
    set((s) => ({ toasts: [...s.toasts.filter((t) => t.text !== text), { id, text, kind }] }));
    setTimeout(() => get().dropToast(id), kind === "error" ? 9000 : 6000);
  },
  dropToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  setOnline: (up) => {
    set({ online: up });
    if (up) get().refresh();
  },

  onEvent: (ev) => {
    const s = get();
    const mine = (id?: string) => !!id && s.meeting?.id === id;
    switch (ev.type) {
      case "download": {
        const job = ev as Job & { type: string };
        set((st) => st.status ? {
          status: {
            ...st.status,
            downloads: { ...st.status.downloads, [job.id]: job },
            models: job.state === "done" ? { ...st.status.models, [job.id]: true } : st.status.models,
          },
        } : {});
        if (job.state === "done" || job.state === "error") get().refresh();
        break;
      }
      case "live": {
        const live = { ...s.live };
        if (ev.state === "recording" || ev.state === "paused") {
          Object.assign(live, { meetingId: ev.meeting_id, state: ev.state, sources: ev.sources || live.sources,
            model: ev.model || live.model, elapsed: ev.offset ?? live.elapsed });
        } else if (ev.state === "processing" || ev.state === "importing") {
          Object.assign(live, { meetingId: ev.meeting_id, state: ev.state, partial: {}, levels: { you: 0, others: 0 } });
        } else if (ev.state === "done") {
          Object.assign(live, initialLive);
          if (mine(ev.meeting_id)) api.meeting(ev.meeting_id).then((m) => get().setMeeting(m));
        }
        set({ live });
        get().refresh();
        break;
      }
      case "tick":
        if (s.live.meetingId === ev.meeting_id) set({ live: { ...s.live, elapsed: ev.elapsed } });
        break;
      case "level":
        set({ live: { ...s.live, levels: { ...s.live.levels, [ev.source]: ev.level } } });
        break;
      case "partial":
        set({ live: { ...s.live, partial: { ...s.live.partial, [ev.source]: ev.text } } });
        break;
      case "segment":
        set({ live: { ...s.live, partial: { ...s.live.partial, [ev.source]: "" } } });
        if (mine(ev.meeting_id) && s.meeting) {
          const seg: Segment = { id: ev.id, source: ev.source, t0: ev.t0, t1: ev.t1, text: ev.text };
          set({ meeting: { ...s.meeting, segments: [...(s.meeting.segments || []), seg] } });
        }
        break;
      case "enhancing":
        set({ live: { ...s.live, enhancing: ev.state === "start" } });
        if (ev.state === "error") get().toast(ev.text, "warn");
        break;
      case "enhanced":
        set({ live: { ...s.live, enhancing: false } });
        if (mine(ev.meeting_id) && s.meeting) set({ meeting: { ...s.meeting, enhanced: ev.blocks as Block[] } });
        break;
      case "summary":
        if (mine(ev.meeting_id) && s.meeting)
          set({ meeting: { ...s.meeting, summary: ev.summary, title: ev.title || s.meeting.title } });
        break;
      case "assist":
        if (mine(ev.meeting_id) && s.meeting)
          set({ meeting: { ...s.meeting, assists: [ev.assist as Assist, ...(s.meeting.assists || [])] } });
        break;
      case "sysaudio":
        set({ live: { ...s.live, sysDenied: !ev.ok, sources: s.live.sources.filter((x) => x !== "others") } });
        break;
      case "warning":
        get().toast(ev.text, "warn");
        break;
      case "kb":
        if (ev.event === "progress") {
          const { type: _t, event: _e, ...index } = ev;
          set((st) => (st.status ? { status: { ...st.status, index } } : {}));
        } else if (ev.event === "counts") {
          set((st) => (st.status ? { status: { ...st.status, kb: { documents: ev.documents, meetings: ev.meetings, notes: ev.notes } } } : {}));
        } else {
          window.dispatchEvent(new CustomEvent("lumina:kb", { detail: ev }));
        }
        break;
    }
  },
}));

window.addEventListener("hashchange", () => useStore.setState({ route: location.hash.slice(1) || "/meetings" }));
