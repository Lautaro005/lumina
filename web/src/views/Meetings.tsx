import { useEffect, useRef, useState } from "react";
import { FileAudio, Plus, Search } from "lucide-react";
import { api, fmtClock, type Meeting } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";

export default function Meetings() {
  const t = useT();
  const go = useStore((s) => s.go);
  const toast = useStore((s) => s.toast);
  const liveId = useStore((s) => s.live.meetingId);
  const [rows, setRows] = useState<Meeting[] | null>(null);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [range, setRange] = useState("0");
  const [tags, setTags] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const since = range === "0" ? 0 : Date.now() / 1000 - Number(range) * 86400;
    const timer = setTimeout(() => api.meetings({ q, tag, since }).then(setRows).catch(() => setRows([])), q ? 250 : 0);
    return () => clearTimeout(timer);
  }, [q, tag, range]);
  useEffect(() => { api.tags().then(setTags).catch(() => {}); }, []);

  const create = async () => {
    const m = await api.createMeeting("");
    go(`/meeting/${m.id}`);
  };
  const importFile = async (f?: File) => {
    if (!f) return;
    try {
      const m = await api.importRecording(f);
      go(`/meeting/${m.id}`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const filtered = q || tag || range !== "0";
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1>{t("list.title")}</h1>
        </div>
        <div className="actions">
          <input ref={fileRef} type="file" accept="audio/*,video/*" hidden onChange={(e) => importFile(e.target.files?.[0])} />
          <button className="btn" onClick={() => fileRef.current?.click()}><FileAudio size={16} /> {t("meeting.import")}</button>
          <button className="btn primary" onClick={create}><Plus size={16} /> {t("nav.new")}</button>
        </div>
      </div>

      <div className="toolbar">
        <div className="search-wrap">
          <Search size={16} />
          <input className="input" placeholder={t("list.search")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("list.search")} />
        </div>
        <select className="input" style={{ width: "auto" }} value={tag} onChange={(e) => setTag(e.target.value)} aria-label={t("common.tags")}>
          <option value="">{t("list.allTags")}</option>
          {tags.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        <select className="input" style={{ width: "auto" }} value={range} onChange={(e) => setRange(e.target.value)} aria-label={t("list.date")}>
          <option value="0">{t("list.anyDate")}</option>
          <option value="7">{t("list.week")}</option>
          <option value="30">{t("list.month")}</option>
        </select>
      </div>

      {rows && rows.length === 0 ? (
        <div className="panel empty">
          <h2>{filtered ? t("list.noMatch") : t("list.empty")}</h2>
          {!filtered && <p>{t("list.emptyHint")}</p>}
          {!filtered && (
            <div className="actions" style={{ justifyContent: "center" }}>
              <button className="btn record" onClick={create}><span className="dot" style={{ background: "#fff" }} /> {t("meeting.start")}</button>
              <button className="btn" onClick={() => fileRef.current?.click()}><FileAudio size={16} /> {t("meeting.import")}</button>
            </div>
          )}
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{t("list.date")}</th>
              <th>{t("list.meeting")}</th>
              <th className="num">{t("list.length")}</th>
            </tr>
          </thead>
          <tbody>
            {(rows || []).map((m) => {
              const d = new Date((m.started || m.created) * 1000);
              return (
                <tr key={m.id} className="click" tabIndex={0} onClick={() => go(`/meeting/${m.id}`)}
                  onKeyDown={(e) => e.key === "Enter" && go(`/meeting/${m.id}`)}>
                  <td className="date">
                    {d.toLocaleDateString(t.lang, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" })}
                    <div style={{ color: "var(--ink-3)", fontSize: 12.5 }}>{d.toLocaleTimeString(t.lang, { hour: "2-digit", minute: "2-digit" })}</div>
                  </td>
                  <td>
                    <div className="t-title">
                      {m.title || t("common.untitled")}{" "}
                      {(m.status === "live" || liveId === m.id) && <span className="chip live" style={{ verticalAlign: 2 }}><i className="dot live" /> {t("list.live")}</span>}
                    </div>
                    {m.summary?.overview && <div className="t-sub">{m.summary.overview}</div>}
                    {m.tags.length > 0 && <div className="t-tags">{m.tags.map((x) => <span key={x} className="tag">{x}</span>)}</div>}
                  </td>
                  <td className="num">{m.duration ? fmtClock(m.duration) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
