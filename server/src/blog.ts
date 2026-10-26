/**
 * The blog (localtc.tech/blog): news about LocalTC, written on the blog page itself by its authors and read by
 * anyone.
 *
 * Only the accounts in BLOG_AUTHORS can write: the comma-separated addresses in wrangler.toml, checked against the
 * signed-in account's own address (which signing in proves: there are no passwords, a code is emailed). Anyone
 * else, signed in or not, gets the published posts and nothing more. A post is Markdown; its pictures are uploaded
 * here (JPEG, PNG, WebP or GIF, up to 1.5 MB: the page shrinks a photo before it sends it) and served from
 * /v1/blog/images/<id>.
 *
 * Readers can react to a post (like, love, takeoff, clap) and each post counts its readers: one reaction of a kind
 * and one read per reader, no account needed. A reader is known by a hash of a secret salt, their IP address and
 * their browser's user agent: nothing is stored on their device (the site keeps its promise of no cookies and no
 * storage), and the hash doesn't say who. Accounts can follow the blog: each post, the first time it's published, is
 * emailed to them from MARKETING_FROM, with a link that unfollows without signing in.
 *
 * Posts written in the repository (site/blog-posts.json) are the site's own, not here.
 */
import type { Auth } from "./auth";
import type { Env } from "./env";
import { sha256 } from "./crypto";
import { sendMany } from "./email";
import { HttpError, json, now, readJson } from "./http";
import { clientIp, limit } from "./rate";

const DEFAULT_AUTHORS = "duckiesty@gmail.com";
const MAX_IMAGE = 1_500_000;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TYPES: [string, number[]][] = [
  ["image/png", [0x89, 0x50, 0x4e, 0x47]], ["image/jpeg", [0xff, 0xd8, 0xff]], ["image/gif", [0x47, 0x49, 0x46, 0x38]],
  ["image/webp", [0x52, 0x49, 0x46, 0x46]],  // RIFF; WEBP at 8 is checked below
];

type Post = { slug: string; title: string; summary: string; body: string; cover: string | null; author_name: string;
  published_at: string | null; created_at: string; updated_at: string; notified_at?: string | null };
export const REACTIONS = ["like", "love", "takeoff", "clap"];

/** Whether this account may write the blog: its address is one of BLOG_AUTHORS, and it's been confirmed. */
export function isAuthor(env: Env, auth: Auth): boolean {
  const authors = (env.BLOG_AUTHORS ?? DEFAULT_AUTHORS).split(",").map((a) => a.trim().toLowerCase()).filter(Boolean);
  return !!auth.user.verified_at && authors.includes(auth.user.email.toLowerCase());
}

function author(env: Env, auth: Auth): void {
  if (!isAuthor(env, auth)) throw new HttpError(403, "Only the blog's authors can do that.");
}

const card = (p: Post) => ({ slug: p.slug, title: p.title, summary: p.summary, cover: p.cover, author: p.author_name,
  published_at: p.published_at, updated_at: p.updated_at });

/** GET /v1/blog: the published posts, newest first (no bodies). */
export async function list(env: Env): Promise<Response> {
  const { results } = await env.DB.prepare(
    "SELECT * FROM blog_posts WHERE published_at IS NOT NULL ORDER BY published_at DESC LIMIT 200").all<Post>();
  return json({ posts: results.map(card) }, 200, { "Cache-Control": "public, max-age=60" });
}

/** GET /v1/blog/posts/<slug>: one published post, whole. */
export async function get(env: Env, slug: string): Promise<Response> {
  const p = await env.DB.prepare("SELECT * FROM blog_posts WHERE slug = ?1 AND published_at IS NOT NULL").bind(slug).first<Post>();
  if (!p) throw new HttpError(404, "No such post.");
  return json({ ...card(p), body: p.body }, 200, { "Cache-Control": "public, max-age=60" });
}

/** GET /v1/blog/drafts: every post, drafts too, whole (authors only). */
export async function all(env: Env, auth: Auth): Promise<Response> {
  author(env, auth);
  const { results } = await env.DB.prepare("SELECT * FROM blog_posts ORDER BY COALESCE(published_at, updated_at) DESC").all<Post>();
  return json({ posts: results.map((p) => ({ ...card(p), body: p.body })), ...(await audience(env)) });
}

/** For the authors: each post's readers and reactions (the repository's posts too), and how many follow the blog. */
async function audience(env: Env): Promise<{ reads: Record<string, number>; reactions: Record<string, Record<string, number>>; followers: number }> {
  const [reads, reacts, followers] = await Promise.all([
    env.DB.prepare("SELECT slug, COUNT(*) AS n FROM blog_reads GROUP BY slug").all<{ slug: string; n: number }>(),
    env.DB.prepare("SELECT slug, kind, COUNT(*) AS n FROM blog_reactions GROUP BY slug, kind").all<{ slug: string; kind: string; n: number }>(),
    env.DB.prepare("SELECT COUNT(*) AS n FROM blog_followers").first<{ n: number }>(),
  ]);
  const reactions: Record<string, Record<string, number>> = {};
  for (const r of reacts.results) (reactions[r.slug] ??= {})[r.kind] = r.n;
  return { reads: Object.fromEntries(reads.results.map((r) => [r.slug, r.n])), reactions, followers: followers?.n ?? 0 };
}

/** A picture's address on this server, or a page of the site's own ("/blog/img/x.jpg", "img/x.jpg"); else null. */
function imageUrl(v: unknown): string | null {
  if (v == null || v === "") return null;
  if (typeof v !== "string" || v.length > 300) throw new HttpError(400, "The cover isn't a picture's address.");
  if (/^https:\/\/api\.localtc\.(tech|test)\/v1\/blog\/images\/[a-f0-9]{32}$/.test(v) || /^http:\/\/localhost:8787\/v1\/blog\/images\/[a-f0-9]{32}$/.test(v)
    || /^\/?[\w/.-]+\.(jpe?g|png|webp|gif)$/.test(v)) return v;
  throw new HttpError(400, "The cover has to be a picture uploaded here.");
}

function slugOf(title: string): string {
  return title.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "post";
}

/** POST /v1/blog/posts: a new post or a change to one ({slug?, title, summary, body, cover, published}). */
export async function save(env: Env, request: Request, auth: Auth): Promise<Response> {
  author(env, auth);
  const b = await readJson(request, 400 * 1024);
  const title = typeof b.title === "string" ? b.title.replace(/\s+/g, " ").trim() : "";
  if (!title || title.length > 140) throw new HttpError(400, "A post needs a title, up to 140 characters.");
  const summary = typeof b.summary === "string" ? b.summary.replace(/\s+/g, " ").trim().slice(0, 400) : "";
  const body = typeof b.body === "string" ? b.body : "";
  if (body.length > 200_000) throw new HttpError(400, "The post is too long.");
  const cover = imageUrl(b.cover);
  const at = now();
  let slug = typeof b.slug === "string" && b.slug ? b.slug : "";
  if (slug && !SLUG.test(slug)) throw new HttpError(400, "A post's address is lowercase letters, digits and dashes.");
  const existing = slug ? await env.DB.prepare("SELECT * FROM blog_posts WHERE slug = ?1").bind(slug).first<Post>() : null;
  if (!existing) {  // a new post: its address from the title, made unique
    const base = slug || slugOf(title);
    slug = base;
    for (let i = 2; await env.DB.prepare("SELECT 1 FROM blog_posts WHERE slug = ?1").bind(slug).first(); i++) slug = `${base}-${i}`;
  }
  const published = b.published ? existing?.published_at ?? at : null;
  const name = (auth.user.display_name || "").trim() || "The LocalTC team";
  await env.DB.prepare(
    `INSERT INTO blog_posts (slug, title, summary, body, cover, author_id, author_name, published_at, created_at, updated_at)
     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?9)
     ON CONFLICT(slug) DO UPDATE SET title = ?2, summary = ?3, body = ?4, cover = ?5, published_at = ?8, updated_at = ?9`,
  ).bind(slug, title, summary, body, cover, auth.user.id, name, published, at).run();
  if (published && !existing?.notified_at) await notify(env, { slug, title, summary });
  return json({ slug, published_at: published, updated_at: at });
}

/** The post to everyone following the blog, once: the first time it's published. */
async function notify(env: Env, p: { slug: string; title: string; summary: string }): Promise<void> {
  await env.DB.prepare("UPDATE blog_posts SET notified_at = ?1 WHERE slug = ?2").bind(now(), p.slug).run();
  const { results } = await env.DB.prepare(
    "SELECT u.email, f.token FROM blog_followers f JOIN users u ON u.id = f.user_id").all<{ email: string; token: string }>();
  if (!results.length) return;
  const link = `${env.SITE_URL}/blog#${p.slug}`;
  const api = env.SITE_URL.startsWith("http://localhost") ? "http://localhost:8787" : "https://api.localtc.tech";
  await sendMany(env, results.map((f) => {
    const off = `${api}/v1/blog/unfollow/${f.token}`;
    return {
      to: f.email, from: env.MARKETING_FROM ?? "LocalTC <marketing@localtc.tech>",
      subject: `New on the LocalTC blog: ${p.title}`,
      text: [p.title, "", ...(p.summary ? [p.summary, ""] : []), `Read it: ${link}`, "", "--",
        "You get this because you follow the LocalTC blog. Stop these emails (no sign-in needed):", off].join("\n"),
      headers: { "List-Unsubscribe": `<${off}>`, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click" },
    };
  }));
}

/** A post's reaction counts, and which this browser gave. */
async function reactionsOf(env: Env, slug: string, viewer: string): Promise<{ reactions: Record<string, number>; mine: string[] }> {
  const { results } = await env.DB.prepare("SELECT kind, COUNT(*) AS n, SUM(viewer = ?2) AS me FROM blog_reactions WHERE slug = ?1 GROUP BY kind")
    .bind(slug, viewer).all<{ kind: string; n: number; me: number }>();
  return { reactions: Object.fromEntries(results.map((r) => [r.kind, r.n])), mine: results.filter((r) => r.me).map((r) => r.kind) };
}

/** Who's reading, as stored: the salted hash of their IP address and user agent. */
async function viewerOf(env: Env, request: Request): Promise<string> {
  let row = await env.DB.prepare("SELECT salt FROM blog_salt WHERE id = 1").first<{ salt: string }>();
  if (!row) {
    const salt = [...crypto.getRandomValues(new Uint8Array(32))].map((b) => b.toString(16).padStart(2, "0")).join("");
    await env.DB.prepare("INSERT OR IGNORE INTO blog_salt (id, salt) VALUES (1, ?1)").bind(salt).run();
    row = await env.DB.prepare("SELECT salt FROM blog_salt WHERE id = 1").first<{ salt: string }>();
  }
  return sha256(`${row!.salt}|${clientIp(request)}|${request.headers.get("User-Agent") ?? ""}`);
}

/** POST /v1/blog/posts/<slug>/read: counted once per reader; the post's reactions. */
export async function read(env: Env, request: Request, slug: string): Promise<Response> {
  const viewer = await viewerOf(env, request);
  await limit(env, `blog-read:${clientIp(request)}`, 300, 3600);
  await env.DB.prepare("INSERT OR IGNORE INTO blog_reads (slug, viewer, read_at) VALUES (?1, ?2, ?3)").bind(slug, viewer, now()).run();
  return json(await reactionsOf(env, slug, viewer));
}

/** POST /v1/blog/posts/<slug>/react {kind, on}: a reaction given or taken back; the post's reactions. */
export async function react(env: Env, request: Request, slug: string): Promise<Response> {
  const b = await readJson(request, 1024);
  const viewer = await viewerOf(env, request);
  const kind = String(b.kind);
  if (!REACTIONS.includes(kind)) throw new HttpError(400, "That isn't a reaction.");
  await limit(env, `blog-react:${clientIp(request)}`, 120, 3600);
  if (b.on === false) {
    await env.DB.prepare("DELETE FROM blog_reactions WHERE slug = ?1 AND kind = ?2 AND viewer = ?3").bind(slug, kind, viewer).run();
  } else {
    await env.DB.prepare("INSERT OR IGNORE INTO blog_reactions (slug, kind, viewer, created_at) VALUES (?1, ?2, ?3, ?4)").bind(slug, kind, viewer, now()).run();
  }
  return json(await reactionsOf(env, slug, viewer));
}

/** GET/PUT/DELETE /v1/blog/follow: whether this account gets new posts by email; follow; stop. */
export async function follow(env: Env, request: Request, auth: Auth): Promise<Response> {
  if (request.method === "PUT") {
    const token = [...crypto.getRandomValues(new Uint8Array(24))].map((b) => b.toString(16).padStart(2, "0")).join("");
    await env.DB.prepare("INSERT OR IGNORE INTO blog_followers (user_id, token, created_at) VALUES (?1, ?2, ?3)").bind(auth.user.id, token, now()).run();
  } else if (request.method === "DELETE") {
    await env.DB.prepare("DELETE FROM blog_followers WHERE user_id = ?1").bind(auth.user.id).run();
  }
  const row = await env.DB.prepare("SELECT 1 FROM blog_followers WHERE user_id = ?1").bind(auth.user.id).first();
  return json({ following: !!row, email: auth.user.email });
}

/** GET (or POST: one-click from the mail app) /v1/blog/unfollow/<token>: the link in each email, no sign-in. */
export async function unfollow(env: Env, token: string): Promise<Response> {
  await env.DB.prepare("DELETE FROM blog_followers WHERE token = ?1").bind(token).run();
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unfollowed | LocalTC</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#0e1013;color:#e8ecf1;font:16px/1.6 system-ui,sans-serif">
<main style="max-width:440px;padding:24px;text-align:center"><h1 style="font-size:24px">You're no longer following the blog.</h1>
<p style="color:#8a939d">No more emails about new posts. Follow it again any time on <a style="color:#6cc9e8" href="${env.SITE_URL}/blog">the blog</a>.</p></main>`,
  { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'" } });
}

/** DELETE /v1/blog/posts/<slug> (authors only). Its pictures stay: another post may show them. */
export async function remove(env: Env, auth: Auth, slug: string): Promise<Response> {
  author(env, auth);
  const res = await env.DB.prepare("DELETE FROM blog_posts WHERE slug = ?1").bind(slug).run();
  if (!res.meta.changes) throw new HttpError(404, "No such post.");
  return json({ ok: true });
}

/** PUT /v1/blog/images: a picture for a post, the request's body (authors only): {id, url}. */
export async function putImage(env: Env, request: Request, auth: Auth, url: URL): Promise<Response> {
  author(env, auth);
  if (Number(request.headers.get("Content-Length") ?? "0") > MAX_IMAGE) throw new HttpError(413, "The picture is too big: up to 1.5 MB.");
  await limit(env, `blog-image:${auth.user.id}`, 200, 86400);
  const data = await request.arrayBuffer();
  if (data.byteLength > MAX_IMAGE) throw new HttpError(413, "The picture is too big: up to 1.5 MB.");
  const head = new Uint8Array(data.slice(0, 12));
  const type = TYPES.find(([, magic]) => magic.every((b, i) => head[i] === b))?.[0];
  if (!type || data.byteLength < 32 || (type === "image/webp" && String.fromCharCode(...head.slice(8, 12)) !== "WEBP")) {
    throw new HttpError(400, "That isn't a JPEG, PNG, WebP or GIF picture.");
  }
  const id = [...crypto.getRandomValues(new Uint8Array(16))].map((b) => b.toString(16).padStart(2, "0")).join("");
  await env.DB.prepare("INSERT INTO blog_images (id, type, data, author_id, created_at) VALUES (?1, ?2, ?3, ?4, ?5)")
    .bind(id, type, data, auth.user.id, now()).run();
  return json({ id, url: `${url.origin}/v1/blog/images/${id}` });
}

/** GET /v1/blog/images/<id>: a post's picture, for anyone. */
export async function image(env: Env, id: string): Promise<Response> {
  const row = await env.DB.prepare("SELECT type, data FROM blog_images WHERE id = ?1").bind(id).first<{ type: string; data: ArrayBuffer | number[] }>();
  if (!row) throw new HttpError(404, "No such picture.");
  const bytes = row.data instanceof ArrayBuffer ? row.data : new Uint8Array(row.data).buffer;  // D1 gives a BLOB as numbers
  return new Response(bytes, { headers: { "Content-Type": row.type, "Cache-Control": "public, max-age=31536000, immutable",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'", "Cross-Origin-Resource-Policy": "cross-origin" } });
}
