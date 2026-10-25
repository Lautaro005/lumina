import { useEffect, useRef } from "react";
import { BookOpen, FolderOpen, MessageSquareText, Plus, Settings as Cog, ShieldCheck } from "lucide-react";
import { api, connectLive, fmtClock } from "./api";
import { IndexStatus, Logo, Toasts, UpdateOverlay, UpdateRail } from "./components";
import { useT } from "./i18n";
import { useStore } from "./store";
import Chat from "./views/Chat";
import MeetingView from "./views/Meeting";
import Meetings from "./views/Meetings";
import Onboarding from "./views/Onboarding";
import Settings from "./views/Settings";
import Sources from "./views/Sources";

function useTheme(pref?: string) {
  useEffect(() => {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = pref === "dark" || ((!pref || pref === "system") && mq.matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [pref]);
}

function Rail() {
  const t = useT();
  const route = useStore((s) => s.route);
  const go = useStore((s) => s.go);
  const live = useStore((s) => s.live);
  const toast = useStore((s) => s.toast);
  const section = route.split("/")[1];
  const items = [
    { id: "meetings", icon: BookOpen, label: t("nav.meetings") },
    { id: "sources", icon: FolderOpen, label: t("nav.sources") },
    { id: "chat", icon: MessageSquareText, label: t("nav.chat") },
    { id: "settings", icon: Cog, label: t("nav.settings") },
  ];
  const newMeeting = async () => {
    try {
      const m = await api.createMeeting("");
      go(`/meeting/${m.id}`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const recording = live.state === "recording" || live.state === "paused";
  return (
    <nav className="rail" aria-label="Lumina">
      <a className="brand" href="#/meetings">
        <Logo />
        <span className="brand-word">lumina</span>
      </a>
      <button className="rail-new" onClick={newMeeting}>
        <Plus size={18} strokeWidth={2.5} />
        <span>{t("nav.new")}</span>
      </button>
      <div className="nav">
        {items.map(({ id, icon: Icon, label }) => (
          <button key={id} onClick={() => go(`/${id}`)}
            aria-current={section === id || (id === "meetings" && section === "meeting") ? "page" : undefined}>
            <Icon size={19} />
            <span>{label}</span>
          </button>
        ))}
      </div>
      {/* status cards (recording, indexing, update) sit at the bottom, right above the privacy line */}
      <div className="rail-status">
        {recording && live.meetingId && (
          <button className="rail-live" onClick={() => go(`/meeting/${live.meetingId}`)}>
            <i className={`dot ${live.state === "recording" ? "live" : ""}`} />
            <b>{live.state === "recording" ? t("nav.recording") : t("meeting.paused")}</b>
            <span>{fmtClock(live.elapsed)}</span>
          </button>
        )}
        <IndexStatus compact />
        <UpdateRail />
      </div>
      <div className="rail-foot">
        <ShieldCheck size={16} />
        <span>{t("nav.local")}</span>
      </div>
    </nav>
  );
}

let creating = false;
function NewMeeting() {
  const go = useStore((s) => s.go);
  useEffect(() => {
    if (creating) return;
    creating = true;
    api.createMeeting("").then((m) => go(`/meeting/${m.id}`)).finally(() => { creating = false; });
  }, [go]);
  return null;
}

export default function App() {
  const status = useStore((s) => s.status);
  const route = useStore((s) => s.route);
  const refresh = useStore((s) => s.refresh);
  const onEvent = useStore((s) => s.onEvent);
  const setOnline = useStore((s) => s.setOnline);
  const t = useT();
  useTheme(status?.settings.theme);

  useEffect(() => {
    refresh();
    const stop = connectLive(onEvent, setOnline);
    const timer = setInterval(refresh, 15000);
    return () => {
      stop();
      clearInterval(timer);
    };
  }, [refresh, onEvent, setOnline]);

  useEffect(() => {
    document.documentElement.lang = t.lang;
  }, [t.lang]);

  // the server came back as another version (an update): load the new interface
  const booted = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (!status?.version) return;
    if (!booted.current) booted.current = status.version;
    else if (booted.current !== status.version) location.reload();
  }, [status?.version]);

  if (!status) {
    return (
      <div className="setup">
        <div className="empty"><Logo size={40} onCover={false} /><p>{t("common.loading")}</p></div>
      </div>
    );
  }
  if (!status.settings.first_run_done) return (<><Onboarding /><Toasts /></>);

  const [, section, id] = route.split("/");
  let view;
  if (section === "new") view = <NewMeeting />;
  else if (section === "meeting" && id) view = <MeetingView key={id} id={id} />;
  else if (section === "sources") view = <Sources />;
  else if (section === "chat") view = <Chat chatId={id} />;
  else if (section === "settings") view = <Settings />;
  else view = <Meetings />;

  return (
    <div className="app">
      <Rail />
      <main className="main" id="main">{view}</main>
      <Toasts />
      <UpdateOverlay />
    </div>
  );
}
