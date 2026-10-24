export type Settings = Record<string, any> & {
  ui_language: string;
  theme: string;
  llm_provider: "builtin" | "ollama" | "lmstudio" | "openai";
  builtin_model: string;
  stt_engine: string;
  stt_language: string;
  whisper_size: string;
  first_run_done: boolean;
};

export type Job = { id: string; state: string; done: number; total: number; error: string; speed: number };

export type Status = {
  version: string;
  system: { os: string; arch: string; python: string; data: string; app: boolean };
  settings: Settings;
  models: Record<string, boolean>;
  downloads: Record<string, Job>;
  llm: { ollama: boolean; lmstudio: boolean; builtin: boolean; builtin_running: boolean; effective: string };
  llm_ready: boolean;
  stt_model: string;
  stt_loaded: string;
  embeddings_ready: boolean;
  tts_ready: boolean;
  session: { meeting_id: string; paused: boolean; elapsed: number; sources: string[]; model: string } | null;
  kb: { documents: number; meetings: number; notes: number };
  index: Progress;
  port: number;
  running: Running;
};

/** Indexing progress: files done out of total, the file in hand and a rough time left. */
export type Progress = {
  active: boolean; total: number; done: number; fraction: number; eta: number | null;
  current: { id: string; title: string; phase: "reading" | "embedding"; done: number; total: number } | null;
};

export type Running = { llm: boolean; llm_model: string; stt: boolean; stt_model: string; embeddings: boolean };

export type Model = {
  id: string; kind: string; label: string; size: number; license: string; source: string;
  languages: string[]; note: string; ram_gb: number; installed: boolean; job?: Job | null;
};

export type Block = { cue: string; text: string; auto?: boolean; changed?: number };
export type Summary = {
  title?: string; overview?: string; decisions?: string[]; action_items?: string[];
  topics?: string[]; open_questions?: string[];
};
export type Segment = { id: number; meeting_id?: string; source: string; t0: number; t1: number; text: string; language?: string };
export type Assist = { id: string; question: string; answer: string; sources: any[]; created: number; seen: number };

export type Meeting = {
  id: string; title: string; created: number; started: number | null; ended: number | null;
  status: "draft" | "live" | "done"; language: string; tags: string[]; cues: string;
  enhanced: Block[]; summary: Summary; duration: number;
  segments?: Segment[]; assists?: Assist[];
};

export type Source = {
  id: string; kind: "file" | "folder" | "note" | "meeting"; path: string; parent: string | null; title: string;
  ext: string; tags: string[]; added: number; mtime: number; size: number; chunks: number;
  status: string; error: string; watch: number;
};

export type ChatT = { id: string; title: string; created: number; updated: number; meeting_id: string | null };
export type Message = { id: string; role: "user" | "assistant"; content: string; sources: any[]; created: number };

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["content-type"] = "application/json";
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  status: () => req<Status>("GET", "/api/status"),
  settings: (changes: Partial<Settings>) => req<Settings>("PUT", "/api/settings", changes),
  languages: (engine = "") => req<{ languages: string[]; eu25: string[] }>("GET", `/api/languages?engine_name=${engine}`),
  models: () => req<Model[]>("GET", "/api/models"),
  download: (id: string) => req<Job>("POST", `/api/models/${id}/download`),
  cancelDownload: (id: string) => req("POST", `/api/models/${id}/cancel`),
  removeModel: (id: string) => req("DELETE", `/api/models/${id}`),
  llmModels: (provider: string) => req<{ models: string[] }>("GET", `/api/llm/models?provider=${provider}`),
  llmTest: () => req<{ ok: boolean; text?: string; seconds?: number; error?: string }>("POST", "/api/llm/test"),
  devices: () => req<{ inputs: { id: number; name: string; default: boolean }[] }>("GET", "/api/audio/devices"),

  meetings: (p: { q?: string; tag?: string; since?: number } = {}) =>
    req<Meeting[]>("GET", `/api/meetings?q=${encodeURIComponent(p.q || "")}&tag=${encodeURIComponent(p.tag || "")}&since=${p.since || 0}`),
  createMeeting: (title = "") => req<Meeting>("POST", "/api/meetings", { title, tags: [] }),
  meeting: (id: string) => req<Meeting>("GET", `/api/meetings/${id}`),
  patchMeeting: (id: string, fields: Partial<Meeting>) => req<Meeting>("PATCH", `/api/meetings/${id}`, fields),
  deleteMeeting: (id: string) => req("DELETE", `/api/meetings/${id}`),
  start: (id: string) => req<{ model: string; sources: string[] }>("POST", `/api/meetings/${id}/start`),
  pause: (id: string, paused: boolean) => req("POST", `/api/meetings/${id}/pause`, { paused }),
  stop: (id: string) => req("POST", `/api/meetings/${id}/stop`),
  enhance: (id: string) => req("POST", `/api/meetings/${id}/enhance`),
  summarize: (id: string) => req("POST", `/api/meetings/${id}/summarize`),
  assistSeen: (mid: string, aid: string) => req("POST", `/api/meetings/${mid}/assists/${aid}/seen`),
  importRecording: (file: File) => {
    const f = new FormData();
    f.append("file", file);
    return req<Meeting>("POST", "/api/meetings/import", f);
  },
  tags: () => req<string[]>("GET", "/api/tags"),

  sources: () => req<Source[]>("GET", "/api/sources"),
  addSource: (path: string, watch = true, tags: string[] = []) => req<Source>("POST", "/api/sources", { path, watch, tags }),
  upload: (files: File[]) => {
    const f = new FormData();
    files.forEach((x) => f.append("files", x));
    return req<Source[]>("POST", "/api/sources/upload", f);
  },
  addNote: (title: string, text: string, tags: string[] = []) => req<Source>("POST", "/api/notes", { title, text, tags }),
  patchSource: (id: string, fields: Partial<Source>) => req<Source>("PATCH", `/api/sources/${id}`, fields),
  deleteSource: (id: string) => req("DELETE", `/api/sources/${id}`),
  reindex: (id: string) => req("POST", `/api/sources/${id}/reindex`),
  stopRunning: (name: "llm" | "stt" | "embeddings") => req<{ running: Running }>("POST", `/api/running/${name}/stop`),
  startLlm: () => req<{ running: Running }>("POST", "/api/running/llm/start"),
  reindexAll: () => req("POST", "/api/reindex"),
  deleteData: (what: "chats" | "sources" | "meetings" | "all") =>
    req<{ deleted: Record<string, number>; kb: Status["kb"] }>("DELETE", `/api/data/${what}`),
  reveal: (id: string) => req("POST", "/api/open", { id }),
  openPrivacy: (what: "mic" | "screen") => req("POST", "/api/open-privacy", { what }),
  pick: (type: "file" | "folder") => req<{ path: string }>("POST", "/api/pick", { type }),
  search: (q: string) => req<any[]>("GET", `/api/search?q=${encodeURIComponent(q)}&k=6`),

  chats: () => req<ChatT[]>("GET", "/api/chats"),
  newChat: (meeting_id?: string) => req<ChatT>("POST", "/api/chats", meeting_id ? { meeting_id } : {}),
  chat: (id: string) => req<ChatT & { messages: Message[] }>("GET", `/api/chats/${id}`),
  deleteChat: (id: string) => req("DELETE", `/api/chats/${id}`),
  agents: () => req<{ stdio: { command: string; args: string[] }; http: { url: string; token: string }; data: string }>("GET", "/api/agents"),
  voices: () => req<Record<string, string[]>>("GET", "/api/tts/voices"),
};

/** POST a chat message and read the server-sent events it streams back. */
export async function sendChat(chatId: string, text: string, web: boolean, onEvent: (ev: any) => void) {
  const r = await fetch(`/api/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text, web }),
  });
  if (!r.ok || !r.body) throw new Error(await r.text());
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (chunk.startsWith("data:")) onEvent(JSON.parse(chunk.slice(5)));
    }
  }
}

/** One WebSocket for live events, reconnecting with backoff. */
export function connectLive(onEvent: (ev: any) => void, onState: (up: boolean) => void) {
  let ws: WebSocket | null = null;
  let wait = 500;
  let closed = false;
  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/api/live`);
    ws.onopen = () => {
      wait = 500;
      onState(true);
    };
    ws.onmessage = (e) => onEvent(JSON.parse(e.data));
    ws.onclose = () => {
      onState(false);
      if (!closed) setTimeout(open, (wait = Math.min(wait * 2, 8000)));
    };
  };
  open();
  return () => {
    closed = true;
    ws?.close();
  };
}

export function fmtBytes(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

export function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}
