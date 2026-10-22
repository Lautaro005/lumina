import { useEffect, useState } from "react";
import { CheckCircle2, Info, Loader2, RefreshCw } from "lucide-react";
import { api, type Model } from "../api";
import { CopyButton, ModelRow, Switch, useModels, useSetting } from "../components";
import { langName, useT } from "../i18n";
import { useStore } from "../store";

const EU25 = ["bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it", "lv", "lt", "mt", "pl", "pt", "ro", "ru", "sk", "sl", "es", "sv", "uk"];

export function sttFor(engine: string, language: string, size: string): string {
  if (engine === "parakeet") return "parakeet-v3";
  if (engine === "canary") return "canary-1b-v2";
  if (engine === "whisper") return `whisper-${size}`;
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
      {engine !== "canary" && <option value="auto">{t("common.auto")}</option>}
      <optgroup label="—">
        {common.map((c) => <option key={`c-${c}`} value={c}>{langName(c, t.lang)}</option>)}
      </optgroup>
      <optgroup label={`${sorted.length}`}>
        {sorted.map((c) => <option key={c} value={c}>{langName(c, t.lang)}</option>)}
      </optgroup>
    </select>
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
  const [mic, setMic] = useSetting<number | null>("mic_device");
  const [sysAudio, setSysAudio] = useSetting<boolean>("system_audio");
  const [partials, setPartials] = useSetting<boolean>("partials");
  const [autoEnh, setAutoEnh] = useSetting<boolean>("auto_enhance");
  const [assist, setAssist] = useSetting<boolean>("live_assistance");
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

  const [devices, setDevices] = useState<{ id: number; name: string; default: boolean }[]>([]);
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

  const nav: [string, string][] = [
    ["general", t("settings.general")], ["transcription", t("settings.transcription")], ["generation", t("settings.generation")],
    ["knowledge", t("settings.knowledge")], ["speech", t("settings.speech")], ["agents", t("settings.agents")], ["about", t("settings.about")],
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
          </Section>

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
                  if (v === "canary" && lang === "auto") setLang(t.lang);
                  if ((v === "parakeet" || v === "canary") && lang !== "auto" && !EU25.includes(lang)) setLang(v === "canary" ? "en" : "auto");
                }}>
                  {(["auto", "parakeet", "canary", "whisper"] as const).map((x) => <option key={x} value={x}>{t(`settings.engine.${x}`).split(":")[0]}</option>)}
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
            {engine === "parakeet" && lang !== "auto" && <div className="callout warn"><Info size={18} />{t("settings.parakeetNote")}</div>}
            <div className="callout"><Info size={18} /><span>{t("settings.willUse")} <b>{byId(chosen)?.label || chosen}</b>.</span></div>
            <div className="models">{row(byId(chosen))}{row(byId("silero-vad"))}</div>
            <div className="grid2">
              <div className="field">
                <label htmlFor="mic">{t("settings.mic")}</label>
                <select id="mic" className="input" value={mic ?? ""} onChange={(e) => setMic(e.target.value === "" ? null : Number(e.target.value))}>
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

          <Section id="about" title={t("settings.about")}>
            <div className="grid2">
              <div className="field"><span className="label">{t("settings.version")}</span><span>Lumina {status.version} · {status.system.os} {status.system.arch}</span></div>
              <div className="field"><span className="label">{t("settings.data")}</span><span className="src-path" style={{ fontSize: 14 }}>{status.system.data}</span></div>
            </div>
            <p className="hint">{t("settings.license")} <a href="https://github.com/Lautaro005/lumina" target="_blank" rel="noopener">github.com/Lautaro005/lumina</a></p>
            <span className="label">{t("settings.models")}</span>
            <div className="models">{models.map((m) => <ModelRow key={m.id} model={m} job={jobs[m.id]} />)}</div>
          </Section>
        </div>
      </div>
    </div>
  );
}
