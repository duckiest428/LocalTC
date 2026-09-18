# LocalTC

Free, open-source, offline-capable ATC for **Microsoft Flight Simulator 2024**. No cloud, no API keys.

- A small local LLM (1B–4B, via Ollama) reads every pilot call; a grammar checks it and takes over when the model is slow, missing or wrong.
- A deterministic engine makes every ATC decision (clearances, handoffs, sequencing). Routine calls use exact FAA phraseology; the model words only replies that have no template.
- An optional copilot works the radio for you: readbacks, frequency changes, or every call.
- You talk to ATC with push-to-talk; faster-whisper transcribes locally. (ATC's own voice, Piper with radio effects, comes next.)

Windows is the only supported runtime. Development works on macOS too, using recorded sim sessions.

> **Status: Phase 3 (voice in).**
> - **Working:** the full IFR dialogue from clearance delivery to taxi-in; the local model understanding pilot calls (with checks and grammar fallback); questions, altitude requests and emergencies; the copilot; **push-to-talk speech with Whisper**.
> - **Pilot input:** voice, typed, scripted, or the copilot.
> - **Not yet:** ATC speech synthesis and radio DSP.

## Install (Windows)

Download or clone LocalTC, open PowerShell in its folder, and run:

```powershell
powershell -ExecutionPolicy Bypass -File install\install.ps1
```

It installs everything a flight needs and can be run again safely:
- Python 3.12, via winget if you don't have Python yet.
- LocalTC, with Whisper speech-to-text, microphone and push-to-talk support.
- CUDA support for Whisper if there's an NVIDIA card.
- Ollama and its language model.
- The Whisper model. After that, flights work offline.
- A **LocalTC** desktop shortcut.

Options: `-Cpu` (no GPU support), `-NoOllama` (grammar only), `-WhisperModel small.en`.

On macOS/Linux (development against recordings): `./install/install.sh`.

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
  stt/ tts/ dsp/   Later phases
  app.py       Wiring: config -> source -> bus -> recorder
  cli.py       `localtc run | record | replay | inspect`
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

The microphone stays open and keeps 0.3 s from before the key went down, so the first word isn't cut off. Each clip is saved in the recording (`audio/*.wav`) with its transcript.

**Whisper** (`[voice] model`, `device`): `auto` picks `small.en` on an NVIDIA GPU and `base.en` on the CPU. On a MacBook Air CPU, base.en takes about 0.4 s for a transmission and small.en about 1.3 s, with fewer errors. `localtc setup` downloads the model to `%LOCALAPPDATA%\LocalTC\models`.

**Aviation vocabulary** (`[voice] vocabulary`): Whisper is prompted with standard phraseology and this flight's names: callsign, stations, airports, every runway, and the local taxiways. It is never given the numbers ATC just assigned. Priming it with the expected squawk could make it "hear" the right one when the pilot said another, and hide a readback error. Known mishearings are corrected afterwards: "whole short", "decent and maintain", "1-2000" for one two thousand, and "12,000,000 minutes" for "12,000, one zero minutes". On the spoken edge cases the prompt halves the word error rate (51% to 25%).

**Into the parser.** A transcript goes to the same understanding path as typed text. ATC waits for it before answering, and it belongs to the frequency you keyed on, even if you switch before Whisper finishes. Whisper's confidence is a trigger: in `fallback` mode, a low-confidence transcript goes to the model even if the grammar parsed it. Push-to-talk with no speech gets no reply.

**Testing without a sim or a microphone.** `tests/fixtures/voice_kpae` is a recording of the KPAE departure flown by voice, made with macOS speech voices (several accents, radio filtering, cockpit noise; `tools/make_voice_fixture.py`). `tests/fixtures/voice_clips` holds the 34 edge cases, spoken. `pytest -m whisper -s` checks that the departure flies identically from Whisper's transcripts: every readback correct, each under 0.7 s. It also runs the spoken edge cases; with Ollama, 30 of 34 are understood.

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
