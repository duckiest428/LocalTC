/**
 * The admin panel's server side (the dashboard's Admin page): only for the addresses in ADMINS (wrangler.toml), with a
 * confirmed address, signed in. Everything here is checked again on the server; the page only hides what isn't yours.
 *
 * - An overview: how many accounts, flights, replays, shares, sign-ins, followers, and flights on Live Flights.
 * - The accounts: search them, see one (its devices, flights, sharing), give it tags ("Streamer", "Contributor", shown on
 *   its Live Flights card), turn its Live Flights sharing off or back on, sign it out everywhere, delete it.
 * - Live Flights: the flights on the map with their accounts, take one down, pause it for everyone (and back on).
 */
import { type Auth, deleteUser } from "./auth";
import type { Env } from "./env";
import { HttpError, json, now, readJson } from "./http";
import * as sky from "./sky";

const PAGE = 50;

function admin(env: Env, auth: Auth): void {
  if (!sky.isAdmin(env, auth)) throw new HttpError(403, "That's for LocalTC's admins only.");
}

async function count(env: Env, sql: string, ...args: unknown[]): Promise<number> {
  return (await env.DB.prepare(sql).bind(...args).first<{ n: number }>())?.n ?? 0;
}

/** GET /v1/admin/overview */
export async function overview(env: Env, auth: Auth): Promise<Response> {
  admin(env, auth);
  const day = new Date(Date.now() - 86400_000).toISOString(), week = new Date(Date.now() - 7 * 86400_000).toISOString();
  const [users, verified, newWeek, activeDay, flights, replays, shares, followers, posts, live] = await Promise.all([
    count(env, "SELECT COUNT(*) AS n FROM users"),
    count(env, "SELECT COUNT(*) AS n FROM users WHERE verified_at IS NOT NULL"),
    count(env, "SELECT COUNT(*) AS n FROM users WHERE created_at >= ?1", week),
    count(env, "SELECT COUNT(DISTINCT user_id) AS n FROM sessions WHERE last_used_at >= ?1", day),
    count(env, "SELECT COUNT(*) AS n FROM flights"),
    count(env, "SELECT COUNT(*) AS n FROM replays"),
    count(env, "SELECT COUNT(*) AS n FROM shares"),
    count(env, "SELECT COUNT(*) AS n FROM blog_followers"),
    count(env, "SELECT COUNT(*) AS n FROM blog_posts WHERE published_at IS NOT NULL"),
    sky.adminFlights(env).then((f) => f.length),
  ]);
  const sharing = await count(env, "SELECT COUNT(*) AS n FROM users WHERE live_share != 'off'");
  return json({ users, verified, new_week: newWeek, active_day: activeDay, flights, replays, shares, followers, posts,
    live, sharing, live_paused: !(await sky.enabled(env)) });
}

interface Row {
  id: string; email: string; display_name: string | null; created_at: string; verified_at: string | null;
  live_share: string; live_banned: number; tags: string; flights: number; last_seen: string | null;
}

const view = (r: Row) => ({
  id: r.id, email: r.email, display_name: r.display_name ?? "", created_at: r.created_at, verified: !!r.verified_at,
  live_share: r.live_share, live_banned: !!r.live_banned, tags: sky.readTags(r.tags), flights: r.flights,
  last_seen: r.last_seen,
});

const USER_SQL = `SELECT u.id, u.email, u.display_name, u.created_at, u.verified_at, u.live_share, u.live_banned, u.tags,
  (SELECT COUNT(*) FROM flights f WHERE f.user_id = u.id) AS flights,
  (SELECT MAX(s.last_used_at) FROM sessions s WHERE s.user_id = u.id) AS last_seen FROM users u`;

/** GET /v1/admin/users?q=&offset= : newest first, by email or display name. */
export async function users(env: Env, auth: Auth, url: URL): Promise<Response> {
  admin(env, auth);
  const q = (url.searchParams.get("q") ?? "").trim().slice(0, 80);
  const offset = Math.max(0, Math.min(1e6, Number(url.searchParams.get("offset")) || 0));
  const like = q.toLowerCase(); // (instr, not LIKE: D1 refuses a LIKE pattern as long as an address)
  const where = q ? " WHERE instr(lower(u.email), ?1) > 0 OR instr(lower(coalesce(u.display_name, '')), ?1) > 0" : "";
  const { results } = await env.DB.prepare(`${USER_SQL}${where} ORDER BY u.created_at DESC LIMIT ${PAGE} OFFSET ${offset}`)
    .bind(...(q ? [like] : [])).all<Row>();
  const total = await count(env, `SELECT COUNT(*) AS n FROM users u${where}`, ...(q ? [like] : []));
  return json({ users: results.map(view), total, offset, page: PAGE });
}

async function one(env: Env, id: string): Promise<Row> {
  const row = await env.DB.prepare(`${USER_SQL} WHERE u.id = ?1`).bind(id).first<Row>();
  if (!row) throw new HttpError(404, "No such account.");
  return row;
}

/** GET /v1/admin/users/:id : the account, its devices and its latest flights. */
export async function user(env: Env, auth: Auth, id: string): Promise<Response> {
  admin(env, auth);
  const row = await one(env, id);
  const sessions = await env.DB.prepare(
    "SELECT kind, device, created_at, last_used_at FROM sessions WHERE user_id = ?1 ORDER BY last_used_at DESC LIMIT 20",
  ).bind(id).all();
  const flights = await env.DB.prepare(
    "SELECT callsign, aircraft, origin, destination, started_at FROM flights WHERE user_id = ?1 ORDER BY started_at DESC LIMIT 10",
  ).bind(id).all();
  return json({ ...view(row), sessions: sessions.results, recent: flights.results });
}

/** PATCH /v1/admin/users/:id {tags?, live_banned?} */
export async function update(env: Env, request: Request, auth: Auth, id: string): Promise<Response> {
  admin(env, auth);
  await one(env, id);
  const body = await readJson(request, 4096);
  if ("tags" in body) {
    if (!Array.isArray(body.tags)) throw new HttpError(400, "Send the tags as a list.");
    if (body.tags.length > sky.MAX_TAGS) throw new HttpError(400, `At most ${sky.MAX_TAGS} tags.`);
    const tags = sky.readTags(body.tags);
    if (tags.length !== body.tags.length) {
      throw new HttpError(400, "A tag is 1 to 20 letters, digits and spaces (and . & + ' -), in one of the colours.");
    }
    await env.DB.prepare("UPDATE users SET tags = ?1 WHERE id = ?2").bind(JSON.stringify(tags), id).run();
    await sky.setMeta(env, id, { tags });
  }
  if ("live_banned" in body) {
    if (typeof body.live_banned !== "boolean") throw new HttpError(400, "live_banned is true or false.");
    await env.DB.prepare(body.live_banned ? "UPDATE users SET live_banned = 1, live_share = 'off' WHERE id = ?1"
      : "UPDATE users SET live_banned = 0 WHERE id = ?1").bind(id).run();
    if (body.live_banned) await sky.forget(env, id);
  }
  return json(view(await one(env, id)));
}

/** POST /v1/admin/users/:id/signout : every device signed out. */
export async function signOut(env: Env, auth: Auth, id: string): Promise<Response> {
  admin(env, auth);
  await one(env, id);
  const res = await env.DB.prepare("DELETE FROM sessions WHERE user_id = ?1").bind(id).run();
  return json({ ok: true, signed_out: res.meta.changes ?? 0 });
}

/** DELETE /v1/admin/users/:id {email} : the account and everything in it, the address typed again to confirm. */
export async function remove(env: Env, request: Request, auth: Auth, id: string): Promise<Response> {
  admin(env, auth);
  const row = await one(env, id);
  const body = await readJson(request, 1024);
  if (typeof body.email !== "string" || body.email.trim().toLowerCase() !== row.email.toLowerCase()) {
    throw new HttpError(400, "Type the account's email address to confirm.");
  }
  if (id === auth.user.id) throw new HttpError(400, "Delete your own account from its Account page.");
  await deleteUser(env, id);
  return json({ ok: true });
}

/** GET /v1/admin/live : the flights on Live Flights, with their accounts. */
export async function live(env: Env, auth: Auth): Promise<Response> {
  admin(env, auth);
  const flights = await sky.adminFlights(env);
  const ids = [...new Set(flights.map((f) => f.uid))];
  const emails = new Map<string, string>();
  for (const id of ids) {
    const r = await env.DB.prepare("SELECT email, display_name FROM users WHERE id = ?1").bind(id).first<{ email: string }>();
    if (r) emails.set(id, r.email);
  }
  return json({
    paused: !(await sky.enabled(env)), locked: (env.LIVE_SKY ?? "on") === "off",
    flights: flights.map(({ uid, ...f }) => ({ ...f, user_id: uid, email: emails.get(uid) ?? "" })),
  });
}

/** PUT /v1/admin/live {paused} : Live Flights off for everyone (every flight off the map), or back on. */
export async function pause(env: Env, request: Request, auth: Auth): Promise<Response> {
  admin(env, auth);
  const body = await readJson(request, 256);
  if (typeof body.paused !== "boolean") throw new HttpError(400, "paused is true or false.");
  await env.DB.prepare(`INSERT INTO server_flags (key, value, updated_at) VALUES ('live_sky_paused', ?1, ?2)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`).bind(body.paused ? "1" : "0", now()).run();
  if (body.paused) await sky.clearAll(env);
  return live(env, auth);
}
