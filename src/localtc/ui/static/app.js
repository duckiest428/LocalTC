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
function toast(text, error = false, ms = null) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast" + (error ? " error" : "");
  el.hidden = false;
  // In the top layer, shown after (so over) any dialog open: an error in the setup was hidden behind it.
  try { el.hidePopover?.(); el.showPopover?.(); } catch { /* no popovers here: a plain toast */ }
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; try { el.hidePopover?.(); } catch { /* already gone */ } }, ms ?? (error ? 6000 : 3000));
}
const fail = (e) => toast(e.message || String(e), true);

/* ---------- live events ---------- */

function connect() {
  const es = new EventSource("/api/events");
  const on = (kind, fn) => es.addEventListener(kind, (e) => fn(JSON.parse(e.data)));
  on("state", setState);
  on("traffic_control", (s) => { if (S.state) S.state.traffic_control = s; const el = $("#traffic-status"); if (el) el.innerHTML = trafficStatus(s); });
  on("radio_history", (lines) => { clearLog(); lines.forEach(addLine); });
  on("trail", (points) => MapView.setTrail(points));
  on("radio", addLine);
  on("own", setOwn);
  on("traffic", (t) => { S.traffic = t; MapView.traffic(t); });
  on("flight", (f) => { setFlight(f); MapView.flightChanged(f); });
  on("cleared", clearFlight);
  on("ptt", (p) => $(p.intercom ? "#btn-ic" : "#btn-ptt").classList.toggle("down", p.down));
  on("thinking", thinking);
  on("efb", (d) => Efb.update(d));  // efb.js: the checklist, Airport Ops, ATC's hold and conditions, the notes
  on("jobs", (jobs) => { S.state.jobs = jobs; Settings.jobs(jobs); Setup.jobs(jobs); });
  on("update", (u) => { S.state.update = u; Settings.update(u); });
  on("account", (a) => { S.account = a; Settings.account(); });
  on("dev", (d) => Dev.event(d));
  on("airport", ({ icao }) => {  // the flight fetched an airport's layout: draw it if it's on the route
    const wanted = [S.state.plan?.origin, S.state.plan?.destination, S.flight.origin, S.flight.destination];
    if (wanted.includes(icao)) MapView.airport(icao);
  });
  es.onerror = () => setStatus({ status: "error", detail: "LocalTC isn't answering" });
  es.onopen = () => setStatus(S.state);
}

const THEMES = [["radio", "Radio panel (dark)"], ["midnight", "Midnight blue"], ["oled", "Black (OLED)"],
  ["amber", "Amber avionics"], ["slate", "Slate"], ["daylight", "Daylight (light)"]];
function applyTheme(theme) {
  const t = THEMES.some(([v]) => v === theme) ? theme : "radio";
  if (t === "radio") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch { /* storage unavailable: the setting still applies */ }
}

function setState(st) {
  applyTheme(st.theme);
  S.state = st;
  setStatus(st);
  if (st.flight && Object.keys(st.flight).length) setFlight(st.flight);
  $("#sw-copilot").classList.toggle("on", st.copilot && st.copilot !== "off");
  const radioMode = st.copilot_mode === "assist" ? "reads back and changes frequencies" : "works the whole radio";
  $("#sw-copilot").title = st.copilot && st.copilot !== "off" ? `Copilot on: ${radioMode}` : `Copilot off: ${radioMode}`;
  $("#sw-atc").classList.toggle("on", !st.muted);
  $("#devbar").hidden = !st.dev_mode;
  $("#btn-coffee").hidden = !!st.coffee_clicked;
  if (st.language && st.language !== I18n.lang) I18n.set(st.language);
  Flight.label();
  // The popups that open by themselves, each once a session (saved as seen when it's closed), one at a time and never
  // over a dialog that's open (Popups): a new install's setup holds the welcome and the language.
  if (st.setup) {
    if (!S.setupAsked) { S.setupAsked = true; Popups.want("setup"); }
  } else {
    if (st.language_asked === false && !S.languageAsked) { S.languageAsked = true; Popups.want("language"); }
    if (st.welcome && !S.welcomed) { S.welcomed = true; Popups.want("welcome"); }
    if (st.whats_new && !S.whatsNew) { S.whatsNew = true; Popups.want("whatsnew"); }
    if (st.account_prompt && !S.accountAsked) { S.accountAsked = true; Popups.want("account"); }
  }
  $("#btn-ptt").classList.toggle("off", !st.voice);
  $("#help-ptt").textContent = pttName(st.ptt);
  $("#btn-ptt").title = st.voice ? `Hold to talk (or ${pttName(st.ptt)})` : "Voice input is off (Quick Settings > Push-to-talk)";
  $("#btn-ic").classList.toggle("off", !st.voice);
  $("#btn-ic").title = st.voice ? `Hold to talk to the copilot${st.ptt && st.ptt.intercom ? ` (or ${keyLabel(st.ptt.intercom)})` : ""}` : "Voice input is off (Quick Settings > Push-to-talk)";
  if (!S.flight.callsign) planHeader(st.plan);
  MapView.plan(st.plan);
  MapView.syncRules();
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

// The controller working out an answer (the language model has the call): shown until the answer is on the air.
function thinking(t) {
  $("#log .line.thinking")?.remove();
  if (!t.busy) return;
  const log = $("#log");
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const row = document.createElement("div");
  row.className = "line atc thinking";
  row.innerHTML = `<span class="t"></span><div><span class="who">ATC</span><span class="st">${esc(t.station)} ${mhz(t.mhz)}</span>`
    + `<div class="body"><span class="dots" aria-label="thinking"><i></i><i></i><i></i></span></div></div>`;
  log.appendChild(row);
  $("#log-empty").hidden = true;
  if (stick) log.scrollTop = log.scrollHeight;
}

function addLine(l) {
  const log = $("#log");
  if (l.kind === "atc") $("#log .line.thinking")?.remove();
  const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const row = document.createElement("div");
  row.className = `line ${l.kind}` + (l.level ? ` ${l.level}` : "") + (l.kind === "readback" && !l.ok ? " bad" : "");
  row.setAttribute("translate", "no");  // the radio's words: aviation English in every language
  let body;
  switch (l.kind) {
    case "atc":
      body = `<span class="who">ATC</span><span class="st">${esc(l.station)} ${mhz(l.mhz)}</span><div class="body">${esc(l.text)}</div>`; break;
    case "pilot":
      body = `<span class="who">YOU</span><span class="body">${esc(l.text)}</span>${l.unclear ? '<span class="unclear">(unclear)</span>' : ""}`; break;
    case "copilot":
      body = `<span class="who">COPILOT</span><span class="body">${esc(l.text)}</span>`; break;
    case "intercom":  // you, to the copilot
      body = `<span class="who">YOU</span><span class="ic">intercom</span><span class="body">${esc(l.text)}</span>${l.unclear ? '<span class="unclear">(unclear)</span>' : ""}`; break;
    case "crew":  // the copilot, to you
      body = `<span class="who">COPILOT</span><span class="ic">intercom</span><span class="body">${esc(l.text)}</span>`; break;
    case "chatter":  // somebody else on the frequency
      if ((l.live || S.settings?.traffic?.enabled) && l.callsign) {
        // LocalTC's traffic (real flights): shown like a real transmission, by its callsign in its airline's colour.
        row.classList.add("live");
        const cs = `<span class="cs" style="color:${airlineColour(l.ident || l.callsign)}">${esc(l.callsign)}</span>`;
        body = l.atc ? `<span class="who">ATC</span><span class="st">${esc(l.station)} ${mhz(l.mhz)}</span> <span class="st">to</span> ${cs}<div class="body">${esc(l.text)}</div>`
          : `<span class="who">${cs}</span><span class="st">${mhz(l.mhz)}</span><div class="body">${esc(l.text)}</div>`;
        break;
      }
      body = `<span class="who">${l.atc ? "ATC" : "OTHER"}</span><span class="st">${esc(l.station)}</span><div class="body">${esc(l.text)}</div>`; break;
    case "atis":
      body = `<span class="who">ATIS</span><span class="st">${esc(l.station)}</span><div class="body">${esc(l.text)}</div>`; break;
    case "tuned":
      body = `<span class="body">COM${l.radio || 1} ${mhz(l.mhz)} &nbsp;${esc(l.text)}</span>`; break;
    case "note":
      body = `<span class="body">&#9873; ${esc(l.text)}</span>`; break;
    default:
      body = `<span class="body">${l.kind === "phase" ? "&mdash; " : ""}${esc(l.text)}</span>`;
  }
  // A play button where the transmission's audio was kept (Quick Settings → ATC voice → Play buttons).
  const play = l.audio ? `<button class="play" data-audio="${esc(l.audio)}" title="Play it again" aria-label="Play">&#9654;</button>` : "";
  row.innerHTML = `<span class="t">${clock(l.t)}</span><div>${play}${body}</div>`;
  log.appendChild(row);
  $("#log-empty").hidden = true;
  if (++S.radioLines > 500) log.querySelector(".line")?.remove();
  if (stick) log.scrollTop = log.scrollHeight;
  if (l.kind === "alert") { S.alerts.push(l); updateAlerts(); }
  // The model running out of time changes what ATC said: always shown, with where to give it longer.
  if (l.kind === "alert" && l.code === "llm_rejected") toast(l.text, false, 8000);
  if (l.kind === "alert" && l.code === "llm_timeout") toast(`${l.text}. Give it longer in Quick Settings → Language models → ATC.`, true, 12000);
}

// Each airline in its own colour (its livery's, where known), each of its flights a shade of it.
const AIRLINE_HUES = {
  RYR: 215, BAW: 355, EZY: 28, DLH: 45, AFR: 225, KLM: 200, UAE: 0, QTR: 330, AAL: 210, DAL: 350, UAL: 230,
  SWA: 40, JBU: 220, ASA: 190, ACA: 0, WJA: 175, IBE: 5, VLG: 50, SAS: 220, FIN: 210, THY: 0, AUA: 0, SWR: 0,
  EIN: 140, WZZ: 300, NKS: 55, FFT: 130, VIR: 340, QFA: 0, ANA: 210, JAL: 0, CPA: 170, SIA: 40, ETD: 35,
};
function airlineColour(ident) {
  const id = String(ident || "").toUpperCase().replace(/\s+/g, "");
  const m = id.match(/^([A-Z]{3})(\d.*)$/);
  const prefix = m ? m[1] : id.slice(0, 3);
  let h = 0;
  for (const c of prefix) h = (h * 31 + c.charCodeAt(0)) % 360;
  const hue = AIRLINE_HUES[prefix] ?? h;
  let n = 0;
  for (const c of (m ? m[2] : id)) n = (n * 17 + c.charCodeAt(0)) % 997;
  const light = 50 + (n % 5) * 6;  // 50-74%: a shade per flight
  return `hsl(${hue} ${m ? 75 : 25}% ${light}%)`;
}

let playing = null;
$("#log").addEventListener("click", async (e) => {
  const btn = e.target.closest(".play");
  if (!btn) return;
  if (playing) { playing.audio.pause(); playing.btn.classList.remove("on"); }
  if (playing && playing.btn === btn) { playing = null; return; }
  btn.classList.add("on");
  try {
    const res = await fetch(`/api/clip?id=${encodeURIComponent(btn.dataset.audio)}`);
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || "Not kept");
    const audio = new Audio(URL.createObjectURL(await res.blob()));
    playing = { audio, btn };
    audio.onended = () => { btn.classList.remove("on"); if (playing && playing.audio === audio) playing = null; };
    await audio.play();
  } catch (err) {
    btn.classList.remove("on");
    btn.disabled = true;
    btn.title = err.message;
    playing = null;
  }
});

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

/* The last flight gone from the screen (10 minutes after it ended): the aircraft, its path, the traffic, the radio
   log and the flight's details. The state with the plan (or none) follows. */
function clearFlight() {
  S.own = null;
  S.traffic = [];
  clearLog();
  $("#com1").textContent = "---.---";
  $("#com2").textContent = "OFF";
  $("#squawk").textContent = "----";
  $("#xmode").textContent = "";
  setFlight({});
  MapView.clear();
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
  // (Nothing required, as clearance's "readback correct, contact ground when ready": not a readback, a call to make)
  if (f.pending && (f.pending.required || []).length) next = `Read back: ${Object.values(f.pending.expected).join(", ")}`;
  else if (f.expected) next = `Contact ${f.expected.station} ${mhz(f.expected.mhz)}`;
  else if (f.tuned) next = `On ${f.tuned.station}`;
  $("#fl-next").textContent = next;
  enRoute(f.status);
  const ap = f.airport;
  $("#ap-icao").textContent = ap?.icao || "----";
  $("#ap-name").textContent = ap?.name || (ap ? "" : "No airport yet");
  // The ATIS, where it's from and how old ([atc] atis_source): "ATIS W · real ATIS 1756Z, 12 min old".
  const atisLine = $("#ap-atis");
  atisLine.hidden = !f.atis;
  atisLine.textContent = f.atis ? `ATIS ${f.atis}${f.atis_source ? " · " + f.atis_source : ""}` : "";
  const freqs = $("#ap-freqs");
  if (ap?.frequencies?.length) {
    const key = JSON.stringify(ap.frequencies) + ap.icao;
    if (freqs.dataset.key !== key) {
      freqs.dataset.key = key;
      freqs.innerHTML = ap.frequencies.map((q) => `<div class="fq" data-mhz="${q.mhz}" title="Tune COM1 to ${mhz(q.mhz)}${q.others.length ? " (also " + q.others.map(mhz).join(", ") + ")" : ""}">
        <span class="fl">${esc(q.label)}</span><span class="fv">${mhz(q.mhz)}</span></div>`).join("");
    }
  } else if (!ap && freqs.dataset.key) {
    freqs.innerHTML = '<div class="muted small">Frequencies appear when the flight starts.</div>';
    freqs.dataset.key = "";
  }
  markTuned();
}

/* En route (``flight.status``): the flight's status in place of the airport box, the airline's logo (else its
   colour), the way along the route, and how it's going. */
function enRoute(s) {
  $("#airport-box").hidden = !!s;
  $("#status-box").hidden = !s;
  if (!s) return;
  const logo = $("#er-logo"), badge = $("#er-badge");
  if (logo.dataset.src !== s.logo) {
    logo.dataset.src = s.logo || "";
    logo.hidden = true;
    if (s.logo) { logo.onload = () => { logo.hidden = false; badge.textContent = ""; }; logo.onerror = () => { logo.hidden = true; }; logo.src = s.logo; }
  }
  if (logo.hidden) {
    badge.textContent = s.iata || s.airline_icao || "";
    badge.style.background = airlineColour(s.callsign);
  }
  $("#er-flight").textContent = s.flight || s.callsign || "—";
  $("#er-airline").textContent = [s.airline, s.callsign && s.callsign !== s.flight ? s.callsign : ""].filter(Boolean).join(" · ");
  $("#er-aircraft").textContent = s.aircraft || s.type || "—";
  $("#er-with").textContent = s.with ? `With ${s.with}` : s.phase || "";
  $("#er-orig").textContent = s.origin || "----";
  $("#er-orig-name").textContent = s.origin_name || "";
  $("#er-dest").textContent = s.destination || "----";
  $("#er-dest-name").textContent = s.destination_name || "";
  const pct = Math.round((s.progress || 0) * 1000) / 10;
  $("#er-fill").style.width = `${pct}%`;
  $("#er-plane").style.left = `${pct}%`;
  const hm = (m) => (m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min` : `${m} min`);
  const facts = [
    ["To go", `${Number(s.to_go_nm).toLocaleString()} nm`], ["Flown", `${Number(s.flown_nm).toLocaleString()} nm`],
    ["Time left", hm(s.ete_min)], ["ETA", s.eta || "—"], ["Alt", s.altitude], ["GS", `${s.gs_kt} kt`],
    ["IAS", `${s.ias_kt} kt`], ["V/S", `${s.vs_fpm > 0 ? "+" : ""}${s.vs_fpm} fpm`], ["Hdg", String(s.heading).padStart(3, "0")],
    ["Wind", s.wind || "—"], ["OAT", s.temp_c != null ? `${s.temp_c} °C` : "—"],
    ["Fuel", s.fuel_lb ? `${Number(s.fuel_lb).toLocaleString()} lb` : "—"],
  ];
  $("#er-facts").innerHTML = facts.map(([k, v]) => `<div class="kv"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join("");
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
$("#btn-crew").onclick = async () => {
  const input = $("#tx-text"), text = input.value.trim();
  if (!text) return;
  try { await api("radio/crew", { text }); input.value = ""; } catch (e) { fail(e); }
};
$("#btn-clear").onclick = () => { $("#tx-text").value = ""; $("#tx-text").focus(); };

const held = { radio: false, intercom: false };
async function ptt(down, intercom = false) {
  const which = intercom ? "intercom" : "radio", btn = $(intercom ? "#btn-ic" : "#btn-ptt");
  if (down === held[which]) return;
  held[which] = down;
  btn.classList.toggle("down", down);
  try { await api("radio/ptt", { down, intercom }); } catch (e) { held[which] = false; btn.classList.remove("down"); if (down) fail(e); }
}
for (const [id, intercom] of [["#btn-ptt", false], ["#btn-ic", true]]) {
  const btn = $(id);
  btn.addEventListener("pointerdown", (e) => { e.preventDefault(); btn.setPointerCapture(e.pointerId); ptt(true, intercom); });
  btn.addEventListener("pointerup", () => ptt(false, intercom));
  btn.addEventListener("pointercancel", () => ptt(false, intercom));
}

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
  if (name === "efb") Efb.show();
  if (name === "lookup") Lookup.show();
  if (name === "logbook") Logbook.load().then(() => (Logbook.player ? Logbook.player.fit() : Logbook.map?.fit()));
  else Logbook.player?.pause();
}
$$(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
// The gear: Settings, the things that aren't about flying (the account, appearance, updates, support, the sim
// connection, developer mode). Quick Settings (the tab) keeps what changes how a flight goes.
$("#btn-settings").onclick = () => {
  const dlg = $("#dlg-settings");
  if (!dlg.open) dlg.showModal();
  Settings.load();  // fresh each time it opens (the account, the update state)
};
$("#dlg-settings-close").onclick = () => $("#dlg-settings").close();
$("#dlg-settings").addEventListener("click", (e) => { if (e.target === $("#dlg-settings")) $("#dlg-settings").close(); });
$("#btn-alerts").onclick = () => $("#dlg-alerts").showModal();
$("#btn-help").onclick = () => $("#dlg-help").showModal();
// The welcome notes: shown once ever (existing installs included), seen for good once closed. This version's notes
// count as seen with it: a new install has nothing to catch up on.
$("#dlg-welcome")?.addEventListener("close", () => {
  if (S.state) { S.state.welcome = false; S.state.whats_new = null; }
  Popups.drop("whatsnew");  // (this version's notes count as seen with it)
  api("settings", { settings: { ui: { welcome_seen: true, notes_seen: S.state?.version || "" } } }).catch(() => {});
});
// What's new: once after each update, this version's CHANGELOG section.
$("#dlg-whatsnew")?.addEventListener("close", () => {
  const version = S.state?.whats_new?.version;
  if (S.state) S.state.whats_new = null;
  if (version) api("settings", { settings: { ui: { notes_seen: version } } }).catch(() => {});
});
/** The CHANGELOG's Markdown (### headings, "- " items, **bold**, `code`) as HTML. */
function notesHtml(md) {
  const inline = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = [];
  let item = null, list = false;
  const flush = () => { if (item !== null) out.push(`<li>${inline(item)}</li>`); item = null; };
  const close = () => { flush(); if (list) out.push("</ul>"); list = false; };
  for (const line of String(md || "").split("\n")) {
    if (line.startsWith("### ")) { close(); out.push(`<h3>${inline(line.slice(4))}</h3>`); }
    else if (line.startsWith("- ")) { flush(); if (!list) { out.push("<ul>"); list = true; } item = line.slice(2).trim(); }
    else if (line.startsWith("  ") && item !== null) item += " " + line.trim();
    else if (line.trim()) { close(); out.push(`<p>${inline(line.trim())}</p>`); }
    else flush();
  }
  close();
  return out.join("");
}
// The account suggestion: answered either way, it's never shown again; yes opens the account card in Settings.
$("#dlg-account").addEventListener("close", () => {
  const yes = $("#dlg-account").returnValue === "yes";
  if (S.state) S.state.account_prompt = false;
  api("account/prompt", {}).catch(() => {});
  if (!yes) return;
  $("#btn-settings").click();
  const find = (tries = 20) => {
    const card = $("#s-account-card");
    if (card) { card.scrollIntoView({ block: "start", behavior: "smooth" }); $("#a-email")?.focus(); }
    else if (tries) setTimeout(() => find(tries - 1), 150);
  };
  find();
});

// Buy me a coffee: shown until it's been clicked once, then never again.
$("#btn-coffee").addEventListener("click", () => {
  $("#btn-coffee").hidden = true;
  S.state.coffee_clicked = true;
  api("settings", { settings: { ui: { coffee_clicked: true } } }).catch(() => {});
});

// Links open in the real browser, not inside the app window.
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='http']");
  if (!a) return;
  e.preventDefault();
  api("open", { what: "url", url: a.href }).catch(fail);
});

/* ---------- the Mixer: everything you hear at its own volume, and the microphone ---------- */

const SPEAKER_ON = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9zm13.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/></svg>';
const SPEAKER_OFF = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9zm18.6 3-2.3-2.3-1.4 1.4 2.3 2.3-2.3 2.3 1.4 1.4 2.3-2.3 2.3 2.3 1.4-1.4-2.3-2.3 2.3-2.3-1.4-1.4z" transform="translate(-2 0)"/></svg>';

const Mixer = {
  // [section, key, label, hint, max]: each row's setting. The microphone's gain is its own (no mute).
  GROUPS: [
    ["You", [
      ["mixer", "mic_gain", "Microphone gain", "How loud your microphone reaches speech-to-text. Test it: you'll hear yourself as ATC does.", 3],
      ["mixer", "sidetone", "Hear yourself on the radio", "Your voice in your headset while you hold the key, as in a real aircraft. Headsets only: with speakers the microphone hears it.", 1],
    ]],
    ["Master", [["tts", "volume", "Everything", "", 1]]],
    ["Radio", [
      ["mixer", "atc", "ATC", "The controllers talking to you.", 1.5],
      ["mixer", "atis", "ATIS", "The recording while its frequency is tuned.", 1.5],
      ["mixer", "chatter", "Other flights", "Other aircraft on the frequency and ATC's calls to them.", 1.5],
    ]],
    ["Copilot", [
      ["mixer", "copilot", "On the intercom", "The copilot beside you.", 1.5],
      ["mixer", "copilot_radio", "On the radio", "Its readbacks and calls to ATC.", 1.5],
    ]],
    ["Radio sounds", [["mixer", "effects", "Clicks and squelch", "Your push-to-talk's click and the receiver's rush when you let go.", 1.5]]],
  ],
  async open() {
    try { S.settings = await api("settings"); } catch (e) { fail(e); return; }
    this.render();
    const dlg = $("#dlg-mixer");
    if (!dlg.open) dlg.showModal();
  },
  value(section, key) { return Number(S.settings.settings[section][key]); },
  muted(key) { return (S.settings.settings.mixer.muted || []).includes(key); },
  render() {
    const st = S.settings.settings;
    const pct = (v) => `${Math.round(v * 100)}%`;
    const row = ([section, key, label, hint, max]) => {
      const v = this.value(section, key), mutable = key !== "mic_gain" && section === "mixer", off = mutable && this.muted(key);
      return `<div class="mixrow ${off ? "muted-row" : ""}" data-row="${key}">
        <div class="mixlabel"><b>${esc(label)}</b>${hint ? `<span class="muted small">${esc(hint)}</span>` : ""}</div>
        <input type="range" min="0" max="${max}" step="0.05" value="${v}" data-mix="${section}.${key}" aria-label="${esc(label)}">
        <span class="mixval" id="mixval-${key}">${pct(v)}</span>
        ${mutable ? `<button type="button" class="iconbtn mixmute" data-mute="${key}" title="${off ? "Unmute" : "Mute"}" aria-pressed="${off}">${off ? SPEAKER_OFF : SPEAKER_ON}</button>` : '<span class="mixmute"></span>'}
      </div>`;
    };
    $("#mixer-body").innerHTML = this.GROUPS.map(([title, rows]) => `<div class="mixgroup"><div class="mixhead">${esc(title)}</div>
      ${rows.map(row).join("")}
      ${title === "You" ? `<div class="mixtest"><button type="button" class="btn small" id="mix-test">&#127908; Test my microphone</button>
        <span class="muted small" id="mix-test-note">Speaks for 4 seconds, then plays you back through the radio.</span></div>` : ""}
      </div>`).join("") +
      `<div class="mixgroup"><div class="mixhead">The radio's sound</div>
        <div class="mixrow"><div class="mixlabel"><b>Static</b><span class="muted small">The receiver's hiss and squelch under the voices (from the next line).</span></div>
          <input type="range" min="0" max="1" step="0.05" value="${st.tts.static}" data-static aria-label="Static"><span class="mixval" id="mixval-static">${pct(st.tts.static)}</span><span class="mixmute"></span></div>
        <label class="check-row"><input type="checkbox" id="mix-effect" ${st.tts.radio_effect ? "checked" : ""}> Radio effect: the voices as over a real aircraft radio (off: clean)</label>
      </div>`;
    let timer = null;
    $$("[data-mix]").forEach((input) => {
      const [section, key] = input.dataset.mix.split(".");
      const send = (save) => {
        const value = Number(input.value);
        S.settings.settings[section][key] = value;
        $(`#mixval-${key}`).textContent = pct(value);
        return api("mixer", { settings: { [section]: { [key]: value } }, save }).catch(fail);
      };
      input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => send(false), 60); });  // heard while dragged
      input.addEventListener("change", () => { clearTimeout(timer); send(true); });  // kept once let go
    });
    $$("[data-mute]").forEach((b) => (b.onclick = async () => {
      const key = b.dataset.mute, muted = new Set(st.mixer.muted || []);
      if (muted.has(key)) muted.delete(key); else muted.add(key);
      st.mixer.muted = [...muted];
      await api("mixer", { settings: { mixer: { muted: st.mixer.muted } }, save: true }).catch(fail);
      this.render();
    }));
    const stat = $("[data-static]");
    stat.addEventListener("input", () => ($("#mixval-static").textContent = pct(Number(stat.value))));
    stat.addEventListener("change", () => Settings.save("tts", "static", Number(stat.value)));
    $("#mix-effect").onchange = (e) => Settings.save("tts", "radio_effect", e.target.checked);
    $("#mix-test").onclick = () => this.test();
  },
  async test() {
    const b = $("#mix-test"), note = $("#mix-test-note");
    b.disabled = true;
    note.textContent = "Listening ... say a call, as to ATC.";
    try {
      const r = await api("voice/mic_test", { seconds: 4 });
      note.innerHTML = {
        silent: `<b class="bad">Nothing heard</b> from ${esc(r.microphone)}. Check it's the microphone you talk into (Quick Settings &gt; Push-to-talk) and that it isn't muted.`,
        quiet: `<b class="warn">Quiet</b> (${esc(r.microphone)}): raise the gain, or speak closer to the microphone.`,
        loud: `<b class="warn">Too loud</b>, it clips (${esc(r.microphone)}): lower the gain a little.`,
        good: `<b class="ok">Good level</b> (${esc(r.microphone)}). That's how ATC hears you.`,
      }[r.verdict] || "";
    } catch (e) { note.textContent = e.message; }
    b.disabled = false;
  },
};
$("#btn-mixer").onclick = () => Mixer.open();

/* ---------- Language: the window's, and the one the copilot talks with you in ---------- */

const Language = {
  list() { return S.state?.languages?.length ? S.state.languages : [{ code: "en", native: "English", english: "English" }]; },
  current() { return S.state?.language || "en"; },
  /** The languages as cards, and whether the copilot talks with you in the one chosen (``id``: the ids' prefix). */
  picker(id) {
    const cur = this.current(), crew = S.settings?.settings?.crew?.language || S.state?.crew_language || "";
    return `<div class="wlangs">${this.list().map((l) => `<button type="button" class="wcard ${l.code === cur ? "current" : ""}"
        data-lang="${esc(l.code)}" translate="no"><b>${esc(l.native)}</b><span>${esc(l.english)}</span></button>`).join("")}</div>
      <div ${cur === "en" ? "hidden" : ""}><label class="check-row"><input type="checkbox" id="${id}-crew-lang" ${crew && crew === cur ? "checked" : ""}>
        The copilot talks with me in my language</label>
        <span class="hint">What it says to you and what it hears on the intercom. Its callouts, checklists and radio calls stay
          in aviation English, as ATC does. It needs speech recognition that knows the language and a voice that speaks
          it: downloaded once, by itself (the voices of your language: Quick Settings &rarr; Models).</span></div>
      <p class="muted small">ATC always speaks aviation English, as on the real radio.</p>`;
  },
  wire(id, after) {
    $$("[data-lang]").forEach((b) => (b.onclick = async () => {
      const code = b.dataset.lang;
      if (!await Settings.save("ui", "language", code)) return;
      if (S.state) S.state.language = code;
      I18n.set(code);
      // The copilot in the language picked too, unless it's English (or the pilot turns it off below).
      await Settings.save("crew", "language", code === "en" ? "" : code);
      if (S.state) S.state.crew_language = code === "en" ? "" : code;
      this.fetchVoices(code === "en" ? "" : code);
      after?.();
    }));
    const crew = $(`#${id}-crew-lang`);
    if (crew) crew.onchange = async () => {
      const code = crew.checked ? this.current() : "";
      if (await Settings.save("crew", "language", code) && S.state) S.state.crew_language = code;
      this.fetchVoices(code);
    };
  },
  /** The voice pack of the language the copilot talks in, and the speech recognition that knows it, downloaded
   *  meanwhile (each once: skipped when it's there). */
  fetchVoices(code) {
    if (code && S.settings?.settings?.crew?.enabled !== false) api("models/install", { kinds: ["voice_lang", "whisper"] }).catch(() => {});
  },
  /** Asked once of a pilot who had LocalTC before it came in other languages. */
  ask() {
    const dlg = $("#dlg-language");
    const draw = () => {
      $("#language-body").innerHTML = `<p>LocalTC now comes in your language. Which would you like? You can change it any time
        in Settings (the gear).</p>${this.picker("l")}<div class="row welcome-go"><button class="btn primary" value="ok">Done</button></div>`;
      this.wire("l", draw);
    };
    // Open at once (the next popup due waits for it: opened a moment later, "what's new" came up underneath it).
    draw();
    if (!dlg.open) dlg.showModal();
    if (!S.settings) api("settings").then((s) => { S.settings = s; if (dlg.open) draw(); }).catch(() => {});
  },
};
// However the question's closed, it's been asked: never again.
$("#dlg-language").addEventListener("close", () => {
  if (S.state) S.state.language_asked = true;
  api("settings", { settings: { ui: { language_asked: true } } }).catch(() => {});
});

/* ---------- Popups: the ones that open by themselves, one at a time and never over another ---------- */

const Popups = {
  // The order they come in when more than one is due: the setup (a new install), the language (once), the welcome
  // notes (once), what's new (after an update), the account (after the third flight).
  ORDER: ["setup", "language", "welcome", "whatsnew", "account"],
  wanted: new Set(),
  want(id) { this.wanted.add(id); this.next(); },
  drop(...ids) { ids.forEach((id) => this.wanted.delete(id)); },
  /** The next one due, once nothing else is open (a dialog the pilot opened, or another of these). */
  next() {
    if ($$("dialog[open]").length) return;
    const id = this.ORDER.find((k) => this.wanted.has(k));
    if (!id) return;
    this.wanted.delete(id);
    ({
      setup: () => Setup.open(),
      language: () => Language.ask(),
      welcome: () => $("#dlg-welcome").showModal(),
      whatsnew: () => {
        if (!S.state?.whats_new) return this.next();
        $("#wn-version").textContent = S.state.whats_new.version;
        $("#wn-notes").innerHTML = notesHtml(S.state.whats_new.notes);
        $("#dlg-whatsnew").showModal();
      },
      account: () => (S.state?.account_prompt ? $("#dlg-account").showModal() : this.next()),
    })[id]();
  },
};
// Each dialog closed (any of them, the share card's too): the next one due, if any.
document.addEventListener("close", (e) => { if (e.target instanceof HTMLDialogElement) setTimeout(() => Popups.next(), 0); }, true);

/* ---------- Components: Ollama, Whisper on the GPU and WebView2, installed from the app (the setup leaves them) ---------- */

const COMPONENT_KINDS = ["ollama", "gpu", "webview2"];
const COMPONENT_NAMES = { ollama: "Ollama", gpu: "Graphics card", webview2: "WebView2" };
/** The components worth showing here (`only`: these ids), each with its state and an Install button. */
function componentsPart(m, only = COMPONENT_KINDS) {
  const jobs = S.state?.jobs || {};
  const parts = (m.components || []).filter((c) => only.includes(c.id) && c.supported && (c.needed || c.installed));
  if (!parts.length) return "";
  return `<div class="components">${parts.map((c) => {
    const running = jobs[c.id]?.state === "running";
    const state = c.installed ? '<span class="mstatus ok">&#10003; installed</span>'
      : `<button type="button" class="btn small" data-install="${c.id}" ${running ? "disabled" : ""}>${running ? "Installing ..." : `Install (${esc(c.size)})`}</button>`;
    return `<div class="row comp"><div><b>${esc(c.label)}</b>${c.detail ? `<div class="muted small">${esc(c.detail)}</div>` : ""}</div><div>${state}</div></div>
      <div class="progress" id="p-${c.id}" hidden><div></div></div>`;
  }).join("")}</div>`;
}

/* ---------- Setup: the guide a new install opens with (and Settings > Developer mode shows again) ---------- */

const Setup = {
  STEPS: [
    ["language", "Welcome"], ["model", "Language model"], ["computer", "Your computer"], ["talk", "Talking to ATC"],
    ["hear", "Hearing"], ["copilot", "Your copilot"], ["people", "Who flies with you"], ["atc", "ATC"],
    ["flight", "Your flights"], ["traffic", "Traffic"], ["done", "Ready"],
  ],
  step: 0,
  /** The steps shown: who flies with you only with a copilot. */
  steps() { return this.STEPS.filter(([id]) => id !== "people" || S.settings?.settings?.crew?.enabled !== false); },
  async open() {
    const dlg = $("#dlg-setup");
    $("#setup-body").innerHTML = '<div class="muted pad">Loading ...</div>';
    if (!dlg.open) dlg.showModal();
    try {
      const [s, m, d, c] = await Promise.all([api("settings"), api("models"), api("devices"), api("cloud").catch(() => null)]);
      S.settings = s; S.models = m; S.devices = d; S.cloud = c;
    } catch (e) { fail(e); dlg.close(); return; }
    this.step = 0;
    this.render();
  },
  go(delta) {
    const steps = this.steps();
    this.step = Math.max(0, Math.min(steps.length - 1, this.step + delta));
    this.render();
    $("#setup-body").scrollTop = 0;
  },
  save(section, key, value) { return Settings.save(section, key, value); },
  render() {
    this.shown = true;
    const steps = this.steps();
    this.step = Math.min(this.step, steps.length - 1);
    const [id, title] = steps[this.step];
    $("#setup-steps").innerHTML = steps.map(([, t], i) =>
      `<span class="wdot ${i < this.step ? "done" : ""} ${i === this.step ? "now" : ""}" title="${esc(t)}"></span>`).join("");
    $("#setup-title").textContent = title;
    $("#setup-count").textContent = `Step ${this.step + 1} of ${steps.length}`;
    $("#setup-body").innerHTML = this[id]();
    $("#setup-back").hidden = this.step === 0;
    $("#setup-skip").hidden = this.step === steps.length - 1;
    $("#setup-next").textContent = this.step === 0 ? "Let's set it up" : this.step === steps.length - 1 ? "Let's fly" : "Next";
    this["wire_" + id]?.();
  },
  finish() { $("#dlg-setup").close(); },
  devices(list, cur) {
    return `<option value="" ${!cur ? "selected" : ""}>System default</option>` + list.map((d) =>
      `<option value="${esc(d.name)}" ${cur && d.name.toLowerCase().includes(String(cur).toLowerCase()) ? "selected" : ""}>${esc(d.name)}</option>`).join("");
  },

  language() {
    return `<div class="whero"><img src="/static/logo.svg" alt="" width="64" height="64">
      <h2>Welcome to LocalTC</h2>
      <p>ATC and a first officer for MSFS 2024, running on this computer. A couple of minutes here sets up what's worth
        choosing before your first flight. First, your language:</p></div>
      ${Language.picker("w")}
      <p class="muted small">Everything here saves as you go, and all of it can be changed later in Quick Settings and the gear.</p>`;
  },
  wire_language() { Language.wire("w", () => this.render()); },

  model() {
    const st = S.settings.settings, mistral = (S.cloud?.providers || []).find((p) => p.id === "mistral");
    const on = mistral?.has_key && st.cloud?.enabled;
    const link = mistral?.signup || "https://console.mistral.ai/";
    return `<p>ATC and the copilot understand what you say, and word what they say back, with a language model. The one on
      this PC works offline but is small enough to run beside the sim; a large one in the cloud understands far more of
      what you say, and its replies sound like people.</p>
      <div class="wbox"><b>Recommended: Mistral, free with your own key</b>
        <ol class="small">
          <li>Sign up at <a href="${esc(link)}" target="_blank" rel="noopener">console.mistral.ai</a>: the free plan, no card needed.</li>
          <li>Open <b>API Keys</b> and create a key.</li>
          <li>Paste it here and press <b>Save and use it</b>.</li>
        </ol>
        <div class="row"><input type="password" id="w-mistral" placeholder="${mistral?.has_key ? "A key is saved" : "Paste your Mistral key"}" autocomplete="off" spellcheck="false">
          <button type="button" class="btn primary small" id="w-mistral-save">Save and use it</button></div>
        <span class="small" id="w-mistral-note">${on ? '<span class="ok">&#10003; The cloud model is on.</span>' : ""}</span>
        <p class="muted small">The key is kept in Windows' credential store, never in a file. While the cloud model is on, what
          you say and your flight's details go to Mistral to be understood.</p></div>
      <p class="muted small">No key? Press <b>Next</b>: everything works offline with the model on this PC, just more strictly.
        You can add one any time in Quick Settings &rarr; Language models.</p>`;
  },
  wire_model() {
    $("#w-mistral-save").onclick = async () => {
      const key = $("#w-mistral").value.trim();
      if (!key) { toast("Paste the key first", true); return; }
      try {
        S.cloud = await api("cloud/key", { provider: "mistral", key });
        await this.save("cloud", "enabled", true);
        $("#w-mistral").value = "";
        $("#w-mistral-note").innerHTML = '<span class="ok">&#10003; Saved. The cloud model is on from the next flight.</span>';
      } catch (e) { fail(e); }
    };
  },

  computer() {
    const m = S.models, hw = m.hardware, st = S.settings.settings;
    const cur = m.catalog.profiles.find((p) => p.llm === st.llm.model && (p.whisper === st.voice.model ||
      (st.voice.model === "auto" && p.whisper === m.whisper_model)) && p.voice === st.tts.voice);
    const missing = m.status.filter((s) => !s.installed);
    const gpu = hw.gpu ? `${esc(hw.gpu)} (${hw.gpu_vram_gb} GB)` : "no NVIDIA graphics card";
    return `<p>LocalTC understands you with speech recognition and a language model, and speaks with voices, all on this
      computer beside the sim. Pick how much of the computer they may use.</p>
      <div class="hw">This computer: ${hw.cpu_cores} CPU threads, ${hw.ram_gb} GB RAM, ${gpu}.</div>
      <div class="profiles">${m.catalog.profiles.map((p) => `<button type="button" class="profile ${cur?.id === p.id ? "current" : ""}" data-wprofile="${p.id}">
        <b>${esc(p.label)}</b>${p.id === m.recommended ? '<span class="rec">recommended</span>' : ""}<div class="small muted">${esc(p.description)}</div></button>`).join("")}</div>
      <div class="wbox">${missing.length
        ? `<b>${missing.length === 1 ? "1 download still to do" : `${missing.length} downloads still to do`}</b>: once, and flights then work offline.
           ${missing.some((s) => s.kind === "llm") && !m.ollama.running ? '<span class="muted small">The language model comes with Ollama, its engine, installed first.</span>' : ""}
           <div class="row"><button type="button" class="btn primary small" id="w-install">Download now</button><span class="muted small" id="w-jobs"></span></div>`
        : '<span class="ok">&#10003; Everything for this profile is downloaded.</span>'}</div>
      ${componentsPart(m, ["gpu", "webview2"])}`;
  },
  wire_computer() {
    $$("[data-wprofile]").forEach((b) => (b.onclick = async () => {
      try {
        await api("models/profile", { profile: b.dataset.wprofile });
        S.settings = await api("settings"); S.models = await api("models");
        this.render();
      } catch (e) { fail(e); }
    }));
    $$("#setup-body [data-install]").forEach((b) => (b.onclick = () => api("models/install", { kinds: [b.dataset.install] })
      .then(() => { b.disabled = true; b.textContent = "Installing ..."; }).catch(fail)));
    const install = $("#w-install");
    if (install) install.onclick = () => api("models/install", { kinds: S.models.status.filter((s) => !s.installed).map((s) => s.kind) })
      .then(() => ($("#w-jobs").textContent = "Downloading: carry on meanwhile.")).catch(fail);
  },
  jobs(jobs) {
    if (!$("#dlg-setup").open || this.steps()[this.step]?.[0] !== "computer") return;
    const running = Object.entries(jobs || {}).filter(([, j]) => j.state === "running");
    const names = { llm: "the language model", whisper: "Whisper", voice: "the voice", voice_lang: "the copilot's voices",
      kokoro: "Kokoro", ...COMPONENT_NAMES };
    const el = $("#w-jobs");
    if (el) el.innerHTML = running.length ? running.map(([k, j]) => `<span>${esc(names[k] || k)}</span>: <span>${esc(j.message)}</span>`).join(" · ") : "Done.";
    for (const kind of COMPONENT_KINDS) {
      const bar = $(`#p-${kind}`), j = jobs?.[kind];
      if (bar) bar.hidden = !j || j.state !== "running";
      if (bar && j?.state === "running") bar.firstElementChild.style.width = j.fraction == null ? "" : `${Math.round(j.fraction * 100)}%`;
    }
    const key = Object.entries(jobs || {}).map(([k, j]) => `${k}:${j.state}`).join(",");
    if (!running.length && key !== this.doneJobs) {  // all finished: show what's installed now
      this.doneJobs = key;
      api("models").then((m) => { S.models = m; if (this.steps()[this.step]?.[0] === "computer") this.render(); }).catch(() => {});
    }
  },

  talk() {
    const v = S.settings.settings.voice, mix = S.settings.settings.mixer;
    return `<p>Talk to ATC as on a real radio: hold your push-to-talk, say the call, let go. Typing a call works too.</p>
      <label class="check-row"><input type="checkbox" id="w-voice" ${v.enabled ? "checked" : ""}> Talk to ATC with a microphone</label>
      <div class="row"><label>Microphone<select id="w-mic">${this.devices(S.devices.inputs || [], v.input_device)}</select></label></div>
      <div class="row"><label>Push-to-talk<select id="w-ptt">
        <option value="keyboard" ${v.ptt === "keyboard" ? "selected" : ""}>A keyboard key (works while the sim has focus)</option>
        <option value="joystick" ${v.ptt === "joystick" ? "selected" : ""}>A yoke or joystick button</option>
        <option value="enter" ${v.ptt === "enter" ? "selected" : ""}>Only the headset button in this window</option></select></label></div>
      <div class="row" ${v.ptt === "keyboard" ? "" : "hidden"}><span>Transmit key</span><span class="keycap" id="w-key">${esc(keyLabel(v.ptt_key))}</span>
        <button type="button" class="btn small" id="w-key-set">Change</button><span class="muted small" id="w-key-hint"></span></div>
      <div class="row" ${v.ptt === "joystick" ? "" : "hidden"}><label>Transmit button<input id="w-joy" value="${esc(v.ptt_joystick)}" placeholder="joystick:0:button:3"></label>
        ${Settings.joyPicker("wptt")}</div>
      <div class="row"><span>Intercom key (talk to the copilot)</span><span class="keycap" id="w-ic-key">${esc(keyLabel(v.intercom_key))}</span>
        <button type="button" class="btn small" id="w-ic-key-set">Change</button><span class="muted small" id="w-ic-key-hint"></span></div>
      <div class="row"><label>Or an intercom button<input id="w-ic-joy" value="${esc(v.intercom_joystick)}" placeholder="joystick:0:button:4"></label>
        ${Settings.joyPicker("wic")}</div>
      <div class="wbox"><b>Check your microphone</b>
        <div class="row"><label>Gain <input type="range" id="w-gain" min="0" max="3" step="0.05" value="${mix.mic_gain}"></label>
          <button type="button" class="btn small" id="w-mic-test">&#127908; Test</button></div>
        <span class="muted small" id="w-mic-note">Press Test and say a call: you'll hear yourself as ATC does, and whether the level is right.</span></div>`;
  },
  wire_talk() {
    const val = (id) => $(id).value;
    $("#w-voice").onchange = (e) => this.save("voice", "enabled", e.target.checked);
    $("#w-mic").onchange = () => this.save("voice", "input_device", val("#w-mic"));
    $("#w-ptt").onchange = async () => { await this.save("voice", "ptt", val("#w-ptt")); this.render(); };
    $("#w-key-set").onclick = () => Settings.captureKey("ptt_key", "#w-key", "#w-key-hint");
    $("#w-ic-key-set").onclick = () => Settings.captureKey("intercom_key", "#w-ic-key", "#w-ic-key-hint");
    $("#w-joy").onchange = () => this.save("voice", "ptt_joystick", val("#w-joy").trim());
    $("#w-ic-joy").onchange = () => this.save("voice", "intercom_joystick", val("#w-ic-joy").trim());
    Settings.bindJoy("wptt", "#w-joy", "ptt_joystick");
    Settings.bindJoy("wic", "#w-ic-joy", "intercom_joystick");
    $("#w-gain").onchange = () => api("mixer", { settings: { mixer: { mic_gain: Number(val("#w-gain")) } }, save: true })
      .then(() => (S.settings.settings.mixer.mic_gain = Number(val("#w-gain")))).catch(fail);
    $("#w-mic-test").onclick = async (e) => {
      const b = e.target, note = $("#w-mic-note");
      b.disabled = true;
      note.textContent = "Listening for 4 seconds ... say a call.";
      try {
        const r = await api("voice/mic_test", { seconds: 4 });
        note.innerHTML = {
          silent: "<b class=\"bad\">Nothing heard.</b> Pick the microphone you talk into above, and check it isn't muted.",
          quiet: "<b class=\"warn\">Quiet.</b> Raise the gain a little, or speak closer to the microphone.",
          loud: "<b class=\"warn\">Too loud: it clips.</b> Lower the gain a little.",
          good: "<b class=\"ok\">Good level.</b> That's how ATC hears you.",
        }[r.verdict] || "";
      } catch (err) { note.textContent = err.message; }
      b.disabled = false;
    };
  },

  hear() {
    const st = S.settings.settings, t = st.tts, headset = st.mixer.sidetone > 0;
    return `<p>ATC, the other flights and your copilot come through a radio, as in a real aircraft.</p>
      <div class="row"><label>Speakers or headset<select id="w-out">${this.devices(S.devices.outputs || [], t.output_device)}</select></label></div>
      <div class="wchoice">
        <button type="button" class="wcard ${headset ? "current" : ""}" data-wear="headset"><b>&#127911; A headset</b>
          <span class="muted small">You'll hear yourself on the radio while you hold the key, as in a real cockpit.</span></button>
        <button type="button" class="wcard ${headset ? "" : "current"}" data-wear="speakers"><b>&#128266; Speakers</b>
          <span class="muted small">No sidetone: the microphone would hear it.</span></button>
      </div>
      <div class="row"><label>Voices<select id="w-provider">
        <option value="piper" ${t.provider === "piper" ? "selected" : ""}>Piper: on this PC, quick, always there</option>
        <option value="kokoro" ${t.provider === "kokoro" ? "selected" : ""}>Kokoro: on this PC, more natural, a little slower (a 340 MB download)</option></select></label></div>
      <div class="row"><button type="button" class="btn small" id="w-hear">&#9654; Hear ATC</button>
        <span class="muted small">The mixer (the sliders at the top of the window) sets the volume of each.</span></div>`;
  },
  wire_hear() {
    $("#w-out").onchange = () => this.save("tts", "output_device", $("#w-out").value);
    $$("[data-wear]").forEach((b) => (b.onclick = async () => {
      const sidetone = b.dataset.wear === "headset" ? 0.4 : 0;
      await api("mixer", { settings: { mixer: { sidetone } }, save: true }).catch(fail);
      S.settings.settings.mixer.sidetone = sidetone;
      this.render();
    }));
    $("#w-provider").onchange = async () => {
      const v = $("#w-provider").value;
      // Kokoro chosen: downloaded meanwhile (Piper speaks until it's there).
      if (await this.save("tts", "provider", v) && v === "kokoro") api("models/install", { kinds: ["kokoro"] }).catch(() => {});
    };
    $("#w-hear").onclick = async (e) => {
      e.target.disabled = true;
      // Kokoro's own voice once it's downloaded; Piper's until then.
      const kokoro = S.settings.settings.tts.provider === "kokoro";
      try { await api("voice/preview", kokoro ? { provider: "kokoro" } : {}); } catch (err) {
        if (kokoro) { toast("Kokoro is still downloading: this is Piper"); await api("voice/preview", {}).catch(fail); } else fail(err);
      }
      e.target.disabled = false;
    };
  },

  copilot() {
    const st = S.settings.settings, on = st.crew.enabled !== false;
    const verbosity = [["quiet", "Quiet: only what's safety"], ["standard", "Standard: callouts, ATC relays and reminders"],
      ["chatty", "Chatty: also status updates and small talk"]];
    return `<p>A first officer beside you: it makes the callouts, runs the checklists, answers you on the intercom and, with the
      Copilot switch, works the radio with ATC. Do you want one?</p>
      <div class="wchoice">
        <button type="button" class="wcard ${on ? "current" : ""}" data-wcrew="on"><b>Fly with a copilot</b>
          <span class="muted small">Callouts, checklists and the intercom; the radio with ATC when you flip the Copilot switch.</span></button>
        <button type="button" class="wcard ${on ? "" : "current"}" data-wcrew="off"><b>Fly alone</b>
          <span class="muted small">No copilot. You can bring one in any time in Quick Settings.</span></button>
      </div>
      ${on ? `<label class="check-row"><input type="checkbox" id="w-hands" ${st.crew.hands === "pm" ? "checked" : ""}> Let it work the aircraft's controls</label>
        <span class="hint">Its own side of the cockpit, by itself: the radios in standby, the transponder, the altimeter, the lights, the
          gear and flaps after takeoff, the altitude and heading ATC gives. Off: it says what's due and does only what you ask it
          to ("flaps 2"). Only in aircraft it knows; your side (the engines, the autopilot, flaps for takeoff and landing) it never
          touches either way.</span>
        <div class="row"><label>When the Copilot switch is on, it
          <select id="w-copilot"><option value="full" ${st.ui.copilot === "full" ? "selected" : ""}>works the whole radio: requests, check-ins, readbacks</option>
          <option value="assist" ${st.ui.copilot === "assist" ? "selected" : ""}>reads back and changes frequencies; you make the calls</option></select></label></div>
        <div class="row"><label>What it says by itself<select id="w-verbosity">${verbosity.map(([v, l]) =>
          `<option value="${v}" ${st.crew.verbosity === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>` : ""}`;
  },
  wire_copilot() {
    $$("[data-wcrew]").forEach((b) => (b.onclick = async () => {
      if (await this.save("crew", "enabled", b.dataset.wcrew === "on")) this.render();
    }));
    const on = (id, fn) => { if ($(id)) $(id).onchange = fn; };
    on("#w-hands", (e) => this.save("crew", "hands", e.target.checked ? "pm" : "calls"));
    on("#w-copilot", () => this.save("ui", "copilot", $("#w-copilot").value));
    on("#w-verbosity", () => this.save("crew", "verbosity", $("#w-verbosity").value));
  },

  people() {
    const cur = S.settings.settings.crew.personality || "neutral";
    return `<p>Choose who's in the right seat. Each has a voice and a manner of their own; the safety calls are the standard
      ones whoever you pick.</p>
      <div class="wpeople">${(S.settings.personalities || []).map((p) => `<div class="wcard person ${p.id === cur ? "current" : ""}" data-person="${esc(p.id)}" role="button" tabindex="0">
        <b translate="no">${esc(p.name)}</b><span class="muted small">${esc(p.style)}</span><i class="small" translate="no">&ldquo;${esc(p.sample)}&rdquo;</i>
        <button type="button" class="wplay" data-hear="${esc(p.id)}" title="Hear ${esc(p.name)}">&#9654;</button></div>`).join("")}</div>`;
  },
  wire_people() {
    $$("[data-hear]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      api("voice/crew_preview", { personality: b.dataset.hear }).catch(fail);
    }));
    $$("[data-person]").forEach((card) => {
      const pick = async () => {
        await this.save("crew", "personality", card.dataset.person);
        $$("[data-person]").forEach((x) => x.classList.toggle("current", x === card));
      };
      card.onclick = pick;
      card.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
    });
  },

  atc() {
    const st = S.settings.settings;
    const phrase = [["auto", "By region: FAA in the US and Canada, ICAO elsewhere"], ["faa", "FAA everywhere"], ["icao", "ICAO everywhere"]];
    return `<p>How ATC works with you.</p>
      <div class="row"><label>Phraseology<select id="w-phrase">${phrase.map(([v, l]) =>
        `<option value="${v}" ${st.atc.phraseology === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
      <label class="check-row"><input type="checkbox" id="w-humanize" ${st.atc.humanize !== false ? "checked" : ""}> Natural speech: controllers and other pilots say "uh" now and then, as people do</label>
      <label class="check-row"><input type="checkbox" id="w-chatter" ${st.atc.chatter ? "checked" : ""}> Other flights on the frequency</label>
      <label class="check-row"><input type="checkbox" id="w-personal" ${st.atc.personalities !== false ? "checked" : ""}> Controllers with personalities of their own</label>`;
  },
  wire_atc() {
    $("#w-phrase").onchange = () => this.save("atc", "phraseology", $("#w-phrase").value);
    $("#w-humanize").onchange = (e) => this.save("atc", "humanize", e.target.checked);
    $("#w-chatter").onchange = (e) => this.save("atc", "chatter", e.target.checked);
    $("#w-personal").onchange = (e) => this.save("atc", "personalities", e.target.checked);
  },

  flight() {
    const ui = S.settings.settings.ui;
    return `<p>LocalTC clears you as filed: put in your SimBrief username and <b>New Flight</b> imports your latest plan, with
      your callsign, route and runways. You can type a plan in instead, or just press Start and fly.</p>
      <div class="row"><label>SimBrief username or Pilot ID<input id="w-simbrief" value="${esc(ui.simbrief_user)}" autocomplete="off" spellcheck="false"></label></div>
      <label class="check-row"><input type="checkbox" id="w-require-plan" ${ui.require_plan !== false ? "checked" : ""}> <span>Require a flight
        plan to start a flight. Off: press <b>Start</b> and fly, and <b>File Plan</b> gives ATC your plan whenever you have it.</span></label>
      <div class="wbox"><b>Each flight</b><ol class="small">
        <li>Load your aircraft in MSFS 2024, at a gate, cold and dark or ready to go.</li>
        <li><b>New Flight</b> imports your plan; then <b>Start</b>. The light turns green when the sim's connected.</li>
        <li>Tune COM1 and call clearance: "Request IFR to Denver". Read back what ATC gives you, and check in after each handoff.</li>
      </ol></div>`;
  },
  wire_flight() {
    $("#w-simbrief").onchange = () => this.save("ui", "simbrief_user", $("#w-simbrief").value.trim());
    $("#w-require-plan").onchange = (e) => requirePlan(e.target.checked);
  },

  traffic() {
    const on = !!S.settings.settings.traffic?.enabled;
    return `<p>The other aircraft around you: the sim's own traffic, or LocalTC's.</p>
      <div class="wbox wwarn"><b class="exp-badge">EXPERIMENTAL</b>
        <p><b>LocalTC's traffic</b>: the real flights around you, live from free ADS-B sources, flown in the sim with their
          airline's FSLTL model; the gates filled with the airlines that fly there; and ATC controlling them, holding them
          short or sending them around when you have the runway and stopping them to give way to you on the ground.</p>
        <p><b>It's still in progress:</b> now and then an aircraft stops somewhere odd, turns late or holds the others up.
          Each report you send in helps it along.</p>
        <p><b>Before you turn it on, turn off every other traffic</b>: MSFS's own (Options &rarr; General &rarr; Traffic: air
          traffic and parked aircraft off) and any traffic add-on (FSLTL's injector, AIG and the like). With both on you'll see
          aircraft twice.</p>
        <label class="check-row"><input type="checkbox" id="w-traffic" ${on ? "checked" : ""}> Turn on LocalTC's traffic</label></div>
      <p class="muted small">FSLTL's free models give each aircraft its airline's colours; without them the sim's own models stand
        in. Turn it on or off any time in Quick Settings &rarr; Traffic.</p>`;
  },
  wire_traffic() {
    $("#w-traffic").onchange = (e) => this.save("traffic", "enabled", e.target.checked);
  },

  done() {
    const st = S.settings.settings, v = st.voice;
    const who = (S.settings.personalities || []).find((p) => p.id === (st.crew.personality || "neutral"));
    const copilot = who && who.sex ? who.name : "your copilot";
    const ptt = v.ptt === "keyboard" ? keyLabel(v.ptt_key) : v.ptt === "joystick" ? "your yoke or joystick button" : "the headset button";
    // One sentence each way, so it's one sentence in every language ("Hold" alone was a holding pattern).
    const ic = st.crew.enabled && v.intercom_key ? keyLabel(v.intercom_key) : "";
    const named = who && who.sex;  // (one with a name, else "your copilot": a sentence of its own, for the grammar)
    const said = v.enabled
      ? (!ic ? `Hold ${ptt} to talk to ATC.` : named ? `Hold ${ptt} to talk to ATC, and ${ic} to talk to ${copilot}.`
        : `Hold ${ptt} to talk to ATC, and ${ic} to talk to your copilot.`)
      : (!ic ? "Type your calls to ATC." : named ? `Type your calls to ATC, and hold ${ic} to talk to ${copilot}.`
        : `Type your calls to ATC, and hold ${ic} to talk to your copilot.`);
    return `<div class="whero"><img src="/static/logo.svg" alt="" width="56" height="56"><h2>You're set</h2><p>${esc(said)}</p></div>
      <ul class="benefits small">
        <li><b>Talk like on a real radio.</b> Hold the key a moment before you speak and until you've finished, say your callsign, and read back what ATC gives you.</li>
        <li><b>ATC and the copilot aren't perfect.</b> When something goes wrong, send it in (the gear &rarr; Support &amp; feedback): each report makes them better.</li>
        <li><b>The mixer</b> (the sliders at the top) sets the volume of ATC, the other flights, the copilot and your microphone.</li>
        <li><b>Quick Settings</b> has all of this and more: the voices, the language models, the copilot's hands, checklists, LocalTC's traffic, the taxi guidance.</li>
      </ul>`;
  },
};
$("#setup-next").onclick = () => (Setup.step === Setup.steps().length - 1 ? Setup.finish() : Setup.go(1));
$("#setup-back").onclick = () => Setup.go(-1);
$("#setup-skip").onclick = () => Setup.finish();
// However it's closed (finished, skipped, Esc), it's done: never shown by itself again, nor the welcome notes it holds
// or the language question (its first step).
$("#dlg-setup").addEventListener("close", () => {
  if (!Setup.shown) return;  // it never loaded: shown again next time
  if (S.state) { S.state.setup = false; S.state.welcome = false; S.state.whats_new = null; S.state.language_asked = true; }
  Popups.drop("welcome", "whatsnew", "language");
  api("settings", { settings: { ui: { setup_done: true, welcome_seen: true, language_asked: true, notes_seen: S.state?.version || "" } } }).catch(() => {});
});

/* ---------- New Flight ---------- */

const Flight = {
  fetched: null,
  /** Without a plan required ([ui] require_plan off), the plan is filed with ATC whenever it's ready: the flight
   * starts without one and goes on. */
  get filing() { return S.state?.require_plan === false; },
  label() {
    $("#btn-newflight").textContent = this.filing ? "File Plan" : "New Flight";
    $("#btn-newflight").title = this.filing ? "Give ATC your flight plan (SimBrief or typed in): the flight goes on" : "";
  },
  open() {
    const plan = S.state.plan;
    $("#plan-error").hidden = true;
    const running = S.state.status === "running" || S.state.status === "starting";
    $("#plan-title").textContent = this.filing ? "File Flight Plan" : "New Flight";
    $("#plan-start").hidden = this.filing;
    $("#plan-save").textContent = this.filing ? "File plan" : "Save plan";
    $("#plan-save").classList.toggle("primary", this.filing);
    $("#sb-user").value = S.state.simbrief_user || S.settings?.settings?.ui?.simbrief_user || localStorage.getItem("sbUser") || "";
    if (plan && plan.source === "manual") this.fill(plan);
    this.fetched = null;
    $("#sb-result").hidden = true;
    this.sub(plan?.source === "manual" ? "manual" : "simbrief");
    $("#plan-note").textContent = this.filing
      ? (running ? "ATC has it at once; a clearance already given stands." : "ATC has it when you start the flight.")
      : (S.state.status === "running" ? "Starting a new flight ends the current one." : "");
    $("#dlg-flight").showModal();
  },
  fill(p) {
    for (const k of ["callsign", "aircraft", "origin", "destination", "alternate", "route"]) $(`#mp-${k}`).value = p[k] || "";
    $("#mp-rules").value = p.rules || "IFR";
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
      body = Object.fromEntries(["callsign", "aircraft", "origin", "destination", "alternate", "route", "cruise", "rules"].map((k) => [k, $(`#mp-${k}`).value]));
    }
    body.start = start;
    body.file = this.filing;
    try {
      const r = await api("flight/plan", body);
      $("#dlg-flight").close();
      toast(start ? `Starting ${r.summary}` : r.filed ? `Plan filed with ATC: ${r.summary}` : `Plan saved: ${r.summary}`);
    } catch (e) { $("#plan-error").textContent = e.message; $("#plan-error").hidden = false; }
  },
};
$("#btn-newflight").onclick = () => Flight.open();
/** [ui] require_plan, from Settings or the setup guide: saved, and the button renamed at once. */
function requirePlan(on) {
  if (S.state) S.state.require_plan = on;
  Flight.label();
  return Settings.save("ui", "require_plan", on);
}
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

/* ---------- Logbook ---------- */

const Logbook = {
  map: null,
  player: null,
  async load() {
    const body = $("#logbook-body");
    try { this.render(await api("logbook")); } catch (e) { body.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  },
  render(r) {
    const t = r.totals;
    const hm = (min) => (min == null ? "—" : `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, "0")}`);
    const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
    const stat = (v, k) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`;
    if (this.map) { this.map.map.remove(); this.map = null; }
    $("#logbook-body").innerHTML = `
      <div class="stats">
        ${stat(t.flights, "flights")}${stat(t.air_hours, "hours flown")}${stat(t.airports.length, "airports")}
        ${stat(t.distance_nm.toLocaleString(), "nm")}${stat(t.average_landing_fpm == null ? "—" : `${t.average_landing_fpm}`, "avg landing fpm")}
        ${stat(t.readback_accuracy == null ? "—" : `${Math.round(t.readback_accuracy * 100)}%`, "readbacks right")}
        ${S.account?.signed_in && r.flights.length ? '<button class="btn small lb-wrapped" id="lb-wrapped" title="A week, a month or a year of your flying, as a story">Wrapped</button>' : ""}
      </div>
      ${r.flights.length ? '<div class="lb-map" id="lb-map" aria-label="Map of every flight in the logbook"></div>' : ""}
      ${r.flights.length ? `<table class="lb"><thead><tr><th>Date</th><th>Flight</th><th>Route</th><th>Air</th><th>Block</th><th>Landing</th><th></th></tr></thead><tbody>
        ${r.flights.map((f) => `<tr data-route="${esc(`${f.origin}>${f.destination}`)}" title="${esc([f.aircraft, f.departure_gate && `from ${f.departure_gate}`, f.arrival_gate && `to ${f.arrival_gate}`, f.max_alt_ft && `max ${f.max_alt_ft.toLocaleString()} ft`, `${f.distance_nm} nm`].filter(Boolean).join(" · "))}">
          <td>${esc(day(f.started_at))}</td><td class="mono">${esc(f.callsign || "—")}</td>
          <td class="mono">${esc(f.origin || "?")} → ${esc(f.destination || "?")}${f.landed ? "" : ' <span class="muted small">(no landing)</span>'}</td>
          <td class="mono">${hm(f.air_min)}</td><td class="mono">${hm(f.block_min)}</td>
          <td class="mono">${f.landing_vs_fpm == null ? "—" : `${f.landing_vs_fpm} fpm`}</td>
          <td class="lb-actions">${f.has_recording ? `<button class="btn small" data-replay="${esc(f.id)}" title="Watch the flight again: the map and the radio, in step">&#9654; Replay</button>`
              : '<span class="muted small" title="The recording of this flight is not on this computer (recording was off, or it was deleted)">no recording</span>'}
            ${f.has_recording && S.account?.signed_in ? (f.replay_uploaded_at
              ? `<button class="linkbtn up on" data-unshare="${esc(f.id)}" title="The replay is in your account (localtc.tech and the phone). Click to remove it from the account.">&#9729;&#9654;</button>`
              : `<button class="linkbtn up" data-share="${esc(f.id)}" title="Upload the replay (the track and the radio transcript, no audio) so it plays on localtc.tech and your phone">Upload</button>`) : ""}
            ${S.account?.signed_in ? `<button class="linkbtn up${f.share_url ? " on" : ""}" data-card="${esc(f.id)}" title="${f.share_url ? "Shared: anyone with the link sees this flight's card" : "Share this flight: a public card with the route and the numbers"}">${f.share_url ? "Shared" : "Share flight"}</button>` : ""}
            <span class="sync ${f.synced_at ? "on" : ""}" title="${f.synced_at ? "In your account" : "Only on this computer"}">${f.synced_at ? "&#9729;" : ""}</span>
            ${f.has_recording ? `<button class="linkbtn" data-rebuild="${esc(f.id)}" title="Measure this line again from its recording: times, landing rate, distance, aircraft">Rebuild</button>` : ""}
            <button class="linkbtn" data-del="${esc(f.id)}" title="Delete from this computer">&times;</button></td></tr>`).join("")}
      </tbody></table>` : `<p class="muted pad">No flights yet. Every live flight gets a line here when it ends: airports, times, the landing.</p>`}
      <p class="muted small pad">The logbook is kept on this computer (${t.flights} flight${t.flights === 1 ? "" : "s"}).
        An optional account in Quick Settings can copy it to localtc.tech.</p>`;
    $$("[data-del]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      if (confirm("Delete this flight from the logbook on this computer?")) api("logbook/delete", { id: b.dataset.del }).then((v) => this.render(v)).catch(fail);
    }));
    $$("[data-rebuild]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      if (!confirm("Measure this flight again from its recording? Its times, landing rate, distance and aircraft are replaced.")) return;
      b.disabled = true;
      b.textContent = "Rebuilding ...";
      api("logbook/rebuild", { id: b.dataset.rebuild }).then((v) => { this.render(v); toast("Rebuilt from the recording"); })
        .catch((err) => { fail(err); b.disabled = false; b.textContent = "Rebuild"; });
    }));
    $$("[data-replay]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); this.replay(b.dataset.replay); }));
    $$("[data-share]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      b.disabled = true;
      b.textContent = "Uploading ...";
      api("replay/upload", { id: b.dataset.share }).then((v) => { this.render(v); toast("Replay uploaded: it plays on localtc.tech and your phone"); })
        .catch((err) => { fail(err); b.disabled = false; b.textContent = "Upload"; });
    }));
    $$("[data-unshare]").forEach((b) => (b.onclick = (e) => {
      e.stopPropagation();
      if (confirm("Remove this flight's replay from your account? It still plays here.")) api("replay/remove", { id: b.dataset.unshare }).then((v) => this.render(v)).catch(fail);
    }));
    $$("[data-card]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); this.share(r.flights.find((f) => f.id === b.dataset.card)); }));
    if ($("#lb-wrapped")) $("#lb-wrapped").onclick = () => this.wrapped();
    if (!r.flights.length) return;
    // Every flight on a map; a click on a route marks its rows, a click on a row shows its route.
    const pick = (o, d) => $$("#logbook-body tr[data-route]").forEach((tr) => tr.classList.toggle("picked", tr.dataset.route === `${o}>${d}`));
    this.map = new FlightsMap($("#lb-map"), { tiles: S.state.map_tiles !== false, onRoute: pick });
    this.map.show(FlightsMap.summarize(r.flights));
    $$("#logbook-body tr[data-route]").forEach((tr) => (tr.onclick = () => {
      const [o, d] = tr.dataset.route.split(">");
      pick(o, d);
      this.map.focus(o, d);
    }));
  },
};

// A flight to rewatch (replayplayer.js): built from its recording by the app the first time, then kept.
Object.assign(Logbook, {
  async replay(id) {
    $("#logbook-body").hidden = true;
    $("#replay-page").hidden = false;
    $("#replay-note").textContent = "Loading the flight ...";
    this.closePlayer();
    try {
      const r = await api(`replay?id=${encodeURIComponent(id)}`);
      $("#replay-note").textContent = "Space plays and pauses, J and K go to the previous and next call, the arrows skip 10 seconds.";
      this.player = new ReplayPlayer($("#replay-player"), r, { tiles: S.state.map_tiles !== false });
      setTimeout(() => this.player?.fit(), 0);
    } catch (e) {
      $("#replay-note").textContent = "";
      fail(e);
      this.back();
    }
  },
  closePlayer() {
    if (this.player) { this.player.destroy(); this.player = null; }
  },
  back() {
    this.closePlayer();
    $("#replay-page").hidden = true;
    $("#logbook-body").hidden = false;
    this.map?.fit();
  },
});
$("#replay-back").onclick = () => Logbook.back();

// Sharing a flight (a public card at localtc.tech/f/...) and Wrapped: sharecard.js and wrapped.js, the
// website's own files, through the account.
Object.assign(Logbook, {
  model: null,
  async share(f) {
    if (!f) return;
    this.model = this.model || await import("/static/cardmodel.js");
    let moments = [], names = {};
    if (f.has_recording) {
      try {
        const r = await api(`replay?id=${encodeURIComponent(f.id)}`);
        moments = this.model.moments(r.radio);
        names = this.model.airportNames(r.airports);
      } catch (_) { /* no quote to pick from, then */ }
    }
    const toDataUrl = (blob) => new Promise((ok) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.readAsDataURL(blob); });
    ShareCard.dialog({
      base: "/static/", moments, shared: f.share_url, slug: f.share_url ? f.share_url.split("/").pop() : null,
      replay: !!(f.has_recording || f.replay_uploaded_at),
      card: (quote) => this.model.flightCard(f, { quote, names }),
      share: (quote, replay) => api("share", { id: f.id, quote, names, replay }),
      putImage: async (slug, blob) => api("share/image", { slug, png: await toDataUrl(blob) }),
      unshare: () => api("share/remove", { id: f.id }),
      onClose: () => this.load(),
    });
  },
  wrappedView: null,
  wrapped() {
    $("#logbook-body").hidden = true;
    $("#wrapped-page").hidden = false;
    if (!this.wrappedView) {
      const toDataUrl = (blob) => new Promise((ok) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.readAsDataURL(blob); });
      const q = (r) => new URLSearchParams({ period: r.period, from: r.from, to: r.to, tz: r.tz, label: r.label });
      this.wrappedView = new WrappedView($("#wrapped-view"), {
        base: "/static/",
        load: (r) => api(`wrapped?${q(r)}`),
        share: (r) => api("wrapped/share", Object.fromEntries(q(r))),
        timeline: (period, tz) => api(`wrapped/timeline?${new URLSearchParams({ period, tz })}`),
        putImage: async (slug, blob) => api("share/image", { slug, png: await toDataUrl(blob) }),
      });
      this.wrappedView.open("month", -1);
    }
  },
});
$("#wrapped-back").onclick = () => {
  Logbook.wrappedView?.stop();
  $("#wrapped-page").hidden = true;
  $("#logbook-body").hidden = false;
  Logbook.map?.fit();
};

/* ---------- Quick Settings ---------- */

/** EXPERIMENTAL traffic control's last status, in a line. */
/** LocalTC's traffic now, in a line or two (Quick Settings). */
function trafficStatus(s) {
  if (!s || s.mode === "off") return "";
  const parts = [`${s.live} real flights`, `${s.parked} parked`];
  if (s.source) parts.push(`positions from ${s.source}`);
  parts.push(s.fsltl ? "FSLTL models" : "the sim's models (FSLTL isn't installed)");
  const recent = (s.recent || []).slice(0, 4);
  return `<br><b>Now:</b> ${esc(parts.join(", "))}.${s.note ? `<br><b style="color:var(--amber)">${esc(s.note)}</b>` : ""}` +
    `${recent.length ? `<br><span class="muted">${recent.map(esc).join("<br>")}</span>` : ""}`;
}

/** The copilot's personality: a select of them (crew/personalities.py), with the chosen one's manner and a line. */
function personalityRow(list, current, idPrefix = "s-crew") {
  const cur = current || "neutral";
  return `<div class="row"><label>Copilot's personality<select id="${idPrefix}-personality">${(list || []).map((p) =>
    `<option value="${esc(p.id)}" ${p.id === cur ? "selected" : ""}>${esc(p.name)}${p.sex ? ` (${p.sex === "F" ? "she" : "he"})` : ""}${p.brief ? ` · ${esc(p.brief)}` : ""}</option>`).join("")}</select>
    <span class="hint" id="${idPrefix}-persona-hint">${personalityHint(list, cur)}</span></label>
    <div><button type="button" class="btn small" id="${idPrefix}-preview">&#9654; Preview</button></div></div>`;
}
function personalityHint(list, id) {
  const p = (list || []).find((x) => x.id === id);
  return p ? `${esc(p.style)} <i translate="no">&ldquo;${esc(p.sample)}&rdquo;</i>` : "";  // (its words: aviation English)
}

const Settings = {
  seenJobs: new Set(),
  async load() {
    const body = $("#settings-body");
    try {
      const [s, m, d, c, v] = await Promise.all([api("settings"), api("models"), S.devices ? Promise.resolve(S.devices) : api("devices"),
        api("cloud").catch(() => null), api("tts").catch(() => null)]);
      S.settings = s; S.models = m; S.devices = d; S.cloud = c; S.tts = v;
      this.render();
    } catch (e) { body.innerHTML = `<div class="error">${esc(e.message)}</div>`; }
  },
  async save(section, key, value) {
    try {
      const r = await api("settings", { settings: { [section]: { [key]: value } } });
      if (S.settings) S.settings.settings[section][key] = value;
      if ($("#restart-bar")) $("#restart-bar").hidden = !r.restart;
      if ($("#saved-note")) {
        $("#saved-note").textContent = "Saved";
        setTimeout(() => ($("#saved-note").textContent = ""), 1500);
      }
      return true;
    } catch (e) { fail(e); return false; }
  },
  async refreshModels() {
    S.models = await api("models");
    this.render();
  },
  statusOf(kind) { return S.models.status.find((x) => x.kind === kind); },
  // Cloud language models: optional, off by default, recommended for quality. Keys go to the system's credential
  // store through the app and never come back to the page.
  cloudCard(st) {
    const c = S.cloud;
    if (!c) return "";
    const on = !!st.cloud?.enabled;
    const kinds = { none: ["No key", "ok"], free: ["Free key", ""], required: ["Paid key", "muted"] };
    const live = Object.fromEntries((c.live || []).map((r) => [r.route.split("/")[0], r]));
    const state = (p) => {
      const r = live[p.id];
      if (r?.dead) return `<span class="mstatus missing">${esc(r.dead)}</span>`;
      if (r?.resting_s) return `<span class="mstatus missing">resting ${r.resting_s} s</span>`;
      if (r?.ok) return `<span class="mstatus ok">&#10003; ${r.ok} answers</span>`;
      if (p.key === "none" || p.has_key) return '<span class="mstatus ok">ready</span>';
      return '<span class="mstatus muted">needs a key</span>';
    };
    return `<div class="card" id="cloud-card">
        <h3>Cloud language model <span class="rec-badge">Recommended for quality</span></h3>
        <p class="muted small">A large model in the cloud understands your calls and words ATC's and the copilot's replies far
          better than one small enough to run beside the sim, and it's given the whole flight (the route, every clearance,
          the ATIS, what was said) where the model on this PC gets only the essentials. Off unless you turn it on: while it's
          on, what you say and your flight's details go to the service answering. Mistral is tried first (the most generous free
          limits), Pollinations (no key) last; one that's busy, out of allowance or down steps aside and the next answers.</p>
        <label class="check-row"><input type="checkbox" id="s-cloud" ${on ? "checked" : ""}> Use cloud language models (from the next flight)</label>
        <label class="check-row"><input type="checkbox" id="s-cloud-local" ${st.cloud?.local_fallback ? "checked" : ""}> When every cloud service fails, the model on this PC answers (if Ollama is running)</label>
        ${c.via ? `<p class="small">This flight's last answer came from <b>${esc(c.via)}</b>.</p>` : ""}
        <details class="cloud-services" ${this.cloudOpen?.has("_all") ? "open" : ""} data-cloud-fold="_all">
          <summary><b>Services</b> <span class="muted small">${c.providers.filter((p) => p.key === "none" || p.has_key).length} of ${c.providers.length} ready</span></summary>
        <div class="cloud-list">${c.providers.map((p) => `<details class="cloud-row" data-provider="${p.id}" data-cloud-fold="${p.id}" ${this.cloudOpen?.has(p.id) ? "open" : ""}>
          <summary class="cloud-name"><b>${esc(p.name)}</b> <span class="cloud-kind ${kinds[p.key][1]}">${kinds[p.key][0]}</span> ${state(p)}</summary>
          <div class="cloud-body">
            <div class="small muted">${esc(p.note)} ${p.signup && p.key !== "none" ? `<a href="${esc(p.signup)}">Get a key</a>` : ""}</div>
            <div class="small muted">Models, in order: ${p.models.map(esc).join(", ")}</div>
          <div class="cloud-key">${p.key === "none" ? "" : `<input type="password" autocomplete="off" spellcheck="false"
              placeholder="${p.has_key ? "Key saved: paste a new one to replace it" : p.id === "cloudflare" ? "ACCOUNT_ID:API_TOKEN" : "Paste your API key"}" data-key="${p.id}">
            <button class="btn small" data-key-save="${p.id}">Save</button>${p.has_key ? `<button class="btn small" data-key-clear="${p.id}">Remove</button>` : ""}`}
            <button class="btn small" data-cloud-test="${p.id}" ${p.key === "none" || p.has_key ? "" : "disabled"}>Test</button></div>
          <div class="small cloud-test" id="cloud-test-${p.id}"></div></div></details>`).join("")}</div>
        </details>
        <p class="muted small">Qwen Chat's, Qoder's and OpenCode's own unlimited free use is only for their own apps, so LocalTC
          can't use them. The order and each
          service's models can be changed in the settings file ([cloud] order, models).</p>
      </div>`;
  },
  // Who speaks for ATC and the copilot, all on this PC: Piper (quick, always there) or Kokoro (more natural, slower,
  // Piper standing in), each with a preview; and the voices of the language the copilot talks with you in.
  voicesPart(st) {
    const v = S.tts;
    if (!v) return "";
    const by = Object.fromEntries((v.providers || []).map((p) => [p.id, p]));
    const state = (id) => {
      const p = by[id];
      if (!p) return "";
      if (!p.ready) return `<span class="mstatus missing">${esc(p.why)}</span>`;
      if (p.resting_s) return `<span class="mstatus missing">resting ${p.resting_s} s (${esc(p.last_error)})</span>`;
      const speed = p.latency_ms != null ? ` · ${p.latency_ms} ms a line, ${p.rtf}× real time` : "";
      return `<span class="mstatus ok">&#10003; ${p.ok ? `${p.ok} lines` : "ready"}${speed}</span>`;
    };
    const k = v.kokoro, kjob = (v.jobs || {}).kokoro, lang = v.language, ljob = (v.jobs || {}).voice_lang;
    const piper = this.statusOf("voice");
    const play = (attrs) => `<button class="btn small" ${attrs}>&#9654; Preview</button>`;
    const kokoroState = k.package ? `<span class="mstatus missing">${esc(k.package)}</span>`
      : !k.installed ? `<button class="btn small" id="s-kokoro-get" ${kjob?.state === "running" ? "disabled" : ""}>Download (${k.size_mb} MB)</button>`
      : `${play('data-preview="kokoro"')} ${st.tts.provider === "kokoro" ? state("kokoro") : '<span class="mstatus ok">&#10003; Downloaded</span>'}`;
    return `<p class="sub-h">Voices</p>
      <div class="row"><label>ATC and the copilot speak with<select id="s-provider">
        <option value="piper" ${st.tts.provider !== "kokoro" ? "selected" : ""}>Piper: quick, always there</option>
        <option value="kokoro" ${st.tts.provider === "kokoro" ? "selected" : ""}>Kokoro: more natural, slower (Piper stands in)</option></select></label></div>
      <div class="voice-list">
        <div class="voice-row"><div><b>Piper</b> <span class="muted small">LibriTTS: every controller a voice of their own,
          from one download (80 MB)</span></div>
          <div>${play('id="s-preview"')} ${piper?.installed ? (st.tts.provider === "kokoro" ? '<span class="mstatus ok">&#10003; installed</span>' : state("piper") || '<span class="mstatus ok">&#10003; installed</span>')
            : '<span class="mstatus missing">not downloaded</span> <button class="btn small" data-install="voice">Download</button>'}</div></div>
        <div class="progress" id="p-voice" hidden><div></div></div>
        <div class="voice-row"><div><b>Kokoro</b> <span class="muted small">Kokoro-82M (Apache-2.0): more natural, American or
          British English. About 0.2-0.3 s of work per second of speech, so a long clearance starts a second or two later.</span></div>
          <div>${kokoroState}</div></div>
        <div class="progress busy" id="p-kokoro" hidden><div></div></div>
        ${lang ? `<div class="voice-row"><div><b translate="no">${esc(lang.native)}</b> <span class="muted small">The copilot's voices
          when it talks with you in your language (its voice setting picks between them).</span></div>
          <div>${lang.installed ? lang.voices.map((x) => `<button class="btn small" data-preview-lang="${x.sex}" translate="no">&#9654; ${esc(x.name)}</button>`).join(" ")
            + ' <span class="mstatus ok">&#10003; installed</span>'
            : `<span class="mstatus missing">not downloaded</span> <button class="btn small" data-install="voice_lang" ${ljob?.state === "running" ? "disabled" : ""}>Download (${lang.size_mb} MB)</button>`}</div></div>
        <div class="progress" id="p-voice_lang" hidden><div></div></div>` : ""}
      </div>
      <div class="small voices-status">
        ${v.text_only ? `<div class="mstatus missing">${v.text_only} lines this flight had no voice (text only)</div>` : ""}
        <div class="muted">${st.tts.provider === "kokoro" ? "If Kokoro can't speak a line, Piper does; with neither, it's text." : "If Piper can't speak a line, it's text."}</div>
      </div>`;
  },
  // A yoke or joystick button found by pressing it: LocalTC reads the controllers from Windows (as during a flight,
  // whichever window has focus), so Detect then press it. Written as MSFS names it, joystick:<device>:button:<n>.
  joyPicker(id) {
    return `<div class="joy-pick">
      <button class="btn small" id="joy-detect-${id}">Detect</button>
      <span class="muted small" id="joy-hint-${id}"></span></div>`;
  },
  bindJoy(id, input, key) {
    const button = $(`#joy-detect-${id}`), hint = $(`#joy-hint-${id}`);
    if (!button) return;
    button.onclick = async (e) => {
      e.preventDefault();
      hint.textContent = "Press the button now ...";
      button.disabled = true;
      try {
        const r = await api("joystick/detect", {});
        if (!r.button) {
          const d = await api("joystick").catch(() => ({ devices: [] }));
          hint.textContent = d.devices.length ? "Nothing pressed in 15 s: Detect, then press it." : "No controller connected.";
        } else {
          $(input).value = r.button;
          await this.save("voice", key, r.button);
          hint.textContent = `Got it: button ${r.number + 1} on ${r.device}.`;
        }
      } catch (err) { hint.textContent = ""; fail(err); }
      button.disabled = false;
    };
  },
  bindCloud() {
    const on = (id, ev, fn) => $(id)?.addEventListener(ev, fn);
    on("#s-cloud", "change", (e) => this.save("cloud", "enabled", e.target.checked));
    const ttsRefresh = async (v) => { S.tts = v || await api("tts"); this.render(); };
    on("#s-provider", "change", async () => { if (await this.save("tts", "provider", $("#s-provider").value)) ttsRefresh(); });
    on("#s-kokoro-get", "click", async () => {
      try { await api("models/install", { kinds: ["kokoro"] }); toast("Downloading Kokoro ..."); } catch (e) { fail(e); }
    });
    const preview = async (b, args) => {
      const label = b.innerHTML;
      b.disabled = true; b.textContent = "Speaking ...";
      try { await api("voice/preview", args); } catch (err) { fail(err); }
      finally { b.disabled = false; b.innerHTML = label; }
    };
    $$("[data-preview]").forEach((b) => (b.onclick = () => preview(b, { provider: b.dataset.preview })));
    $$("[data-preview-lang]").forEach((b) => (b.onclick = () => preview(b, { language: S.tts.language.code, sex: b.dataset.previewLang })));
    on("#s-cloud-local", "change", (e) => this.save("cloud", "local_fallback", e.target.checked));
    this.cloudOpen ??= new Set();
    $$("[data-cloud-fold]").forEach((d) => d.addEventListener("toggle", () => {
      this.cloudOpen[d.open ? "add" : "delete"](d.dataset.cloudFold);  // stays as it was when the card is drawn again
    }));
    const refresh = (c) => { S.cloud = c; this.render(); };
    $$("[data-key-save]").forEach((b) => (b.onclick = async () => {
      const id = b.dataset.keySave, key = $(`[data-key="${id}"]`).value.trim();
      if (!key) { toast("Paste the key first", true); return; }
      try { refresh(await api("cloud/key", { provider: id, key })); toast("Key saved"); } catch (e) { fail(e); }
    }));
    $$("[data-key-clear]").forEach((b) => (b.onclick = async () => {
      try { refresh(await api("cloud/key", { provider: b.dataset.keyClear, key: "" })); toast("Key removed"); } catch (e) { fail(e); }
    }));
    $$("[data-cloud-test]").forEach((b) => (b.onclick = async () => {
      const id = b.dataset.cloudTest, out = $(`#cloud-test-${id}`);
      b.disabled = true; out.textContent = "Asking ...";
      try {
        const { results } = await api("cloud/test", { provider: id });
        out.innerHTML = results.length ? results.map((r) => r.ok
          ? `<span class="ok">&#10003; ${esc(r.route)} answered in ${(r.ms / 1000).toFixed(1)} s</span>`
          : `<span class="bad">&#10007; ${esc(r.route)}: ${esc(r.detail)}</span>`).join("<br>") : "Nothing to test: it needs a key.";
      } catch (e) { out.textContent = e.message; }
      b.disabled = false;
    }));
  },
  render() {
    const st = S.settings.settings, m = S.models, cat = m.catalog, hw = m.hardware;
    const opt = (list, cur) => list.map((o) => `<option value="${esc(o.id)}" ${o.id === cur ? "selected" : ""}>${esc(o.label)}${o.tested ? "" : ""} — ${esc(o.speed)}, ${esc(o.quality)} (${o.size_mb >= 1000 ? (o.size_mb / 1000).toFixed(1) + " GB" : o.size_mb + " MB"})</option>`).join("");
    const llmIds = new Set(cat.llm.map((o) => o.id));
    const extra = (m.ollama.installed || []).filter((n) => !llmIds.has(n) && !llmIds.has(n.replace(/:latest$/, "")));
    const llmCur = st.llm.enabled ? st.llm.model : "__off";
    const crewNone = st.crew.llm === "off" || st.crew.mode === "off";  // the copilot without a model
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
        <div class="hw">This computer: ${hw.cpu_cores} CPU threads, ${hw.ram_gb} GB RAM, ${gpu}. Recommended: <b>${esc(cat.profiles.find((p) => p.id === m.recommended)?.label || m.recommended)}</b>.</div>
        <div class="profiles">${cat.profiles.map((p) => `<button class="profile ${profileCur?.id === p.id ? "current" : ""}" data-profile="${p.id}">
          <b>${esc(p.label)}</b>${p.id === m.recommended ? '<span class="rec">recommended</span>' : ""}<div class="small muted">${esc(p.description)}</div></button>`).join("")}</div>
        ${componentsPart(m)}
      </div>

      <div class="card">
        <h3>Models</h3>
        <p class="muted small">Speech recognition and the voices. Bigger models hear better but answer more slowly. All run on
          this computer. The language models are in their own section, below.</p>
        <div class="row"><label>Speech recognition (Whisper)
          <select id="s-whisper"><option value="auto" ${st.voice.model === "auto" ? "selected" : ""}>Automatic (${esc(m.whisper_model)})</option>${opt(cat.whisper, st.voice.model)}</select>
          <span class="hint">${note(cat.whisper, st.voice.model === "auto" ? m.whisper_model : st.voice.model)}</span></label>
          <label style="flex:0 1 120px">Runs on<select id="s-device">${["auto", "cpu", "cuda"].map((d) => `<option ${st.voice.device === d ? "selected" : ""} value="${d}">${{ auto: "Automatic", cpu: "CPU", cuda: "NVIDIA GPU" }[d]}</option>`).join("")}</select></label>
          <div>${status("whisper")}</div></div>
        <div class="progress" id="p-whisper" hidden><div></div></div>
        ${this.voicesPart(st)}
        ${missing ? `<div class="row"><button class="btn primary" id="s-install-all">Download everything missing</button><span class="muted small">Once; flights then work offline.</span></div>` : ""}
        <div class="muted small" id="job-msg"></div>
      </div>
      <div class="card" id="llm-card">
        <h3>Language models</h3>
        <p class="muted small">How ATC and the copilot understand what you say and word what they say back: the model on this
          PC, a <a href="#cloud-card" class="jump">cloud model</a> (recommended for quality, below), and how much each of them
          leans on it.</p>
        <p class="sub-h">The model on this PC</p>
        <div class="row"><label>Language model (reads your calls)
          <select id="s-llm"><option value="__off" ${llmCur === "__off" ? "selected" : ""}>Off: grammar only (fastest, strict phrasing)</option>${opt(cat.llm, llmCur)}
          ${extra.map((n) => `<option value="${esc(n)}" ${n === llmCur ? "selected" : ""}>${esc(n)} (installed in Ollama, untested)</option>`).join("")}</select>
          <span class="hint">${note(cat.llm, st.llm.model)}${m.ollama.running ? "" : " Ollama isn't running."}</span></label>
          <div>${st.llm.enabled ? status("llm") : ""}</div></div>
        <div class="progress" id="p-llm" hidden><div></div></div>
        <label class="check-row"><input type="checkbox" id="s-cpu-only" ${st.llm.cpu_only ? "checked" : ""}> Run it on the CPU only: leaves the graphics card and its memory to the sim. Answers take a little longer (its timeouts are doubled). From the next flight</label>
        <div class="row"><label>After a flight, keep the language model loaded for
          <select id="s-keepalive">${["0", "5m", "30m", "1h", "24h"].map((v) => `<option value="${v}" ${st.llm.keep_alive === v ? "selected" : ""}>${{ "0": "Unload it at once", "5m": "5 minutes", "30m": "30 minutes", "1h": "1 hour", "24h": "All day" }[v]}</option>`).join("")}</select>
          <span class="hint">During a flight it always stays loaded: a model that has to load mid-flight misses the call. ${esc(m.ollama.loaded || "")}</span></label></div>
        <p class="sub-h">ATC</p>
        <div class="row llm-timing"><label>Wait for an answer
          <input id="s-llm-timeout" type="number" min="1" max="60" step="0.5" value="${st.llm.timeout_s}"> s</label>
          <label>Per call, all tries
          <input id="s-llm-budget" type="number" min="1" max="120" step="0.5" value="${st.llm.budget_s}"> s</label>
          <label>Second try when it missed
          <input id="s-llm-patience" type="number" min="0" max="120" step="1" value="${st.llm.patience_s}"> s</label>
          <span class="hint">How long ATC waits for the model before answering without it. Longer is more patient with
            a busy PC (the app shows the controller thinking meanwhile); shorter answers sooner. On the CPU the first two are
            doubled. The app tells you whenever the model runs out of time. From the next flight.</span></div>
        <div class="row"><label>Script or model
          <select id="s-llm-mode">${[
            ["mostly_llm", "Mostly LLM: the model reads and words most calls; the script takes the clearly routine ones (recommended)"],
            ["llm", "Fully LLM: the model reads and words every call (slowest; the script checks it and steps in when it fails)"],
            ["semi", "Semi script/LLM: the script for clear routine calls, the model for the rest and for wording off the script"],
            ["scripted", "Fully scripted: standard phraseology; the model only for a call the script can't read (lightest)"],
          ].map(([v, t]) => `<option value="${v}" ${st.llm.mode === v ? "selected" : ""}>${t}</option>`).join("")}</select>
          <span class="hint">How much ATC leans on the language model, for understanding you and for the words of its replies.
            Whatever the model says is checked first: a clearance keeps every number and instruction the script decided, and
            anything that fails the checks is said in the script's words.</span></label></div>
        <label class="check-row"><input type="checkbox" id="s-beyond-facts" ${st.llm.beyond_facts ? "checked" : ""}> Let the model answer beyond the sim's data: its answers may use what it knows itself (a runway length, an airport's layout, what's usual), not only what the sim tells it. They can be wrong. It still never gives an instruction, and a clearance is still checked against the script</label>

        <p class="sub-h">Copilot</p>
        <div class="row"><label>Its language model
          <select id="s-crew-which">
            <option value="cloud" ${!crewNone && st.cloud?.copilot !== false ? "selected" : ""}>The cloud model, when it's on (its own connection, never holding up ATC; the whole flight as context)</option>
            <option value="local" ${!crewNone && st.cloud?.copilot === false ? "selected" : ""}>The model on this PC</option>
            <option value="none" ${crewNone ? "selected" : ""}>None: the listed commands and common questions only</option></select>
          <span class="hint">${crewNone ? "" : st.cloud?.enabled ? "The cloud language model is on." : 'The cloud language model is off (<a href="#cloud-card" class="jump">Cloud language model</a>): the copilot uses the model on this PC either way.'}</span></label></div>
        <div ${crewNone ? "hidden" : ""}>
        <div class="row"><label>How much it leans on it<select id="s-crew-mode">${[
            ["auto", "Automatic: mostly LLM with the cloud model, fully scripted with the model on this PC (recommended)"],
            ["llm", "Fully LLM: the model reads everything you say and words everything it says but the safety calls"],
            ["mostly_llm", "Mostly LLM: short clear commands straight away; the rest, and its own calls, the model's"],
            ["semi", "Semi script/LLM: the listed commands from the script; everything else you say to the model"],
            ["scripted", "Fully scripted: the listed commands and common questions; the model only when nothing else understood"],
          ].map(([v, t]) => `<option value="${v}" ${(st.crew.mode && st.crew.mode !== "off" ? st.crew.mode : "auto") === v ? "selected" : ""}>${t}</option>`).join("")}</select>
          <span class="hint">As ATC's setting does. A command it reads from your own words that the script didn't read the
            same is said back for "confirm" first; checklists, callouts and safety calls never wait on the model.</span></label></div>
        <label class="check-row"><input type="checkbox" id="s-crew-beyond" ${st.crew.beyond_facts ? "checked" : ""}> Let it answer beyond what it knows from the sim and ATC: its answers may use what a first officer would know (how a system works, what's usual). They can be wrong. Commands are still read back and checked</label>
        <div class="row llm-timing"><label>Wait for an answer
          <input id="s-crew-timeout" type="number" min="1" max="60" step="0.5" value="${st.crew.timeout_s}"> s</label>
          <label>At most, on a busy PC
          <input id="s-crew-patience" type="number" min="1" max="120" step="1" value="${st.crew.patience_s}"> s</label>
          <span class="hint">How long the copilot waits for the model before "say again?". On the CPU only the first is doubled;
            with the cloud, at least the cloud's own wait. From the next flight.</span></div>
        </div>
      </div>
      ${this.cloudCard(st)}

      <div class="card">
        <h3>Push-to-talk</h3>
        <label class="check-row"><input type="checkbox" id="s-voice-on" ${st.voice.enabled ? "checked" : ""}> Talk to ATC with a microphone</label>
        <div class="row">
          <label>Push-to-talk switch<select id="s-ptt">
            <option value="keyboard" ${st.voice.ptt === "keyboard" ? "selected" : ""}>A keyboard key (works while the sim has focus)</option>
            <option value="joystick" ${st.voice.ptt === "joystick" ? "selected" : ""}>A yoke or joystick button (works while the sim has focus)</option>
            <option value="enter" ${st.voice.ptt === "enter" ? "selected" : ""}>Only the headset button in this window</option></select></label>
        </div>
        <div class="row" id="ptt-key-row" ${st.voice.ptt === "keyboard" ? "" : "hidden"}>
          <span>Key</span><span class="keycap" id="s-key">${esc(keyLabel(st.voice.ptt_key))}</span>
          <button class="btn small" id="s-key-set">Change</button><span class="muted small" id="s-key-hint"></span>
        </div>
        <div class="row" id="ptt-joy-row" ${st.voice.ptt === "joystick" ? "" : "hidden"}>
          <label>Button<input id="s-joy" value="${esc(st.voice.ptt_joystick)}" placeholder="joystick:0:button:3"></label>
          ${this.joyPicker("ptt")}
        </div>
        <div class="row"><label>Microphone<select id="s-mic">${devOpts(S.devices.inputs || [], st.voice.input_device)}</select></label></div>
      </div>

      <div class="card">
        <h3>ATC voice</h3>
        <div class="row"><label>Speakers or headset<select id="s-out">${devOpts(S.devices.outputs || [], st.tts.output_device)}</select></label></div>
        <div class="row">
          <div><button type="button" class="btn small" id="s-open-mixer" title="The volume of ATC, the ATIS, other flights, the copilot and the radio's clicks, and your microphone">Mixer &hellip;</button></div>
          <label>Speed <input type="range" id="s-rate" min="0.8" max="1.5" step="0.05" value="${st.tts.rate}"></label>
          <label>Static <input type="range" id="s-static" min="0" max="1" step="0.05" value="${st.tts.static}"></label>
        </div>
        <label class="check-row"><input type="checkbox" id="s-effect" ${st.tts.radio_effect ? "checked" : ""}> Radio effect (band-pass, compression, squelch)</label>
        <label class="check-row"><input type="checkbox" id="s-atis" ${st.tts.atis ? "checked" : ""}> Read the ATIS aloud while it's tuned</label>
        <label class="check-row"><input type="checkbox" id="s-cpvoice" ${st.tts.copilot ? "checked" : ""}> Speak the copilot's calls too</label>
        <label class="check-row"><input type="checkbox" id="s-replay" ${st.ui.replay_audio ? "checked" : ""}> Play buttons on the radio log: ATC and the copilot as you heard them, you as the microphone took it (here, on the phone and on the website's Flight Tracker; the last 80, in memory only)</label>
      </div>

      <div class="card">
        <h3>Copilot</h3>
        <div class="row"><label>When the Copilot switch is on, it
          <select id="s-copilot"><option value="full" ${st.ui.copilot === "full" ? "selected" : ""}>works the whole radio: requests, check-ins, readbacks</option>
          <option value="assist" ${st.ui.copilot === "assist" ? "selected" : ""}>reads back and changes frequencies; you make the calls</option></select></label></div>
        <div class="master-row">
          <div><b>Intercom copilot</b><div class="muted small">Talk to the copilot on a key of its own. It works the aircraft for you
            ("flaps two", "gear down", "set heading 270", "squawk 4521"), makes the callouts, runs checklists and answers
            questions. Off: none of it, and only the radio copilot above.</div></div>
          <label class="toggle" title="Intercom copilot on or off"><input type="checkbox" id="s-crew" ${st.crew.enabled ? "checked" : ""}><span></span></label>
        </div>
        <div id="crew-opts" class="${st.crew.enabled ? "" : "off"}">
        <div class="row">
          <span>Intercom key</span><span class="keycap" id="s-ic-key">${esc(keyLabel(st.voice.intercom_key))}</span>
          <button class="btn small" id="s-ic-key-set">Change</button><span class="muted small" id="s-ic-key-hint"></span>
        </div>
        <div class="row"><label>Or a yoke or joystick button<input id="s-ic-joy" value="${esc(st.voice.intercom_joystick)}" placeholder="joystick:0:button:4"></label>
          ${this.joyPicker("ic")}</div>
        ${personalityRow(S.settings.personalities, st.crew.personality)}
        <div class="row" id="crew-voice-row" ${(st.crew.personality || "neutral") === "neutral" ? "" : "hidden"}>
          <label>Copilot's voice<select id="s-crew-sex">${["female", "male", "any"].map((s) => `<option value="${s}" ${st.crew.voice_sex === s ? "selected" : ""}>${{ female: "Female", male: "Male", any: "Either" }[s]}</option>`).join("")}</select></label>
          <label style="flex:0 1 120px">Voice<select id="s-crew-pick">${[0, 1, 2, 3, 4, 5, 6, 7].map((i) => `<option value="${i}" ${st.crew.voice_pick === i ? "selected" : ""}>${i + 1}</option>`).join("")}</select></label>
        </div>
        <span class="hint">The same voice reads back on the radio. Needs the multi-speaker ATC voice (LibriTTS). A personality changes only the routine and conversational lines: the safety calls (stall, overspeed, wind shear, minimums, "not cleared to land") keep their standard words for every one.</span>
        <div class="row"><label>What it says by itself<select id="s-crew-verbosity">
          <option value="quiet" ${st.crew.verbosity === "quiet" ? "selected" : ""}>Quiet: only what's safety (config, gear, not cleared to land, speed)</option>
          <option value="standard" ${st.crew.verbosity === "standard" ? "selected" : ""}>Standard: callouts, ATC relays and reminders</option>
          <option value="chatty" ${st.crew.verbosity === "chatty" ? "selected" : ""}>Chatty: also status updates and small talk (never below 10,000 ft)</option></select></label></div>
        <label class="check-row"><input type="checkbox" id="s-crew-hands" ${st.crew.hands === "pm" ? "checked" : ""}> Let it work the aircraft's controls: its own side by itself (the radios in standby, the transponder, the altimeter, lights, gear and flaps after takeoff, the altitude and heading ATC gives). Off: it says what's due and does only what you ask it to</label>
        <label class="check-row"><input type="checkbox" id="s-crew-repeat" ${st.crew.repeat_atc ? "checked" : ""}> Chatty only: it says the key part of each ATC instruction back to you before your readback ("Descend and maintain 8,000"), unless you read it back first</label>
        <span class="hint">Your side (parking brake, engines, the autopilot, flaps for takeoff and landing) it never touches: it tells you when something's missed. Say "quiet please" or "keep me posted" to change how much it talks mid-flight.</span>

        </div>
      </div>

      <div class="card">
        <h3>ATC</h3>
        <label class="check-row"><input type="checkbox" id="s-unscripted" ${st.atc.unscripted ? "checked" : ""}> Unscripted moments: traffic calls, altitude checks, "how do you read"</label>
        <label class="check-row"><input type="checkbox" id="s-strict" ${st.atc.strict_callsign ? "checked" : ""}> Readbacks must include the callsign</label>
        <label class="check-row"><input type="checkbox" id="s-fpln-rwy" ${st.atc.enforce_fpln_runways ? "checked" : ""}> Enforce FPLN runway assignments: ATC gives the flight plan's departure and arrival runways. Off: the runways in use, from the ATIS</label>
        <label class="check-row"><input type="checkbox" id="s-real-gates" ${st.atc.real_gates !== false ? "checked" : ""}> Real gate names: gates as the airport numbers them (E9, not the scenery's 88), and international flights to international gates. Downloaded from OpenStreetMap and kept; off: the scenery's names</label>
        <div class="row"><label>ATIS source<select id="s-atis-source">${[
            ["hybrid", "Real world: the airport's real ATIS, else LocalTC's from its real METAR, the simulator's only where there's neither (recommended)"],
            ["sim", "Simulator only: LocalTC's ATIS from the sim's weather, nothing downloaded"],
          ].map(([v, t]) => `<option value="${v}" ${({real: "hybrid"}[st.atc.atis_source] || st.atc.atis_source || "hybrid") === v ? "selected" : ""}>${t}</option>`).join("")}</select>
          <span class="hint">The real ATIS is the FAA's digital ATIS (atis.info) in the US; elsewhere it's the ATIS of a VATSIM
            controller on at the airport, when there is one (its runways and approaches; the weather is the real METAR's).
            The METAR is aviationweather.gov's; only the airport codes are sent. Once you're at or near an airport, the weather the sim has there wins over any real-world
            report, so ATC never gives you conditions you aren't flying in; the ATIS says when the two differ. Each ATIS shows
            where it's from and how old it is. Takes effect on the next flight.</span></label></div>
        <label class="check-row"><input type="checkbox" id="s-range" ${st.atc.radio_range ? "checked" : ""}> Radio range: an airport's frequencies work only near it (tower 20-60 nm, ground a few miles)</label>
        <label class="check-row"><input type="checkbox" id="s-personalities" ${st.atc.personalities !== false ? "checked" : ""}> Controllers with personalities: each station has its own controller (calm, formal, friendly, strict, hurried, dry or conversational), the same through a flight (a new shift after a break of 5 hours or more), in their greetings, acknowledgements, corrections, pace, and the language model's wording. Never in the instructions themselves</label>
        <label class="check-row"><input type="checkbox" id="s-humanize" ${st.atc.humanize !== false ? "checked" : ""}> Natural speech: controllers and the other pilots now and then say "uh" as people do (more from a conversational controller, hardly any from a strict one or on a busy frequency). Only in what's said, never inside a number or in anything urgent</label>
        <label class="check-row"><input type="checkbox" id="s-callsign-check" ${st.atc.callsign_check ? "checked" : ""}> Callsign check: another flight's callsign gets "say again your callsign"</label>
        <label class="check-row"><input type="checkbox" id="s-taxi-ribbon" ${st.atc.taxi_guide ? "checked" : ""}> Taxi guidance: green arrows on the ground along the route ground gave you, a red stop bar short of a runway you aren't cleared onto or across. LocalTC puts its arrows in the sim's Community folder when you turn this on; they show from the sim's next start</label>
        <label class="check-row"><input type="checkbox" id="s-auto-stop" ${st.session.auto_stop_at_gate ? "checked" : ""}> Stop the flight at the gate: once parked at a gate or stand at the destination (stopped, taxi done), the flight ends as if you pressed Stop</label>
        <div class="row"><label>Phraseology
          <select id="s-phraseology">${[["auto", "By region: FAA in the US and Canada, ICAO elsewhere"], ["faa", "FAA everywhere"], ["icao", "ICAO everywhere"]]
            .map(([v, t]) => `<option value="${v}" ${st.atc.phraseology === v ? "selected" : ""}>${t}</option>`).join("")}</select>
          <span class="hint">ICAO says "QNH 1013", "decimal", "taxi to holding point", "climb to FL120"; FAA says "altimeter 29.92", "point", "hold short", "climb and maintain".</span></label></div>
        <div class="row"><label>Center name (where no airport lists one)<input id="s-center" value="${esc(st.atc.center_name)}"></label>
          <label>Center frequency<input id="s-center-mhz" type="number" step="0.005" min="118" max="137" value="${st.atc.center_mhz}"></label></div>
      </div>

      <div class="card">
        <h3>Traffic</h3>
        <div class="master-row"><div><b>LocalTC's traffic</b> <span class="exp-badge">EXPERIMENTAL</span>
          <div class="muted small">On: the real flights around you (live from free ADS-B sources) fly in the sim with their own
            airline's FSLTL model, the gates fill with the airlines that fly there, as busy as the airport is at that hour, and
            ATC controls them: sent around or held short when you have the runway, stopped to give way to you on the ground,
            and the gate you're given kept clear. <b>Turn MSFS's own air traffic and parked aircraft off</b>
            (Options &gt; General &gt; Traffic) or both are there. Off: MSFS's traffic as it is.
            <span id="traffic-status">${trafficStatus(S.state.traffic_control)}</span></div></div>
          <label class="toggle" title="LocalTC's traffic on or off"><input type="checkbox" id="s-traffic" ${st.traffic?.enabled ? "checked" : ""}><span></span></label>
        </div>
        <div id="traffic-opts" class="${st.traffic?.enabled ? "" : "off"}">
          <label class="check-row"><input type="checkbox" id="s-traffic-scheduled" ${st.traffic?.scheduled !== false ? "checked" : ""}> Scheduled departures at the gates: the airport's departures due in the next 45 minutes parked at gates their wings fit, handed to the real flight when its transponder comes on (a free OpenSky or AirLabs account: EFB → Data; without one, only the real aircraft at the gates are shown)</label>
          <label class="check-row"><input type="checkbox" id="s-traffic-gates" ${st.traffic?.fill_gates ? "checked" : ""}> Parked airliners at the gates too: the gates the departures don't take filled with the airport's airlines (off: the remote stands and ramps only)</label>
          <label class="check-row"><input type="checkbox" id="s-traffic-atc" ${st.traffic?.atc_control !== false ? "checked" : ""}> ATC controls them: sent around or held short when you have the runway, stopped to give way to you on the ground</label>
          <div class="row"><label>Parked aircraft on the ramps<select id="s-traffic-parked">${[[-1, "Realistic: as busy as the airport is at that hour (up to 60)"], [0, "None"], [10, "A few (10)"], [20, "Some (20)"], [40, "Busy (40)"], [60, "Packed (60)"]].map(([n, label]) => {
            const t = st.traffic || {};
            const cur = t.parked === false ? 0 : (t.parked_fill || "realistic") === "realistic" ? -1 : (t.max_parked ?? 60);
            return `<option value="${n}" ${n === cur ? "selected" : ""}>${label}</option>`;
          }).join("")}</select></label><span class="hint">Realistic goes by the airport's size, the local time there (busy
            mornings and evenings, a quiet night) and how busy the live traffic shows it today. Each one is a model the sim
            draws: fewer if the frame rate suffers. From the next flight.</span></div>
        </div>
        <label class="check-row"><input type="checkbox" id="s-traffic-rwy" ${st.atc.traffic_runways !== false ? "checked" : ""}> Runways in use follow the sim's traffic: the way its AI aircraft take off and land, when the wind allows (fewer head-on finals and go-arounds). Off: by the wind alone</label>
        <label class="check-row"><input type="checkbox" id="s-chatter" ${st.atc.chatter ? "checked" : ""}> Other traffic on the frequency: other flights cleared and reading back now and then</label>
      </div>

      <div class="savebar"><span class="muted small" title="${esc(S.settings.path)}">Changes save as you make them.</span><span class="small green" id="saved-note"></span>
        <span id="restart-bar" hidden><button class="btn small primary" id="s-restart">Restart flight to apply</button></span></div>`;
    // Everything that isn't about flying: in Settings (the gear), not Quick Settings.
    $("#settings-more").innerHTML = `
      <div class="card" id="s-language-card">
        <h3>Language</h3>
        ${Language.picker("s")}
      </div>

      <div class="card">
        <h3>Third party</h3>
        <p class="muted small">Accounts LocalTC reads flight plans from. Nothing is sent anywhere else.</p>
        <div class="row"><label>SimBrief username or Pilot ID<input id="s-simbrief" value="${esc(st.ui.simbrief_user)}"
          placeholder="e.g. 1207576" autocomplete="off" spellcheck="false"></label>
          <div><button class="btn small" id="s-simbrief-fetch">Import latest plan</button></div></div>
        <span class="hint">Your Pilot ID is on the SimBrief Account Settings page. <b>New Flight</b> uses this too.</span>
        <label class="check-row"><input type="checkbox" id="s-require-plan" ${st.ui.require_plan !== false ? "checked" : ""}> <span>Require a
          flight plan to start a flight (<b>New Flight</b>: the plan, then the flight). Off: <b>Start</b> flies without one, and
          <b>File Plan</b> gives ATC your plan whenever you have it, the flight going on.</span></label>
      </div>

      <div class="card">
        <h3>Appearance</h3>
        <div class="row theme-row">${THEMES.map(([v, label]) => `<button type="button" class="theme-pick${(st.ui.theme || "radio") === v ? " on" : ""}" data-theme-pick="${v}" title="${label}">
          <span class="swatch sw-${v}"><i></i><i></i><i></i></span><span>${label}</span></button>`).join("")}</div>
      </div>

      <div class="card">
        <h3>Sim</h3>
        <div class="row"><label>Connect to<select id="s-source"><option value="live" ${st.ui.source === "live" ? "selected" : ""}>MSFS 2024 (live)</option>
          <option value="replay" ${st.ui.source === "replay" ? "selected" : ""}>A recorded flight (replay, for development)</option></select></label></div>
        <div class="row" ${st.ui.source === "replay" ? "" : "hidden"}><label>Recording<input id="s-replay" value="${esc(st.replay.path)}"></label>
          <label style="flex:0 1 100px">Speed<input id="s-replay-speed" type="number" step="0.5" min="0" value="${st.replay.speed}"></label></div>
        <label class="check-row" ${st.ui.source === "replay" ? "hidden" : ""}><input type="checkbox" id="s-sc-builtin" ${(st.live.connection || "builtin") === "builtin" ? "checked" : ""}> Built-in SimConnect: nothing to install</label>
        <span class="hint" ${st.ui.source === "replay" ? "hidden" : ""}>Off: through the MSFS SDK's SimConnect.dll instead (installed with the SDK). From the next flight.</span>
        <label class="check-row"><input type="checkbox" id="s-tiles" ${st.ui.map_tiles ? "checked" : ""}> Map background from OpenStreetMap (needs the internet)</label>
        <div class="row"><label>Keep LocalTC above other windows<select id="s-on-top">${[["off", "Never"], ["flying", "While flying (above the sim)"], ["always", "Always"]]
          .map(([v, label]) => `<option value="${v}" ${st.ui.on_top === v ? "selected" : ""}>${label}</option>`).join("")}</select></label></div>
        <span class="hint">Above the sim when it runs in a window or borderless full screen; exclusive full screen always covers it.</span>
      </div>

      <div class="card" id="s-account-card"></div>

      <div class="card" id="s-update-card"></div>

      <div class="card" id="s-support-card">
        <h3>Support &amp; feedback</h3>
        <div class="row"><a class="btn small discord-btn" href="https://discord.gg/6vbD2T9zHE" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M19.3 5.4A16.6 16.6 0 0 0 15.2 4l-.5 1a15.4 15.4 0 0 0-5.4 0l-.5-1a16.6 16.6 0 0 0-4.1 1.4C2.1 9.3 1.4 13.1 1.7 16.8a16.7 16.7 0 0 0 5 2.6l1.1-1.7a10.8 10.8 0 0 1-1.7-.8l.4-.3a11.9 11.9 0 0 0 11 0l.4.3c-.5.3-1.1.6-1.7.8l1.1 1.7a16.6 16.6 0 0 0 5-2.6c.4-4.3-.7-8-3-11.4ZM8.5 14.5c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2Zm7 0c-1 0-1.8-.9-1.8-2s.8-2 1.8-2 1.8.9 1.8 2-.8 2-1.8 2Z"/></svg> Join the Discord</a>
          <span class="muted small">Ask other pilots, share flights, hear about new versions first.</span></div>
        <p class="muted small" id="sup-who">Sent to support@localtc.tech. Needs the account (above): the answer goes to its address.</p>
        <div class="row">
          <label>About <select id="sup-kind"><option value="feedback">Feedback or an idea</option><option value="bug">Something went wrong</option>
            <option value="support">Help setting it up</option></select></label>
        </div>
        <textarea id="sup-msg" rows="4" maxlength="8000" placeholder="What happened (the airport, what ATC said), or what would make LocalTC better?"></textarea>
        <label class="sup-files">Attach anything: a session export, logs, screenshots (up to 5 files, 25 MB) <input type="file" id="sup-files" multiple></label>
        <div class="row" style="margin-top:8px"><button class="btn small primary" id="sup-send">Send</button><span class="small" id="sup-note"></span></div>
        <p class="muted small">For a problem in a flight, turn on Developer mode below and fly it again: Export session makes a zip that shows exactly what happened. Attach it here.</p>
      </div>

      <div class="card">
        <h3>Developer mode</h3>
        <label class="check-row"><input type="checkbox" id="s-dev" ${st.ui.dev_mode ? "checked" : ""}> Record every flight with its audio, and show Mark and Export session</label>
        <p class="muted small">A session export is a zip of a flight's recording, the log files, your settings and the flight plan: everything needed to replay the flight exactly and see why ATC did what it did.</p>
        <div class="row"><button class="btn small" data-open="recordings">Open recordings</button><button class="btn small" data-open="logs">Open logs</button>
          <button class="btn small" data-open="data">Open data folder</button></div>
        <div id="s-sessions"></div>
        ${st.ui.dev_mode ? `<div class="row"><button class="btn small" id="s-setup-again">Show the setup guide</button>
          <span class="muted small">As a new install opens with it. Your settings are what it shows, and change only as you change them in it.</span></div>` : ""}
      </div>
      <div class="savebar"><span class="muted small">Changes save as you make them.</span></div>`;
    this.wire();
    this.jobs(S.state.jobs || {});
    this.update(S.state.update);
    this.supportCard();
    api("account").then((a) => { S.account = a; this.account(); }).catch(() => {});
    if (st.ui.dev_mode) this.sessions();
    $("#sup-send").onclick = async () => {
      const note = $("#sup-note"), button = $("#sup-send");
      const body = { kind: $("#sup-kind").value, message: $("#sup-msg").value.trim() };
      if (!body.message) { note.className = "small error"; note.textContent = "Write a message first."; return; }
      button.disabled = true;
      try {
        // The files picked, as the server takes them: [{name, data (base64)}], 25 MB together.
        const files = [...$("#sup-files").files];
        if (files.length > 5) throw new Error("Attach up to 5 files.");
        if (files.reduce((t, f) => t + f.size, 0) > 25 * 1024 * 1024) throw new Error("The files are too big together: up to 25 MB.");
        body.attachments = await Promise.all(files.map((f) => new Promise((ok, fail) => {
          const r = new FileReader();
          r.onload = () => ok({ name: f.name, data: String(r.result).split(",", 2)[1] || "" });
          r.onerror = () => fail(new Error(`Couldn't read ${f.name}.`));
          r.readAsDataURL(f);
        })));
        if (files.length) { note.className = "small"; note.textContent = "Sending ..."; }
        const r = await api("support", body);
        note.className = "small green"; note.textContent = r.message;
        $("#sup-msg").value = "";
        $("#sup-files").value = "";
      } catch (e) { note.className = "small error"; note.textContent = e.message; }
      finally { button.disabled = false; }
    };
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
    on("#s-preview", "click", async (e) => {
      const b = e.target; b.disabled = true; b.textContent = "Speaking ...";
      try { await api("voice/preview", {}); this.refreshModels(); } catch (err) { fail(err); }
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
    on("#s-ic-key-set", "click", () => this.captureKey("intercom_key", "#s-ic-key", "#s-ic-key-hint"));
    on("#s-ic-joy", "change", () => this.save("voice", "intercom_joystick", val("#s-ic-joy").trim()));
    this.bindJoy("ptt", "#s-joy", "ptt_joystick");
    this.bindJoy("ic", "#s-ic-joy", "intercom_joystick");
    on("#s-crew", "change", (e) => { this.save("crew", "enabled", e.target.checked); $("#crew-opts")?.classList.toggle("off", !e.target.checked); });
    on("#s-crew-which", "change", async () => {
      const v = val("#s-crew-which"), crew = S.settings.settings.crew;
      if (v === "none") await this.save("crew", "llm", "off");
      else {
        if (crew.llm === "off") await this.save("crew", "llm", "full");
        if (crew.mode === "off") await this.save("crew", "mode", "auto");
        await this.save("cloud", "copilot", v === "cloud");
      }
      this.render();
    });
    on("#s-crew-beyond", "change", (e) => this.save("crew", "beyond_facts", e.target.checked));
    for (const [id, key, low, high] of [["#s-crew-timeout", "timeout_s", 1, 60], ["#s-crew-patience", "patience_s", 1, 120]])
      on(id, "change", () => {
        const value = Number(val(id));
        if (!Number.isFinite(value) || value < low || value > high) { toast(`Between ${low} and ${high} seconds`, true); return; }
        this.save("crew", key, value);
      });
    on("#s-crew-sex", "change", () => this.save("crew", "voice_sex", val("#s-crew-sex")));
    on("#s-crew-mode", "change", async () => {
      const v = val("#s-crew-mode");
      this.save("crew", "mode", v);
    });
    on("#s-crew-verbosity", "change", () => this.save("crew", "verbosity", val("#s-crew-verbosity")));
    on("#s-crew-hands", "change", (e) => this.save("crew", "hands", e.target.checked ? "pm" : "calls"));
    on("#s-crew-pick", "change", () => this.save("crew", "voice_pick", Number(val("#s-crew-pick"))));
    on("#s-crew-preview", "click", async (e) => {
      e.target.disabled = true;
      try { await api("voice/crew_preview", { personality: val("#s-crew-personality"), sex: val("#s-crew-sex"), pick: Number(val("#s-crew-pick")) }); } catch (err) { fail(err); }
      e.target.disabled = false;
    });
    on("#s-crew-personality", "change", () => {
      const id = val("#s-crew-personality");
      $("#crew-voice-row").hidden = id !== "neutral";
      $("#s-crew-persona-hint").innerHTML = personalityHint(S.settings.personalities, id);
      this.save("crew", "personality", id);
    });
    on("#s-mic", "change", () => this.save("voice", "input_device", val("#s-mic")));
    on("#s-out", "change", () => this.save("tts", "output_device", val("#s-out")));
    on("#s-open-mixer", "click", () => Mixer.open());
    for (const [id, key] of [["#s-rate", "rate"], ["#s-static", "static"]])
      on(id, "change", () => this.save("tts", key, Number(val(id))));
    on("#s-effect", "change", (e) => this.save("tts", "radio_effect", e.target.checked));
    on("#s-atis", "change", (e) => this.save("tts", "atis", e.target.checked));
    on("#s-cpvoice", "change", (e) => this.save("tts", "copilot", e.target.checked));
    on("#s-replay", "change", (e) => this.save("ui", "replay_audio", e.target.checked));
    on("#s-crew-repeat", "change", (e) => this.save("crew", "repeat_atc", e.target.checked));
    on("#s-copilot", "change", () => api("radio/copilot", { mode: val("#s-copilot") }).then(() => this.save("ui", "copilot", val("#s-copilot"))).catch(fail));
    on("#s-unscripted", "change", (e) => this.save("atc", "unscripted", e.target.checked));
    on("#s-strict", "change", (e) => this.save("atc", "strict_callsign", e.target.checked));
    on("#s-chatter", "change", (e) => this.save("atc", "chatter", e.target.checked));
    on("#s-fpln-rwy", "change", (e) => this.save("atc", "enforce_fpln_runways", e.target.checked));
    on("#s-real-gates", "change", (e) => this.save("atc", "real_gates", e.target.checked));
    on("#s-traffic-rwy", "change", (e) => this.save("atc", "traffic_runways", e.target.checked));
    on("#s-range", "change", (e) => this.save("atc", "radio_range", e.target.checked));
    on("#s-callsign-check", "change", (e) => this.save("atc", "callsign_check", e.target.checked));
    on("#s-personalities", "change", (e) => this.save("atc", "personalities", e.target.checked));
    on("#s-humanize", "change", (e) => this.save("atc", "humanize", e.target.checked));
    on("#s-traffic", "change", (e) => { this.save("traffic", "enabled", e.target.checked); $("#traffic-opts")?.classList.toggle("off", !e.target.checked); });
    on("#s-traffic-scheduled", "change", (e) => this.save("traffic", "scheduled", e.target.checked));
    on("#s-traffic-atc", "change", (e) => this.save("traffic", "atc_control", e.target.checked));
    on("#s-traffic-gates", "change", (e) => this.save("traffic", "fill_gates", e.target.checked));
    on("#s-traffic-parked", "change", (e) => {
      const n = Number(e.target.value);
      this.save("traffic", "parked", n !== 0)
        .then(() => (n !== 0 ? this.save("traffic", "parked_fill", n < 0 ? "realistic" : "fixed") : null))
        .then(() => (n !== 0 ? this.save("traffic", "max_parked", n < 0 ? 60 : n) : null));
    });
    on("#s-atis-source", "change", () => this.save("atc", "atis_source", val("#s-atis-source")));
    on("#s-phraseology", "change", () => this.save("atc", "phraseology", val("#s-phraseology")));
    on("#s-center", "change", () => this.save("atc", "center_name", val("#s-center").trim()));
    on("#s-center-mhz", "change", () => this.save("atc", "center_mhz", Number(val("#s-center-mhz"))));
    on("#s-llm-mode", "change", () => this.save("llm", "mode", val("#s-llm-mode")));
    on("#s-auto-stop", "change", (e) => this.save("session", "auto_stop_at_gate", e.target.checked));
    on("#s-taxi-ribbon", "change", (e) => this.save("atc", "taxi_guide", e.target.checked));
    on("#s-beyond-facts", "change", (e) => this.save("llm", "beyond_facts", e.target.checked));
    this.bindCloud();
    on("#s-keepalive", "change", () => this.save("llm", "keep_alive", val("#s-keepalive")));
    on("#s-cpu-only", "change", () => this.save("llm", "cpu_only", $("#s-cpu-only").checked));
    for (const [id, key, low, high] of [["#s-llm-timeout", "timeout_s", 1, 60], ["#s-llm-budget", "budget_s", 1, 120],
                                         ["#s-llm-patience", "patience_s", 0, 120]])
      on(id, "change", () => {
        const value = Number(val(id));
        if (!Number.isFinite(value) || value < low || value > high) { toast(`Between ${low} and ${high} seconds`, true); return; }
        this.save("llm", key, value);
      });
    Language.wire("s", () => this.render());
    on("#s-simbrief", "change", () => this.save("ui", "simbrief_user", val("#s-simbrief").trim()));
    on("#s-require-plan", "change", (e) => requirePlan(e.target.checked));
    on("#s-simbrief-fetch", "click", async (e) => {
      const user = val("#s-simbrief").trim();
      if (!user) { toast("Enter your SimBrief username or Pilot ID first", true); return; }
      const b = e.target; b.disabled = true; b.textContent = "Fetching ...";
      try {
        const r = await api("flight/simbrief", { user });
        const saved = await api("flight/plan", { plan: r.plan });
        toast(`Plan imported: ${saved.summary}`);
      } catch (err) { fail(err); } finally { b.disabled = false; b.textContent = "Import latest plan"; }
    });
    on("#s-source", "change", async () => { await this.save("ui", "source", val("#s-source")); this.render(); });
    on("#s-on-top", "change", () => this.save("ui", "on_top", val("#s-on-top")));
    $$("[data-theme-pick]").forEach((b) => (b.onclick = async () => {
      applyTheme(b.dataset.themePick);
      $$("[data-theme-pick]").forEach((x) => x.classList.toggle("on", x === b));
      await this.save("ui", "theme", b.dataset.themePick);
    }));
    on("#s-replay", "change", () => this.save("replay", "path", val("#s-replay").trim()));
    on("#s-replay-speed", "change", () => this.save("replay", "speed", Number(val("#s-replay-speed"))));
    on("#s-sc-builtin", "change", (e) => this.save("live", "connection", e.target.checked ? "builtin" : "dll"));
    on("#s-tiles", "change", async (e) => { await this.save("ui", "map_tiles", e.target.checked); S.state.map_tiles = e.target.checked; MapView.tiles(); });
    on("#s-dev", "change", async (e) => { await this.save("ui", "dev_mode", e.target.checked); this.render(); });
    on("#s-setup-again", "click", () => { $("#dlg-settings").close(); Setup.open(); });
    $$("[data-open]").forEach((b) => (b.onclick = () => api("open", { what: b.dataset.open }).catch(fail)));
    on("#s-restart", "click", async () => {
      try { await api("flight/stop", {}); await api("flight/start", {}); $("#restart-bar").hidden = true; showTab("atc"); } catch (e) { fail(e); }
    });
  },
  update(u) {
    const card = $("#s-update-card");
    if (!card || !u) return;
    const st = S.settings.settings.ui;
    const r = u.release;
    const busy = u.state === "checking" || u.state === "downloading";
    const action = u.state === "available" && u.can_apply ? `<button class="btn small primary" id="u-download">Download ${esc(r.version)}</button>`
      : u.state === "ready" && u.can_apply ? `<button class="btn small primary" id="u-install">Install and restart</button>` : "";
    card.innerHTML = `
      <h3>Updates</h3>
      <div class="row"><span>LocalTC <b>${esc(u.current)}</b></span>
        <button class="btn small" id="u-check" ${busy ? "disabled" : ""}>${u.state === "checking" ? "Checking ..." : "Check now"}</button> ${action}</div>
      ${u.message ? `<p class="small ${u.state === "failed" ? "error" : "muted"}">${esc(u.message)}</p>` : ""}
      ${r && !u.can_apply && u.why_not ? `<p class="muted small">${esc(u.why_not)}</p>` : ""}
      ${r ? `<details class="small"><summary>What's new in ${esc(r.version)}</summary><pre class="notes">${esc(r.notes || "No notes.")}</pre>
        ${r.page ? `<a href="${esc(r.page)}" target="_blank" rel="noopener">The release on GitHub</a>` : ""}</details>` : ""}
      <div class="row"><label>Look for new versions
        <select id="u-mode"><option value="notify" ${st.updates === "notify" ? "selected" : ""}>Tell me, and install when I say (recommended)</option>
        <option value="auto" ${st.updates === "auto" ? "selected" : ""}>Download by itself, install when LocalTC closes</option>
        <option value="off" ${st.updates === "off" ? "selected" : ""}>Never (LocalTC won't contact GitHub)</option></select></label></div>
      <span class="hint">Checks GitHub once a day at most, and never installs during a flight.</span>`;
    $("#u-check").onclick = () => api("update/check", {}).then((v) => this.update(v)).catch(fail);
    if ($("#u-download")) $("#u-download").onclick = () => api("update/download", {}).catch(fail);
    if ($("#u-install")) $("#u-install").onclick = () => api("update/install", {}).catch(fail);
    $("#u-mode").onchange = async (e) => { if (await this.save("ui", "updates", e.target.value)) this.update(u); };
  },
  /** Support messages need the account: the answer goes to its address. */
  supportCard() {
    const who = $("#sup-who"), signedIn = Boolean(S.account?.signed_in && S.account.email);
    if (!who) return;
    $("#sup-send").disabled = !signedIn;
    who.textContent = signedIn
      ? `Sent to support@localtc.tech. The answer comes to ${S.account.email}.`
      : "Sign in to the account (above) to send a message: the answer goes to its email address.";
  },
  account() {
    const card = $("#s-account-card"), a = S.account;
    this.supportCard();
    if (!card || !a) return;
    if (!a.signed_in) {
      card.innerHTML = `
        <h3>Account <span class="muted small">(optional)</span></h3>
        <p class="muted small">LocalTC works fully without one, and your logbook stays on this computer either way.
          An account copies the logbook to <a href="${esc(a.dashboard)}" target="_blank" rel="noopener">localtc.tech</a>
          and feeds the companion app. It sends flight summaries (airports, times, distance, landing rate) and, while
          you fly, the phase and frequencies: never your position, voice, transcripts or recordings.</p>
        <div class="row"><label>Email<input id="a-email" type="email" autocomplete="email" spellcheck="false" value="${esc(this.pendingEmail || "")}"></label>
          <div><button class="btn small primary" id="a-start">${this.pendingEmail ? "Send a new code" : "Email me a code"}</button></div></div>
        <div class="row" id="a-code-row" ${this.pendingEmail ? "" : "hidden"}><label>6-digit code from the email
          <input id="a-code" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="123456"></label>
          <div><button class="btn small primary" id="a-finish">Sign in</button></div></div>
        <p class="small muted">No password: each sign-in is a code sent to your email. The first one creates the account,
          which means agreeing to the <a href="https://localtc.tech/terms#account" target="_blank" rel="noopener">terms</a>
          and <a href="https://localtc.tech/privacy#account" target="_blank" rel="noopener">privacy policy</a> (16 or over).</p>
        <p class="small muted" id="a-msg"></p>`;
      const msg = (t, err) => { $("#a-msg").textContent = t; $("#a-msg").className = `small ${err ? "error" : "muted"}`; };
      $("#a-start").onclick = () => {
        const email = $("#a-email").value.trim();
        api("account/start", { email }).then((v) => { this.pendingEmail = email; this.account(); $("#a-code").focus(); msg(v.message); })
          .catch((e) => msg(e.message, true));
      };
      const finish = () => api("account/finish", { email: this.pendingEmail, code: $("#a-code").value })
        .then((v) => { this.pendingEmail = null; S.account = v; this.account(); toast("Signed in"); })
        .catch((e) => msg(e.message, true));
      $("#a-finish").onclick = finish;
      $("#a-code").onkeydown = (e) => { if (e.key === "Enter") finish(); };
      return;
    }
    card.innerHTML = `
      <h3>Account</h3>
      <div class="row"><span>Signed in as <b>${esc(a.email || "")}</b></span>
        <a class="btn small" href="${esc(a.dashboard)}" target="_blank" rel="noopener">Open dashboard</a></div>
      <label class="check-row"><input type="checkbox" id="a-sync" ${a.sync ? "checked" : ""}> Copy each flight's logbook line to my account</label>
      <label class="check-row"><input type="checkbox" id="a-companion" ${a.companion ? "checked" : ""}> Show the flight in the companion app (phase, frequencies, ATC's last call)</label>
      <label class="check-row"><input type="checkbox" id="a-lan" ${a.companion_lan ? "checked" : ""} ${a.companion ? "" : "disabled"}> On the same Wi-Fi, the phone connects to this PC directly (map, traffic, radio log; nothing goes online)</label>
      <label class="check-row"><input type="checkbox" id="a-remote" ${a.companion_remote_map ? "checked" : ""} ${a.companion ? "" : "disabled"}> Away from this Wi-Fi, send the map, traffic and radio log through the server while the phone or the website's Flight Tracker watches (held in memory there, never stored)</label>
      <label class="check-row"><input type="checkbox" id="a-replays" ${a.upload_replays ? "checked" : ""}> Also upload each flight's replay (its track and radio transcript, never audio), so it plays on localtc.tech and the phone. Off: only the ones you upload from the Logbook</label>
      <label class="check-row"><input type="checkbox" id="a-stats" ${a.stats ? "checked" : ""} ${a.sync ? "" : "disabled"}> Stats and metrics: collect a few more numbers from each flight (the northernmost and southernmost points, equator and date-line crossings, fuel burned, top speed, night landings, time in cloud) and show the Stats page on localtc.tech. Off: none of it is collected, and the account's copy is deleted</label>
      <div class="row"><button class="btn small" id="a-sync-now">Sync now</button>
        <span class="muted small">${a.unsynced ? `${a.unsynced} flight${a.unsynced === 1 ? "" : "s"} not synced yet.` : "Everything synced."} ${esc(a.last_sync || "")}</span></div>
      <div class="row"><button class="btn small" id="a-logout">Sign out</button>
        <button class="btn small danger" id="a-delete">Delete account</button></div>`;
    $("#a-sync").onchange = (e) => this.save("account", "sync", e.target.checked).then(() => (a.sync = e.target.checked));
    $("#a-companion").onchange = (e) => this.save("account", "companion", e.target.checked).then(() => { a.companion = e.target.checked; this.account(); });
    $("#a-lan").onchange = (e) => this.save("account", "companion_lan", e.target.checked).then(() => { a.companion_lan = e.target.checked; $("#restart-bar").hidden = false; });
    $("#a-remote").onchange = (e) => this.save("account", "companion_remote_map", e.target.checked).then(() => (a.companion_remote_map = e.target.checked));
    $("#a-replays").onchange = (e) => this.save("account", "upload_replays", e.target.checked).then(() => (a.upload_replays = e.target.checked));
    $("#a-stats").onchange = (e) => this.save("account", "stats", e.target.checked).then(() => { a.stats = e.target.checked; return api("account/sync", {}); })
      .then((v) => { if (v) { S.account = v; this.account(); } }).catch(fail);
    $("#a-sync-now").onclick = () => api("account/sync", {}).then((v) => { S.account = v; this.account(); }).catch(fail);
    $("#a-logout").onclick = () => api("account/logout", {}).then((v) => { S.account = v; this.account(); toast("Signed out"); }).catch(fail);
    $("#a-delete").onclick = () => {
      const email = prompt("Deleting the account removes it and every flight synced to it from the server, for good. Your logbook on this computer stays.\n\nType the account's email address to delete it:");
      if (email) api("account/delete", { email }).then((v) => { S.account = v; this.account(); toast("Account deleted"); }).catch(fail);
    };
  },
  captureKey(field = "ptt_key", capId = "#s-key", hintId = "#s-key-hint") {
    const cap = $(capId), hint = $(hintId);
    cap.classList.add("listening"); cap.textContent = "press a key";
    hint.textContent = "Esc cancels. Pick a key the sim doesn't use (Right Ctrl, F13, Scroll Lock ...).";
    const handler = async (e) => {
      e.preventDefault(); e.stopPropagation();
      document.removeEventListener("keydown", handler, true);
      cap.classList.remove("listening");
      hint.textContent = "";
      const name = e.code === "Escape" ? null : keyName(e);
      if (e.code !== "Escape" && !name) { toast(`${e.code} can't be a push-to-talk key`, true); }
      if (name && field === "ptt_key" && await this.save("voice", "ptt_key", name)) { S.state.ptt.key = name; $("#help-ptt").textContent = keyLabel(name); }
      else if (name && field !== "ptt_key") await this.save("voice", field, name);
      cap.textContent = keyLabel(S.settings.settings.voice[field]);
    };
    document.addEventListener("keydown", handler, true);
  },
  jobs(jobs) {
    if (!$("#settings-body .card")) return;
    let msg = [];
    for (const kind of ["llm", "whisper", "voice", "voice_lang", "kokoro", ...COMPONENT_KINDS]) {
      const bar = $(`#p-${kind}`), j = jobs[kind];
      if (!bar) continue;
      bar.hidden = !j || j.state !== "running";
      if (j && j.state === "running") {
        bar.classList.toggle("busy", j.fraction == null);
        bar.firstElementChild.style.width = j.fraction == null ? "" : `${Math.round(j.fraction * 100)}%`;
      }
      // (Each part its own text, so each is translated: "Whisper" "Ready".)
      if (j) msg.push(`<span>${esc({ llm: "Language model", whisper: "Whisper", voice: "Voice", voice_lang: "The copilot's voices", kokoro: "Kokoro", ...COMPONENT_NAMES }[kind])}</span>: <span>${esc(j.message)}</span>`);
      const key = j ? `${kind}:${j.state}:${j.message}` : "";
      if (j && j.state !== "running" && !this.seenJobs.has(key)) {
        this.seenJobs.add(key);
        if (kind === "kokoro") setTimeout(async () => { S.tts = await api("tts").catch(() => S.tts); this.render(); }, 300);
        else if (S.models) setTimeout(() => this.refreshModels(), 300);
      }
    }
    const el = $("#job-msg");
    if (el) el.innerHTML = msg.join(" · ");
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


const MapView = {
  map: null, ownMarker: null, trail: null, path: AtcMap.track(), tfc: new Map(), route: null, routeLayer: null, follow: true, tileLayer: null, airports: new Set(),
  zoneLayer: null, zonesOn: true, zonesTimer: null, zonesAt: 0, zonesWho: "", drawnRef: null,
  wxLayer: null, wxOn: true, wxAt: 0, wxWhere: null, radarLayer: null, opsLayer: null, atcOps: null, notamLayer: null,
  zoneAirports: [],
  grounds: new Map(), groundRenderer: null, gateSeen: "",  // each airport's ground close in (AtcMap.ground), by ICAO
  mode: "ifr", rulesSeen: null,  // the IFR or VFR map: follows the flight's rules when they change, else the pilot's pick
  show() {
    if (!this.map) this.init();
    setTimeout(() => this.map.invalidateSize(), 0);
  },
  init() {
    this.map = L.map("map", { zoomControl: true, attributionControl: true, maxZoom: 19 }).setView([47.9, -122.28], 9);
    this.map.createPane("ground").style.zIndex = 390;  // under the routes, the zones and the aircraft (400 and up)
    this.groundRenderer = L.canvas({ padding: 0.5, pane: "ground" });
    this.map.on("zoomend", () => this.groundZoom());
    $("#map-legend").innerHTML = AtcMap.LEGEND;  // atcmap.js: the same legend as the website's Flight Tracker
    $$("#map-mode [data-mode]").forEach((b) => (b.onclick = () => this.setMode(b.dataset.mode)));
    this.syncRules();
    this.tiles();
    this.wxLayer = L.layerGroup().addTo(this.map);  // the real-world hazards, under the ATC zones
    this.zoneLayer = L.layerGroup().addTo(this.map);  // under the route, the track and the aircraft
    this.opsLayer = L.layerGroup().addTo(this.map);  // ATC's hold and route amendments
    this.notamLayer = L.layerGroup().addTo(this.map);  // the flight's airports' NOTAMs (with the weather toggle)
    try { this.wxOn = localStorage.getItem("map-wx") !== "off"; } catch { /* default on */ }
    $("#map-wx").onclick = () => this.setWx(!this.wxOn);
    this.setWx(this.wxOn);
    this.operations(this.atcOps);
    try { this.zonesOn = localStorage.getItem("map-zones") !== "off"; } catch { /* storage unavailable: default on */ }
    this.setZones(this.zonesOn);
    $("#map-zones").onclick = () => this.setZones(!this.zonesOn);
    this.map.on("moveend", () => this.viewChanged());
    this.trail = L.polyline(this.path.pts, { color: "#5fd068", weight: 2, opacity: 0.7 }).addTo(this.map);
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
    const vfr = this.mode === "vfr";  // terrain, light like a paper chart; IFR stays dark
    $("#map").classList.toggle("tiles-dark", !!S.state.map_tiles && !vfr);
    if (S.state.map_tiles === false) return;
    const osm = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
    this.tileLayer = (vfr
      ? L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, subdomains: "abc",
        attribution: `${osm}, SRTM | style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)` })
      : L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, maxNativeZoom: 18, attribution: osm })).addTo(this.map);
  },
  /* IFR or VFR. The map opens on the flight's rules and follows them when a new plan changes them; the
     switch works any time, and the pick holds until the rules change again. */
  syncRules() {
    const rules = String(S.flight?.rules || S.state?.plan?.rules || "IFR").toLowerCase() === "vfr" ? "vfr" : "ifr";
    if (rules !== this.rulesSeen) { this.rulesSeen = rules; this.setMode(rules); }
  },
  setMode(mode) {
    this.mode = mode === "vfr" ? "vfr" : "ifr";
    $("#page-map").dataset.mode = this.mode;
    $$("#map-mode [data-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === this.mode)));
    this.tiles();
    this.zonesSoon(0);
  },
  /* Real-world weather and restrictions (EFB → Data): SIGMETs, TFRs and restricted areas, and the radar, around
     the aircraft (or the map's middle before a flight), fetched again every few minutes or after a long move. */
  setWx(on) {
    this.wxOn = on;
    $("#map-wx").setAttribute("aria-pressed", String(on));
    $$("#map-legend .lg-wx").forEach((r) => (r.hidden = !on));
    try { localStorage.setItem("map-wx", on ? "on" : "off"); } catch { /* not remembered */ }
    if (!on) { this.wxLayer?.clearLayers(); this.notamLayer?.clearLayers(); if (this.radarLayer) { this.map.removeLayer(this.radarLayer); this.radarLayer = null; } }
    else { this.weather(true); this.drawNotams(); }
  },
  async weather(force = false) {
    if (!this.map || !this.wxOn) return;
    const c = S.own ? [S.own.lat, S.own.lon] : [this.map.getCenter().lat, this.map.getCenter().lng];
    const moved = !this.wxWhere || Math.abs(c[0] - this.wxWhere[0]) + Math.abs(c[1] - this.wxWhere[1]) > 1.5;
    if (!force && !moved && Date.now() - this.wxAt < 300000) return;
    this.wxAt = Date.now(); this.wxWhere = c;
    let h;
    try { h = await api(`ops/around?lat=${c[0].toFixed(3)}&lon=${c[1].toFixed(3)}`); } catch { return; }
    if (!this.wxOn) return;
    this.wxLayer.clearLayers();
    AtcMap.hazards(this.wxLayer, h);
    if (this.radarLayer) { this.map.removeLayer(this.radarLayer); this.radarLayer = null; }
    if (h.radar && h.radar.url) {
      this.radarLayer = L.tileLayer(h.radar.url, { opacity: 0.5, maxNativeZoom: h.radar.max_zoom || 7, maxZoom: 18,
        attribution: h.radar.attribution || "RainViewer" }).addTo(this.map);
    }
    const why = [h.sigmets_meta, h.tfrs_meta, h.airspace_meta].filter((m) => m && !m.available && m.why).map((m) => m.why);
    $("#map-wx").title = why.length ? `Not shown: ${why.join("; ")}` : "Weather (SIGMETs, radar) and TFRs / restricted areas";
  },
  /** The flight's airports' NOTAMs (the EFB's briefings) on their airports, with the weather toggle. */
  drawNotams() {
    if (!this.notamLayer) return;
    this.notamLayer.clearLayers();
    if (this.wxOn) AtcMap.notams(this.notamLayer, Efb.state?.briefings, this.zoneAirports);
  },
  /** ATC's hold and route amendments (the "efb" message's ``atc``). */
  operations(ops) {
    this.atcOps = ops;
    if (!this.opsLayer) return;
    this.opsLayer.clearLayers();
    AtcMap.operations(this.opsLayer, ops);
    const min = ops && ops.minimum_altitude_ft;
    $("#map-mva").textContent = min ? `MIN ALT ${Number(min).toLocaleString()}` : "";
  },
  setFollow(on) {
    this.follow = on;
    $("#map-follow").setAttribute("aria-pressed", String(on));
    if (on && S.own) this.map.panTo([S.own.lat, S.own.lon]);
  },
  /* The path flown so far, from LocalTC itself: on opening, and whenever this page fell behind (a window the sim
     covered stops reading for a while; what it missed is in here, not drawn as one straight line). */
  setTrail(points) {
    if (!Array.isArray(points) || !points.length) return;
    this.path.set(points);
    if (S.own) this.path.pts.push([S.own.lat, this.path.lon(S.own.lon)]);
    if (this.trail) this.trail.setLatLngs(this.path.pts);
  },
  own(o) {
    // The track is kept whether or not the map has ever been opened, so opening it mid-flight shows
    // where the flight has been rather than starting a line from that moment.
    if (this.path.add(o.lat, o.lon, o.ground) && this.trail) this.trail.setLatLngs(this.path.pts);
    if (!this.map) return;
    // Past the date line the aircraft is drawn where the path goes on, and the route and zones are moved there.
    const at = [o.lat, this.path.lon(o.lon)];
    AtcMap.setRef(at[1]);
    if (this.drawnRef != null && Math.abs(at[1] - this.drawnRef) > 90) { this.planKey = null; this.plan(S.state.plan); this.zonesSoon(0); }
    if (this.drawnRef == null || Math.abs(at[1] - this.drawnRef) > 90) this.drawnRef = at[1];
    // Drawn as what's being flown (a Cessna as a Cessna), from the flight's aircraft type
    const kind = AtcMap.kindOf(S.flight?.aircraft || S.state.plan?.aircraft || "");
    const icon = L.divIcon({ className: "own-icon", html: `<div style="transform:rotate(${o.hdg}deg)">${AtcMap.PLANE("#5fd068", 30, kind)}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
    if (!this.ownMarker) { this.ownMarker = L.marker(at, { icon, zIndexOffset: 1000 }).addTo(this.map); this.map.setView(at, 11); }
    else { this.ownMarker.setLatLng(at); this.ownMarker.setIcon(icon); }
    if (this.follow) this.map.panTo(at, { animate: false });
    this.weather();
    $("#map-info").textContent = `${o.alt.toLocaleString()} ft  ${o.gs} kt  HDG ${String(o.hdg_mag).padStart(3, "0")}  VS ${o.vs > 0 ? "+" : ""}${o.vs}`;
  },
  clear() {
    this.path = AtcMap.track();
    this.trail?.setLatLngs([]);
    if (this.ownMarker) { this.map.removeLayer(this.ownMarker); this.ownMarker = null; }
    this.traffic([]);
    this.drawnRef = null;
    $("#map-info").textContent = "";
  },
  traffic(list) {
    if (!this.map) return;
    AtcMap.traffic(this.map, this.tfc, list);  // atcmap.js: the website's Flight Tracker draws them the same way
  },
  plan(p) {
    if (!this.map) return;
    const key = p ? `${p.origin}-${p.destination}-${p.simbrief_id}-${p.fixes?.length}-${this.arrivalKey || ""}` : "";
    if (key === this.planKey) return;
    this.planKey = key;
    if (this.routeLayer) { this.map.removeLayer(this.routeLayer); this.routeLayer = null; }
    this.route = null;
    if (!p) return;
    this.routeLayer = L.layerGroup().addTo(this.map);
    // ATC's runway, not the plan's: the route as planned up to the airport, then in to the final of the runway given
    const arr = this.arrival, fixes = p.fixes || [];
    const shown = arr && fixes.length > 1 ? { ...p, fixes: [...fixes.filter((x, i) => !(i === fixes.length - 1 && x.kind === "apt")),
      { ident: `${arr.runway} final`, lat: arr.final[0], lon: arr.final[1], kind: "wpt" },
      { ident: p.destination, lat: arr.threshold[0], lon: arr.threshold[1], kind: "apt" }] } : p;
    this.route = AtcMap.route(this.routeLayer, shown);
    this.zonesSoon();
    Promise.all([p.origin, p.destination].map((icao) => (icao ? this.airport(icao) : null))).then(([from, to]) => {
      if (!this.route && from && to && this.planKey === key)  // a typed plan: no fixes, a straight line
        this.route = AtcMap.straight(this.routeLayer, from, to);
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
    AtcMap.runways(this.map, a);
    this.layout(a.icao);
  },
  /* An airport's ground, close in: taxiways and runways from zoom 13, their names from 15, the gates' names from 16
     (the map stays quick further out, and readable). Drawn again when ground gives a gate (yours, and who's where). */
  async layout(icao, again = false) {
    if (!this.map || (this.grounds.has(icao) && !again)) return;
    let a;
    try { a = await api(`airport/layout?icao=${encodeURIComponent(icao)}`); } catch { return; /* not known yet */ }
    const old = this.grounds.get(icao);
    if (old) Object.values(old).forEach((l) => l.remove?.());
    const g = { base: L.layerGroup(), labels: L.layerGroup(), gates: L.layerGroup(), gateLabels: L.layerGroup() };
    AtcMap.ground({ ...g, renderer: this.groundRenderer }, a);
    this.grounds.set(icao, g);
    this.groundZoom();
  },
  groundZoom() {
    const z = this.map.getZoom();
    for (const g of this.grounds.values()) {
      for (const [layer, from] of [[g.base, 13], [g.gates, 14], [g.labels, 15], [g.gateLabels, 16]]) {
        const on = z >= from;
        if (on && !this.map.hasLayer(layer)) layer.addTo(this.map);
        else if (!on && this.map.hasLayer(layer)) layer.remove();
      }
    }
  },
  focus(a) {
    showTab("map");
    this.drawAirport(a);
    this.setFollow(false);
    this.map.setView([a.lat, a.lon], 13);
  },

  /* ATC zones: the airspace the engine hands over at, drawn from the same data it uses. */
  setZones(on) {
    this.zonesOn = on;
    $("#map-zones").setAttribute("aria-pressed", String(on));
    $("#map-legend").hidden = !on;
    try { localStorage.setItem("map-zones", on ? "on" : "off"); } catch { /* not remembered: fine */ }
    if (on) this.zonesSoon(0); else this.zoneLayer?.clearLayers();
  },
  zonesSoon(delay = 400) {
    if (!this.map || !this.zonesOn) return;
    if (this.zonesTimer && delay) return;  // one is on its way already: never pushed back, or a moving map starves it
    clearTimeout(this.zonesTimer);
    this.zonesTimer = setTimeout(() => { this.zonesTimer = null; this.zones(); }, delay);
  },
  // Following the aircraft moves the map several times a second: the zones are fetched again only when the view
  // has really changed (another zoom, or panned out of the area last drawn), else the wait never ends.
  viewChanged() {
    const v = this.zonesView;
    if (v && v.zoom === this.map.getZoom() && v.bounds.contains(this.map.getBounds())) return;
    this.zonesSoon();
  },
  flightChanged(f) {
    this.syncRules();
    const arr = f?.final && f.final.runway !== f.final.planned ? f.final : null;
    if ((arr?.runway || "") !== (this.arrivalKey || "")) {  // a runway other than the plan's: the route redrawn to it
      this.arrival = arr;
      this.arrivalKey = arr?.runway || "";
      this.plan(S.state?.plan);
    }
    // A handoff changes who is highlighted, a taxi clearance the route drawn; otherwise the zones only need an
    // occasional refresh.
    const who = `${f?.tuned?.station || ""}|${f?.expected?.station || ""}|${f?.taxi || ""}`;
    if (who !== this.zonesWho || Date.now() - this.zonesAt > 20000) { this.zonesWho = who; this.zonesSoon(); }
  },
  async zones() {
    if (!this.map || !this.zonesOn) return;
    const b = this.map.getBounds();
    let z;
    try {
      z = await api(`zones?south=${b.getSouth().toFixed(3)}&west=${b.getWest().toFixed(3)}&north=${b.getNorth().toFixed(3)}&east=${b.getEast().toFixed(3)}`);
    } catch { return; /* nothing to draw yet */ }
    this.zonesAt = Date.now();
    this.zonesView = { zoom: this.map.getZoom(), bounds: b.pad(0.3) };
    if (!this.zonesOn) return;
    this.zoneLayer.clearLayers();
    AtcMap.zones(this.zoneLayer, z, { vfr: this.mode === "vfr" });  // atcmap.js, shared with the website
    this.zoneAirports = z.airports || [];
    const gate = z.gate ? `${z.gate.icao}:${z.gate.name}` : "";
    if (gate !== this.gateSeen) {  // ground gave a gate: the gates drawn again, yours among them
      this.gateSeen = gate;
      if (z.gate) this.layout(z.gate.icao, true);
    }
    this.atisSeen = this.atisSeen || {};
    for (const [icao, letter] of Object.entries(z.atis || {})) {  // a new ATIS: the runways in use and closures again
      if (this.atisSeen[icao] && this.atisSeen[icao] !== letter && this.grounds.has(icao)) this.layout(icao, true);
      this.atisSeen[icao] = letter;
    }
    this.drawNotams();
    $("#map-legend .lg-talk").innerHTML = AtcMap.talk(z);
  },
};

/* ---------- Airport Lookup ---------- */

const KINDS = [["international", "International"], ["airport", "Other airports"], ["heliport", "Helipads"]];

const Lookup = {
  shown: false,
  kinds: null,
  show() {
    if (!this.shown) { this.shown = true; this.kinds = null; this.filters(); this.search($("#lookup-q").value.trim()); }
    $("#lookup-q").focus();
  },
  chosen() {
    if (this.kinds === null) this.kinds = (S.state.lookup_kinds || ["international"]).slice();
    return this.kinds;
  },
  filters(counts) {
    const on = this.chosen();
    $("#lookup-filters").innerHTML = KINDS.map(([id, label]) => {
      const n = counts ? ` <span class="muted">${counts[id] || 0}</span>` : "";
      return `<button class="switch ${on.includes(id) ? "on" : ""}" data-kind="${id}">${label}${n}</button>`;
    }).join("");
    $$("#lookup-filters [data-kind]").forEach((b) => (b.onclick = () => this.toggle(b.dataset.kind)));
  },
  async toggle(kind) {
    const on = this.chosen();
    const next = on.includes(kind) ? on.filter((k) => k !== kind) : [...on, kind];
    this.kinds = next.length ? next : KINDS.map(([id]) => id);  // none chosen means show everything
    this.filters();
    await this.search($("#lookup-q").value.trim());
    Settings.save("ui", "lookup_kinds", this.kinds).catch(() => {});
  },
  async search(q) {
    try {
      const kinds = this.chosen().map((k) => `&kinds=${encodeURIComponent(k)}`).join("");
      const r = await api(`airports/search?q=${encodeURIComponent(q)}${kinds}`);
      this.filters(r.counts);
      $("#lookup-hint").textContent = `${r.matching} of ${r.known} airport${r.known === 1 ? "" : "s"} known on this computer (every airport a flight visits is saved). Any other ICAO is fetched from the sim during a flight.`;
      $("#lookup-results").innerHTML = r.airports.map((a) => `<span class="chip" data-icao="${esc(a.icao)}"><b>${esc(a.icao)}</b>${esc(a.name)}</span>`).join("")
        || '<p class="muted small">Nothing matches these filters.</p>';
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
        <table><tr><th>Runway</th><th>Length</th><th>Width</th><th>Heading</th><th>ILS</th><th>Approaches</th></tr>${a.runways.map((r) => `<tr>
          <td><b>${esc(r.name)}</b></td><td>${r.length_ft.toLocaleString()} ft</td><td>${r.width_ft} ft</td><td>${String(r.heading_mag).padStart(3, "0")}&deg;</td><td>${esc(r.ils.join(", ") || "—")}</td>
          <td>${esc(Object.entries(r.approaches || {}).filter(([, v]) => v.length).map(([end, v]) => `${end}: ${v.join(", ")}`).join("  ·  ") || "visual only")}</td></tr>`).join("")}</table>
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

/* ---------- the ATC tab's boxes, resized ---------- */

const Split = {
  key: "atc-sizes",
  sizes() { try { return JSON.parse(localStorage.getItem(this.key) || "{}") || {}; } catch { return {}; } },
  keep(sizes) { try { localStorage.setItem(this.key, JSON.stringify(sizes)); } catch { /* not remembered: fine */ } },
  apply(ids, height) {
    for (const id of ids) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.style.height = height ? `${height}px` : "";
      el.classList.toggle("sized", !!height);
    }
  },
  init() {
    const saved = this.sizes();
    $$(".splitter").forEach((bar) => {
      const ids = bar.dataset.for.split(",");
      this.apply(ids, saved[ids[0]]);
      bar.addEventListener("dblclick", () => { const s = this.sizes(); delete s[ids[0]]; this.keep(s); this.apply(ids, 0); });
      bar.addEventListener("pointerdown", (e) => {
        const shown = ids.map((id) => document.getElementById(id)).find((el) => el && !el.hidden);
        if (!shown) return;
        e.preventDefault();
        bar.setPointerCapture(e.pointerId);
        bar.classList.add("dragging");
        const y0 = e.clientY, h0 = shown.getBoundingClientRect().height;
        const room = $("#page-atc").getBoundingClientRect().height - 200; // the radio log keeps some room
        let h = h0;
        const move = (ev) => { h = Math.round(Math.max(48, Math.min(room, h0 + ev.clientY - y0))); this.apply(ids, h); };
        const up = () => {
          bar.classList.remove("dragging");
          bar.removeEventListener("pointermove", move);
          const s = this.sizes(); s[ids[0]] = h; this.keep(s);
        };
        bar.addEventListener("pointermove", move);
        bar.addEventListener("pointerup", up, { once: true });
        bar.addEventListener("pointercancel", up, { once: true });
      });
    });
  },
};
Split.init();

/* ---------- start ---------- */

api("state").then(setState).catch(fail);
api("efb").then((d) => Efb.update(d)).catch(() => {});
api("settings").then((s) => (S.settings = s)).catch(() => {});
connect();
