import type { Env } from "./env";
import { HttpError, json } from "./http";
import { clientIp, limit } from "./rate";

/**
 * The departures due from an airport, for LocalTC's traffic (src/localtc/ops/schedules.py): the gates' aircraft before
 * they push back. No account or key from the pilot: this server holds one free OpenSky Network account, and every pilot
 * at an airport shares one answer, kept here for BLOCK_S.
 *
 * OpenSky's flights are history, so this is yesterday's, at the same hours: airlines fly the same flights day after day,
 * so yesterday's 14:05 is today's. A free account has 4,000 credits a day and this costs 30 a fetch, so no more than
 * MAX_FETCHES_A_DAY; past it, or with no secrets, the app is told there are none and the gates have only the real
 * aircraft whose transponders are on.
 *
 * Secrets: OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET (wrangler secret put). Migration 0011.
 */
const TOKEN = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token";
const DEPARTURES = "https://opensky-network.org/api/flights/departure";
const DAY_S = 86400;
const BLOCK_S = 3 * 3600; // an airport's answer is kept this long
const WINDOW_S = 6 * 3600; // ... and covers this long, from the start of its block
const FAILED_S = 15 * 60; // a fetch that failed isn't tried again for this long
const MAX_FETCHES_A_DAY = 120;
const SEEN_AFTER_OFF_BLOCK_S = 15 * 60; // OpenSky first sees a flight about at takeoff: off the gate this long before
const AIRLINE_FLIGHT = /^[A-Z]{3}[0-9][A-Z0-9]*$/; // not a registration: a private flight isn't a schedule

/** How this server reaches OpenSky (tests replace it). */
export const openskyHttp = { send: (request: Request): Promise<Response> => fetch(request) };

interface Row { callsign: string; off_t: number; destination: string; hex: string }

async function fetchDepartures(env: Env, icao: string, begin: number): Promise<Row[]> {
  const token = await openskyHttp.send(new Request(TOKEN, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "client_credentials", client_id: env.OPENSKY_CLIENT_ID!, client_secret: env.OPENSKY_CLIENT_SECRET!,
    }).toString(),
  }));
  if (!token.ok) throw new Error(`OpenSky sign-in: ${token.status}`);
  const { access_token: bearer } = await token.json() as { access_token: string };
  const url = `${DEPARTURES}?airport=${icao}&begin=${begin}&end=${begin + WINDOW_S}`;
  const res = await openskyHttp.send(new Request(url, { headers: { Authorization: `Bearer ${bearer}` } }));
  if (res.status === 404) return []; // OpenSky's way of saying no flights in the window
  if (!res.ok) throw new Error(`OpenSky departures: ${res.status}`);
  const flights = await res.json() as { callsign?: string; firstSeen?: number; estArrivalAirport?: string; icao24?: string }[];
  const rows: Row[] = [];
  for (const f of flights) {
    const callsign = (f.callsign ?? "").trim().toUpperCase();
    if (!AIRLINE_FLIGHT.test(callsign) || !f.firstSeen) continue;
    rows.push({ callsign, off_t: f.firstSeen + DAY_S - SEEN_AFTER_OFF_BLOCK_S, destination: f.estArrivalAirport ?? "",
                hex: (f.icao24 ?? "").toLowerCase() });
  }
  return rows;
}

async function underBudget(env: Env): Promise<boolean> {
  try {
    await limit(env, "opensky-fetches", MAX_FETCHES_A_DAY, DAY_S);
    return true;
  } catch (err) {
    if (err instanceof HttpError) return false;
    throw err;
  }
}

/** GET /v1/departures/LEMD: {"departures": [{callsign, off_t, destination, hex}], "source": "OpenSky Network"} */
export async function departures(env: Env, request: Request, icao: string): Promise<Response> {
  await limit(env, `departures:${clientIp(request)}`, 120, 3600);
  const none = { departures: [] as Row[], source: "OpenSky Network" };
  const nowS = Math.floor(Date.now() / 1000);
  const block = Math.floor(nowS / BLOCK_S) * BLOCK_S;
  const kept = await env.DB.prepare("SELECT body FROM departures_cache WHERE icao = ?1 AND block = ?2 AND expires_at > ?3")
    .bind(icao, block, nowS).first<{ body: string }>();
  if (kept) return json({ ...none, departures: JSON.parse(kept.body) });
  if (!env.OPENSKY_CLIENT_ID || !env.OPENSKY_CLIENT_SECRET || !await underBudget(env)) return json(none);
  let rows: Row[] = [];
  let expires = block + BLOCK_S;
  try {
    rows = await fetchDepartures(env, icao, block - DAY_S);
  } catch (err) {
    console.error(err);
    expires = nowS + FAILED_S;
  }
  await env.DB.prepare("INSERT OR REPLACE INTO departures_cache (icao, block, body, expires_at) VALUES (?1, ?2, ?3, ?4)")
    .bind(icao, block, JSON.stringify(rows), expires).run();
  return json({ ...none, departures: rows });
}

export async function cleanup(env: Env): Promise<void> {
  await env.DB.prepare("DELETE FROM departures_cache WHERE expires_at <= ?1").bind(Math.floor(Date.now() / 1000)).run();
}
