import type { Env } from "./env";
import { HttpError, json, readJson, str } from "./http";
import { clientIp, limit } from "./rate";

/*
 * ChartFox sign-in for the LocalTC app (ChartFox's OAuth 2.0, with this server holding the client secret).
 *
 * ChartFox's API needs an OAuth client whose secret must never be in a program people download, so the app never
 * sees it: the app opens "GET /v1/chartfox/authorize?state=..." in the pilot's browser, this sends the browser on to
 * ChartFox's own sign-in with LocalTC's client id, and ChartFox brings it back to "/v1/chartfox/callback" with a code.
 * The code is exchanged here (with the secret) for the pilot's tokens, which wait under the app's random state until
 * the app collects them once ("POST /v1/chartfox/token"), for at most PENDING_S. Refreshing ("POST
 * /v1/chartfox/refresh") goes through here for the same reason. Nothing is kept beyond that: the tokens live on the
 * pilot's PC, in its credential store.
 *
 * No LocalTC account is needed for any of it. Secrets: CHARTFOX_CLIENT_ID, CHARTFOX_CLIENT_SECRET (wrangler secret
 * put); without them ChartFox sign-in is off (503).
 */

const AUTHORIZE = "https://api.chartfox.org/oauth/authorize";
const TOKEN = "https://api.chartfox.org/oauth/token";
const SCOPES = "airports:view charts:index charts:view";
const PENDING_S = 600;
const STATE = /^[A-Za-z0-9_-]{32,128}$/;

/** How this server reaches ChartFox (tests replace it). */
export const chartfoxHttp = { send: (request: Request): Promise<Response> => fetch(request) };

function configured(env: Env): { id: string; secret: string } {
  if (!env.CHARTFOX_CLIENT_ID || !env.CHARTFOX_CLIENT_SECRET) {
    throw new HttpError(503, "ChartFox sign-in isn't set up on this server.");
  }
  return { id: env.CHARTFOX_CLIENT_ID, secret: env.CHARTFOX_CLIENT_SECRET };
}

function callbackUrl(url: URL): string {
  return `${url.origin}/v1/chartfox/callback`;
}

/** The pilot's browser, from the app: on to ChartFox's sign-in. */
export async function authorize(env: Env, request: Request, url: URL): Promise<Response> {
  const { id } = configured(env);
  const state = url.searchParams.get("state") ?? "";
  if (!STATE.test(state)) throw new HttpError(400, "Start the ChartFox sign-in from LocalTC.");
  await limit(env, `chartfox:${clientIp(request)}`, 30, 3600);
  const to = new URL(AUTHORIZE);
  to.searchParams.set("response_type", "code");
  to.searchParams.set("client_id", id);
  to.searchParams.set("redirect_uri", callbackUrl(url));
  to.searchParams.set("scope", SCOPES);
  to.searchParams.set("state", state);
  return Response.redirect(to.toString(), 302);
}

async function exchange(env: Env, body: Record<string, string>): Promise<Record<string, unknown>> {
  const { id, secret } = configured(env);
  const form = new URLSearchParams({ ...body, client_id: id, client_secret: secret });
  const res = await chartfoxHttp.send(new Request(TOKEN, {
    method: "POST", body: form, headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
  }));
  let data: Record<string, unknown> = {};
  try { data = (await res.json()) as Record<string, unknown>; } catch { /* not JSON */ }
  if (!res.ok || typeof data.access_token !== "string") {
    throw new HttpError(502, `ChartFox didn't sign you in (${res.status}${data.error ? `: ${data.error}` : ""}).`);
  }
  return { access_token: data.access_token, refresh_token: data.refresh_token ?? null,
    expires_in: typeof data.expires_in === "number" ? data.expires_in : null, token_type: data.token_type ?? "Bearer",
    scope: data.scope ?? SCOPES };
}

function page(title: string, body: string, status = 200): Response {
  const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
  return new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>${esc(title)}</title><body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.4rem">${esc(title)}</h1><p>${esc(body)}</p>
<p style="color:#666;font-size:.9rem">Chart data powered by <a href="https://chartfox.org">ChartFox</a>.</p></body>`,
  { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
}

/** ChartFox back with the code: exchanged, and the tokens held for the app to collect. */
export async function callback(env: Env, _request: Request, url: URL): Promise<Response> {
  const state = url.searchParams.get("state") ?? "";
  const code = url.searchParams.get("code") ?? "";
  if (url.searchParams.get("error")) return page("ChartFox not connected", "ChartFox sign-in was cancelled. You can try again from LocalTC.", 400);
  if (!STATE.test(state) || !code) return page("ChartFox not connected", "That sign-in link isn't complete. Start again from LocalTC.", 400);
  let tokens: Record<string, unknown>;
  try {
    tokens = await exchange(env, { grant_type: "authorization_code", code, redirect_uri: callbackUrl(url) });
  } catch (err) {
    return page("ChartFox not connected", err instanceof HttpError ? err.message : "Something went wrong.", 502);
  }
  const nowS = Math.floor(Date.now() / 1000);
  await env.DB.prepare("INSERT OR REPLACE INTO chartfox_pending (state, tokens, expires_at) VALUES (?1, ?2, ?3)")
    .bind(state, JSON.stringify(tokens), nowS + PENDING_S).run();
  return page("ChartFox connected", "You can close this tab and go back to LocalTC: your charts are on their way.");
}

/** The app collects its tokens, once. 404 while the pilot hasn't finished signing in. */
export async function token(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request, 4 * 1024);
  const state = str(body.state, "state", 128);
  if (!STATE.test(state)) throw new HttpError(400, "That isn't a sign-in from LocalTC.");
  await limit(env, `chartfox-token:${clientIp(request)}`, 400, 3600);
  const nowS = Math.floor(Date.now() / 1000);
  const row = await env.DB.prepare("DELETE FROM chartfox_pending WHERE state = ?1 AND expires_at > ?2 RETURNING tokens")
    .bind(state, nowS).first<{ tokens: string }>();
  if (!row) return json({ error: "Not signed in to ChartFox yet." }, 404);
  return json(JSON.parse(row.tokens));
}

/** A new access token from the refresh token (the secret is needed for that too). */
export async function refresh(env: Env, request: Request): Promise<Response> {
  const body = await readJson(request, 8 * 1024);
  const refreshToken = str(body.refresh_token, "refresh_token", 4096);
  await limit(env, `chartfox-refresh:${clientIp(request)}`, 60, 3600);
  return json(await exchange(env, { grant_type: "refresh_token", refresh_token: refreshToken, scope: SCOPES }));
}

/** Old sign-ins never collected. */
export async function cleanup(env: Env): Promise<void> {
  await env.DB.prepare("DELETE FROM chartfox_pending WHERE expires_at <= ?1").bind(Math.floor(Date.now() / 1000)).run();
}
