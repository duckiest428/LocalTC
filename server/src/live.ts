import { DurableObject } from "cloudflare:workers";
import type { Auth } from "./auth";
import { es256Jwt } from "./crypto";
import type { Env } from "./env";
import { HttpError, allowedOrigin, json, now, readJson, str } from "./http";
import { limit } from "./rate";

/*
 * The companion app's live view, one LiveRoom (Durable Object) per account.
 *
 * - The flight's status (phase, frequencies, ATC's last line) is kept in storage, so a phone opening the
 *   app sees the latest even after the room was evicted.
 * - Position, traffic, the radio log, the flight's airports, its route and the ATC zones the app's Live Map
 *   draws arrive only while a phone (or the website's Flight Tracker) is watching through the server (the
 *   desktop checks the watcher count in every answer). They're held in memory and passed on, never
 *   written to storage: when the room is evicted, they're gone.
 * - Where the phone can reach the PC on the local network, and the key it needs there, is stored until the
 *   desktop replaces it.
 *
 * - A radio call typed on the phone or the website ("POST /v1/live/say") waits here, in memory, for the desktop to
 *   pick up with its next update (or its poll while somebody watches) and transmit, as if typed in the app; one
 *   not picked up within CALL_TTL_MS is dropped (the PC is off, or the flight is over).
 *
 * Everything the phone receives over the WebSocket is {type, data}, as in docs/companion-protocol.md.
 */

const STALE_MS = 10 * 60_000; // no word from the app for this long: the flight is over (or the PC is off)
const CONNECT_MS = 24 * 3600_000; // local-network details older than this are dropped
const MAX_STATUS = 8 * 1024;
const MAX_FRAME = 64 * 1024;
const RADIO_KEEP = 50;
const TRAIL_KEEP = 2000; // points of the path flown, for a map opened mid-flight (thinned beyond: ``thin``)
const TRAIL_STEP_DEG = 0.002; // a new point every ~200 m of movement
const MAX_AIRPORTS = 32 * 1024;
const MAX_MAP = 384 * 1024; // the route and the zones: a long flight crosses a dozen centres' outlines
const CALL_TTL_MS = 60_000; // a radio call from the phone or website the desktop hasn't picked up by then is dropped
const CALLS_KEEP = 5; // calls waiting at once (more is somebody typing faster than ATC can answer)
const CALL_MAX = 300; // characters in one call, as the desktop's own box takes

type Station = { station?: string; mhz?: number } | null;
export interface Status {
  active: boolean;
  callsign?: string;
  aircraft?: string;
  origin?: string;
  destination?: string;
  phase?: string;
  phase_label?: string;
  squawk?: string;
  altitude_ft?: number;
  runway?: string;
  gate?: string;
  rules?: "IFR" | "VFR";
  tuned?: Station;
  next?: Station;
  ete?: { nm: number; min: number } | null;
  last_atc?: { station?: string; mhz?: number; text?: string } | null;
  updated_at?: string;
}

const STATUS_KEYS = ["active", "callsign", "aircraft", "origin", "destination", "phase", "phase_label", "squawk",
  "altitude_ft", "runway", "gate", "rules", "tuned", "next", "ete", "last_atc"] as const;
const OWN_KEYS = ["t", "lat", "lon", "alt", "agl", "hdg", "hdg_mag", "gs", "vs", "ground", "com1", "com2", "squawk"] as const;
const TRAFFIC_KEYS = ["id", "callsign", "type", "lat", "lon", "alt", "hdg", "gs", "ground"] as const;
const RADIO_KEYS = ["kind", "t", "station", "mhz", "text", "ok", "level", "unclear", "radio"] as const;
const ALERT_KINDS = ["handoff", "clearance", "traffic", "emergency"];

function pick(raw: unknown, keys: readonly string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!raw || typeof raw !== "object") return out;
  for (const k of keys) if (k in (raw as object)) out[k] = (raw as Record<string, unknown>)[k];
  return out;
}

/** Only the fields the companion shows; anything else sent is dropped here. */
export function cleanStatus(raw: Record<string, unknown>): Status {
  const out = pick(raw, STATUS_KEYS);
  out.active = !!raw.active;
  if ("rules" in out) out.rules = out.rules === "VFR" ? "VFR" : "IFR";
  if (JSON.stringify(out).length > MAX_STATUS) throw new HttpError(413, "The status is too big.");
  return out as unknown as Status;
}

export type Point = [number, number];
export interface Frame { own?: Record<string, unknown>; traffic?: Record<string, unknown>[]; trail?: Point[] }

const isLatLon = (p: unknown): p is Point => Array.isArray(p) && p.length === 2
  && typeof p[0] === "number" && typeof p[1] === "number" && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180;

export function cleanFrame(raw: Record<string, unknown>): Frame {
  const out: Frame = {};
  if (raw.own) out.own = pick(raw.own, OWN_KEYS);
  if (Array.isArray(raw.traffic)) out.traffic = raw.traffic.slice(0, 300).map((t) => pick(t, TRAFFIC_KEYS));
  // The path flown so far, which the app sends when someone starts watching mid-flight: [[lat, lon], ...].
  if (Array.isArray(raw.trail)) {
    out.trail = thin(raw.trail.slice(0, 8 * TRAIL_KEEP).filter(isLatLon)
      .map(([lat, lon]) => [Math.round(lat * 1e4) / 1e4, Math.round(lon * 1e4) / 1e4] as Point));
  }
  if (JSON.stringify(out).length > MAX_FRAME) throw new HttpError(413, "Too much traffic at once.");
  return out;
}

/** Every other point (the last kept) until the path fits TRAIL_KEEP: all of the flight, less dense. Cutting the
 * oldest points off instead moved the start of the line along behind a long flight. */
export function thin(path: Point[]): Point[] {
  let out = path;
  while (out.length > TRAIL_KEEP) out = out.filter((_, i) => i % 2 === 0 && i !== out.length - 1).concat([out[out.length - 1]]);
  return out;
}

export function cleanRadio(raw: unknown): Record<string, unknown>[] {
  if (!Array.isArray(raw)) throw new HttpError(400, "Send the radio lines as a list.");
  return raw.slice(-RADIO_KEEP).map((line) => {
    const out = pick(line, RADIO_KEYS);
    if (typeof out.text === "string") out.text = out.text.slice(0, 600);
    return out;
  });
}

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj | null => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null);
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);
const text = (v: unknown, max = 80): string | undefined => (typeof v === "string" ? v.slice(0, max) : undefined);
const flag = (v: unknown): boolean => v === true;
const points = (v: unknown, max: number): Point[] => (Array.isArray(v) ? v.slice(0, max).filter(isLatLon) : []);
function list<T>(v: unknown, max: number, each: (x: Obj) => T): T[] {
  return Array.isArray(v) ? v.slice(0, max).map(obj).filter((x): x is Obj => x !== null).map(each) : [];
}
const station = (s: Obj) => ({ station: text(s.station), controller: text(s.controller, 20), mhz: num(s.mhz) });

export interface LiveMap { route?: Obj | null; zones?: Obj | null }

/** The flight plan's route and the Live Map's ATC layer (the desktop's ui/zones.py), field by field. */
export function cleanMap(raw: Obj): LiveMap {
  const out: LiveMap = {};
  if ("route" in raw) {
    const r = obj(raw.route);
    out.route = r && {
      origin: text(r.origin, 8), destination: text(r.destination, 8),
      fixes: list(r.fixes, 600, (f) => ({ ident: text(f.ident, 12), lat: num(f.lat), lon: num(f.lon), kind: text(f.kind, 8) }))
        .filter((f) => f.lat !== undefined && f.lon !== undefined),
    };
  }
  if ("zones" in raw) {
    const z = obj(raw.zones);
    const area = (a: Obj) => ({
      id: text(a.id), name: text(a.name), kind: text(a.kind, 20), label: isLatLon(a.label) ? a.label : null,
      rings: (Array.isArray(a.rings) ? a.rings.slice(0, 12) : []).map((ring) => points(ring, 2000)),
      active: flag(a.active), route: flag(a.route), working: flag(a.working), icao: text(a.icao, 8), role: text(a.role, 20),
    });
    const final = obj(z?.final), taxi = obj(z?.taxi), gate = obj(z?.gate), tuned = obj(z?.tuned), next = obj(z?.next);
    out.zones = z && {
      rules: z.rules === "VFR" ? "VFR" : "IFR", center: text(z.center),
      tuned: tuned && station(tuned), next: next && station(next),
      centers: list(z.centers, 60, area), terminals: list(z.terminals, 8, area),
      final: final && { icao: text(final.icao, 8), runway: text(final.runway, 8), ring: points(final.ring, 8) },
      taxi: taxi && { icao: text(taxi.icao, 8), to: text(taxi.to), points: points(taxi.points, 800),
        taxiways: (Array.isArray(taxi.taxiways) ? taxi.taxiways.slice(0, 40) : []).map((t) => text(t, 12)).filter(Boolean) },
      gate: gate && { icao: text(gate.icao, 8), name: text(gate.name), lat: num(gate.lat), lon: num(gate.lon) },
      airports: list(z.airports, 4, (a) => ({
        icao: text(a.icao, 8), name: text(a.name), lat: num(a.lat), lon: num(a.lon), role: text(a.role, 20), tower_nm: num(a.tower_nm),
        stations: list(a.stations, 24, (s) => ({ ...station(s), tuned: flag(s.tuned), next: flag(s.next) })),
        runways: list(a.runways, 24, (r) => ({ name: text(r.name, 12), lat: num(r.lat), lon: num(r.lon),
          heading_true: num(r.heading_true), length_m: num(r.length_m) })),
      })),
      classes: list(z.classes, 40, (a) => ({
        icao: text(a.icao, 8), name: text(a.name), lat: num(a.lat), lon: num(a.lon), class: text(a.class, 4), label: text(a.label),
        towered: flag(a.towered), rings: list(a.rings, 4, (r) => ({ nm: num(r.nm), floor: num(r.floor), ceiling: num(r.ceiling) })),
      })),
    };
  }
  if (JSON.stringify(out).length > MAX_MAP) throw new HttpError(413, "The map is too big.");
  return out;
}

const AIRPORT_KEYS = ["icao", "name", "role", "lat", "lon", "elev_ft", "atis"] as const;
const FREQUENCY_KEYS = ["label", "kind", "mhz", "name"] as const;
const RUNWAY_KEYS = ["name", "length_ft", "heading_mag", "ils"] as const;

/** The flight's airports for the phone's Frequencies and Airports tabs: published data, nothing personal. */
export function cleanAirports(raw: unknown): Record<string, unknown>[] {
  if (!Array.isArray(raw)) throw new HttpError(400, "Send the airports as a list.");
  const out = raw.slice(0, 4).map((a) => {
    const airport = pick(a, AIRPORT_KEYS);
    const src = (a ?? {}) as Record<string, unknown>;
    airport.frequencies = Array.isArray(src.frequencies) ? src.frequencies.slice(0, 40).map((f) => pick(f, FREQUENCY_KEYS)) : [];
    airport.runways = Array.isArray(src.runways) ? src.runways.slice(0, 20).map((r) => {
      const runway = pick(r, RUNWAY_KEYS);
      runway.ils = Array.isArray(runway.ils) ? runway.ils.slice(0, 4).map(String) : [];
      return runway;
    }) : [];
    return airport;
  });
  if (JSON.stringify(out).length > MAX_AIRPORTS) throw new HttpError(413, "Too much airport data.");
  return out;
}

export function cleanAlert(raw: Record<string, unknown>): { kind: string; title: string; body: string; mhz?: number } {
  const kind = String(raw.kind ?? "");
  if (!ALERT_KINDS.includes(kind)) throw new HttpError(400, "Unknown alert kind.");
  return { kind, title: String(raw.title ?? "").slice(0, 120), body: String(raw.body ?? "").slice(0, 600),
    ...(typeof raw.mhz === "number" ? { mhz: raw.mhz } : {}) };
}

/** Private addresses only: this is for a phone on the PC's own network, never a public server. */
export function cleanConnect(raw: Record<string, unknown>): { lan: string[]; key: string } {
  const key = str(raw.key, "Key", 100);
  if (!/^[\w-]{20,100}$/.test(key)) throw new HttpError(400, "That isn't a companion key.");
  if (!Array.isArray(raw.lan)) throw new HttpError(400, "Send the addresses as a list.");
  const lan = raw.lan.slice(0, 8).filter((u): u is string => typeof u === "string" && isPrivateUrl(u));
  return { lan, key };
}

function isPrivateUrl(u: string): boolean {
  const m = /^http:\/\/(\d+)\.(\d+)\.(\d+)\.(\d+):(\d{1,5})$/.exec(u);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

/** What changed that deserves a notification on the phone: a handoff, or the flight ending. */
export function pushFor(before: Status | null, after: Status): { title: string; body: string } | null {
  const next = after.next?.station;
  if (after.active && next && next !== before?.next?.station) {
    return { title: `Contact ${next}`, body: `${after.next?.mhz?.toFixed(3) ?? ""} ${after.callsign ?? ""}`.trim() };
  }
  if (before?.active && !after.active) {
    return { title: "Flight ended", body: `${before.callsign ?? ""} ${before.origin ?? ""} → ${before.destination ?? ""}`.trim() };
  }
  return null;
}

/** A radio call to transmit: the words, trimmed to one line. */
export function cleanCall(raw: Record<string, unknown>): string {
  const text = typeof raw.text === "string" ? raw.text.replace(/\s+/g, " ").trim() : "";
  if (!text) throw new HttpError(400, "Nothing to say.");
  return text.slice(0, CALL_MAX);
}

export interface Call { id: string; text: string; at: number }

export class LiveRoom extends DurableObject<Env> {
  // In memory only. Never written to this.ctx.storage.
  private calls: Call[] = [];
  private own: Record<string, unknown> | null = null;
  private traffic: Record<string, unknown>[] = [];
  private radio: Record<string, unknown>[] = [];
  private airports: Record<string, unknown>[] = [];
  private trail: Point[] = [];
  private route: Obj | null = null;
  private zones: Obj | null = null;

  private forget(): void {
    this.own = null; this.traffic = []; this.radio = []; this.airports = []; this.trail = []; this.route = null; this.zones = null;
    this.calls = [];
  }

  /** The calls waiting for the desktop, handed over once: each answer to the desktop carries them. */
  private take(): Call[] {
    const fresh = this.calls.filter((c) => Date.now() - c.at <= CALL_TTL_MS);
    this.calls = [];
    return fresh;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const watchers = () => this.ctx.getWebSockets().length;
    switch (`${request.method} ${url.pathname}`) {
      case "POST /wipe": {
        for (const ws of this.ctx.getWebSockets()) ws.close(1000, "account deleted");
        await this.ctx.storage.deleteAll();
        this.forget();
        return json({ ok: true });
      }
      case "GET /ws": {
        if (request.headers.get("Upgrade") !== "websocket") return json({ error: "Expected a WebSocket." }, 426);
        const pair = new WebSocketPair();
        this.ctx.acceptWebSocket(pair[1]);
        pair[1].send(JSON.stringify({ type: "hello", data: { protocol: 1, status: await this.current(), own: this.own,
          traffic: this.traffic, radio: this.radio, airports: this.airports, trail: this.trail, route: this.route,
          zones: this.zones } }));
        return new Response(null, { status: 101, webSocket: pair[0] });
      }
      case "PUT /status": {
        const { status, userId } = (await request.json()) as { status: Status; userId: string };
        const before = await this.current();
        const after = { ...status, updated_at: now() };
        await this.ctx.storage.put("status", after);
        if (!after.active) this.forget();
        this.broadcast("status", after);
        const push = pushFor(before.active ? before : null, after);
        if (push) this.ctx.waitUntil(notify(this.env, userId, push));
        return json({ ok: true, watchers: watchers(), calls: this.take() });
      }
      case "PUT /frame": {
        const frame = (await request.json()) as Frame;
        if (frame.trail) { this.trail = frame.trail; this.broadcast("trail", this.trail); }
        if (frame.own) { this.own = frame.own; this.extendTrail(frame.own); this.broadcast("own", frame.own); }
        if (frame.traffic) { this.traffic = frame.traffic; this.broadcast("traffic", frame.traffic); }
        return json({ ok: true, watchers: watchers(), calls: this.take() });
      }
      case "PUT /map": {
        const map = (await request.json()) as LiveMap;
        if (map.route !== undefined) { this.route = map.route; this.broadcast("route", this.route); }
        if (map.zones !== undefined) { this.zones = map.zones; this.broadcast("zones", this.zones); }
        return json({ ok: true, watchers: watchers(), calls: this.take() });
      }
      case "POST /radio": {
        const lines = (await request.json()) as Record<string, unknown>[];
        for (const line of lines) this.broadcast("radio", line);
        this.radio = this.radio.concat(lines).slice(-RADIO_KEEP);
        return json({ ok: true, watchers: watchers(), calls: this.take() });
      }
      case "PUT /airports": {
        this.airports = (await request.json()) as Record<string, unknown>[];
        this.broadcast("airports", this.airports);
        return json({ ok: true, watchers: watchers(), calls: this.take() });
      }
      case "POST /alert": {
        const { alert, userId } = (await request.json()) as { alert: ReturnType<typeof cleanAlert>; userId: string };
        this.broadcast("alert", alert);
        this.ctx.waitUntil(notify(this.env, userId, { title: alert.title, body: alert.body }));
        return json({ ok: true, watchers: watchers() });
      }
      case "POST /say": {
        // A call typed on the phone or the website. Only while a flight is on: otherwise nothing would answer it.
        const { text } = (await request.json()) as { text: string };
        if (!(await this.current()).active) return json({ error: "LocalTC isn't flying right now." }, 409);
        this.calls = this.calls.filter((c) => Date.now() - c.at <= CALL_TTL_MS).concat([{ id: crypto.randomUUID(), text, at: Date.now() }])
          .slice(-CALLS_KEEP);
        return json({ ok: true, waiting: this.calls.length });
      }
      case "GET /calls":  // the desktop's poll while somebody watches and nothing else is going up
        return json({ ok: true, watchers: watchers(), calls: this.take() });
      case "PUT /connect": {
        await this.ctx.storage.put("connect", { ...(await request.json() as object), updated_at: Date.now() });
        return json({ ok: true });
      }
      case "GET /connect": {
        const c = await this.ctx.storage.get<{ lan: string[]; key: string; updated_at: number }>("connect");
        if (!c || Date.now() - c.updated_at > CONNECT_MS) return json({ lan: [], key: null });
        return json({ lan: c.lan, key: c.key });
      }
      case "GET /memory":  // what's held in memory (tests use it to check nothing is persisted)
        return json({ own: this.own, traffic: this.traffic, radio: this.radio, airports: this.airports, trail: this.trail,
          route: this.route, zones: this.zones, calls: this.calls,
          stored: [...(await this.ctx.storage.list()).keys()] });
      case "GET /":
        return json({ ...(await this.current()), watchers: watchers() });
    }
    throw new HttpError(404, "Not found.");
  }

  /** The path grows with the positions passed on, so a second viewer gets it all in its hello. */
  private extendTrail(own: Record<string, unknown>): void {
    const { lat, lon } = own;
    if (typeof lat !== "number" || typeof lon !== "number") return;
    const last = this.trail[this.trail.length - 1];
    if (last && Math.abs(last[0] - lat) + Math.abs(last[1] - lon) < TRAIL_STEP_DEG) return;
    this.trail.push([Math.round(lat * 1e4) / 1e4, Math.round(lon * 1e4) / 1e4]);
    this.trail = thin(this.trail);
  }

  private broadcast(type: string, data: unknown): void {
    const text = JSON.stringify({ type, data });
    for (const ws of this.ctx.getWebSockets()) {
      try { ws.send(text); } catch { /* closing; the runtime drops it */ }
    }
  }

  async current(): Promise<Status> {
    const s = await this.ctx.storage.get<Status>("status");
    if (!s || (s.active && Date.now() - Date.parse(s.updated_at ?? "") > STALE_MS)) return { ...(s ?? {}), active: false };
    return s;
  }

  async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    if (message === "ping") ws.send("pong");
  }

  async webSocketClose(ws: WebSocket, code: number): Promise<void> {
    // 1005/1006 mean "no code given" and may not be sent back; answer those with a plain close.
    try { ws.close(code === 1005 || code === 1006 ? 1000 : code, "bye"); } catch { /* already closed */ }
  }
}

function room(env: Env, auth: Auth): DurableObjectStub {
  return env.LIVE.get(env.LIVE.idFromName(auth.user.id));
}

function send(env: Env, auth: Auth, method: string, path: string, body?: unknown): Promise<Response> {
  return room(env, auth).fetch(`https://live${path}`, { method, body: body === undefined ? undefined : JSON.stringify(body) });
}

export async function put(env: Env, request: Request, auth: Auth): Promise<Response> {
  const status = cleanStatus(await readJson(request, MAX_STATUS));
  return send(env, auth, "PUT", "/status", { status, userId: auth.user.id });
}

export async function get(env: Env, auth: Auth): Promise<Response> {
  return send(env, auth, "GET", "/");
}

export async function frame(env: Env, request: Request, auth: Auth): Promise<Response> {
  return send(env, auth, "PUT", "/frame", cleanFrame(await readJson(request, MAX_FRAME)));
}

export async function map(env: Env, request: Request, auth: Auth): Promise<Response> {
  return send(env, auth, "PUT", "/map", cleanMap(await readJson(request, MAX_MAP)));
}

export async function radio(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson<{ lines?: unknown }>(request, MAX_FRAME);
  return send(env, auth, "POST", "/radio", cleanRadio(body.lines));
}

export async function alert(env: Env, request: Request, auth: Auth): Promise<Response> {
  return send(env, auth, "POST", "/alert", { alert: cleanAlert(await readJson(request, MAX_STATUS)), userId: auth.user.id });
}

/** A radio call from the phone or the website's Flight Tracker, for the desktop to transmit. The website signs in
 * with its cookie, which a browser sends by itself: only the site's own pages may send one that way. */
export async function say(env: Env, request: Request, auth: Auth): Promise<Response> {
  if (auth.viaCookie && !allowedOrigin(env, request.headers.get("Origin"))) throw new HttpError(403, "Not from this site.");
  const text = cleanCall(await readJson(request, 4 * 1024));
  await limit(env, `say:${auth.user.id}`, 120, 3600);
  return send(env, auth, "POST", "/say", { text });
}

/** The desktop picking up the calls waiting for it. */
export async function calls(env: Env, auth: Auth): Promise<Response> {
  return send(env, auth, "GET", "/calls");
}

export async function putConnect(env: Env, request: Request, auth: Auth): Promise<Response> {
  return send(env, auth, "PUT", "/connect", cleanConnect(await readJson(request, MAX_STATUS)));
}

export async function getConnect(env: Env, auth: Auth): Promise<Response> {
  return send(env, auth, "GET", "/connect");
}

export async function airports(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson<{ airports?: unknown }>(request, MAX_AIRPORTS);
  return send(env, auth, "PUT", "/airports", cleanAirports(body.airports));
}

export async function socket(env: Env, request: Request, auth: Auth): Promise<Response> {
  // The website's Flight Tracker signs in with the cookie, which a browser sends by itself: only the
  // site's own pages may open the socket that way (the app and the phone use a bearer token).
  if (auth.viaCookie && !allowedOrigin(env, request.headers.get("Origin"))) throw new HttpError(403, "Not from this site.");
  return room(env, auth).fetch(new Request("https://live/ws", request));
}

export async function addPushToken(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request);
  const token = str(body.token, "Token", 200);
  if (!/^[0-9a-fA-F]{32,200}$/.test(token)) throw new HttpError(400, "That isn't an APNs device token.");
  await env.DB.prepare(
    `INSERT INTO push_tokens (token, user_id, platform, created_at) VALUES (?1, ?2, 'ios', ?3)
     ON CONFLICT(token) DO UPDATE SET user_id = excluded.user_id`,
  ).bind(token.toLowerCase(), auth.user.id, now()).run();
  return json({ ok: true });
}

export async function removePushToken(env: Env, auth: Auth, token: string): Promise<Response> {
  await env.DB.prepare("DELETE FROM push_tokens WHERE token = ?1 AND user_id = ?2").bind(token.toLowerCase(), auth.user.id).run();
  return json({ ok: true });
}

let providerToken: { jwt: string; at: number } | null = null;

/** Send an alert to every iPhone signed in to the account. Off until the APNS_* settings exist. */
async function notify(env: Env, userId: string, push: { title: string; body: string }): Promise<void> {
  if (!env.APNS_TOPIC || !env.APNS_KEY_ID || !env.APNS_TEAM_ID || !env.APNS_PRIVATE_KEY) return;
  const { results } = await env.DB.prepare("SELECT token FROM push_tokens WHERE user_id = ?1").bind(userId).all<{ token: string }>();
  if (!results.length) return;
  // Apple wants a new provider token at most every 20 minutes and at least every hour.
  if (!providerToken || Date.now() - providerToken.at > 40 * 60_000) {
    const jwt = await es256Jwt(env.APNS_PRIVATE_KEY, { kid: env.APNS_KEY_ID }, { iss: env.APNS_TEAM_ID, iat: Math.floor(Date.now() / 1000) });
    providerToken = { jwt, at: Date.now() };
  }
  const payload = JSON.stringify({ aps: { alert: push, sound: "default", "thread-id": "flight" } });
  for (const { token } of results) {
    const res = await fetch(`https://${env.APNS_HOST ?? "api.push.apple.com"}/3/device/${token}`, {
      method: "POST",
      headers: { authorization: `bearer ${providerToken.jwt}`, "apns-topic": env.APNS_TOPIC, "apns-push-type": "alert",
        "apns-priority": "10", "content-type": "application/json" },
      body: payload,
    });
    if (res.status === 410 || res.status === 400) {  // the app was deleted, or the token is for another build
      await env.DB.prepare("DELETE FROM push_tokens WHERE token = ?1").bind(token).run();
    }
  }
}
