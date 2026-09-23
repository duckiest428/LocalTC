/* The dashboard: signs in to the optional LocalTC account, follows the flight in progress (the Flight
   Tracker) and shows the logbook synced from the app. Leaflet draws the maps (vendor/leaflet, served from
   here; the tiles come from OpenStreetMap). The sign-in is an HttpOnly cookie on api.localtc.tech, which
   this page can't read; nothing is kept in this browser's storage. */

const API = ["localhost", "127.0.0.1"].includes(location.hostname) ? "http://localhost:8787" : "https://api.localtc.tech";
const $ = (s) => document.querySelector(s);

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(method, path, body) {
  const res = await fetch(API + path, {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-LocalTC": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `The server said ${res.status}.`);
    err.status = res.status;
    throw err;
  }
  return data;
}

/** "Firefox on Windows": enough to tell sign-ins apart in the device list, nothing more. */
function deviceName() {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "A browser";
  const os = /Windows/.test(ua) ? "Windows" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS X/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

function say(text, error = false) {
  const m = $("#msg");
  m.hidden = !text;
  m.textContent = text || "";
  m.classList.toggle("error", error);
}

function show(which) {
  for (const id of ["auth", "dash"]) $(`#${id}`).hidden = id !== which;
  $("#dash-who").hidden = which !== "dash";
}

// --- signing in: a link or a code by email, no password ----------------------------------------------------

let pendingEmail = null;

function awaitingCode(email) {
  pendingEmail = email;
  $("#start-form").hidden = !!email;
  $("#code-form").hidden = !email;
  if (email) $("#f-code").focus();
}

$("#start-form").onsubmit = async (e) => {
  e.preventDefault();
  const email = $("#f-email").value.trim();
  if (!email) return say("Enter your email address.", true);
  const button = $("#f-start");
  button.disabled = true;
  try {
    const r = await api("POST", "/v1/auth/start", { email });
    awaitingCode(email);
    say(r.message);
  } catch (err) {
    say(err.message, true);
  } finally {
    button.disabled = false;
  }
};

$("#code-form").onsubmit = async (e) => {
  e.preventDefault();
  const code = $("#f-code").value.replace(/\D/g, "");
  if (code.length !== 6) return say("Enter the 6-digit code from the email.", true);
  try {
    await api("POST", "/v1/auth/finish", { email: pendingEmail, code, kind: "web", device: deviceName() });
    $("#f-code").value = "";
    awaitingCode(null);
    say("");
    await load();
  } catch (err) { say(err.message, true); }
};

$("#f-again").onclick = () => { awaitingCode(null); say(""); };

$("#btn-signout").onclick = async () => {
  try { await api("POST", "/v1/auth/logout"); } catch { /* signed out either way */ }
  Tracker.stop();
  show("auth");
  say("Signed out.");
};

// --- the dashboard --------------------------------------------------------------------------------------

let next = null;

async function load() {
  let me;
  try {
    me = await api("GET", "/v1/me");
  } catch (err) {
    if (err.status === 401) { show("auth"); return; }
    show("auth");
    say(`Can't reach the LocalTC server: ${err.message}`, true);
    return;
  }
  show("dash");
  $("#who-email").textContent = me.email;
  devices(me.sessions);
  const [stats, page] = await Promise.all([api("GET", "/v1/stats"), api("GET", "/v1/flights?limit=50")]);
  tiles(stats);
  map(stats);
  $("#flights").innerHTML = "";
  rows(page);
  Tracker.start();
}

function hm(min) {
  if (min == null) return "—";
  return `${Math.floor(min / 60)}:${String(Math.round(min % 60)).padStart(2, "0")}`;
}

function tiles(s) {
  const tile = (v, k) => `<div class="tile"><b>${esc(v)}</b><span>${esc(k)}</span></div>`;
  $("#tiles").innerHTML = [
    tile(s.flights, "flights"), tile(s.air_hours, "hours flown"), tile(s.airports.length, "airports"),
    tile(s.distance_nm.toLocaleString(), "nm flown"), tile(s.landings, "landings"),
    tile(s.average_landing_fpm == null ? "—" : `${s.average_landing_fpm} fpm`, "average landing"),
    tile(s.readback_accuracy == null ? "—" : `${Math.round(s.readback_accuracy * 100)}%`, "readbacks right"),
  ].join("");
  $("#dash-title").textContent = s.flights ? "Your flights." : "No flights yet.";
  $(".flights-h").textContent = s.flights ? `Flights (${s.flights})` : "Flights";
}

function rows(page) {
  const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  const html = page.flights.map((f) => `
    <tr data-id="${esc(f.id)}" data-route="${esc(`${f.origin}>${f.destination}`)}">
      <td>${esc(day(f.started_at))}</td>
      <td class="mono">${esc(f.callsign || "—")}</td>
      <td class="mono">${esc(f.origin || "?")} → ${esc(f.destination || "?")}${f.arrival_gate ? ` <span class="sub">${esc(f.arrival_gate)}</span>` : ""}</td>
      <td>${esc(f.aircraft || "")}</td>
      <td class="mono">${hm(f.air_min)}</td>
      <td class="mono">${hm(f.block_min)}</td>
      <td class="mono">${f.landing_vs_fpm == null ? "—" : `${esc(f.landing_vs_fpm)} fpm`}</td>
      <td><button class="linklike del" type="button" title="Delete this flight from the account">Delete</button></td>
    </tr>`).join("");
  $("#flights").insertAdjacentHTML("beforeend", html);
  next = page.next;
  $("#more").hidden = !next;
  $("#empty").hidden = $("#flights").children.length > 0;
}

$("#more").onclick = async () => rows(await api("GET", `/v1/flights?limit=50&before=${encodeURIComponent(next)}`));

$("#flights").onclick = async (e) => {
  const button = e.target.closest(".del");
  if (!button) {  // a click on a flight shows its route on the map
    const tr = e.target.closest("tr[data-route]");
    if (tr && flightsMap) { pick(...tr.dataset.route.split(">")); flightsMap.focus(...tr.dataset.route.split(">")); }
    return;
  }
  const row = button.closest("tr");
  if (!confirm("Delete this flight from your account? The app's logbook keeps its copy.")) return;
  try {
    await api("DELETE", `/v1/flights/${encodeURIComponent(row.dataset.id)}`);
    row.remove();
    const stats = await api("GET", "/v1/stats");
    tiles(stats);
    map(stats);
  } catch (err) { say(err.message, true); }
};

function devices(sessions) {
  const since = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const kind = { desktop: "LocalTC app", ios: "Companion app", web: "Browser" };
  $("#devices").innerHTML = sessions.map((s) => `
    <li data-id="${esc(s.id)}"><span><b>${esc(kind[s.kind] || s.kind)}</b> ${esc(s.device)}
      <span class="sub">last used ${esc(since(s.last_used_at))}${s.current ? " · this browser" : ""}</span></span>
      ${s.current ? "" : '<button class="linklike" type="button">Sign out</button>'}</li>`).join("");
}

$("#devices").onclick = async (e) => {
  const li = e.target.closest("button") && e.target.closest("li");
  if (!li) return;
  try {
    await api("DELETE", `/v1/sessions/${encodeURIComponent(li.dataset.id)}`);
    li.remove();
  } catch (err) { say(err.message, true); }
};

$("#btn-export").onclick = async () => {
  try {
    const data = await api("GET", "/v1/export");
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "localtc-account.json" });
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) { say(err.message, true); }
};

$("#del-form").onsubmit = async (e) => {
  e.preventDefault();
  const email = $("#del-email").value.trim();
  if (!email) return say("Type your email address to confirm.", true);
  if (!confirm("Delete the account and every flight in it, for good?")) return;
  try {
    await api("DELETE", "/v1/me", { email });
    Tracker.stop();
    show("auth");
    say("The account and everything in it are deleted.");
  } catch (err) { say(err.message, true); }
};

// --- the logbook's map: every airport and route (flightsmap.js, shared with the app) -------------------------

let flightsMap = null;

function pick(origin, destination) {
  document.querySelectorAll("#flights tr[data-route]").forEach((tr) => tr.classList.toggle("picked", tr.dataset.route === `${origin}>${destination}`));
}

function map(stats) {
  if (!flightsMap) flightsMap = new FlightsMap($("#lb-map"), { tiles: true, onRoute: pick });
  const shown = flightsMap.show({ airports: stats.airports, routes: stats.routes });
  $("#lb-map").classList.toggle("empty", !shown);
}

// --- the Flight Tracker: the flight in progress, live, as the companion app sees it ------------------------
// A WebSocket to the account's relay. While it's open the app sends the aircraft, traffic and radio (if its
// "map away from home" setting is on); they pass through the server's memory and are never stored.

const Tracker = {
  ws: null, retry: 0, timer: null, ping: null, map: null, plane: null, trail: null, trailPts: [], tfc: new Map(),
  status: { active: false }, gotOwn: false, follow: true, wanted: false,

  start() {
    this.wanted = true;
    this.connect();
    document.addEventListener("visibilitychange", this.onVisibility);
  },
  stop() {
    this.wanted = false;
    document.removeEventListener("visibilitychange", this.onVisibility);
    this.close();
  },
  // Hidden tab: let go, so the app stops sending the position for nobody.
  onVisibility: () => (document.hidden ? Tracker.close() : Tracker.wanted && Tracker.connect()),

  connect() {
    if (this.ws || !this.wanted) return;
    clearTimeout(this.timer);
    this.conn("Connecting ...");
    const ws = new WebSocket(`${API.replace(/^http/, "ws")}/v1/live/ws`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.conn("Live", "on");
      this.ping = setInterval(() => ws.readyState === 1 && ws.send("ping"), 30000);
    };
    ws.onmessage = (e) => {
      if (e.data === "pong") return;
      try { const m = JSON.parse(e.data); this.handle(m.type, m.data); } catch { /* not ours */ }
    };
    ws.onclose = () => {
      clearInterval(this.ping);
      this.ws = null;
      if (!this.wanted || document.hidden) return;
      this.retry = Math.min(this.retry + 1, 6);
      this.conn("Reconnecting ...");
      this.timer = setTimeout(() => this.connect(), 1000 * 2 ** this.retry);
    };
  },
  close() {
    clearTimeout(this.timer);
    clearInterval(this.ping);
    if (this.ws) { const ws = this.ws; this.ws = null; ws.onclose = null; ws.close(); }
    this.conn("Paused");
  },
  conn(text, cls = "") {
    const c = $("#tr-conn");
    c.textContent = text;
    c.className = `tr-conn ${cls}`;
  },

  handle(type, data) {
    if (type === "hello") {
      this.statusIs(data.status || { active: false });
      this.clearMap();
      if (data.own) this.own(data.own);
      if (data.traffic) this.traffic(data.traffic);
      $("#tr-radio").innerHTML = "";
      for (const line of data.radio || []) this.radio(line);
    } else if (type === "status") this.statusIs(data);
    else if (type === "own") this.own(data);
    else if (type === "traffic") this.traffic(data);
    else if (type === "radio") this.radio(data);
  },

  statusIs(s) {
    const was = this.status.active;
    this.status = s;
    $("#tr-body").hidden = !s.active;
    if (!s.active) {
      $("#tr-status").innerHTML = `<p class="hint">No flight right now. Start one in the LocalTC app, signed in with this
        account and the companion on (Quick Settings → Account), and it shows up here.</p>`;
      $("#tr-nomap").hidden = true;
      if (was) this.clearMap();
      return;
    }
    const st = (x) => (x && x.station ? `${esc(x.station)} <span class="mono">${x.mhz ? Number(x.mhz).toFixed(3) : ""}</span>` : "—");
    $("#tr-status").innerHTML = `<div class="tr-head"><span class="live-dot"></span><b>${esc(s.callsign || "Flying")}</b>
      <span class="mono">${esc(s.origin || "?")} → ${esc(s.destination || "?")}</span>
      <span class="tr-phase">${esc(s.phase_label || s.phase || "")}</span>
      ${s.rules ? `<span class="tr-rules">${esc(s.rules)}</span>` : ""}</div>
      ${s.last_atc && s.last_atc.text ? `<p class="last-atc">“${esc(s.last_atc.text)}”</p>` : ""}`;
    const fact = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    $("#tr-facts").innerHTML = [
      fact("On", st(s.tuned)), fact("Next", st(s.next)),
      fact("Squawk", `<span class="mono">${esc(s.squawk || "—")}</span>`),
      fact("Assigned", s.altitude_ft ? `<span class="mono">${Number(s.altitude_ft).toLocaleString()} ft</span>` : "—"),
      fact("Runway", esc(s.runway || "—")),
      fact("To go", s.ete ? `<span class="mono">${esc(s.ete.nm)} nm · ${esc(s.ete.min)} min</span>` : "—"),
      ...(s.gate ? [fact("Gate", esc(s.gate))] : []),
    ].join("");
    this.ensureMap();
    // No position a while into the flight: the app keeps it at home (its setting), say so.
    clearTimeout(this.noMapTimer);
    this.noMapTimer = setTimeout(() => { $("#tr-nomap").hidden = this.gotOwn || !this.status.active; }, 12000);
  },

  ensureMap() {
    if (this.map) { setTimeout(() => this.map.invalidateSize(), 0); return; }
    this.map = L.map("tr-map", { worldCopyJump: true }).setView([39, -98], 4);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 16,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(this.map);
    $("#tr-map").classList.add("fm-tiles");
    this.trail = L.polyline([], { className: "tr-trail", weight: 2.5 }).addTo(this.map);
    this.map.on("dragstart", () => { this.follow = false; });
    const follow = L.control({ position: "topright" });
    follow.onAdd = () => {
      const b = L.DomUtil.create("button", "tr-follow");
      b.type = "button"; b.textContent = "Follow";
      L.DomEvent.on(b, "click", (e) => { L.DomEvent.stop(e); this.follow = true; if (this.plane) this.map.panTo(this.plane.getLatLng()); });
      return b;
    };
    follow.addTo(this.map);
  },
  clearMap() {
    this.gotOwn = false;
    this.trailPts = [];
    if (this.trail) this.trail.setLatLngs([]);
    if (this.plane) { this.plane.remove(); this.plane = null; }
    for (const m of this.tfc.values()) m.remove();
    this.tfc.clear();
  },
  icon(hdg, cls, size) {
    const svg = `<svg viewBox="0 0 32 32" width="${size}" height="${size}"><path d="M16 2c1.2 0 2 1.4 2 3v7l11 6v3l-11-3v6l3 2v2.5l-5-1.5-5 1.5V26l3-2v-6L3 21v-3l11-6V5c0-1.6.8-3 2-3z"/></svg>`;
    return L.divIcon({ className: `tr-plane ${cls}`, html: `<div style="transform:rotate(${Number(hdg) || 0}deg)">${svg}</div>`,
      iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  },
  own(o) {
    if (!this.status.active || o.lat == null) return;
    this.ensureMap();
    this.gotOwn = true;
    $("#tr-nomap").hidden = true;
    const at = [o.lat, o.lon];
    if (!this.plane) { this.plane = L.marker(at, { icon: this.icon(o.hdg, "own", 30), zIndexOffset: 1000 }).addTo(this.map); this.map.setView(at, o.ground ? 13 : 9); }
    else { this.plane.setLatLng(at); this.plane.setIcon(this.icon(o.hdg, "own", 30)); }
    const last = this.trailPts[this.trailPts.length - 1];
    if (!last || Math.abs(last[0] - o.lat) + Math.abs(last[1] - o.lon) > 0.002) { this.trailPts.push(at); this.trail.setLatLngs(this.trailPts); }
    if (this.follow) this.map.panTo(at, { animate: false });
    this.plane.bindTooltip(`${Number(o.alt || 0).toLocaleString()} ft · ${o.gs ?? "—"} kt · ${String(o.hdg ?? 0).padStart(3, "0")}°`);
  },
  traffic(list) {
    if (!this.map) return;
    const seen = new Set();
    for (const t of list || []) {
      seen.add(t.id);
      const label = `${esc(t.callsign || "")} ${t.ground ? "GND" : Math.round((t.alt || 0) / 100).toString().padStart(3, "0")}`;
      const m = this.tfc.get(t.id);
      if (m) { m.setLatLng([t.lat, t.lon]); m.setIcon(this.icon(t.hdg, t.ground ? "ground" : "air", 18)); }
      else this.tfc.set(t.id, L.marker([t.lat, t.lon], { icon: this.icon(t.hdg, t.ground ? "ground" : "air", 18) })
        .bindTooltip(label).addTo(this.map));
    }
    for (const [id, m] of this.tfc) if (!seen.has(id)) { m.remove(); this.tfc.delete(id); }
  },
  radio(line) {
    if (!["atc", "pilot", "copilot"].includes(line.kind) || !line.text) return;
    const list = $("#tr-radio");
    const who = line.kind === "atc" ? esc(line.station || "ATC") : line.kind === "copilot" ? "Copilot" : "You";
    list.insertAdjacentHTML("beforeend", `<li class="${esc(line.kind)}"><span class="who">${who}</span> ${esc(line.text)}</li>`);
    while (list.children.length > 40) list.firstElementChild.remove();
    list.scrollTop = list.scrollHeight;
  },
};

// --- arriving from the email's link --------------------------------------------------------------------

(async function start() {
  const token = new URLSearchParams(location.search).get("login");
  if (token) {
    history.replaceState(null, "", location.pathname);  // the token leaves the address bar
    try {
      await api("POST", "/v1/auth/finish", { token, kind: "web", device: deviceName() });
    } catch (err) { say(err.message, true); }
  }
  await load();
})();
