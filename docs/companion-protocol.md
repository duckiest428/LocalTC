# Companion protocol, v1

What the LocalTC companion app receives about a running flight. The same messages travel two ways:

| Path | When | Transport | Auth |
|---|---|---|---|
| **Local network** | the phone is on the PC's Wi-Fi | `GET http://<pc>:47800/companion/v1/stream`, Server-Sent Events: `event: <type>`, `data: <json>` | `Authorization: Bearer <companion key>` |
| **Relay** | anywhere else | `wss://api.localtc.tech/v1/live/ws`, one JSON text frame per message: `{"type", "data"}` | `Authorization: Bearer <account token>` |

The phone finds the PC with `GET /v1/live/connect` on the account (`{lan: ["http://192.168.1.20:47800"], key}`)
or by Bonjour (`_localtc._tcp`), tries each local URL with `GET /companion/v1/hello` (1.5 s timeout), and
falls back to the relay. The companion key is made fresh each time LocalTC starts and reaches the phone only
through the signed-in account.

Over the relay, `own`, `traffic`, `radio` and `alert` flow only while a phone is connected and the pilot allows
it (`[account] companion_remote_map`); the server holds them in memory and never stores them. `status` always
flows while the account's companion setting is on.

## Messages

### `hello` (first message on either path)
```json
{"protocol": 1, "version": "0.3.0", "status": {...}, "own": {...}|null, "traffic": [...],
 "route": {...}|null, "radio": [...]}
```
The relay's `hello` has no `route` (the phone gets it on the local network only) and no `version`.

### `status`
```json
{"active": true, "callsign": "FFT2084", "aircraft": "A320neo", "origin": "KSAN", "destination": "KPHX",
 "phase": "CRUISE", "phase_label": "Cruise", "squawk": "4512", "altitude_ft": 35000, "runway": "26",
 "gate": "Gate C14", "tuned": {"station": "Albuquerque Center", "mhz": 133.65},
 "next": {"station": "Phoenix Approach", "mhz": 119.2}, "ete": {"nm": 80, "min": 11},
 "last_atc": {"station": "Albuquerque Center", "mhz": 133.65, "text": "Frontier 2084, roger."}}
```
`{"active": false}` when no flight is running.

### `own` (about 4 Hz locally, 1 Hz over the relay)
```json
{"t": 1234.5, "lat": 33.1, "lon": -115.2, "alt": 35000, "agl": 34000, "hdg": 88, "hdg_mag": 77,
 "gs": 450, "vs": 0, "ground": false, "com1": 133.65, "com2": 121.5, "squawk": "4512"}
```
`alt` is indicated altitude in feet, `hdg` true heading in degrees, `gs` knots, `vs` feet per minute.

### `traffic` (every few seconds; the whole list each time)
```json
[{"id": 7, "callsign": "SWA118", "type": "B738", "lat": 33.2, "lon": -115.0, "alt": 35000, "hdg": 270,
  "gs": 440, "ground": false}]
```

### `route` (local network only)
```json
{"origin": "KSAN", "destination": "KPHX", "fixes": [{"ident": "HYDRR", "lat": 33.5, "lon": -112.5}]}
```

### `radio` (one line)
```json
{"kind": "atc|pilot|copilot|readback|atis|phase|tuned|alert|system", "t": 1234.5,
 "station": "Albuquerque Center", "mhz": 133.65, "text": "...", "ok": true, "level": "warn"}
```
Only `kind`, `t` and `text` are always present.

### `alert`
```json
{"kind": "handoff|clearance|traffic|emergency", "title": "Albuquerque Center",
 "body": "Frontier 2084, contact Phoenix Approach 119.2.", "mhz": 133.65}
```
The phone shows a banner, and a local notification if it's in the background.
