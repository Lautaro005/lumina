import { useEffect, useState } from "react";
import { ChevronRight, FileAudio, FileText, Folder, FolderOpen, NotebookPen, RefreshCw, Search, StickyNote, Trash2, Upload, X } from "lucide-react";
import { api, fmtBytes, type Meeting, type Source } from "../api";
import { IndexStatus, ProgressBar } from "../components";
import { useT, type Key } from "../i18n";
import { useStore } from "../store";

const statusChip: Record<string, string> = { ready: "ok", error: "err", indexing: "blue", pending: "", skipped: "warn" };

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

  const add = async (p: string) => {
    if (!p.trim()) return;
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
  const Icon = (k: string) => (k === "folder" ? Folder : k === "note" ? StickyNote : FileText);

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
              <button className="btn" type="submit" disabled={!path.trim()}>{t("sources.add")}</button>
            </form>
            <div className="hint">{over ? t("sources.drop") : t("sources.counts", status.kb)}</div>
          </div>

          <IndexStatus />

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
                  const kids = children(s.id);
                  const I = Icon(s.kind);
                  const ready = kids.filter((k) => k.status === "ready").length;
                  const st = s.kind === "folder" ? (kids.some((k) => k.status === "indexing" || k.status === "pending") ? "indexing" : "ready") : s.status;
                  const isOpen = open.has(s.id);
                  const files = [...kids].sort((a, b) => rel(s, a).localeCompare(rel(s, b)));
                  const busy = s.kind === "folder" && kids.some((k) => k.status === "indexing" || k.status === "pending");
                  return (
                    <tr key={s.id}>
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
                            {s.path && <div className="src-path">{s.path}</div>}
                            <div className="t-tags">
                              <span className={`chip ${statusChip[st] ?? ""}`}>{t(`sources.status.${st}` as Key)}</span>
                              <span className="chip">
                                {s.kind === "folder" ? `${ready}/${kids.length} ${t("sources.files")}` : `${s.chunks} ${t("sources.chunks")}`}
                                {s.kind === "file" && s.size ? ` · ${fmtBytes(s.size)}` : ""}
                              </span>
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
                            {s.kind === "folder" && isOpen && (
                              <ul className="folder-files" id={`ff-${s.id}`}>
                                {files.length === 0 && <li className="ff-empty">{t("sources.noFiles")}</li>}
                                {files.map((k) => (
                                  <li key={k.id} title={k.path}>
                                    <FileText size={14} />
                                    <span className="ff-name">{rel(s, k)}</span>
                                    <span className="ff-meta">{k.status === "ready" ? `${k.chunks} ${t("sources.chunks")}` : ""}{k.size ? `${k.status === "ready" ? " · " : ""}${fmtBytes(k.size)}` : ""}</span>
                                    <span className={`chip ${statusChip[k.status] ?? ""}`}>{t(`sources.status.${k.status}` as Key)}</span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="act-col">
                        <div className="row-actions">
                          <button className="btn sm ghost icon" title={t("sources.reindex")} aria-label={t("sources.reindex")} onClick={() => api.reindex(s.id).then(load)}><RefreshCw size={15} /></button>
                          {s.path && <button className="btn sm ghost icon" title={t("sources.reveal")} aria-label={t("sources.reveal")} onClick={() => api.reveal(s.id)}><FolderOpen size={15} /></button>}
                          <button className="btn sm ghost icon danger" title={t("common.remove")} aria-label={t("common.remove")} onClick={() => remove(s.id)}><Trash2 size={15} /></button>
                        </div>
                      </td>
                    </tr>
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
