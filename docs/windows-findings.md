# Findings from the sim

What the Windows session (docs/windows-session.md) found running LocalTC against MSFS 2024, newest first, for the
Mac session to read after a pull. Each entry: the date, the aircraft or airport, what failed, what was changed, and
what's still open.

## 2026-10-08: the copilot's hands, stock A320neo V2 and A350-1000
- `debug aircraft` listed no input events in any aircraft: MSFS 2024 (12.2) sends one descriptor (76 bytes) more than
  `dwArraySize` counts, and `_parse_input_events` wanted an exact fit. Now at least, under one more. 588 (A320neo),
  780 (A350) listed.
- Both FCUs ignore `AP_ALT_VAR_SET_ENGLISH` / `AP_SPD_VAR_SET` (sent with index 3, ALTITUDE LOCK VAR:3 moved but the
  FCU window didn't: checked by eye). Their knobs (`INSTRUMENT_FCU_*_KNOB` A320, `AIRLINER_FCU_*_KNOB` A350) move one
  step per set whatever the value (sign = direction; altitude in 1000s), several in one frame count once, and they
  speed up when turned steadily (30 steps gave +259 kt). New `TurnKnob` command + `sim_bridge/knob.py`: read, a burst
  of at most 20 steps, settle 0.35 s, read again. 3-10 s for 150 kt. A profile asks for it with `knob = "..."`
  (`step`, `var`). The FCU windows showed the values set (A320, checked by the user). Copilot checks wait 20 s for it.
- A350: profile `a350.toml` (landing light input event `AIRLINER_LIGHTS_EXT_LANDING` 1/0, knobs, VFE). Flaps by
  FLAPS_SET pass.
- Squawk and COM standby failed once on the A320 while it was powering up; pass after.
- Open (needs flight, not parked): autopilot on (AP1 input events don't engage on the ground, engines off), V/S
  (the window shows dashes until V/S is pulled), spoilers arm (no response to SPOILERS_ARM_* or `AIRLINER_SPEEDBRAKE`
  parked), A350 autobrake (`AIRLINER_LDG_AUTO_BRK` 0-3 moved no AUTO BRAKE SWITCH CB).
- Unrelated, failing before this: tests/test_cardmodel.py mini replay, test_copilot.py service test (flaky),
  tts providers speed.

<!-- ## 2026-10-08: stock A320neo, hands
- FAIL logo light: LOGO_LIGHTS_SET ignored. Mapped [actions.light_logo] input = "LIGHTING_LOGO_1". Passes now.
- Open: ... -->
