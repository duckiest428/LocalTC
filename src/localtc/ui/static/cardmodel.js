/**
 * What goes on a shared card, as plain data: one definition for the dashboard, the desktop app and the
 * account server (which imports this file), so a card previewed in the app is the card the public sees.
 *
 * - moments(radio): the controller's calls worth quoting, best first (a flight card's quote, and Wrapped's
 *   standout moment). Only the controller's words: they're what the pilot heard, never speech-to-text garble.
 * - flightCard(flight, {quote, names}): a logbook line as the public snapshot of it. Nothing private: no
 *   gates, no times of day, no track, one line of the transcript at most.
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
