/**
 * Live Flights: the public map of LocalTC pilots who chose to share their flight (localtc.tech/live).
 *
 * Off unless the pilot turns it on (users.live_share: "flight" for this flight only, "always" for every flight),
 * and only with the account, so it can be stopped from the app or the dashboard and taken down if abused.
 * The app sends a small heartbeat every 10 s while a flight runs; what it may carry is SKY_KEYS and nothing else.
 * The pilot can hide the route and their name, and delay what's shown by 5, 10 or 15 minutes.
 *
 * Everything is held in memory in one Durable Object (SkyRoom, "public"): never written to storage or D1. A flight
 * leaves the map when it ends, when sharing is turned off, when the app closes, or 90 s after its last heartbeat.
 * The map's snapshot (GET /v1/sky/flights) is cached at the edge for SKY_CACHE_S, so viewers don't reach the room.
 * An idle room is evicted from memory (70-140 s with nothing coming in; much sooner under `wrangler dev`): its flights
 * go with it and come back with their next heartbeat, under a new id (a delayed one after its delay again).
 */
import { DurableObject } from "cloudflare:workers";
import type { Auth } from "./auth";
import type { Env } from "./env";
import { HttpError, json, readJson } from "./http";
import { clientIp } from "./rate";

export const STALE_MS = 90_000; // no heartbeat for this long: off the map
export const BEAT_MIN_MS = 4_000; // an account's heartbeats at most this often
export const IP_BEATS_PER_MIN = 20;
export const MAX_FLIGHTS = 1000;
export const DELAYS = [0, 5, 10, 15] as const; // minutes
export const MODES = ["off", "flight", "always"] as const;
export type Mode = (typeof MODES)[number];
const MAX_BODY = 4096;
const HISTORY_EVERY_MS = 10_000; // a delayed flight's positions are kept one per this long
export const PHASES = ["parked", "taxi", "takeoff", "climb", "cruise", "descent", "approach", "landing", "landed"] as const;

/** Everything a heartbeat may carry. The pilot's name is the server's own (users.display_name), never the app's. */
export const SKY_KEYS = ["callsign", "type", "from", "to", "from_ll", "to_ll", "route", "rules", "phase", "lat", "lon", "alt",
  "gs", "hdg", "ground", "station", "mhz"] as const;

export interface SkyFlight {
  callsign: string; type?: string; from?: string; to?: string; route?: string;
  from_ll?: [number, number]; to_ll?: [number, number]; // the airports' own positions, for the line between them
  rules: "IFR" | "VFR"; phase?: string;
  lat: number; lon: number; alt: number; gs: number; hdg: number; ground: boolean; station?: string; mhz?: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const finite = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const code = (v: unknown, re: RegExp, max: number): string | undefined => {
  if (typeof v !== "string") return undefined;
  const s = v.trim().toUpperCase().slice(0, max);
  return re.test(s) ? s : undefined;
};

/** The heartbeat, cleaned: unknown fields dropped, numbers clamped, strings checked against what they may be. */
export function cleanSkyFlight(raw: Record<string, unknown>): SkyFlight {
  const lat = finite(raw.lat), lon = finite(raw.lon);
  if (lat === undefined || lon === undefined || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    throw new HttpError(400, "Send the position as lat and lon.");
  }
  const callsign = code(raw.callsign, /^[A-Z0-9]{2,8}$/, 8);
  if (!callsign) throw new HttpError(400, "Send the flight's callsign.");
  const out: SkyFlight = {
    callsign,
    rules: raw.rules === "VFR" ? "VFR" : "IFR",
    lat: Math.round(lat * 1e4) / 1e4,
    lon: Math.round(lon * 1e4) / 1e4,
    alt: Math.round(clamp(finite(raw.alt) ?? 0, -1500, 60000)),
    gs: Math.round(clamp(finite(raw.gs) ?? 0, 0, 1200)),
    hdg: Math.round((((finite(raw.hdg) ?? 0) % 360) + 360) % 360) % 360,
    ground: raw.ground === true,
  };
  const type = code(raw.type, /^[A-Z0-9]{2,4}$/, 4);
  if (type) out.type = type;
  const from = code(raw.from, /^[A-Z0-9]{3,4}$/, 4), to = code(raw.to, /^[A-Z0-9]{3,4}$/, 4);
  if (from) out.from = from;
  if (to) out.to = to;
  for (const k of ["from_ll", "to_ll"] as const) {
    const p = raw[k];
    if (Array.isArray(p) && p.length === 2 && finite(p[0]) !== undefined && finite(p[1]) !== undefined
        && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180) out[k] = [Math.round(p[0] * 1e3) / 1e3, Math.round(p[1] * 1e3) / 1e3];
  }
  if (typeof raw.route === "string") {
    const route = raw.route.toUpperCase().replace(/\s+/g, " ").trim().slice(0, 400);
    if (route && /^[A-Z0-9/ .-]+$/.test(route)) out.route = route;
  }
  if (typeof raw.phase === "string" && (PHASES as readonly string[]).includes(raw.phase)) out.phase = raw.phase;
  if (typeof raw.station === "string") {
    const station = raw.station.normalize("NFC").replace(/\s+/g, " ").trim().slice(0, 40);
    if (station && /^[\p{L}\p{M}\p{N} .'()/-]+$/u.test(station)) out.station = station;
  }
  const mhz = finite(raw.mhz);
  if (mhz !== undefined && mhz >= 118 && mhz <= 136.975) out.mhz = Math.round(mhz * 1000) / 1000;
  return out;
}

/** What the pilot chose, from their account: shown with the flight. */
export interface Meta { name: string; hideRoute: boolean; hideName: boolean; delayMin: number }

type Moving = Pick<SkyFlight, "lat" | "lon" | "alt" | "gs" | "hdg" | "ground" | "phase" | "station" | "mhz">;
const MOVING = ["lat", "lon", "alt", "gs", "hdg", "ground", "phase", "station", "mhz"] as const;

interface Held {
  id: string; uid: string; meta: Meta; flight: SkyFlight; last: number; started: number;
  history: { t: number; at: Moving }[]; // for a delay: where it was, one point per HISTORY_EVERY_MS
}

/** What the map shows of one flight. No account id, no email: a random id made for this flight. */
export interface Shown extends Partial<SkyFlight> { id: string; name: string; delay_min: number }

/** The map's flights, kept in memory. Pure, with the time passed in, so the tests can run the clock. */
export class Sky {
  private flights = new Map<string, Held>();
  private byUser = new Map<string, string>();
  private lastBeat = new Map<string, number>();
  private ipHits = new Map<string, { minute: number; count: number }>();

  get size(): number { return this.flights.size; }

  beat(uid: string, ip: string, meta: Meta, flight: SkyFlight, now: number): string {
    this.prune(now);
    const prev = this.lastBeat.get(uid);
    if (prev !== undefined && now - prev < BEAT_MIN_MS) throw new HttpError(429, "Too many updates. Every 10 seconds is plenty.");
    const minute = Math.floor(now / 60_000);
    const hits = this.ipHits.get(ip);
    if (hits && hits.minute === minute && hits.count >= IP_BEATS_PER_MIN) throw new HttpError(429, "Too many updates from this address.");
    this.ipHits.set(ip, hits && hits.minute === minute ? { minute, count: hits.count + 1 } : { minute, count: 1 });
    this.lastBeat.set(uid, now);

    let held = this.flights.get(this.byUser.get(uid) ?? "");
    // One shared flight per account: a new callsign or route is a new flight, under a new id.
    if (held && (held.flight.callsign !== flight.callsign || held.flight.from !== flight.from || held.flight.to !== flight.to)) {
      this.flights.delete(held.id);
      held = undefined;
    }
    if (!held) {
      if (this.flights.size >= MAX_FLIGHTS) throw new HttpError(503, "Live Flights is full right now. Try again later.");
      held = { id: newId(), uid, meta, flight, last: now, started: now, history: [] };
      this.flights.set(held.id, held);
      this.byUser.set(uid, held.id);
    }
    held.meta = meta;
    held.flight = flight;
    held.last = now;
    const at = Object.fromEntries(MOVING.filter((k) => flight[k] !== undefined).map((k) => [k, flight[k]])) as Moving;
    const top = held.history[held.history.length - 1];
    if (!top || now - top.t >= HISTORY_EVERY_MS) held.history.push({ t: now, at });
    else top.at = at; // the newest point stays the latest position
    const keep = now - (Math.max(...DELAYS) + 1) * 60_000;
    while (held.history.length > 1 && held.history[1].t <= keep) held.history.shift();
    return held.id;
  }

  /** The pilot's choices changed on the dashboard or in the app: at once, not at the next heartbeat. */
  setMeta(uid: string, meta: Partial<Meta>): void {
    const held = this.flights.get(this.byUser.get(uid) ?? "");
    if (held) held.meta = { ...held.meta, ...meta };
  }

  /** The account's flight off the map (flight ended, sharing off, app closed, banned). */
  stop(uid: string): boolean {
    const id = this.byUser.get(uid);
    this.byUser.delete(uid);
    return id !== undefined && this.flights.delete(id);
  }

  /** A flight taken down by an admin: the account behind it, for a ban. */
  remove(id: string): string | null {
    const held = this.flights.get(id);
    if (!held) return null;
    this.flights.delete(id);
    if (this.byUser.get(held.uid) === id) this.byUser.delete(held.uid);
    return held.uid;
  }

  prune(now: number): void {
    for (const [id, held] of this.flights) {
      if (now - held.last > STALE_MS) {
        this.flights.delete(id);
        if (this.byUser.get(held.uid) === id) this.byUser.delete(held.uid);
      }
    }
    for (const [uid, t] of this.lastBeat) if (now - t > STALE_MS) this.lastBeat.delete(uid);
    const minute = Math.floor(now / 60_000);
    for (const [ip, h] of this.ipHits) if (h.minute !== minute) this.ipHits.delete(ip);
  }

  snapshot(now: number): { flights: Shown[]; generated_at: string } {
    this.prune(now);
    const flights: Shown[] = [];
    for (const held of this.flights.values()) {
      const { meta, flight } = held;
      let at: Moving = flight;
      if (meta.delayMin > 0) {
        const cut = now - meta.delayMin * 60_000;
        const past = [...held.history].reverse().find((h) => h.t <= cut);
        if (!past) continue; // not shared yet: the delay hasn't passed since it started
        at = past.at;
      }
      const shown: Shown = {
        id: held.id, name: meta.hideName || !meta.name ? "LocalTC pilot" : meta.name, delay_min: meta.delayMin,
        callsign: flight.callsign, rules: flight.rules,
      };
      for (const k of ["type", "from", "to"] as const) if (flight[k]) shown[k] = flight[k];
      if (flight.from_ll) shown.from_ll = flight.from_ll;
      if (flight.to_ll) shown.to_ll = flight.to_ll;
      if (!meta.hideRoute && flight.route) shown.route = flight.route;
      for (const k of MOVING) if (at[k] !== undefined) (shown as unknown as Record<string, unknown>)[k] = at[k];
      flights.push(shown);
    }
    return { flights, generated_at: new Date(now).toISOString() };
  }
}

function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export class SkyRoom extends DurableObject<Env> {
  // In memory only. Never written to this.ctx.storage.
  private sky = new Sky();

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const now = Date.now();
    const body = request.method === "GET" ? {} : ((await request.json()) as Record<string, unknown>);
    switch (`${request.method} ${url.pathname}`) {
      case "PUT /beat": {
        try {
          const id = this.sky.beat(String(body.uid), String(body.ip), body.meta as Meta, body.flight as SkyFlight, now);
          return json({ ok: true, flight_id: id });
        } catch (err) {
          if (err instanceof HttpError) return json({ error: err.message }, err.status);
          throw err;
        }
      }
      case "POST /meta":
        this.sky.setMeta(String(body.uid), body.meta as Partial<Meta>);
        return json({ ok: true });
      case "POST /stop":
        return json({ ok: true, removed: this.sky.stop(String(body.uid)) });
      case "POST /remove":
        return json({ uid: this.sky.remove(String(body.id)) });
      case "GET /snapshot":
        return json(this.sky.snapshot(now));
    }
    return json({ error: "Not found." }, 404);
  }
}

// --- The Worker's side ----------------------------------------------------------------------------------------

function room(env: Env): DurableObjectStub {
  return env.SKY.get(env.SKY.idFromName("public"));
}

async function send(env: Env, method: string, path: string, body?: unknown): Promise<Response> {
  return room(env).fetch(`https://sky${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

const enabled = (env: Env) => (env.LIVE_SKY ?? "on") !== "off";

/** The pilot's sharing choices, as GET /v1/me shows them. */
export function choices(env: Env, user: Auth["user"]): Record<string, unknown> {
  return {
    live_share: (MODES as readonly string[]).includes(user.live_share ?? "") ? user.live_share : "off",
    live_hide_route: !!user.live_hide_route, live_hide_name: !!user.live_hide_name,
    live_delay_min: (DELAYS as readonly number[]).includes(Number(user.live_delay_min)) ? Number(user.live_delay_min) : 0,
    live_blocked: !!user.live_banned, // an admin turned this account's sharing off
    live_paused: !enabled(env), // Live Flights is off for everyone (LIVE_SKY)
  };
}

/** The sharing fields of a PATCH /v1/me, checked: only what's sent changes. */
export function readChoices(body: Record<string, unknown>): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  if ("live_share" in body) {
    if (!(MODES as readonly unknown[]).includes(body.live_share)) throw new HttpError(400, "live_share is off, flight or always.");
    out.live_share = body.live_share as string;
  }
  for (const k of ["live_hide_route", "live_hide_name"] as const) {
    if (k in body) {
      if (typeof body[k] !== "boolean") throw new HttpError(400, `${k} is true or false.`);
      out[k] = body[k] ? 1 : 0;
    }
  }
  if ("live_delay_min" in body) {
    if (!(DELAYS as readonly unknown[]).includes(body.live_delay_min)) throw new HttpError(400, "live_delay_min is 0, 5, 10 or 15.");
    out.live_delay_min = body.live_delay_min as number;
  }
  return out;
}

/** After the account's choices changed: off takes the flight down now, the rest applies at once. */
export async function applied(env: Env, auth: Auth, changed: Record<string, string | number>, name: string): Promise<void> {
  if (changed.live_share === "off") {
    await send(env, "POST", "/stop", { uid: auth.user.id });
    return;
  }
  const meta: Partial<Meta> = {};
  if ("live_hide_route" in changed) meta.hideRoute = !!changed.live_hide_route;
  if ("live_hide_name" in changed) meta.hideName = !!changed.live_hide_name;
  if ("live_delay_min" in changed) meta.delayMin = Number(changed.live_delay_min);
  if ("display_name" in changed) meta.name = name;
  if (Object.keys(meta).length) await send(env, "POST", "/meta", { uid: auth.user.id, meta });
}

/** PUT /v1/sky/flight: the app's heartbeat while a shared flight runs. */
export async function beat(env: Env, request: Request, auth: Auth): Promise<Response> {
  const user = auth.user;
  const mode = (MODES as readonly string[]).includes(user.live_share ?? "") ? (user.live_share as Mode) : "off";
  if (!enabled(env)) return json({ ok: false, mode, error: "Live Flights is paused for now." }, 503);
  if (user.live_banned) return json({ ok: false, mode: "off", error: "Sharing is turned off for this account." }, 403);
  if (mode === "off") return json({ ok: false, mode, error: "Sharing is off. Turn it on in Settings, Account." }, 403);
  const flight = cleanSkyFlight(await readJson(request, MAX_BODY));
  const meta: Meta = {
    name: user.display_name ?? "", hideRoute: !!user.live_hide_route, hideName: !!user.live_hide_name,
    delayMin: (DELAYS as readonly number[]).includes(Number(user.live_delay_min)) ? Number(user.live_delay_min) : 0,
  };
  const res = await send(env, "PUT", "/beat", { uid: user.id, ip: clientIp(request), meta, flight });
  const out = (await res.json()) as Record<string, unknown>;
  return json({ ...out, mode }, res.status);
}

/** DELETE /v1/sky/flight: off the map now. {ended: true} at a flight's end: "this flight only" is over too. */
export async function stop(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request, 1024).catch(() => ({}) as Record<string, unknown>);
  await send(env, "POST", "/stop", { uid: auth.user.id });
  let mode = auth.user.live_share ?? "off";
  if (body.ended === true && mode === "flight") {
    await env.DB.prepare("UPDATE users SET live_share = 'off' WHERE id = ?1").bind(auth.user.id).run();
    mode = "off";
  }
  return json({ ok: true, mode });
}

/** GET /v1/sky/flights: the map, for anyone. Cached at the edge, so a crowd of viewers costs the room nothing. */
export async function flights(env: Env, request: Request): Promise<Response> {
  const ttl = Number(env.SKY_CACHE_S ?? "10");
  const headers = { "Cache-Control": `public, max-age=${ttl}`, "Access-Control-Allow-Origin": "*" };
  if (!enabled(env)) return json({ flights: [], generated_at: new Date().toISOString(), paused: true }, 200, headers);
  const key = new Request(new URL("/v1/sky/flights", request.url).toString());
  const cache = ttl > 0 ? await caches.open("sky") : null;
  const hit = cache ? await cache.match(key) : undefined;
  if (hit) return new Response(hit.body, hit);
  const res = await send(env, "GET", "/snapshot");
  const out = json(await res.json(), 200, headers);
  if (cache) await cache.put(key, out.clone());
  return out;
}

export function isAdmin(env: Env, auth: Auth): boolean {
  if (!auth.user.verified_at) return false;
  return (env.LIVE_ADMINS ?? "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean).includes(auth.user.email.toLowerCase());
}

/** DELETE /v1/sky/flights/:id (admins): one flight off the map. */
export async function takeDown(env: Env, auth: Auth, id: string): Promise<Response> {
  if (!isAdmin(env, auth)) throw new HttpError(403, "Only Live Flights' admins can do that.");
  const res = (await (await send(env, "POST", "/remove", { id })).json()) as { uid: string | null };
  if (!res.uid) throw new HttpError(404, "That flight isn't on the map.");
  return json({ ok: true });
}

/** POST /v1/sky/ban {flight_id | email, banned} (admins): an account's sharing turned off for good, or back on. */
export async function ban(env: Env, request: Request, auth: Auth): Promise<Response> {
  if (!isAdmin(env, auth)) throw new HttpError(403, "Only Live Flights' admins can do that.");
  const body = await readJson(request, 1024);
  const banned = body.banned !== false;
  let uid: string | null = null;
  if (typeof body.flight_id === "string") {
    uid = ((await (await send(env, "POST", "/remove", { id: body.flight_id })).json()) as { uid: string | null }).uid;
  } else if (typeof body.email === "string") {
    const row = await env.DB.prepare("SELECT id FROM users WHERE email = ?1").bind(body.email.trim().toLowerCase()).first<{ id: string }>();
    uid = row?.id ?? null;
  }
  if (!uid) throw new HttpError(404, "No such flight or account.");
  await env.DB.prepare(banned ? "UPDATE users SET live_banned = 1, live_share = 'off' WHERE id = ?1" : "UPDATE users SET live_banned = 0 WHERE id = ?1")
    .bind(uid).run();
  if (banned) await send(env, "POST", "/stop", { uid });
  return json({ ok: true, banned });
}

/** The account deleted: its flight off the map. */
export async function forget(env: Env, uid: string): Promise<void> {
  await send(env, "POST", "/stop", { uid });
}
