import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, Download, Loader2, Trash2, X } from "lucide-react";
import { api, fmtBytes, type Job, type Model } from "./api";
import { useT } from "./i18n";
import { useStore } from "./store";

/** The Lumina mark: a Cornell sheet, one blue rule and the red margin line. */
export function Logo({ size = 28, onCover = true }: { size?: number; onCover?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="3.5" y="2.5" width="25" height="27" rx="4" fill={onCover ? "#ffffff" : "var(--paper)"} stroke="var(--rule-soft)" />
      <rect x="4" y="9" width="24" height="1.6" fill="#7b9be0" />
      <rect x="12" y="10.6" width="1.6" height="18.4" fill="#e0594f" />
      <rect x="16.5" y="15" width="8" height="1.6" rx="0.8" fill="#b9c8ee" />
      <rect x="16.5" y="19.5" width="6" height="1.6" rx="0.8" fill="#b9c8ee" />
      <rect x="6.5" y="15" width="3.6" height="1.6" rx="0.8" fill="#20242c" />
    </svg>
  );
}

marked.setOptions({ gfm: true, breaks: true });

export function Markdown({ text, className = "md", cites }: { text: string; className?: string; cites?: boolean }) {
  const html = useMemo(() => {
    let h = marked.parse(text || "", { async: false }) as string;
    if (cites) h = h.replace(/\[(\d{1,2})\]/g, '<span class="cite">$1</span>');
    return DOMPurify.sanitize(h, { ADD_ATTR: ["target"] });
  }, [text, cites]);
  return <div className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const drop = useStore((s) => s.dropToast);
  const t = useT();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((x) => (
        <div key={x.id} className={`toast ${x.kind}`}>
          <span>{x.text}</span>
          <button onClick={() => drop(x.id)} aria-label={t("common.close")}>
            <X size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

/** Closes on outside click and Escape. */
export function Popover({ open, onClose, children, align = "right" }: {
  open: boolean; onClose: () => void; children: ReactNode; align?: "left" | "right";
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    setTimeout(() => document.addEventListener("mousedown", down), 0);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div ref={ref} className="menu" role="menu" style={{ top: "calc(100% + 6px)", [align]: 0 }}>
      {children}
    </div>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

export function useSetting<T = any>(key: string): [T, (v: T) => Promise<void>] {
  const value = useStore((s) => s.status?.settings[key]) as T;
  const refresh = useStore((s) => s.refresh);
  const toast = useStore((s) => s.toast);
  const set = async (v: T) => {
    useStore.setState((s) => (s.status ? { status: { ...s.status, settings: { ...s.status.settings, [key]: v } } } : {}));
    try {
      await api.settings({ [key]: v });
      refresh();
    } catch (e) {
      toast(String((e as Error).message), "error");
    }
  };
  return [value, set];
}

export function ModelRow({ model, job, compact = false }: { model: Model; job?: Job | null; compact?: boolean }) {
  const t = useT();
  const installed = useStore((s) => s.status?.models[model.id]) ?? model.installed;
  const refresh = useStore((s) => s.refresh);
  const toast = useStore((s) => s.toast);
  const running = job && (job.state === "running" || job.state === "queued");
  const pct = job && job.total ? Math.min(100, (job.done / job.total) * 100) : 0;
  const start = async () => {
    try {
      await api.download(model.id);
      refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="model">
      <div className="model-name">
        {model.label}
        {installed && <span className="chip ok"><Check size={13} /> {t("common.downloaded")}</span>}
        {job?.state === "error" && <span className="chip err">{t("model.error")}</span>}
      </div>
      <div className="model-act">
        {running ? (
          <button className="btn sm ghost" onClick={() => api.cancelDownload(model.id)}>
            <Loader2 size={15} className="spin" /> {t("common.cancel")}
          </button>
        ) : installed ? (
          !compact && (
            <button className="btn sm ghost danger" onClick={async () => { await api.removeModel(model.id); refresh(); }}>
              <Trash2 size={15} /> {t("common.remove")}
            </button>
          )
        ) : (
          <button className="btn sm" onClick={start}>
            <Download size={15} /> {job?.state === "error" ? t("common.retry") : t("common.download")} · {fmtBytes(model.size)}
          </button>
        )}
      </div>
      <div className="model-meta">
        {running
          ? job!.done ? t("model.downloading", { done: fmtBytes(job!.done), total: fmtBytes(job!.total) }) : t("model.queued")
          : job?.state === "error" ? job.error
          : [model.note, model.ram_gb ? t("model.ram", { gb: model.ram_gb }) : "", model.license].filter(Boolean).join(" · ")}
      </div>
      {running && <div className="progress"><i style={{ transform: `scaleX(${pct / 100})` }} /></div>}
    </div>
  );
}

export function useModels() {
  const [models, setModels] = useState<Model[]>([]);
  const status = useStore((s) => s.status);
  useEffect(() => {
    api.models().then(setModels).catch(() => {});
  }, [status?.version]);
  return models;
}

export function Meter({ level, on }: { level: number; on: boolean }) {
  const bars = [0.15, 0.35, 0.55, 0.75];
  return (
    <span className={`meter ${on ? "on" : ""}`} aria-hidden="true">
      {bars.map((b, i) => (
        <i key={i} className={level > b ? "lit" : ""} style={{ height: 4 + i * 3 }} />
      ))}
    </span>
  );
}

export function CopyButton({ text }: { text: string }) {
  const t = useT();
  const [done, setDone] = useState(false);
  return (
    <button className="btn sm" onClick={async () => {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    }}>
      {done ? <Check size={14} /> : null} {done ? t("common.copied") : t("common.copy")}
    </button>
  );
}

/** Read text aloud: Kokoro through the backend when chosen and downloaded, otherwise the system voice. */
let currentAudio: HTMLAudioElement | null = null;
export async function speak(text: string, lang: string, useKokoro: boolean): Promise<void> {
  stopSpeaking();
  const plain = text.replace(/[#*_`>\[\]]/g, "").replace(/\s+/g, " ").trim();
  if (useKokoro) {
    const r = await fetch("/api/tts", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: plain, lang }) });
    if (r.ok) {
      const url = URL.createObjectURL(await r.blob());
      currentAudio = new Audio(url);
      await currentAudio.play();
      return;
    }
  }
  const u = new SpeechSynthesisUtterance(plain);
  u.lang = lang;
  speechSynthesis.speak(u);
}
export function stopSpeaking() {
  currentAudio?.pause();
  currentAudio = null;
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}

export function guessLang(text: string, fallback: string): string {
  const es = (text.match(/\b(el|la|los|las|que|de|para|con|una|por|es|se|del)\b/gi) || []).length;
  const en = (text.match(/\b(the|and|to|of|is|for|with|that|on|we|it)\b/gi) || []).length;
  if (es > en * 1.2) return "es";
  if (en > es * 1.2) return "en";
  return fallback;
}
