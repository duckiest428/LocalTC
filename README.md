# LocalTC

Free, open-source, offline-capable ATC for **Microsoft Flight Simulator 2024**. No cloud, no API keys.

- A deterministic state machine handles standard ATC (clearances, handoffs, sequencing).
- A small local LLM (1B–4B, via Ollama) handles the hard cases.
- Pilot speech is transcribed locally with faster-whisper. ATC speaks through Piper with radio-effect DSP.

Windows is the only supported runtime. Development works on macOS too, using recorded sim sessions.

> **Status: Phase 1 (deterministic IFR ATC).**
> - **Working:** flight phase tracking, FAA phraseology templates, readback checking, and the full IFR dialogue from clearance delivery to taxi-in.
> - **Pilot input:** typed or scripted for now.
> - **Not yet:** speech-to-text, speech synthesis, radio DSP and the LLM fallback.

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
    engine.py      AtcEngine: the IFR dialogue, handle(event) -> events
    session.py     SessionState + JSON SessionSnapshot (the Phase 2 LLM's view)
    service.py     connects the engine to the bus
  airports/    JSON airport cache (%LOCALAPPDATA%\LocalTC\airports)
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
- `atc_core` only imports `sim_api`, `bus` and `airports`. `lint-imports` and `tests/test_architecture.py` both enforce this. Everything except the bridge can be built and tested on a Mac against recordings.

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

**Readbacks** go through the transcript normalizer and the element extractors. The result is `correct`, `incorrect` ("negative, squawk …") or `incomplete` ("read back …"). A second failure, an unparseable call or an ambiguous one goes to the fallback interpreter. In Phase 1 the fallback says "say again"; Phase 2 swaps in the LLM, which returns the same `Interpretation`.

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
```

**macOS: `No module named 'localtc'`.** If the project is in an iCloud-synced folder such as `~/Desktop`, macOS can mark the editable install's `.pth` file as hidden, and Python 3.12.13+ skips hidden `.pth` files. Tests aren't affected, because pytest adds `src` to the path itself. For the CLI, either move the project outside the synced folder, run `chflags nohidden .venv/lib/python3*/site-packages/*.pth`, or prefix commands with `PYTHONPATH=src`.

`tests/windows/` runs the SimSource contract against the live sim. It only runs on Windows with MSFS 2024 running and `LOCALTC_LIVE_SIM=1`.
