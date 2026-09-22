# LocalTC account server

The **optional** part of LocalTC that runs on a server: accounts, the synced logbook, and the companion
app's live view. LocalTC works fully without it; nothing contacts it until a pilot signs in.

It is a [Cloudflare Worker](https://developers.cloudflare.com/workers/) with a D1 (SQLite) database and one
Durable Object per account for the live view. It lives at `https://api.localtc.tech`.

## What it stores

| Table | What | Why |
|---|---|---|
| `users` | email, password hash (PBKDF2-SHA256), when confirmed | signing in |
| `sessions` | a hash of each sign-in token, the kind of device and its name, last used | staying signed in; "sign out that device" |
| `tokens` | hashes of one-time links (confirm email, reset password), expiring | the links in emails |
| `flights` | the app's logbook lines: airports, gates, runways, times, distance, max altitude, landing rate, readback and alert counts | the dashboard and stats |
| `push_tokens` | APNs device tokens | companion notifications |
| `attempts` | rate-limit counters, keyed by IP or email, gone within a day | stopping password guessing |
| `LiveRoom` (Durable Object) | the flight's latest status: phase, frequencies, ATC's last line | the companion app |

Never stored or accepted: positions or tracks, audio, transcripts, recordings, settings. Fields the server
doesn't know are dropped (`src/flights.ts` `clean`, `src/live.ts` `cleanStatus`).

Unconfirmed accounts are deleted after 7 days, expired sign-ins and links daily (the cron trigger).
`DELETE /v1/me` deletes the account and everything in it at once; `GET /v1/export` gives it all as JSON.

## API

All JSON. The desktop and iOS apps send `Authorization: Bearer <token>`. The website signs in with
`"kind": "web"` and gets an HttpOnly cookie instead (the page never sees the token); requests that change
anything with the cookie must carry `X-LocalTC: 1` (CSRF), and CORS admits only `ALLOWED_ORIGINS`.

| | |
|---|---|
| `POST /v1/auth/register` `{email, password}` | sends a confirmation link; same answer whether or not the address has an account |
| `POST /v1/auth/verify` `{token}` | from the link |
| `POST /v1/auth/login` `{email, password, kind: desktop\|ios\|web, device}` | `{token, user}` (web: a cookie) |
| `POST /v1/auth/logout` | |
| `POST /v1/auth/reset/request` `{email}`, `POST /v1/auth/reset` `{token, password}` | signs out every device |
| `POST /v1/auth/password` `{current, password}` | signs out the other devices |
| `GET /v1/me`, `DELETE /v1/me` `{password}` | the account and its devices; delete it all |
| `DELETE /v1/sessions/:id` | sign out a device |
| `POST /v1/flights` `{flights: [...]}` (up to 100) | upsert by the app's flight id; `{accepted: [ids]}` |
| `GET /v1/flights?limit=&before=` | newest first; `next` pages on |
| `DELETE /v1/flights/:id`, `GET /v1/stats`, `GET /v1/export` | |
| `PUT /v1/live` (desktop), `GET /v1/live`, `GET /v1/live/ws` | the companion's view; the WebSocket gets each update |
| `POST /v1/push-tokens` `{token}`, `DELETE /v1/push-tokens/:token` | APNs, for the iOS app |

## Setting it up

Needs a Cloudflare account with the `localtc.tech` zone, and Node 20+.

```bash
cd server
npm install --legacy-peer-deps
npx wrangler login
npx wrangler d1 create localtc          # copy the database_id into wrangler.toml
npm run migrate                         # creates the tables
npx wrangler secret put RESEND_API_KEY  # email: a Resend account with localtc.tech verified (SPF/DKIM)
npm run deploy                          # also creates the api.localtc.tech custom domain
```

**Password hashing and the Workers plan.** PBKDF2 at 100,000 iterations (the most Workers' WebCrypto
allows) takes more CPU than the Free plan's 10 ms per request, so sign-in needs the Workers Paid plan
($5/month). `PBKDF2_ITERATIONS` can be lowered to stay on the free plan, at the cost of weaker protection if
the database ever leaked; the iterations are stored with each hash, so raising it later works for new
passwords.

**Companion notifications** (optional): an Apple Developer account, an APNs key (.p8), and the app's bundle
id. Set `APNS_TOPIC` in `wrangler.toml` and the secrets `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_PRIVATE_KEY`.
Development builds of the app need `APNS_HOST = "api.sandbox.push.apple.com"`.

## Developing

```bash
npm test                                   # vitest in the Workers runtime (Miniflare); no account needed
npx wrangler d1 migrations apply localtc --local --env dev
npx wrangler dev --env dev                 # http://localhost:8787; emails are printed, not sent
```

Point the app at it with `[account] api_url = "http://localhost:8787"` in the settings file.
