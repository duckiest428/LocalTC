import { randomCode, randomToken, sameHash, sha256 } from "./crypto";
import { send } from "./email";
import type { Env } from "./env";
import { COOKIE, HttpError, isoIn, json, now, readCookie, readJson, sessionCookie, str } from "./http";
import { clientIp, limit } from "./rate";

const SESSION_S = { desktop: 365 * 86400, ios: 365 * 86400, web: 30 * 86400 } as const;
const LOGIN_S = 15 * 60; // a code or link works for this long
const MAX_ATTEMPTS = 5; // wrong codes before a sign-in is void
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type Kind = keyof typeof SESSION_S;

export interface User {
  id: string;
  email: string;
  verified_at: string | null;
  created_at: string;
}

export interface Auth {
  user: User;
  sessionId: string;
  viaCookie: boolean;
}

function email(value: unknown): string {
  const e = str(value, "Email", 254).trim().toLowerCase();
  if (!EMAIL.test(e)) throw new HttpError(400, "That isn't an email address.");
  return e;
}

function kindOf(value: unknown): Kind {
  return (["desktop", "ios", "web"] as const).find((k) => k === value) ?? "web";
}

const SENT = "Check your email: we've sent a sign-in code and a link. They work for 15 minutes.";

/**
 * Step one of signing in, and of creating an account: a code and a link to the address. The answer is the
 * same whether or not the address has an account, so this can't be used to find out who does.
 */
export async function start(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request);
  const address = email(body.email);
  await limit(env, `start:ip:${clientIp(request)}`, 20, 3600);
  await limit(env, `start:email:${address}`, 5, 3600);
  let user = await env.DB.prepare("SELECT * FROM users WHERE email = ?1").bind(address).first<User>();
  if (!user) {
    user = { id: crypto.randomUUID(), email: address, verified_at: null, created_at: now() };
    await env.DB.prepare("INSERT INTO users (id, email, created_at) VALUES (?1, ?2, ?3)").bind(user.id, address, user.created_at).run();
  }
  const id = crypto.randomUUID();
  const code = randomCode();
  const token = randomToken();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM logins WHERE user_id = ?1").bind(user.id), // only the newest email works
    env.DB.prepare("INSERT INTO logins (id, user_id, code_hash, link_hash, expires_at) VALUES (?1, ?2, ?3, ?4, ?5)")
      .bind(id, user.id, await sha256(`${id}:${code}`), await sha256(token), isoIn(LOGIN_S)),
  ]);
  const link = `${env.SITE_URL}/dashboard.html?login=${token}`;
  const welcome = user.verified_at ? "" : "This creates your LocalTC account. ";
  await send(env, {
    to: address,
    subject: `${code} is your LocalTC sign-in code`,
    text: `Your LocalTC sign-in code is:\n\n    ${code}\n\nEnter it in the LocalTC app, or sign in to the website with this link:\n\n${link}\n\n`
      + `Both work once, for 15 minutes. ${welcome}If you didn't ask to sign in, ignore this email: nothing happens without the code.`,
  });
  return json({ message: SENT }, 202);
}

async function createSession(env: Env, userId: string, kind: Kind, device: string): Promise<string> {
  const token = randomToken();
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO sessions (id, user_id, token_hash, kind, device, created_at, last_used_at, expires_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7)`,
    ).bind(crypto.randomUUID(), userId, await sha256(token), kind, device.slice(0, 80), now(), isoIn(SESSION_S[kind])),
    env.DB.prepare("UPDATE users SET verified_at = COALESCE(verified_at, ?1) WHERE id = ?2").bind(now(), userId),
    env.DB.prepare("DELETE FROM logins WHERE user_id = ?1").bind(userId),
  ]);
  return token;
}

/** Step two: the code from the email (with the address), or the link's token. Either signs in once. */
export async function finish(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request);
  const kind = kindOf(body.kind);
  const device = typeof body.device === "string" ? body.device : "";
  const wrong = "That code is wrong or has expired. Ask for a new one.";
  let userId: string;
  if (body.token !== undefined) {
    const row = await env.DB.prepare("SELECT user_id, expires_at FROM logins WHERE link_hash = ?1")
      .bind(await sha256(str(body.token, "The link", 128))).first<{ user_id: string; expires_at: string }>();
    if (!row || row.expires_at < now()) throw new HttpError(400, "That link has expired or was already used. Ask for a new one.");
    userId = row.user_id;
  } else {
    const address = email(body.email);
    const code = str(body.code, "Code", 16).replace(/\D/g, "");
    await limit(env, `finish:ip:${clientIp(request)}`, 30, 900);
    const row = await env.DB.prepare(
      "SELECT l.id, l.user_id, l.code_hash, l.attempts, l.expires_at FROM logins l JOIN users u ON u.id = l.user_id WHERE u.email = ?1",
    ).bind(address).first<{ id: string; user_id: string; code_hash: string; attempts: number; expires_at: string }>();
    if (!row || row.expires_at < now() || row.attempts >= MAX_ATTEMPTS) throw new HttpError(401, wrong);
    if (!sameHash(await sha256(`${row.id}:${code}`), row.code_hash)) {
      await env.DB.prepare("UPDATE logins SET attempts = attempts + 1 WHERE id = ?1").bind(row.id).run();
      throw new HttpError(401, wrong);
    }
    userId = row.user_id;
  }
  const token = await createSession(env, userId, kind, device);
  const user = await env.DB.prepare("SELECT email, created_at FROM users WHERE id = ?1").bind(userId).first<User>();
  const result = { token: kind === "web" ? undefined : token, user: { email: user!.email, created_at: user!.created_at } };
  // The website never sees its token: it lives in an HttpOnly cookie.
  return json(result, 200, kind === "web" ? { "Set-Cookie": sessionCookie(env, token, SESSION_S.web) } : {});
}

export async function authenticate(env: Env, request: Request): Promise<Auth> {
  const header = request.headers.get("Authorization");
  const bearer = header?.startsWith("Bearer ") ? header.slice(7).trim() : null;
  const cookie = bearer ? null : readCookie(request, COOKIE);
  const token = bearer ?? cookie;
  if (!token) throw new HttpError(401, "Sign in first.");
  // A cookie is sent by the browser on its own; anything that changes data must also carry a header that a
  // page on another site can't add without the CORS preflight this server refuses (CSRF).
  if (cookie && request.method !== "GET" && request.headers.get("X-LocalTC") !== "1") {
    throw new HttpError(403, "Missing X-LocalTC header.");
  }
  const row = await env.DB.prepare(
    `SELECT s.id AS session_id, s.expires_at, s.last_used_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token_hash = ?1`,
  ).bind(await sha256(token)).first<User & { session_id: string; expires_at: string; last_used_at: string }>();
  if (!row || row.expires_at < now()) throw new HttpError(401, "Your sign-in has expired. Sign in again.");
  if (Date.parse(now()) - Date.parse(row.last_used_at) > 3600_000) {
    await env.DB.prepare("UPDATE sessions SET last_used_at = ?1 WHERE id = ?2").bind(now(), row.session_id).run();
  }
  const { session_id, expires_at: _e, last_used_at: _l, ...user } = row;
  return { user, sessionId: session_id, viaCookie: cookie !== null };
}

export async function logout(env: Env, auth: Auth): Promise<Response> {
  await env.DB.prepare("DELETE FROM sessions WHERE id = ?1").bind(auth.sessionId).run();
  return json({ ok: true }, 200, auth.viaCookie ? { "Set-Cookie": sessionCookie(env, "", 0) } : {});
}

export async function me(env: Env, auth: Auth): Promise<Response> {
  const { results } = await env.DB.prepare(
    "SELECT id, kind, device, created_at, last_used_at FROM sessions WHERE user_id = ?1 ORDER BY last_used_at DESC",
  ).bind(auth.user.id).all<{ id: string }>();
  return json({
    email: auth.user.email, created_at: auth.user.created_at,
    sessions: results.map((s) => ({ ...s, current: s.id === auth.sessionId })),
  });
}

export async function revokeSession(env: Env, auth: Auth, id: string): Promise<Response> {
  const res = await env.DB.prepare("DELETE FROM sessions WHERE id = ?1 AND user_id = ?2").bind(id, auth.user.id).run();
  if (!res.meta.changes) throw new HttpError(404, "No such device.");
  return json({ ok: true });
}

/** The account and everything in it, for good. The email address is typed again to confirm. */
export async function deleteMe(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request);
  if (typeof body.email !== "string" || body.email.trim().toLowerCase() !== auth.user.email.toLowerCase()) {
    throw new HttpError(400, "Type the account's email address to confirm.");
  }
  const id = auth.user.id;
  await env.DB.batch(["shares", "replays", "flights", "sessions", "logins", "push_tokens"].map((t) =>
    env.DB.prepare(`DELETE FROM ${t} WHERE user_id = ?1`).bind(id)).concat(
    env.DB.prepare("DELETE FROM users WHERE id = ?1").bind(id)));
  await env.LIVE.get(env.LIVE.idFromName(id)).fetch("https://live/wipe", { method: "POST" });
  return json({ ok: true }, 200, auth.viaCookie ? { "Set-Cookie": sessionCookie(env, "", 0) } : {});
}

/** The daily tidy-up: expired sign-ins and codes, old rate-limit counters, accounts never signed in to. */
export async function cleanup(env: Env): Promise<void> {
  const day = new Date(Date.now() - 86400_000).toISOString();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM sessions WHERE expires_at < ?1").bind(now()),
    env.DB.prepare("DELETE FROM logins WHERE expires_at < ?1").bind(now()),
    env.DB.prepare("DELETE FROM attempts WHERE expires_at < ?1").bind(Math.floor(Date.now() / 1000)),
    env.DB.prepare("DELETE FROM users WHERE verified_at IS NULL AND created_at < ?1").bind(day),
  ]);
}
