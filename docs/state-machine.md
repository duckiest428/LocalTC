# Extending the flight state machine

LocalTC's ATC is two machines stacked:

1. **The flight phase** (`atc_core/phase/detector.py`) comes from telemetry and airport geometry only:
   where the aircraft is and what it's doing. It never looks at what ATC said.
2. **The dialogue** (`atc_core/engine.py`) reacts to phase changes, pilot calls and time: it issues
   clearances, hands off, and checks readbacks. What has been said lives in `SessionState`
   (`atc_core/session.py`), not in the phase.

Keeping them apart means the phase is right even if the pilot never talks to ATC, and each can be
tested alone against a recording.

## Phases

```
PARKED ─► TAXI_OUT ─► RUNWAY_HOLD ─► TAKEOFF ─► DEPARTURE ─► CRUISE ─► ARRIVAL ─► APPROACH ─► LANDING ─► TAXI_IN ─► PARKED
            ▲  │           │   ▲        │  (rejected)  ▲                                          │ (go-around)
            └──┴───────────┘   └────────┘              └──────────────────────────────────────────┘
```

| Transition | When (each held for a dwell time) |
|---|---|
| PARKED → TAXI_OUT | on the ground, moving faster than `taxi_start_kt` |
| TAXI_OUT → RUNWAY_HOLD | stopped at a hold-short point, or lined up on a runway |
| TAXI_OUT/RUNWAY_HOLD → TAKEOFF | aligned on a runway, faster than `takeoff_gs_kt` |
| TAKEOFF → DEPARTURE | airborne above `airborne_agl_ft` |
| DEPARTURE → CRUISE | level within `cruise_alt_tol_ft` of the planned cruise |
| DEPARTURE/CRUISE → ARRIVAL | sustained descent, or inside the top-of-descent distance of the destination |
| ARRIVAL → APPROACH | near the destination and low, lined up with a runway, or gear and flaps out |
| APPROACH → LANDING | on final within `landing_dist_nm`, below `landing_agl_ft` |
| LANDING → DEPARTURE | climbing away after getting low: a go-around |
| LANDING → TAXI_IN | slowed down on the ground |
| TAXI_IN → PARKED | stopped with the brake set or the engine off |
| any | a position jump or a newly loaded flight: classified again from scratch |

Every threshold is a field of `PhaseThresholds` and can be tuned without code in
`config/localtc.toml`:

```toml
[atc.phase]
taxi_start_kt = 4
tod_min_nm = 20
```

`PositionContext` (`phase/context.py`) supplies the geometry: on a runway, the nearest hold-short point,
the runway the aircraft is lined up with and the distance to its threshold, and the distance to the
destination. `airport/geometry.py` computes all of it in local meters around each airport.

## Adding a phase

Example: a `HOLDING` phase for an aircraft circling in a hold before the approach.

1. **The enum.** Add `HOLDING = "HOLDING"` to `FlightPhase` in `phase/detector.py`. If it's a ground phase,
   add it to `GROUND_PHASES`.
2. **Thresholds.** Add its tunables to `PhaseThresholds` (e.g. `hold_turn_deg = 300`, `hold_s = 120`).
   They become `[atc.phase]` settings on their own.
3. **Transitions.** In `PhaseDetector._transition`, add the ways in and out under the phases they start
   from:
   ```python
   elif phase is FlightPhase.ARRIVAL:
       ...
       if self._held("holding", self._turned_through(th.hold_turn_deg), t, th.hold_s):
           return FlightPhase.HOLDING, "holding"
   elif phase is FlightPhase.HOLDING:
       if self._held("leave_hold", ctx.final is not None, t, th.approach_s):
           return FlightPhase.APPROACH, f"leaving the hold for runway {ctx.final.end.ident}"
   ```
   Use `self._held(name, condition, t, seconds)` for every condition: it's what keeps noise from flipping
   phases. Anything that needs memory across ticks (like a turn counter) goes in the detector's state and is
   cleared in `_set()`.
4. **Classification.** If a flight can *start* in the new phase (loaded mid-air), handle it in `_classify`.
5. **The rest of the code.** Phase sets in the engine (`DEPARTURE_PHASES`, `AIRBORNE_PHASES`), the
   copilot, `console.PHASES` (the label shown in the console and the app) and
   `ui/controller.py`'s `DEPARTING` all list phases: decide where the new one belongs.
6. **Tests.** `tests/test_phase.py` builds tracks with `tests/helpers/flightgen.py` (a kinematic flight
   generator). Add a case that flies into and out of the new phase, and one with noise that must not
   trigger it. `localtc phases <recording>` prints the timeline of a real flight.

## Adding ATC behavior (a scenario)

Most new behavior doesn't need a phase. It's a reaction in the engine:

| To react to | Put it in |
|---|---|
| a phase change ("went around without saying so") | `AtcEngine._on_phase_change` |
| time and position while nothing else is going on ("contact approach within 40 nm") | `AtcEngine._monitor`, one `elif` per automatic call, guarded by `once("flag")` so it happens once |
| something that must work even with a readback pending ("how do you read?") | `AtcEngine._watch` |
| a pilot request | an intent (below), then a branch in `_on_request` or the `unscripted` table |

A new automatic call in `_monitor`:

```python
elif phase is P.ARRIVAL and tuned == "center" and ctx.destination_distance_nm < 60 and once("expect_star"):
    self._schedule(t, "center.expect_star", {"star": star}, st.comms.tuned, delay=False)
```

`_monitor` only runs when the frequency is free and no readback is pending (`_can_call`). An unanswered
readback is said again once, then dropped (`STALE_READBACK_S`), so one missed readback can't hold up
the automatic calls for the rest of the flight.

### A new pilot request

1. **Grammar.** Add the intent in `readback/intents.py` (`match_intents`) with the phrases that mean it:
   ```python
   if _has_any(tokens, ("request", "hold"), ("hold", "at")):
       add("request_hold", fix=_fix(tokens))
   ```
   If it can legitimately appear together with another intent, add the pair to `COMPATIBLE`.
2. **The language model.** Add it to the intent list and examples in `atc_core/llm/understand.py` /
   `examples.toml`, with the words it must contain in the intent checks (`grounding.py`), so the model
   can't claim it without the pilot saying so. Add edge cases to `src/localtc/llm/eval_cases.toml` and run
   `localtc llm eval`.
3. **The engine.** Handle it in `_on_request` (or add it to the `unscripted` table with a method like
   `_direct`). Decide, then `_schedule` a template (see `docs/phraseology.md`). For "no", use
   `common.unable`, or `_decline()` to have the model word it.
4. **The copilot** (optional): if the copilot should make this call in `full` mode, add it in
   `copilot.py`.

### Testing a scenario

A scenario is a recording plus a scripted pilot (`tests/scenarios/*.toml`):

```toml
[scenario]
name = "holding"
recording = "../fixtures/ifr_kpae_kbfi"
airports = ["../fixtures/airports"]

[flight]
destination = "KBFI"
cruise_ft = 5000

[[pilot]]                                    # say something when a condition holds
when = { phase = "ARRIVAL", after_s = 20 }
say = "Seattle Center, {callsign_short}, request hold at BLAKO"

[[pilot]]                                    # answer an instruction
on = "center.hold"
say = "{readback}"
```

- `when` fires on `phase`, `after_s`, `min_t`, `final_nm_below`, `agl_above`, or `cleared` (a clearance
  read back correctly).
- `on` answers an `instruction_id`. `{readback}` is the template's `pilot_readback`.
- `tune = "tower"` (or a frequency, or `"handoff"`) changes COM1 first.

Run it with `localtc atc --scenario tests/scenarios/holding.toml`. `tests/test_scenarios.py` picks up every
file in `tests/scenarios/`. `pytest --update-goldens` writes its `.golden.txt`: read it before committing,
because it is the expected dialogue from then on.

**Real flights** make the best tests. Fly with developer mode on, **Mark** the moment something goes wrong,
then **Export session**. `python tools/thin_recording.py <export>/recording tests/fixtures/real_<name>`
makes a fixture. A test like `tests/test_cyul_flight.py` replays the pilot's recorded words through today's
ATC and checks each answer.
