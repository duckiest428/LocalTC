/**
 * Flight replays: the aircraft's track and the radio transcript of a flight in the logbook, uploaded by the
 * app only when the pilot asks (one flight, or the "upload replays" setting), for the dashboard and the
 * companion app to play. The format is the app's (docs/replay-format.md, v1). Everything is checked and
 * rebuilt field by field; what isn't expected is dropped. Stored gzipped, deleted with the flight.
 */
import type { Auth } from "./auth";
import type { Env } from "./env";
import { HttpError, json, now } from "./http";
import { airportNames, miniReplay, moments as pickMoments } from "./cardmodel";
import { limit } from "./rate";

const MAX_GZ = 1024 * 1024; // D1 rows top out at 2 MB
const MAX_JSON = 8 * 1024 * 1024;
const MAX_POINTS = 20000;
const MAX_LINES = 3000;
const MAX_MARKS = 500;
const MAX_FIXES = 400;
const TEXT = 2000; // an ATIS is long
const RADIO_KINDS = new Set(["atc", "pilot", "copilot", "atis", "tuned", "alert", "phase"]);
const MARK_KINDS = new Set(["phase", "alert", "handoff", "takeoff", "landing"]);
// Track column -> [min, max]
const COLUMNS: Record<string, [number, number]> = {
  t: [0, 7 * 86400], lat: [-90, 90], lon: [-180, 180], alt: [-2000, 100000], gs: [0, 3000], hdg: [0, 360],
  vs: [-30000, 30000], gnd: [0, 1],
};
const FLIGHT_TEXT = ["id", "callsign", "aircraft", "origin", "destination", "departure_runway", "arrival_runway",
  "departure_gate", "arrival_gate", "livery"] as const;

type Obj = Record<string, unknown>;

const bad = (what: string): never => {
  throw new HttpError(400, `The replay's ${what} isn't right.`);
};
const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown, min: number, max: number, what: string): number =>
  typeof v === "number" && Number.isFinite(v) && v >= min && v <= max ? v : bad(what);
const text = (v: unknown, max: number, what: string): string =>
  v === undefined || v === null ? "" : typeof v === "string" && v.length <= max ? v : bad(what);

/** A replay from the app, rebuilt from the fields this server knows. */
export function cleanReplay(raw: unknown): Obj {
  if (!isObj(raw) || raw.v !== 1) bad("version");
  const r = raw as Obj;
  const f = isObj(r.flight) ? r.flight : bad("flight");
  const flight: Obj = {};
  for (const k of FLIGHT_TEXT) flight[k] = text(f[k], 64, k);
  flight.started_at = typeof f.started_at === "string" && /^\d{4}-\d{2}-\d{2}T[\d:.]+Z$/.test(f.started_at) ? f.started_at : bad("start");
  flight.zulu0 = f.zulu0 === null || f.zulu0 === undefined ? null : num(f.zulu0, 0, 86400, "zulu");
  flight.duration_s = num(f.duration_s, 0, 7 * 86400, "duration");

  const airports: Obj = {};
  const a = isObj(r.airports) ? r.airports : {};
  for (const [icao, p] of Object.entries(a).slice(0, 10)) {
    if (!/^[A-Z0-9]{2,8}$/.test(icao) || !isObj(p)) bad("airports");
    const q = p as Obj;
    airports[icao] = { lat: num(q.lat, -90, 90, "airports"), lon: num(q.lon, -180, 180, "airports"), elev: num(q.elev ?? 0, -2000, 20000, "airports") };
    const name = text(q.name, 40, "airport name");
    if (name) (airports[icao] as Obj).name = name;
  }

  const route = (Array.isArray(r.route) ? r.route : []).slice(0, MAX_FIXES).map((x) => {
    if (!isObj(x)) bad("route");
    return { ident: text(x.ident, 12, "route"), lat: num(x.lat, -90, 90, "route"), lon: num(x.lon, -180, 180, "route") };
  });

  const t = isObj(r.track) ? r.track : bad("track");
  const n = Array.isArray(t.t) ? t.t.length : bad("track");
  if (n < 1 || n > MAX_POINTS) bad("track length");
  const track: Record<string, number[]> = {};
  for (const [k, [min, max]] of Object.entries(COLUMNS)) {
    const col = t[k];
    if (!Array.isArray(col) || col.length !== n) bad(`track ${k}`);
    track[k] = (col as unknown[]).map((v) => num(v, min, max, `track ${k}`));
  }

  const radio = (Array.isArray(r.radio) ? r.radio : bad("radio")).slice(0, MAX_LINES).map((x) => {
    if (!isObj(x) || typeof x.kind !== "string" || !RADIO_KINDS.has(x.kind)) bad("radio");
    const line: Obj = { kind: x.kind, t: num(x.t, 0, 7 * 86400, "radio time"), text: text(x.text, TEXT, "radio text") };
    if (x.station !== undefined) line.station = text(x.station, 120, "radio station");
    if (x.mhz !== undefined && x.mhz !== null) line.mhz = num(x.mhz, 0, 1000, "radio frequency");
    if (typeof x.ok === "boolean") line.ok = x.ok;
    if (x.readback !== undefined) line.readback = text(x.readback, 300, "readback");
    if (x.unclear === true) line.unclear = true;
    return line;
  });

  const marks = (Array.isArray(r.marks) ? r.marks : []).slice(0, MAX_MARKS).map((x) => {
    if (!isObj(x) || typeof x.kind !== "string" || !MARK_KINDS.has(x.kind)) bad("marks");
    return { t: num(x.t, 0, 7 * 86400, "mark time"), kind: x.kind, text: text(x.text, 200, "mark") };
  });
  return { v: 1, flight, airports, route, track, radio, marks };
}

async function gunzip(bytes: ArrayBuffer, max: number): Promise<string> {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) {
      await reader.cancel();
      throw new HttpError(413, "The replay is too big.");
    }
    chunks.push(value);
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.byteLength;
  }
  return new TextDecoder().decode(out);
}

async function gzip(text: string): Promise<ArrayBuffer> {
  return new Response(new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
}

async function flightExists(env: Env, auth: Auth, id: string): Promise<boolean> {
  return !!(await env.DB.prepare("SELECT 1 FROM flights WHERE user_id = ?1 AND id = ?2").bind(auth.user.id, id).first());
}

export async function put(env: Env, request: Request, auth: Auth, id: string): Promise<Response> {
  if (Number(request.headers.get("Content-Length") ?? "0") > MAX_GZ) throw new HttpError(413, "The replay is too big.");
  if (!(await flightExists(env, auth, id))) throw new HttpError(404, "Sync the flight's logbook line first.");
  await limit(env, `replay:${auth.user.id}`, 50, 86400);
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_GZ) throw new HttpError(413, "The replay is too big.");
  let raw: unknown;
  try {
    raw = JSON.parse(await gunzip(body, MAX_JSON));
  } catch (err) {
    if (err instanceof HttpError) throw err;
    throw new HttpError(400, "A replay is gzipped JSON.");
  }
  const replay = cleanReplay(raw);
  (replay.flight as Obj).id = id;
  const data = await gzip(JSON.stringify(replay));
  // The calls worth quoting, kept alongside: for sharing from the phone, and Wrapped's standout moment.
  const kept = JSON.stringify({ moments: pickMoments(replay.radio), names: airportNames(replay.airports) });
  // And the replay cut down for a shared page, should the pilot put it on one.
  const mini = JSON.stringify(miniReplay(replay));
  await env.DB.prepare(
    `INSERT INTO replays (user_id, flight_id, created_at, size, data, moments, mini) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
     ON CONFLICT(user_id, flight_id) DO UPDATE SET created_at = excluded.created_at, size = excluded.size, data = excluded.data,
       moments = excluded.moments, mini = excluded.mini`,
  ).bind(auth.user.id, id, now(), data.byteLength, data, kept, mini).run();
  return json({ ok: true, size: data.byteLength });
}

/** The replay as JSON (Cloudflare compresses it again on the way out). */
export async function get(env: Env, auth: Auth, id: string): Promise<Response> {
  const row = await env.DB.prepare("SELECT data FROM replays WHERE user_id = ?1 AND flight_id = ?2")
    .bind(auth.user.id, id).first<{ data: ArrayBuffer | number[] }>();
  if (!row) throw new HttpError(404, "No replay for this flight.");
  const bytes = row.data instanceof ArrayBuffer ? row.data : new Uint8Array(row.data).buffer;
  return new Response(await gunzip(bytes, MAX_JSON), {
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "private, max-age=3600" },
  });
}

export async function remove(env: Env, auth: Auth, id: string): Promise<Response> {
  const res = await env.DB.prepare("DELETE FROM replays WHERE user_id = ?1 AND flight_id = ?2").bind(auth.user.id, id).run();
  if (!res.meta.changes) throw new HttpError(404, "No replay for this flight.");
  return json({ ok: true });
}

/** The calls worth quoting on a shared card, and the airports' names, from the flight's uploaded replay. */
export async function moments(env: Env, auth: Auth, id: string): Promise<Response> {
  const row = await env.DB.prepare("SELECT moments, data FROM replays WHERE user_id = ?1 AND flight_id = ?2")
    .bind(auth.user.id, id).first<{ moments: string | null; data: ArrayBuffer | number[] }>();
  if (!row) return json({ moments: [], names: {} });
  if (row.moments) return json(JSON.parse(row.moments));
  // Uploaded before moments were kept: work them out once, and keep them.
  const bytes = row.data instanceof ArrayBuffer ? row.data : new Uint8Array(row.data).buffer;
  const replay = JSON.parse(await gunzip(bytes, MAX_JSON)) as Obj;
  const kept = { moments: pickMoments(replay.radio), names: airportNames(replay.airports) };
  await env.DB.prepare("UPDATE replays SET moments = ?1 WHERE user_id = ?2 AND flight_id = ?3").bind(JSON.stringify(kept), auth.user.id, id).run();
  return json(kept);
}

/** The flight's replay cut down for a shared page (cardmodel miniReplay), or null with no replay uploaded. */
export async function mini(env: Env, userId: string, id: string): Promise<Obj | null> {
  const row = await env.DB.prepare("SELECT mini FROM replays WHERE user_id = ?1 AND flight_id = ?2")
    .bind(userId, id).first<{ mini: string | null }>();
  if (!row) return null;
  if (row.mini) return JSON.parse(row.mini) as Obj;
  // Uploaded before these were kept: work it out once, and keep it.
  const full = await env.DB.prepare("SELECT data FROM replays WHERE user_id = ?1 AND flight_id = ?2")
    .bind(userId, id).first<{ data: ArrayBuffer | number[] }>();
  const bytes = full!.data instanceof ArrayBuffer ? full!.data : new Uint8Array(full!.data).buffer;
  const made = miniReplay(JSON.parse(await gunzip(bytes, MAX_JSON)) as Obj) as Obj;
  await env.DB.prepare("UPDATE replays SET mini = ?1 WHERE user_id = ?2 AND flight_id = ?3").bind(JSON.stringify(made), userId, id).run();
  return made;
}

/** Every replay in the account, for the export. */
export async function all(env: Env, auth: Auth): Promise<Obj[]> {
  const { results } = await env.DB.prepare("SELECT flight_id, data FROM replays WHERE user_id = ?1 ORDER BY flight_id")
    .bind(auth.user.id).all<{ flight_id: string; data: ArrayBuffer | number[] }>();
  const out: Obj[] = [];
  for (const r of results) {
    const bytes = r.data instanceof ArrayBuffer ? r.data : new Uint8Array(r.data).buffer;
    out.push(JSON.parse(await gunzip(bytes, MAX_JSON)));
  }
  return out;
}
