/**
 * Shared flights and Wrapped recaps: a public page for what the pilot chose to share.
 *
 * Sharing is per flight (or per Wrapped period) and off until asked. What's public is a snapshot taken when
 * it's shared (site/cardmodel.js: the route, the date, the numbers, one line of the transcript at most),
 * never the account, the track, gates or the times of day, and the card image the pilot's own app rendered
 * (the free Workers plan hasn't the CPU to draw one here). The page lives at localtc.tech/f/<slug>, a route
 * of this Worker on the site's zone, so chat apps unfurl it with its own title and picture.
 *
 * A slug is random and never the flight's id. Unsharing, deleting the flight or the account removes it;
 * sharing again makes a new one, so an old link stays dead.
 */
import type { Auth } from "./auth";
import { MAX_QUOTE, MOMENT_LABELS, flightCard } from "./cardmodel";
import type { Env } from "./env";
import { HttpError, json, now, readJson } from "./http";
import { limit } from "./rate";
import * as wrapped from "./wrapped";

const MAX_PNG = 1024 * 1024;
const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const SLUG = /^[A-Za-z0-9]{10}$/;
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
const PUBLIC_CACHE = "public, max-age=300";

type Obj = Record<string, unknown>;
type Row = { slug: string; kind: string; ref: string; created_at: string; data: string; has_image?: number };

function newSlug(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(10));
  return [...bytes].map((b) => ALPHABET[b % ALPHABET.length]).join("");
}

export function shareUrl(env: Env, kind: string, slug: string): string {
  return `${env.SITE_URL}/${kind === "wrapped" ? "w" : "f"}/${slug}`;
}

const clip = (v: unknown, max: number): string => (typeof v === "string" ? v.trim().slice(0, max) : "");

/** The pilot's chosen line, checked: it has to be ATC talking to this flight, not any text at all. */
function cleanQuote(raw: unknown, callsign: string): Obj | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw !== "object" || Array.isArray(raw)) throw new HttpError(400, "The quote isn't right.");
  const q = raw as Obj;
  const text = clip(q.text, MAX_QUOTE);
  const kind = clip(q.kind, 20);
  if (!text || !(kind in MOMENT_LABELS)) throw new HttpError(400, "The quote isn't right.");
  const digits = callsign.replace(/\D/g, "");
  if (digits && !text.includes(digits)) throw new HttpError(400, "The quote has to be a call to this flight.");
  const mhz = typeof q.mhz === "number" && q.mhz >= 100 && q.mhz <= 140 ? q.mhz : null;
  return { kind, text, station: clip(q.station, 64), mhz };
}

function cleanNames(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [icao, name] of Object.entries(raw as Obj).slice(0, 4)) {
    if (/^[A-Z0-9]{2,8}$/.test(icao) && typeof name === "string" && /^[\p{L}\p{M} .'-]{1,40}$/u.test(name.trim())) out[icao] = name.trim();
  }
  return out;
}

async function upsert(env: Env, auth: Auth, kind: string, ref: string, data: Obj): Promise<Response> {
  // Changing an existing share (another quote) keeps its link; the card is drawn again.
  const existing = await env.DB.prepare("SELECT slug FROM shares WHERE user_id = ?1 AND kind = ?2 AND ref = ?3")
    .bind(auth.user.id, kind, ref).first<{ slug: string }>();
  const slug = existing?.slug ?? newSlug();
  await env.DB.prepare(
    `INSERT INTO shares (slug, user_id, kind, ref, created_at, data) VALUES (?1, ?2, ?3, ?4, ?5, ?6)
     ON CONFLICT(user_id, kind, ref) DO UPDATE SET data = excluded.data, image = NULL`,
  ).bind(slug, auth.user.id, kind, ref, now(), JSON.stringify(data)).run();
  return json({ slug, kind, ref, url: shareUrl(env, kind, slug), card: data });
}

/** POST /v1/shares {kind: "flight", ref: <flight id>, quote?, names?} or {kind: "wrapped", ref, from, to, tz?}. */
export async function create(env: Env, request: Request, auth: Auth): Promise<Response> {
  const body = await readJson(request, 16 * 1024);
  await limit(env, `share:${auth.user.id}`, 60, 86400);
  if (body.kind === "flight") {
    const ref = clip(body.ref, 64);
    const row = await env.DB.prepare("SELECT * FROM flights WHERE user_id = ?1 AND id = ?2").bind(auth.user.id, ref).first<Obj>();
    if (!row) throw new HttpError(404, "Sync the flight's logbook line first.");
    const card = flightCard(row, { quote: cleanQuote(body.quote, String(row.callsign ?? "")), names: cleanNames(body.names) });
    return upsert(env, auth, "flight", ref, card);
  }
  if (body.kind === "wrapped") {
    const range = wrapped.range(body);
    const recap = await wrapped.compute(env, auth, range);
    if (!recap.flights) throw new HttpError(400, "There's nothing to share for a period with no flights.");
    return upsert(env, auth, "wrapped", range.ref, wrapped.card(recap));
  }
  throw new HttpError(400, "Share a flight or a Wrapped recap.");
}

/** PUT /v1/shares/:slug/image: the card as the app drew it, a PNG. */
export async function putImage(env: Env, request: Request, auth: Auth, slug: string): Promise<Response> {
  if (Number(request.headers.get("Content-Length") ?? "0") > MAX_PNG) throw new HttpError(413, "The image is too big.");
  const found = await env.DB.prepare("SELECT 1 FROM shares WHERE slug = ?1 AND user_id = ?2").bind(slug, auth.user.id).first();
  if (!found) throw new HttpError(404, "No such share.");
  await limit(env, `share-image:${auth.user.id}`, 60, 86400);
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_PNG) throw new HttpError(413, "The image is too big.");
  const head = new Uint8Array(body.slice(0, 8));
  if (body.byteLength < 64 || PNG_MAGIC.some((b, i) => head[i] !== b)) throw new HttpError(400, "The card has to be a PNG.");
  await env.DB.prepare("UPDATE shares SET image = ?1 WHERE slug = ?2 AND user_id = ?3").bind(body, slug, auth.user.id).run();
  return json({ ok: true, size: body.byteLength });
}

export async function remove(env: Env, auth: Auth, slug: string): Promise<Response> {
  const res = await env.DB.prepare("DELETE FROM shares WHERE slug = ?1 AND user_id = ?2").bind(slug, auth.user.id).run();
  if (!res.meta.changes) throw new HttpError(404, "No such share.");
  return json({ ok: true });
}

export async function list(env: Env, auth: Auth): Promise<Response> {
  const { results } = await env.DB.prepare(
    "SELECT slug, kind, ref, created_at, image IS NOT NULL AS has_image FROM shares WHERE user_id = ?1 ORDER BY created_at DESC",
  ).bind(auth.user.id).all<Row>();
  return json({ shares: results.map((r) => ({ ...r, has_image: !!r.has_image, url: shareUrl(env, r.kind, r.slug) })) });
}

// --- the public page ------------------------------------------------------------------------------------------

const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

function describe(card: Obj): { title: string; description: string } {
  if (card.kind === "wrapped") {
    const c = card as { label?: string; flights?: number; hours?: number; distance_nm?: number };
    return {
      title: `${c.label ?? "My flying"}: ATC Wrapped`,
      description: `${c.flights} flights, ${c.hours} hours, ${(c.distance_nm ?? 0).toLocaleString("en-US")} nm, flown with LocalTC.`,
    };
  }
  const c = card as { callsign: string; aircraft: string; origin: { icao: string; name: string }; destination: { icao: string; name: string };
    distance_nm: number; landing_fpm: number | null; air_min: number | null };
  const from = c.origin.name ? `${c.origin.name} (${c.origin.icao})` : c.origin.icao;
  const to = c.destination.name ? `${c.destination.name} (${c.destination.icao})` : c.destination.icao;
  const bits = [c.aircraft, c.distance_nm ? `${c.distance_nm.toLocaleString("en-US")} nm` : "",
    c.landing_fpm != null ? `landed at ${c.landing_fpm} fpm` : ""].filter(Boolean);
  return { title: `${c.callsign}: ${from} to ${to}`, description: `${bits.join(", ")}. Flown with LocalTC.` };
}

function page(env: Env, kind: string, slug: string, card: Obj | null, hasImage: boolean): string {
  const site = env.SITE_URL;
  const url = shareUrl(env, kind, slug);
  const { title, description } = card ? describe(card) : { title: "No longer shared", description: "This flight isn't shared any more." };
  const image = hasImage ? `${url}.png` : `${site}/og.png`;
  // The snapshot goes in as data, never as script: '<' can't end the element.
  const data = card ? JSON.stringify(card).replace(/</g, "\\u003c") : "null";
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${esc(title)} · LocalTC</title>
<meta name="description" content="${esc(description)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="LocalTC">
<meta property="og:url" content="${esc(url)}">
<meta property="og:title" content="${esc(title)}">
<meta property="og:description" content="${esc(description)}">
<meta property="og:image" content="${esc(image)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${esc(title)}">
<meta name="twitter:description" content="${esc(description)}">
<meta name="twitter:image" content="${esc(image)}">
<link rel="icon" href="${site}/logo.svg">
<link rel="stylesheet" href="${site}/styles.css">
<link rel="stylesheet" href="${site}/sharecard.css">
</head>
<body class="share-page">
<main id="share" data-site="${esc(site)}" data-kind="${esc(kind)}">
${card ? "" : `<div class="share-gone"><h1>No longer shared</h1><p>The pilot has stopped sharing this.</p></div>`}
</main>
<footer class="share-foot"><a href="${site}/">Flown with <strong>LocalTC</strong>: free, offline ATC for Microsoft Flight Simulator</a></footer>
<script type="application/json" id="share-data">${data}</script>
<script src="${site}/sharecard.js"></script>
</body>
</html>`;
}

function pageHeaders(env: Env): Record<string, string> {
  const site = env.SITE_URL;
  return {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": PUBLIC_CACHE,
    "Content-Security-Policy": `default-src 'none'; script-src ${site}; style-src ${site}; img-src ${site} data: blob:; ` +
      `font-src ${site} data:; connect-src ${site}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
    "Referrer-Policy": "no-referrer",
  };
}

/** GET /f/<slug>, /w/<slug> (the page) and /f/<slug>.png (its card): public, no sign-in. */
export async function view(env: Env, kind: string, slug: string, png: boolean): Promise<Response> {
  if (!SLUG.test(slug)) throw new HttpError(404, "Not found.");
  const row = await env.DB.prepare(
    `SELECT data, ${png ? "image" : "image IS NOT NULL AS has_image"} FROM shares WHERE slug = ?1 AND kind = ?2`,
  ).bind(slug, kind).first<{ data: string; image?: ArrayBuffer | number[] | null; has_image?: number }>();
  if (png) {
    if (!row?.image) return new Response("Not found.", { status: 404, headers: { "Cache-Control": "no-store" } });
    const bytes = row.image instanceof ArrayBuffer ? row.image : new Uint8Array(row.image).buffer;
    return new Response(bytes, { headers: { "Content-Type": "image/png", "Cache-Control": PUBLIC_CACHE } });
  }
  const card = row ? (JSON.parse(row.data) as Obj) : null;
  return new Response(page(env, kind, slug, card, !!row?.has_image), { status: row ? 200 : 404, headers: pageHeaders(env) });
}
