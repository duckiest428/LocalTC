# Findings from the sim

What the Windows session (docs/windows-session.md) found running LocalTC against MSFS 2024, newest first, for the
Mac session to read after a pull. Each entry: the date, the aircraft or airport, what failed, what was changed, and
what's still open.

## 2026-10-08: FlyByWire A380X, the copilot's hands
- Lights, squawk, COM active take the sim's events. `A32NX.FCU_HDG_SET` / `SPD_SET` / `ALT_SET` (FlyByWire's custom
  events) set the FCU (by eye) and the sim's AUTOPILOT vars follow; AP_SPD_VAR_SET didn't. New `fbw_a380.toml`.
- Flaps: FLAPS_1/2/UP and FLAPS_SET (quarters) move `L:A32NX_FLAPS_HANDLE_INDEX` 0-4; FLAPS HANDLE INDEX stays 0, so
  `reads_flaps = false`. Setting the L:var itself sticks but moved no flaps.
- Parking brake: `L:A32NX_PARK_BRAKE_LEVER_POS` 1/0 (set directly, sticks); BRAKE PARKING POSITION reads 0 while it's
  set. `debug hands` chose "set" from that and released it on the way back (put back at once, the aircraft didn't
  move): it now leaves a brake the profile marks `unread` alone.
- Open: spoilers arm (only SPOILERS_ARM_TOGGLE, `L:A32NX_SPOILERS_ARMED` follows it but can't be set; `cannot` for
  now, a toggle against that L:var would do it if the copilot could read L:vars), `A32NX.FCU_VS_SET` (window dashes
  parked), COM standby (the RMP's), autopilot (not tried parked).
- A copilot that could read an aircraft's own L:vars (flaps, spoilers, brake, the Fenix's switches) would check what
  it now only sends: worth a `[reads]` table in profiles and a dynamic definition in simconnect_source.

## 2026-10-08: Fenix A319 (FenixA319 IAE WF SD), the copilot's hands
- It got the stock A320neo profile ("A319" in its title). New `fenix_a32x.toml` (match "Fenix"). Only 130 input
  events (audio volumes): its controls are L:vars, named in its package (`grep -a` over fnx-aircraft-320).
- Work, checked: `L:S_OH_EXT_LT_BEACON` 0/1, `STROBE` 0 off/1 auto/2 on, `NAV_LOGO` (one switch), `NOSE` 0/1 taxi/2
  T.O. (the sim's LIGHT LANDING follows NOSE=2, not the landing lights), `LANDING_L` and `LANDING_R` 2 on (by eye;
  `LANDING_BOTH` moves neither), `S_FC_FLAPS` 0-4 (the lever moves; FLAPS HANDLE INDEX doesn't follow).
- FCU: `L:E_FCU_SPEED` / `E_FCU_ALTITUDE` are encoders that turn by how much they change (+50 = 50 clicks, by eye);
  no variable shows the windows. New `NudgeVar`: read the counter, add past the stop (speed 100, altitude 100), then
  the clicks up (altitude in 1000s from 100). SPD 250 / ALT 12000 checked by eye. Profile: `encoder = true`, `stop`,
  `low`, `step`.
- New profile keys: `cannot = [...]` (straight to "that one's yours"), `unread = [...]` (readings not checked:
  `light_landing`, `ap_speed_sel`, `ap_altitude_sel` here), `lvar = "L:A, L:B"` (a switch each side).
- Open: heading (no stop to count from; dashes when managed), V/S, AP1 (`S_FCU_AP1`, not tried parked), spoilers arm
  (`A_FC_SPEEDBRAKE` is an axis, 1 at rest; nothing followed -1/0), autobrake (`S_MIP_AUTOBRAKE_*` presses moved no
  `I_MIP_AUTOBRAKE_*` light parked), COM standby (the sim's didn't follow), gear (`S_MIP_GEAR`, never touched).

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
