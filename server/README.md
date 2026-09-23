# LocalTC account server

The **optional** part of LocalTC that runs on a server: accounts, the synced logbook, and the companion
app's live view. LocalTC works fully without it; nothing contacts it until a pilot signs in.

It is a [Cloudflare Worker](https://developers.cloudflare.com/workers/) with a D1 (SQLite) database and one
Durable Object per account for the live view. It lives at `https://api.localtc.tech`.

## What it stores

| Table | What | Why |
|---|---|---|
| `users` | email, when it was first signed in to | signing in |
| `sessions` | a hash of each sign-in token, the kind of device and its name, last used | staying signed in; "sign out that device" |
| `logins` | hashes of the emailed sign-in code and link, wrong-code count, 15-minute expiry | signing in by email |
| `flights` | the app's logbook lines: airports, gates, runways, times, distance, max altitude, landing rate, readback and alert counts | the dashboard and stats |
| `push_tokens` | APNs device tokens | companion notifications |
| `attempts` | rate-limit counters, keyed by IP or email, gone within a day | stopping code guessing and email floods |
| `LiveRoom` (Durable Object) | stored: the flight's latest status (phase, frequencies, ATC's last line) and the PC's local-network address and key; **in memory only**, while a phone watches remotely: position, traffic, the radio log | the companion app |

Never stored: positions or tracks, the radio log, audio, recordings, settings. Position, traffic and radio
text pass through `LiveRoom`'s memory to a phone watching away from the PC's network (the desktop sends them
only while one is), and are gone when the room is evicted. Fields the server doesn't know are dropped
(`src/flights.ts` `clean`, `src/live.ts` `cleanStatus`, `cleanFrame`, `cleanRadio`). The messages are in
[`docs/companion-protocol.md`](../docs/companion-protocol.md).

**There are no passwords.** Signing in (and creating an account, the first time) emails a 6-digit code and a
link: the apps take the code, the website either. Each works once, for 15 minutes, only from the
newest email, and five wrong codes void it. Accounts asked for but never signed in to are deleted after a day, expired
sign-ins and codes daily (the cron trigger).
`DELETE /v1/me` deletes the account and everything in it at once; `GET /v1/export` gives it all as JSON.

## API

All JSON. The desktop and iOS apps send `Authorization: Bearer <token>`. The website signs in with
`"kind": "web"` and gets an HttpOnly cookie instead (the page never sees the token); requests that change
anything with the cookie must carry `X-LocalTC: 1` (CSRF), and CORS admits only `ALLOWED_ORIGINS`.

| | |
|---|---|
| `POST /v1/auth/start` `{email}` | emails a code and a link (creating the account the first time); the same answer whether or not the address has one |
| `POST /v1/auth/finish` `{email, code, kind: desktop\|ios\|web, device}` or `{token, kind: web}` | `{token, user}` (web: a cookie) |
| `POST /v1/auth/logout` | |
| `GET /v1/me`, `DELETE /v1/me` `{email}` | the account and its devices; delete it all (the address typed to confirm) |
| `DELETE /v1/sessions/:id` | sign out a device |
| `POST /v1/flights` `{flights: [...]}` (up to 100) | upsert by the app's flight id; `{accepted: [ids]}` |
| `GET /v1/flights?limit=&before=` | newest first; `next` pages on |
| `DELETE /v1/flights/:id`, `GET /v1/stats`, `GET /v1/export` | |
| `PUT /v1/live` (desktop), `GET /v1/live`, `GET /v1/live/ws` | the status; each answer to the desktop has `watchers`; the WebSocket gets every message (the website's Flight Tracker opens it with its cookie, from `ALLOWED_ORIGINS` only) |
| `PUT /v1/live/frame` `{own, traffic}`, `POST /v1/live/radio` `{lines}`, `POST /v1/live/alert`, `PUT /v1/live/airports` `{airports}` | desktop → phone or tracker, in memory only |
| `PUT /v1/live/connect` `{lan, key}`, `GET /v1/live/connect` | where the phone finds the PC on its network (private addresses only) |
| `POST /v1/push-tokens` `{token}`, `DELETE /v1/push-tokens/:token` | APNs, for the iOS app |
| `POST /v1/support` `{kind: bug\|support\|feedback, message, version?, platform?, source?}` | signed in; emailed to `SUPPORT_EMAIL` with the account's address as Reply-To, stored nowhere; 5 an hour per IP, 10 a day per account |

## Setting it up

Needs a Cloudflare account with the `localtc.tech` zone, and Node 20+.

```bash
cd server
npm install --legacy-peer-deps
npx wrangler login
npx wrangler d1 create localtc          # copy the database_id into wrangler.toml
npm run migrate                         # creates the tables
npx wrangler secret put RESEND_API_KEY  # the sign-in emails: a Resend account with localtc.tech verified
npx wrangler secret put SUPPORT_EMAIL   # where the Dashboard's and the app's support messages go (your own inbox)
npm run deploy                          # also creates the api.localtc.tech custom domain
```

It all fits the free plans: Workers Free, D1 Free, and Resend's free tier (100 emails a day).

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
