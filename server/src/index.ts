/**
 * The optional LocalTC account server: accounts, the synced logbook, and the companion app's live view.
 *
 * LocalTC works without it. It holds only what an account needs: an email address, sign-ins, and the
 * logbook's summary lines, the replays of the flights the pilot uploads, and the public pages of what they
 * chose to share. There are no passwords: signing in is a code or link sent by email. See README.md.
 */
import * as auth from "./auth";
import type { Env } from "./env";
import * as flights from "./flights";
import { HttpError, cors, json } from "./http";
import * as live from "./live";
import * as replays from "./replays";
import * as shares from "./shares";
import * as support from "./support";
import * as wrapped from "./wrapped";

export { LiveRoom } from "./live";

type Handler = (env: Env, request: Request, url: URL, params: string[]) => Promise<Response>;

const signedIn = (fn: (env: Env, request: Request, a: auth.Auth, url: URL, params: string[]) => Promise<Response>): Handler =>
  async (env, request, url, params) => fn(env, request, await auth.authenticate(env, request), url, params);

const ROUTES: [string, RegExp, Handler][] = [
  ["GET", /^\/v1\/health$/, async () => json({ ok: true })],
  ["POST", /^\/v1\/support$/, signedIn((env, req, a) => support.submit(env, req, a))],
  ["POST", /^\/v1\/auth\/start$/, (env, req) => auth.start(env, req)],
  ["POST", /^\/v1\/auth\/finish$/, (env, req) => auth.finish(env, req)],
  ["POST", /^\/v1\/auth\/logout$/, signedIn((env, _req, a) => auth.logout(env, a))],
  ["GET", /^\/v1\/me$/, signedIn((env, _req, a) => auth.me(env, a))],
  ["DELETE", /^\/v1\/me$/, signedIn((env, req, a) => auth.deleteMe(env, req, a))],
  ["DELETE", /^\/v1\/sessions\/([\w-]+)$/, signedIn((env, _req, a, _u, p) => auth.revokeSession(env, a, p[0]))],
  ["POST", /^\/v1\/flights$/, signedIn((env, req, a) => flights.upload(env, req, a))],
  ["GET", /^\/v1\/flights$/, signedIn((env, _req, a, url) => flights.list(env, url, a))],
  ["DELETE", /^\/v1\/flights\/([\w-]+)$/, signedIn((env, _req, a, _u, p) => flights.remove(env, a, p[0]))],
  ["PUT", /^\/v1\/flights\/([\w-]+)\/replay$/, signedIn((env, req, a, _u, p) => replays.put(env, req, a, p[0]))],
  ["GET", /^\/v1\/flights\/([\w-]+)\/replay$/, signedIn((env, _req, a, _u, p) => replays.get(env, a, p[0]))],
  ["DELETE", /^\/v1\/flights\/([\w-]+)\/replay$/, signedIn((env, _req, a, _u, p) => replays.remove(env, a, p[0]))],
  ["GET", /^\/v1\/flights\/([\w-]+)\/moments$/, signedIn((env, _req, a, _u, p) => replays.moments(env, a, p[0]))],
  ["GET", /^\/v1\/shares$/, signedIn((env, _req, a) => shares.list(env, a))],
  ["POST", /^\/v1\/shares$/, signedIn((env, req, a) => shares.create(env, req, a))],
  ["PUT", /^\/v1\/shares\/(\w+)\/image$/, signedIn((env, req, a, _u, p) => shares.putImage(env, req, a, p[0]))],
  ["DELETE", /^\/v1\/shares\/(\w+)$/, signedIn((env, _req, a, _u, p) => shares.remove(env, a, p[0]))],
  ["GET", /^\/v1\/wrapped$/, signedIn((env, _req, a, url) => wrapped.get(env, url, a))],
  // The public pages of what's shared: localtc.tech/f/<slug> and /w/<slug> are routed to this Worker.
  ["GET", /^\/f\/(\w+?)(\.png)?$/, (env, _req, _u, p) => shares.view(env, "flight", p[0], !!p[1])],
  ["GET", /^\/w\/(\w+?)(\.png)?$/, (env, _req, _u, p) => shares.view(env, "wrapped", p[0], !!p[1])],
  ["GET", /^\/v1\/stats$/, signedIn((env, _req, a) => flights.stats(env, a))],
  ["GET", /^\/v1\/export$/, signedIn((env, _req, a) => flights.exportAll(env, a))],
  ["PUT", /^\/v1\/live$/, signedIn((env, req, a) => live.put(env, req, a))],
  ["GET", /^\/v1\/live$/, signedIn((env, _req, a) => live.get(env, a))],
  ["GET", /^\/v1\/live\/ws$/, signedIn((env, req, a) => live.socket(env, req, a))],
  ["PUT", /^\/v1\/live\/frame$/, signedIn((env, req, a) => live.frame(env, req, a))],
  ["POST", /^\/v1\/live\/radio$/, signedIn((env, req, a) => live.radio(env, req, a))],
  ["POST", /^\/v1\/live\/alert$/, signedIn((env, req, a) => live.alert(env, req, a))],
  ["PUT", /^\/v1\/live\/airports$/, signedIn((env, req, a) => live.airports(env, req, a))],
  ["PUT", /^\/v1\/live\/connect$/, signedIn((env, req, a) => live.putConnect(env, req, a))],
  ["GET", /^\/v1\/live\/connect$/, signedIn((env, _req, a) => live.getConnect(env, a))],
  ["POST", /^\/v1\/push-tokens$/, signedIn((env, req, a) => live.addPushToken(env, req, a))],
  ["DELETE", /^\/v1\/push-tokens\/(\w+)$/, signedIn((env, _req, a, _u, p) => live.removePushToken(env, a, p[0]))],
];

async function route(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  let pathMatched = false;
  for (const [method, pattern, handler] of ROUTES) {
    const m = pattern.exec(url.pathname);
    if (!m) continue;
    pathMatched = true;
    if (method === request.method) return handler(env, request, url, m.slice(1));
  }
  throw new HttpError(pathMatched ? 405 : 404, pathMatched ? "Method not allowed." : "Not found.");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const headers = cors(env, request);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    let response: Response;
    try {
      response = await route(request, env);
    } catch (err) {
      if (err instanceof HttpError) response = json({ error: err.message }, err.status);
      else {
        console.error(err);
        response = json({ error: "Something went wrong on the server." }, 500);
      }
    }
    if (response.status === 101) return response; // a WebSocket: headers can't change
    const out = new Response(response.body, response);
    for (const [k, v] of Object.entries(headers)) out.headers.set(k, v);
    out.headers.set("X-Content-Type-Options", "nosniff");
    return out;
  },

  async scheduled(_event: ScheduledController, env: Env): Promise<void> {
    await auth.cleanup(env);
  },
} satisfies ExportedHandler<Env>;
