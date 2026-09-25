/**
 * ATC Wrapped: a week's, a month's or a year's flying, told as a handful of slides.
 *
 * Worked out when asked, from the logbook lines (a year is a few hundred rows, well inside a request), and
 * never stored: nothing here that the logbook doesn't already hold, except the standout radio moment, which
 * comes from the moments kept with uploaded replays. The period's bounds come from the pilot's app, in its
 * own time zone ("this month" starts at local midnight), with the offset for counting days.
 *
 * Every client draws the same slides from this: the dashboard, the desktop app and the phone. A slide that
 * would say nothing (a "top airport" visited once) is left out; a month with a single flight is shown as a
 * week's card, a year with a few as a month.
 */
import type { Auth } from "./auth";
import { distanceFraming, hoursFraming } from "./cardmodel";
import type { Env } from "./env";
import { HttpError, json } from "./http";

export type Period = "week" | "month" | "year";
const PERIODS: Period[] = ["week", "month", "year"];
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const MAX_SPAN_DAYS = 370;
const GREASER_FPM = -150; // a landing softer than this is a greaser
const MIN_FLIGHTS: Record<Period, number> = { week: 1, month: 2, year: 5 }; // fewer, and it's shown as the tier below

export type Range = { period: Period; from: string; to: string; tz: number; label: string; ref: string };

type Flight = {
  id: string; started_at: string; callsign: string; aircraft: string; origin: string; destination: string;
  air_min: number | null; block_min: number | null; distance_nm: number; max_alt_ft: number; landing_vs_fpm: number | null;
  readbacks: number; readbacks_correct: number; landed: number;
  origin_lat: number | null; origin_lon: number | null; destination_lat: number | null; destination_lon: number | null;
};
type Slide = { kind: string } & Record<string, unknown>;
type Obj = Record<string, unknown>;

/** The period asked for, checked: {period, from, to, tz?, label?} from a query string or a body. */
export function range(q: Obj): Range {
  const period = q.period as Period;
  if (!PERIODS.includes(period)) throw new HttpError(400, "The period is week, month or year.");
  const from = String(q.from ?? "");
  const to = String(q.to ?? "");
  if (!ISO.test(from) || !ISO.test(to) || Date.parse(to) <= Date.parse(from)) throw new HttpError(400, "The period's dates aren't right.");
  if (Date.parse(to) - Date.parse(from) > MAX_SPAN_DAYS * 86400_000) throw new HttpError(400, "That period is too long.");
  const tz = Math.max(-840, Math.min(840, Math.round(Number(q.tz ?? 0) || 0)));
  const label = typeof q.label === "string" && /^[\w ,.-]{1,24}$/.test(q.label) ? q.label : from.slice(0, 10);
  return { period, from, to, tz, label, ref: `${period}:${label}` };
}

const localDay = (iso: string, tz: number): string => new Date(Date.parse(iso) + tz * 60_000).toISOString().slice(0, 10);
const localHour = (iso: string, tz: number): number => new Date(Date.parse(iso) + tz * 60_000).getUTCHours();
const round1 = (x: number): number => Math.round(x * 10) / 10;
const brief = (f: Flight): Obj => ({ id: f.id, origin: f.origin, destination: f.destination, date: f.started_at.slice(0, 10),
  callsign: f.callsign, aircraft: f.aircraft, air_min: f.air_min, distance_nm: Math.round(f.distance_nm) });

function tally<T>(items: T[], key: (x: T) => string): [string, number][] {
  const counts = new Map<string, number>();
  for (const x of items) {
    const k = key(x);
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function longestStreak(days: string[]): { days: number; from: string; to: string } {
  const sorted = [...new Set(days)].sort();
  let best = { days: 0, from: "", to: "" };
  let start = 0;
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && Date.parse(sorted[i]) - Date.parse(sorted[i - 1]) !== 86400_000) start = i;
    if (i - start + 1 > best.days) best = { days: i - start + 1, from: sorted[start], to: sorted[i] };
  }
  return best;
}

/** A pilot type, for the year: what their flying says about them. */
function persona(flights: Flight[], tz: number, topRouteShare: number, airports: number): { name: string; blurb: string } {
  const n = flights.length;
  const avgNm = flights.reduce((s, f) => s + f.distance_nm, 0) / n;
  const local = flights.filter((f) => f.origin && f.origin === f.destination).length / n;
  const night = flights.filter((f) => { const h = localHour(f.started_at, tz); return h >= 20 || h < 5; }).length / n;
  if (local >= 0.4) return { name: "Pattern Rat", blurb: "Round and round the circuit: the landings are the point." };
  if (avgNm >= 1500) return { name: "Long-Haul Hauler", blurb: "Oceans, time zones and a lot of coffee." };
  if (night >= 0.4) return { name: "Night Owl", blurb: "Most of your flying happens after dark." };
  if (topRouteShare >= 0.4) return { name: "Creature of Habit", blurb: "You know that route better than the controllers do." };
  if (airports >= n * 1.2) return { name: "Explorer", blurb: "Always somewhere new." };
  return { name: "Regional Regular", blurb: "A steady mix of hops and hauls: the backbone of the airways." };
}

export type Recap = {
  period: Period; tier: Period; label: string; from: string; to: string; flights: number; hours: number; block_hours: number;
  distance_nm: number; landings: number; airports: { icao: string; visits: number; lat: number | null; lon: number | null; name: string }[];
  routes: { origin: string; destination: string; flights: number }[]; slides: Slide[]; previous: Obj | null;
  last_flight: Obj | null;
};

export async function compute(env: Env, auth: Auth, r: Range): Promise<Recap> {
  const user = auth.user.id;
  const { results: flights } = await env.DB.prepare(
    `SELECT id, started_at, callsign, aircraft, origin, destination, air_min, block_min, distance_nm, max_alt_ft, landing_vs_fpm,
            readbacks, readbacks_correct, landed, origin_lat, origin_lon, destination_lat, destination_lon
     FROM flights WHERE user_id = ?1 AND started_at >= ?2 AND started_at < ?3 ORDER BY started_at`,
  ).bind(user, r.from, r.to).all<Flight>();
  const span = Date.parse(r.to) - Date.parse(r.from);
  const prevFrom = new Date(Date.parse(r.from) - span).toISOString().replace(/\.\d{3}Z$/, "Z");
  const [prev, last, before, moments] = await Promise.all([
    env.DB.prepare(`SELECT COUNT(*) AS flights, SUM(readbacks) AS readbacks, SUM(readbacks_correct) AS correct, COALESCE(SUM(air_min), 0) AS air
                    FROM flights WHERE user_id = ?1 AND started_at >= ?2 AND started_at < ?3`).bind(user, prevFrom, r.from)
      .first<{ flights: number; readbacks: number | null; correct: number | null; air: number }>(),
    env.DB.prepare("SELECT id, started_at, origin, destination FROM flights WHERE user_id = ?1 AND started_at < ?2 ORDER BY started_at DESC LIMIT 1")
      .bind(user, r.to).first<Obj>(),
    env.DB.prepare(`SELECT DISTINCT icao FROM (SELECT origin AS icao FROM flights WHERE user_id = ?1 AND started_at < ?2
                    UNION SELECT destination FROM flights WHERE user_id = ?1 AND started_at < ?2) WHERE icao != ''`)
      .bind(user, r.from).all<{ icao: string }>(),
    env.DB.prepare(`SELECT r.flight_id, r.moments FROM replays r JOIN flights f ON f.user_id = r.user_id AND f.id = r.flight_id
                    WHERE r.user_id = ?1 AND f.started_at >= ?2 AND f.started_at < ?3 AND r.moments IS NOT NULL`)
      .bind(user, r.from, r.to).all<{ flight_id: string; moments: string }>(),
  ]);

  // Names for the airports, where an uploaded replay said them.
  const names: Record<string, string> = {};
  let standout: Obj | null = null;
  for (const m of moments.results) {
    const kept = JSON.parse(m.moments) as { moments?: Obj[]; names?: Record<string, string> };
    Object.assign(names, kept.names ?? {});
    const best = kept.moments?.[0];
    if (best && (!standout || Number(best.score) > Number(standout.score))) {
      const f = flights.find((x) => x.id === m.flight_id);
      standout = { ...best, flight: f ? brief(f) : null };
    }
  }

  const n = flights.length;
  const air = flights.reduce((s, f) => s + (f.air_min ?? 0), 0);
  const block = flights.reduce((s, f) => s + (f.block_min ?? 0), 0);
  const nm = Math.round(flights.reduce((s, f) => s + f.distance_nm, 0));
  const readbacks = flights.reduce((s, f) => s + f.readbacks, 0);
  const correct = flights.reduce((s, f) => s + f.readbacks_correct, 0);
  const landed = flights.filter((f) => f.landed && f.landing_vs_fpm != null && f.landing_vs_fpm < 0);
  const coords = new Map<string, [number | null, number | null]>();
  for (const f of flights) {
    if (f.origin) coords.set(f.origin, [f.origin_lat, f.origin_lon]);
    if (f.destination) coords.set(f.destination, [f.destination_lat, f.destination_lon]);
  }
  const visits = tally(flights.flatMap((f) => [f.origin, f.destination]), (x) => x);
  const airports = visits.map(([icao, v]) => ({ icao, visits: v, lat: coords.get(icao)?.[0] ?? null, lon: coords.get(icao)?.[1] ?? null, name: names[icao] ?? "" }));
  const routeTally = tally(flights.filter((f) => f.origin && f.destination), (f) => `${f.origin}>${f.destination}`);
  const routes = routeTally.slice(0, 60).map(([k, c]) => { const [o, d] = k.split(">"); return { origin: o, destination: d, flights: c }; });
  const hours = round1(air / 60);

  const tier: Period = n >= MIN_FLIGHTS[r.period] ? r.period
    : r.period === "year" && n >= MIN_FLIGHTS.month ? "month" : "week";
  const recap: Recap = {
    period: r.period, tier, label: r.label, from: r.from, to: r.to, flights: n, hours, block_hours: round1(block / 60),
    distance_nm: nm, landings: flights.filter((f) => f.landed).length, airports, routes, slides: [],
    previous: prev && prev.flights ? { flights: prev.flights, hours: round1(prev.air / 60),
      readback_accuracy: prev.readbacks ? round1((100 * (prev.correct ?? 0)) / prev.readbacks) : null } : null,
    last_flight: last ? { ...last, days_ago: Math.floor((Date.now() - Date.parse(String(last.started_at))) / 86400_000) } : null,
  };
  if (!n) return recap;

  const softest = landed.reduce<Flight | null>((b, f) => (!b || (f.landing_vs_fpm ?? -1e9) > (b.landing_vs_fpm ?? -1e9) ? f : b), null);
  const greasers = landed.filter((f) => (f.landing_vs_fpm ?? -1e9) > GREASER_FPM).length;
  const longest = flights.reduce((b, f) => ((f.air_min ?? 0) > (b.air_min ?? 0) ? f : b), flights[0]);
  const types = tally(flights, (f) => f.aircraft);
  const accuracy = readbacks ? round1((100 * correct) / readbacks) : null;
  const top = airports[0];
  const topRoute = routes[0];
  const seen = new Set(before.results.map((x) => x.icao));
  const newAirports = airports.filter((a) => !seen.has(a.icao)).map((a) => a.icao);
  const kind = persona(flights, r.tz, topRoute ? topRoute.flights / n : 0, airports.length);
  const summary: Slide = {
    kind: "summary", label: r.label, flights: n, hours, distance_nm: nm, airport_count: airports.length,
    top_airport: top && top.visits >= 2 ? top.icao : null, best_landing_fpm: softest?.landing_vs_fpm ?? null,
    persona: tier === "year" ? kind.name : null,
  };
  const s: Slide[] = [];
  const add = (slide: Slide | null, ...tiers: Period[]) => { if (slide && tiers.includes(tier)) s.push(slide); };

  add({ kind: "intro", label: r.label, period: r.period, flights: n }, "month", "year");
  add({ kind: "totals", flights: n, hours, block_hours: recap.block_hours, landings: recap.landings, hours_framing: hoursFraming(hours) }, "month", "year");
  add(nm ? { kind: "distance", distance_nm: nm, framing: distanceFraming(nm) } : null, "month", "year");
  add({ kind: "map", airports, routes }, "month", "year");
  if (tier === "year") {
    const months = tally(flights, (f) => localDay(f.started_at, r.tz).slice(0, 7));
    add(months.length > 1 ? { kind: "busiest_month", month: months[0][0], flights: months[0][1] } : null, "year");
  }
  add(top && top.visits >= 2 ? { kind: "top_airport", icao: top.icao, name: top.name, visits: top.visits } : null, "month", "year");
  add(topRoute && topRoute.flights >= 2 ? { kind: "top_route", ...topRoute } : null, "month", "year");
  add(newAirports.length && seen.size ? { kind: "new_airports", count: newAirports.length, icaos: newAirports.slice(0, 12) } : null, "year");
  add(types.length && types[0][1] >= 2 ? { kind: "aircraft", aircraft: types[0][0], flights: types[0][1], types: types.length } : null, "month", "year");
  add(n >= 2 && longest.air_min ? { kind: "longest", ...brief(longest) } : null, "month", "year");
  add(softest ? { kind: "best_landing", fpm: softest.landing_vs_fpm, greasers, landings: landed.length, flight: brief(softest) } : null, "month", "year");
  if (tier === "year") {
    const streak = longestStreak(flights.map((f) => localDay(f.started_at, r.tz)));
    add(streak.days >= 2 ? { kind: "streak", ...streak } : null, "year");
  }
  add(readbacks >= 5 ? { kind: "radio", accuracy, readbacks, previous: recap.previous?.readback_accuracy ?? null } : null, "month", "year");
  add(standout ? { ...standout, kind: "moment", moment: standout.kind } : null, "year");
  add(n >= 2 ? { kind: "first_last", first: brief(flights[0]), last: brief(flights[n - 1]) } : null, "year");
  add({ kind: "persona", ...kind }, "year");
  s.push(summary);
  recap.slides = s;
  return recap;
}

/** GET /v1/wrapped?period=&from=&to=&tz=&label= */
export async function get(env: Env, url: URL, auth: Auth): Promise<Response> {
  const recap = await compute(env, auth, range(Object.fromEntries(url.searchParams)));
  return json({ ...recap, card: recap.flights ? card(recap) : null }); // the summary card, as a share would show it
}

/** The public snapshot of a recap, for a shared summary card: the totals and the map, no flight in detail. */
export function card(r: Recap): Obj {
  const summary: Obj = r.slides.find((x) => x.kind === "summary") ?? {};
  return {
    kind: "wrapped", period: r.period, tier: r.tier, label: r.label, flights: r.flights, hours: r.hours,
    distance_nm: r.distance_nm, distance_framing: distanceFraming(r.distance_nm), landings: r.landings,
    airports: r.airports.slice(0, 40).map(({ icao, lat, lon, visits, name }) => ({ icao, lat, lon, visits, name })),
    routes: r.routes.slice(0, 40), top_airport: summary.top_airport ?? null, best_landing_fpm: summary.best_landing_fpm ?? null,
    persona: summary.persona ?? null,
  };
}
