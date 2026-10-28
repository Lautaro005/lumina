import { useEffect, useState } from "react";
import { CheckCircle2, Columns2, Download, Info, Loader2, Play, Rows3, RefreshCw, Square, StickyNote, Trash2 } from "lucide-react";
import { api, type Integrations, type Job, type Model, type UpdateInfo } from "../api";
import { ConfirmDialog, CopyButton, ModelRow, PermissionsPanel, Switch, useModels, useSetting } from "../components";
import { langName, useT } from "../i18n";
import { useStore } from "../store";

const EU25 = ["bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "ru", "sk", "sl", "es", "sv", "uk"];

export function sttFor(engine: string, language: string, size: string): string {
  if (engine === "parakeet") return "parakeet-v3";
  if (engine === "canary") return "canary-1b-v2";
  if (engine === "whisper") return `whisper-${size}`;
  if (engine === "embeddinggemma") return "embeddinggemma-2-stt";
  if (!language || language === "auto") return "parakeet-v3";
  return EU25.includes(language) ? "canary-1b-v2" : `whisper-${size}`;
}

export function LanguageSelect({ id }: { id?: string }) {
  const t = useT();
  const [engine] = useSetting<string>("stt_engine");
  const [lang, setLang] = useSetting<string>("stt_language");
  const [langs, setLangs] = useState<string[]>([]);
  useEffect(() => { api.languages(engine).then((r) => setLangs(r.languages)).catch(() => {}); }, [engine]);
  const sorted = [...langs].sort((a, b) => langName(a, t.lang).localeCompare(langName(b, t.lang), t.lang));
  const common = ["es", "en", "pt", "fr", "de", "it"].filter((x) => langs.includes(x));
  return (
    <select id={id} className="input" value={lang} onChange={(e) => setLang(e.target.value)}>
      {engine !== "canary" && engine !== "embeddinggemma" && <option value="auto">{t("common.auto")}</option>}
      <optgroup label="—">
        {common.map((c) => <option key={`c-${c}`} value={c}>{langName(c, t.lang)}</option>)}
      </optgroup>
      <optgroup label={`${sorted.length}`}>
        {sorted.map((c) => <option key={c} value={c}>{langName(c, t.lang)}</option>)}
      </optgroup>
    </select>
  );
}

const VIEWS = ["columns", "rows", "tabs"] as const;

/** A small drawing of each layout so the choice is visible at a glance. */
function ViewThumb({ kind }: { kind: (typeof VIEWS)[number] }) {
  const ink = "var(--ink-3)", wash = "var(--blue-wash)", rule = "var(--rule)", red = "var(--margin)";
  const lines = (x: number, y: number, w: number, n: number, c: string) =>
    Array.from({ length: n }, (_, i) => <rect key={`${x}-${y}-${i}`} x={x} y={y + i * 7} width={i % 3 === 2 ? w * 0.6 : w} height="3" rx="1.5" fill={c} opacity="0.7" />);
  return (
    <svg className="view-thumb" viewBox="0 0 120 80" role="img" aria-hidden="true">
      <rect x="1" y="1" width="118" height="78" rx="6" fill="var(--paper)" stroke="var(--rule-soft)" />
      <rect x="1" y="1" width="118" height="12" rx="6" fill={wash} />
      <rect x="8" y="5" width="30" height="3" rx="1.5" fill={rule} />
      {kind === "columns" && (
        <>
          <line x1="60" y1="13" x2="60" y2="79" stroke="var(--rule-soft)" />
          {lines(8, 20, 44, 6, ink)}
          <rect x="68" y="19" width="12" height="3" rx="1.5" fill={rule} />
          {lines(68, 26, 44, 5, ink)}
        </>
      )}
      {kind === "rows" && (
        <>
          <line x1="42" y1="13" x2="42" y2="79" stroke={red} strokeWidth="1.5" />
          {[0, 1, 2].map((i) => (
            <g key={i}>
              <rect x="8" y={21 + i * 19} width="26" height="3" rx="1.5" fill={rule} />
              {lines(50, 19 + i * 19, 60, 2, ink)}
              {i < 2 && <line x1="4" y1={34 + i * 19} x2="116" y2={34 + i * 19} stroke="var(--rule-soft)" />}
            </g>
          ))}
        </>
      )}
      {kind === "tabs" && (
        <>
          <rect x="8" y="18" width="40" height="9" rx="4.5" fill={wash} stroke={rule} />
          <rect x="52" y="18" width="40" height="9" rx="4.5" fill="none" stroke="var(--rule-soft)" />
          {lines(8, 36, 104, 6, ink)}
        </>
      )}
    </svg>
  );
}

function NotesViewPicker() {
  const t = useT();
  const [view, setView] = useSetting<string>("notes_view");
  const icons = { columns: Columns2, rows: Rows3, tabs: StickyNote } as const;
  return (
    <div className="field">
      <span className="label" id="nv-label">{t("settings.notesView")}</span>
      <div className="view-picker" role="radiogroup" aria-labelledby="nv-label">
        {VIEWS.map((v) => {
          const Icon = icons[v];
          return (
            <button key={v} type="button" role="radio" aria-checked={view === v} className="view-card" onClick={() => setView(v)}>
              <ViewThumb kind={v} />
              <b><Icon size={15} /> {t(`settings.view.${v}`)}</b>
              <small>{t(`settings.view.${v}Hint` as "settings.view.columnsHint")}</small>
            </button>
          );
        })}
      </div>
      <p className="hint">{t("settings.notesViewHint")}</p>
    </div>
  );
}

/** What is loaded in memory, with a Stop button for each (the notes model, the speech model, the search model). */
function RunningModels() {
  const t = useT();
  const status = useStore((s) => s.status)!;
  const refresh = useStore((s) => s.refresh);
  const toast = useStore((s) => s.toast);
  const [busy, setBusy] = useState("");
  const r = status.running;
  const builtin = status.llm.effective === "builtin";
  const act = async (name: "llm" | "stt" | "embeddings", start = false) => {
    setBusy(name + (start ? "+" : ""));
    try {
      if (start) await api.startLlm();
      else await api.stopRunning(name);
      toast(start ? t("settings.started") : t("settings.stopped"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy("");
      refresh();
    }
  };
  const rows: { id: "llm" | "stt" | "embeddings"; on: boolean; detail: string; external?: boolean }[] = [
    { id: "llm", on: r.llm, detail: r.llm ? r.llm_model : "", external: !builtin },
    { id: "stt", on: r.stt, detail: r.stt_model },
    { id: "embeddings", on: r.embeddings, detail: "" },
  ];
  return (
    <div className="running" role="group" aria-label={t("settings.running")}>
      <h3>{t("settings.running")}</h3>
      <p className="hint">{t("settings.runningLead")}</p>
      {rows.map((x) => (
        <div className="run-row" key={x.id}>
          <span className={`run-dot ${x.on ? "on" : ""}`} aria-hidden="true" />
          <div className="run-name">
            <b>{t(`settings.run.${x.id}` as "settings.run.llm")}</b>
            <small>{x.external ? t("settings.run.external") : [x.on ? t("settings.run.on") : t("settings.run.off"), x.detail].filter(Boolean).join(" · ")}</small>
          </div>
          {x.on && !x.external && (
            <button className="btn sm" disabled={!!busy} onClick={() => act(x.id)}>
              {busy === x.id ? <Loader2 size={14} className="spin" /> : <Square size={13} fill="currentColor" />} {t("settings.stopModel")}
            </button>
          )}
          {!x.on && x.id === "llm" && builtin && status.llm.builtin && (
            <button className="btn sm ghost" disabled={!!busy} onClick={() => act("llm", true)}>
              {busy === "llm+" ? <Loader2 size={14} className="spin" /> : <Play size={14} />} {t("settings.startModel")}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

const DELETES = ["chats", "sources", "meetings", "all"] as const;

/** Wipes chats, sources, meetings or all of them, after a confirmation. */
function UpdatePanel() {
  const t = useT();
  const toast = useStore((s) => s.toast);
  const refresh = useStore((s) => s.refresh);
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [ask, setAsk] = useState(false);
  const [auto, setAuto] = useSetting<boolean>("update_check");
  useEffect(() => { api.update().then(setInfo).catch(() => {}); }, []);
  const check = async () => {
    setBusy(true);
    try { setInfo(await api.checkUpdate()); } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(false); }
  };
  const run = async () => {
    setBusy(true);
    try { await api.applyUpdate(); await refresh(); } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(false); setAsk(false); }
  };
  return (
    <div className="update-panel">
      <div className="update-line">
        <div>
          <b>{!info ? "…" : info.available ? t("update.available", { v: info.latest }) : info.error ? t("update.cantCheck") : t("update.upToDate")}</b>
          <small>
            {t("update.current", { v: info?.current ?? "" })}
            {info && info.checked > 0 && ` · ${t("update.checked", { when: new Date(info.checked * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) })}`}
          </small>
          {info?.state === "failed" && <small className="bad">{t("update.failed")}</small>}
          {info?.error && <small className="bad">{info.error}</small>}
        </div>
        <div className="actions">
          <button className="btn" onClick={check} disabled={busy}>{t("update.check")}</button>
          {info?.available && <button className="btn primary" onClick={() => setAsk(true)} disabled={busy || !info.can_update}><Download size={15} /> {t("update.cta", { v: info.latest })}</button>}
        </div>
      </div>
      {info?.available && !info.can_update && <p className="hint">{t("update.manual")}</p>}
      <Switch checked={!!auto} onChange={setAuto} label={t("update.auto")} />
      {ask && info && (
        <ConfirmDialog title={t("update.confirmTitle", { v: info.latest })} body={t("update.confirmBody")}
          confirm={t("update.now")} cancel={t("common.cancel")} busy={busy} onConfirm={run} onCancel={() => setAsk(false)} />
      )}
    </div>
  );
}

function DeleteData() {
  const t = useT();
  const toast = useStore((s) => s.toast);
  const refresh = useStore((s) => s.refresh);
  const [ask, setAsk] = useState<(typeof DELETES)[number] | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!ask) return;
    setBusy(true);
    try {
      await api.deleteData(ask);
      toast(t("settings.delete.done"));
      window.dispatchEvent(new CustomEvent("lumina:kb"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
      setAsk(null);
      refresh();
    }
  };
  return (
    <>
      <p className="lead" style={{ marginTop: 0 }}>{t("settings.deleteLead")}</p>
      <div className="danger-list">
        {DELETES.map((d) => (
          <div key={d} className={`danger-row ${d === "all" ? "all" : ""}`}>
            <div>
              <b>{t(`settings.delete.${d}` as "settings.delete.all")}</b>
              <small>{t(`settings.delete.${d}Hint` as "settings.delete.allHint")}</small>
            </div>
            <button className="btn danger" onClick={() => setAsk(d)}><Trash2 size={15} /> {t("common.delete")}</button>
          </div>
        ))}
      </div>
      {ask && (
        <ConfirmDialog busy={busy} onConfirm={run} onCancel={() => setAsk(null)}
          title={t("settings.delete.title", { what: t(`settings.delete.what.${ask}` as "settings.delete.what.all") })}
          body={t("settings.delete.body", { what: t(`settings.delete.what.${ask}` as "settings.delete.what.all") })}
          confirm={t("common.delete")} cancel={t("common.cancel")} />
      )}
    </>
  );
}

const GROUPS: { id: string; kinds: string[] }[] = [
  { id: "chat", kinds: ["llm", "runtime"] },
  { id: "stt", kinds: ["stt", "vad"] },
  { id: "embedding", kinds: ["embedding"] },
  { id: "tts", kinds: ["tts"] },
];

/** Every model, grouped by what it is used for. */
function ModelGroups({ models, jobs }: { models: Model[]; jobs: Record<string, Job> }) {
  const t = useT();
  const have = useStore((s) => s.status?.models) || {};
  return (
    <div className="model-groups">
      {GROUPS.map((g) => {
        const list = models.filter((m) => g.kinds.includes(m.kind));
        if (!list.length) return null;
        const n = list.filter((m) => have[m.id]).length;
        return (
          <section key={g.id} className="model-group" aria-labelledby={`mg-${g.id}`}>
            <header>
              <h3 id={`mg-${g.id}`}>{t(`settings.group.${g.id}` as "settings.group.chat")}</h3>
              <span className="chip">{t("settings.groupCount", { n, total: list.length })}</span>
            </header>
            <p className="hint">{t(`settings.group.${g.id}Hint` as "settings.group.chatHint")}</p>
            <div className="models">{list.map((m) => <ModelRow key={m.id} model={m} job={jobs[m.id]} />)}</div>
          </section>
        );
      })}
    </div>
  );
}

/** brain, OpenCue and AI WorkStudio: what is installed and open, and what Lumina does with each one. */
function ConnectedApps() {
  const t = useT();
  const toast = useStore((s) => s.toast);
  const [apps, setApps] = useState<Integrations | null>(null);
  const [brainChat, setBrainChat] = useSetting<boolean>("brain_chat");
  const [brainAuto, setBrainAuto] = useSetting<boolean>("brain_autosave");
  const [brainTr, setBrainTr] = useSetting<boolean>("brain_transcript");
  useEffect(() => {
    const load = () => api.integrations().then(setApps).catch(() => {});
    load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, []);
  if (!apps) return null;
  const open = (id: "brain" | "opencue" | "workstudio") => api.openApp(id).catch((e) => toast((e as Error).message, "error"));
  const state = (a: { installed: boolean; running?: boolean }) =>
    a.running ? <span className="chip ok">{t("apps.running")}</span>
      : a.installed ? <span className="chip">{t("apps.installed")}</span>
      : <span className="chip">{t("apps.missing")}</span>;
  const action = (id: "brain" | "opencue" | "workstudio", installed: boolean, site: string) => installed
    ? <button className="btn sm" onClick={() => open(id)}>{t("apps.open")}</button>
    : <a className="btn sm" href={site} target="_blank" rel="noopener">{t("apps.install")}</a>;
  return (
    <div className="app-links">
      <div className="app-link">
        <header>
          <span className="app-mark brain" aria-hidden="true">b</span>
          <div><b>brain</b>{apps.brain.version && <small> · {apps.brain.version}</small>}<p>{t("apps.brain")}</p></div>
          <span className="app-act">{state(apps.brain)}{action("brain", apps.brain.installed, "https://lautaro005.github.io/brain/")}</span>
        </header>
        {apps.brain.installed && (
          <div className="app-opts">
            <Switch checked={brainChat !== false} onChange={setBrainChat} label={t("apps.brainChat")} />
            <Switch checked={!!brainAuto} onChange={setBrainAuto} label={t("apps.brainAuto")} />
            <Switch checked={!!brainTr} onChange={setBrainTr} label={t("apps.brainTranscript")} />
            <p className="hint">{t("apps.brainWhere")}</p>
          </div>
        )}
      </div>
      <div className="app-link">
        <header>
          <span className="app-mark opencue" aria-hidden="true">o</span>
          <div><b>OpenCue</b>{apps.opencue.agents.length > 0 && <small> · {t("apps.agents", { n: apps.opencue.agents.length })}</small>}<p>{t("apps.opencue")}</p></div>
          <span className="app-act">{state(apps.opencue)}{action("opencue", apps.opencue.installed, "https://lautaro005.github.io/opencue/")}</span>
        </header>
      </div>
      <div className="app-link">
        <header>
          <span className="app-mark workstudio" aria-hidden="true">w</span>
          <div><b>AI WorkStudio</b><p>{t("apps.workstudio")}</p>
            {apps.workstudio.installed && <p className="hint">{apps.workstudio.hub ? t("apps.hubOn") : t("apps.hubOff")}</p>}</div>
          <span className="app-act">{state(apps.workstudio)}{action("workstudio", apps.workstudio.installed, "https://lautaro005.github.io/workstudio/")}</span>
        </header>
      </div>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section className="section" id={id}>
      <h2>{title}</h2>
      <div className="section-body">{children}</div>
    </section>
  );
}

export default function Settings() {
  const t = useT();
  const status = useStore((s) => s.status)!;
  const models = useModels();
  const byId = (id: string) => models.find((m) => m.id === id);
  const jobs = status.downloads;
  const row = (m?: Model) => (m ? <ModelRow key={m.id} model={m} job={jobs[m.id]} /> : null);

  const [ui, setUi] = useSetting<string>("ui_language");
  const [theme, setTheme] = useSetting<string>("theme");
  const [engine, setEngine] = useSetting<string>("stt_engine");
  const [lang, setLang] = useSetting<string>("stt_language");
  const [wsize, setWsize] = useSetting<string>("whisper_size");
  const [mic, setMic] = useSetting<number | string | null>("mic_device");
  const [sysAudio, setSysAudio] = useSetting<boolean>("system_audio");
  const [partials, setPartials] = useSetting<boolean>("partials");
  const [autoEnh, setAutoEnh] = useSetting<boolean>("auto_enhance");
  const [assist, setAssist] = useSetting<boolean>("live_assistance");
  const [assistWeb, setAssistWeb] = useSetting<boolean>("assist_web");
  const [notch, setNotch] = useSetting<boolean>("notch_live");
  const [saver, setSaver] = useSetting<boolean>("memory_saver");
  const [provider, setProvider] = useSetting<string>("llm_provider");
  const [gemma, setGemma] = useSetting<string>("builtin_model");
  const [ollamaUrl, setOllamaUrl] = useSetting<string>("ollama_url");
  const [ollamaModel, setOllamaModel] = useSetting<string>("ollama_model");
  const [lmsUrl, setLmsUrl] = useSetting<string>("lmstudio_url");
  const [lmsModel, setLmsModel] = useSetting<string>("lmstudio_model");
  const [oaiUrl, setOaiUrl] = useSetting<string>("openai_url");
  const [oaiModel, setOaiModel] = useSetting<string>("openai_model");
  const [oaiKey, setOaiKey] = useSetting<string>("openai_key");
  const [dims, setDims] = useSetting<number>("embedding_dim");
  const [searchProv, setSearchProv] = useSetting<string>("search_provider");
  const [searx, setSearx] = useSetting<string>("searxng_url");
  const [webDefault, setWebDefault] = useSetting<boolean>("web_search");
  const [ttsEngine, setTtsEngine] = useSetting<string>("tts_engine");
  const [ttsVoice, setTtsVoice] = useSetting<string>("tts_voice");

  const [devices, setDevices] = useState<{ id: number | string; name: string; default: boolean }[]>([]);
  const [remote, setRemote] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; text?: string; seconds?: number; error?: string } | "busy" | null>(null);
  const [agents, setAgents] = useState<Awaited<ReturnType<typeof api.agents>> | null>(null);
  const [voices, setVoices] = useState<Record<string, string[]>>({});

  useEffect(() => { api.devices().then((d) => setDevices(d.inputs)).catch(() => {}); api.agents().then(setAgents).catch(() => {}); api.voices().then(setVoices).catch(() => {}); }, []);
  const loadRemote = () => {
    if (provider === "builtin") return;
    setLoadingModels(true);
    api.llmModels(provider).then((r) => setRemote(r.models)).finally(() => setLoadingModels(false));
  };
  useEffect(loadRemote, [provider]);

  const chosen = sttFor(engine, lang, wsize);
  const runTest = async () => {
    setTest("busy");
    setTest(await api.llmTest());
  };

  const stdioJson = agents ? JSON.stringify({ mcpServers: { lumina: { command: agents.stdio.command, args: agents.stdio.args } } }, null, 2) : "";
  const claudeCmd = agents ? `claude mcp add lumina -- ${agents.stdio.command} mcp` : "";
  const httpJson = agents ? JSON.stringify({ mcpServers: { lumina: { url: agents.http.url, headers: { Authorization: `Bearer ${agents.http.token}` } } } }, null, 2) : "";

  const isMac = status.system.os === "Darwin";
  const nav: [string, string][] = [
    ["general", t("settings.general")], ...(isMac ? [["permissions", t("perm.title")] as [string, string]] : []), ["transcription", t("settings.transcription")], ["generation", t("settings.generation")],
    ["knowledge", t("settings.knowledge")], ["speech", t("settings.speech")], ["models", t("settings.modelsNav")],
    ["apps", t("apps.title")], ["agents", t("settings.agents")], ["delete", t("settings.delete")], ["about", t("settings.about")],
  ];

  return (
    <div className="view">
      <div className="view-head"><h1>{t("settings.title")}</h1></div>
      <div className="settings">
        <nav className="settings-nav">{nav.map(([id, label]) => <a key={id} href={`#/settings`} onClick={(e) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }); }}>{label}</a>)}</nav>
        <div className="settings-body">
          <Section id="general" title={t("settings.general")}>
            <div className="grid2">
              <div className="field">
                <label htmlFor="ui">{t("settings.uiLanguage")}</label>
                <select id="ui" className="input" value={ui} onChange={(e) => setUi(e.target.value)}>
                  <option value="auto">{t("common.auto")}</option>
                  <option value="en">English</option>
                  <option value="es">Español</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="theme">{t("settings.theme")}</label>
                <select id="theme" className="input" value={theme} onChange={(e) => setTheme(e.target.value)}>
                  <option value="system">{t("settings.themeSystem")}</option>
                  <option value="light">{t("settings.themeLight")}</option>
                  <option value="dark">{t("settings.themeDark")}</option>
                </select>
              </div>
            </div>
            <NotesViewPicker />
            <Switch checked={!!saver} onChange={setSaver} label={t("settings.memorySaver")} />
            <p className="hint" style={{ marginLeft: 50 }}>{t("settings.memorySaverHint")}</p>
          </Section>

          {isMac && (
            <Section id="permissions" title={t("perm.title")}>
              <p className="hint">{t("perm.lead")}</p>
              <PermissionsPanel />
            </Section>
          )}

          <Section id="transcription" title={t("settings.transcription")}>
            <div className="grid2">
              <div className="field">
                <label htmlFor="stt-lang">{t("settings.sttLanguage")}</label>
                <LanguageSelect id="stt-lang" />
                <p className="hint">{t("settings.sttLanguageHint")}</p>
              </div>
              <div className="field">
                <label htmlFor="stt-engine">{t("settings.sttEngine")}</label>
                <select id="stt-engine" className="input" value={engine} onChange={(e) => {
                  const v = e.target.value;
                  setEngine(v);
                  if (v === "embeddinggemma") setLang("en");
                  if (v === "canary" && lang === "auto") setLang(t.lang);
                  if ((v === "parakeet" || v === "canary") && lang !== "auto" && !EU25.includes(lang)) setLang(v === "canary" ? "en" : "auto");
                }}>
                  {(["auto", "parakeet", "canary", "whisper", "embeddinggemma"] as const).map((x) => <option key={x} value={x}>{t(`settings.engine.${x}`).split(":")[0]}</option>)}
                </select>
                <p className="hint">{t(`settings.engine.${engine as "auto"}`)}</p>
              </div>
              {(engine === "whisper" || (engine === "auto" && lang !== "auto" && !EU25.includes(lang))) && (
                <div className="field">
                  <label htmlFor="wsize">{t("settings.whisperSize")}</label>
                  <select id="wsize" className="input" value={wsize} onChange={(e) => setWsize(e.target.value)}>
                    {["tiny", "base", "small", "medium", "large-v3-turbo"].map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                </div>
              )}
            </div>
            {engine === "embeddinggemma" && <div className="callout warn"><Info size={18} />{t("settings.experimentalNote")}</div>}
            {engine === "parakeet" && lang !== "auto" && <div className="callout warn"><Info size={18} />{t("settings.parakeetNote")}</div>}
            <div className="callout"><Info size={18} /><span>{t("settings.willUse")} <b>{byId(chosen)?.label || chosen}</b>.</span></div>
            <div className="models">{row(byId(chosen))}{row(byId("silero-vad"))}</div>
            <div className="grid2">
              <div className="field">
                <label htmlFor="mic">{t("settings.mic")}</label>
                <select id="mic" className="input" value={mic ?? ""} onChange={(e) => setMic(e.target.value === "" ? null : e.target.value)}>
                  <option value="">{t("settings.micDefault")}</option>
                  {devices.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
            </div>
            <div style={{ display: "grid", gap: 14 }}>
              <div><Switch checked={!!sysAudio} onChange={setSysAudio} label={t("settings.systemAudio")} /><p className="hint" style={{ marginLeft: 50 }}>{t("settings.systemAudioHint")}</p></div>
              <Switch checked={!!partials} onChange={setPartials} label={t("settings.partials")} />
              <Switch checked={!!autoEnh} onChange={setAutoEnh} label={t("settings.autoEnhance")} />
              <Switch checked={!!assist} onChange={setAssist} label={t("settings.assist")} />
              <div>
                <Switch checked={!!assistWeb} onChange={setAssistWeb} label={t("settings.assistWeb")} />
                <p className="hint" style={{ marginLeft: 50 }}>{t("settings.assistWebHint")}</p>
              </div>
              <div>
                <Switch checked={!!notch} onChange={setNotch} label={t("settings.notch")} />
                <p className="hint" style={{ marginLeft: 50 }}>{t("settings.notchHint")}{!status.system.app && <> {t("settings.notchBrowser")}</>}</p>
              </div>
            </div>
          </Section>

          <Section id="generation" title={t("settings.generation")}>
            <div className="field">
              <label htmlFor="provider">{t("settings.provider")}</label>
              <select id="provider" className="input" style={{ maxWidth: 420 }} value={provider} onChange={(e) => setProvider(e.target.value)}>
                {(["builtin", "ollama", "lmstudio", "openai"] as const).map((p) => (
                  <option key={p} value={p}>{t(`settings.provider.${p}`)}{p !== "builtin" && p !== "openai" && status.llm[p] ? ` · ${t("setup.detected")}` : ""}</option>
                ))}
              </select>
            </div>
            {provider === "builtin" && (
              <>
                <div className="field">
                  <label htmlFor="gemma">{t("settings.gemmaSize")}</label>
                  <select id="gemma" className="input" style={{ maxWidth: 420 }} value={gemma} onChange={(e) => setGemma(e.target.value)}>
                    {["gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b"].map((g) => {
                      const m = byId(g);
                      return <option key={g} value={g}>{m ? `${m.label} · ${(m.size / 1e9).toFixed(1)} GB${status.models[g] ? " ✓" : ""}` : g}</option>;
                    })}
                  </select>
                  <p className="hint">{byId(gemma)?.note}</p>
                </div>
                <div className="models">{row(byId(gemma))}{row(byId("llama-server"))}</div>
              </>
            )}
            {(provider === "ollama" || provider === "lmstudio") && (
              <div className="grid2">
                <div className="field">
                  <label htmlFor="url">{t("settings.url")}</label>
                  <input id="url" className="input" value={provider === "ollama" ? ollamaUrl : lmsUrl}
                    onChange={(e) => (provider === "ollama" ? setOllamaUrl : setLmsUrl)(e.target.value)} onBlur={loadRemote} />
                </div>
                <div className="field">
                  <label htmlFor="model">{t("settings.model")}</label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <select id="model" className="input" value={provider === "ollama" ? ollamaModel : lmsModel}
                      onChange={(e) => (provider === "ollama" ? setOllamaModel : setLmsModel)(e.target.value)}>
                      <option value="">{remote[0] ? `${t("common.auto")} (${remote[0]})` : "—"}</option>
                      {remote.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                    <button className="btn icon" aria-label={t("settings.refresh")} onClick={loadRemote}>{loadingModels ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}</button>
                  </div>
                  {!loadingModels && remote.length === 0 && <p className="hint">{t("settings.notRunning", { name: provider === "ollama" ? "Ollama" : "LM Studio" })}</p>}
                </div>
              </div>
            )}
            {provider === "openai" && (
              <div className="grid2">
                <div className="field"><label htmlFor="ourl">{t("settings.url")}</label><input id="ourl" className="input" placeholder="http://127.0.0.1:8000/v1" value={oaiUrl} onChange={(e) => setOaiUrl(e.target.value)} onBlur={loadRemote} /></div>
                <div className="field"><label htmlFor="omodel">{t("settings.model")}</label>
                  <input id="omodel" className="input" list="omodels" value={oaiModel} onChange={(e) => setOaiModel(e.target.value)} />
                  <datalist id="omodels">{remote.map((r) => <option key={r} value={r} />)}</datalist>
                </div>
                <div className="field"><label htmlFor="okey">{t("settings.apiKey")}</label><input id="okey" className="input" type="password" value={oaiKey} onChange={(e) => setOaiKey(e.target.value)} /></div>
              </div>
            )}
            <div className="actions">
              <button className="btn" onClick={runTest} disabled={test === "busy"}>{test === "busy" ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />} {t("settings.test")}</button>
              {status.running.llm && provider === "builtin" && (
                <button className="btn" onClick={async () => { await api.stopRunning("llm").catch((e) => useStore.getState().toast(e.message, "error")); useStore.getState().refresh(); }}>
                  <Square size={14} fill="currentColor" /> {t("settings.stopModel")}
                </button>
              )}
              {test && test !== "busy" && (test.ok
                ? <span className="chip ok">{t("settings.testOk", { s: test.seconds ?? "" })}</span>
                : <span className="chip err" style={{ height: "auto", padding: "4px 10px", whiteSpace: "normal" }}>{test.error}</span>)}
            </div>
          </Section>

          <Section id="knowledge" title={t("settings.knowledge")}>
            <div className="models">{row(byId("embeddinggemma-2"))}</div>
            <div className="grid2">
              <div className="field">
                <label htmlFor="dims">{t("settings.dims")}</label>
                <select id="dims" className="input" value={dims} onChange={(e) => setDims(Number(e.target.value))}>
                  {[768, 512, 256, 128].map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
                <p className="hint">{t("settings.dimsHint")}</p>
              </div>
              <div className="field">
                <label htmlFor="sp">{t("settings.webProvider")}</label>
                <select id="sp" className="input" value={searchProv} onChange={(e) => setSearchProv(e.target.value)}>
                  <option value="duckduckgo">DuckDuckGo</option>
                  <option value="searxng">SearXNG</option>
                </select>
                {searchProv === "searxng" && <input className="input" aria-label={t("settings.searxng")} value={searx} onChange={(e) => setSearx(e.target.value)} />}
              </div>
            </div>
            <Switch checked={!!webDefault} onChange={setWebDefault} label={`${t("chat.web")} · ${t("chat.webHint")}`} />
            <div><button className="btn" onClick={() => api.reindexAll()}><RefreshCw size={16} /> {t("settings.reindex")}</button></div>
          </Section>

          <Section id="speech" title={t("settings.speech")}>
            <div className="grid2">
              <div className="field">
                <label htmlFor="tts">{t("settings.ttsEngine")}</label>
                <select id="tts" className="input" value={ttsEngine} onChange={(e) => setTtsEngine(e.target.value)}>
                  <option value="system">{t("settings.tts.system")}</option>
                  <option value="kokoro">{t("settings.tts.kokoro")}</option>
                </select>
              </div>
              {ttsEngine === "kokoro" && (
                <div className="field">
                  <label htmlFor="voice">{t("settings.ttsVoice")}</label>
                  <select id="voice" className="input" value={ttsVoice} onChange={(e) => setTtsVoice(e.target.value)}>
                    <option value="">{t("common.auto")}</option>
                    {Object.entries(voices).map(([l, vs]) => (
                      <optgroup key={l} label={langName(l, t.lang)}>{vs.map((v) => <option key={v} value={v}>{v}</option>)}</optgroup>
                    ))}
                  </select>
                </div>
              )}
            </div>
            {ttsEngine === "kokoro" && <div className="models">{row(byId("kokoro"))}</div>}
          </Section>

          <Section id="models" title={t("settings.models")}>
            <RunningModels />
            <p className="lead" style={{ marginTop: 0 }}>{t("settings.modelsLead")}</p>
            <ModelGroups models={models} jobs={jobs} />
          </Section>

          <Section id="apps" title={t("apps.title")}>
            <p className="hint">{t("apps.lead")}</p>
            <ConnectedApps />
          </Section>

          <Section id="agents" title={t("settings.agents")}>
            <p className="lead" style={{ marginTop: 0 }}>{t("settings.agentsLead")}</p>
            {agents && (
              <>
                <div className="field">
                  <span className="label">{t("settings.stdio")}</span>
                  <div className="code">{claudeCmd}<CopyButton text={claudeCmd} /></div>
                  <div className="code">{stdioJson}<CopyButton text={stdioJson} /></div>
                </div>
                <div className="field">
                  <span className="label">{t("settings.httpMcp")}</span>
                  <div className="code">{httpJson}<CopyButton text={httpJson} /></div>
                </div>
              </>
            )}
            <p className="hint">{t("settings.tools")}</p>
          </Section>

          <Section id="delete" title={t("settings.delete")}>
            <DeleteData />
          </Section>

          <Section id="about" title={t("settings.about")}>
            <UpdatePanel />
            <div className="grid2">
              <div className="field"><span className="label">{t("settings.version")}</span><span>Lumina {status.version} · {status.system.os} {status.system.arch}</span></div>
              <div className="field"><span className="label">{t("settings.data")}</span><span className="src-path" style={{ fontSize: 14 }}>{status.system.data}</span></div>
            </div>
            <p className="hint">{t("settings.license")} <a href="https://github.com/Lautaro005/lumina" target="_blank" rel="noopener">github.com/Lautaro005/lumina</a></p>
          </Section>
        </div>
      </div>
    </div>
  );
}
