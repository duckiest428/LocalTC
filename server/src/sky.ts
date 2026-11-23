/**
 * Live Flights: the public map of LocalTC pilots who chose to share their flight (localtc.tech/live).
 *
 * Off unless the pilot turns it on (users.live_share: "flight" for this flight only, "always" for every flight),
 * and only with the account, so it can be stopped from the app or the dashboard and taken down if abused.
 * The app sends a small heartbeat every 10 s while a flight runs; what it may carry is SKY_KEYS and nothing else.
 * The pilot can hide the route and their name, and delay what's shown by 5, 10 or 15 minutes.
 *
 * One Durable Object (SkyRoom, "public") holds the flights on the map: in memory, and in its own storage only while
 * they're on it (never in D1, the account's database). A flight leaves the map, and that storage, when it ends, when
 * sharing is turned off, when the app closes, or 90 s after its last heartbeat.
 * The map's snapshot (GET /v1/sky/flights) is cached at the edge for SKY_CACHE_S, so viewers don't reach the room.
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
const MAX_BODY = 16384; // a heartbeat with the route's fixes in it
const MAX_FIXES = 150;
const HISTORY_EVERY_MS = 10_000; // a delayed flight's positions are kept one per this long
// The path flown, as the app's map keeps it: a point every ~0.3 nm in the air and ~12 m on the ground, so the taxi keeps
// its turns. Past MAX_TRAIL points the older half is thinned to every other point.
const TRAIL_AIR_NM = 0.3;
const TRAIL_GROUND_NM = 0.0065;
const MAX_TRAIL = 1500;
export const PHASES = ["parked", "taxi", "takeoff", "climb", "cruise", "descent", "approach", "landing", "landed"] as const;

/** Everything a heartbeat may carry. The pilot's name is the server's own (users.display_name), never the app's. */
export const SKY_KEYS = ["callsign", "type", "airline", "from", "to", "from_ll", "to_ll", "route", "fixes", "rules", "phase",
  "lat", "lon", "alt", "gs", "hdg", "ground", "station", "mhz"] as const;

/** A fix of the filed route, for drawing it: sent once a flight (and again if the room asks), not with every beat. */
export interface Fix { ident: string; lat: number; lon: number; kind?: string }
/** A label an admin gave the account ("Streamer", "Contributor"), shown on its flight's card. */
export interface Tag { label: string; color: string }
export const TAG_COLORS = ["green", "blue", "purple", "amber", "red", "pink", "teal", "gray"] as const;
export const MAX_TAGS = 4;

export interface SkyFlight {
  callsign: string; type?: string; airline?: string; from?: string; to?: string; route?: string; fixes?: Fix[];
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
  const airline = code(raw.airline, /^[A-Z0-9]{2}$/, 2); // its IATA code, for the logo
  if (airline) out.airline = airline;
  if (Array.isArray(raw.fixes)) {
    const fixes: Fix[] = [];
    for (const f of raw.fixes.slice(0, MAX_FIXES)) {
      if (!f || typeof f !== "object") continue;
      const o = f as Record<string, unknown>, la = finite(o.lat), lo = finite(o.lon), id = code(o.ident, /^[A-Z0-9]{1,7}$/, 7);
      if (la === undefined || lo === undefined || Math.abs(la) > 90 || Math.abs(lo) > 180 || !id) continue;
      const fix: Fix = { ident: id, lat: Math.round(la * 1e4) / 1e4, lon: Math.round(lo * 1e4) / 1e4 };
      if (typeof o.kind === "string" && /^[a-z]{1,8}$/.test(o.kind)) fix.kind = o.kind;
      fixes.push(fix);
    }
    if (fixes.length >= 2) out.fixes = fixes;
  }
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
export interface Meta { name: string; hideRoute: boolean; hideName: boolean; delayMin: number; tags?: Tag[] }

/** An account's tags, from users.tags (JSON), checked again on the way out. */
export function readTags(raw: unknown): Tag[] {
  let list: unknown = raw;
  if (typeof raw === "string") { try { list = JSON.parse(raw); } catch { return []; } }
  if (!Array.isArray(list)) return [];
  return list.slice(0, MAX_TAGS).flatMap((t) => {
    const o = t && typeof t === "object" ? (t as Record<string, unknown>) : {};
    const label = typeof o.label === "string" ? o.label.normalize("NFC").replace(/\s+/g, " ").trim() : "";
    if (!label || label.length > 20 || !/^[\p{L}\p{N}][\p{L}\p{N} .&+'-]*$/u.test(label)) return [];
    const color = (TAG_COLORS as readonly string[]).includes(String(o.color)) ? String(o.color) : "gray";
    return [{ label, color }];
  });
}

type Moving = Pick<SkyFlight, "lat" | "lon" | "alt" | "gs" | "hdg" | "ground" | "phase" | "station" | "mhz">;
const MOVING = ["lat", "lon", "alt", "gs", "hdg", "ground", "phase", "station", "mhz"] as const;

export interface Held {
  id: string; uid: string; meta: Meta; flight: SkyFlight; last: number; started: number;
  history: { t: number; at: Moving }[]; // for a delay: where it was, one point per HISTORY_EVERY_MS
  trail?: [number, number, number][]; // the path flown: [time, lat, lon]
}

const nmBetween = (lat1: number, lon1: number, lat2: number, lon2: number): number => {
  const r = Math.PI / 180, dLat = (lat2 - lat1) * r, dLon = (lon2 - lon1) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dLon / 2) ** 2;
  return 3440 * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
};

/** A point added to the path flown when the aircraft has moved far enough from the last one. */
function addTrail(held: Held, f: SkyFlight, now: number): void {
  if (!Number.isFinite(f.lat) || !Number.isFinite(f.lon)) return;
  const trail = (held.trail ??= []);
  const last = trail[trail.length - 1];
  const step = f.ground ? TRAIL_GROUND_NM : TRAIL_AIR_NM;
  if (last && nmBetween(last[1], last[2], f.lat, f.lon) < step) return;
  trail.push([now, Math.round(f.lat * 1e5) / 1e5, Math.round(f.lon * 1e5) / 1e5]);
  if (trail.length > MAX_TRAIL) {
    const half = Math.floor(trail.length / 2);
    held.trail = [...trail.slice(0, half).filter((_, i) => i % 2 === 0), ...trail.slice(half)];
  }
}

/** What the map shows of one flight. No account id, no email: a random id made for this flight. */
export interface Shown extends Partial<SkyFlight> { id: string; name: string; delay_min: number; tags?: Tag[]; has_route?: boolean; has_track?: boolean }

/** The map's flights, kept in memory. Pure, with the time passed in, so the tests can run the clock. */
export class Sky {
  private flights = new Map<string, Held>();
  private byUser = new Map<string, string>();
  private lastBeat = new Map<string, number>();
  private ipHits = new Map<string, { minute: number; count: number }>();

  // What changed since the room last saved: flights to write, and the ids to delete.
  private dirty = new Set<string>();
  private gone = new Set<string>();

  get size(): number { return this.flights.size; }

  private keep(id: string, held: Held): void { this.flights.set(id, held); this.dirty.add(id); this.gone.delete(id); }

  private drop(id: string): boolean {
    const had = this.flights.delete(id);
    if (had) { this.gone.add(id); this.dirty.delete(id); }
    return had;
  }

  /** The changes since the last call, for the room's storage. */
  changes(): { put: Held[]; del: string[] } {
    const out = { put: [...this.dirty].map((id) => this.flights.get(id)).filter((h): h is Held => !!h), del: [...this.gone] };
    this.dirty.clear();
    this.gone.clear();
    return out;
  }

  /** The flights the room saved, after it was evicted from memory and started again. */
  restore(saved: Held[], now: number): void {
    for (const held of saved) {
      this.flights.set(held.id, held);
      this.byUser.set(held.uid, held.id);
    }
    this.prune(now);
  }

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
      this.drop(held.id);
      held = undefined;
    }
    if (!held) {
      if (this.flights.size >= MAX_FLIGHTS) throw new HttpError(503, "Live Flights is full right now. Try again later.");
      held = { id: newId(), uid, meta, flight, last: now, started: now, history: [] };
      this.keep(held.id, held);
      this.byUser.set(uid, held.id);
    }
    held.meta = meta;
    held.flight = { ...flight, fixes: flight.fixes ?? held.flight.fixes };
    held.last = now;
    this.dirty.add(held.id);
    const at = Object.fromEntries(MOVING.filter((k) => flight[k] !== undefined).map((k) => [k, flight[k]])) as Moving;
    const top = held.history[held.history.length - 1];
    if (!top || now - top.t >= HISTORY_EVERY_MS) held.history.push({ t: now, at });
    else top.at = at; // the newest point stays the latest position
    const keep = now - (Math.max(...DELAYS) + 1) * 60_000;
    while (held.history.length > 1 && held.history[1].t <= keep) held.history.shift();
    addTrail(held, flight, now);
    return held.id;
  }

  /** The pilot's choices changed on the dashboard or in the app: at once, not at the next heartbeat. */
  setMeta(uid: string, meta: Partial<Meta>): void {
    const held = this.flights.get(this.byUser.get(uid) ?? "");
    if (held) { held.meta = { ...held.meta, ...meta }; this.dirty.add(held.id); }
  }

  /** The account's flight off the map (flight ended, sharing off, app closed, banned). */
  stop(uid: string): boolean {
    const id = this.byUser.get(uid);
    this.byUser.delete(uid);
    return id !== undefined && this.drop(id);
  }

  /** A flight taken down by an admin: the account behind it, for a ban. */
  remove(id: string): string | null {
    const held = this.flights.get(id);
    if (!held) return null;
    this.drop(id);
    if (this.byUser.get(held.uid) === id) this.byUser.delete(held.uid);
    return held.uid;
  }

  prune(now: number): void {
    for (const [id, held] of this.flights) {
      if (now - held.last > STALE_MS) {
        this.drop(id);
        if (this.byUser.get(held.uid) === id) this.byUser.delete(held.uid);
      }
    }
    for (const [uid, t] of this.lastBeat) if (now - t > STALE_MS) this.lastBeat.delete(uid);
    const minute = Math.floor(now / 60_000);
    for (const [ip, h] of this.ipHits) if (h.minute !== minute) this.ipHits.delete(ip);
  }

  /** A flight's filed route, fix by fix, and the path flown so far (as far as the delay shows it), unless the pilot
   * hides the route. */
  routeOf(id: string, now: number): { fixes: Fix[] | null; trail: [number, number][] } | null {
    const held = this.flights.get(id);
    if (!held || held.meta.hideRoute) return null;
    const cut = now - held.meta.delayMin * 60_000;
    const trail = (held.trail ?? []).filter((p) => p[0] <= cut).map((p) => [p[1], p[2]] as [number, number]);
    return { fixes: held.flight.fixes ?? null, trail };
  }

  /** Whether the account's flight here has its fixes (else the app is asked for them). */
  hasFixes(uid: string): boolean {
    return !!this.flights.get(this.byUser.get(uid) ?? "")?.flight.fixes;
  }

  /** For the admin panel: every flight with the account behind it. */
  all(now: number): { id: string; uid: string; callsign: string; from?: string; to?: string; last: number; started: number }[] {
    this.prune(now);
    return [...this.flights.values()].map((h) => ({ id: h.id, uid: h.uid, callsign: h.flight.callsign, from: h.flight.from,
      to: h.flight.to, last: h.last, started: h.started }));
  }

  /** Everything off the map (Live Flights paused). */
  clear(): void {
    for (const id of [...this.flights.keys()]) this.drop(id);
    this.byUser.clear();
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
      for (const k of ["type", "airline", "from", "to"] as const) if (flight[k]) shown[k] = flight[k];
      if (meta.tags?.length) shown.tags = meta.tags;
      if (!meta.hideRoute && flight.fixes) shown.has_route = true;
      if (!meta.hideRoute) shown.has_track = true;
      if (flight.from_ll) shown.from_ll = flight.from_ll;
      if (flight.to_ll) shown.to_ll = flight.to_ll;
      if (!meta.hideRoute && flight.route) shown.route = flight.route;
      delete shown.fixes;
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
  // The flights on the map, in memory, and in this room's own storage so they survive the room being evicted from
  // memory between two heartbeats (Cloudflare does that after a few idle seconds). Only what's on the map now: a
  // flight is deleted from the storage the moment it leaves the map, and an alarm clears any left when nothing comes in.
  private sky = new Sky();

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      const saved = await ctx.storage.list<Held>({ prefix: "f:" });
      this.sky.restore([...saved.values()], Date.now());
      await this.save();
    });
  }

  /** What changed, written; what left the map, deleted; and the alarm to tidy up after the last heartbeat. */
  private async save(): Promise<void> {
    const { put, del } = this.sky.changes();
    if (del.length) await this.ctx.storage.delete(del.map((id) => `f:${id}`));
    if (put.length) await this.ctx.storage.put(Object.fromEntries(put.map((h) => [`f:${h.id}`, h])));
    if (put.length && (await this.ctx.storage.getAlarm()) === null) await this.ctx.storage.setAlarm(Date.now() + STALE_MS + 5_000);
  }

  async alarm(): Promise<void> {
    this.sky.prune(Date.now());
    await this.save();
    if (this.sky.size) await this.ctx.storage.setAlarm(Date.now() + STALE_MS + 5_000);
  }

  async fetch(request: Request): Promise<Response> {
    try {
      return await this.handle(request);
    } finally {
      await this.save();
    }
  }

  private async handle(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const now = Date.now();
    const body = request.method === "GET" ? {} : ((await request.json()) as Record<string, unknown>);
    switch (`${request.method} ${url.pathname}`) {
      case "PUT /beat": {
        try {
          const id = this.sky.beat(String(body.uid), String(body.ip), body.meta as Meta, body.flight as SkyFlight, now);
          return json({ ok: true, flight_id: id, need_fixes: !this.sky.hasFixes(String(body.uid)) });
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
      case "GET /all":
        return json({ flights: this.sky.all(now) });
      case "POST /clear":
        this.sky.clear();
        return json({ ok: true });
    }
    const route = /^\/route\/([0-9a-f]{16})$/.exec(url.pathname);
    if (route && request.method === "GET") {
      const out = this.sky.routeOf(route[1], now);
      return out ? json(out) : json({ error: "No route for that flight." }, 404);
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

/** Live Flights on for everyone: not paused in wrangler.toml (LIVE_SKY) nor from the admin panel (server_flags). */
export async function enabled(env: Env): Promise<boolean> {
  if ((env.LIVE_SKY ?? "on") === "off") return false;
  const row = await env.DB.prepare("SELECT value FROM server_flags WHERE key = 'live_sky_paused'").first<{ value: string }>();
  return row?.value !== "1";
}

/** The pilot's sharing choices, as GET /v1/me shows them. */
export function choices(user: Auth["user"], paused = false): Record<string, unknown> {
  return {
    live_share: (MODES as readonly string[]).includes(user.live_share ?? "") ? user.live_share : "off",
    live_hide_route: !!user.live_hide_route, live_hide_name: !!user.live_hide_name,
    live_delay_min: (DELAYS as readonly number[]).includes(Number(user.live_delay_min)) ? Number(user.live_delay_min) : 0,
    live_blocked: !!user.live_banned, // an admin turned this account's sharing off
    live_paused: paused, // Live Flights is off for everyone (LIVE_SKY, or the admin panel)
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
  await setMeta(env, auth.user.id, meta);
}

/** An account's flight shown differently at once (its name, choices or tags changed). */
export async function setMeta(env: Env, uid: string, meta: Partial<Meta>): Promise<void> {
  if (Object.keys(meta).length) await send(env, "POST", "/meta", { uid, meta });
}

/** PUT /v1/sky/flight: the app's heartbeat while a shared flight runs. */
export async function beat(env: Env, request: Request, auth: Auth): Promise<Response> {
  const user = auth.user;
  const mode = (MODES as readonly string[]).includes(user.live_share ?? "") ? (user.live_share as Mode) : "off";
  if (!(await enabled(env))) return json({ ok: false, mode, error: "Live Flights is paused for now." }, 503);
  if (user.live_banned) return json({ ok: false, mode: "off", error: "Sharing is turned off for this account." }, 403);
  if (mode === "off") return json({ ok: false, mode, error: "Sharing is off. Turn it on in Settings, Account." }, 403);
  const flight = cleanSkyFlight(await readJson(request, MAX_BODY));
  const meta: Meta = {
    name: user.display_name ?? "", hideRoute: !!user.live_hide_route, hideName: !!user.live_hide_name,
    delayMin: (DELAYS as readonly number[]).includes(Number(user.live_delay_min)) ? Number(user.live_delay_min) : 0,
    tags: readTags(user.tags),
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
  const key = new Request(new URL("/v1/sky/flights", request.url).toString());
  const cache = ttl > 0 ? await caches.open("sky") : null;
  const hit = cache ? await cache.match(key) : undefined;
  if (hit) return new Response(hit.body, hit);
  if (!(await enabled(env))) return json({ flights: [], generated_at: new Date().toISOString(), paused: true }, 200, headers);
  const res = await send(env, "GET", "/snapshot");
  const out = json(await res.json(), 200, headers);
  if (cache) await cache.put(key, out.clone());
  return out;
}

/** GET /v1/sky/flights/:id/route: a flight's filed route, fix by fix, and the path it has flown (unless the pilot
 * hides the route), for its card. The path grows, so it's cached only as long as the snapshot. */
export async function route(env: Env, id: string): Promise<Response> {
  const res = await send(env, "GET", `/route/${id}`);
  const ttl = Math.max(0, Number(env.SKY_CACHE_S ?? 10));
  return json(await res.json(), res.status, { "Cache-Control": `public, max-age=${ttl}`, "Access-Control-Allow-Origin": "*" });
}

/** For the admin panel (src/admin.ts): the flights on the map with their accounts, and pausing it all. */
export async function adminFlights(env: Env): Promise<{ id: string; uid: string; callsign: string; last: number; started: number }[]> {
  return ((await (await send(env, "GET", "/all")).json()) as { flights: [] }).flights;
}

export async function clearAll(env: Env): Promise<void> {
  await send(env, "POST", "/clear", {});
}

/** The server's admins (ADMINS in wrangler.toml), with a confirmed address. */
export function isAdmin(env: Env, auth: Auth): boolean {
  if (!auth.user.verified_at) return false;
  return (env.ADMINS ?? env.LIVE_ADMINS ?? "").split(",").map((e) => e.trim().toLowerCase()).filter(Boolean).includes(auth.user.email.toLowerCase());
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

/** GET /v1/logo/:iata : an airline's logo for Live Flights' cards (pics.avs.io's PNG, as the app shows it), through
 * here so a viewer's browser asks no one else, and readable by the page (it clears the white round it). A week cached. */
export async function logo(iata: string): Promise<Response> {
  const res = await fetch(`https://pics.avs.io/200/60/${iata}.png`, {
    headers: { "User-Agent": "LocalTC" }, cf: { cacheTtl: 7 * 86400, cacheEverything: true },
  } as RequestInit);
  const type = res.headers.get("Content-Type") ?? "";
  if (!res.ok || !type.startsWith("image/")) return json({ error: "No logo for that airline." }, 404, { "Cache-Control": "public, max-age=86400" });
  return new Response(res.body, { headers: { "Content-Type": type, "Cache-Control": "public, max-age=604800",
    "Access-Control-Allow-Origin": "*", "Cross-Origin-Resource-Policy": "cross-origin" } });
}
