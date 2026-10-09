# The Windows session: what only the sim can answer

You're the Claude session on the Windows PC with MSFS 2024. The Mac session writes most of LocalTC, but it has no
sim, so some things were built from the SDK docs and never seen working. Your job: run them against the real sim, find
what doesn't work, fix it, and leave a note for the Mac session. Read [CLAUDE.md](../CLAUDE.md) first; its rules apply.

## Setup

- Repo: `C:\Users\peter\Documents\LocalTC`. PowerShell, venv not activated: `.venv\Scripts\python`, `.venv\Scripts\localtc`.
- Pull with GitHub Desktop first (ask the user if it isn't done), then `.venv\Scripts\pip install -e ".[dev]"`.
- MSFS 2024 SDK: `C:\MSFS 2024 SDK` (SimConnect.dll is found there automatically).
- Logs: `%LOCALAPPDATA%\LocalTC\logs\` (the app's), reports from the checks in `%LOCALAPPDATA%\LocalTC\simcheck\`.
- The unit tests run here too: `.venv\Scripts\python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_llm_live.py`.

The user has to load a flight in the sim for anything below: ask them to, and say what you need (which aircraft,
parked at a gate, engines off or running). Never move the aircraft or fly it yourself; never change the sim's own
settings.

## The checks

`localtc debug ...` connects to the sim like the app does, tries things, prints a summary and writes a JSON report
(what was sent, what the sim showed before and after). The same checks run as tests:

```
$env:LOCALTC_SIM_LIVE = "1"
.venv\Scripts\python -m pytest tests/test_sim_live.py -v -s
```

| Command | What it does | Needs |
|---|---|---|
| `localtc debug aircraft` | The aircraft's title and model, the copilot profile it gets, every MSFS 2024 input event it lists, and whether the profile's input event names exist | A flight loaded |
| `localtc debug hands` | Each control the copilot can move (lights, flaps, spoilers arm, heading/altitude/speed/VS bugs, squawk, COM1 standby and active, the first officer's altimeter, parking brake with engines off; `--autopilot` adds the autopilot), sent the way the copilot sends it, read back the way the copilot reads it, then put back. `--only light` for some | Parked: on the ground, stopped. Never touches the gear or the engines |
| `localtc debug traffic` | The sim's AI traffic as LocalTC sees it (snapshots, identities and AI states, installed models, FSLTL), then one aircraft created 120 m to the right of yours, seen in the traffic, removed. `--no-spawn` to only watch. `--enroute KSEA 16R --watch 180`: also one flying in on a LocalTC flight plan to that runway, to see if the sim's AI flies it | A flight loaded; Live Traffic on in the sim for the first part |

The code: [src/localtc/simcheck.py](../src/localtc/simcheck.py) (the checks), tested against a fake sim in
[tests/test_simcheck.py](../tests/test_simcheck.py). If a check itself is wrong, fix it there and keep the fake-sim
tests passing.

## Task 1: the copilot's hands ("the aircraft doesn't show what the copilot does")

The copilot sends key events (`LANDING_LIGHTS_SET`, `FLAPS_1`, ...) unless the aircraft's profile says otherwise.
Many MSFS 2024 aircraft ignore the old key events for cockpit switches; they move through **input events** instead.

1. Load the stock A320neo (the first profile, [crew/profiles/a320_stock.toml](../src/localtc/crew/profiles/a320_stock.toml)),
   parked, engines off. Run `localtc debug aircraft`, then `localtc debug hands`. Watch the cockpit while it runs.
2. For each FAIL, find the input event that moves that control in the aircraft's list (the `aircraft` report), and map
   it in the profile:

   ```toml
   [actions.light_logo]
   input = "LIGHTING_LOGO_1"   # a name from the aircraft's list
   on = 1                       # the value for on (and off); some switches are 0/1/2 positions
   off = 0
   ```

   The action names: `light_landing`, `light_taxi`, `light_nav`, `light_beacon`, `light_strobe`, `light_logo`,
   `flaps_0` ... `flaps_4` (by handle index), `spoilers_arm`, `spoilers_disarm`, `gear_up`, `gear_down`, `heading`,
   `altitude`, `speed`, `vs`, `squawk`, `com_standby`, `com_active`, `altimeter`, `parking_brake_on`,
   `parking_brake_off`, `autopilot_on`, `autopilot_off` (see `plan()` in
   [crew/actions.py](../src/localtc/crew/actions.py)). A profile can also use `event = "..."` (another key event)
   or `lvar = "L:..."`.
3. If the control **moved in the cockpit** but the check says FAIL, the problem is the read-back, not the write: the
   sim variable the copilot reads (`AircraftSystems` in
   [sim_bridge/definitions.py](../src/localtc/sim_bridge/definitions.py)) doesn't follow that switch in this
   aircraft. Note which, and fix the read for that aircraft if there's a variable that does follow it.
4. If a control **didn't move and has no input event**, note it; don't guess.
   The FSLabs A319/A320/A321 is the known case: its profile ([crew/profiles/fslabs_a3xx.toml](../src/localtc/crew/profiles/fslabs_a3xx.toml))
   has `hands = false` because the sim's events don't reach it, and `reads_flaps` / `reads_autopilot = false` because
   the sim's variables don't follow it (flap index 0-8, autopilot always off). If its own L:vars can be found
   (the FSLabs SDK, or the sim's dev mode), map them and set `hands = true`.
5. **The A350-1000** (title "A350-1000 (Default Cabin)", no profile yet: it gets `stock`). On a 2026-10-08 flight the
   lights took, but the altitude, heading and autopilot never did, and the sim's flap, gear and autopilot readings
   never moved (flaps read up at flaps 1). The copilot now drops a control after it fails twice with its reading never
   moving, and says so. Map it properly: `debug aircraft` and `debug hands --autopilot` in the A350, then a profile
   `crew/profiles/a350.toml` (match `A350`) with the FCU's input events or L:vars for `altitude`, `heading`, `speed`,
   `autopilot_on/off`, the flap detents (A350: up, 1, 2, 3, full; VFE 255/212/195/186) and `autobrake_*`
   (`autobrake_low`, `autobrake_medium`, `autobrake_max`, `autobrake_off`), and `reads_flaps` / `reads_autopilot =
   false` if no variable follows them.
6. Run `hands` again until it's clean, then fly a short test with the copilot's hands on (Quick Settings → Copilot →
   Its hands) and check it does them in the flight. Repeat for the other aircraft the user flies (a new profile in
   `crew/profiles/`, matched by title or model).

## Task 2: traffic control (EXPERIMENTAL)

[traffic/control.py](../src/localtc/traffic/control.py) shadows the sim's Live Traffic and, in *reinject* mode, puts
back an aircraft the sim drops nearby (`SimConnect_AICreateNonATCAircraft_EX1` for a parked one, `AICreateEnrouteATCAircraft_EX1` for a flying one, `AIRemoveObject`
in [sim_bridge/dll.py](../src/localtc/sim_bridge/dll.py), the answers in [sim_bridge/protocol.py](../src/localtc/sim_bridge/protocol.py)).
None of it has run against the sim yet.

1. `localtc debug traffic`: do snapshots and identities arrive (title, livery, origin, destination, AI state)? Does
   the model list come back, with FSLTL's models if it's installed? Is the created aircraft seen, and gone after the
   remove? Fix what fails (the struct layouts in `protocol.py` are the likely place for a wrong parse).
2. `localtc debug traffic --enroute <ICAO> <RUNWAY> --watch 180` at an airport near the user: does the aircraft fly
   the plan, towards that runway? The plan is written to `%LOCALAPPDATA%\LocalTC\simcheck\LTC02.pln`. If the sim
   ignores the runway in the plan, note it: then LocalTC's ATIS runway can't steer the AI this way.
3. Then in the app: Quick Settings → ATC → Traffic control → Shadow, then Reinject, at a busy airport. The status line
   shows what's shadowed, put back and dropped. Check nothing is put back twice, onto a runway, or on top of another
   aircraft, and that everything LocalTC created goes when it's turned off.

## Task 3: the voices with the sim running

[docs/voices.md](voices.md): Kokoro (a more natural voice on the PC) took 1.1-1.8 s a line on the Mac (real-time
factor 0.2-0.3); nobody has measured it beside MSFS.

1. `localtc tts kokoro` (a 337 MB download), then with MSFS running a flight: `localtc tts bench --whisper`. Note each
   voice's latency, real-time factor and word error in windows-findings.md, and try `--provider kokoro` with
   `[tts] kokoro_model = "int8"` and `kokoro_threads` 2, 4 and 8: which is quickest without the sim stuttering?
2. A flight with Quick Settings > ATC voice > Voices: Kokoro. Does ATC answer late? Does the sim's frame rate drop
   while it speaks? Is any line given to Piper (the status under Voices says)?
3. If the user has an Azure Speech key (KEY 1 from the resource's Keys and Endpoint page, in Quick Settings > ATC voice >
   Voices, and the region): the same with Azure, and the characters it counted for the flight. On the Mac it took
   about 0.55 s a line and Whisper understood it best of the three (word error 0.10).

## Fixing and leaving a note

- Fix in the code, add or adjust a unit test where the fix can be tested without the sim (tests/test_traffic_control.py,
  tests/test_crew_*.py, tests/test_simcheck.py), run the unit tests, commit (CLAUDE.md's rules), and tell the user
  to push.
- Write what you found in [docs/windows-findings.md](windows-findings.md), newest first: the date, the aircraft or
  airport, what failed, what you changed, what's still open. The Mac session reads it after a pull. Attach nothing
  from the user's machine beyond what's needed (the report JSONs are fine to summarise, not to commit).
- Keep changes small and in the files above where you can: the Mac session is working on the same repo, and the user
  carries commits between the machines.
