import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, Download } from "lucide-react";
import { api, fmtBytes } from "../api";
import { Logo, ModelRow, useModels, useSetting } from "../components";
import { useT } from "../i18n";
import { useStore } from "../store";
import { LanguageSelect, sttFor } from "./Settings";

function DemoSheet() {
  const t = useT();
  const es = t.lang === "es";
  const rows = es
    ? [["presupuesto Q3, ¿recorte?", "Julián propone recortar un 10 % de marketing para sumar dos personas a soporte. Marta pide ver el impacto en leads antes del viernes."],
       ["fecha página de precios", "Sale el 5 de noviembre. Responsable: Marta Gómez."]]
    : [["Q3 budget, cut?", "Julián proposes a 10% cut to marketing to fund two support hires. Marta wants to see the impact on leads before Friday."],
       ["pricing page date", "Goes live November 5th. Owner: Marta Gómez."]];
  return (
    <div className="panel" style={{ boxShadow: "none", border: "1px solid var(--rule-soft)", overflow: "hidden" }} aria-hidden="true">
      <div style={{ padding: "10px 16px", borderBottom: "2px solid var(--rule)", font: "600 15px var(--serif)" }}>{es ? "Revisión semanal" : "Weekly review"}</div>
      {rows.map(([cue, body]) => (
        <div className="demo-cornell" key={cue}>
          <div style={{ padding: "10px 14px", fontWeight: 600, fontSize: 14 }}>{cue}</div>
          <div style={{ padding: "10px 16px", font: "15px/1.5 var(--serif)" }}>{body}</div>
        </div>
      ))}
    </div>
  );
}

export default function Onboarding() {
  const t = useT();
  const status = useStore((s) => s.status)!;
  const refresh = useStore((s) => s.refresh);
  const models = useModels();
  const [step, setStep] = useState(0);
  const [ui, setUi] = useSetting<string>("ui_language");
  const [provider, setProvider] = useSetting<string>("llm_provider");
  const [gemma, setGemma] = useSetting<string>("builtin_model");
  const [engine] = useSetting<string>("stt_engine");
  const [lang] = useSetting<string>("stt_language");
  const [wsize] = useSetting<string>("whisper_size");

  useEffect(() => {
    // if Ollama or LM Studio is already running and the user has nothing downloaded, suggest it
    if (provider === "builtin" && !status.models[gemma]) {
      if (status.llm.ollama) setProvider("ollama");
      else if (status.llm.lmstudio) setProvider("lmstudio");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stt = sttFor(engine, lang, wsize);
  const needed = ["embeddinggemma-2", "silero-vad", stt, ...(provider === "builtin" ? ["llama-server", gemma] : [])];
  const list = needed.map((id) => models.find((m) => m.id === id)).filter(Boolean) as typeof models;
  const missing = list.filter((m) => !status.models[m.id]);
  const total = missing.reduce((a, m) => a + m.size, 0);
  const anyRunning = list.some((m) => ["running", "queued"].includes(status.downloads[m.id]?.state || ""));

  const downloadAll = async () => {
    for (const m of missing) await api.download(m.id).catch(() => {});
    refresh();
  };
  const finish = async () => {
    await api.settings({ first_run_done: true });
    await refresh();
    location.hash = "/meetings";
  };

  const steps = [t("setup.step1"), t("setup.step2"), t("setup.step3")];
  const choice = (value: string, title: string, sub: string, extra?: React.ReactNode) => (
    <div role="radio" tabIndex={0} aria-checked={provider === value} className="choice"
      onClick={() => setProvider(value)} onKeyDown={(e) => (e.key === " " || e.key === "Enter") && setProvider(value)}>
      <span className="radio" />
      <span><b>{title}</b><small>{sub}</small>{provider === value && extra}</span>
      {value !== "builtin" && status.llm[value as "ollama"] && <span className="chip ok">{t("setup.detected")}</span>}
    </div>
  );

  return (
    <div className="setup">
      <div className="setup-sheet">
        <aside className="setup-cues">
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Logo onCover={false} />
            <span style={{ font: "600 22px var(--serif)" }}>lumina</span>
          </div>
          <div className="setup-steps">
            {steps.map((s, i) => (
              <div key={s} aria-current={step === i + 1 ? "step" : undefined} className={step > i + 1 ? "done" : ""}>
                <i>{i + 1}</i>{s}
              </div>
            ))}
          </div>
          <p className="hint">{t("nav.local")}</p>
        </aside>

        <div className="setup-body">
          {step === 0 && (
            <div style={{ display: "grid", gap: 20, alignContent: "start" }}>
              <h1>{t("setup.welcome")}</h1>
              <p className="lead">{t("setup.welcomeLead")}</p>
              <DemoSheet />
              <div className="field" style={{ maxWidth: 280 }}>
                <label htmlFor="ui">{t("settings.uiLanguage")}</label>
                <select id="ui" className="input" value={ui} onChange={(e) => setUi(e.target.value)}>
                  <option value="auto">{t("common.auto")}</option>
                  <option value="en">English</option>
                  <option value="es">Español</option>
                </select>
              </div>
            </div>
          )}
          {step === 1 && (
            <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
              <h2>{t("setup.languageQ")}</h2>
              <p className="lead">{t("setup.languageLead")}</p>
              <div className="field" style={{ maxWidth: 360 }}>
                <label htmlFor="stt">{t("settings.sttLanguage")}</label>
                <LanguageSelect id="stt" />
              </div>
              <p className="hint">{t("settings.willUse")} <b>{models.find((m) => m.id === stt)?.label}</b>. {models.find((m) => m.id === stt)?.note}</p>
            </div>
          )}
          {step === 2 && (
            <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
              <h2>{t("setup.generatorQ")}</h2>
              <p className="lead">{t("setup.generatorLead")}</p>
              <div className="choices" role="radiogroup">
                {choice("builtin", t("settings.provider.builtin"), "Google Gemma 4 · Apache-2.0",
                  <select className="input" style={{ marginTop: 10, maxWidth: 380 }} value={gemma} onClick={(e) => e.stopPropagation()}
                    onChange={(e) => setGemma(e.target.value)} aria-label={t("settings.gemmaSize")}>
                    {["gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b"].map((g) => {
                      const m = models.find((x) => x.id === g);
                      return <option key={g} value={g}>{m ? `${m.label} · ${fmtBytes(m.size)} · ${m.note}` : g}</option>;
                    })}
                  </select>)}
                {choice("ollama", "Ollama", "ollama.com · 127.0.0.1:11434")}
                {choice("lmstudio", "LM Studio", "lmstudio.ai · 127.0.0.1:1234")}
              </div>
            </div>
          )}
          {step === 3 && (
            <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
              <h2>{t("setup.downloadsQ")}</h2>
              <p className="lead">{t("setup.downloadsLead")}</p>
              <div className="models">{list.map((m) => <ModelRow key={m.id} model={m} job={status.downloads[m.id]} compact />)}</div>
              {missing.length > 0 && (
                <div className="actions">
                  <button className="btn primary" onClick={downloadAll} disabled={anyRunning}><Download size={16} /> {t("setup.startDownloads")} · {fmtBytes(total)}</button>
                  {anyRunning && <span className="hint">{t("setup.waiting")}</span>}
                </div>
              )}
            </div>
          )}

          <div className="setup-foot">
            {step > 0 ? <button className="btn ghost" onClick={() => setStep(step - 1)}><ArrowLeft size={16} /> {t("setup.back")}</button> : <span />}
            {step === 0 && <button className="btn primary" onClick={() => setStep(1)}>{t("setup.begin")} <ArrowRight size={16} /></button>}
            {step > 0 && step < 3 && <button className="btn primary" onClick={() => setStep(step + 1)}>{t("setup.next")} <ArrowRight size={16} /></button>}
            {step === 3 && (
              <div className="actions">
                {missing.length > 0 && !anyRunning && <button className="btn ghost" onClick={finish}>{t("setup.skip")}</button>}
                <button className="btn primary" onClick={finish} disabled={missing.length > 0 && !anyRunning}>{t("setup.finish")} <ArrowRight size={16} /></button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
