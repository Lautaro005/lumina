import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Download, FileAudio, Highlighter, Loader2, MessageSquareText, MoreHorizontal, Pause, Play,
  RotateCw, ScrollText, Search, Sparkles, Square, Trash2, Volume2, X,
} from "lucide-react";
import { api, fmtClock, type Block, type Meeting as M, type Segment } from "../api";
import { CopyButton, Markdown, Meter, Popover, guessLang, speak, stopSpeaking } from "../components";
import { langName, useT } from "../i18n";
import { useStore } from "../store";

const norm = (s: string) => s.toLowerCase().replace(/[\W_]+/g, " ").trim();

function useDebounced<T extends unknown[]>(fn: (...a: T) => void, ms: number) {
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef(fn);
  latest.current = fn;
  return useCallback((...a: T) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => latest.current(...a), ms);
  }, [ms]);
}

function AutoText({ value, onChange, onKeyDown, placeholder, inputRef, label }: {
  value: string; onChange: (v: string) => void; onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder?: string; inputRef: (el: HTMLTextAreaElement | null) => void; label: string;
}) {
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el) {
      el.style.height = "0px";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [value]);
  return (
    <textarea
      ref={(el) => { ref.current = el; inputRef(el); }}
      rows={1} value={value} placeholder={placeholder} aria-label={label} spellCheck
      onChange={(e) => onChange(e.target.value.replace(/\n/g, " "))} onKeyDown={onKeyDown}
    />
  );
}

export default function MeetingView({ id }: { id: string }) {
  const t = useT();
  const meeting = useStore((s) => s.meeting);
  const setMeeting = useStore((s) => s.setMeeting);
  const live = useStore((s) => s.live);
  const status = useStore((s) => s.status)!;
  const go = useStore((s) => s.go);
  const toast = useStore((s) => s.toast);
  const [cues, setCues] = useState<string[]>([""]);
  const [raw, setRaw] = useState("");
  const [tab, setTab] = useState<"notes" | "enhanced">("notes");
  const [title, setTitle] = useState("");
  const [menu, setMenu] = useState<"" | "export" | "more">("");
  const [drawer, setDrawer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const refs = useRef<(HTMLTextAreaElement | null)[]>([]);
  const focusNext = useRef<{ i: number; pos: "start" | "end" } | null>(null);

  useEffect(() => {
    let alive = true;
    api.meeting(id).then((m) => {
      if (!alive) return;
      setMeeting(m);
      setCues([...m.cues.split("\n").filter((x) => x.trim()), ""]);
      setRaw(m.cues);
      setTitle(m.title);
    }).catch(() => { toast("Meeting not found", "error"); go("/meetings"); });
    return () => { alive = false; setMeeting(null); stopSpeaking(); };
  }, [id, setMeeting, go, toast]);

  // keep exactly one empty line at the end for typing
  useEffect(() => {
    if (cues.length === 0 || cues[cues.length - 1].trim() !== "") setCues((c) => [...c, ""]);
  }, [cues]);

  useLayoutEffect(() => {
    if (focusNext.current) {
      const { i, pos } = focusNext.current;
      const el = refs.current[i];
      if (el) {
        el.focus();
        const p = pos === "start" ? 0 : el.value.length;
        el.setSelectionRange(p, p);
      }
      focusNext.current = null;
    }
  });

  const saveCues = useDebounced((lines: string[]) => {
    const text = lines.map((x) => x.trim()).filter(Boolean).join("\n");
    api.patchMeeting(id, { cues: text }).catch((e) => toast(e.message, "error"));
    useStore.setState((s) => (s.meeting && s.meeting.id === id ? { meeting: { ...s.meeting, cues: text } } : {}));
  }, 600);
  const saveTitle = useDebounced((v: string) => api.patchMeeting(id, { title: v }).catch(() => {}), 500);

  const saveRaw = useDebounced((text: string) => {
    const clean = text.split("\n").map((x) => x.trimEnd()).join("\n").trim();
    api.patchMeeting(id, { cues: clean }).catch((e) => toast(e.message, "error"));
    useStore.setState((s) => (s.meeting && s.meeting.id === id ? { meeting: { ...s.meeting, cues: clean } } : {}));
  }, 600);
  const updateRaw = (v: string) => {
    setRaw(v);
    saveRaw(v);
  };

  const updateCue = (i: number, v: string) => {
    const next = cues.slice();
    next[i] = v;
    setCues(next);
    saveCues(next);
  };
  const onKey = (i: number) => (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget;
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      const before = el.value.slice(0, el.selectionStart);
      const after = el.value.slice(el.selectionEnd);
      const next = [...cues.slice(0, i), before, after, ...cues.slice(i + 1)];
      setCues(next);
      saveCues(next);
      focusNext.current = { i: i + 1, pos: "start" };
    } else if (e.key === "Backspace" && el.selectionStart === 0 && el.selectionEnd === 0 && i > 0) {
      e.preventDefault();
      const prevLen = cues[i - 1].length;
      const next = [...cues.slice(0, i - 1), cues[i - 1] + cues[i], ...cues.slice(i + 1)];
      setCues(next);
      saveCues(next);
      focusNext.current = { i: i - 1, pos: "end" };
      requestAnimationFrame(() => refs.current[i - 1]?.setSelectionRange(prevLen, prevLen));
    } else if (e.key === "ArrowUp" && el.selectionStart === 0 && i > 0) {
      e.preventDefault();
      focusNext.current = { i: i - 1, pos: "end" };
      setCues(cues.slice());
    } else if (e.key === "ArrowDown" && el.selectionEnd === el.value.length && i < cues.length - 1) {
      e.preventDefault();
      focusNext.current = { i: i + 1, pos: "end" };
      setCues(cues.slice());
    }
  };

  const m = meeting && meeting.id === id ? meeting : null;
  const isMine = live.meetingId === id;
  const openPermissions = () => {
    go("/settings");
    setTimeout(() => document.getElementById("permissions")?.scrollIntoView({ behavior: "smooth" }), 200);
  };
  const recording = isMine && live.state === "recording";
  const paused = isMine && live.state === "paused";
  // a recording that was imported has a transcript and a summary, not notes of its own
  const imported = !!m && (m.tags.includes("imported") || !!m.audio);
  const processing = isMine && (live.state === "processing" || live.state === "importing");
  const otherLive = !!live.meetingId && !isMine && (live.state === "recording" || live.state === "paused");

  const blocks: Block[] = m?.enhanced || [];
  const userBlocks = blocks.filter((b) => !b.auto);
  const autoBlocks = blocks.filter((b) => b.auto);
  const blockFor = (cue: string, i: number): Block | undefined => {
    const key = norm(cue);
    return userBlocks.find((b) => norm(b.cue) === key) || (userBlocks[i] && norm(userBlocks[i].cue) === key ? userBlocks[i] : undefined);
  };

  const sttReady = status.models[status.stt_model] && status.models["silero-vad"];
  const segments: Segment[] = m?.segments || [];
  const elapsed = isMine ? live.elapsed : m?.duration || 0;
  const date = m ? new Date((m.started || m.created) * 1000) : new Date();
  const dateStr = date.toLocaleString(t.lang, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const language = status.settings.stt_language;

  const start = async () => {
    setBusy(true);
    try {
      if (paused) await api.pause(id, false);
      else await api.start(id);
      useStore.getState().refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    setBusy(true);
    await api.stop(id).catch((e) => toast(e.message, "error"));
    setBusy(false);
  };
  const pause = () => api.pause(id, true);
  const enhanceNow = () => api.enhance(id).catch((e) => toast(e.message, "error"));
  const summarize = () => api.summarize(id).catch((e) => toast(e.message, "error"));
  const retranscribe = async () => {
    setBusy(true);
    try { await api.retranscribe(id); } catch (e) { toast((e as Error).message, "error"); } finally { setBusy(false); }
  };
  // window.confirm does not open in the desktop webview, so the confirmation lives in the menu
  const [confirmDel, setConfirmDel] = useState(false);
  const del = async () => {
    await api.deleteMeeting(id);
    go("/meetings");
  };
  const chatAbout = async () => {
    const c = await api.newChat(id);
    go(`/chat/${c.id}`);
  };
  const addTag = (tag: string) => {
    const v = tag.trim();
    if (!m || !v || m.tags.includes(v)) return;
    const tags = [...m.tags, v];
    setMeeting({ ...m, tags });
    api.patchMeeting(id, { tags });
    setTagDraft("");
  };
  const removeTag = (tag: string) => {
    if (!m) return;
    const tags = m.tags.filter((x) => x !== tag);
    setMeeting({ ...m, tags });
    api.patchMeeting(id, { tags });
  };

  const readNotes = () => {
    if (speaking) { stopSpeaking(); setSpeaking(false); return; }
    const text = [m?.summary?.overview, ...blocks.map((b) => `${b.cue}. ${b.text}`)].filter(Boolean).join("\n");
    setSpeaking(true);
    speak(text, guessLang(text, t.lang), status.settings.tts_engine === "kokoro" && status.tts_ready)
      .catch(() => {}).finally(() => setTimeout(() => setSpeaking(false), 500));
  };

  const assists = m?.assists || [];
  const showGutter = status.settings.live_assistance || assists.length > 0;
  const view = (["columns", "rows", "tabs"] as const).find((v) => v === status.settings.notes_view) || "columns";

  if (!m) return <div className="empty"><Loader2 className="spin" /></div>;

  return (
    <>
      <div className={`meeting ${showGutter ? "" : "no-gutter"}`}>
        <article className="sheet" aria-label={m.title || t("common.untitled")}>
          <header className="sheet-head">
            <div className="sheet-title-row">
              <input className="title-input" value={title} placeholder={t("meeting.titlePlaceholder")}
                aria-label={t("meeting.titlePlaceholder")}
                onChange={(e) => { setTitle(e.target.value); saveTitle(e.target.value); }} />
              <div className="actions">
                {recording || paused ? (
                  <>
                    {recording
                      ? <button className="btn" onClick={pause}><Pause size={16} /> {t("meeting.pause")}</button>
                      : <button className="btn" onClick={start} disabled={busy}><Play size={16} /> {t("meeting.resume")}</button>}
                    <button className="btn stop" onClick={stop} disabled={busy}><Square size={14} fill="currentColor" /> {t("meeting.stop")}</button>
                  </>
                ) : processing ? (
                  <span className="chip blue"><Loader2 size={14} className="spin" /> {live.state === "importing" ? t("meeting.importing") : t("meeting.processing")}</span>
                ) : imported ? (
                  <button className="btn" onClick={retranscribe} disabled={busy || !m.audio || !sttReady}>
                    <RotateCw size={16} /> {t("meeting.retranscribe")}
                  </button>
                ) : (
                  <button className="btn record" onClick={start} disabled={busy || !sttReady || otherLive}>
                    {busy ? <Loader2 size={16} className="spin" /> : <span className="dot" style={{ background: "#fff" }} />}
                    {m.status === "done" ? t("meeting.resume") : t("meeting.start")}
                  </button>
                )}
                <div style={{ position: "relative" }}>
                  <button className="btn icon" aria-label={t("meeting.export")} aria-haspopup="menu" onClick={() => setMenu(menu === "export" ? "" : "export")}>
                    <Download size={17} />
                  </button>
                  <Popover open={menu === "export"} onClose={() => setMenu("")}>
                    {(["md", "txt", "srt", "json"] as const).map((f) => (
                      <button key={f} role="menuitem" onClick={() => { setMenu(""); location.href = `/api/meetings/${id}/export?format=${f}`; }}>
                        <ScrollText size={16} /> {t(f === "md" ? "meeting.exportMd" : f === "txt" ? "meeting.exportTxt" : f === "srt" ? "meeting.exportSrt" : "meeting.exportJson")}
                      </button>
                    ))}
                  </Popover>
                </div>
                <div style={{ position: "relative" }}>
                  <button className="btn icon" aria-label="More" aria-haspopup="menu" onClick={() => setMenu(menu === "more" ? "" : "more")}>
                    <MoreHorizontal size={18} />
                  </button>
                  <Popover open={menu === "more"} onClose={() => { setMenu(""); setConfirmDel(false); }}>
                    <button role="menuitem" onClick={() => { setMenu(""); chatAbout(); }}><MessageSquareText size={16} /> {t("meeting.chat")}</button>
                    <button role="menuitem" onClick={() => { setMenu(""); setDrawer(true); }}><Search size={16} /> {t("meeting.findTranscript")}</button>
                    <button role="menuitem" onClick={() => { setMenu(""); readNotes(); }}><Volume2 size={16} /> {t("common.readAloud")}</button>
                    {m.status === "done" && !imported && m.cues.trim() && (
                      <button role="menuitem" disabled={!status.llm_ready} onClick={() => { setMenu(""); enhanceNow(); }}><Sparkles size={16} /> {t("meeting.enhanceAgain")}</button>
                    )}
                    <hr />
                    {confirmDel ? (
                      <div style={{ padding: "6px 10px", display: "grid", gap: 8, maxWidth: 280 }}>
                        <span style={{ fontSize: 13.5 }}>{t("meeting.deleteConfirm")}</span>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button className="btn sm danger" style={{ height: 30 }} onClick={del}><Trash2 size={14} /> {t("common.delete")}</button>
                          <button className="btn sm ghost" style={{ height: 30 }} onClick={() => setConfirmDel(false)}>{t("common.cancel")}</button>
                        </div>
                      </div>
                    ) : (
                      <button role="menuitem" onClick={() => setConfirmDel(true)} style={{ color: "var(--margin-ink)" }}><Trash2 size={16} /> {t("meeting.delete")}</button>
                    )}
                  </Popover>
                </div>
              </div>
            </div>
            <div className="meta">
              <span>{dateStr}</span>
              <i className="sep" />
              <span>{fmtClock(elapsed)}</span>
              <i className="sep" />
              <span>{language === "auto" ? t("common.auto") : langName(language, t.lang)}</span>
              {(recording || paused) && (
                <>
                  <i className="sep" />
                  <span className="levels">
                    {live.sources.includes("you") && <span>{t("common.you")} <Meter level={live.levels.you} on={recording} /></span>}
                    {live.sources.includes("others") && <span>{t("common.others")} <Meter level={live.levels.others} on={recording} /></span>}
                  </span>
                </>
              )}
              {m.tags.map((tag) => (
                <span key={tag} className="tag">{tag}<button aria-label={`${t("common.remove")} ${tag}`} onClick={() => removeTag(tag)}><X size={12} /></button></span>
              ))}
              <input className="tag-input" placeholder={`+ ${t("common.addTag")}`}
                value={tagDraft} onChange={(e) => setTagDraft(e.target.value)} aria-label={t("common.addTag")}
                onKeyDown={(e) => { if (e.key === "Enter") addTag(tagDraft); }} onBlur={() => tagDraft && addTag(tagDraft)} />
            </div>
            {(live.micDenied || (isMine && live.sysDenied)) && (
              <div className="callout warn">
                <FileAudio size={18} />
                <span style={{ flex: 1 }}>{live.micDenied ? t("meeting.micDenied") : t("meeting.sysDenied")}</span>
                <button className="btn sm" onClick={openPermissions}>{t("meeting.openPermission")}</button>
              </div>
            )}
            {!status.llm_ready && (
              <div className="callout warn">
                <Sparkles size={18} />
                <span style={{ flex: 1 }}>{t("meeting.noModel", { why: "" }).replace(/: $/, ".")}</span>
                <button className="btn sm" onClick={() => go("/settings")}>{t("meeting.setup")}</button>
              </div>
            )}
            {status.llm_ready && status.llm.effective && status.llm.effective !== status.settings.llm_provider && (
              <div className="callout">
                <Sparkles size={18} />
                <span>{t("meeting.fallback", { chosen: t(`settings.provider.${status.settings.llm_provider}` as "settings.provider.ollama"), used: t(`settings.provider.${status.llm.effective}` as "settings.provider.ollama") })}</span>
              </div>
            )}
            {!sttReady && (
              <div className="callout warn">
                <FileAudio size={18} />
                <span style={{ flex: 1 }}>{t("meeting.notReady")}</span>
                <button className="btn sm" onClick={() => go("/settings")}>{t("meeting.setup")}</button>
              </div>
            )}
          </header>

          {imported ? (
            <ImportedBody m={m} segments={segments}
              summary={<Summary m={m} canWrite={m.status === "done" && segments.length > 0 && !processing} onWrite={summarize} />} />
          ) : view === "rows" ? (
            <div className={`cornell ${recording ? "live" : paused ? "paused" : ""}`}>
              {(recording || paused) && (
                <span className="margin-clock" aria-live="off"><i className="dot" />{recording ? t("meeting.listening") : t("meeting.paused")} {fmtClock(elapsed)}</span>
              )}
              <div className="col-heads">
                <div>{t("meeting.cues")}</div>
                <div>
                  {t("meeting.notes")}
                  {blocks.length > 0 && (
                    <button className="btn sm ghost" onClick={readNotes} aria-pressed={speaking}>
                      <Volume2 size={15} /> {speaking ? t("common.stop") : t("common.readAloud")}
                    </button>
                  )}
                </div>
              </div>
              {cues.map((cue, i) => {
                const b = cue.trim() ? blockFor(cue, i) : undefined;
                const fresh = !!b?.changed && Date.now() / 1000 - b.changed < 30;
                return (
                  <div className="row" key={i}>
                    <div className="cue">
                      <AutoText value={cue} label={`${t("meeting.cues")} ${i + 1}`}
                        placeholder={i === cues.length - 1 ? (cues.length === 1 ? t("meeting.cuePlaceholder") : "…") : ""}
                        onChange={(v) => updateCue(i, v)} onKeyDown={onKey(i)} inputRef={(el) => { refs.current[i] = el; }} />
                    </div>
                    {b?.text ? (
                      <div key={b.changed} className={`body ${fresh ? "fresh" : ""}`}><Markdown text={b.text} /></div>
                    ) : (
                      <div className="body muted">
                        {cue.trim() ? (b ? t("meeting.nothingHeard") : "") : i === 0 && cues.length === 1 ? t("meeting.emptyBody") : ""}
                      </div>
                    )}
                  </div>
                );
              })}
              {cues.length <= 1 && <div className="cue-hint">{t("meeting.cueHint")}</div>}
              {autoBlocks.map((b, i) => (
                <div className="row" key={`auto-${i}`}>
                  <div className="cue auto">{b.cue}</div>
                  <div key={b.changed} className={`body ${b.changed && Date.now() / 1000 - b.changed < 30 ? "fresh" : ""}`}><Markdown text={b.text} /></div>
                </div>
              ))}
            </div>
          ) : (
            <NotesPanels
              view={view} tab={tab} setTab={setTab} raw={raw} onRaw={updateRaw} blocks={[...userBlocks, ...autoBlocks]}
              recording={recording} paused={paused} elapsed={elapsed} speaking={speaking} onRead={readNotes}
            />
          )}

          {!imported && <Summary m={m} canWrite={m.status === "done" && segments.length > 0 && !processing} onWrite={summarize} />}
        </article>

        {showGutter && <Gutter m={m} />}
      </div>

      {!imported && <Strip segments={segments} onOpen={() => setDrawer(true)} />}
      {drawer && <TranscriptDrawer segments={segments} onClose={() => setDrawer(false)} />}
    </>
  );
}

function NotesPanels({ view, tab, setTab, raw, onRaw, blocks, recording, paused, elapsed, speaking, onRead }: {
  view: "columns" | "tabs"; tab: "notes" | "enhanced"; setTab: (t: "notes" | "enhanced") => void;
  raw: string; onRaw: (v: string) => void; blocks: Block[]; recording: boolean; paused: boolean; elapsed: number;
  speaking: boolean; onRead: () => void;
}) {
  const t = useT();
  const showNotes = view === "columns" || tab === "notes";
  const showEnhanced = view === "columns" || tab === "enhanced";
  const enhancedText = blocks.filter((b) => b.text).map((b) => `${b.cue}: ${b.text}`).join("\n\n");
  return (
    <div className={`panels ${view} ${recording ? "live" : paused ? "paused" : ""}`}>
      {view === "tabs" && (
        <div className="seg-switch" role="tablist" aria-label={t("settings.notesView")}>
          {(["notes", "enhanced"] as const).map((k) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
              {k === "notes" ? t("meeting.shorthand") : t("meeting.liveEnhanced")}
            </button>
          ))}
        </div>
      )}
      {showNotes && (
        <section className="panel notes-panel" aria-label={t("meeting.shorthand")}>
          <header>
            <h2>{t("meeting.shorthand")}</h2>
            {(recording || paused) && (
              <span className={`chip ${recording ? "ok" : "warn"}`}><i className="dot" />{recording ? t("meeting.live") : t("meeting.paused")} {fmtClock(elapsed)}</span>
            )}
            <CopyButton text={raw} />
          </header>
          <textarea className="shorthand" value={raw} spellCheck onChange={(e) => onRaw(e.target.value)}
            placeholder={t("meeting.shorthandPlaceholder")} aria-label={t("meeting.shorthand")} />
        </section>
      )}
      {showEnhanced && (
        <section className="panel enhanced-panel" aria-label={t("meeting.liveEnhanced")}>
          <header>
            <h2><Sparkles size={14} /> {t("meeting.liveEnhanced")}</h2>
            {blocks.length > 0 && (
              <button className="btn sm ghost" onClick={onRead} aria-pressed={speaking}>
                <Volume2 size={15} /> {speaking ? t("common.stop") : t("common.readAloud")}
              </button>
            )}
            <CopyButton text={enhancedText} />
          </header>
          {blocks.some((b) => b.text) ? (
            <ul className="enhanced-list">
              {blocks.filter((b) => b.text).map((b, i) => (
                <li key={`${b.cue}-${i}`} className={b.changed && Date.now() / 1000 - b.changed < 30 ? "fresh" : ""}>
                  <b>{b.cue}</b>
                  <Markdown text={b.text} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="placeholder-line">{t("meeting.enhancedEmpty")}</p>
          )}
        </section>
      )}
    </div>
  );
}

function Summary({ m, canWrite, onWrite }: { m: M; canWrite: boolean; onWrite: () => void }) {
  const t = useT();
  const writing = useStore((s) => s.summarizing === m.id);
  const ready = useStore((s) => s.status?.llm_ready);
  const s = m.summary || {};
  const cols: [string, string[] | undefined][] = [
    [t("meeting.decisions"), s.decisions],
    [t("meeting.actions"), s.action_items],
    [t("meeting.questions"), s.open_questions],
    [t("meeting.topics"), s.topics],
  ];
  const has = s.overview || cols.some(([, v]) => v && v.length);
  return (
    <section className="summary-band" aria-label={t("meeting.summary")}>
      <div className="summary-head">
        <h2>{t("meeting.summary")}</h2>
        {canWrite && (
          <button className="btn sm" onClick={onWrite} disabled={writing || !ready}>
            {writing ? <Loader2 size={15} className="spin" /> : <RotateCw size={15} />}
            {writing ? t("meeting.summarizing") : has ? t("meeting.regenerate") : t("meeting.writeSummary")}
          </button>
        )}
      </div>
      {has ? (
        <>
          {s.overview && <p className="overview">{s.overview}</p>}
          <div className="summary-cols">
            {cols.filter(([, v]) => v && v.length).map(([label, v]) => (
              <div key={label}>
                <h3>{label}</h3>
                <ul>{v!.map((x, i) => <li key={i}>{x}</li>)}</ul>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="placeholder-line">{t("meeting.summaryEmpty")}</p>
      )}
    </section>
  );
}

/** An imported recording: the player, the transcript and its actions, not the notes of a live meeting. */
function ImportedBody({ m, segments, summary }: { m: M; segments: Segment[]; summary: React.ReactNode }) {
  const t = useT();
  const [q, setQ] = useState("");
  const shown = useMemo(() => (q ? segments.filter((s) => s.text.toLowerCase().includes(q.toLowerCase())) : segments), [segments, q]);
  const text = segments.map((s) => `[${fmtClock(s.t0)}] ${s.text}`).join("\n");
  return (
    <>
      <div className="import-media">
        {m.audio ? (
          <audio className="import-audio" controls preload="none" src={`/api/meetings/${m.id}/audio`} aria-label={t("meeting.recording")} />
        ) : (
          <p className="hint">{t("meeting.noRecording")}</p>
        )}
      </div>
      {summary}
      <section className="import-transcript" aria-label={t("meeting.transcript")}>
        <header>
          <h2>{t("meeting.transcript")}</h2>
          <div className="search-wrap" style={{ maxWidth: 280 }}>
            <Search size={16} />
            <input className="input" placeholder={t("meeting.findTranscript")} value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <CopyButton text={text} />
        </header>
        {segments.length === 0 ? (
          <p className="placeholder-line">{t("meeting.transcriptEmpty")}</p>
        ) : (
          <div className="import-segs">
            {shown.map((s) => (
              <div className="seg" key={s.id}>
                <time>{fmtClock(s.t0)}</time>
                <p>{s.text}</p>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function Gutter({ m }: { m: M }) {
  const t = useT();
  const assists = m.assists || [];
  const [hidden, setHidden] = useState<string[]>([]);
  useEffect(() => {
    const unseen = assists.filter((a) => !a.seen);
    if (!unseen.length) return;
    const timer = setTimeout(() => unseen.forEach((a) => api.assistSeen(m.id, a.id)), 8000);
    return () => clearTimeout(timer);
  }, [assists, m.id]);
  const visible = assists.filter((a) => !hidden.includes(a.id));
  return (
    <aside className="gutter" aria-label={t("meeting.assist")}>
      <h2><Highlighter size={15} /> {t("meeting.assist")}</h2>
      {visible.length === 0 && <p className="hint">{t("meeting.assistHint")}</p>}
      {visible.map((a) => (
        <div key={a.id} className={`assist ${a.seen ? "" : "new"}`}>
          <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
            <q style={{ flex: 1 }}>{a.question}</q>
            <button className="btn sm ghost icon" aria-label={t("meeting.dismiss")} onClick={() => setHidden([...hidden, a.id])}><X size={14} /></button>
          </div>
          <Markdown className="ans" text={a.answer} cites />
          {a.sources?.length > 0 && (
            <div className="src">{t("meeting.assistSources")} {a.sources.slice(0, 3).map((s: any, i: number) => <span key={i}>{i + 1}. {s.title}</span>)}</div>
          )}
        </div>
      ))}
    </aside>
  );
}

function Strip({ segments, onOpen }: { segments: Segment[]; onOpen: () => void }) {
  const t = useT();
  const partial = useStore((s) => s.live.partial);
  const last = segments.slice(-2);
  const who = (src: string) => (src === "you" ? t("common.you") : src === "others" ? t("common.others") : t("common.speaker"));
  const lines: { src: string; text: string; partial?: boolean }[] = last.map((s) => ({ src: s.source, text: s.text }));
  for (const src of ["you", "others", "file"] as const) if (partial[src]) lines.push({ src, text: partial[src]!, partial: true });
  return (
    <div className="strip" role="region" aria-label={t("meeting.transcript")}>
      <b style={{ fontSize: 13, color: "var(--ink-3)" }}>{t("meeting.transcript")}</b>
      <div className="strip-lines" aria-live="polite">
        {lines.length === 0 && <div className="partial">{t("meeting.transcriptEmpty")}</div>}
        {lines.slice(-3).map((l, i) => (
          <div key={i} className={l.partial ? "partial" : ""}>
            <span className={`who ${l.src === "you" ? "you" : "others"}`}>{who(l.src)}</span>{l.text}
          </div>
        ))}
      </div>
      <button className="btn sm" onClick={onOpen} disabled={!segments.length}><ScrollText size={15} /> {t("meeting.showTranscript")}</button>
    </div>
  );
}

function TranscriptDrawer({ segments, onClose }: { segments: Segment[]; onClose: () => void }) {
  const t = useT();
  const [q, setQ] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  useEffect(() => { if (!q) endRef.current?.scrollIntoView({ block: "end" }); }, [segments.length, q]);
  const shown = useMemo(() => (q ? segments.filter((s) => s.text.toLowerCase().includes(q.toLowerCase())) : segments), [segments, q]);
  const hl = (text: string) => {
    if (!q) return text;
    const parts = text.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
    return parts.map((p, i) => (p.toLowerCase() === q.toLowerCase() ? <mark key={i}>{p}</mark> : p));
  };
  return (
    <div className="drawer" role="dialog" aria-modal="false" aria-label={t("meeting.transcript")}>
      <div className="drawer-head">
        <h2>{t("meeting.transcript")}</h2>
        <button className="btn icon ghost" onClick={onClose} aria-label={t("common.close")}><X size={18} /></button>
      </div>
      <div style={{ padding: "12px 20px 4px" }}>
        <div className="search-wrap" style={{ maxWidth: "none" }}>
          <Search size={16} />
          <input className="input" autoFocus placeholder={t("meeting.findTranscript")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <div className="drawer-body">
        {shown.map((s) => (
          <div className="seg" key={s.id}>
            <time>{fmtClock(s.t0)}</time>
            <div>
              <span className={`who ${s.source === "you" ? "you" : "others"}`}>{s.source === "you" ? t("common.you") : s.source === "others" ? t("common.others") : t("common.speaker")}</span>
              <p>{hl(s.text)}</p>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}
