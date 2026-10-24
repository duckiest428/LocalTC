/**
 * The blog (localtc.tech/blog): news about LocalTC, written on the blog page itself by its authors and read by
 * anyone.
 *
 * Only the accounts in BLOG_AUTHORS can write: the comma-separated addresses in wrangler.toml, checked against the
 * signed-in account's own address (which signing in proves: there are no passwords, a code is emailed). Anyone
 * else, signed in or not, gets the published posts and nothing more. A post is Markdown; its pictures are uploaded
 * here (JPEG, PNG, WebP or GIF, up to 1.5 MB: the page shrinks a photo before it sends it) and served from
 * /v1/blog/images/<id>. Posts written in the repository (site/blog-posts.json) are the site's own, not here.
 */
import type { Auth } from "./auth";
import type { Env } from "./env";
import { HttpError, json, now, readJson } from "./http";
import { limit } from "./rate";

const DEFAULT_AUTHORS = "duckiesty@gmail.com";
const MAX_IMAGE = 1_500_000;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const TYPES: [string, number[]][] = [
  ["image/png", [0x89, 0x50, 0x4e, 0x47]], ["image/jpeg", [0xff, 0xd8, 0xff]], ["image/gif", [0x47, 0x49, 0x46, 0x38]],
  ["image/webp", [0x52, 0x49, 0x46, 0x46]],  // RIFF; WEBP at 8 is checked below
];

type Post = { slug: string; title: string; summary: string; body: string; cover: string | null; author_name: string;
  published_at: string | null; created_at: string; updated_at: string };

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
  return json({ posts: results.map((p) => ({ ...card(p), body: p.body })) });
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
  return json({ slug, published_at: published, updated_at: at });
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
