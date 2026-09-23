import { DurableObject } from "cloudflare:workers";
import type { Auth } from "./auth";
import { es256Jwt } from "./crypto";
import type { Env } from "./env";
import { HttpError, json, now, readJson, str } from "./http";

/*
 * The companion app's live view, one LiveRoom (Durable Object) per account.
 *
 * - The flight's status (phase, frequencies, ATC's last line) is kept in storage, so a phone opening the
 *   app sees the latest even after the room was evicted.
 * - Position, traffic and the radio log arrive only while a phone is watching through the server (the
 *   desktop checks the watcher count in every answer). They're held in memory and passed on, never
 *   written to storage: when the room is evicted, they're gone.
 * - Where the phone can reach the PC on the local network, and the key it needs there, is stored until the
 *   desktop replaces it.
 *
 * Everything the phone receives over the WebSocket is {type, data}, as in docs/companion-protocol.md.
 */

const STALE_MS = 10 * 60_000; // no word from the app for this long: the flight is over (or the PC is off)
const CONNECT_MS = 24 * 3600_000; // local-network details older than this are dropped
const MAX_STATUS = 8 * 1024;
const MAX_FRAME = 64 * 1024;
const RADIO_KEEP = 50;

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
  tuned?: Station;
  next?: Station;
  ete?: { nm: number; min: number } | null;
  last_atc?: { station?: string; mhz?: number; text?: string } | null;
  updated_at?: string;
}

const STATUS_KEYS = ["active", "callsign", "aircraft", "origin", "destination", "phase", "phase_label", "squawk",
  "altitude_ft", "runway", "gate", "tuned", "next", "ete", "last_atc"] as const;
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
  if (JSON.stringify(out).length > MAX_STATUS) throw new HttpError(413, "The status is too big.");
  return out as unknown as Status;
}

export function cleanFrame(raw: Record<string, unknown>): { own?: Record<string, unknown>; traffic?: Record<string, unknown>[] } {
  const out: { own?: Record<string, unknown>; traffic?: Record<string, unknown>[] } = {};
  if (raw.own) out.own = pick(raw.own, OWN_KEYS);
  if (Array.isArray(raw.traffic)) out.traffic = raw.traffic.slice(0, 300).map((t) => pick(t, TRAFFIC_KEYS));
  if (JSON.stringify(out).length > MAX_FRAME) throw new HttpError(413, "Too much traffic at once.");
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

export class LiveRoom extends DurableObject<Env> {
  // In memory only. Never written to this.ctx.storage.
  private own: Record<string, unknown> | null = null;
  private traffic: Record<string, unknown>[] = [];
  private radio: Record<string, unknown>[] = [];

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const watchers = () => this.ctx.getWebSockets().length;
    switch (`${request.method} ${url.pathname}`) {
      case "POST /wipe": {
        for (const ws of this.ctx.getWebSockets()) ws.close(1000, "account deleted");
        await this.ctx.storage.deleteAll();
        this.own = null; this.traffic = []; this.radio = [];
        return json({ ok: true });
      }
      case "GET /ws": {
        if (request.headers.get("Upgrade") !== "websocket") return json({ error: "Expected a WebSocket." }, 426);
        const pair = new WebSocketPair();
        this.ctx.acceptWebSocket(pair[1]);
        pair[1].send(JSON.stringify({ type: "hello", data: { protocol: 1, status: await this.current(), own: this.own,
          traffic: this.traffic, radio: this.radio } }));
        return new Response(null, { status: 101, webSocket: pair[0] });
      }
      case "PUT /status": {
        const { status, userId } = (await request.json()) as { status: Status; userId: string };
        const before = await this.current();
        const after = { ...status, updated_at: now() };
        await this.ctx.storage.put("status", after);
        if (!after.active) { this.own = null; this.traffic = []; this.radio = []; }
        this.broadcast("status", after);
        const push = pushFor(before.active ? before : null, after);
        if (push) this.ctx.waitUntil(notify(this.env, userId, push));
        return json({ ok: true, watchers: watchers() });
      }
      case "PUT /frame": {
        const frame = (await request.json()) as ReturnType<typeof cleanFrame>;
        if (frame.own) { this.own = frame.own; this.broadcast("own", frame.own); }
        if (frame.traffic) { this.traffic = frame.traffic; this.broadcast("traffic", frame.traffic); }
        return json({ ok: true, watchers: watchers() });
      }
      case "POST /radio": {
        const lines = (await request.json()) as Record<string, unknown>[];
        for (const line of lines) this.broadcast("radio", line);
        this.radio = this.radio.concat(lines).slice(-RADIO_KEEP);
        return json({ ok: true, watchers: watchers() });
      }
      case "POST /alert": {
        const { alert, userId } = (await request.json()) as { alert: ReturnType<typeof cleanAlert>; userId: string };
        this.broadcast("alert", alert);
        this.ctx.waitUntil(notify(this.env, userId, { title: alert.title, body: alert.body }));
        return json({ ok: true, watchers: watchers() });
      }
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
        return json({ own: this.own, traffic: this.traffic, radio: this.radio, stored: [...(await this.ctx.storage.list()).keys()] });
      case "GET /":
        return json({ ...(await this.current()), watchers: watchers() });
    }
    throw new HttpError(404, "Not found.");
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

export async function radio(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson<{ lines?: unknown }>(request, MAX_FRAME);
  return send(env, auth, "POST", "/radio", cleanRadio(body.lines));
}

export async function alert(env: Env, request: Request, auth: Auth): Promise<Response> {
  return send(env, auth, "POST", "/alert", { alert: cleanAlert(await readJson(request, MAX_STATUS)), userId: auth.user.id });
}

export async function putConnect(env: Env, request: Request, auth: Auth): Promise<Response> {
  return send(env, auth, "PUT", "/connect", cleanConnect(await readJson(request, MAX_STATUS)));
}

export async function getConnect(env: Env, auth: Auth): Promise<Response> {
  return send(env, auth, "GET", "/connect");
}

export async function socket(env: Env, request: Request, auth: Auth): Promise<Response> {
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
