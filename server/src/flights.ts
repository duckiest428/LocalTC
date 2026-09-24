import type { Auth } from "./auth";
import type { Env } from "./env";
import { HttpError, json, now, readJson } from "./http";
import * as replays from "./replays";

const BATCH = 100;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;
const TEXT = ["callsign", "aircraft", "origin", "destination", "departure_gate", "arrival_gate",
  "departure_runway", "arrival_runway"] as const;
// Field -> [min, max, nullable]. A number outside is a broken client, not a flight.
const NUMBERS: Record<string, [number, number, boolean]> = {
  block_min: [0, 7 * 24 * 60, true],
  air_min: [0, 7 * 24 * 60, true],
  distance_nm: [0, 30000, false],
  max_alt_ft: [-2000, 100000, false],
  landing_vs_fpm: [-20000, 20000, true],
  readbacks: [0, 100000, false],
  readbacks_correct: [0, 100000, false],
  alerts: [0, 100000, false],
  origin_lat: [-90, 90, true],
  origin_lon: [-180, 180, true],
  destination_lat: [-90, 90, true],
  destination_lon: [-180, 180, true],
};
const COLUMNS = ["id", "started_at", "ended_at", ...TEXT, ...Object.keys(NUMBERS), "landed"];

type Flight = Record<string, string | number | null>;

/** A logbook line from the app, checked field by field. Anything else in it is dropped. */
export function clean(raw: unknown): Flight {
  if (!raw || typeof raw !== "object") throw new HttpError(400, "A flight must be an object.");
  const f = raw as Record<string, unknown>;
  const out: Flight = {};
  if (typeof f.id !== "string" || !/^[A-Za-z0-9-]{1,64}$/.test(f.id)) throw new HttpError(400, "A flight has a bad id.");
  out.id = f.id;
  for (const k of ["started_at", "ended_at"]) {
    if (typeof f[k] !== "string" || !ISO.test(f[k] as string)) throw new HttpError(400, `Flight ${f.id}: bad ${k}.`);
    out[k] = f[k] as string;
  }
  for (const k of TEXT) {
    const v = f[k] ?? "";
    if (typeof v !== "string" || v.length > 64) throw new HttpError(400, `Flight ${f.id}: bad ${k}.`);
    out[k] = v;
  }
  for (const [k, [min, max, nullable]] of Object.entries(NUMBERS)) {
    const v = f[k];
    if (v === null || v === undefined) {
      if (!nullable) throw new HttpError(400, `Flight ${f.id}: ${k} is missing.`);
      out[k] = null;
    } else if (typeof v !== "number" || !Number.isFinite(v) || v < min || v > max) {
      throw new HttpError(400, `Flight ${f.id}: bad ${k}.`);
    } else out[k] = v;
  }
  out.landed = f.landed ? 1 : 0;
  return out;
}

export async function upload(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson<{ flights?: unknown }>(request, 512 * 1024);
  if (!Array.isArray(body.flights) || body.flights.length > BATCH) {
    throw new HttpError(400, `Send up to ${BATCH} flights at a time.`);
  }
  const flights = body.flights.map(clean);
  const received = now();
  const placeholders = COLUMNS.map((_, i) => `?${i + 3}`).join(", ");
  const updates = COLUMNS.filter((c) => c !== "id").map((c) => `${c} = excluded.${c}`).join(", ");
  const sql = `INSERT INTO flights (user_id, received_at, ${COLUMNS.join(", ")}) VALUES (?1, ?2, ${placeholders})
               ON CONFLICT(user_id, id) DO UPDATE SET ${updates}`;
  if (flights.length) {
    await env.DB.batch(flights.map((f) => env.DB.prepare(sql).bind(auth.user.id, received, ...COLUMNS.map((c) => f[c]))));
  }
  return json({ accepted: flights.map((f) => f.id) });
}

export async function list(env: Env, url: URL, auth: Auth): Promise<Response> {
  const limitN = Math.min(Math.max(Number(url.searchParams.get("limit") ?? "50") || 50, 1), 500);
  const before = url.searchParams.get("before") ?? "9999";
  const { results } = await env.DB.prepare(
    `SELECT ${COLUMNS.join(", ")}, EXISTS (SELECT 1 FROM replays r WHERE r.user_id = flights.user_id AND r.flight_id = flights.id) AS has_replay
     FROM flights WHERE user_id = ?1 AND started_at < ?2 ORDER BY started_at DESC LIMIT ?3`,
  ).bind(auth.user.id, before, limitN + 1).all<Flight>();
  const page = results.slice(0, limitN).map((f) => ({ ...f, landed: !!f.landed, has_replay: !!f.has_replay }));
  return json({ flights: page, next: results.length > limitN ? results[limitN - 1].started_at : null });
}

export async function remove(env: Env, auth: Auth, id: string): Promise<Response> {
  const [res] = await env.DB.batch([
    env.DB.prepare("DELETE FROM flights WHERE user_id = ?1 AND id = ?2").bind(auth.user.id, id),
    env.DB.prepare("DELETE FROM replays WHERE user_id = ?1 AND flight_id = ?2").bind(auth.user.id, id),
  ]);
  if (!res.meta.changes) throw new HttpError(404, "No such flight.");
  return json({ ok: true });
}

/** The same totals as the app's logbook.totals. */
export async function stats(env: Env, auth: Auth): Promise<Response> {
  const t = await env.DB.prepare(
    `SELECT COUNT(*) AS flights, COALESCE(SUM(air_min), 0) AS air, COALESCE(SUM(block_min), 0) AS block,
            COALESCE(SUM(distance_nm), 0) AS distance, COALESCE(SUM(landed), 0) AS landings,
            AVG(landing_vs_fpm) AS avg_landing, SUM(readbacks) AS readbacks, SUM(readbacks_correct) AS correct
     FROM flights WHERE user_id = ?1`,
  ).bind(auth.user.id).first<Record<string, number | null>>();
  const { results: airports } = await env.DB.prepare(
    `SELECT icao, COUNT(*) AS visits, MAX(lat) AS lat, MAX(lon) AS lon FROM (
       SELECT origin AS icao, origin_lat AS lat, origin_lon AS lon FROM flights WHERE user_id = ?1 AND origin != ''
       UNION ALL SELECT destination, destination_lat, destination_lon FROM flights WHERE user_id = ?1 AND destination != '')
     GROUP BY icao ORDER BY visits DESC, icao`,
  ).bind(auth.user.id).all<{ icao: string; visits: number; lat: number | null; lon: number | null }>();
  const { results: routes } = await env.DB.prepare(
    `SELECT origin, destination, COUNT(*) AS flights FROM flights
     WHERE user_id = ?1 AND origin != '' AND destination != '' GROUP BY origin, destination ORDER BY flights DESC LIMIT 200`,
  ).bind(auth.user.id).all();
  const readbacks = Number(t?.readbacks ?? 0);
  return json({
    flights: t?.flights ?? 0,
    air_hours: Math.round(Number(t?.air ?? 0) / 6) / 10,
    block_hours: Math.round(Number(t?.block ?? 0) / 6) / 10,
    distance_nm: Math.round(Number(t?.distance ?? 0)),
    landings: t?.landings ?? 0,
    average_landing_fpm: t?.avg_landing == null ? null : Math.round(t.avg_landing),
    readback_accuracy: readbacks ? Math.round((Number(t?.correct ?? 0) / readbacks) * 1000) / 1000 : null,
    airports,
    routes,
  });
}

/** Everything the account holds, as one JSON file (GDPR access and portability). */
export async function exportAll(env: Env, auth: Auth): Promise<Response> {
  const { results } = await env.DB.prepare(`SELECT ${COLUMNS.join(", ")}, received_at FROM flights WHERE user_id = ?1 ORDER BY started_at`)
    .bind(auth.user.id).all<Flight>();
  const { results: sessions } = await env.DB.prepare("SELECT kind, device, created_at, last_used_at FROM sessions WHERE user_id = ?1")
    .bind(auth.user.id).all();
  const data = { exported_at: now(), account: { email: auth.user.email, created_at: auth.user.created_at, verified_at: auth.user.verified_at },
    devices: sessions, flights: results.map((f) => ({ ...f, landed: !!f.landed })), replays: await replays.all(env, auth) };
  return json(data, 200, { "Content-Disposition": 'attachment; filename="localtc-account.json"' });
}
