/* LocalTC app page. Talks to the local server (ui/server.py): JSON calls under /api, live events on /api/events. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const mhz = (f) => (f == null ? "---.---" : Number(f).toFixed(3));
const clock = (t) => {
  if (t == null) return "";
  const s = Math.max(0, Math.floor(t)), m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

const S = { state: {}, flight: {}, own: null, traffic: [], alerts: [], settings: null, models: null, devices: null, radioLines: 0 };

async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(`/api/${path}`, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status}`);
  return data;
}

let toastTimer;
function toast(text, error = false) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast" + (error ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), error ? 6000 : 3000);
}
const fail = (e) => toast(e.message || String(e), true);

/* ---------- live events ---------- */

function connect() {
  const es = new EventSource("/api/events");
  const on = (kind, fn) => es.addEventListener(kind, (e) => fn(JSON.parse(e.data)));
  on("state", setState);
  on("radio_history", (lines) => { clearLog(); lines.forEach(addLine); });
  on("radio", addLine);
  on("own", setOwn);
  on("traffic", (t) => { S.traffic = t; MapView.traffic(t); });
  on("flight", setFlight);
  on("ptt", (p) => $("#btn-ptt").classList.toggle("down", p.down));
  on("jobs", (jobs) => { S.state.jobs = jobs; Settings.jobs(jobs); });
  on("dev", (d) => Dev.event(d));
  on("airport", ({ icao }) => {  // the flight fetched an airport's layout: draw it if it's on the route
    const wanted = [S.state.plan?.origin, S.state.plan?.destination, S.flight.origin, S.flight.destination];
    if (wanted.includes(icao)) MapView.airport(icao);
  });
  es.onerror = () => setStatus({ status: "error", detail: "LocalTC isn't answering" });
  es.onopen = () => setStatus(S.state);
}

function setState(st) {
  S.state = st;
  setStatus(st);
  if (st.flight && Object.keys(st.flight).length) setFlight(st.flight);
  $("#sw-copilot").classList.toggle("on", st.copilot && st.copilot !== "off");
  $("#sw-copilot").title = `Copilot ${st.copilot !== "off" ? "on" : "off"}: ${st.copilot_mode === "assist" ? "reads back and changes frequencies" : "works the whole radio"}`;
  $("#sw-atc").classList.toggle("on", !st.muted);
  $("#devbar").hidden = !st.dev_mode;
  $("#btn-ptt").classList.toggle("off", !st.voice);
  $("#help-ptt").textContent = pttName(st.ptt);
  $("#btn-ptt").title = st.voice ? `Hold to talk (or ${pttName(st.ptt)})` : "Voice input is off (Quick Settings > Push-to-talk)";
  if (!S.flight.callsign) planHeader(st.plan);
  MapView.plan(st.plan);
  Settings.jobs(st.jobs || {});
}

function pttName(p) {
  if (!p) return "Right Ctrl";
  if (p.mode === "joystick") return p.joystick;
  if (p.mode === "enter") return "the headset button";
  return keyLabel(p.key);
}

function setStatus(st) {
  const el = $("#status"), text = $("#status-text"), btn = $("#btn-startstop");
  el.className = "status " + (st.status || "idle");
  const label = { idle: "Not flying", starting: "Starting ...", running: "Connected", stopping: "Stopping ...", error: "Error" }[st.status] || st.status;
  text.textContent = label;
  el.title = st.detail || label;
  const flying = st.status === "running" || st.status === "starting";
  btn.textContent = flying ? "Stop" : "Start";
  btn.classList.toggle("danger", flying);
  btn.disabled = st.status === "stopping";
  if (st.status === "error" && st.detail) toast(st.detail, true);
}

/* ---------- the radio log ---------- */

function clearLog() {
  $$("#log .line").forEach((l) => l.remove());
  S.radioLines = 0;
  S.alerts = [];
  $("#log-empty").hidden = false;
  updateAlerts();
}

function addLine(l) {
  const log = $("#log");
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const row = document.createElement("div");
  row.className = `line ${l.kind}` + (l.level ? ` ${l.level}` : "") + (l.kind === "readback" && !l.ok ? " bad" : "");
  let body;
  switch (l.kind) {
    case "atc":
      body = `<span class="who">ATC</span><span class="st">${esc(l.station)} ${mhz(l.mhz)}</span><div class="body">${esc(l.text)}</div>`; break;
    case "pilot":
      body = `<span class="who">YOU</span><span class="body">${esc(l.text)}</span>${l.unclear ? '<span class="unclear">(unclear)</span>' : ""}`; break;
    case "copilot":
      body = `<span class="who">COPILOT</span><span class="body">${esc(l.text)}</span>`; break;
    case "atis":
      body = `<span class="who">ATIS</span><span class="st">${esc(l.station)}</span><div class="body">${esc(l.text)}</div>`; break;
    case "tuned":
      body = `<span class="body">COM${l.radio || 1} ${mhz(l.mhz)} &nbsp;${esc(l.text)}</span>`; break;
    case "note":
      body = `<span class="body">&#9873; ${esc(l.text)}</span>`; break;
    default:
      body = `<span class="body">${l.kind === "phase" ? "&mdash; " : ""}${esc(l.text)}</span>`;
  }
  row.innerHTML = `<span class="t">${clock(l.t)}</span><div>${body}</div>`;
  log.appendChild(row);
  $("#log-empty").hidden = true;
  if (++S.radioLines > 500) log.querySelector(".line")?.remove();
  if (stick) log.scrollTop = log.scrollHeight;
  if (l.kind === "alert") { S.alerts.push(l); updateAlerts(); }
}

function updateAlerts() {
  const n = S.alerts.length, badge = $("#alert-count");
  badge.hidden = n === 0;
  badge.textContent = n;
  $("#alerts-list").innerHTML = n ? S.alerts.map((a) => `<div class="al"><span class="muted">${clock(a.t)}</span> ${esc(a.text)}</div>`).join("")
    : '<p class="muted">None this flight.</p>';
}

/* ---------- top bar and the ATC tab ---------- */

function setOwn(o) {
  S.own = o;
  $("#com1").textContent = mhz(o.com1);
  $("#com2").textContent = o.com2 ? mhz(o.com2) : "OFF";
  $("#squawk").textContent = o.squawk || "----";
  $("#xmode").textContent = { alt: "ALT", on: "ON", standby: "STBY", off: "OFF", ground: "GND", test: "TST" }[o.xpdr] || "";
  markTuned();
  MapView.own(o);
}

function planHeader(plan) {
  $("#fl-callsign").textContent = plan?.callsign || "—";
  $("#fl-type").textContent = plan?.aircraft ? `[${plan.aircraft}]` : "";
  $("#fl-plan-ok").hidden = !plan;
  $("#fl-dest").textContent = plan?.destination || "—";
}

function setFlight(f) {
  S.flight = f || {};
  const plan = S.state.plan;
  $("#fl-callsign").textContent = f.callsign || plan?.callsign || "—";
  $("#fl-type").textContent = plan?.aircraft ? `[${plan.aircraft}]` : "";
  $("#fl-plan-ok").hidden = !plan;
  $("#fl-dest").textContent = f.destination || plan?.destination || "—";
  $("#fl-squawk").textContent = f.squawk || "—";
  $("#fl-alt").textContent = f.altitude_ft ? Number(f.altitude_ft).toLocaleString() : "—";
  $("#fl-rwy-label").textContent = f.approach ? "Appr" : "Rwy";
  $("#fl-rwy").textContent = f.approach || f.runway || "—";
  let phase = f.phase_label || "—";
  if (f.ete) phase += `  ·  ${f.ete.nm} nm, ${f.ete.min} min`;
  $("#fl-phase").textContent = phase;
  let next = "—";
  if (f.pending) next = `Read back: ${Object.values(f.pending.expected).join(", ")}`;
  else if (f.expected) next = `Contact ${f.expected.station} ${mhz(f.expected.mhz)}`;
  else if (f.tuned) next = `On ${f.tuned.station}`;
  $("#fl-next").textContent = next;
  const ap = f.airport;
  $("#ap-icao").textContent = ap?.icao || "----";
  $("#ap-name").textContent = ap?.name || (ap ? "" : "No airport yet");
  const freqs = $("#ap-freqs");
  if (ap?.frequencies?.length) {
    const key = JSON.stringify(ap.frequencies) + ap.icao;
    if (freqs.dataset.key !== key) {
      freqs.dataset.key = key;
      freqs.innerHTML = ap.frequencies.map((q) => `<div class="fq" data-mhz="${q.mhz}" title="Tune COM1 to ${mhz(q.mhz)}${q.others.length ? " (also " + q.others.map(mhz).join(", ") + ")" : ""}">
        <span class="fl">${esc(q.label)}</span><span class="fv">${mhz(q.mhz)}</span></div>`).join("");
    }
  }
  markTuned();
}

function markTuned() {
  const com1 = S.own?.com1;
  $$("#ap-freqs .fq").forEach((el) => el.classList.toggle("tuned", com1 != null && Math.abs(Number(el.dataset.mhz) - com1) < 0.004));
}

$("#ap-freqs").addEventListener("click", (e) => {
  const fq = e.target.closest(".fq");
  if (fq) tune(Number(fq.dataset.mhz));
});

async function tune(f) {
  try { await api("radio/tune", { mhz: f }); toast(`COM1 ${mhz(f)}`); } catch (e) { fail(e); }
}

/* ---------- footer: talk, type, switches ---------- */

async function transmit() {
  const input = $("#tx-text"), text = input.value.trim();
  if (!text) return;
  try { await api("radio/say", { text }); input.value = ""; } catch (e) { fail(e); }
}
$("#tx-text").addEventListener("keydown", (e) => { if (e.key === "Enter") transmit(); });
$("#btn-send").onclick = transmit;
$("#btn-clear").onclick = () => { $("#tx-text").value = ""; $("#tx-text").focus(); };

let pttDown = false;
async function ptt(down) {
  if (down === pttDown) return;
  pttDown = down;
  $("#btn-ptt").classList.toggle("down", down);
  try { await api("radio/ptt", { down }); } catch (e) { pttDown = false; $("#btn-ptt").classList.remove("down"); if (down) fail(e); }
}
const pttBtn = $("#btn-ptt");
pttBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); pttBtn.setPointerCapture(e.pointerId); ptt(true); });
pttBtn.addEventListener("pointerup", () => ptt(false));
pttBtn.addEventListener("pointercancel", () => ptt(false));

$("#sw-atc").onclick = async () => {
  try { await api("radio/mute", { muted: !S.state.muted }); } catch (e) { fail(e); }
};
$("#sw-copilot").onclick = async () => {
  const on = !(S.state.copilot && S.state.copilot !== "off");
  try { await api("radio/copilot", { on }); } catch (e) { fail(e); }
};

$("#btn-startstop").onclick = async () => {
  const flying = S.state.status === "running" || S.state.status === "starting";
  try { await api(flying ? "flight/stop" : "flight/start", {}); } catch (e) { fail(e); }
};

/* ---------- tabs and dialogs ---------- */

function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${name}`));
  if (name === "settings") Settings.load();
  if (name === "map") MapView.show();
  if (name === "lookup") Lookup.show();
}
$$(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
$("#btn-settings").onclick = () => showTab("settings");
$("#btn-alerts").onclick = () => $("#dlg-alerts").showModal();
$("#btn-help").onclick = () => $("#dlg-help").showModal();

// Links open in the real browser, not inside the app window.
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='http']");
  if (!a) return;
  e.preventDefault();
  api("open", { what: "url", url: a.href }).catch(fail);
});

/* ---------- New Flight ---------- */

const Flight = {
  fetched: null,
  open() {
    const plan = S.state.plan;
    $("#plan-error").hidden = true;
    $("#sb-user").value = S.settings?.settings?.ui?.simbrief_user || localStorage.getItem("sbUser") || "";
    if (plan && plan.source === "manual") this.fill(plan);
    this.fetched = null;
    $("#sb-result").hidden = true;
    this.sub(plan?.source === "manual" ? "manual" : "simbrief");
    $("#plan-note").textContent = S.state.status === "running" ? "Starting a new flight ends the current one." : "";
    $("#dlg-flight").showModal();
  },
  fill(p) {
    for (const k of ["callsign", "aircraft", "origin", "destination", "alternate", "route"]) $(`#mp-${k}`).value = p[k] || "";
    $("#mp-cruise").value = p.cruise_ft || "";
  },
  sub(name) {
    this.mode = name;
    $$(".subtab").forEach((b) => b.classList.toggle("active", b.dataset.sub === name));
    $$("#dlg-flight .sub").forEach((s) => s.classList.toggle("active", s.id === `sub-${name}`));
  },
  async fetch() {
    const user = $("#sb-user").value.trim();
    const btn = $("#sb-fetch");
    btn.disabled = true; btn.textContent = "Fetching ...";
    $("#plan-error").hidden = true;
    try {
      const r = await api("flight/simbrief", { user });
      try { localStorage.setItem("sbUser", user); } catch {}
      this.fetched = r.plan;
      const p = r.plan;
      const alt = p.cruise_ft >= 18000 ? `FL${String(Math.round(p.cruise_ft / 100)).padStart(3, "0")}` : `${p.cruise_ft.toLocaleString()} ft`;
      $("#sb-result").innerHTML = `<h3>${esc(p.callsign)} &nbsp; ${esc(p.origin)} &rarr; ${esc(p.destination)}</h3>
        <div>${esc(p.aircraft)} ${p.registration ? "(" + esc(p.registration) + ")" : ""} &middot; cruise ${alt}${p.alternate ? " &middot; alternate " + esc(p.alternate) : ""}</div>
        <div class="muted small">${p.sid ? "SID " + esc(p.sid) + " &middot; " : ""}${p.star ? "STAR " + esc(p.star) + " &middot; " : ""}${p.dep_runway ? "departing " + esc(p.dep_runway) : ""}${p.arr_runway ? ", arriving " + esc(p.arr_runway) : ""} &middot; ${p.fixes.length} fixes</div>
        <div class="route">${esc(p.route)}</div>`;
      $("#sb-result").hidden = false;
    } catch (e) {
      $("#plan-error").textContent = e.message; $("#plan-error").hidden = false;
    } finally { btn.disabled = false; btn.textContent = "Fetch"; }
  },
  async save(start) {
    $("#plan-error").hidden = true;
    let body;
    if (this.mode === "simbrief") {
      if (!this.fetched) { $("#plan-error").textContent = "Fetch your SimBrief plan first."; $("#plan-error").hidden = false; return; }
      body = { plan: this.fetched };
    } else {
      body = Object.fromEntries(["callsign", "aircraft", "origin", "destination", "alternate", "route", "cruise"].map((k) => [k, $(`#mp-${k}`).value]));
    }
    body.start = start;
    try {
      const r = await api("flight/plan", body);
      $("#dlg-flight").close();
      toast(start ? `Starting ${r.summary}` : `Plan saved: ${r.summary}`);
    } catch (e) { $("#plan-error").textContent = e.message; $("#plan-error").hidden = false; }
  },
};
$("#btn-newflight").onclick = () => Flight.open();
$$(".subtab").forEach((b) => (b.onclick = () => Flight.sub(b.dataset.sub)));
$("#sb-fetch").onclick = () => Flight.fetch();
$("#sb-user").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); Flight.fetch(); } });
$("#plan-save").onclick = () => Flight.save(false);
$("#plan-start").onclick = () => Flight.save(true);

/* ---------- Dev mode ---------- */

const Dev = {
  event() {},  // raw events arrive here in dev mode; the log file has them too
};
$("#btn-note").onclick = async () => {
  const text = $("#note-text").value.trim();
  try { await api("dev/note", { text }); $("#note-text").value = ""; toast("Marked in the recording"); } catch (e) { fail(e); }
};
$("#note-text").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#btn-note").click(); });
$("#btn-export").onclick = () => exportSession();
async function exportSession(session) {
  try {
    toast("Packing the session ...");
    const r = await api("dev/export", session ? { session } : {});
    toast(`Saved ${r.path} (${r.size_mb} MB)`);
  } catch (e) { fail(e); }
}

/* ---------- push-to-talk keys: browser key codes -> the names LocalTC uses (pynput) ---------- */

const KEYS = {
  ControlRight: "ctrl_r", ControlLeft: "ctrl_l", AltRight: "alt_r", AltLeft: "alt_l", ShiftRight: "shift_r", ShiftLeft: "shift_l",
  CapsLock: "caps_lock", ScrollLock: "scroll_lock", Pause: "pause", Insert: "insert", Home: "home", End: "end",
  PageUp: "page_up", PageDown: "page_down", Delete: "delete", Space: "space", Tab: "tab", ContextMenu: "menu",
  MetaRight: "cmd_r", Backquote: "`", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]", Backslash: "\\",
  Semicolon: ";", Quote: "'", Comma: ",", Period: ".", Slash: "/",
};
function keyName(e) {
  if (KEYS[e.code]) return KEYS[e.code];
  let m = /^F(\d{1,2})$/.exec(e.code);
  if (m && Number(m[1]) <= 20) return `f${m[1]}`;
  m = /^Key([A-Z])$/.exec(e.code);
  if (m) return m[1].toLowerCase();
  m = /^Digit(\d)$/.exec(e.code);
  if (m) return m[1];
  return null;
}
function keyLabel(name) {
  const labels = { ctrl_r: "Right Ctrl", ctrl_l: "Left Ctrl", alt_r: "Right Alt", alt_l: "Left Alt", shift_r: "Right Shift",
    shift_l: "Left Shift", caps_lock: "Caps Lock", scroll_lock: "Scroll Lock", page_up: "Page Up", page_down: "Page Down",
    cmd_r: "Right Win", menu: "Menu", space: "Space", tab: "Tab" };
  if (!name) return "none";
  return labels[name] || (name.length === 1 ? name.toUpperCase() : name.replace(/^f(\d+)$/, "F$1").replace(/_/g, " "));
}

/* ---------- Quick Settings ---------- */

const Settings = {
  seenJobs: new Set(),
  async load() {
    const body = $("#settings-body");
    try {
      const [s, m, d] = await Promise.all([api("settings"), api("models"), S.devices ? Promise.resolve(S.devices) : api("devices")]);
      S.settings = s; S.models = m; S.devices = d;
      this.render();
    } catch (e) { body.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  },
  async save(section, key, value) {
    try {
      const r = await api("settings", { settings: { [section]: { [key]: value } } });
      S.settings.settings[section][key] = value;
      $("#restart-bar").hidden = !r.restart;
      $("#saved-note").textContent = "Saved";
      setTimeout(() => ($("#saved-note").textContent = ""), 1500);
      return true;
    } catch (e) { fail(e); return false; }
  },
  async refreshModels() {
    S.models = await api("models");
    this.render();
  },
  statusOf(kind) { return S.models.status.find((x) => x.kind === kind); },
  render() {
    const st = S.settings.settings, m = S.models, cat = m.catalog, hw = m.hardware;
    const opt = (list, cur) => list.map((o) => `<option value="${esc(o.id)}" ${o.id === cur ? "selected" : ""}>${esc(o.label)}${o.tested ? "" : ""} — ${esc(o.speed)}, ${esc(o.quality)} (${o.size_mb >= 1000 ? (o.size_mb / 1000).toFixed(1) + " GB" : o.size_mb + " MB"})</option>`).join("");
    const llmIds = new Set(cat.llm.map((o) => o.id));
    const extra = (m.ollama.installed || []).filter((n) => !llmIds.has(n) && !llmIds.has(n.replace(/:latest$/, "")));
    const llmCur = st.llm.enabled ? st.llm.model : "__off";
    const profileCur = m.catalog.profiles.find((p) => p.llm === st.llm.model && (p.whisper === st.voice.model || (st.voice.model === "auto" && p.whisper === m.whisper_model)) && p.voice === st.tts.voice);
    const gpu = hw.gpu ? `${esc(hw.gpu)} (${hw.gpu_vram_gb} GB)` : "no NVIDIA GPU";
    const devOpts = (list, cur) => `<option value="" ${!cur ? "selected" : ""}>System default</option>` +
      list.map((d) => `<option value="${esc(d.name)}" ${cur && d.name.toLowerCase().includes(String(cur).toLowerCase()) ? "selected" : ""}>${esc(d.name)}${d.default ? " (default)" : ""}</option>`).join("");
    const note = (list, id) => esc(list.find((o) => o.id === id)?.note || "");
    const status = (kind) => {
      const s = this.statusOf(kind);
      if (!s) return '<span class="mstatus muted">off</span>';
      return s.installed ? '<span class="mstatus ok">&#10003; installed</span>'
        : `<span class="mstatus missing">${esc(s.detail || "not downloaded")}</span> <button class="btn small" data-install="${kind}">Download</button>`;
    };
    const missing = m.status.filter((s) => !s.installed).length;
    $("#settings-body").innerHTML = `
      <div class="card">
        <h3>Performance</h3>
        <div class="hw">This computer: ${hw.cpu_cores} CPU threads, ${hw.ram_gb} GB RAM, ${gpu}. Recommended: <b>${esc(m.recommended)}</b>.</div>
        <div class="profiles">${cat.profiles.map((p) => `<button class="profile ${profileCur?.id === p.id ? "current" : ""}" data-profile="${p.id}">
          <b>${esc(p.label)}</b>${p.id === m.recommended ? '<span class="rec">recommended</span>' : ""}<div class="small muted">${esc(p.description)}</div></button>`).join("")}</div>
      </div>

      <div class="card">
        <h3>Models</h3>
        <p class="muted small">Bigger models understand and hear better but answer more slowly. All run on this computer.</p>
        <div class="row"><label>Language model (reads your calls)
          <select id="s-llm"><option value="__off" ${llmCur === "__off" ? "selected" : ""}>Off: grammar only (fastest, strict phrasing)</option>${opt(cat.llm, llmCur)}
          ${extra.map((n) => `<option value="${esc(n)}" ${n === llmCur ? "selected" : ""}>${esc(n)} (installed in Ollama, untested)</option>`).join("")}</select>
          <span class="hint">${note(cat.llm, st.llm.model)}${m.ollama.running ? "" : " Ollama isn't running."}</span></label>
          <div>${st.llm.enabled ? status("llm") : ""}</div></div>
        <div class="progress" id="p-llm" hidden><div></div></div>
        <div class="row"><label>Speech recognition (Whisper)
          <select id="s-whisper"><option value="auto" ${st.voice.model === "auto" ? "selected" : ""}>Automatic (${esc(m.whisper_model)})</option>${opt(cat.whisper, st.voice.model)}</select>
          <span class="hint">${note(cat.whisper, st.voice.model === "auto" ? m.whisper_model : st.voice.model)}</span></label>
          <label style="flex:0 1 120px">Runs on<select id="s-device">${["auto", "cpu", "cuda"].map((d) => `<option ${st.voice.device === d ? "selected" : ""} value="${d}">${{ auto: "Automatic", cpu: "CPU", cuda: "NVIDIA GPU" }[d]}</option>`).join("")}</select></label>
          <div>${status("whisper")}</div></div>
        <div class="progress" id="p-whisper" hidden><div></div></div>
        <div class="row"><label>ATC voice (Piper)
          <select id="s-voice">${opt(cat.voice, st.tts.voice)}</select><span class="hint">${note(cat.voice, st.tts.voice)}</span></label>
          <div><button class="btn small" id="s-preview">&#9654; Preview</button> ${status("voice")}</div></div>
        <div class="progress" id="p-voice" hidden><div></div></div>
        ${missing ? `<div class="row"><button class="btn primary" id="s-install-all">Download everything missing</button><span class="muted small">Once; flights then work offline.</span></div>` : ""}
        <div class="muted small" id="job-msg"></div>
      </div>

      <div class="card">
        <h3>Push-to-talk</h3>
        <label class="check-row"><input type="checkbox" id="s-voice-on" ${st.voice.enabled ? "checked" : ""}> Talk to ATC with a microphone</label>
        <div class="row">
          <label>Push-to-talk switch<select id="s-ptt">
            <option value="keyboard" ${st.voice.ptt === "keyboard" ? "selected" : ""}>A keyboard key (works while the sim has focus)</option>
            <option value="joystick" ${st.voice.ptt === "joystick" ? "selected" : ""}>A yoke or joystick button (through the sim)</option>
            <option value="enter" ${st.voice.ptt === "enter" ? "selected" : ""}>Only the headset button in this window</option></select></label>
        </div>
        <div class="row" id="ptt-key-row" ${st.voice.ptt === "keyboard" ? "" : "hidden"}>
          <span>Key</span><span class="keycap" id="s-key">${esc(keyLabel(st.voice.ptt_key))}</span>
          <button class="btn small" id="s-key-set">Change</button><span class="muted small" id="s-key-hint"></span>
        </div>
        <div class="row" id="ptt-joy-row" ${st.voice.ptt === "joystick" ? "" : "hidden"}>
          <label>Button, as MSFS names it<input id="s-joy" value="${esc(st.voice.ptt_joystick)}" placeholder="joystick:0:button:3"></label>
        </div>
        <div class="row"><label>Microphone<select id="s-mic">${devOpts(S.devices.inputs || [], st.voice.input_device)}</select></label></div>
      </div>

      <div class="card">
        <h3>ATC voice</h3>
        <div class="row"><label>Speakers or headset<select id="s-out">${devOpts(S.devices.outputs || [], st.tts.output_device)}</select></label></div>
        <div class="row">
          <label>Volume <input type="range" id="s-volume" min="0" max="1" step="0.05" value="${st.tts.volume}"></label>
          <label>Speed <input type="range" id="s-rate" min="0.8" max="1.5" step="0.05" value="${st.tts.rate}"></label>
          <label>Static <input type="range" id="s-static" min="0" max="1" step="0.05" value="${st.tts.static}"></label>
        </div>
        <label class="check-row"><input type="checkbox" id="s-effect" ${st.tts.radio_effect ? "checked" : ""}> Radio effect (band-pass, compression, squelch)</label>
        <label class="check-row"><input type="checkbox" id="s-atis" ${st.tts.atis ? "checked" : ""}> Read the ATIS aloud while it's tuned</label>
        <label class="check-row"><input type="checkbox" id="s-cpvoice" ${st.tts.copilot ? "checked" : ""}> Speak the copilot's calls too</label>
      </div>

      <div class="card">
        <h3>Copilot</h3>
        <div class="row"><label>When the Copilot switch is on, it
          <select id="s-copilot"><option value="full" ${st.ui.copilot === "full" ? "selected" : ""}>works the whole radio: requests, check-ins, readbacks</option>
          <option value="assist" ${st.ui.copilot === "assist" ? "selected" : ""}>reads back and changes frequencies; you make the calls</option></select></label></div>
      </div>

      <div class="card">
        <h3>ATC</h3>
        <label class="check-row"><input type="checkbox" id="s-unscripted" ${st.atc.unscripted ? "checked" : ""}> Unscripted moments: traffic calls, altitude checks, "how do you read"</label>
        <label class="check-row"><input type="checkbox" id="s-strict" ${st.atc.strict_callsign ? "checked" : ""}> Readbacks must include the callsign</label>
        <div class="row"><label>Center name (where no airport lists one)<input id="s-center" value="${esc(st.atc.center_name)}"></label>
          <label>Center frequency<input id="s-center-mhz" type="number" step="0.005" min="118" max="137" value="${st.atc.center_mhz}"></label></div>
        <div class="row"><label>Understanding
          <select id="s-understand"><option value="primary" ${st.llm.understanding === "primary" ? "selected" : ""}>The model reads every call (most natural)</option>
          <option value="fallback" ${st.llm.understanding === "fallback" ? "selected" : ""}>The grammar first, the model only when stuck (faster)</option></select></label></div>
      </div>

      <div class="card">
        <h3>Sim</h3>
        <div class="row"><label>Connect to<select id="s-source"><option value="live" ${st.source.kind === "live" ? "selected" : ""}>MSFS 2024 (live)</option>
          <option value="replay" ${st.source.kind === "replay" ? "selected" : ""}>A recorded flight (replay, for development)</option></select></label></div>
        <div class="row" ${st.source.kind === "replay" ? "" : "hidden"}><label>Recording<input id="s-replay" value="${esc(st.replay.path)}"></label>
          <label style="flex:0 1 100px">Speed<input id="s-replay-speed" type="number" step="0.5" min="0" value="${st.replay.speed}"></label></div>
        <label class="check-row"><input type="checkbox" id="s-tiles" ${st.ui.map_tiles ? "checked" : ""}> Map background from OpenStreetMap (needs the internet)</label>
      </div>

      <div class="card">
        <h3>Developer mode</h3>
        <label class="check-row"><input type="checkbox" id="s-dev" ${st.ui.dev_mode ? "checked" : ""}> Record every flight with its audio, and show Mark and Export session</label>
        <p class="muted small">A session export is a zip of a flight's recording, the log files, your settings and the flight plan: everything needed to replay the flight exactly and see why ATC did what it did.</p>
        <div class="row"><button class="btn small" data-open="recordings">Open recordings</button><button class="btn small" data-open="logs">Open logs</button>
          <button class="btn small" data-open="data">Open data folder</button></div>
        <div id="s-sessions"></div>
      </div>
      <div class="savebar"><span class="muted small" title="${esc(S.settings.path)}">Changes save as you make them.</span><span class="small green" id="saved-note"></span>
        <span id="restart-bar" hidden><button class="btn small primary" id="s-restart">Restart flight to apply</button></span></div>`;
    this.wire();
    this.jobs(S.state.jobs || {});
    if (st.ui.dev_mode) this.sessions();
  },
  wire() {
    const on = (id, ev, fn) => $(id)?.addEventListener(ev, fn);
    const val = (id) => $(id).value;
    on("#s-llm", "change", async () => {
      const v = val("#s-llm");
      if (v === "__off") await this.save("llm", "enabled", false);
      else { await this.save("llm", "enabled", true); await this.save("llm", "model", v); }
      this.refreshModels();
    });
    on("#s-whisper", "change", async () => { await this.save("voice", "model", val("#s-whisper")); this.refreshModels(); });
    on("#s-device", "change", async () => { await this.save("voice", "device", val("#s-device")); this.refreshModels(); });
    on("#s-voice", "change", async () => { await this.save("tts", "voice", val("#s-voice")); this.refreshModels(); });
    on("#s-preview", "click", async (e) => {
      const b = e.target; b.disabled = true; b.textContent = "Speaking ...";
      try { await api("voice/preview", { voice: val("#s-voice") }); this.refreshModels(); } catch (err) { fail(err); }
      finally { b.disabled = false; b.innerHTML = "&#9654; Preview"; }
    });
    $$("[data-install]").forEach((b) => (b.onclick = () => api("models/install", { kinds: [b.dataset.install] }).catch(fail)));
    on("#s-install-all", "click", () => api("models/install", { kinds: S.models.status.filter((s) => !s.installed).map((s) => s.kind) }).catch(fail));
    $$("[data-profile]").forEach((b) => (b.onclick = async () => {
      try { await api("models/profile", { profile: b.dataset.profile }); S.settings = await api("settings"); this.refreshModels(); toast(`${b.dataset.profile} profile`); } catch (e) { fail(e); }
    }));
    on("#s-voice-on", "change", (e) => this.save("voice", "enabled", e.target.checked));
    on("#s-ptt", "change", async () => { await this.save("voice", "ptt", val("#s-ptt")); this.render(); });
    on("#s-joy", "change", () => this.save("voice", "ptt_joystick", val("#s-joy").trim()));
    on("#s-key-set", "click", () => this.captureKey());
    on("#s-mic", "change", () => this.save("voice", "input_device", val("#s-mic")));
    on("#s-out", "change", () => this.save("tts", "output_device", val("#s-out")));
    for (const [id, key] of [["#s-volume", "volume"], ["#s-rate", "rate"], ["#s-static", "static"]])
      on(id, "change", () => this.save("tts", key, Number(val(id))));
    on("#s-effect", "change", (e) => this.save("tts", "radio_effect", e.target.checked));
    on("#s-atis", "change", (e) => this.save("tts", "atis", e.target.checked));
    on("#s-cpvoice", "change", (e) => this.save("tts", "copilot", e.target.checked));
    on("#s-copilot", "change", () => api("radio/copilot", { mode: val("#s-copilot") }).then(() => this.save("ui", "copilot", val("#s-copilot"))).catch(fail));
    on("#s-unscripted", "change", (e) => this.save("atc", "unscripted", e.target.checked));
    on("#s-strict", "change", (e) => this.save("atc", "strict_callsign", e.target.checked));
    on("#s-center", "change", () => this.save("atc", "center_name", val("#s-center").trim()));
    on("#s-center-mhz", "change", () => this.save("atc", "center_mhz", Number(val("#s-center-mhz"))));
    on("#s-understand", "change", () => this.save("llm", "understanding", val("#s-understand")));
    on("#s-source", "change", async () => { await this.save("source", "kind", val("#s-source")); this.render(); });
    on("#s-replay", "change", () => this.save("replay", "path", val("#s-replay").trim()));
    on("#s-replay-speed", "change", () => this.save("replay", "speed", Number(val("#s-replay-speed"))));
    on("#s-tiles", "change", async (e) => { await this.save("ui", "map_tiles", e.target.checked); S.state.map_tiles = e.target.checked; MapView.tiles(); });
    on("#s-dev", "change", async (e) => { await this.save("ui", "dev_mode", e.target.checked); this.render(); });
    $$("[data-open]").forEach((b) => (b.onclick = () => api("open", { what: b.dataset.open }).catch(fail)));
    on("#s-restart", "click", async () => {
      try { await api("flight/stop", {}); await api("flight/start", {}); $("#restart-bar").hidden = true; showTab("atc"); } catch (e) { fail(e); }
    });
  },
  captureKey() {
    const cap = $("#s-key"), hint = $("#s-key-hint");
    cap.classList.add("listening"); cap.textContent = "press a key";
    hint.textContent = "Esc cancels. Pick a key the sim doesn't use (Right Ctrl, F13, Scroll Lock ...).";
    const handler = async (e) => {
      e.preventDefault(); e.stopPropagation();
      document.removeEventListener("keydown", handler, true);
      cap.classList.remove("listening");
      hint.textContent = "";
      const name = e.code === "Escape" ? null : keyName(e);
      if (e.code !== "Escape" && !name) { toast(`${e.code} can't be a push-to-talk key`, true); }
      if (name && await this.save("voice", "ptt_key", name)) { S.state.ptt.key = name; $("#help-ptt").textContent = keyLabel(name); }
      cap.textContent = keyLabel(S.settings.settings.voice.ptt_key);
    };
    document.addEventListener("keydown", handler, true);
  },
  jobs(jobs) {
    if (!$("#settings-body .card")) return;
    let msg = [];
    for (const kind of ["llm", "whisper", "voice"]) {
      const bar = $(`#p-${kind}`), j = jobs[kind];
      if (!bar) continue;
      bar.hidden = !j || j.state !== "running";
      if (j && j.state === "running") {
        bar.classList.toggle("busy", j.fraction == null);
        bar.firstElementChild.style.width = j.fraction == null ? "" : `${Math.round(j.fraction * 100)}%`;
      }
      if (j) msg.push(`${{ llm: "Language model", whisper: "Whisper", voice: "Voice" }[kind]}: ${j.message}`);
      const key = j ? `${kind}:${j.state}:${j.message}` : "";
      if (j && j.state !== "running" && !this.seenJobs.has(key)) { this.seenJobs.add(key); if (S.models) setTimeout(() => this.refreshModels(), 300); }
    }
    const el = $("#job-msg");
    if (el) el.textContent = msg.join(" · ");
  },
  async sessions() {
    try {
      const r = await api("dev/sessions");
      $("#s-sessions").innerHTML = r.sessions.length ? `<table><tr><th>Recorded flight</th><th>Size</th><th></th></tr>${r.sessions.map((s) =>
        `<tr><td class="mono">${esc(s.name)}</td><td>${s.size_mb} MB</td><td><button class="btn small" data-export="${esc(s.path)}">Export</button></td></tr>`).join("")}</table>`
        : '<p class="muted small">No recorded flights yet.</p>';
      $$("[data-export]").forEach((b) => (b.onclick = () => exportSession(b.dataset.export)));
    } catch (e) { fail(e); }
  },
};

/* ---------- Live Map ---------- */

const PLANE = (color) => `<svg viewBox="0 0 32 32" width="30" height="30"><path fill="${color}" stroke="#000" stroke-width="1" d="M16 2c1.2 0 2 1.4 2 3v7l11 6v3l-11-3v6l3 2v2.5l-5-1.5-5 1.5V26l3-2v-6L3 21v-3l11-6V5c0-1.6.8-3 2-3z"/></svg>`;

const MapView = {
  map: null, ownMarker: null, trail: null, trailPts: [], tfc: new Map(), route: null, routeFixes: null, follow: true, tileLayer: null, airports: new Set(),
  show() {
    if (!this.map) this.init();
    setTimeout(() => this.map.invalidateSize(), 0);
  },
  init() {
    this.map = L.map("map", { zoomControl: true, attributionControl: true, worldCopyJump: true }).setView([47.9, -122.28], 9);
    this.tiles();
    this.trail = L.polyline([], { color: "#5fd068", weight: 2, opacity: 0.7 }).addTo(this.map);
    this.map.on("dragstart", () => this.setFollow(false));
    $("#map-follow").onclick = () => this.setFollow(!this.follow);
    $("#map-route").onclick = () => { this.setFollow(false); if (this.route) this.map.fitBounds(this.route.getBounds(), { padding: [30, 30] }); else toast("No flight plan route"); };
    if (S.own) this.own(S.own);
    this.traffic(S.traffic);
    this.plan(S.state.plan);
  },
  tiles() {
    if (!this.map) return;
    if (this.tileLayer) { this.map.removeLayer(this.tileLayer); this.tileLayer = null; }
    $("#map").classList.toggle("tiles-dark", !!S.state.map_tiles);
    if (S.state.map_tiles !== false) {
      this.tileLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    }
  },
  setFollow(on) {
    this.follow = on;
    $("#map-follow").setAttribute("aria-pressed", String(on));
    if (on && S.own) this.map.panTo([S.own.lat, S.own.lon]);
  },
  own(o) {
    if (!this.map) return;
    const icon = L.divIcon({ className: "own-icon", html: `<div style="transform:rotate(${o.hdg}deg)">${PLANE("#5fd068")}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
    if (!this.ownMarker) { this.ownMarker = L.marker([o.lat, o.lon], { icon, zIndexOffset: 1000 }).addTo(this.map); this.map.setView([o.lat, o.lon], 11); }
    else { this.ownMarker.setLatLng([o.lat, o.lon]); this.ownMarker.setIcon(icon); }
    const last = this.trailPts[this.trailPts.length - 1];
    if (!last || Math.abs(last[0] - o.lat) + Math.abs(last[1] - o.lon) > 0.0015) {
      this.trailPts.push([o.lat, o.lon]);
      if (this.trailPts.length > 3000) this.trailPts.shift();
      this.trail.setLatLngs(this.trailPts);
    }
    if (this.follow) this.map.panTo([o.lat, o.lon], { animate: false });
    $("#map-info").textContent = `${o.alt.toLocaleString()} ft  ${o.gs} kt  HDG ${String(o.hdg_mag).padStart(3, "0")}  VS ${o.vs > 0 ? "+" : ""}${o.vs}`;
  },
  traffic(list) {
    if (!this.map) return;
    const seen = new Set();
    for (const t of list || []) {
      seen.add(t.id);
      const color = t.ground ? "#8d959e" : "#62c7e6";
      const alt = t.ground ? "GND" : `${Math.round(t.alt / 100).toString().padStart(3, "0")}`;
      const icon = L.divIcon({ className: "", html: `<div style="transform:rotate(${t.hdg}deg);width:20px;height:20px">${PLANE(color).replace('width="30" height="30"', 'width="20" height="20"')}</div>
        <div class="tfc-label" style="margin-left:18px;margin-top:-18px">${esc(t.callsign || "")}<br>${alt} ${esc(t.type)}</div>`, iconSize: [20, 20], iconAnchor: [10, 10] });
      const m = this.tfc.get(t.id);
      if (m) { m.setLatLng([t.lat, t.lon]); m.setIcon(icon); } else this.tfc.set(t.id, L.marker([t.lat, t.lon], { icon, interactive: false }).addTo(this.map));
    }
    for (const [id, m] of this.tfc) if (!seen.has(id)) { this.map.removeLayer(m); this.tfc.delete(id); }
  },
  plan(p) {
    if (!this.map) return;
    const key = p ? `${p.origin}-${p.destination}-${p.simbrief_id}-${p.fixes?.length}` : "";
    if (key === this.planKey) return;
    this.planKey = key;
    if (this.route) { this.map.removeLayer(this.route); this.route = null; }
    if (this.routeFixes) { this.map.removeLayer(this.routeFixes); this.routeFixes = null; }
    if (!p) return;
    const pts = (p.fixes || []).map((f) => [f.lat, f.lon]);
    this.routeFixes = L.layerGroup().addTo(this.map);
    for (const f of p.fixes || []) {
      if (f.kind === "apt") continue;
      L.circleMarker([f.lat, f.lon], { radius: 3, color: "#e9d38a", weight: 1, fillOpacity: 0.8 }).addTo(this.routeFixes);
      L.marker([f.lat, f.lon], { icon: L.divIcon({ className: "", html: `<div class="fix-label" style="margin:6px 0 0 6px">${esc(f.ident)}</div>`, iconSize: [0, 0] }), interactive: false }).addTo(this.routeFixes);
    }
    if (pts.length > 1) this.route = L.polyline(pts, { color: "#e978d6", weight: 2, opacity: 0.85 }).addTo(this.map);
    Promise.all([p.origin, p.destination].map((icao) => (icao ? this.airport(icao) : null))).then(([from, to]) => {
      if (!this.route && from && to && this.planKey === key)  // a typed plan: no fixes, a straight line
        this.route = L.polyline([[from.lat, from.lon], [to.lat, to.lon]], { color: "#e978d6", weight: 2, dashArray: "6 6" }).addTo(this.map);
    });
  },
  async airport(icao) {
    try {
      const a = await api(`airport?icao=${encodeURIComponent(icao)}`);
      this.drawAirport(a);
      return a;
    } catch { return null; /* not known yet: drawn when the flight fetches it */ }
  },
  drawAirport(a) {
    if (!this.map || this.airports.has(a.icao)) return;
    this.airports.add(a.icao);
    for (const r of a.runways) {
      const half = r.length_m / 2, h = (r.heading_true * Math.PI) / 180;
      const dLat = (half * Math.cos(h)) / 111320, dLon = (half * Math.sin(h)) / (111320 * Math.cos((r.lat * Math.PI) / 180));
      L.polyline([[r.lat - dLat, r.lon - dLon], [r.lat + dLat, r.lon + dLon]], { color: "#d9dde2", weight: 4, opacity: 0.9 }).addTo(this.map).bindTooltip(`${a.icao} ${r.name}`);
    }
    L.marker([a.lat, a.lon], { icon: L.divIcon({ className: "", html: `<div class="tfc-label" style="color:#e9d38a;font-weight:700">${esc(a.icao)}</div>`, iconSize: [0, 0] }) }).addTo(this.map);
  },
  focus(a) {
    showTab("map");
    this.drawAirport(a);
    this.setFollow(false);
    this.map.setView([a.lat, a.lon], 13);
  },
};

/* ---------- Airport Lookup ---------- */

const Lookup = {
  shown: false,
  show() {
    if (!this.shown) { this.shown = true; this.search(""); }
    $("#lookup-q").focus();
  },
  async search(q) {
    try {
      const r = await api(`airports/search?q=${encodeURIComponent(q)}`);
      $("#lookup-hint").textContent = `${r.known} airport${r.known === 1 ? "" : "s"} known on this computer (every airport a flight visits is saved). Any other ICAO is fetched from the sim during a flight.`;
      $("#lookup-results").innerHTML = r.airports.map((a) => `<span class="chip" data-icao="${esc(a.icao)}"><b>${esc(a.icao)}</b>${esc(a.name)}</span>`).join("");
    } catch (e) { fail(e); }
  },
  async open(icao) {
    const box = $("#lookup-detail");
    box.innerHTML = `<p class="muted">Looking up ${esc(icao)} ...</p>`;
    try {
      const a = await api(`airport?icao=${encodeURIComponent(icao)}`);
      this.current = a;
      const running = S.state.status === "running";
      box.innerHTML = `<div class="detail">
        <h2>${esc(a.icao)} &nbsp;${esc(a.name)}</h2>
        <div class="meta">Elevation ${a.elev_ft.toLocaleString()} ft &middot; ${a.lat.toFixed(4)}, ${a.lon.toFixed(4)} &middot; magnetic variation ${Math.abs(a.magvar).toFixed(1)}&deg;${a.magvar >= 0 ? "E" : "W"}
          &nbsp;<button class="btn small" id="lk-map">Show on map</button> <button class="btn small" id="lk-dest">Fly here</button></div>
        <h3>Frequencies</h3>
        <table><tr><th></th><th>MHz</th><th>Name</th></tr>${a.all_frequencies.map((f) => `<tr class="${running ? "tunable" : ""}" data-mhz="${f.mhz}" title="${running ? "Tune COM1" : ""}">
          <td><b>${esc(f.label)}</b></td><td class="mono">${mhz(f.mhz)}</td><td class="muted">${esc(f.name)}</td></tr>`).join("") || '<tr><td colspan="3" class="muted">None listed</td></tr>'}</table>
        <h3>Runways</h3>
        <table><tr><th>Runway</th><th>Length</th><th>Width</th><th>Heading</th><th>ILS</th></tr>${a.runways.map((r) => `<tr>
          <td><b>${esc(r.name)}</b></td><td>${r.length_ft.toLocaleString()} ft</td><td>${r.width_ft} ft</td><td>${String(r.heading_mag).padStart(3, "0")}&deg;</td><td>${esc(r.ils.join(", ") || "—")}</td></tr>`).join("")}</table>
        ${a.taxiways.length ? `<h3>Taxiways</h3><div class="muted small">${esc(a.taxiways.join(", "))}</div>` : ""}
        <div class="muted small" style="margin-top:8px">${a.parking} parking spots</div></div>`;
      $("#lk-map").onclick = () => MapView.focus(a);
      $("#lk-dest").onclick = () => { Flight.open(); Flight.sub("manual"); $("#mp-destination").value = a.icao; };
      $$("#lookup-detail tr.tunable").forEach((tr) => (tr.onclick = () => tune(Number(tr.dataset.mhz))));
    } catch (e) { box.innerHTML = `<div class="error" style="margin:0">${esc(e.message)}</div>`; }
  },
};
let searchTimer;
$("#lookup-q").addEventListener("input", (e) => { clearTimeout(searchTimer); searchTimer = setTimeout(() => Lookup.search(e.target.value), 200); });
$("#lookup-q").addEventListener("keydown", (e) => { if (e.key === "Enter") Lookup.open(e.target.value.trim().toUpperCase()); });
$("#lookup-go").onclick = () => Lookup.open($("#lookup-q").value.trim().toUpperCase());
$("#lookup-results").addEventListener("click", (e) => { const c = e.target.closest(".chip"); if (c) Lookup.open(c.dataset.icao); });

/* ---------- start ---------- */

api("state").then(setState).catch(fail);
api("settings").then((s) => (S.settings = s)).catch(() => {});
connect();
