# LocalTC architecture

LocalTC is one Python process. The sim, the radio, ATC and the app talk over an in-process event
bus. Everything runs on the pilot's computer: MSFS 2024 over SimConnect, Whisper for speech in,
a small language model in Ollama for understanding, Piper for ATC's voice.

```
            MSFS 2024                               the pilot
               │ SimConnect (ctypes)                  │ microphone, push-to-talk        ▲ headset
               ▼                                      ▼                                 │
  sim_bridge ─► SimSource ──events──►  ┌───────────── EventBus ─────────────┐     tts (Piper + radio DSP)
  replay     ─►  (live or recorded)    │ OwnshipState, Transcript,           │──►  AtcTransmission, AtisBroadcast
                                       │ AtcTransmission, PhaseChanged, ...  │
                    stt (Whisper) ────►│                                     │──► recorder (JSONL + WAVs)
                    copilot ─────────►│                                     │──► ui (the app window)
                    ui (typed calls) ─►└──────────────┬──────────────────────┘
                                                      │ inputs            ▲ outputs
                                                      ▼                   │
                                                 atc_core.AtcEngine.handle(event) -> [events]
                                                 (phases, dialogue, readbacks; asks llm/ when needed)
```

## The pieces

| Package | Job | May import |
|---|---|---|
| `sim_api/` | The contract: event types (`events.py`), commands, the `SimSource` interface, session clock, airport structs | nothing else in LocalTC |
| `sim_bridge/` | The live MSFS 2024 bridge over SimConnect (Windows) | `sim_api` |
| `replay/`, `recorder/` | Play and record sessions as JSONL (+ WAV audio) | `sim_api`, `bus` |
| `bus/` | Async publish/subscribe, one queue per subscriber | `sim_api` |
| `atc_core/` | ATC: phase detection, the dialogue, phraseology, readback checking, the model's two jobs | `sim_api`, `bus`, `airports` |
| `llm/` | The Ollama HTTP client and the model evaluation | `atc_core.llm` types |
| `stt/` | Microphone, push-to-talk, Whisper | `sim_api`, `bus` |
| `tts/`, `dsp/` | Piper, the radio effect, the audio player | `sim_api`, `bus` |
| `copilot.py` | Works the radio for the pilot; reads the engine's state, never changes it | `atc_core` |
| `app.py` | Wiring: config → source → bus → engine, voice, recorder. `run_session()` runs one flight. `LiveSession` holds the controls of a running flight. | everything (the only module that imports `sim_bridge`) |
| `ui/` | The app window: `server.py` (HTTP + Server-Sent Events on 127.0.0.1), `controller.py` (settings, plan, flight start/stop, events → page), `static/` (HTML/CSS/JS, Leaflet) | `app` and below |
| `flightplan.py` | SimBrief import and typed plans | `sim_api` types only |
| `models.py` | The model catalog, hardware detection, profiles, downloads | `llm`, `stt`, `tts` |
| `config.py` | `config/localtc.toml` + the app's `settings.toml` on top | nothing |
| `cli.py` | `localtc app / run / replay / atc / setup / ...` | everything |

`pyproject.toml` (`lint-imports`) and `tests/test_architecture.py` enforce the import rules. Only the
bridge needs Windows. Everything else is built and tested on a Mac against recordings.

## A pilot transmission, end to end

1. **Push-to-talk down.** The key hook (`stt/ptt.py`), a SimConnect joystick event or the app's headset
   button calls `VoiceService.press()`. `PttPressed` goes on the bus, and ATC holds its next call while the
   pilot is talking.
2. **Push-to-talk up.** The audio clip goes to Whisper on a worker thread with a prompt of aviation words
   and this flight's callsign, stations and runways (`voice.flight_hints`). `Transcript(text, source="voice")`
   goes on the bus. Typed calls (the app's text box, `--type`) are `Transcript(source="typed")`.
3. **`AtcService`** feeds it to `AtcEngine.handle()` on a thread, because the model may be asked.
4. **The engine** (`atc_core/engine.py`) finds the controller from the COM1 frequency, then interprets the
   words with the `ChainInterpreter`:
   - The **grammar** (`readback/`) normalizes numbers and phonetics, extracts elements (runway,
     altitude, squawk ...) and matches intents.
   - The **language model** (`atc_core/llm/understand.py`) fills a JSON form for the same call.
     Every value it gives must appear in the pilot's words.
   - `understanding = "primary"` asks the model every time. `"fallback"` asks only when the grammar is stuck.
5. The engine **decides**: a readback is `correct` / `incorrect` / `incomplete` / `unclear`, and a request
   gets its clearance, a handoff or "unable". The reply is scheduled 1.5–3 s later in event time, and never
   while the pilot is transmitting.
6. **The reply** is rendered from a phraseology template (display text + spoken form) and published as
   `AtcTransmission`. Replies with no template (answers to questions, declined requests) are worded by the
   model and checked: no instructions, no invented numbers.
7. **Voice out** (`tts/service.py`) synthesizes the spoken form with Piper in that station's voice, runs
   it through the radio effect, and plays it. The engine keeps the frequency busy for as long as the
   words take (`speech_s_per_char`).
8. The **recorder** writes every event. The **app** shows it in the radio log.

## Determinism and recordings

`AtcEngine.handle(event)` is synchronous and depends only on the events, the config and a seed:
the same recording gives the same squawk, runways, phrasing and delays. That's what makes it testable:

- `localtc replay <recording> --atc` plays a flight through today's ATC.
- `localtc atc <recording> --pilot recorded` does the same, instantly, with the recorded transcripts.
- `tests/scenarios/*.toml` run a scripted pilot against a recording and compare the transcript to a
  golden file (`pytest --update-goldens` rewrites them).
- Model answers are recorded (`llm_exchange` events) and replayed by default, so a replay behaves like
  the flight did without Ollama.

**Developer mode** in the app records every flight with its audio. **Mark** adds a `session_note` event
at that moment. **Export session** zips the recording, the logs, `settings.toml` and the flight plan.
`tools/thin_recording.py` turns an export into a test fixture (see `tests/test_cyul_flight.py`).

## The app

`localtc app` (or the **LocalTC** shortcut, `localtc-app.exe`) starts `ui.run_app()`:

- An asyncio loop on a background thread runs `AppServer` on `127.0.0.1` and the `AppController`.
- The main thread opens a pywebview window (Edge WebView2 on Windows) on that address. Without
  pywebview it opens the default browser (`--browser`).
- The page calls `/api/...` (JSON) and listens to `/api/events` (Server-Sent Events):
  `state`, `radio`, `own`, `traffic`, `flight`, `ptt`, `jobs`, `dev`.
- **Start** runs `app.run_session(cfg, on_event=controller.on_event, on_ready=...)`, the same code path as
  `localtc run`. `LiveSession` gives the controller the running flight's controls: typed calls, the PTT
  button, tuning COM1, the copilot switch, mute and notes.
- **Settings** are saved as they change, to `settings.toml` in the data folder
  (`%LOCALAPPDATA%\LocalTC`). Only the differences from `config/localtc.toml` are written.
  `load_config()` applies them on top, so the CLI sees them too. Models, voice and push-to-talk changes
  apply when the next flight starts.
- **Flight plans** (`flightplan.py`) come from SimBrief's API
  (`https://www.simbrief.com/api/xml.fetcher.php?username=<name>&json=1`) or the Manual form, and are kept
  in `flightplan.json`. ATC uses the callsign, destination and cruise altitude. The route, SID, STAR and
  fixes are drawn on the map.

## Files on disk

| Where | What |
|---|---|
| `config/localtc.toml` | Defaults, with comments (edit by hand if you like) |
| `%LOCALAPPDATA%\LocalTC\settings.toml` | What the app changed |
| `%LOCALAPPDATA%\LocalTC\flightplan.json` | The current flight plan |
| `%LOCALAPPDATA%\LocalTC\models`, `voices`, `airports`, `logs` | Whisper models, Piper voices, cached airport layouts, `localtc.log` |
| `recordings/<session>/` | Recorded flights: `session.jsonl` and `audio/*.wav` |

On macOS/Linux, the data folder is `~/.cache/localtc`.
