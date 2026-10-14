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

Over the relay, `own`, `trail`, `traffic`, `route`, `zones`, `radio`, `airports` and `alert` flow only while a phone (or the website's
Flight Tracker) is connected and the pilot allows
it (`[account] companion_remote_map`); the server holds them in memory and never stores them. `status` always
flows while the account's companion setting is on.

## Messages

### `hello` (first message on either path)
```json
{"protocol": 1, "version": "0.4.0", "status": {...}, "own": {...}|null, "traffic": [...],
 "route": {...}|null, "radio": [...], "airports": [...], "trail": [[33.1, -115.2], ...], "zones": {...}|null}
```
The relay's `hello` has no `version`. `trail` is the path flown so far, oldest first; `zones` the ATC layer
of the desktop's Live Map (both left out by older desktops, and `route` and `zones` by the relay before 0.4).

### `trail` (over the relay: when someone starts watching mid-flight)
```json
[[32.7336, -117.1897], [32.7401, -117.2012], ...]
```
The path flown this flight, `[lat, lon]` pairs rounded to 4 decimals, a point every ~200 m of movement (at most
1,500 from the desktop; a long flight's are thinned evenly, so the whole route stays). It replaces any path
drawn so far; the `own` positions that follow carry on from its end. The relay keeps it in memory, adds the
positions it passes on, and hands it to every new viewer in `hello`; it's dropped when the flight ends.

### `status`
```json
{"active": true, "callsign": "FFT2084", "aircraft": "A320neo", "origin": "KSAN", "destination": "KPHX",
 "phase": "CRUISE", "phase_label": "Cruise", "squawk": "4512", "altitude_ft": 35000, "runway": "26",
 "gate": "Gate C14", "rules": "IFR", "tuned": {"station": "Albuquerque Center", "mhz": 133.65},
 "next": {"station": "Phoenix Approach", "mhz": 119.2}, "ete": {"nm": 80, "min": 11},
 "last_atc": {"station": "Albuquerque Center", "mhz": 133.65, "text": "Frontier 2084, roger."}, "crew": true}
```
`{"active": false}` when no flight is running. `rules` is `"IFR"` or `"VFR"`: the map opens on the matching
view (the pilot can switch). Older desktops leave it out; read that as IFR. `crew` is true while the copilot is
on the intercom: a typed line can go to it (below).

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

### `route`
```json
{"origin": "KSAN", "destination": "KPHX", "fixes": [{"ident": "HYDRR", "lat": 33.5, "lon": -112.5, "kind": "wpt"}]}
```
The flight plan's route, sent when it changes and to each new viewer. `kind` `"apt"` marks the airports at the
ends (drawn without a label). A plan typed without fixes has none: draw a straight line between the airports
in `zones`. The desktop sends it to the relay with `PUT /v1/live/map {"route": ...}`.

### `zones`
The ATC layer the desktop's Live Map draws (`ui/zones.py`), worked out for the flight as a whole: again after a
handoff, a taxi clearance or a new runway or gate, and every 30 s as the flight moves. `null` when the flight
ends. Points are `[lat, lon]`, rounded to 3 decimals (5 for the taxi route).
```json
{"rules": "IFR", "center": "Los Angeles Center",
 "tuned": {"station": "SoCal Departure", "controller": "departure", "mhz": 124.3}, "next": null,
 "centers": [{"id": "KZLA", "name": "Los Angeles Center", "kind": "center", "label": [34.1, -117.0],
              "rings": [[[33.0, -118.0], ...]], "active": true, "route": true, "working": false}],
 "terminals": [{"id": "SCT", "name": "SoCal Departure", "label": [...], "rings": [...], "active": true,
                "working": true, "icao": "KSAN", "role": "departure"}],
 "final": {"icao": "KPHX", "runway": "26", "ring": [[...], [...], [...], [...]]}|null,
 "taxi": {"icao": "KSAN", "to": "runway 27", "points": [[...], ...], "taxiways": ["B", "A"]}|null,
 "gate": {"icao": "KPHX", "name": "Gate C14", "lat": 33.43, "lon": -112.0}|null,
 "airports": [{"icao": "KSAN", "name": "...", "lat": 32.73, "lon": -117.19, "role": "departure", "tower_nm": 5,
               "stations": [{"controller": "tower", "station": "Lindbergh Tower", "mhz": 118.3, "tuned": false, "next": true}],
               "runways": [{"name": "09/27", "lat": 32.73, "lon": -117.19, "heading_true": 105.0, "length_m": 2865}]}],
 "classes": [{"icao": "KSAN", "name": "...", "lat": 32.73, "lon": -117.19, "class": "B", "label": "Class B",
              "towered": true, "rings": [{"nm": 10, "floor": 0, "ceiling": 10000}, ...]}]}
```
- `centers`: the centres the route passes through. `active` is the one the aircraft is in, `working` the one
  talking to it, and `label` is where to put the name (on the route).
- `terminals`: the departure and approach areas: departure works the flight until it leaves its area, and
  approach takes it in there.
- `final`: the stretch of final where approach clears the approach and sends the flight to tower.
- `airports`: the flight's airports, their controllers as badges (D clearance, G ground, T tower, A departure or
  approach, `tuned` and `next` marked), their runways, and `tower_nm`, the tower's control zone as a circle.
- `classes`: the VFR map's airspace class around each of the flight's airports, with each ring's floor and
  ceiling in feet MSL (floor 0 is the surface).
- The desktop sends it to the relay with `PUT /v1/live/map {"zones": ...}`; `null` there clears it.

### `radio` (one line)
```json
{"kind": "atc|pilot|copilot|crew|intercom|chatter|readback|atis|phase|tuned|alert|system", "t": 1234.5,
 "station": "Albuquerque Center", "mhz": 133.65, "text": "...", "ok": true, "level": "warn", "radio": 1,
 "audio": "0123456789abcdef"}
```
Only `kind`, `t` and `text` are always present. `radio` is the COM (1 or 2) a call of yours or the copilot's went out
on; for ATC's, compare `mhz` with `own`'s `com1` and `com2`. `audio`, when the desktop keeps the transmissions to play
again (its "Play buttons" setting), is the id the line's audio is kept under (below).

### A transmission's audio

An 8 kHz, 8-bit mono WAV (20 s at most): ATC's and the copilot's words as they were heard, the pilot's as the
microphone took them. Only fetched when somebody presses play.

- On the same Wi-Fi: `GET http://<pc>:47800/companion/v1/clip?id=<audio>` with the companion key. The desktop keeps
  the last 80, in memory; a line just shown may still be being synthesized, so the answer waits a few seconds for it.
  `404` when it isn't kept.
- Through the account: `GET /v1/live/clip/<audio>` (signed in; the website with its cookie, from its own pages). The
  desktop sends each one with `PUT /v1/live/clip/<audio>` (the WAV as the body) while somebody watches; the relay holds
  the last 40 in memory, never stored, and forgets them with the flight.

### `alert`
```json
{"kind": "handoff|clearance|traffic|emergency", "title": "Albuquerque Center",
 "body": "Frontier 2084, contact Phoenix Approach 119.2.", "mhz": 133.65}
```
The phone shows a banner, and a local notification if it's in the background.

### `airports` (when the flight's airports are known, and again when the ATIS changes)
```json
[{"icao": "KPHX", "name": "Phoenix Sky Harbor Intl", "role": "arrival", "lat": 33.43, "lon": -112.01,
  "elev_ft": 1135, "atis": "D",
  "frequencies": [{"label": "TWR", "kind": "tower", "mhz": 118.7, "name": "Phoenix Tower"}],
  "runways": [{"name": "08/26", "length_ft": 11489, "heading_mag": 76, "ils": ["26 (IPHX)"]}]}]
```
The departure and arrival airports (one if they're the same): published data for the Frequencies and
Airports tabs.

## Talking from the phone or the website

On the same Wi-Fi: `POST http://<pc>:47800/companion/v1/say` with the companion key and `{"text": "Phoenix
Approach, Frontier 2084, with you"}` transmits the call on COM1, exactly as if it were typed in the app (with
`"to": "com2"`, on COM2). `200 {"ok": true}`, or `409 {"error": ...}` when no flight is running. With `"to": "crew"` the words go to the
copilot on the intercom instead (as if said on the intercom key): its answer comes back as a `crew` radio line,
and the words as an `intercom` one.

Anywhere else, through the account: `POST /v1/live/say` (signed in; the website's Flight Tracker with its cookie
and `X-LocalTC: 1`, from the site's own pages) with `{"text": ..., "to": "atc" | "com2" | "crew"}`. The relay holds the call in
memory, five at most, for a minute: `200 {"ok": true, "waiting": n}`, `409` when no flight is on (or for the
copilot when the status doesn't say `crew`), `400` for an empty call. The
desktop app picks the waiting calls up from the `calls` list in the answer to its next live update (`PUT
/v1/live`, `/frame`, `/map`, `POST /radio`, `PUT /airports`), or with `GET /v1/live/calls` every couple of seconds
while somebody watches, and transmits each as if typed (each call says its `to`). A call it can't transmit comes back in the radio log as
an `alert` line starting "Not transmitted".

### `efb` (when the EFB changes: the checklist, Airport Ops, ATC's operations, the notes; every few minutes the briefings)
```json
{"checklist": {"id": "before_start", "name": "Before start", "status": "waiting", "index": 3, "source": "...",
               "lines": [{"challenge": "Parking brake", "response": "Set", "result": "ok", "why": ""}]},
 "book": {"name": "Airbus A320 family", "source": "...", "checklists": [{"id": "before_start", "name": "Before start", "phase": "before_start"}]},
 "ops": {"icao": "KSEA", "role": "departure", "gsx": false, "services": {"fuel": {"state": "in progress", "source": "the sim's fuel"}},
         "deice": {"required": true, "position": 2, "wait_min": 12, "why": "-2°C with snow", "state": "queued"},
         "messages": [{"t": 1791983400.5, "kind": "service", "text": "Fuelling in progress.", "from": "Airport Ops"}],
         "requests": [{"gate": "B25", "icao": "KLAX", "state": "approved", "why": "Gate B25 approved."}], "closures": [], "notes": []},
 "atc": {"conditions": [{"kind": "behind_landing", "traffic": "the landing Airbus A320", "runway": "16L", "state": "active"}],
         "hold": {"fix": "BLAKO", "lat": 47.8, "lon": -122.3, "inbound": 160, "turn": "right", "leg_min": 1.0, "altitude_ft": 8000,
                  "reason": "arrival traffic", "efc": "1432", "position": 5, "delay_min": 8, "entry": "direct", "state": "holding",
                  "laps": 1, "racetrack": [[47.8, -122.3], ...]},
         "routing": [{"kind": "reroute", "fix": "BRAVO", "heading": 40, "reason": "restricted airspace R-6714", "state": "active",
                      "line": [[46.0, -121.5], ...]}],
         "hazards": [...], "minimum_altitude_ft": 4200},
 "scratchpad": [{"id": "a1b2", "text": "SQ 4521", "kind": "squawk", "t": 1791983400, "pinned": false, "phase": "PARKED",
                 "source": "phone", "picked": {"squawk": "4521"}}],
 "achievements": {"earned": 5, "total": 18, "new": [{"id": "first_flight", "title": "First flight", "detail": "KSEA to KLAX"}]},
 "chartfox": {"connected": true, "credit": {"text": "Chart data powered by ChartFox", "url": "https://chartfox.org",
                                           "privacy": "https://chartfox.org/privacy", "disclaimer": "..."}},
 "briefings": {"KSEA": {"icao": "KSEA", "metar": "...", "taf": {...}, "notams": {"items": [{"id": "A1/26", "text": "...",
                                                                                            "works_to": [["runway_closed", "16L/34R"]], "in_force": true}]}}},
 "charts": {"KSEA": {"Ground": [{"id": "...", "name": "AIRPORT DIAGRAM", "type": "Ground", "has_georef": true}]}},
 "hazards": [{"id": "T1", "kind": "tfr", "name": "Stadium", "hazard": "", "floor_ft": 0, "ceiling_ft": 3000, "polygon": [[47.5, -122.3], ...]}]}
```
Every part is optional. The desktop sends it to the relay with `PUT /v1/live/map {"efb": ...}` (only these parts, 96 KB
at most, held in memory like the rest of the map); the relay's `hello` carries the latest.

## The EFB from the phone

On the same Wi-Fi: `POST http://<pc>:47800/companion/v1/efb` with the companion key and a command, answered with
LocalTC's JSON (`409 {"error": ...}` when it can't):

- `{"action": "checklist", "do": "run" | "pause" | "resume" | "skip" | "stop" | "check", "id": "before_start"}`: said to the
  copilot on the intercom as the pilot would ("Before start checklist", "hold the checklist", "checked").
- `{"action": "scratchpad", "op": "add", "text": ..., "kind": "", "pinned": false}`, `"op": "edit"` (`id`, `pinned` or
  `text`), `"op": "delete"` (`id`).
- `{"action": "gate", "gate": "B25"}`: Airport Ops asks ATC for it (`{"ok": true, "why": "Gate B25 approved."}`).
- `{"action": "ops_note", "text": ..., "kind": "maintenance" | "dispatch" | "other"}`.
- Questions, on the same Wi-Fi only: `{"action": "gates", "icao": ""}` (the gate map: each stand's name, position,
  occupied, fits, airline), `{"action": "chart", "id": ..., "height": 800}` (its viewing link, and `own`: where the
  aircraft is on it in pixels at that height), `{"action": "achievements"}`, `{"action": "briefing", "icao": ...}`,
  `{"action": "charts", "icao": ...}`, `{"action": "perf"}`, `{"action": "checklists"}`.

Anywhere else, through the account: `POST /v1/live/say {"to": "efb", "command": {...}}` for the four that change something
(`checklist`, `scratchpad`, `gate`, `ops_note`; 2,000 characters at most): held like a typed call, picked up by the desktop
the same way; no answer, the next `efb` message shows the outcome.
