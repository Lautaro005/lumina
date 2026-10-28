import { useEffect, useRef, useState } from "react";
import { ArrowUp, BookOpen, Brain, Check, Copy, FileText, Globe, Pencil, Plus, RotateCw, Square, StickyNote, Volume2 } from "lucide-react";
import { api, sendChat, type ChatT, type Message } from "../api";
import { ConfirmDialog, ContextMenu, Markdown, RenameInput, guessLang, speak } from "../components";
import { useT } from "../i18n";
import { useStore } from "../store";

type Live = { text: string; sources: any[]; status: string } | null;

const MAX_CHIPS = 4;

function SourceChip({ s, n }: { s: any; n: number }) {
  const go = useStore((st) => st.go);
  const Icon = s.kind === "web" ? Globe : s.kind === "brain" ? Brain : s.kind === "meetings" || s.kind === "meeting" ? BookOpen : s.kind === "notes" ? StickyNote : FileText;
  const open = () => {
    if (s.url) window.open(s.url, "_blank", "noopener");
    else if (s.meeting_id) go(`/meeting/${s.meeting_id}`);
  };
  const clickable = !!(s.url || s.meeting_id);
  return (
    <button type="button" className="src-chip" onClick={open} disabled={!clickable} title={s.snippet ? `${s.title}\n\n${s.snippet}` : s.title}>
      <span className="cite">{n}</span>
      <Icon size={13} />
      <span className="src-chip-title">{s.title || s.url || "—"}</span>
    </button>
  );
}

function Sources({ list, numbers }: { list: any[]; numbers?: number[] }) {
  const t = useT();
  const [all, setAll] = useState(false);
  if (!list.length) return null;
  const shown = all ? list : list.slice(0, MAX_CHIPS);
  return (
    <div className="src-chips" aria-label={t("chat.sources")}>
      {shown.map((s, i) => <SourceChip key={i} s={s} n={numbers ? numbers[i] : i + 1} />)}
      {!all && list.length > MAX_CHIPS && (
        <button type="button" className="src-more" onClick={() => setAll(true)}>{t("chat.moreSources", { n: list.length - MAX_CHIPS })}</button>
      )}
    </div>
  );
}

function AnswerTools({ text, kokoro }: { text: string; kokoro: boolean }) {
  const t = useT();
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* clipboard blocked */ }
  };
  return (
    <div className="a-tools">
      <button type="button" className="tool" onClick={copy} aria-label={t("chat.copy")} title={t("chat.copy")}>
        {done ? <Check size={15} /> : <Copy size={15} />}
      </button>
      <button type="button" className="tool" onClick={() => speak(text, guessLang(text, t.lang), kokoro)} aria-label={t("common.readAloud")} title={t("common.readAloud")}>
        <Volume2 size={15} />
      </button>
    </div>
  );
}

export default function Chat({ chatId }: { chatId?: string }) {
  const t = useT();
  const go = useStore((s) => s.go);
  const toast = useStore((s) => s.toast);
  const status = useStore((s) => s.status)!;
  const [chats, setChats] = useState<ChatT[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [web, setWeb] = useState<boolean>(!!status.settings.web_search);
  // brain as a source: offered while the brain app is open (its search runs there)
  const [brainOpen, setBrainOpen] = useState(false);
  const [useBrain, setUseBrain] = useState<boolean>(status.settings.brain_chat !== false);
  useEffect(() => { api.integrations().then((x) => setBrainOpen(!!x.brain.running)).catch(() => {}); }, []);
  const [live, setLive] = useState<Live>(null);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [renaming, setRenaming] = useState("");
  const [deleting, setDeleting] = useState<ChatT | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const abort = useRef<AbortController | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const loadChats = () => api.chats().then(setChats).catch(() => {});
  useEffect(() => { loadChats(); }, []);
  useEffect(() => {
    if (!chatId) { setMessages([]); return; }
    api.chat(chatId).then((c) => setMessages(c.messages)).catch(() => go("/chat"));
  }, [chatId, go]);
  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [messages.length, live?.text]);
  useEffect(() => {
    const el = box.current;
    if (el) { el.style.height = "0px"; el.style.height = `${Math.min(180, el.scrollHeight)}px`; }
  }, [draft]);

  const send = async (text = draft, replace = false) => {
    const q = text.trim();
    if (!q || live) return;
    let id = chatId;
    if (!id) {
      const c = await api.newChat();
      id = c.id;
      go(`/chat/${id}`);
    }
    setDraft("");
    setEditing(null);
    setMessages((m) => {
      let base = m;
      if (replace) {  // the last question and its answer are replaced
        base = m.slice();
        while (base.length && base[base.length - 1].role === "assistant") base.pop();
        if (base.length && base[base.length - 1].role === "user") base.pop();
      }
      return [...base, { id: `tmp-${Date.now()}`, role: "user", content: q, sources: [], created: Date.now() / 1000 }];
    });
    setLive({ text: "", sources: [], status: "searching" });
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      await sendChat(id!, q, web, (ev) => {
        if (ev.type === "sources") setLive((l) => (l ? { ...l, sources: ev.sources, status: "writing" } : l));
        else if (ev.type === "delta") setLive((l) => (l ? { ...l, text: l.text + ev.text } : l));
        else if (ev.type === "error") toast(ev.text, "error");
        else if (ev.type === "done") setMessages((m) => [...m, ev.message]);
      }, { signal: ctl.signal, replace, brain: brainOpen && useBrain });
    } catch (e) {
      if ((e as Error).name === "AbortError") {
        // stopped by the user: the server kept what was written so far
        await new Promise((r) => setTimeout(r, 250));
        api.chat(id!).then((c) => setMessages(c.messages)).catch(() => {});
      } else {
        toast((e as Error).message, "error");
      }
    } finally {
      abort.current = null;
      setLive(null);
      loadChats();
    }
  };
  const stop = () => abort.current?.abort();

  const remove = async (id: string) => {
    await api.deleteChat(id);
    if (id === chatId) go("/chat");
    setDeleting(null);
    loadChats();
  };
  const rename = async (id: string, title: string) => {
    setRenaming("");
    setChats((cs) => cs.map((c) => (c.id === id ? { ...c, title } : c)));
    await api.renameChat(id, title).catch((e) => toast((e as Error).message, "error"));
    loadChats();
  };

  const lastUser = messages.map((m) => m.role).lastIndexOf("user");
  const kokoro = status.settings.tts_engine === "kokoro" && status.tts_ready;
  const empty = messages.length === 0 && !live;

  const composer = (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
      <div className="composer-box">
        <textarea ref={box} rows={1} value={draft} placeholder={t("chat.placeholder")} aria-label={t("chat.placeholder")}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <div className="composer-tools">
          <button type="button" className="web-toggle" aria-pressed={web} title={t("chat.webHint")} onClick={() => setWeb(!web)}>
            <Globe size={15} /> {t("chat.web")}
          </button>
          {brainOpen && (
            <button type="button" className="web-toggle" aria-pressed={useBrain} title={t("brain.chatHint")} onClick={() => setUseBrain(!useBrain)}>
              <Brain size={15} /> brain
            </button>
          )}
          <span className="send-wrap">
            {live && (
              <button type="button" className="stop" onClick={stop} aria-label={t("chat.stop")} title={t("chat.stop")}>
                <Square size={13} fill="currentColor" />
              </button>
            )}
            <button type="submit" className="send" aria-label={t("chat.send")} disabled={!draft.trim() || !!live}><ArrowUp size={18} /></button>
          </span>
        </div>
      </div>
    </form>
  );

  return (
    <div className="chat">
      {menu && (
        <ContextMenu at={menu} onClose={() => setMenu(null)}
          onRename={() => setRenaming(menu.id)} onDelete={() => setDeleting(chats.find((c) => c.id === menu.id) ?? null)} />
      )}
      {deleting && (
        <ConfirmDialog title={t("chat.deleteTitle")} body={`“${deleting.title || t("chat.new")}” — ${t("chat.deleteBody")}`}
          confirm={t("common.delete")} cancel={t("common.cancel")} onConfirm={() => remove(deleting.id)} onCancel={() => setDeleting(null)} />
      )}
      <nav className="chat-list" aria-label={t("chat.history")}>
        <button className="chat-new" onClick={() => go("/chat")}><Plus size={16} /> {t("chat.new")}</button>
        {chats.length === 0 && <p className="hint" style={{ padding: "8px 10px" }}>{t("chat.noChats")}</p>}
        {chats.map((c) => (
          <div key={c.id} className="chat-item" role="button" tabIndex={0} aria-current={c.id === chatId}
            onClick={() => renaming !== c.id && go(`/chat/${c.id}`)}
            onKeyDown={(e) => e.key === "Enter" && renaming !== c.id && go(`/chat/${c.id}`)}
            onContextMenu={(e) => { e.preventDefault(); setMenu({ id: c.id, x: e.clientX, y: e.clientY }); }}>
            {renaming === c.id
              ? <RenameInput value={c.title} label={t("common.rename")} onSave={(v) => rename(c.id, v)} onCancel={() => setRenaming("")} />
              : <span>{c.title || t("chat.new")}</span>}
          </div>
        ))}
      </nav>

      <section className={`convo ${empty ? "is-empty" : ""}`}>
        {empty ? (
          <div className="convo-hero">
            <h1>{t("chat.greeting")}</h1>
            <p>{t("chat.empty")}</p>
            {composer}
            <div className="suggest">
              {(["chat.suggest1", "chat.suggest2", "chat.suggest3"] as const).map((k) => (
                <button key={k} type="button" onClick={() => send(t(k))}>{t(k)}</button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div className="convo-scroll" ref={scroller}>
              <div className="convo-inner">
                {messages.map((m, idx) => m.role === "user" ? (
                  editing === m.id ? (
                    <form key={m.id} className="q-edit" onSubmit={(e) => { e.preventDefault(); send(editDraft, true); }}>
                      <textarea value={editDraft} autoFocus rows={3} aria-label={t("chat.edit")} onChange={(e) => setEditDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }} />
                      <div className="actions">
                        <button type="button" className="btn sm ghost" onClick={() => setEditing(null)}>{t("common.cancel")}</button>
                        <button type="submit" className="btn sm primary" disabled={!editDraft.trim()}>{t("chat.saveEdit")}</button>
                      </div>
                    </form>
                  ) : (
                    <div key={m.id} className="q-wrap">
                      <div className="q">{m.content}</div>
                      {!live && lastUser === idx && (
                        <div className="q-tools">
                          <button type="button" className="tool" onClick={() => send(m.content, true)} aria-label={t("chat.regenerate")} title={t("chat.regenerate")}><RotateCw size={15} /></button>
                          <button type="button" className="tool" onClick={() => { setEditDraft(m.content); setEditing(m.id); }} aria-label={t("chat.edit")} title={t("chat.edit")}><Pencil size={15} /></button>
                        </div>
                      )}
                    </div>
                  )
                ) : (
                  <article key={m.id} className="a">
                    <div className="a-body"><Markdown text={m.content} cites /></div>
                    <Sources list={m.sources} numbers={m.sources.map((s: any) => s.n)} />
                    <AnswerTools text={m.content} kokoro={kokoro} />
                  </article>
                ))}
                {live && (
                  <article className="a" aria-busy="true">
                    <div className="a-body">
                      {live.text
                        ? <Markdown text={live.text} cites className="md typing" />
                        : <p className="thinking">{t("chat.searching")}<i /><i /><i /></p>}
                    </div>
                    <Sources list={live.sources} />
                  </article>
                )}
              </div>
            </div>
            {composer}
          </>
        )}
      </section>
    </div>
  );
}
