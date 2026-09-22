# ***https://buymeacoffee.com/petey1***

# LocalTC

Free, open-source, offline-capable ATC for **Microsoft Flight Simulator 2024**. No cloud, no API keys.

- A small local LLM (1B–4B, via Ollama) reads every pilot call; a grammar checks it and takes over when the model is slow, missing or wrong.
- A deterministic engine makes every ATC decision (clearances, handoffs, sequencing). Routine calls use exact FAA phraseology; the model words only replies that have no template.
- You talk with push-to-talk; faster-whisper transcribes locally. ATC answers in Piper voices through a radio effect, one voice per controller.
- An optional copilot works the radio for you: readbacks, frequency changes, or every call.
- **The LocalTC app**: the radio log, frequencies to click, a live map, airport lookup, SimBrief import, and settings for models, voice and push-to-talk.

Windows is the only supported runtime. Development works on macOS too, using recorded sim sessions.

> **Status: Phase 5 (the app).**
> - **Working:** the full IFR flow, from clearance delivery to taxi-in; voice in and out; ATIS and weather; unscripted moments; the copilot; the app.
> - **Not yet:** SIDs, STARs and published arrivals (ATC clears "as filed" and gives vectors or a straight-in approach), and AI traffic on the frequency.

## Install (Windows)

Download **[LocalTC-Setup.exe](https://github.com/duckiest428/LocalTC/releases/latest/download/LocalTC-Setup.exe)**
and run it. It needs no administrator rights and no git: it fetches the latest release from GitHub, checks
it against the release's `SHA256SUMS`, installs it to `%LOCALAPPDATA%\Programs\LocalTC`, and runs the setup
below. Windows may warn that the installer is from an unknown publisher (it isn't code-signed): choose
**More info → Run anyway**.

**Updates:** the app checks GitHub once a day (Quick Settings → Updates) and says when a new version is
out. Install it with one click, let it install itself when LocalTC closes, or turn the check off. It never
updates during a flight, and keeps the Python environment, recordings and downloaded models. What changed
is in [CHANGELOG.md](CHANGELOG.md).

**From source** (developers, or without the installer): clone LocalTC and double-click
**`Install LocalTC.cmd`** in its folder, or run `powershell -ExecutionPolicy Bypass -File install\install.ps1`.
Update a clone with `git pull`, then run it again.

It installs everything a flight needs and can be run again safely:
- Python 3.12, via winget if you don't have Python yet.
- LocalTC, with Whisper, Piper and push-to-talk support, and the app window (Edge WebView2).
- CUDA support for Whisper if there's an NVIDIA card.
- Ollama.
- **The models, picked for this computer**: its RAM and graphics card choose the *light*, *balanced* or
  *quality* set, and downloads them. After that, flights work offline.
- **LocalTC** shortcuts on the desktop and in the Start menu.

Options: `-Quality light|balanced|quality` (instead of automatic), `-Cpu` (no GPU support), `-NoOllama` (grammar only).

On macOS/Linux (development against recordings): `./install/install.sh`.

## The app

Start **LocalTC** from the shortcut, or run `.venv\Scripts\localtc` with no command.

To open it in a browser instead of its own window — handy for working on the page itself, and the
only way on macOS or Linux — run `localtc app --port 8765 --browser`. With `--no-open` it prints the
address and waits, so you can point any browser at `http://127.0.0.1:8765/`. It listens on the
loopback address only; nothing on the network can reach it. The page runs without the sim: the
tabs, settings and airport lookup all work, and **Start** is what needs MSFS.

| Tab | |
|---|---|
| **ATC** | COM1/COM2 and the transponder at the top. The airport's frequencies: click one to tune COM1. Your callsign, destination, assigned squawk, altitude, runway or approach, the phase, and what ATC expects next. Below that, the radio log. |
| **Quick Settings** | Performance profiles and the models (language model, Whisper size, ATC voice), each with its speed, quality and size, a **Download** button and a voice **Preview**. Also the push-to-talk key (press **Change**, then the key), yoke button, microphone, speakers, volume and speed, the copilot, ATC options, and developer mode. |
| **Live Map** | Your aircraft and its track, AI traffic, the flight plan route and fixes, and the runways. **ATC zones** (on by default) draws who controls what: the enroute centres on your route, the departure and approach areas, each airport's Clearance, Ground, Tower and Dep/App, the tower's zone, and the stretch of final where approach clears you and sends you to tower. These are the same outlines ATC hands you over at, so the map explains every handoff; the one you're talking to and the one you're about to be sent to are highlighted. |
| **Airport Lookup** | Frequencies, runways (length, heading, ILS) and taxiways for any airport a flight has visited. During a flight, any other ICAO is fetched from the sim. |
| **Logbook** | Every live flight, kept on this computer: date, callsign, route, air and block time, the landing rate, and totals (hours, airports, distance, readbacks right). |

- **New Flight**: import your latest **SimBrief** plan (username or Pilot ID), or type one in (**Manual**). Then **Save and start flight**.
- **Start / Stop** (top right) connects to MSFS 2024 and runs ATC.
- **Talk**: hold your push-to-talk key (Right Ctrl by default) or the headset button next to the text box. You can also type a call and press Enter.
- **ATC** switch: ATC's voice on or off (text only). **Copilot** switch: the copilot works the radio with ATC.
- **Developer mode** (Quick Settings): every flight is recorded with its audio. **Mark** notes the moment something goes wrong, and **Export session** zips the recording, logs, settings and flight plan into your Downloads folder, ready to send.

### The logbook and the optional account

When a live flight ends, LocalTC writes a line to the logbook (`%LOCALAPPDATA%\LocalTC\logbook.db`):
airports, gates, runways, block and air time, distance flown, highest altitude, the vertical speed at
touchdown, and how many readbacks and alerts there were. It needs no account and nothing leaves the computer.
Turn it off with `[logbook] enabled = false`.

An **account is optional** (Quick Settings → Account). It copies those logbook lines to
[localtc.tech](https://localtc.tech/dashboard.html), where there are totals, a map of the airports and routes,
an export and a delete button, and it feeds the companion app while you fly (the phase, the frequency tuned
and next, and ATC's last call). It never sends your position, voice, transcripts, recordings or settings. The
sign-in is kept in Windows Credential Manager (or the macOS Keychain), not in a file. The server is in
[`server/`](server/README.md).

Settings save as you change them, to `%LOCALAPPDATA%\LocalTC\settings.toml`. Only the changes from
`config/localtc.toml` are written, and the command line uses them too.

## Documentation

- [docs/architecture.md](docs/architecture.md): the pieces, the event bus, a transmission end to end, the app.
- [docs/phraseology.md](docs/phraseology.md): adding phraseology templates.
- [docs/state-machine.md](docs/state-machine.md): extending the phases and the dialogue for new scenarios.
- [site/](site/): the project's website, published to GitHub Pages by `.github/workflows/pages.yml`.

## Layout

```
src/localtc/
  sim_api/     Event types, SimSource interface, session clock, unit decoding (pure; any OS)
  sim_bridge/  Live MSFS 2024 bridge over SimConnect (ctypes; Windows only at runtime)
  bus/         In-process async pub/sub
  recorder/    Bus events -> timestamped JSONL + WAV sidecars
  replay/      ReplaySource: plays a recording through the SimSource interface
  atc_core/    ATC logic (any OS)
    airport/       runway/taxiway geometry, runway selection, taxi routing
    airspace/      the world's enroute centres and approach areas (CC BY-SA data; tools/make_airspace.py)
    route.py       the filed route: when the climb ends and where the descent begins
    phase/         flight phase detection from telemetry + geometry
    phraseology/   FAA speech formatting, typed slots, TOML templates (templates/*.toml)
    readback/      transcript normalizer, element extractors, intents, interpreter chain
    llm/           the model's two jobs: understanding (prompt, few-shot examples, checks) and phrasing
    engine.py      AtcEngine: the IFR dialogue, handle(event) -> events
    session.py     SessionState + JSON SessionSnapshot (the Phase 2 LLM's view)
    service.py     connects the engine to the bus
  airports/    JSON airport cache (%LOCALAPPDATA%\LocalTC\airports)
  llm/         Ollama client (standard library HTTP) and the edge-case evaluation (eval_cases.toml)
  copilot.py   the copilot: reads back, changes frequencies, makes calls
  scenario.py  offline scripted-pilot scenarios
  stt/         microphone, push-to-talk, Whisper
  tts/ dsp/    Piper voices, the radio effect, audio out
  ui/          the app: local HTTP/SSE server, controller, static page (HTML/CSS/JS, Leaflet)
  flightplan.py  SimBrief import and typed flight plans
  models.py    model catalog, hardware profiles, downloads
  app.py       Wiring: config -> source -> bus -> engine, voice, recorder; LiveSession controls
  cli.py       `localtc app | run | record | replay | atc | setup | ...`
tools/         Script shortcuts, plus make_fixture.py for the synthetic test recording
tests/         Runs on any OS; tests/windows/ needs a live sim
```

**Dependency rules:**
- Only `app.py` may import `sim_bridge`.
- `sim_api` imports nothing else from LocalTC.
- `atc_core` only imports `sim_api`, `bus` and `airports`: no HTTP, no copilot. `lint-imports` and `tests/test_architecture.py` both enforce this. Everything except the bridge can be built and tested on a Mac against recordings.

## Setup

Requires Python 3.11+ (3.12 recommended).

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip install -e ".[dev]"
```

or with uv:

```bash
uv sync --extra dev
```

## Usage

```bash
localtc replay tests/fixtures/pattern_short --speed 4     # play back a recording (any OS)
localtc inspect tests/fixtures/pattern_short              # summarize a recording
localtc record --print                                    # record a live session (Windows + MSFS 2024)
localtc run --source replay --no-record --print           # run with config/localtc.toml

# ATC (Phase 1)
localtc phases tests/fixtures/ifr_kpae_kbfi --destination KBFI --cruise-ft 5000   # phase timeline
localtc atc --scenario tests/scenarios/ifr_happy_path.toml                        # scripted IFR flight, offline
localtc atc recordings/<session> --pilot recorded [--llm live]                   # your flight's calls, answered by today's ATC
localtc replay tests/fixtures/ifr_kpae_kbfi --atc --type --speed 20 \
    --destination KBFI --cruise-ft 5000                   # type pilot calls against a replay
localtc run --source live --type --destination KBFI --cruise-ft 5000              # fly it live (Windows)
localtc debug airport KPAE --json KPAE.json --raw KPAE.bin                        # live airport data diagnostics
```

With `--type`, each line you type is a pilot transmission on COM1, so tune COM1 in the sim first. The ATC engine answers whichever controller works that frequency.

A `TUNE` line shows which facility LocalTC thinks is on COM1. If a call goes unanswered, check that line. `no ATC on this frequency` means LocalTC doesn't know that frequency (for example center, which comes from `[atc] center_mhz`). There is no voice input yet: type in the same terminal window.

**Windows (PowerShell):** run commands as `.venv\Scripts\localtc ...`, or activate the environment first with `.venv\Scripts\Activate.ps1`. After pulling new code, rerun `.venv\Scripts\pip install -e ".[dev]"`.

Pick the source in `config/localtc.toml` (`[source] kind = "live" | "replay"`) or with `LOCALTC_SOURCE=live|replay`.

## Language model (Phase 2)

1. Install [Ollama](https://ollama.com/download) and pull the model: `ollama pull llama3.2:3b`. It runs locally; nothing leaves the machine.
2. `localtc llm check`: is Ollama running, is the model installed, how fast does it answer.
3. `localtc llm eval`: runs the seeded edge cases (`src/localtc/llm/eval_cases.toml`) against your model and shows what it got right, what it got wrong and how long it took.

Without Ollama, LocalTC logs a warning and runs on the grammar alone, exactly as in Phase 1. `--no-llm` does the same on purpose.

With llama3.2:3b the edge cases pass 34/34, at about 1 s per call (median; the p95 is about 2 s including retries). If `llm check` shows slower answers on your machine, raise `[llm] timeout_s` and `budget_s`. Keep `base_url` on `127.0.0.1`: on Windows, `localhost` tries IPv6 first and adds about 2 s to every call.

**What the model does.** It fills in a small JSON form for each pilot call: the kind of call, the intent, and the values the pilot said. It never talks to the pilot and never decides anything. Its answer is checked before it's used:
- The form must match the schema (Ollama enforces it). An answer that doesn't is retried once, with the problem named.
- **Every value must have been said.** A squawk, frequency or altitude that isn't in the pilot's words (after number normalization) makes the whole answer invalid.
- **The intent has to fit the words.** An altitude request needs a request word ("request", "could we", "higher") and an altitude word. A small model otherwise calls every check-in an altitude request.
- Readback values are compared with the same rules as the grammar, and a value the grammar heard wins over the model's. If the grammar recognized a readback, the model can't turn it into a request.
- An intent that makes no sense in the current phase (ready to taxi while cruising) is rejected.
- On a timeout, a missing model or two bad answers, the grammar's result is used. There are two exceptions. A question with a clear topic word ("say the winds") is still answered. A call the model kept calling a request, where the pilot said "request", is declined as unsupported.

**When it's asked** (`[llm] understanding`): `primary` asks it about every call. `fallback` asks only when the grammar can't cope: a parser failure, an ambiguous call, a question, an emergency, a rejected readback, or words outside the grammar ("request direct"). Either way the trigger is recorded.

**Phrasing** (`[llm] phrasing`): routine calls stay exactly as the templates say. The model only words replies with no template: answers the sim can't give (altimeter, wind, runway, squawk and assigned altitude come straight from sim data) and declined requests ("unable direct at this time, continue as filed"). The reply may not contain an instruction or approval ("cleared", "climb", "contact", "approved", ...) or any number that isn't in the facts it was given. Otherwise ATC says "unable".

**Recordings.** Every model call is recorded as an `llm_exchange` event: prompt, answer, outcome, latency. A replay reuses the recorded answers, so it behaves exactly like the flight did, without a model (`localtc replay ... --llm live` asks the model again instead). `localtc atc <recording> --llm live` runs the model offline against a recording.

## Voice

```bash
localtc run --source live --voice --destination CYQB --cruise-ft 12000   # hold Right Ctrl and talk
localtc voice devices                                                    # microphones
localtc voice test                                                       # talk; see what Whisper heard and how fast
localtc voice eval recordings/<session>                                  # re-transcribe a recorded flight
localtc voice eval                                                       # the 34 spoken edge cases
```

**Push-to-talk** (`[voice] ptt`):
- `keyboard` (default): hold `ptt_key` (Right Ctrl). It works while the sim has focus. On macOS, allow Input Monitoring for the terminal.
- `joystick`: a yoke or joystick button (`ptt_joystick`, e.g. `joystick:0:button:3`), bound through SimConnect.
- `enter`: Enter starts and stops a transmission in the LocalTC window. It needs no permissions, which makes it handy for testing.

The microphone stays open and keeps 0.3 s from before the key went down, so the first word isn't cut off. Each clip is saved in the recording (`audio/*.wav`) with its transcript. The quiet before and after the words is trimmed before Whisper hears it.

**Microphone** (`[voice] input_device`, `--mic`): blank uses the system's default input; the log names it at startup. If a transmission comes through silent, LocalTC reopens the microphone, so a default you change in Windows Settings > Sound > Input takes effect on the next press, without a restart. To pin one, give part of its name (`--mic "Headset"`; `localtc voice devices` lists them).

**Whisper** (`[voice] model`, `device`): `auto` picks `small.en` on an NVIDIA GPU and `base.en` on the CPU. On a MacBook Air CPU, base.en takes about 0.4 s for a transmission and small.en about 1.3 s, with fewer errors. With an NVIDIA card, `pip install -e ".[cuda]"` (the installer does this) gets small.en on the GPU. To compare models on your own voice, re-transcribe a flight: `localtc voice eval recordings/<session> --whisper-model small.en`. `localtc setup` downloads the model to `%LOCALAPPDATA%\LocalTC\models`.

**Aviation vocabulary** (`[voice] vocabulary`): Whisper is prompted with standard phraseology and this flight's names: callsign, stations, airports, every runway, and the local taxiways. It is never given the numbers ATC just assigned. Priming it with the expected squawk could make it "hear" the right one when the pilot said another, and hide a readback error. Known mishearings are corrected afterwards: "whole short", "decent and maintain", "1-2000" for one two thousand, and "12,000,000 minutes" for "12,000, one zero minutes". On the spoken edge cases the prompt halves the word error rate (51% to 25%).

**Into the parser.** A transcript goes to the same understanding path as typed text. ATC waits for it before answering, and it belongs to the frequency you keyed on, even if you switch before Whisper finishes. Whisper's confidence is a trigger: in `fallback` mode, a low-confidence transcript goes to the model even if the grammar parsed it. Push-to-talk with no speech gets no reply.

**Testing without a sim or a microphone.** `tests/fixtures/voice_kpae` is a recording of the KPAE departure flown by voice, made with macOS speech voices (several accents, radio filtering, cockpit noise; `tools/make_voice_fixture.py`). `tests/fixtures/voice_clips` holds the 34 edge cases, spoken. `pytest -m whisper -s` checks that the departure flies identically from Whisper's transcripts: every readback correct, each under 0.7 s. It also runs the spoken edge cases; with Ollama, 30 of 34 are understood.

## ATC's voice (Phase 4)

```bash
localtc tts say                                   # hear a tower transmission
localtc tts say "..." --station "Phoenix Approach" --out approach.wav
localtc tts devices                               # speakers and headsets
localtc run ... --no-tts                          # text only
```

ATC talks through **Piper** (the `piper-tts` package ships prebuilt wheels for Windows, macOS and Linux, so there's no separate binary to install). The voice is `en_US-libritts_r-medium`: one 80 MB download (`localtc setup`, or automatically on the first flight) with 904 speakers. Every station gets its own speaker, and the same one every time: Phoenix Tower never sounds like Phoenix Approach. The pool is the 40 speakers Whisper understood best reading ATC phraseology over the radio (`tools/pick_speakers.py`). Synthesis takes about 0.3 s for a 10 s clearance.

**Radio effect** (`[tts] radio_effect`, `static`): band-pass 300-3000 Hz, radio-style compression with light overdrive, slow carrier fading, hiss, and a squelch burst at the end of each transmission. The copilot's calls are spoken too (`[tts] copilot`) in a pilot voice, band-limited but without static.

**Pacing.** Transmissions never overlap, and the ATC engine knows how long its words take, so it doesn't start the next call (or answer the copilot) until the frequency is quiet.

## ATIS and weather

MSFS doesn't give add-ons its ATIS or METARs, only the weather where the aircraft is, so LocalTC builds each airport's ATIS itself: wind (with gusts), visibility and precipitation, temperature, altimeter, the approach and runway in use, and cautions (gusty winds, strong crosswind, low visibility, wet runway, high density altitude). Surface weather is sampled on or near the airport. Until you get to the destination, its ATIS uses the freshest surface sample from an airport within 150 nm; the altimeter is always current.

- Tune an ATIS frequency and it's printed in the console and read aloud on a loop until you tune away.
- The letter advances when the weather really changes (at most every 10 minutes). If you're on ground, tower or approach when it does, ATC tells you: "information Charlie is now current, altimeter 29.90".
- **The runway in use comes from the ATIS**, for taxi clearances and arrivals alike, and it only changes when the tailwind on it passes 5 kt.
- ATC uses it: a taxi clearance adds "information Charlie is current, altimeter 29.92" if you didn't report the current letter ("with information Charlie"). The takeoff clearance gives the wind and any cautions. Descents give the destination altimeter, and approach checks you have the current ATIS. The landing clearance adds cautions.

## Unscripted moments

The language model reads what you say, the engine decides, and the templates speak. So you can go off script and still get a real answer:

| You say | ATC |
|---|---|
| "request direct BLAKO" / "direct to the airport" | "cleared direct BLAKO" (read it back) |
| "request vectors (for the ILS 26)" | a heading to an 8 nm final, then the approach as usual |
| "could we get runway 16R" (on the ground) | a new taxi route to it, or "expect runway 16R"; "unable, wind ..." if the tailwind is over 10 kt |
| "we'd like the visual runway 26" (arriving) | "expect visual runway 26 approach"; unable in low visibility |
| "request return to Paine" | the departure airport becomes the destination: "cleared direct Paine Field airport, maintain ..., expect ..." |
| "going around" (or a go-around without a word) | "fly runway heading, climb and maintain ..., contact approach", then a new approach |
| "moderate chop at 7,000" | "roger, thanks for the report" |
| "traffic in sight" / "looking" | "roger" / nothing |
| "request higher, 9,000" | climb (or unable on the approach) |
| a question it has no answer for | a short answer from the model, or "unable" |

And ATC starts things too (`[atc] unscripted`, on by default):

- **Traffic advisories** from the sim's real AI traffic within 5 nm and 1,200 ft that's converging: "traffic, two o'clock, four miles, opposite direction, 3,500, B738". Each airplane is called at most every 5 minutes, and never one that's just landing or taking off.
- **Altitude checks:** once you've reached your altitude, drifting 300 ft off it for 15 s gets "check altitude, maintain 5,000".
- **A quiet pilot:** an instruction nobody reads back gets "how do you read?" after 30 s, is said once more, then dropped.
- **Missed check-in:** switched to the new frequency and said nothing for 45 s? Departure or approach calls you first. Still on the old frequency 45 s after reading back a handoff? You're told again.
- **"Clearance on request, stand by":** clearance delivery sometimes needs a moment.

The copilot answers these too ("looking", "loud and clear").

`localtc llm eval` has 48 cases, including the new requests: all 48 pass with llama3.2:3b, at about 1.2-1.7 s per call.

## Readback strictness

Exact readbacks pass. A wrong value gets "negative, ..." and a missing one "read back ...". A value that's probably right but misheard or misspoken gets **"confirm ..."**: a frequency missing a digit ("12.1" for 120.1), "1508" for 1,500, or "08 left" for runway 08. Answer "affirm" or read it again. Self-corrections count: "cleared to land 08 left, correction 08" is fine.

## The flight console

`localtc run` prints the radio, not the plumbing: ATC's words wrapped under the station name, your transmissions, COM1 changes, ATIS, phase changes, readback results and alerts. Timings, language model calls and push-to-talk edges go to `%LOCALAPPDATA%\LocalTC\logs\localtc.log`. `-v` shows them on the console, and `--events` prints the raw event stream.

## Copilot

```bash
localtc run --source live --copilot full --destination CYQB --cruise-ft 12000     # it works the radio
localtc run --source live --copilot assist --type --destination CYQB              # you talk, it reads back
localtc atc tests/fixtures/real_cyul --copilot full --destination CYQB --cruise-ft 12000 \
    --callsign DP69 --airports tests/fixtures/airports_real                         # the same, offline
```

- **assist:** reads back every instruction and tunes COM1 whenever ATC hands you off. You make the requests and check-ins.
- **full:** also makes every call itself: IFR clearance, taxi, ready for departure, check-ins, final, clear of the runway.

It tunes COM1 through SimConnect (`COM_RADIO_SET_HZ`). Two-decimal frequency names are the 25 kHz channel ("120.42" is 120.425). If the aircraft doesn't follow, the copilot retries once and then logs a `copilot` alert and skips the call. It won't say the same thing a third time in a row.

## Live bridge setup (Windows)

1. **SimConnect.dll.** Install the MSFS 2024 SDK (in the sim: Options → General → Developers → enable Developer Mode, then download the SDK). LocalTC finds the DLL in this order:
   1. `live.dll_path` in the config
   2. `LOCALTC_SIMCONNECT_DLL`
   3. `%MSFS2024_SDK%\SimConnect SDK\lib\SimConnect.dll` (or `%MSFS_SDK%`)
   4. `C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll` (the installer's default location)
   5. `.\SimConnect.dll`
2. **Connection.** The default local named pipe needs no setup. If connecting fails, check `SimConnect.xml`:
   - Steam: `%APPDATA%\Microsoft Flight Simulator 2024\`
   - MS Store: `%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalCache\`
   - If a leftover `simconnect_ws.exe` is still running after the sim closed, end it in Task Manager.
3. **Mute the built-in ATC.** SimConnect can't disable it. In MSFS, set Sound → Character voices to 0 and turn off ATC subtitles and assistance.

On connect, LocalTC logs the sim version (major 12 = MSFS 2024, 11 = MSFS 2020) and saves it in each recording header.

## ATC (Phase 1)

**Flight phases** come only from telemetry and airport geometry (see `atc_core/phase/detector.py`):

PARKED → TAXI_OUT → RUNWAY_HOLD → TAKEOFF → DEPARTURE → CRUISE → ARRIVAL → APPROACH → LANDING → TAXI_IN

Every threshold can be overridden in `[atc.phase]`.

**The IFR flow:**
1. Clearance delivery gives the IFR clearance ("CRAFT").
2. Ground gives the taxi route (computed from the sim's taxiway graph).
3. Ground hands off to tower at the hold-short line; tower clears for takeoff.
4. Tower hands off to departure, which gives radar contact and the climb.
5. Departure hands off to center.
6. Center gives the descent, then hands off to approach.
7. Approach clears the approach and hands off to tower.
8. Tower clears to land, then hands off to ground after the runway exit.
9. Ground gives the taxi-to-parking route.

**Automatic alerts:** moving without a taxi clearance, runway incursion, takeoff or landing without a clearance, and emergencies.

**Phraseology** lives in `src/localtc/atc_core/phraseology/templates/*.toml`: one file per controller, with typed `{slots}`, required and optional readback elements, and an example pilot readback. Templates are validated when loaded.

**Readbacks** go through the transcript normalizer and the element extractors. The result is `correct`, `incorrect` ("negative, squawk …") or `incomplete` ("read back …"). An unparseable or ambiguous call gets "say again". After three failed tries ATC repeats the instruction once and stops asking, with a `readback_unresolved` alert. With the language model, the model reads the call first (see above) and returns the same `Interpretation`.

**Session snapshot:** `AtcEngine.snapshot()` is JSON covering phase, assignments, clearances, the pending readback, recent exchanges and alerts. It's the read-only context for the Phase 2 LLM. `localtc atc … --snapshot` prints it.

**Scenarios** (`tests/scenarios/*.toml`) run a recording plus a scripted pilot through the engine. Each has a golden transcript. After an intended behavior change, run `pytest tests/test_scenarios.py --update-goldens` and review the diff.

**Synthetic data:**
- `tools/make_ifr_fixture.py` regenerates `tests/fixtures/ifr_kpae_kbfi` and the airport JSON files in `tests/fixtures/airports/`.
- Those test airports are simplified stand-ins. Real layouts come from `localtc debug airport`.

## Recording format

A recording is a directory `recordings/<YYYYMMDD-HHMMSS>_<source>/`:

- `session.jsonl` (or `.jsonl.gz`)
  - Line 1 is a header: `{"type":"header","schema":1,...}`.
  - Every later line is one event, e.g. `{"type":"ownship_state","t":12.5,...}`.
  - `t` is seconds since the session started.
- `audio/NNNN.wav`: push-to-talk captures, referenced by `audio_ref`.

Event types live in `src/localtc/sim_api/events.py`. Their `type` tags are part of the file format.

## Tests

```bash
.venv/bin/pytest
.venv/bin/lint-imports
.venv/bin/pytest -m ollama -s      # the edge cases against your real model (skipped without Ollama)
.venv/bin/pytest -m whisper -s     # speech-to-text on the voice recordings (needs: localtc setup)
```

**macOS: `No module named 'localtc'`.** If the project is in an iCloud-synced folder such as `~/Desktop`, macOS can mark the editable install's `.pth` file as hidden, and Python 3.12.13+ skips hidden `.pth` files. Tests aren't affected, because pytest adds `src` to the path itself. For the CLI, either move the project outside the synced folder, run `chflags nohidden .venv/lib/python3*/site-packages/*.pth`, or prefix commands with `PYTHONPATH=src`.

`tests/windows/` runs the SimSource contract against the live sim. It only runs on Windows with MSFS 2024 running and `LOCALTC_LIVE_SIM=1`.

## Releasing

1. Bump the version in `pyproject.toml` and `src/localtc/__init__.py`, and add a `## [X.Y.Z] - date` section
   to `CHANGELOG.md` (the release notes, and what the app shows under "What's new").
2. Commit, then tag and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

`.github/workflows/release.yml` checks that the three versions agree, runs the tests, and publishes
`LocalTC-X.Y.Z.zip` (the source tree, minus what `.gitattributes` marks `export-ignore`),
`LocalTC-Setup.exe` (Inno Setup, `installer/localtc.iss`) and `SHA256SUMS` as a GitHub release. A tag with a
suffix (`v0.3.0-rc1`) is a pre-release, which neither the installer nor the updater picks up.

## Licence

LocalTC is free software under the [GNU Affero General Public License v3 or later](LICENSE). Run it
for anything, read and change the source, pass it on — and if you distribute a modified version, or
run one as a service other people use, those people get your source under the same licence.

Third-party components keep their own licences: the Whisper models, the Piper voices, and the Inter
and JetBrains Mono typefaces bundled with [the website](site/fonts/) under the SIL Open Font License.
The airspace outlines in [`src/localtc/atc_core/airspace/`](src/localtc/atc_core/airspace/README.md) come
from the VATSIM community's VATSpy Data Project and SimAware TRACON Project and are CC BY-SA 4.0.


_Shamelessly vibecoded_
