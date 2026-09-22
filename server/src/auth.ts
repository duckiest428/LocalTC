import { hashPassword, randomToken, sha256, verifyPassword } from "./crypto";
import { send } from "./email";
import type { Env } from "./env";
import { COOKIE, HttpError, isoIn, json, now, readCookie, readJson, sessionCookie, str } from "./http";
import { clientIp, limit } from "./rate";

const SESSION_S = { desktop: 365 * 86400, ios: 365 * 86400, web: 30 * 86400 } as const;
const LINK_S = { verify: 3 * 86400, reset: 3600 } as const;
const PASSWORD_MIN = 10;
const PASSWORD_MAX = 256;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export interface User {
  id: string;
  email: string;
  password: string;
  verified_at: string | null;
  created_at: string;
}

export interface Auth {
  user: User;
  sessionId: string;
  viaCookie: boolean;
}

function iterations(env: Env): number {
  return Number(env.PBKDF2_ITERATIONS ?? "100000");
}

function email(value: unknown): string {
  const e = str(value, "Email", 254).trim().toLowerCase();
  if (!EMAIL.test(e)) throw new HttpError(400, "That isn't an email address.");
  return e;
}

function password(value: unknown, field = "Password"): string {
  const p = str(value, field, PASSWORD_MAX);
  if (p.length < PASSWORD_MIN) throw new HttpError(400, `Use at least ${PASSWORD_MIN} characters for the password.`);
  return p;
}

async function link(env: Env, userId: string, purpose: "verify" | "reset"): Promise<string> {
  const token = randomToken();
  await env.DB.prepare("DELETE FROM tokens WHERE user_id = ?1 AND purpose = ?2").bind(userId, purpose).run();
  await env.DB.prepare("INSERT INTO tokens (hash, user_id, purpose, expires_at) VALUES (?1, ?2, ?3, ?4)")
    .bind(await sha256(token), userId, purpose, isoIn(LINK_S[purpose])).run();
  return `${env.SITE_URL}/dashboard.html?${purpose}=${token}`;
}

async function useLink(env: Env, token: unknown, purpose: "verify" | "reset"): Promise<string> {
  const hash = await sha256(str(token, "The link", 128));
  const row = await env.DB.prepare("DELETE FROM tokens WHERE hash = ?1 AND purpose = ?2 RETURNING user_id, expires_at")
    .bind(hash, purpose).first<{ user_id: string; expires_at: string }>();
  if (!row || row.expires_at < now()) throw new HttpError(400, "That link has expired or was already used.");
  return row.user_id;
}

async function sendVerification(env: Env, user: { id: string; email: string }): Promise<void> {
  const url = await link(env, user.id, "verify");
  await send(env, {
    to: user.email,
    subject: "Confirm your LocalTC account",
    text: `Confirm this address for your LocalTC account:\n\n${url}\n\nThe link works for 3 days. If you didn't create an account, ignore this email.`,
  });
}

const CHECK_EMAIL = "Check your email for a link to confirm the account, then sign in.";

export async function register(env: Env, request: Request): Promise<Response> {
  await limit(env, `register:${clientIp(request)}`, 5, 3600);
  const body = await readJson(request);
  const address = email(body.email);
  const hash = await hashPassword(password(body.password), iterations(env));
  const existing = await env.DB.prepare("SELECT id, email, verified_at FROM users WHERE email = ?1").bind(address).first<User>();
  // The same answer whether or not the address has an account, so this can't be used to find out who does.
  if (existing) {
    if (!existing.verified_at) await sendVerification(env, existing);
    else await send(env, { to: address, subject: "Your LocalTC account",
      text: `Someone tried to create a LocalTC account with this address, which already has one. If it was you, sign in, or reset the password at ${env.SITE_URL}/dashboard.html.` });
    return json({ message: CHECK_EMAIL }, 202);
  }
  const user = { id: crypto.randomUUID(), email: address };
  await env.DB.prepare("INSERT INTO users (id, email, password, created_at) VALUES (?1, ?2, ?3, ?4)")
    .bind(user.id, address, hash, now()).run();
  await sendVerification(env, user);
  return json({ message: CHECK_EMAIL }, 202);
}

export async function verify(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request);
  const userId = await useLink(env, body.token, "verify");
  await env.DB.prepare("UPDATE users SET verified_at = COALESCE(verified_at, ?1) WHERE id = ?2").bind(now(), userId).run();
  return json({ message: "Your email address is confirmed. Sign in to continue." });
}

async function createSession(env: Env, userId: string, kind: keyof typeof SESSION_S, device: string): Promise<string> {
  const token = randomToken();
  await env.DB.prepare(
    `INSERT INTO sessions (id, user_id, token_hash, kind, device, created_at, last_used_at, expires_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7)`,
  ).bind(crypto.randomUUID(), userId, await sha256(token), kind, device.slice(0, 80), now(), isoIn(SESSION_S[kind])).run();
  return token;
}

// Compared against when the email has no account, so a wrong email takes as long as a wrong password.
let dummyHash: string | null = null;

export async function login(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request);
  const address = email(body.email);
  await limit(env, `login:ip:${clientIp(request)}`, 30, 900);
  await limit(env, `login:email:${address}`, 10, 900);
  const kind = (["desktop", "ios", "web"] as const).find((k) => k === body.kind) ?? "web";
  const user = await env.DB.prepare("SELECT * FROM users WHERE email = ?1").bind(address).first<User>();
  dummyHash ??= await hashPassword("not a real password", iterations(env));
  const ok = await verifyPassword(str(body.password, "Password", PASSWORD_MAX), user?.password ?? dummyHash);
  if (!user || !ok) throw new HttpError(401, "Wrong email or password.");
  if (!user.verified_at) {
    await sendVerification(env, user);
    throw new HttpError(403, "Confirm your email address first: we've sent the link again.");
  }
  const token = await createSession(env, user.id, kind, typeof body.device === "string" ? body.device : "");
  const result = { token: kind === "web" ? undefined : token, user: { email: user.email, created_at: user.created_at } };
  // The website never sees the token: it lives in an HttpOnly cookie.
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

export async function resetRequest(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request);
  const address = email(body.email);
  await limit(env, `reset:${address}`, 3, 3600);
  const user = await env.DB.prepare("SELECT id, email FROM users WHERE email = ?1").bind(address).first<User>();
  if (user) {
    const url = await link(env, user.id, "reset");
    await send(env, { to: address, subject: "Reset your LocalTC password",
      text: `Choose a new password for your LocalTC account:\n\n${url}\n\nThe link works for an hour. If you didn't ask, ignore this email: nothing changes.` });
  }
  return json({ message: "If there's an account for that address, a reset link is on its way." }, 202);
}

export async function reset(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request);
  const newPassword = password(body.password);
  const userId = await useLink(env, body.token, "reset");
  await env.DB.batch([
    env.DB.prepare("UPDATE users SET password = ?1, verified_at = COALESCE(verified_at, ?2) WHERE id = ?3")
      .bind(await hashPassword(newPassword, iterations(env)), now(), userId),
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ?1").bind(userId),  // signed out everywhere
  ]);
  return json({ message: "Password changed. Sign in with the new one." });
}

export async function changePassword(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request);
  if (!(await verifyPassword(str(body.current, "Current password", PASSWORD_MAX), auth.user.password))) {
    throw new HttpError(403, "The current password is wrong.");
  }
  await env.DB.batch([
    env.DB.prepare("UPDATE users SET password = ?1 WHERE id = ?2").bind(await hashPassword(password(body.password), iterations(env)), auth.user.id),
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ?1 AND id != ?2").bind(auth.user.id, auth.sessionId),
  ]);
  return json({ message: "Password changed. Every other device was signed out." });
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

/** The account and everything in it, for good. The password is asked again. */
export async function deleteMe(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request);
  if (!(await verifyPassword(str(body.password, "Password", PASSWORD_MAX), auth.user.password))) {
    throw new HttpError(403, "The password is wrong.");
  }
  const id = auth.user.id;
  await env.DB.batch(["flights", "sessions", "tokens", "push_tokens"].map((t) =>
    env.DB.prepare(`DELETE FROM ${t} WHERE user_id = ?1`).bind(id)).concat(
    env.DB.prepare("DELETE FROM users WHERE id = ?1").bind(id)));
  await env.LIVE.get(env.LIVE.idFromName(id)).fetch("https://live/wipe", { method: "POST" });
  return json({ ok: true }, 200, auth.viaCookie ? { "Set-Cookie": sessionCookie(env, "", 0) } : {});
}

/** The daily tidy-up: expired sign-ins and links, old rate-limit counters, accounts never confirmed. */
export async function cleanup(env: Env): Promise<void> {
  const week = new Date(Date.now() - 7 * 86400_000).toISOString();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM sessions WHERE expires_at < ?1").bind(now()),
    env.DB.prepare("DELETE FROM tokens WHERE expires_at < ?1").bind(now()),
    env.DB.prepare("DELETE FROM attempts WHERE expires_at < ?1").bind(Math.floor(Date.now() / 1000)),
    env.DB.prepare("DELETE FROM users WHERE verified_at IS NULL AND created_at < ?1").bind(week),
  ]);
}
