/* The logbook dashboard: signs in to the optional LocalTC account and shows the flights synced from
   the app. No dependencies. The sign-in is an HttpOnly cookie on api.localtc.tech, which this page
   can't read; nothing is kept in this browser's storage. */

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
  stopLive();
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
  startLive();
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
}

function rows(page) {
  const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  const html = page.flights.map((f) => `
    <tr data-id="${esc(f.id)}">
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
  if (!button) return;
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
    stopLive();
    show("auth");
    say("The account and everything in it are deleted.");
  } catch (err) { say(err.message, true); }
};

// --- the map: airports and the routes between them, no map tiles (nothing loaded from elsewhere) --------

function map(stats) {
  const svg = $("#map");
  const places = stats.airports.filter((a) => a.lat != null && a.lon != null);
  const where = Object.fromEntries(places.map((a) => [a.icao, a]));
  if (!places.length) {
    svg.setAttribute("viewBox", "0 0 1000 300");
    svg.innerHTML = '<text x="500" y="150" text-anchor="middle" class="map-empty">The airports you fly appear here.</text>';
    return;
  }
  let [s, n, w, e] = [Math.min(...places.map((a) => a.lat)), Math.max(...places.map((a) => a.lat)),
    Math.min(...places.map((a) => a.lon)), Math.max(...places.map((a) => a.lon))];
  // Keep the aspect of the box, with room around the edges, and never zoomed in past a few degrees.
  const midLat = (s + n) / 2;
  const k = Math.cos((midLat * Math.PI) / 180);
  let spanX = Math.max((e - w) * k, 4) * 1.3, spanY = Math.max(n - s, 2) * 1.3;
  // The map takes the shape of what was flown, between a wide strip and a near square.
  const ratio = Math.min(Math.max(spanY / spanX, 0.35), 0.75);
  if (spanY / spanX < ratio) spanY = spanX * ratio; else spanX = spanY / ratio;
  const H = Math.round(1000 * ratio);
  svg.setAttribute("viewBox", `0 0 1000 ${H}`);
  const cx = ((w + e) / 2) * k, cy = midLat;
  const x = (lon) => ((lon * k - (cx - spanX / 2)) / spanX) * 1000;
  const y = (lat) => ((cy + spanY / 2 - lat) / spanY) * H;
  const grid = [];
  const step = spanY > 40 ? 20 : spanY > 15 ? 10 : 5;
  for (let lat = Math.ceil((cy - spanY / 2) / step) * step; lat < cy + spanY / 2; lat += step) grid.push(`<line x1="0" x2="1000" y1="${y(lat)}" y2="${y(lat)}"/>`);
  for (let lon = Math.ceil((cx - spanX / 2) / k / step) * step; lon * k < cx + spanX / 2; lon += step) grid.push(`<line y1="0" y2="${H}" x1="${x(lon)}" x2="${x(lon)}"/>`);
  const most = Math.max(1, ...stats.routes.map((r) => r.flights));
  const routes = stats.routes.filter((r) => where[r.origin] && where[r.destination]).map((r) => {
    const a = where[r.origin], b = where[r.destination];
    const [x1, y1, x2, y2] = [x(a.lon), y(a.lat), x(b.lon), y(b.lat)];
    const bend = Math.hypot(x2 - x1, y2 - y1) * 0.12;  // a slight arc reads as a flight, not a border
    const mx = (x1 + x2) / 2 - ((y2 - y1) / (Math.hypot(x2 - x1, y2 - y1) || 1)) * bend;
    const my = (y1 + y2) / 2 + ((x2 - x1) / (Math.hypot(x2 - x1, y2 - y1) || 1)) * bend;
    return `<path d="M${x1},${y1} Q${mx},${my} ${x2},${y2}" stroke-width="${1.2 + 2.5 * (r.flights / most)}"><title>${esc(r.origin)} → ${esc(r.destination)}: ${esc(r.flights)}</title></path>`;
  });
  const dots = places.map((a) => `<g transform="translate(${x(a.lon)},${y(a.lat)})"><circle r="${3 + Math.min(a.visits, 8)}"><title>${esc(a.icao)}: ${esc(a.visits)} visits</title></circle><text x="9" y="4">${esc(a.icao)}</text></g>`);
  svg.innerHTML = `<g class="grid">${grid.join("")}</g><g class="routes">${routes.join("")}</g><g class="airports">${dots.join("")}</g>`;
}

// --- the flight in progress, as the companion app sees it -----------------------------------------------

let liveTimer = null;

async function refreshLive() {
  try {
    const s = await api("GET", "/v1/live");
    const box = $("#live");
    box.hidden = !s.active;
    if (!s.active) return;
    const f = (st) => (st && st.station ? `${esc(st.station)} ${st.mhz ? Number(st.mhz).toFixed(3) : ""}` : "—");
    box.innerHTML = `<span class="live-dot"></span><b>${esc(s.callsign || "Flying")}</b>
      <span class="mono">${esc(s.origin || "?")} → ${esc(s.destination || "?")}</span>
      <span>${esc(s.phase_label || s.phase || "")}</span>
      <span>On ${f(s.tuned)}</span>${s.next && s.next.station ? `<span>Next ${f(s.next)}</span>` : ""}
      ${s.last_atc && s.last_atc.text ? `<span class="last-atc">“${esc(s.last_atc.text)}”</span>` : ""}`;
  } catch { /* the next try will do */ }
}

function startLive() {
  stopLive();
  refreshLive();
  liveTimer = setInterval(() => { if (!document.hidden) refreshLive(); }, 15000);
}

function stopLive() {
  if (liveTimer) clearInterval(liveTimer);
  liveTimer = null;
  $("#live").hidden = true;
}

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
