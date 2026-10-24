(function () {
  var T = window.__TAURI__;
  var es = (navigator.language || "").toLowerCase().indexOf("es") === 0;
  var tx = es
    ? { rec: "Grabando", paused: "En pausa", wait: "Escuchando…", open: "Volver a la reunión" }
    : { rec: "Recording", paused: "Paused", wait: "Listening…", open: "Back to the meeting" };
  document.documentElement.lang = es ? "es" : "en";

  var root = document.documentElement;
  var island = document.getElementById("island");
  var words = document.getElementById("words");
  var clock = document.getElementById("clock");
  var label = document.getElementById("label");
  island.setAttribute("aria-label", tx.open);

  var info = { os: "", notch: false, notch_w: 190, header: 34, size: [440, 108] };
  var meeting = "";
  var opened = false;

  function invoke(cmd, args) {
    if (!T) return Promise.resolve(null);
    return T.core.invoke(cmd, args || {});
  }

  // ---------- sizing and the open / close animation (the transitions are in notch.css) ----------
  function applyInfo(next) {
    if (next) {
      ["os", "notch", "notch_w", "header", "size"].forEach(function (k) { if (next[k] !== undefined) info[k] = next[k]; });
    }
    root.dataset.os = info.os || "";
    root.dataset.notch = info.notch ? "1" : "0";
    island.style.setProperty("--header", info.header + "px");
    island.style.setProperty("--notch-w", info.notch_w + "px");
    island.style.setProperty("--seed-w", (info.notch ? info.notch_w : 150) + "px");
    island.style.setProperty("--seed-h", (info.notch ? info.header : 0) + "px");
    island.style.setProperty("--w", info.size[0] + "px");
    island.style.setProperty("--h", info.size[1] + "px");
  }

  function open() {
    if (opened) return;
    opened = true;
    // start from the notch (or from nothing) without animating, then let it grow
    island.classList.add("seed", "no-anim");
    void island.offsetWidth;
    island.classList.remove("no-anim");
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { island.classList.remove("seed"); });
    });
  }

  function close() {
    opened = false;
    island.classList.add("seed");
  }

  // ---------- the words ----------
  function mmss(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var two = function (n) { return (n < 10 ? "0" : "") + n; };
    return h ? h + ":" + two(m) + ":" + two(s) : m + ":" + two(s);
  }

  function span(cls, text) {
    var e = document.createElement("span");
    e.className = cls;
    e.textContent = text + " ";
    return e;
  }

  function render(state) {
    state = state || {};
    meeting = state.meeting_id || meeting;
    island.classList.toggle("paused", !!state.paused);
    label.textContent = state.paused ? tx.paused : (state.title ? state.title : tx.rec);
    clock.textContent = mmss(state.elapsed);
    var next = document.createDocumentFragment();
    var any = false;
    (state.lines || []).slice(-4).forEach(function (l) {
      if (l && l.text) { next.appendChild(span(l.source === "you" ? "you" : "others", l.text)); any = true; }
    });
    var sp = state.speaking || {};
    ["others", "you"].forEach(function (k) {
      if (sp[k]) { next.appendChild(span(k + " live", sp[k])); any = true; }
    });
    if (!any) next.appendChild(span("idle", tx.wait));
    words.textContent = "";
    words.appendChild(next);
  }

  island.addEventListener("click", function () { if (meeting) invoke("notch_open_meeting", { id: meeting }); });
  island.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && meeting) invoke("notch_open_meeting", { id: meeting });
  });

  // ---------- events from the app ----------
  function onShow(p) {
    p = p || {};
    applyInfo(p);
    render(p.state);
    open();
  }

  function preview() {
    // opened in a browser, for looking at the design: notch.html?os=macos&notch=1&paused=1
    var q = new URLSearchParams(location.search);
    document.body.classList.add("preview");
    var notch = q.get("notch") === "1";
    var header = notch ? 32 : 34;
    onShow({
      os: q.get("os") || "macos", notch: notch, notch_w: 190, header: header, size: [440, header + 74],
      state: {
        meeting_id: "demo", paused: q.get("paused") === "1", title: q.get("title") || "", elapsed: 754,
        lines: [
          { source: "others", text: "So the budget for the second quarter is already approved, we only need the final numbers from Sara." },
          { source: "you", text: "Can you send them before Friday? I would like to review them with the team." },
          { source: "others", text: "Of course. I will also include the forecast for the next two months." }
        ],
        speaking: q.get("empty") === "1" ? {} : { you: "Perfect, that works for us" }
      }
    });
    if (q.get("empty") === "1") render({ elapsed: 3, lines: [], speaking: {} });
  }

  if (!T) { preview(); return; }

  applyInfo();
  T.event.listen("notch-show", function (e) { onShow(e.payload); });
  T.event.listen("notch-update", function (e) { render(e.payload); });
  T.event.listen("notch-hide", function () { close(); });
  invoke("notch_state").then(function (s) { if (s && s.size) onShow(s); }).catch(function () {});
})();
