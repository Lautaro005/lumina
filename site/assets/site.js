// Lumina website: install tabs with OS detection, copy buttons, theme toggle and the live demo sheet.
(function () {
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("lumina-theme");
    if (saved) root.dataset.theme = saved;
  } catch (e) { /* storage blocked */ }

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-theme-toggle]");
    if (!t) return;
    var dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("lumina-theme", root.dataset.theme); } catch (err) { /* ignore */ }
  });

  var CMDS = {
    unix: "curl -fsSL https://lautaro005.github.io/lumina/install.sh | sh",
    win: "irm https://lautaro005.github.io/lumina/install.ps1 | iex",
  };
  var isWin = /Win/i.test(navigator.platform || "") || /Windows/i.test(navigator.userAgent);

  function setTab(group, os) {
    group.querySelectorAll("[role=tab]").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.os === os));
      b.tabIndex = b.dataset.os === os ? 0 : -1;
    });
    var code = group.querySelector("[data-cmd]");
    if (code) code.innerHTML = '<span class="p">' + (os === "win" ? "PS&gt; " : "$ ") + "</span>" + CMDS[os];
    var hint = group.querySelector("[data-after]");
    if (hint) hint.innerHTML = os === "win"
      ? "Paste it in <b>PowerShell</b>. Then type <code>lumina</code> to open it, or <code>lumina app</code> for the desktop app."
      : "Paste it in <b>Terminal</b>. Then type <code>lumina</code> to open it, or <code>lumina app</code> for the desktop app.";
    group.dataset.os = os;
  }

  document.querySelectorAll("[data-install]").forEach(function (group) {
    setTab(group, isWin ? "win" : "unix");
    group.querySelectorAll("[role=tab]").forEach(function (b) {
      b.addEventListener("click", function () { setTab(group, b.dataset.os); });
      b.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          var next = group.dataset.os === "unix" ? "win" : "unix";
          setTab(group, next);
          group.querySelector('[data-os="' + next + '"]').focus();
        }
      });
    });
    var btn = group.querySelector(".copy");
    if (btn) btn.addEventListener("click", function () { copy(btn, CMDS[group.dataset.os]); });
  });

  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var src = document.getElementById(btn.dataset.copy);
      copy(btn, src ? src.textContent : "");
    });
  });

  function copy(btn, text) {
    var label = btn.querySelector("span") || btn;
    var old = label.textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () {
      label.textContent = "Copied";
    }, function () {
      label.textContent = "Select and copy";
    }).finally(function () {
      setTimeout(function () { label.textContent = old; }, 1600);
    });
  }

  // ---- the demo sheet: shorthand typed in the cue column, Lumina's note appears beside it ----
  var demo = document.querySelector("[data-demo]");
  if (!demo) return;
  var rows = [
    { cue: "Q3 budget, cut?", note: "<ul><li>Julián proposes cutting marketing by 10% to fund two support hires.</li><li>Marta wants the impact on leads before Friday.</li></ul>",
      said: ["Julián", "What if we take ten percent out of marketing and hire two people for support?"] },
    { cue: "pricing page → when", note: "<ul><li>New pricing page goes live <b>November 5</b>.</li><li>Owner: Marta Gómez. Team plan stays at USD 29 per seat.</li></ul>",
      said: ["Marta", "The pricing page ships on November fifth, I own it."] },
    { cue: "annual plan?", note: "<ul><li>Open question: offer a discounted annual plan? Nobody decided; revisit next sync.</li></ul>",
      said: ["Ana", "Do we need an annual plan with a discount?"] },
  ];
  var cues = demo.querySelectorAll("[data-cue]");
  var bodies = demo.querySelectorAll("[data-body]");
  var who = document.querySelector("[data-who]");
  var line = document.querySelector("[data-line]");
  var clock = document.querySelector("[data-clock]");
  // phones and reduced motion get the finished page instead of the typing demo
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches || matchMedia("(max-width: 640px)").matches;
  var seconds = 12 * 60 + 41;

  function finalState() {
    rows.forEach(function (r, i) { cues[i].textContent = r.cue; bodies[i].innerHTML = r.note; bodies[i].classList.remove("muted"); });
    if (who) { who.textContent = rows[2].said[0]; line.textContent = rows[2].said[1]; }
  }
  if (reduce) { finalState(); return; }

  setInterval(function () {
    seconds += 1;
    if (clock) clock.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }, 1000);

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  async function typeInto(el, text) {
    el.classList.add("typing");
    for (var i = 1; i <= text.length; i++) { el.textContent = text.slice(0, i); await sleep(55 + Math.random() * 60); }
    el.classList.remove("typing");
  }
  async function hear(r) {
    if (!who) return;
    who.textContent = r.said[0];
    var words = r.said[1].split(" ");
    for (var i = 1; i <= words.length; i++) { line.textContent = words.slice(0, i).join(" "); await sleep(140); }
  }
  async function loop() {
    for (;;) {
      rows.forEach(function (_, i) { cues[i].textContent = ""; bodies[i].innerHTML = "&nbsp;"; bodies[i].classList.add("muted"); bodies[i].classList.remove("fresh"); });
      for (var i = 0; i < rows.length; i++) {
        await hear(rows[i]);
        await typeInto(cues[i], rows[i].cue);
        await sleep(700);
        bodies[i].classList.remove("muted");
        bodies[i].innerHTML = rows[i].note;
        void bodies[i].offsetWidth;
        bodies[i].classList.add("fresh");
        await sleep(1600);
      }
      await sleep(5200);
    }
  }
  var io = new IntersectionObserver(function (entries) {
    if (entries[0].isIntersecting) { io.disconnect(); loop(); }
  });
  io.observe(demo);
})();
