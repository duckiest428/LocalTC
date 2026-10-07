# ***https://buymeacoffee.com/petey1***

# LocalTC

Free, open-source, offline-capable ATC for **Microsoft Flight Simulator 2024**. No cloud, no API keys.

- A small local LLM (1B–4B, via Ollama) reads every pilot call; a grammar checks it and takes over when the model is slow, missing or wrong.
- A deterministic engine makes every ATC decision (clearances, handoffs, sequencing). Routine calls use exact phraseology (FAA in the US and Canada, ICAO elsewhere, or forced either way) for IFR and VFR flights; the model words only replies that have no template.
- You talk with push-to-talk; faster-whisper transcribes locally. ATC answers in Piper voices through a radio effect, one voice per controller.
- An optional copilot works the radio for you: readbacks, frequency changes, or every call.
- **The LocalTC app**: the radio log, frequencies to click, a live map, airport lookup, SimBrief import, and settings for models, voice and push-to-talk.

Windows is the only supported runtime. Development works on macOS too, using recorded sim sessions.

> **Status: Phase 5 (the app).**
> - **Working:** the full IFR flow, from clearance delivery to taxi-in; voice in and out; ATIS and weather; unscripted moments; the copilot; the app.
> - **Not yet:** holding patterns, published missed approaches, and each chart's own minima (typical values are used).

## Install (Windows)

Download **[LocalTC-Setup.exe](https://github.com/duckiest428/LocalTC/releases/latest/download/LocalTC-Setup.exe)**
and run it. It needs no administrator rights and no git: it fetches the latest release from GitHub, checks
it against the release's `SHA256SUMS`, installs it to `%LOCALAPPDATA%\Programs\LocalTC`, and runs the setup
below. Windows may warn that the installer is from an unknown publisher (it isn't code-signed): choose
**More info → Run anyway**.

**Updates:** the app checks GitHub once a day (Settings, the gear → Updates) and says when a new version is
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
| **Live Map** | Your aircraft and its track, AI traffic, the flight plan route and fixes, and the runways. Two maps, switched with **IFR / VFR**: it opens on your flight's rules and follows them when they change. **VFR** is terrain (OpenTopoMap) with the airspace class around each airport in view: Class B shelves, C, D, or an ICAO control zone or traffic zone, labelled ceiling over floor in hundreds of feet like a sectional (simplified sizes, not for real navigation). On the **IFR** map, **ATC zones** (on by default) draws who controls what: the enroute centres on your route, the departure and approach areas, each airport's Clearance, Ground, Tower and Dep/App, the tower's zone, and the stretch of final where approach clears you and sends you to tower. These are the same outlines ATC hands you over at, so the map explains every handoff; the one you're talking to and the one you're about to be sent to are highlighted. |
| **Airport Lookup** | Frequencies, runways (length, heading, ILS) and taxiways for any airport a flight has visited. During a flight, any other ICAO is fetched from the sim. |
| **Logbook** | Every live flight, kept on this computer: date, callsign, route, air and block time, the landing rate, and totals (hours, airports, distance, readbacks right). A map shows every airport and route flown; click a route to find its flights, or a flight to see its route. **Replay** rewatches a flight: the aircraft moving on the map with the whole radio transcript in step, a timeline to scrub, and speeds up to 64× (skipping the quiet stretches). |

- **New Flight**: import your latest **SimBrief** plan (username or Pilot ID), or type one in (**Manual**). Then **Save and start flight**.
- **Start / Stop** (top right) connects to MSFS 2024 and runs ATC.
- **Talk**: hold your push-to-talk key (Right Ctrl by default) or the headset button next to the text box. You can also type a call and press Enter.
- **ATC** switch: ATC's voice on or off (text only). **Copilot** switch: the copilot works the radio with ATC.
- **Support & feedback** (Quick Settings): write to the developer from the app, signed in to the account. It's emailed on, and the answer comes to the account's address. The website's [Dashboard](https://localtc.tech/dashboard#support) has a Support section too.
- **Buy me a coffee** (top right): if LocalTC made a flight better. Click it once and it's gone for good.
- **Developer mode** (Quick Settings): every flight is recorded with its audio. **Mark** notes the moment something goes wrong, and **Export session** zips the recording, logs, settings and flight plan into your Downloads folder, ready to send.

### The logbook and the optional account

When a live flight ends, LocalTC writes a line to the logbook (`%LOCALAPPDATA%\LocalTC\logbook.db`):
airports, gates, runways, block and air time, distance flown, highest altitude, the vertical speed at
touchdown, and how many readbacks and alerts there were. It needs no account and nothing leaves the computer.
Turn it off with `[logbook] enabled = false`. Each line knows its flight's recording (`[recorder]`), which is
what the Logbook's **Replay** plays.

An **account is optional** (Settings (the gear) → Account). It copies those logbook lines to
[localtc.tech](https://localtc.tech/dashboard)'s Dashboard, where there are totals, a map of the airports and
routes, a live **Flight Tracker** for the flight you're flying (the same map as the app's Live Map: the ATC
zones, the route, the path flown, the traffic), an export and a delete button, and it feeds the companion app while you fly (the phase, the frequency tuned
and next, and ATC's last call). Only while the Flight Tracker or the companion app watches from away from your
PC's network (and you allow it) do your position, path, route, ATC zones, traffic and radio pass through the
server, held in memory and never stored; on the same Wi-Fi the phone talks to the PC directly. A flight's **replay** (its track and radio
transcript, no audio) goes up only when you upload it from the Logbook, or turn on "upload each flight's
replay"; then it plays on the Dashboard and in the companion app's Logbook too. **Share** makes a flight's
card public at a link (the route, the numbers and one radio line you pick), and **Wrapped** tells your week,
month or year of flying as a story; both need the account. It never sends voice, recordings or settings. There's
no password: signing in emails you a 6-digit code to type in. The sign-in is then kept in Windows Credential
Manager (or the macOS Keychain), not in a file. The server is in
[`server/`](server/README.md).

Settings save as you change them, to `%LOCALAPPDATA%\LocalTC\settings.toml`. Only the changes from
`config/localtc.toml` are written, and the command line uses them too.

## Documentation

- [docs/architecture.md](docs/architecture.md): the pieces, the event bus, a transmission end to end, the app.
- [docs/phraseology.md](docs/phraseology.md): adding phraseology templates.
- [docs/state-machine.md](docs/state-machine.md): extending the phases and the dialogue for new scenarios.
- [docs/replay-format.md](docs/replay-format.md): a flight's replay (the Logbook's Replay, the Dashboard, the phone).
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

With llama3.2:3b on a CPU the edge cases (54 of them, several from real flights) pass 54/54, at about 2 s per call (median; the p95 is about 4 s including retries). If `llm check` shows slower answers on your machine, raise `[llm] timeout_s` and `budget_s`. Keep `base_url` on `127.0.0.1`: on Windows, `localhost` tries IPv6 first and adds about 2 s to every call.

**The grammar decides, the model only listens.** Every call goes to the grammar first. The model is asked only when the grammar can't place the call confidently among the ones expected right now, and it answers with a small JSON form: the kind of call, the intent, and the values the pilot said. It never talks to the pilot, never issues a clearance and never picks an instruction: the engine (the state machine) acts on the form exactly as on the grammar's reading. "Say again" only comes once the model couldn't make the call out either.

**What sends a call to the model** (the trigger is recorded with each call, `atc_core/llm/triggers.py`):
- nothing matched, or two requests that don't go together;
- a readback that isn't right: wrong, partial, or a number only nearly right;
- a request or question riding along with a readback ("cleared to land 34R, actually can we make that a low approach") the grammar can't place (one it can, it answers itself);
- a self-correction or hesitation ("sorry", "I mean", "uh", a word said twice);
- the callsign missing, garbled or one digit off;
- an altimeter given as STD or QNE;
- a call that makes no sense to this controller in this phase (a pushback request to a tower, a check-in on the ground);
- a word for something the grammar has no call for ("deviation", "icing");
- speech-to-text unsure of the words (confidence under 0.5), or of numbers in them (under 0.7).

A readback the grammar finds correct, and a request it matched confidently, never wait for the model.

**What the model is shown** (one snapshot per call, the same key order every time): the callsign, the phase, who the pilot is talking to and what kind of controller that is, what's cleared (altitude, heading, squawk, runway, approach), traffic called in the last few minutes, the last few exchanges on the frequency, and the readback expected. **It may only answer with intents that fit that controller in that phase** (`readback/expected.py`): the schema's list of intents is cut to them, so on approach "cleared ILS 34R" can't come back as a request for an IFR clearance. The answer only has to carry what the pilot said (no empty fields), which keeps it short: on a CPU that's the difference between 3 and 2 seconds.

**Its answer is checked before it's used:**
- The form must match the schema (Ollama enforces it). An answer that doesn't is retried once, with the problem named.
- **Every value must have been said.** A squawk, frequency or altitude that isn't in the pilot's words (after number normalization) makes the whole answer invalid.
- **The intent has to fit the words.** An altitude request needs a request word ("request", "could we", "higher") and an altitude word. A small model otherwise calls every check-in an altitude request.
- Readback values are compared with the same rules as the grammar, and a value the grammar heard wins over the model's. If the grammar recognized a readback, the model can't turn it into a request.
- On a timeout, a missing model or two bad answers, the grammar's result is used. There are two exceptions. A question with a clear topic word ("say the winds") is still answered. A call the model kept calling a request, where the pilot said "request", is declined as unsupported.

**Numbers speech-to-text nearly got** (`values_close`): a readback number one slip from the right one (a digit wrong, two swapped, one dropped or added: "135105" for 135.05, "5051" for squawk 5015) is "confirm squawk 5015" while speech-to-text wasn't sure of what it heard (confidence under 0.85), and "negative, squawk 5015" when it heard clearly (or the call was typed). A number nothing like the right one is "negative" with the right one. A right one is "readback correct" where ATC says so.

**When it's asked** (`[llm] understanding`): `fallback` (the default) asks only on the triggers above. `primary` asks it about every call except a readback the grammar already finds correct. The model shares the PC with the sim, so every call costs frames; `fallback` keeps that to the calls that need it.

**Patience** (`[llm] patience_s`): the model shares the PC with the sim, and one that answers in 2 s on an idle machine can take 8 in flight. A call the model missed the first deadline on (`timeout_s`), and that the grammar alone couldn't answer, gets one more try with up to `patience_s` (15 s) before ATC answers; meanwhile the radio log shows the controller thinking, and nothing is said on the radio. If even that finds nothing, a long call in your own words gets "roger", and anything else "say again": never silence. A readback the grammar could read (right, wrong or partly) is answered from that at once.

**Timing** (Quick Settings → ATC → Language model timing, or `[llm] timeout_s`, `budget_s`, `patience_s`): how long to wait for an answer, for all tries of one call, and for the second try. Longer is more patient with a busy PC; shorter answers sooner. **Whenever the model runs out of time, the app says so**: a notice on screen, a line in the radio log and an entry under the alerts, saying whether it's being asked once more or ATC answered without it.

**Cloud language model** (`[cloud]`, Quick Settings → Cloud language model; off by default, **recommended for quality**). A large model in the cloud reads your calls and words replies far better than a 3B one beside the sim, and gets the whole flight as context (`LlmRequest.context`: every fact the engine has, the route, both ATIS, the last half hour on the radio), where the local model keeps its lean, topic-gated facts. `localtc.llm.cloud` speaks the OpenAI chat API to each service:

| No key | Free key |
|---|---|
| Pollinations | Mistral (first: the most generous free limits), Groq, Google AI Studio (Gemini), Cloudflare Workers AI (key `ACCOUNT_ID:TOKEN`), NVIDIA NIM, SiliconFlow |

When a flight starts, each keyed service's `/models` list is read and models it doesn't offer are dropped. A call walks the services in `[cloud] order` (Mistral first, Pollinations last), each service's models in turn. A model that answers 429 or 402 rests until its `Retry-After` (else 30 s, doubling to 10 min), the service's other models carrying on (a 429 with a limit of 0, a model the plan doesn't include, drops it); 5xx, a timeout or a non-JSON answer rests it the same way; a refused key takes out the service and a missing model only that model, for the flight. Each try gets at most half the remaining wait while others are left, and when every route has failed the local model can answer with its usual prompt (`local_fallback`, off by default). Answers go through the same checks as the local model's. Keys are kept in the system credential store, not the settings file. Qwen Chat's, Qwen Code's, Qoder's and OpenCode's own free tiers are only for their own apps (private endpoints), so they aren't used.

**Traffic control, EXPERIMENTAL** (`[traffic] control`: off, shadow, reinject; off by default). Checked against the SDK first: SimConnect observes the sim's traffic, can create aircraft (MSFS 2024's `AICreate..._EX1`, with a livery), and can remove only what it created (`AIRemoveObject`); there's no hiding or taking over the sim's own and handing it back. So `localtc/traffic/control.py` never moves the sim's traffic. *Shadow*: a shadow of each aircraft (identity from an extra data definition asked every 8 s only when this is on: model, livery, the AI's origin, destination and state), with teleports, callsign changes, duplicates and losses noted. *Reinject*: an aircraft that vanishes within 80 % of `radius_nm` (and isn't back under a new object id after 8 s) is put back once: parked (`AICreateNonATCAircraft_EX1` where it stood) or, if it was flying to one of this flight's airports, flying (`AICreateEnrouteATCAircraft_EX1` on a generated .PLN with the runway LocalTC's ATIS has in use). FSLTL's model replaces the stock one when the installed list (`EnumerateSimObjectsAndLiveries`) has FSLTL. Safeguards: `max_reinjected`, no second time, a copy the sim never makes is given up after 15 s, a copy is removed when the sim's own returns, every copy is removed on turning it off, a disconnect or the end of the flight, and a replay only shadows. `TrafficControlStatus` (recorded, and shown in Quick Settings) says what's shadowed, reinjected, lost and failed.

**Controllers with personalities** (`[atc] personalities`, on by default). Each station is a controller of its own, picked from the station's name (`atc_core/personality.py`: the role narrows the kind, the name picks one, so Denver Center is the same person every flight and in every replay): calm, formal, friendly, strict, hurried, dry or conversational. The kind sets their acknowledgement and "say again", how often they greet and sign off and with what, how a repeated mistake is corrected (`firmer`: "negative, I say again ..."), their sentence length and habits of speech, and their voice (pace and expressiveness on top of the role's). How busy the frequency is (transmissions in the last five minutes, traffic within 15 nm) makes any of them terser and quicker. The language model gets the controller's description (`LlmRequest.persona`, not part of the replay key) for the words it chooses; the same checks hold every value, runway, route, frequency and readback. The log names each controller as the flight meets them.

**CPU or graphics card** (`[llm] cpu_only`, on by default): the model runs on the CPU and leaves the graphics card and its memory to the sim; its timeouts are doubled to match. Turn it off (Quick Settings → ATC) on a machine with video memory to spare.

**Standard pressure.** "We're on STD", "set to standard", "QNE" and "29.92" are understood. Up in the flight levels ATC reads your altitude as the flight level (pressure altitude), whatever the sim's altimeter setting says. Some airliners keep their own STD while the sim's setting stays on the local one, and that is not an altitude deviation.

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

**Manner** (`tts/voices.py` `DELIVERY`): Piper's pace (`length_scale`), expressiveness (`noise_scale`) and rhythm (`noise_w`) are set per call, by who is talking. Tower is quick and clipped, centre slower and measured, ground conversational, and the ATIS flat and even like the recording it is. Each station gets a few percent of its own on top, the same every time. The words differ too. Each controller has a usual way of saying each instruction, all standard phraseology: "runway 06L, taxi via A4" at one field, "taxi to runway 06L via A4" at another; "contact Toronto Centre 135.55", or "on 135.55". A controller mostly keeps to its own habit, and now and then (12%) says it another correct way. What has to be read back never changes.

**Pacing.** Transmissions never overlap, and the ATC engine knows how long its words take, so it doesn't start the next call (or answer the copilot) until the frequency is quiet.

## ATIS and weather

MSFS doesn't give add-ons its ATIS or METARs, only the weather where the aircraft is, so LocalTC builds each airport's ATIS itself (`atc_core/atis/`), in the FAA format (JO 7110.65 2-9-3, the AIM) in the US and Canada and the ICAO one (Annex 11, Doc 4444) elsewhere, or as `[atc] phraseology` says.

**The weather** is sampled on or near the airport (until you get there, from an airport within 150 nm; the altimeter is the airport's own):
- **Wind**: calm, variable ("wind variable at 4"), gusts, and a direction varying 60 degrees or more ("variable between 240 and 300").
- **Visibility** in statute miles with fractions ("1 1/4") or, ICAO, metres and kilometres ("10 kilometres or more"); **RVR** when it's a mile or less.
- **Present weather**: rain and snow by how hard they fall (light, heavy), freezing rain and fog below zero, a thunderstorm for a downpour with strong gusts, fog, mist, haze, smoke.
- **Sky**: the clouds the aircraft flew through climbing out or coming down within 30 nm (few, scattered, broken, overcast, the ceiling), an obscured sky with a vertical visibility in fog, "sky clear" once it's been seen clear high enough; ICAO: clouds below 5,000 ft, "no significant cloud", CAVOK. The sim has no cloud report, so until the aircraft has seen them the sky is left out, which the FAA's ATIS may do anyway in good weather.
- **Temperature and dew point** (the sim has no humidity: the dew point is estimated from the visibility and precipitation), the **altimeter** (inches, or QNH in hectopascals), the ICAO **transition level**, and remarks: density altitude, the pressure rising or falling rapidly.

**The operations:**
- **Approaches and runways in use**, one approach per landing runway ("ILS runway 34R approach in use"), simultaneous approaches to parallel runways far enough apart ("simultaneous ILS approaches in use, runways 06L and 06R"), landing and departing runways, and "runway change in progress" after one.
- **Notices** (`[atc] notams`, on by default): the sim has none, so each airport gets a few ordinary ones per session: a taxiway closed or under construction, a runway closed (never the longest), an ILS or its glideslope out, approach or edge lights out, a VOR out, bird activity. ATC works to them: a closed runway is never used (and asking for it gets "unable, runway 16R is closed"), taxi routes go round a closed taxiway where there's another way, an ILS out isn't an approach, a glideslope out makes it the localizer approach.
- **Advisories from the weather**: low visibility procedures, low level wind shear, runway condition codes and braking action on a wet, snowy or icy runway, de-icing.
- **Separate arrival and departure ATIS** where a US airport has two frequencies (Denver, Los Angeles): each with its own letters, and ATC gives the one for your flight.

**The letter** advances with every new hourly observation (at 53 minutes past, even if nothing changed), with a change in the weather that matters between them, and with any change in the runways, approaches, notices or advisories, at most every 10 minutes. If you're on ground, tower or approach when it does, ATC tells you: "information Charlie is now current, altimeter 29.90".

**It's read on a loop** while it's tuned, with small differences in the wording each time round, the way a controller's recording sounds; the information is the same.

- **The runway in use and the approach come from the ATIS**, for taxi clearances and arrivals alike: ATC expects you to fly the approach the ATIS advertises for your runway (unless your aircraft can't fly it, or you ask for another). The runway only changes when the tailwind on it passes 5 kt.
- ATC uses it: a taxi clearance adds "information Charlie is current, altimeter 29.92" if you didn't report the current letter ("with information Charlie"). The takeoff clearance gives the wind and any cautions. Descents give the destination altimeter, and approach checks you have the current ATIS. The landing clearance adds cautions.

## Unscripted moments

The language model reads what you say, the engine decides, and the templates speak. So you can go off script and still get a real answer:

| You say | ATC |
|---|---|
| "request direct BLAKO" / "direct to the airport" | "cleared direct BLAKO" (read it back) |
| "request vectors (for the ILS 26)" | a heading to an 8 nm final, then the approach as usual |
| "could we get runway 16R" (on the ground) | a new taxi route to it, or "expect runway 16R"; "unable, wind ..." if the tailwind is over 10 kt |
| "we'd like the visual runway 26" (arriving) | "expect visual approach runway 26"; unable in low visibility |
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
- **Taxiway names** come from the sim's scenery, which is sometimes wrong. A numbered taxiway ("A4") that the data has in two unconnected places, leading to different runways, isn't named. To correct an airport, put an `airport_fixes.toml` in the LocalTC data folder (next to `settings.toml`): rename a stretch of taxiway, or say which taxiway a runway's holding point is on. The format is in `src/localtc/atc_core/airport/airport_fixes.toml`, which ships Montreal's.
- **Gates** (`[atc] real_gates`, Quick Settings → ATC → "Real gate names", on by default): ground sends you to gates by the airport's real names ("Gate E9"), even where the scenery numbers its stands differently ("GATE 88"), and an international arrival goes to a gate that takes international flights. The names come from OpenStreetMap, downloaded once per airport the first time a flight plan names it, and kept for 60 days (`%LOCALAPPDATA%\LocalTC\gates`). Without a connection, or where OpenStreetMap has no gates, ATC uses the scenery's names.
- **Runways** (`[atc] enforce_fpln_runways`, Quick Settings → ATC → "Enforce FPLN runway assignments", off by default): off, ATC gives the runways in use, from each airport's ATIS (or the best for the wind), whatever the flight plan says, as real controllers do. On, it gives the flight plan's departure and arrival runways (SimBrief's `plan_rwy`, or typed in), where the airport has them. A runway you ask for ("request runway 34R") wins either way.
- **Other traffic on the frequency** (`[atc] chatter`): now and then, when it's quiet, you hear the controller with other flights. "Westjet 452, runway 06L, cleared to land, wind 060 at 6", and the readback. They use the airport's own runway in use, wind and real taxi routes, and airlines that fly there. Nothing is behind them: no traffic is simulated, and nothing is for you to answer. They show dimmed in the radio log and never step on your own exchanges. Tower and ground are busiest; centres are quieter.
- **Callsigns** (`[atc] callsign_check`): a call with another flight's callsign ("Westjet 452", or your airline with another number) isn't answered as yours: "station calling Montreal Ground, say again your callsign". One digit off on a first call ("Air Canada 797" for 779) gets the same. Speech-to-text slips are fine ("Canada 779", "Air Canada 79"), and so is a readback without the callsign.
- **Radio range** (`[atc] radio_range`): an airport's frequencies reach only so far. VHF stops at the radio horizon, about 1.23 × (√ your height + √ the antenna's) nm. Within it, each kind of frequency works out to twice its FAA protected service volume (FAA Order 6050.32B):
  - ground and clearance: 3 nm protected, so a few miles
  - tower: 10, 15 or 30 nm by the size of the airport
  - approach and departure: 30-55 nm
  - ATIS: up to 60 nm

  Call a tower 60 miles away and nobody answers; the log says why ("Montreal Tower is 60 nm away; its radio reaches about 30 nm at this altitude"). Centres aren't limited: each works its whole airspace through remote sites.

**Climbing out.** Departure climbs an airliner to the top of its airspace (17,000 ft, or the filed level if lower) and hands it on. Each centre has its own usual step on the way up (FL230 to FL280). You get that, then your cruise once you're nearly at the step. Checking in on the way up gets "continue climb", and a handoff taken with "good day" and the station's name is taken, even without the frequency. With the sim paused, ATC doesn't call you; answers to your own calls still come.

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

### The intercom: the copilot as Pilot Monitoring

A second key talks to the copilot instead of ATC (`[voice] intercom_key`, Right Alt by default, or a yoke button
with `intercom_joystick`; Quick Settings → Copilot). It hears you on the same microphone and answers in a dry
headset voice, never over the radio. The **IC** button holds the intercom from the app, and **CREW** sends typed
words to the copilot instead of transmitting them.

It works the aircraft through SimConnect and says so once the sim shows it ("Flaps 2."), or that it didn't take:

| Say | It does |
|---|---|
| "gear down", "gear up" | the gear |
| "flaps one" (A320: "one plus F"), "flaps full", "flaps up" | the flaps, named the way the aircraft names them |
| "landing lights on", "strobes off", "beacon on", "taxi lights", "nav lights", "logo lights" | the lights |
| "arm the spoilers", "speedbrakes extend" / "retract" | the spoilers |
| "autopilot on" / "off", "autothrottle on", "heading mode", "nav mode", "approach mode", "altitude hold", "vertical speed mode", "level change" | the autopilot |
| "set heading 270", "altitude 10,000", "flight level 240", "speed 250", "vertical speed minus 1,500" | the autopilot's settings |
| "squawk 4521", "tune 121.9", "standby 118.7", "swap" | the transponder and COM1 |
| "altimeter 29.92", "QNH 1013", "standard", "parking brake set" / "release" | the rest |

Several in one breath work ("gear down, flaps three, landing lights on"). It refuses what isn't safe, with the reason:
gear up on the ground or without a positive rate, flaps or gear above their limit speeds (from the aircraft's profile),
any flap change on the takeoff roll, speedbrakes below 1,000 ft, the parking brake while moving. It asks you to
"confirm" first for an emergency squawk, a squawk other than ATC's, an altitude other than the one ATC cleared, and
the autopilot off below 500 ft. A radio call said on the intercom by mistake is offered to be sent.

The copilot's voice is female, male or either (`[crew] voice_sex`, eight voices each with `voice_pick`); it uses the
same voice for its readbacks on the radio.

**It speaks first.** The copilot watches the sim and ATC and says what a Pilot Monitoring says, unasked: a greeting
with the route and the fuel against the SimBrief plan, the ATIS, "call for the clearance when you're ready", checklist
and briefing prompts; on the roll "one hundred knots" (Airbus; "eighty" otherwise), V1 and rotate from the plan's
speeds or the aircraft's own, "positive rate"; "one thousand to go", ten thousand, the transition; the handoffs; top
of climb and of descent, the destination's ATIS, "localizer alive"; on approach "one thousand, stable" (or what isn't),
minimums, "spoilers", the rollout call; and a summary at the gate. It warns of what's unsafe: config on the roll, a
runway ahead without a clearance, gear not down at 1,000 ft, not cleared to land at 500 ft, a stall or overspeed, flap
and gear speeds, an engine failure, the altitude or heading off what ATC gave, a readback or handoff missed, traffic
closing, low fuel. Every call has its trigger, says once (or again only after a pause, and not more than twice), and
never talks over ATC or you; safety calls cut in.

- `[crew] verbosity`: `quiet` (safety only), `standard` (callouts, relays and reminders) or `chatty` (status updates
  too). Below 10,000 ft it never chats. Say "quiet please", "keep me posted" or "normal callouts" mid-flight.
- `[crew] hands = "pm"`: it works its own side as it calls it: the next frequency in standby, the transponder code, the
  altimeter at the transition, the exterior lights, gear up on positive rate and the flaps on schedule after takeoff,
  the cleared altitude and heading, the after-landing flow. The captain's side (parking brake, engines, the autopilot,
  flaps for takeoff and landing) it never touches; it says when something there is missed. `"calls"`: it touches
  nothing.
- "before takeoff checklist" (or "run the checklist") reads one against the aircraft, setting its own side and holding
  on anything of yours that isn't right; "brief" gives the departure or arrival briefing; "status" the fuel, distance
  and ETA.
- It also watches for turbulence (from the load factor), wind shear below 1,500 ft and ice; reads the destination's and
  alternate's ATIS for the weather ahead (the sim gives nothing along the route); suggests step climbs (asking ATC
  itself when it works the radio); checks the STAR's restrictions from the sim's navdata while descending via it; asks
  "field in sight?" on the way in to a visual; confirms the autobrake and calls the reversers; says when you leave the
  taxi route; and on an emergency, engine failure or low fuel names the nearest suitable airports. `[crew] repeat_atc`
  (chatty only) has it say ATC's instruction back to you before your readback.
- From the phone or the website's full-screen Flight Tracker (the INT channel beside COM1 and COM2), typed lines go to
  the copilot on the intercom instead of ATC. The phone can take them by voice too (the microphone in the message box).
- `[ui] replay_audio` (Quick Settings → ATC voice → Play buttons): a play button on each transmission in the app, on the
  phone and on the website, ATC and the copilot as heard and you as the microphone took you. Kept in memory only.

**Aircraft profiles** (`src/localtc/crew/profiles/*.toml`, and your own in `%LOCALAPPDATA%\LocalTC\profiles`, which win)
name the flap detents, the placard speeds, and any action an add-on wants sent another way: its own key event, or an
L:var (MSFS 2024), the autobrake's positions and whether it has reversers. The stock A320neo has one; everything else gets the sim's standard key events. Add-on aircraft that
ignore those (PMDG, Fenix) need a profile of their own; until then the copilot says when a command didn't take.

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

**Radar vectors** (`atc_core/vectors.py`): approach flies an arrival round a pattern worked out in the runway's frame: a join to a downwind on the arrival's side, a base turn about 17 nm out (10 for slow aircraft), and a 30 degree intercept that carries the approach clearance ("maintain 4,000 until established on the localizer", or "on the final approach course" for a VOR, NDB or RNAV approach).

**Approaches** (`atc_core/airport/approaches.py`): every kind the sim's airport data lists: ILS, localizer (LOC), LDA and SDF, the localizer back course (reverse sensing), RNAV (GPS) with its minima lines (LPV, LNAV/VNAV, LP, LNAV), RNAV (RNP) (RNP AR, offered only to an aircraft that can fly it: an airliner), VOR and VOR/DME, NDB and NDB/DME, and circling-only procedures (VOR-A). Each has typical minima: a decision altitude on a glidepath (ILS 200 ft and 1/2 mile, LPV 250, LNAV/VNAV 350) or a minimum descent altitude with step-down fixes (LNAV and localizer 450, VOR 550, NDB 650). The RNAV line depends on the aircraft: the sim's GA navigators have WAAS and fly the LPV, airliners the LNAV/VNAV. ATC gives the approach with the lowest minima the aircraft can fly among those the weather allows, the visual in good weather in the US and Canada, and whatever you ask for by name ("request the VOR 34", "the localizer back course 26", "the RNAV Yankee"). A glideslope out of service turns the ILS into the localizer approach ("ILS or LOC"); an ILS out takes the localizer approaches away; approach lights out raise the visibility needed. A runway with no instrument approach, in weather too poor for a visual, gets one to another runway and a circle to land, with the side restricted when the landing is the other way round: "cleared VOR runway 16 approach, circle west of the airport for a left downwind to runway 34" (FAA JO 7110.65 4-8-6). Circling minima and the circling area grow with the approach category (A to D). The sim doesn't say which localizer approaches need DME, so they're all "LOC"; LOC/DME, VOR/DME and NDB/DME asked for or read back are understood. Altitudes step down with the miles still to fly; an arrival too high for the distance is sent out on a downwind first. The pattern only moves forward, so drifting over a boundary never turns an aircraft back.

**Controllers' habits** (`atc_core/personality.py`): each station, by its name, greets or not on its first real call ("good afternoon", by the sun where you are) and signs off handoffs its own way.

**Automatic alerts:** moving without a taxi clearance, runway incursion, takeoff or landing without a clearance, and emergencies.

**Emergencies** (`atc_core/diversion.py`): a mayday or pan-pan gets priority and the questions (nature, fuel, souls). Far from the destination, ATC looks for the nearest suitable airport: a hard runway long enough for the aircraft's weight, with an ILS or a tower counting as a few miles closer. It fetches the layouts of the airports the sim lists around the aircraft, then offers the best one ("the nearest suitable airport is Yuma MCAS, 25 miles east, runway 21R, 13,300 feet, ILS available, say intentions"). Ask for it ("request vectors to the nearest suitable airport"), or name another ("divert to El Centro"), and it becomes the destination: a heading there with a descent at your discretion, a new heading if you stray, then approach and tower as for any arrival. Close to the destination (40 nm), or with nothing much closer, it's "cleared direct" the destination instead.

**Phraseology** lives in `src/localtc/atc_core/phraseology/templates/*.toml`: one file per controller, with typed `{slots}`, required and optional readback elements, and an example pilot readback. Templates are validated when loaded. ICAO wording overlays the same ids from `templates/icao/*.toml` (text and pilot readback only, so readback checking is identical); which one a controller uses comes from its region (`atc_core/region.py`). VFR flights (`[flight] rules = "VFR"`) take the calls in `templates/vfr.toml`, handled by `atc_core/vfr.py`; the airspace class around an airport (Class B/C/D, ICAO CTR/ATZ) is in `atc_core/airport/classes.py`, with the Class B and C airport lists kept by hand.

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
`LocalTC-Setup.exe` (NSIS, `installer/localtc.nsi`, built by `tools/build_installer.py`) and `SHA256SUMS` as a GitHub release. A tag with a
suffix (`v0.3.0-rc1`) is a pre-release, which neither the installer nor the updater picks up.

To try the installer before a release exists, `python tools/build_installer.py --offline` builds
`installer/LocalTC-Setup.exe` carrying the committed source (git `HEAD`) instead of downloading a release.
It needs NSIS: `brew install makensis` on a Mac, `winget install NSIS.NSIS` on Windows.

## Licence

LocalTC is free software under the [GNU Affero General Public License v3 or later](LICENSE). Run it
for anything, read and change the source, pass it on — and if you distribute a modified version, or
run one as a service other people use, those people get your source under the same licence.

Third-party components keep their own licences: the Whisper models, the Piper voices, and the Inter
and JetBrains Mono typefaces bundled with [the website](site/fonts/) under the SIL Open Font License.
The airspace outlines in [`src/localtc/atc_core/airspace/`](src/localtc/atc_core/airspace/README.md) come
from the VATSIM community's VATSpy Data Project and SimAware TRACON Project and are CC BY-SA 4.0.
Real gate names are © OpenStreetMap contributors, available under the Open Database License (ODbL).


_Shamelessly vibecoded_
