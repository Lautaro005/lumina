import { Fragment, useEffect, useState } from "react";
import { ChevronRight, ExternalLink, FileAudio, FileText, Folder, FolderOpen, Globe, Loader2, NotebookPen, RefreshCw, Search, Sparkles, StickyNote, Trash2, Upload, X } from "lucide-react";
import { api, fmtBytes, type Meeting, type Source, type WebPreview } from "../api";
import { IndexStatus, ProgressBar } from "../components";
import { useT, type Key } from "../i18n";
import { useStore } from "../store";

const statusChip: Record<string, string> = { ready: "ok", error: "err", indexing: "blue", pending: "", skipped: "warn", summarizing: "blue" };
// a web address typed in the path box (with or without https://)
const isUrl = (v: string) => /^(https?:\/\/|www\.)\S+$/i.test(v.trim()) || /^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(\/\S*)?$/i.test(v.trim());

export default function Sources() {
  const t = useT();
  const status = useStore((s) => s.status)!;
  const toast = useStore((s) => s.toast);
  const [rows, setRows] = useState<Source[]>([]);
  const [path, setPath] = useState("");
  const [watch, setWatch] = useState(true);
  const [over, setOver] = useState(false);
  const [note, setNote] = useState<{ title: string; text: string } | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const [tagDraft, setTagDraft] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const refresh = useStore((s) => s.refresh);

  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const go = useStore((s) => s.go);

  const load = () => {
    api.sources().then(setRows).catch(() => {});
    api.meetings().then((m) => setMeetings(m.filter((x) => x.indexed))).catch(() => {});
  };
  useEffect(() => {
    load();
    let timer: ReturnType<typeof setTimeout>;
    const on = () => { clearTimeout(timer); timer = setTimeout(load, 350); }; // a folder fires one event per file
    window.addEventListener("lumina:kb", on);
    return () => { clearTimeout(timer); window.removeEventListener("lumina:kb", on); };
  }, []);
  const toggle = (id: string) => setOpen((o) => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const remove = async (id: string) => {
    await api.deleteSource(id).catch((e) => toast((e as Error).message, "error"));
    load();
    refresh();
  };
  const rel = (folder: Source, k: Source) => (k.path.startsWith(folder.path) ? k.path.slice(folder.path.length).replace(/^[\\/]+/, "") : k.title);

  // a web page: read it first, then ask whether to keep its text or a summary
  const [web, setWeb] = useState<{ url: string; preview?: WebPreview; busy?: "read" | "content" | "summary"; error?: string } | null>(null);
  const readPage = async (url: string) => {
    setWeb({ url, busy: "read" });
    try {
      setWeb({ url, preview: await api.webPreview(url) });
    } catch (e) {
      setWeb({ url, error: (e as Error).message });
    }
  };
  const saveWeb = async (mode: "content" | "summary") => {
    if (!web?.preview) return;
    setWeb({ ...web, busy: mode });
    try {
      await api.addWeb(web.preview.url, mode);
      setWeb(null);
      setPath("");
      load();
    } catch (e) {
      setWeb({ ...web, busy: undefined, error: (e as Error).message });
    }
  };
  const add = async (p: string) => {
    if (!p.trim()) return;
    if (isUrl(p)) { readPage(p.trim()); return; }
    try {
      await api.addSource(p.trim(), watch);
      setPath("");
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const pick = async (type: "file" | "folder") => {
    const r = await api.pick(type).catch(() => ({ path: "" }));
    if (r.path) add(r.path);
  };
  const upload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    try {
      await api.upload(list);
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const runSearch = async () => {
    if (!q.trim()) return;
    try {
      setHits(await api.search(q));
    } catch (e) {
      toast((e as Error).message, "warn");
    }
  };
  const setTags = async (s: Source, tags: string[]) => {
    await api.patchSource(s.id, { tags });
    load();
  };

  const top = rows.filter((r) => !r.parent);
  const children = (id: string) => rows.filter((r) => r.parent === id);
  const Icon = (k: string) => (k === "folder" ? Folder : k === "note" ? StickyNote : k === "web" ? Globe : FileText);

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("sources.title")}</h1>
          <p className="lead">{t("sources.lead")}</p>
        </div>
      </div>

      <div className="sources-grid">
        <div className="sources-main">
          {!status.embeddings_ready && <div className="callout warn">{t("sources.needEmbeddings")}</div>}
          <div className={`dropzone ${over ? "over" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}>
            <div className="addbar">
              <button className="btn primary" onClick={() => pick("folder")}><FolderOpen size={16} /> {t("sources.addFolder")}</button>
              <button className="btn" onClick={() => pick("file")}><Upload size={16} /> {t("sources.addFiles")}</button>
              <button className="btn" onClick={() => setNote({ title: "", text: "" })}><NotebookPen size={16} /> {t("sources.newNote")}</button>
              <label className="switch" style={{ marginLeft: "auto" }}>
                <input type="checkbox" role="switch" checked={watch} onChange={(e) => setWatch(e.target.checked)} />
                <span>{t("sources.watch")}</span>
              </label>
            </div>
            <form className="addbar" onSubmit={(e) => { e.preventDefault(); add(path); }}>
              <input className="input" style={{ flex: 1 }} placeholder={t("sources.path")} value={path} onChange={(e) => setPath(e.target.value)} aria-label={t("sources.path")} />
              <button className="btn" type="submit" disabled={!path.trim()}>{isUrl(path) ? <><Globe size={16} /> {t("web.read")}</> : t("sources.add")}</button>
            </form>
            <div className="hint">{over ? t("sources.drop") : t("sources.counts", status.kb)}</div>
          </div>

          <IndexStatus />

          {web && (
            <div className="panel web-ask" role="dialog" aria-label={t("web.title")}>
              <div className="web-ask-head">
                <span className="kind-ic"><Globe size={17} /></span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <b className="src-title">{web.preview?.title || web.url}</b>
                  <div className="src-path">{web.preview?.url || web.url}</div>
                </div>
                <button className="btn sm ghost icon" aria-label={t("common.cancel")} onClick={() => setWeb(null)}><X size={15} /></button>
              </div>
              {web.busy === "read" && <p className="hint"><Loader2 size={14} className="spin" /> {t("web.reading")}</p>}
              {web.error && <p className="hint" style={{ color: "var(--margin-ink)" }}>{web.error}</p>}
              {web.preview && (
                <>
                  <p className="web-excerpt">{web.preview.chars > 400 ? `${web.preview.excerpt.trimEnd().replace(/[.…\s]+$/, "")}…` : web.preview.excerpt}</p>
                  <p className="hint" style={{ margin: 0 }}>{t("web.ask", { n: web.preview.chars.toLocaleString() })}</p>
                  <div className="web-choices">
                    <button className="web-choice" disabled={!!web.busy} onClick={() => saveWeb("content")}>
                      {web.busy === "content" ? <Loader2 size={18} className="spin" /> : <FileText size={18} />}
                      <span><b>{t("web.content")}</b><small>{t("web.contentHint")}</small></span>
                    </button>
                    <button className="web-choice" disabled={!!web.busy || !status.llm_ready} onClick={() => saveWeb("summary")}
                      title={status.llm_ready ? undefined : t("web.noModel")}>
                      {web.busy === "summary" ? <Loader2 size={18} className="spin" /> : <Sparkles size={18} />}
                      <span><b>{t("web.summary")}</b><small>{status.llm_ready ? t("web.summaryHint") : t("web.noModel")}</small></span>
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {note && (
            <form className="panel" style={{ padding: 16, display: "grid", gap: 10 }}
              onSubmit={async (e) => { e.preventDefault(); await api.addNote(note.title || "Note", note.text); setNote(null); load(); }}>
              <input className="input" placeholder={t("sources.noteTitle")} value={note.title} onChange={(e) => setNote({ ...note, title: e.target.value })} autoFocus />
              <textarea className="input" rows={6} placeholder={t("sources.noteText")} value={note.text} onChange={(e) => setNote({ ...note, text: e.target.value })} />
              <div className="actions" style={{ justifyContent: "flex-end" }}>
                <button type="button" className="btn ghost" onClick={() => setNote(null)}>{t("common.cancel")}</button>
                <button type="submit" className="btn primary" disabled={!note.text.trim()}>{t("common.save")}</button>
              </div>
            </form>
          )}

          {top.length === 0 && meetings.length === 0 ? (
            <div className="panel empty"><p style={{ maxWidth: "60ch" }}>{t("sources.empty")}</p></div>
          ) : top.length > 0 && (
            <table className="table">
              <tbody>
                {top.map((s) => {
                  const kids = children(s.id).filter((k) => k.status !== "excluded"); // files taken out stay hidden
                  const I = Icon(s.kind);
                  const ready = kids.filter((k) => k.status === "ready").length;
                  const st = s.kind === "folder" ? (kids.some((k) => k.status === "indexing" || k.status === "pending") ? "indexing" : "ready") : s.status;
                  const isOpen = open.has(s.id);
                  const files = [...kids].sort((a, b) => rel(s, a).localeCompare(rel(s, b)));
                  const busy = s.kind === "folder" && kids.some((k) => k.status === "indexing" || k.status === "pending");
                  return (
                    <Fragment key={s.id}>
                    <tr className={s.kind === "folder" && isOpen ? "has-files" : ""}>
                      <td>
                        <div className="src-row">
                          <span className="kind-ic"><I size={17} /></span>
                          <div style={{ minWidth: 0, flex: 1 }}>
                            {s.kind === "folder" ? (
                              <button type="button" className="folder-toggle" aria-expanded={isOpen} aria-controls={`ff-${s.id}`}
                                title={isOpen ? t("sources.hideFiles") : t("sources.showFiles")} onClick={() => toggle(s.id)}>
                                <ChevronRight size={16} className="chev" />
                                <span className="src-title">{s.title}</span>
                              </button>
                            ) : (
                              <div className="src-title">{s.title}</div>
                            )}
                            {s.kind === "web" ? (
                              <a className="src-path src-link" href={s.path} target="_blank" rel="noreferrer noopener">{s.path} <ExternalLink size={11} /></a>
                            ) : s.path && <div className="src-path">{s.path}</div>}
                            <div className="t-tags">
                              <span className={`chip ${statusChip[st] ?? ""}`}>{t(`sources.status.${st}` as Key)}</span>
                              <span className="chip">
                                {s.kind === "folder" ? `${ready}/${kids.length} ${t("sources.files")}` : `${s.chunks} ${t("sources.chunks")}`}
                                {s.kind === "file" && s.size ? ` · ${fmtBytes(s.size)}` : ""}
                              </span>
                              {s.kind === "web" && <span className="chip">{s.ext === "summary" ? t("web.isSummary") : t("web.isContent")}</span>}
                              {s.tags.map((x) => (
                                <span key={x} className="tag">{x}<button aria-label={`${t("common.remove")} ${x}`} onClick={() => setTags(s, s.tags.filter((y) => y !== x))}><X size={12} /></button></span>
                              ))}
                              <input className="tag-input" placeholder={`+ ${t("common.addTag")}`}
                                value={tagDraft[s.id] || ""} aria-label={t("common.addTag")}
                                onChange={(e) => setTagDraft({ ...tagDraft, [s.id]: e.target.value })}
                                onKeyDown={(e) => {
                                  const v = (tagDraft[s.id] || "").trim();
                                  if (e.key === "Enter" && v && !s.tags.includes(v)) { setTags(s, [...s.tags, v]); setTagDraft({ ...tagDraft, [s.id]: "" }); }
                                }} />
                            </div>
                            {busy && <ProgressBar value={kids.length ? ready / kids.length : 0} label={t("sources.index.files", { done: ready, total: kids.length })} />}
                            {s.error && s.status === "error" && <div className="hint" style={{ color: "var(--margin-ink)", marginTop: 6 }}>{s.error}</div>}
                          </div>
                        </div>
                      </td>
                      <td className="act-col">
                        <div className="row-actions">
                          <button className="btn sm ghost icon" title={t("sources.reindex")} aria-label={t("sources.reindex")} onClick={() => api.reindex(s.id).then(load)}><RefreshCw size={15} /></button>
                          {s.path && s.kind !== "web" && <button className="btn sm ghost icon" title={t("sources.reveal")} aria-label={t("sources.reveal")} onClick={() => api.reveal(s.id)}><FolderOpen size={15} /></button>}
                          <button className="btn sm ghost icon danger" title={t("common.remove")} aria-label={t("common.remove")} onClick={() => remove(s.id)}><Trash2 size={15} /></button>
                        </div>
                      </td>
                    </tr>
                    {s.kind === "folder" && isOpen && (
                      // its own row, so the list runs to the folder's delete button
                      <tr className="ff-row">
                        <td colSpan={2}>
                          <ul className="folder-files" id={`ff-${s.id}`}>
                            {files.length === 0 && <li className="ff-empty">{t("sources.noFiles")}</li>}
                            {files.map((k) => (
                              <li key={k.id} title={k.path}>
                                <div className="ff-top">
                                  <FileText size={14} />
                                  <span className="ff-name">{rel(s, k)}</span>
                                  <span className="ff-actions">
                                    <button className="btn sm ghost icon" title={t("sources.reindex")} aria-label={`${t("sources.reindex")} ${k.title}`} onClick={() => api.reindex(k.id).then(load)}><RefreshCw size={14} /></button>
                                    <button className="btn sm ghost icon danger" title={t("sources.removeFile")} aria-label={`${t("sources.removeFile")} ${k.title}`} onClick={() => remove(k.id)}><Trash2 size={14} /></button>
                                  </span>
                                </div>
                                <div className="ff-sub">
                                  {k.status === "ready" && <span>{k.chunks} {t("sources.chunks")}</span>}
                                  {k.size > 0 && <span>{fmtBytes(k.size)}</span>}
                                  <span className={`chip ${statusChip[k.status] ?? ""}`}>{t(`sources.status.${k.status}` as Key)}</span>
                                </div>
                                {k.status === "error" && k.error && <div className="ff-error">{k.error}</div>}
                              </li>
                            ))}
                          </ul>
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}

          {meetings.length > 0 && (
            <section>
              <h2 className="src-section">{t("sources.meetings")} <span>{meetings.length}</span></h2>
              <table className="table">
                <tbody>
                  {meetings.map((m) => (
                    <tr key={m.id} className="click" tabIndex={0} onClick={() => go(`/meeting/${m.id}`)}
                      onKeyDown={(e) => e.key === "Enter" && go(`/meeting/${m.id}`)}>
                      <td>
                        <div className="src-row">
                          <span className="kind-ic"><FileAudio size={17} /></span>
                          <div>
                            <div className="src-title">{m.title || t("common.untitled")}</div>
                            {m.summary?.overview && <div className="src-path">{m.summary.overview}</div>}
                            <div className="t-tags">
                              <span className="chip ok">{t("sources.status.ready")}</span>
                              <span className="chip">{new Date((m.started || m.created) * 1000).toLocaleDateString()}</span>
                              {m.summary?.overview && <span className="chip">{t("sources.hasSummary")}</span>}
                            </div>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>

        <aside className="side">
          <form className="panel" onSubmit={(e) => { e.preventDefault(); runSearch(); }}>
            <h2>{t("sources.test")}</h2>
            <div className="search-wrap" style={{ maxWidth: "none" }}>
              <Search size={16} />
              <input className="input" placeholder={t("sources.testPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            {hits && hits.length === 0 && <p className="hint">—</p>}
            {hits && hits.map((h) => (
              <div key={h.id} className="hit">
                <b>{h.title} <span className="chip blue" style={{ height: 18, fontSize: 11 }}>{Math.round(h.score * 100)}%</span></b>
                <p>{h.text}</p>
              </div>
            ))}
          </form>
        </aside>
      </div>
    </div>
  );
}
