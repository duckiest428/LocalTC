# Replay format (v1)

A replay is one flight, made to rewatch. It holds the aircraft's track, the radio transcript and the moments
that matter. The desktop app builds it from the flight's recording (`localtc/replay/rewatch.py`
`build_replay`) and caches it next to the recording as `replay.json.gz`. The app's Logbook plays it
straight from there.

A pilot can also upload it to the account, one flight at a time from the Logbook or every flight with
`[account] upload_replays`. Once uploaded, it plays on the Dashboard and in the companion app.

It's JSON, gzipped for storage and upload. **All times are seconds from the replay's start**, and
`flight.started_at` says when that was.

```jsonc
{
  "v": 1,
  "flight": {
    "id": "…",                     // the logbook line's id ("" when built without one)
    "callsign": "DAL2543", "aircraft": "A220-300", "origin": "KDEN", "destination": "KSEA",
    "livery": "Airbus A220-300 Delta Air Lines",   // the sim's title for the aircraft ("" if unknown; optional)
    "departure_runway": "25", "arrival_runway": "16L", "departure_gate": "", "arrival_gate": "Gate B7",
    "started_at": "2026-09-23T22:18:21Z",
    "zulu0": 80302,                // the sim's time of day at the start, seconds after 00:00Z (null if unknown)
    "duration_s": 10706.7
  },
  "airports": { "KDEN": { "lat": 39.8617, "lon": -104.6732, "elev": 5434, "name": "Denver" } },   // origin, destination, alternate; name optional
  "route": [ { "ident": "MUGBE", "lat": 39.9321, "lon": -104.9046 } ],        // the flight plan's fixes
  "track": {                       // columns of equal length, one entry per point
    "t":   [0, 5.0, …],            // seconds
    "lat": [39.86, …], "lon": [-104.67, …],   // 5 decimals
    "alt": [5380, …],              // indicated altitude, ft (to 10)
    "gs":  [0, …],                 // ground speed, kt
    "hdg": [180, …],               // true heading, degrees
    "vs":  [0, …],                 // vertical speed, fpm (to 10)
    "gnd": [1, …]                  // 1 on the ground
  },
  "radio": [                       // the app's radio log (localtc/radiolog.py radio_line), in order
    { "kind": "pilot", "t": 60.0, "text": "Denver Clearance, Delta 2543, …" },
    { "kind": "atc", "t": 61.9, "station": "Denver Clearance", "mhz": 118.75, "text": "Delta 2543, cleared …" },
    { "kind": "pilot", "t": 119.5, "text": "…", "ok": false, "readback": "readback incomplete: squawk" },
    { "kind": "tuned", "t": 700.2, "mhz": 121.85, "text": "Denver Ground" }
  ],
  "marks": [                       // for the timeline
    { "t": 1407.1, "kind": "takeoff", "text": "Takeoff" },
    { "t": 1427.6, "kind": "handoff", "text": "Denver Tower to Denver Departure" }
  ]
}
```

## Radio lines

- **`kind`** is one of `atc`, `pilot`, `copilot`, `atis`, `tuned`, `alert` or `phase`.
- **Readbacks.** A readback's result is folded onto the pilot's (or copilot's) line it judged, rather than
  given a line of its own:
  - `ok`: whether the readback was right
  - `readback`: what was missing or wrong, when it wasn't
- **`unclear: true`** marks a call the speech-to-text wasn't sure of.

## Marks

`kind` is one of `phase`, `alert`, `handoff`, `takeoff` or `landing`. A landing's text carries the touchdown
rate.

## Trimming

- **The window.** The replay starts a minute before the first call or movement, on a track point. It ends a
  minute after the last one. Time parked with the sim open before or after the flight is left out.
- **Track points.** Points come every 5 s in the air and every 2 s on the ground. There is also one wherever
  the heading changes by more than 4° or the altitude by more than 150 ft. The samples either side of a
  takeoff or touchdown are always kept.
- **Size.** A three-hour flight comes to about 2,500 points, about 35 KB gzipped.

## Left out

- audio, and every `audio_ref`
- the language model's calls
- the pilot's dev notes
- AI traffic
- the recording's settings

## On the server

`server/src/replays.ts` checks and rebuilds every field (`cleanReplay`). It drops unknown keys and refuses
anything out of range:
- more than 20,000 points or 3,000 radio lines
- text longer than 2,000 characters
- a kind outside the lists above
- a body over 1 MB gzipped

It stores the replay gzipped, one per flight. Deleting the flight or the account deletes it.

## Players

Every player follows the same rules, set by the website's player:
- the aircraft is interpolated between track points, turning the short way
- the current line is the last one at or before the clock
- "skip quiet" jumps a gap of more than 45 s with nothing said to 8 s before the next call

The players:
- `site/replayplayer.js` is the website's player, and its copy is the desktop app's (Leaflet).
- `ReplayClock` in `ios/LocalTCKit` drives the companion app's `ReplayView` (MapKit).

A player refuses a `v` it doesn't know.
