import { DurableObject } from "cloudflare:workers";
import type { Auth } from "./auth";
import { es256Jwt } from "./crypto";
import type { Env } from "./env";
import { HttpError, json, now, readJson, str } from "./http";

const STALE_MS = 10 * 60_000; // no word from the app for this long: the flight is over (or the PC is off)
const MAX_STATUS = 8 * 1024;

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
  tuned?: Station;
  next?: Station;
  ete?: { nm: number; min: number } | null;
  last_atc?: { station?: string; mhz?: number; text?: string } | null;
  updated_at?: string;
}

const KEYS = ["active", "callsign", "aircraft", "origin", "destination", "phase", "phase_label", "squawk", "altitude_ft",
  "runway", "tuned", "next", "ete", "last_atc"] as const;

/** Only the fields the companion shows; a position or anything else sent is dropped here. */
export function cleanStatus(raw: Record<string, unknown>): Status {
  const out: Record<string, unknown> = {};
  for (const k of KEYS) if (k in raw) out[k] = raw[k];
  out.active = !!raw.active;
  if (JSON.stringify(out).length > MAX_STATUS) throw new HttpError(413, "The status is too big.");
  return out as unknown as Status;
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

/** One per account: the flight's latest status, the companion apps watching it, and their notifications. */
export class LiveRoom extends DurableObject<Env> {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/wipe") {
      for (const ws of this.ctx.getWebSockets()) ws.close(1000, "account deleted");
      await this.ctx.storage.deleteAll();
      return json({ ok: true });
    }
    if (url.pathname === "/ws") {
      if (request.headers.get("Upgrade") !== "websocket") throw new HttpError(426, "Expected a WebSocket.");
      const pair = new WebSocketPair();
      this.ctx.acceptWebSocket(pair[1]);
      pair[1].send(JSON.stringify(await this.current()));
      return new Response(null, { status: 101, webSocket: pair[0] });
    }
    if (request.method === "PUT") {
      const { status, userId } = (await request.json()) as { status: Status; userId: string };
      const before = await this.current();
      const after = { ...status, updated_at: now() };
      await this.ctx.storage.put("status", after);
      const text = JSON.stringify(after);
      for (const ws of this.ctx.getWebSockets()) {
        try { ws.send(text); } catch { /* closing; the runtime drops it */ }
      }
      const push = pushFor(before.active ? before : null, after);
      if (push) this.ctx.waitUntil(notify(this.env, userId, push));
      return json({ ok: true });
    }
    return json(await this.current());
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
    ws.close(code, "bye");
  }
}

function room(env: Env, auth: Auth): DurableObjectStub {
  return env.LIVE.get(env.LIVE.idFromName(auth.user.id));
}

export async function put(env: Env, request: Request, auth: Auth): Promise<Response> {
  const status = cleanStatus(await readJson(request, MAX_STATUS));
  return room(env, auth).fetch("https://live/", { method: "PUT", body: JSON.stringify({ status, userId: auth.user.id }) });
}

export async function get(env: Env, auth: Auth): Promise<Response> {
  return room(env, auth).fetch("https://live/");
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
