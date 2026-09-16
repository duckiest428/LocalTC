# LocalTC

Free, open-source, offline-capable ATC for **Microsoft Flight Simulator 2024**. No cloud, no API keys.

- A deterministic state machine handles standard ATC (clearances, handoffs, sequencing).
- A small local LLM (1B–4B, via Ollama) handles the hard cases.
- Pilot speech is transcribed locally with faster-whisper. ATC speaks through Piper with radio-effect DSP.

Windows is the only supported runtime. Development works on macOS too, using recorded sim sessions.

> **Status: Phase 0 (foundations and recorder).** The sim bridge, recorder and replay work. ATC, STT, TTS and DSP are placeholders.

## Layout

```
src/localtc/
  sim_api/     Event types, SimSource interface, session clock, unit decoding (pure; any OS)
  sim_bridge/  Live MSFS 2024 bridge over SimConnect (ctypes; Windows only at runtime)
  bus/         In-process async pub/sub
  recorder/    Bus events -> timestamped JSONL + WAV sidecars
  replay/      ReplaySource: plays a recording through the SimSource interface
  atc_core/ stt/ tts/ dsp/   Later phases
  app.py       Wiring: config -> source -> bus -> recorder
  cli.py       `localtc run | record | replay | inspect`
tools/         Script shortcuts, plus make_fixture.py for the synthetic test recording
tests/         Runs on any OS; tests/windows/ needs a live sim
```

**Dependency rule:** only `app.py` may import `sim_bridge`, and `sim_api` imports nothing else from LocalTC. `lint-imports` and `tests/test_architecture.py` both enforce this. Everything except the bridge can be built and tested on a Mac against recordings.

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
```

Pick the source in `config/localtc.toml` (`[source] kind = "live" | "replay"`) or with `LOCALTC_SOURCE=live|replay`.

## Live bridge setup (Windows)

1. **SimConnect.dll.** Install the MSFS 2024 SDK (in the sim: Options → General → Developers → enable Developer Mode, then download the SDK). LocalTC finds the DLL in this order:
   1. `live.dll_path` in the config
   2. `LOCALTC_SIMCONNECT_DLL`
   3. `%MSFS2024_SDK%\SimConnect SDK\lib\SimConnect.dll` (or `%MSFS_SDK%`)
   4. `.\SimConnect.dll`
2. **Connection.** The default local named pipe needs no setup. If connecting fails, check `SimConnect.xml`:
   - Steam: `%APPDATA%\Microsoft Flight Simulator 2024\`
   - MS Store: `%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalCache\`
   - If a leftover `simconnect_ws.exe` is still running after the sim closed, end it in Task Manager.
3. **Mute the built-in ATC.** SimConnect can't disable it. In MSFS, set Sound → Character voices to 0 and turn off ATC subtitles and assistance.

On connect, LocalTC logs the sim version (major 12 = MSFS 2024, 11 = MSFS 2020) and saves it in each recording header.

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
