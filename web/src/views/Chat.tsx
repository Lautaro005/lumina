import { useEffect, useRef, useState } from "react";
import { ArrowUp, BookOpen, FileText, Globe, Plus, StickyNote, Trash2, Volume2 } from "lucide-react";
import { api, sendChat, type ChatT, type Message } from "../api";
import { CopyButton, Markdown, guessLang, speak } from "../components";
import { useT } from "../i18n";
import { useStore } from "../store";

type Live = { text: string; sources: any[]; status: string } | null;

function SourceCue({ s, n }: { s: any; n: number }) {
  const go = useStore((st) => st.go);
  const Icon = s.kind === "web" ? Globe : s.kind === "meetings" || s.kind === "meeting" ? BookOpen : s.kind === "notes" ? StickyNote : FileText;
  const open = () => {
    if (s.url) window.open(s.url, "_blank", "noopener");
    else if (s.meeting_id) go(`/meeting/${s.meeting_id}`);
  };
  return (
    <div className="a-cue">
      <span className="cite">{n}</span>
      <div style={{ minWidth: 0 }}>
        <b title={s.title}>
          <Icon size={12} style={{ verticalAlign: -1, marginRight: 4 }} />
          {s.url || s.meeting_id ? <a href="#" onClick={(e) => { e.preventDefault(); open(); }}>{s.title || s.url}</a> : s.title}
        </b>
        {s.snippet && <small>{s.snippet.slice(0, 120)}…</small>}
      </div>
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
  const [live, setLive] = useState<Live>(null);
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
    if (el) { el.style.height = "0px"; el.style.height = `${Math.min(200, el.scrollHeight)}px`; }
  }, [draft]);

  const send = async (text = draft) => {
    const q = text.trim();
    if (!q || live) return;
    let id = chatId;
    if (!id) {
      const c = await api.newChat();
      id = c.id;
      go(`/chat/${id}`);
    }
    setDraft("");
    setMessages((m) => [...m, { id: `tmp-${Date.now()}`, role: "user", content: q, sources: [], created: Date.now() / 1000 }]);
    setLive({ text: "", sources: [], status: "searching" });
    try {
      await sendChat(id!, q, web, (ev) => {
        if (ev.type === "sources") setLive((l) => (l ? { ...l, sources: ev.sources, status: "writing" } : l));
        else if (ev.type === "delta") setLive((l) => (l ? { ...l, text: l.text + ev.text } : l));
        else if (ev.type === "error") toast(ev.text, "error");
        else if (ev.type === "done") setMessages((m) => [...m, ev.message]);
      });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setLive(null);
      loadChats();
    }
  };

  const remove = async (id: string) => {
    await api.deleteChat(id);
    if (id === chatId) go("/chat");
    loadChats();
  };

  const kokoro = status.settings.tts_engine === "kokoro" && status.tts_ready;
  return (
    <div className="chat">
      <nav className="chat-list" aria-label={t("chat.history")}>
        <button className="btn primary" onClick={() => go("/chat")}><Plus size={16} /> {t("chat.new")}</button>
        <h2>{t("chat.history")}</h2>
        {chats.length === 0 && <p className="hint" style={{ padding: "0 10px" }}>{t("chat.noChats")}</p>}
        {chats.map((c) => (
          <div key={c.id} className="chat-item" role="button" tabIndex={0} aria-current={c.id === chatId}
            onClick={() => go(`/chat/${c.id}`)} onKeyDown={(e) => e.key === "Enter" && go(`/chat/${c.id}`)}>
            <span>{c.title || t("chat.new")}</span>
            <button className="btn sm ghost icon del" aria-label={t("common.delete")} onClick={(e) => { e.stopPropagation(); remove(c.id); }}><Trash2 size={14} /></button>
          </div>
        ))}
      </nav>

      <section className="convo">
        <div className="convo-scroll" ref={scroller}>
          <div className="convo-inner">
            {messages.length === 0 && !live && (
              <div className="empty" style={{ paddingTop: "12vh" }}>
                <h2>{t("chat.title")}</h2>
                <p style={{ maxWidth: "52ch" }}>{t("chat.empty")}</p>
                <div className="suggest">
                  {(["chat.suggest1", "chat.suggest2", "chat.suggest3"] as const).map((k) => (
                    <button key={k} className="btn" onClick={() => send(t(k))}>{t(k)}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => m.role === "user" ? (
              <div key={m.id} className="q">{m.content}</div>
            ) : (
              <article key={m.id} className="a">
                <div className="a-cues" aria-label={t("chat.sources")}>
                  {m.sources.map((s: any) => <SourceCue key={s.n} s={s} n={s.n} />)}
                </div>
                <div className="a-body">
                  <Markdown text={m.content} cites />
                  <div className="a-tools">
                    <CopyButton text={m.content} />
                    <button className="btn sm ghost" onClick={() => speak(m.content, guessLang(m.content, t.lang), kokoro)}><Volume2 size={14} /> {t("common.readAloud")}</button>
                  </div>
                </div>
              </article>
            ))}
            {live && (
              <article className="a" aria-busy="true">
                <div className="a-cues">
                  {live.sources.slice(0, 8).map((s, i) => <SourceCue key={i} s={s} n={i + 1} />)}
                </div>
                <div className="a-body">
                  {live.text ? <Markdown text={live.text} cites className="md typing" /> : <p className="placeholder-line">{t("chat.searching")}</p>}
                </div>
              </article>
            )}
          </div>
        </div>
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <div className="composer-box">
            <textarea ref={box} rows={1} value={draft} placeholder={t("chat.placeholder")} aria-label={t("chat.placeholder")}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
            <div className="composer-tools">
              <button type="button" className="btn sm web-toggle" aria-pressed={web} title={t("chat.webHint")} onClick={() => setWeb(!web)}>
                <Globe size={15} /> {t("chat.web")}
              </button>
              <button type="submit" className="btn primary icon" aria-label={t("chat.send")} disabled={!draft.trim() || !!live}><ArrowUp size={18} /></button>
            </div>
          </div>
        </form>
      </section>
    </div>
  );
}
