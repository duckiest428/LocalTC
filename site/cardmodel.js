/**
 * What goes on a shared card, as plain data: one definition for the dashboard, the desktop app and the
 * account server (which imports this file), so a card previewed in the app is the card the public sees.
 *
 * - moments(radio): the controller's calls worth quoting, best first (a flight card's quote, and Wrapped's
 *   standout moment). Only the controller's words: they're what the pilot heard, never speech-to-text garble.
 * - flightCard(flight, {quote, names}): a logbook line as the public snapshot of it. Nothing private: no
 *   gates, no times of day, no track, one line of the transcript at most.
 * - miniReplay(replay), flightDetails(flight, mini): what the shared page shows under the card, when the pilot
 *   puts the replay on it: the path flown, the radio, the route, the runways, the weather ATC gave.
 * - distanceFraming(nm), hoursFraming(hours): "1.4× around the Earth", "12 feature films".
 */

// Kind -> [pattern on the controller's words, label, score]. The first that matches a line is its kind.
const KINDS = [
  ["clearance", /\bcleared to .+\b(via|as filed)\b/i, "Clearance", 100],
  ["diversion", /\b(divert|diversion)\b/i, "Diversion", 95],
  ["go_around", /\bgo around\b/i, "Go around", 92],
  ["takeoff", /\bcleared for takeoff\b/i, "Takeoff clearance", 90],
  ["landing", /\bcleared to land\b/i, "Landing clearance", 88],
  ["approach", /\bcleared .*\bapproach\b/i, "Approach clearance", 70],
  ["ride", /\bride conditions\b/i, "Ride report", 55],
  ["welcome", /\bradar contact\b/i, "Radar contact", 50],
  ["handoff", /\bcontact [A-Z][\w'-]*(?: [A-Z][\w'-]*)* (Center|Centre|Approach|Departure)\b/, "Handoff", 40],
];
export const MOMENT_LABELS = Object.fromEntries(KINDS.map(([kind, , label]) => [kind, label]));
const MAX_MOMENTS = 5;
export const MAX_QUOTE = 300;

/** The best line of each kind, best kind first. ``radio``: a replay's radio lines (docs/replay-format.md). */
export function moments(radio) {
  const best = new Map();
  for (const line of Array.isArray(radio) ? radio : []) {
    if (!line || line.kind !== "atc" || typeof line.text !== "string" || !line.text.trim()) continue;
    const kind = KINDS.find(([, pattern]) => pattern.test(line.text));
    if (!kind) continue;
    const [name, , label, base] = kind;
    // Of the same kind, the first wins: the clearance as given, not a repeat of it. A greeting is a bonus.
    const score = base + (/\bgood (morning|afternoon|evening|night|day)\b/i.test(line.text) ? 3 : 0);
    if (best.has(name) && best.get(name).score >= score) continue;
    best.set(name, {
      kind: name, label, t: Number(line.t) || 0, station: String(line.station || "").slice(0, 64),
      mhz: typeof line.mhz === "number" ? line.mhz : null, text: line.text.slice(0, MAX_QUOTE), score,
    });
  }
  return [...best.values()].sort((a, b) => b.score - a.score || a.t - b.t).slice(0, MAX_MOMENTS);
}

/** "Seattle" from a replay's airports ({ICAO: {name}}): the names a card can show under the codes. */
export function airportNames(airports) {
  const out = {};
  for (const [icao, a] of Object.entries(airports || {})) {
    if (a && typeof a.name === "string" && a.name.trim()) out[icao] = a.name.trim().slice(0, 40);
  }
  return out;
}

const place = (icao, lat, lon, names) => ({
  icao: String(icao || ""), name: (names && names[icao]) || "",
  lat: typeof lat === "number" ? lat : null, lon: typeof lon === "number" ? lon : null,
});

/**
 * The public snapshot of a logbook line (the server's /v1/flights row, or the app's).
 * @param {Record<string, any>} f
 * @param {{quote?: Record<string, any> | null, names?: Record<string, string>}} [options]
 */
export function flightCard(f, { quote = null, names = {} } = {}) {
  const readbacks = Number(f.readbacks) || 0;
  return {
    kind: "flight",
    callsign: String(f.callsign || ""),
    aircraft: String(f.aircraft || ""),
    date: String(f.started_at || "").slice(0, 10),
    origin: place(f.origin, f.origin_lat, f.origin_lon, names),
    destination: place(f.destination, f.destination_lat, f.destination_lon, names),
    air_min: f.air_min == null ? null : Math.round(f.air_min),
    block_min: f.block_min == null ? null : Math.round(f.block_min),
    distance_nm: Math.round(Number(f.distance_nm) || 0),
    max_alt_ft: Math.round(Number(f.max_alt_ft) || 0),
    // 0 (or climbing) is no measurement: the touchdown was missed, not a perfect one.
    landing_fpm: f.landed && Number(f.landing_vs_fpm) < 0 ? Math.round(f.landing_vs_fpm) : null,
    landed: !!f.landed,
    readback_pct: readbacks ? Math.round((100 * (Number(f.readbacks_correct) || 0)) / readbacks) : null,
    quote: quote && quote.text ? {
      label: MOMENT_LABELS[quote.kind] || String(quote.label || ""), station: String(quote.station || ""),
      mhz: typeof quote.mhz === "number" ? quote.mhz : null, text: String(quote.text).slice(0, MAX_QUOTE),
    } : null,
  };
}

// --- the shared page's extras: the flight's details, and a replay cut down to fit a public page -------------------

const MINI_POINTS = 400;
const MINI_LINES = 250;
const MINI_TEXT = 240;
const SPOKEN = new Set(["atc", "pilot", "copilot"]);
const RUNWAY = String.raw`(\d{1,2}[LRC]?)`;
const round = (v, places) => { const k = 10 ** places; return Math.round(Number(v) * k) / k; };
const nm = (a, b) => {  // great-circle distance, nautical miles
  const r = Math.PI / 180, dLat = (b[0] - a[0]) * r, dLon = (b[1] - a[1]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2;
  return 3440.065 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
};

/** The weather ATC gave in ``lines`` (the controller's calls near one airport): the last of each said. */
function weather(lines) {
  const out = {};
  for (const l of lines) {
    const wind = /\bwind (calm|variable|\d{3}) (?:at )?(\d{1,3})?/i.exec(l.text);
    if (wind) out.wind = /calm/i.test(wind[1]) ? "calm" : `${wind[1]}° at ${wind[2] || "?"} kt`.replace("variable°", "variable");
    const alt = /\baltimeter (\d{2}\.?\d{2})\b/i.exec(l.text) || /\bQNH (\d{3,4})\b/i.exec(l.text);
    if (alt) out.altimeter = alt[0].toLowerCase().startsWith("qnh") ? `QNH ${alt[1]}` : `${alt[1].replace(/^(\d{2})(\d{2})$/, "$1.$2")} inHg`;
    const info = /\binformation ([A-Z][a-z]+)\b/.exec(l.text);
    if (info) out.atis = info[1];
  }
  return out;
}

/**
 * A replay (docs/replay-format.md) cut down for a public page: a few hundred points of the path flown, the
 * radio calls, the phases, and what the page can tell about the flight (the planned route, the runways, the
 * weather ATC gave, the livery). Time is counted from the start of the replay, never the time of day.
 * @param {Record<string, any>} replay
 */
export function miniReplay(replay) {
  const tr = (replay && replay.track) || {};
  const n = Array.isArray(tr.t) ? tr.t.length : 0;
  const step = Math.max(1, n / MINI_POINTS);
  const track = [];  // [seconds, lat, lon, altitude ft, ground speed kt]
  const point = (i) => [Math.round(tr.t[i]), round(tr.lat[i], 4), round(tr.lon[i], 4), Math.round((Number(tr.alt[i]) || 0) / 10) * 10,
    Math.round(Number(tr.gs && tr.gs[i]) || 0)];
  for (let k = 0; k < n; k += step) track.push(point(Math.floor(k)));
  if (n > 1 && track[track.length - 1][0] !== Math.round(tr.t[n - 1])) track.push(point(n - 1));
  const radio = (Array.isArray(replay?.radio) ? replay.radio : [])
    .filter((l) => l && SPOKEN.has(l.kind) && typeof l.text === "string" && l.text.trim())
    .slice(0, MINI_LINES)
    .map((l) => {
      const line = { t: round(l.t, 1), who: l.kind, text: l.text.trim().slice(0, MINI_TEXT) };
      if (l.kind === "atc" && l.station) line.station = String(l.station).slice(0, 64);
      return line;
    });
  const phases = (Array.isArray(replay?.marks) ? replay.marks : [])
    .filter((m) => m && (m.kind === "phase" || m.kind === "takeoff" || m.kind === "landing"))
    .slice(0, 80).map((m) => ({ t: round(m.t, 1), text: String(m.text || "").slice(0, 60) }));

  // Which airport a call was near: the aircraft's position when it was made.
  const f = replay?.flight || {};
  const airports = replay?.airports || {};
  const at = (icao) => (airports[icao] ? [airports[icao].lat, airports[icao].lon] : null);
  const origin = at(f.origin), destination = at(f.destination);
  const where = (t) => {
    let lo = 0, hi = track.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (track[mid][0] <= t) lo = mid; else hi = mid - 1; }
    const a = track[lo], b = track[lo + 1];
    if (!a) return null;
    if (!b || b[0] <= a[0]) return [a[1], a[2]];
    const k = Math.min(1, Math.max(0, (t - a[0]) / (b[0] - a[0])));  // between the points either side
    return [a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];
  };
  const near = (t) => {
    const p = where(t);
    if (!p || !origin || !destination) return null;
    return nm(p, origin) <= nm(p, destination) ? "departure" : "arrival";
  };
  const atc = radio.filter((l) => l.who === "atc");
  const sideOf = (side) => atc.filter((l) => near(l.t) === side);
  const said = (re, lines) => { for (const l of lines) { const m = re.exec(l.text); if (m) return m[1]; } return ""; };
  const route = (Array.isArray(replay?.route) ? replay.route : []).slice(0, 120)
    .map((x) => ({ ident: String(x.ident || "").slice(0, 12), lat: round(x.lat, 3), lon: round(x.lon, 3) }));
  return {
    duration_s: Math.round(Number(f.duration_s) || (track.length ? track[track.length - 1][0] : 0)),
    track, radio, phases, route,
    livery: typeof f.livery === "string" ? f.livery.trim().slice(0, 64) : "",
    departure_runway: String(f.departure_runway || "") || said(new RegExp(String.raw`\brunway ${RUNWAY}\b[^.]*\bcleared for takeoff`, "i"), atc),
    arrival_runway: String(f.arrival_runway || "") || said(new RegExp(String.raw`\brunway ${RUNWAY}\b[^.]*\bcleared to land`, "i"), atc),
    weather: { departure: weather(sideOf("departure")), arrival: weather(sideOf("arrival")) },
  };
}

/**
 * What the shared page lists under the card: the aircraft, the livery, the runways, the weather ATC gave and
 * the route filed. From the logbook line, and the mini replay when the pilot put it on the page.
 * @param {Record<string, any>} f
 * @param {Record<string, any> | null} [mini]
 */
export function flightDetails(f, mini = null) {
  const m = mini || {};
  const skip = new Set(["TOC", "TOD", "T/C", "T/D", String(f.origin || ""), String(f.destination || "")]);
  const idents = (m.route || []).map((x) => x.ident).filter((x) => x && !skip.has(x) && !/^\d/.test(x));
  const clean = (w) => Object.fromEntries(Object.entries(w || {}).filter(([, v]) => v));
  return {
    aircraft: String(f.aircraft || "").slice(0, 40),
    livery: m.livery || "",
    departure_runway: String(f.departure_runway || "") || m.departure_runway || "",
    arrival_runway: String(f.arrival_runway || "") || m.arrival_runway || "",
    max_alt_ft: Math.round(Number(f.max_alt_ft) || 0),
    route: idents.slice(0, 60),
    weather: { departure: clean(m.weather?.departure), arrival: clean(m.weather?.arrival) },
  };
}

// Distances and times, made into something to picture.
const EARTH_NM = 21600;
const MOON_NM = 207600;
const NY_LONDON_NM = 3000;
const MARATHON_NM = 22.8;

export function distanceFraming(nm) {
  nm = Number(nm) || 0;
  if (nm >= MOON_NM) return `${(nm / MOON_NM).toFixed(1)}× the way to the Moon`;
  if (nm >= EARTH_NM) return `${(nm / EARTH_NM).toFixed(1)}× around the Earth, ${Math.round((100 * nm) / MOON_NM)}% of the way to the Moon`;
  if (nm >= NY_LONDON_NM) return `${(nm / NY_LONDON_NM).toFixed(1)}× New York to London`;
  if (nm >= MARATHON_NM * 2) return `${Math.round(nm / MARATHON_NM)} marathons, without the blisters`;
  return nm > 0 ? "A short hop, and it counts" : "";
}

export function hoursFraming(hours) {
  hours = Number(hours) || 0;
  if (hours >= 4) return `${Math.round(hours / 2)} feature films' worth of flying`;
  if (hours >= 1) return `${Math.round(hours * 60)} minutes aloft`;
  return hours > 0 ? "Every minute of it logged" : "";
}

export function duration(min) {
  if (min == null) return "";
  const m = Math.round(min);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}
