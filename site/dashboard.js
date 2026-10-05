/* The dashboard: signs in to the optional LocalTC account, then a sidebar of sections, one shown at a time:
   the Flight Tracker (the flight in progress), the logbook synced from the app (sharing a flight's card:
   sharecard.js), Wrapped (wrapped.js), support, and the account. Leaflet draws the maps (vendor/leaflet, served from
   here; the tiles come from OpenStreetMap). The sign-in is an HttpOnly cookie on api.localtc.tech, which
   this page can't read; nothing is kept in this browser's storage. */

// API, $, esc and api() come from api.js; the Flight Tracker from tracker.js.

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
  if (which === "auth") { Tracker.stop(); page = null; document.title = "Dashboard — LocalTC"; }
}

// --- the sections: the sidebar picks one; the address bar's #hash remembers it --------------------------------

const PAGES = ["tracker", "logbook", "replay", "wrapped", "support", "account"];
let page = null;

function go(name, { push = false } = {}) {
  const [base, id] = (name || "").split("/");  // "replay/<flight id>": a flight to rewatch
  const hash = (base === "replay" || base === "wrapped") && id ? `${base}/${id}` : base;
  name = PAGES.includes(base) && (base !== "replay" || id) ? base : "tracker";
  if (push && `#${hash}` !== location.hash) history.pushState(null, "", `#${name === base ? hash : name}`);
  page = name;
  for (const p of PAGES) $(`#${p}`).hidden = p !== name;
  const nav = name === "replay" ? "logbook" : name;
  document.querySelectorAll(".side-nav a").forEach((a) => {
    if (a.dataset.page === nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (name === "replay") Replay.open(decodeURIComponent(id)); else Replay.close();
  if (name === "wrapped") Wrapped.open(id);
  document.title = `${$(`#${name}-h`).textContent} — LocalTC`;
  // The tracker's connection is only open while it's on screen: the app sends the position for no one otherwise.
  if (name === "tracker") Tracker.start(); else Tracker.stop();
  if (name === "logbook" && flightsMap) setTimeout(() => flightsMap.fit(), 0);  // Leaflet measures a shown map
  if (name === "tracker" && Tracker.map) setTimeout(() => Tracker.map.invalidateSize(), 0);
}

$(".side-nav").onclick = (e) => {
  const a = e.target.closest("a[data-page]");
  if (!a) return;
  e.preventDefault();
  go(a.dataset.page, { push: true });
  window.scrollTo(0, 0);
};
window.addEventListener("popstate", () => { if (!$("#dash").hidden) go(location.hash.slice(1)); });

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

document.querySelectorAll(".signout").forEach((b) => b.onclick = async () => {
  try { await api("POST", "/v1/auth/logout"); } catch { /* signed out either way */ }
  show("auth");
  say("Signed out.");
});

// --- the dashboard --------------------------------------------------------------------------------------

let next = null;
const flightsById = new Map();

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
  document.querySelectorAll(".who-email").forEach((el) => { el.textContent = me.email; });
  $("#display-name").value = me.display_name || "";
  $("#s-from").textContent = me.email;
  devices(me.sessions);
  go(location.hash.slice(1));
  const [stats, first] = await Promise.all([api("GET", "/v1/stats"), api("GET", "/v1/flights?limit=50")]);
  tiles(stats);
  map(stats);
  $("#flights").innerHTML = "";
  rows(first);
  nudge(first.flights);
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
      <td>${f.has_replay ? `<a class="btn btn-ghost btn-sm" href="#replay/${encodeURIComponent(f.id)}" title="Watch the flight again: the map and the radio, in step">&#9654; Replay</a>`
        : '<span class="sub" title="Upload it from the Logbook in the LocalTC app to replay it here">—</span>'}</td>
      <td><button class="btn btn-ghost btn-sm share" type="button" title="${f.share ? "Shared: anyone with the link sees its card" : "Make a public link to this flight's card"}">${f.share ? "Shared ✓" : "Share flight"}</button></td>
      <td><button class="linklike del" type="button" title="Delete this flight from the account">Delete</button></td>
    </tr>`).join("");
  $("#flights").insertAdjacentHTML("beforeend", html);
  for (const f of page.flights) flightsById.set(f.id, f);
  next = page.next;
  $("#more").hidden = !next;
  $("#empty").hidden = $("#flights").children.length > 0;
}

$("#more").onclick = async () => rows(await api("GET", `/v1/flights?limit=50&before=${encodeURIComponent(next)}`));

$("#flights").onclick = async (e) => {
  if (e.target.closest("a[href^='#replay/']")) return;  // the link goes there by itself
  const sharing = e.target.closest(".share");
  if (sharing) return Share.open(sharing.closest("tr").dataset.id, sharing);
  const button = e.target.closest(".del");
  if (!button) {  // a click on a flight shows its route on the map
    const tr = e.target.closest("tr[data-route]");
    if (tr && flightsMap) { pick(...tr.dataset.route.split(">")); flightsMap.focus(...tr.dataset.route.split(">")); }
    return;
  }
  const row = button.closest("tr");
  if (!confirm("Delete this flight (and its replay, if uploaded) from your account? The app's logbook keeps its copy.")) return;
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

// The name on what's shared ("Flown by ..."): the account server checks it.
$("#name-form").onsubmit = async (e) => {
  e.preventDefault();
  const status = $("#name-status");
  try {
    const saved = await api("PATCH", "/v1/me", { display_name: $("#display-name").value });
    $("#display-name").value = saved.display_name;
    status.textContent = saved.display_name ? `Saved. Your shared flights say "Flown by ${saved.display_name}".` : "Saved. Your shared flights show no name.";
    status.classList.remove("error");
  } catch (err) {
    status.textContent = err.message;
    status.classList.add("error");
  }
};

$("#del-form").onsubmit = async (e) => {
  e.preventDefault();
  const email = $("#del-email").value.trim();
  if (!email) return say("Type your email address to confirm.", true);
  if (!confirm("Delete the account and every flight in it, for good?")) return;
  try {
    await api("DELETE", "/v1/me", { email });
    show("auth");
    say("The account and everything in it are deleted.");
  } catch (err) { say(err.message, true); }
};

// --- support: a message to the developer, answered at the account's address ---------------------------------

function note(text, error = false) {
  const m = $("#s-msg");
  m.hidden = !text;
  m.textContent = text || "";
  m.classList.toggle("error", error);
}

$("#support-form").onsubmit = async (e) => {
  e.preventDefault();
  const message = $("#s-message").value.trim();
  if (message.length < 5) return note("Write a little more, so there's something to go on.", true);
  const button = $("#s-send");
  button.disabled = true;
  try {
    const r = await api("POST", "/v1/support", { kind: document.querySelector("input[name=kind]:checked").value, message, source: "web" });
    note(r.message || "Sent. Thanks!");
    $("#s-message").value = "";
  } catch (err) {
    if (err.status === 401) { show("auth"); say("Your sign-in has expired. Sign in again to send it.", true); return; }
    note(err.message === "Failed to fetch" ? "Can't reach the LocalTC server right now. Try again in a bit, or open a GitHub issue." : err.message, true);
  } finally {
    button.disabled = false;
  }
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

// --- a replay: a flight to rewatch, uploaded from the app (replayplayer.js, shared with it) ----------------------

const Replay = {
  player: null, id: null,
  async open(id) {
    if (id === this.id && this.player) { setTimeout(() => this.player?.fit(), 0); return; }
    this.close();
    this.id = id;
    $("#replay-h").textContent = "Replay";
    const msg = $("#replay-msg");
    msg.classList.remove("error");
    msg.textContent = "Loading the flight ...";
    try {
      const r = await api("GET", `/v1/flights/${encodeURIComponent(id)}/replay`);
      if (this.id !== id) return;  // gone somewhere else meanwhile
      this.player = new ReplayPlayer($("#replay-player"), r, { tiles: true });
      $("#replay-h").textContent = `Replay: ${r.flight.origin || "?"} → ${r.flight.destination || "?"}`;
      document.title = `${$("#replay-h").textContent} — LocalTC`;
      msg.textContent = "Space plays and pauses, J and K go to the previous and next call, the arrows skip 10 seconds.";
      setTimeout(() => this.player?.fit(), 0);
    } catch (err) {
      msg.textContent = err.status === 404 ? "This flight has no replay in the account. Upload it from the Logbook in the LocalTC app." : err.message;
      msg.classList.add("error");
    }
  },
  close() {
    if (this.player) { this.player.destroy(); this.player = null; }
    this.id = null;
  },
};

// --- sharing a flight: a public card at localtc.tech/f/<slug> (sharecard.js, shared with the app) --------------

/** Sends ``blob`` to ``path`` as its body, with the sign-in cookie. */
async function upload(method, path, blob, type) {
  const res = await fetch(API + path, { method, credentials: "include", headers: { "Content-Type": type, "X-LocalTC": "1" }, body: blob });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `The server said ${res.status}.`);
  return data;
}

const Share = {
  model: null,
  async open(id, button) {
    const f = flightsById.get(id);
    if (!f) return;
    this.model = this.model || await import("./cardmodel.js");
    const kept = f.has_replay ? await api("GET", `/v1/flights/${encodeURIComponent(id)}/moments`).catch(() => ({ moments: [], names: {} })) : { moments: [], names: {} };
    const slug = f.share ? f.share.split("/").pop() : null;
    ShareCard.dialog({
      base: "", moments: kept.moments, shared: f.share, slug, replay: !!f.has_replay,
      card: (quote) => this.model.flightCard(f, { quote, names: kept.names }),
      share: (quote, replay) => api("POST", "/v1/shares", { kind: "flight", ref: id, quote, names: kept.names, replay }),
      putImage: (s, blob) => upload("PUT", `/v1/shares/${s}/image`, blob, "image/png"),
      unshare: (s) => api("DELETE", `/v1/shares/${s}`),
      onClose: (url) => {
        f.share = url;
        button.textContent = url ? "Shared ✓" : "Share flight";
      },
    });
  },
};

// --- Wrapped: a week, a month or a year, as slides (wrapped.js, shared with the app) --------------------------

const Wrapped = {
  view: null,
  open(which) {
    if (!this.view) {
      this.view = new WrappedView($("#wrapped-view"), {
        base: "",
        load: (r) => api("GET", `/v1/wrapped?${new URLSearchParams({ period: r.period, from: r.from, to: r.to, tz: r.tz, label: r.label })}`),
        share: (r) => api("POST", "/v1/shares", { kind: "wrapped", period: r.period, from: r.from, to: r.to, tz: r.tz, label: r.label }),
        putImage: (s, blob) => upload("PUT", `/v1/shares/${s}/image`, blob, "image/png"),
      });
    }
    const [period, step] = (which || "").split(":");  // #wrapped/month:-1 is last month
    if (["week", "month", "year"].includes(period)) this.view.open(period, Number(step) || 0);
    else if (!this.view.recap) this.view.open("month", -1);  // last month: the one there is to look back on
  },
};

/** At the start of a month (or a year), a nudge on the logbook: the last one's recap is ready. */
function nudge(flights) {
  const now = new Date();
  const lastMonth = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  const flewThen = flights.some((f) => { const d = new Date(f.started_at); return d >= lastMonth && d < new Date(now.getFullYear(), now.getMonth(), 1); });
  const fresh = now.getDate() <= 7 && flewThen;
  $("#wr-nudge").hidden = !fresh;
  if (!fresh) return;
  const january = now.getMonth() === 0;
  $("#wr-nudge-text").textContent = january ? `Your ${now.getFullYear() - 1} in flying is ready.` : `Your ${lastMonth.toLocaleDateString("en-GB", { month: "long" })} Wrapped is ready.`;
  $("#wr-nudge-go").href = january ? "#wrapped/year:-1" : "#wrapped/month:-1";
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
