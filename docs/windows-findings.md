# Findings from the sim

What the Windows session (docs/windows-session.md) found running LocalTC against MSFS 2024, newest first, for the
Mac session to read after a pull. Each entry: the date, the aircraft or airport, what failed, what was changed, and
what's still open.

## 2026-10-10: a whole flight with LocalTC's traffic on (KLAX, Headwind A330-900neo)
- Live traffic plus 80 parked FSLTL aircraft at KLAX: frames dropped, liveries came in late, and the GPU driver crashed
  later in the flight (with the copilot's radios just switched to full). Now 40 parked at most by default, two made a
  second, nearest first, and ten models at most for an airport's parked aircraft (each model's textures are memory).
- New airborne aircraft appeared and then slid quickly somewhere else: the sim flies a non-ATC aircraft on its own
  until it's frozen, which waited for the traffic's first word. The bridge now freezes it the moment the sim assigns
  its object id, where it was made. The two sources' answers were timed by this PC's clock: now by each source's own
  "now" (a source's answer can be a few seconds old), and a correction is eased over 3-20 s by its size.
- Tower holding a departure short raised (a text slot where a Phrase is needed), and the traffic stopped ticking
  for the rest of the flight. Four held departures were then cleared together, two "07R"/"07L" (the runway end
  nearest to where each waited, the opposite way): now one at a time, 90 s apart, the end the user was given.
- The Headwind A330's GEAR HANDLE POSITION showed GEAR_UP 4 s later, the gear moving after that: the copilot's
  check said it didn't take. The gear's check waits 10 s and counts the gear moving.
- Open: the traffic over a whole flight again (frames, the new spawns, the ground give-way), and the FSLabs squawk with
  the aircraft powered.

## 2026-10-09: LocalTC's traffic (live ADS-B, FSLTL) at KORD and KPHX
- A non-ATC aircraft (`AICreateNonATCAircraft_EX1`, an FSLTL title, livery "") frozen with FREEZE_LATITUDE_LONGITUDE_SET,
  FREEZE_ALTITUDE_SET and FREEZE_ATTITUDE_SET (value 1, to its object id) stays exactly where `SetDataOnSimObject`
  puts it (PLANE LATITUDE/LONGITUDE/ALTITUDE, PITCH, BANK, HEADING TRUE): moved 59.5 m in 6 s at 20 Hz, still there 3 s
  later. On the ground its height is GROUND ALTITUDE + STATIC CG TO GROUND read from it (652.3 + 12.5 ft at KORD).
- adsb.lol (and adsb.fi, its fallback) answered about 100 aircraft within 40-50 nm of KORD; adsbdb.com has routes by
  callsign and types by hex. adsb.lol said 429 when asked every 5 s on top of test runs: the sources now take turns
  and a busy one is left for 90 s. airplanes.live answered 403 from this PC.
- `debug traffic --live` at KORD: 24-32 real flights and 23 parked (FSLTL), all taken away at the end. The sim shows
  each within a few metres of where the bridge puts it; the bridge is about 200 m (5-7 s) behind the real flight,
  the corrections eased. Arrivals low on final drop out of the receivers' sight: LocalTC lands them.
- In the app at KPHX with MSFS's air traffic off: 76 parked, the moving traffic "looked excellent". MSFS's own static
  parked aircraft ("Asobo PassiveAircraft", 32 at KPHX, state SLEEP) are a separate setting: still there with air
  traffic off, and LocalTC can't remove them; it now says to turn them off too.
- Open: a whole flight with it on (the go-arounds and holds when the user has the runway, the user's gate kept
  clear), and the FSLabs squawk with the aircraft powered.

## 2026-10-09: SimConnect without the SDK (LocalTC's own client)
- The SDK's DLL was run through a logging TCP proxy (SimConnect.cfg pointing it at 127.0.0.1:5599, relayed to the
  sim's port 500) to see its packets: a 16-byte header (size, protocol 6, 0xF0000000 + the call's number, a running
  packet number), then the arguments, strings in fixed fields (names 256, units 256, tail 12, ICAO 16, region 4, the
  parked-ATC airport 5, a flight plan's path 260). Open is the app name (256), 0, "\0XSF" and 12.2.0.0. Replies are the
  SIMCONNECT_RECV the DLL hands over. `tests/fixtures/simconnect_packets.txt` keeps the DLL's own packets;
  `tests/test_wire.py` checks LocalTC's against them byte for byte.
- `sim_bridge/wire.py` talks over `\\.\pipe\Microsoft Flight Simulator\SimConnect` (polled with PeekNamedPipe, so a
  write never waits behind a read), else the ports in SimConnect.xml. Against MSFS 2024 12.2 with the FSLabs at
  KORD: `debug aircraft`, `debug hands`, `debug traffic` (103 aircraft, 6,110 models, an AI aircraft created and
  removed, KORD's facility data) and `localtc run` all work as with the DLL.

## 2026-10-09: FSLabs A321neo, the copilot's hands; joystick buttons
- Joystick buttons bound through SimConnect (`MapInputEventToClientEvent`, "joystick:0:button:1") are accepted and
  never reported by MSFS 2024 (the log showed "Intercom: joystick:0:button:1 (through the sim)" and no presses). Now
  read from Windows (winmm `joyGetPosEx`, `stt/joystick.py`): a T.Flight Hotas One's buttons 0 and 1 came through
  with the sim focused.
- The FSLabs has hands after all: its clickspots (`ModelBehaviorDefs/Interior/FSLA32X_Interior_Common_*.xml`) send
  `K:ROTOR_BRAKE` with `<EVENT_ID>` (+1 up, +0 down; buttons +0 press, +2 release; FCU knobs +5/+6; the EFIS baro
  +5/+6 and pull +1 / push +0 at its id+7, release +3). Its `L:VC_*` variables read the switches (0/10/20; the flap
  lever 0/110/210, about 105 a detent; the speedbrake lever 10 retracted, 0 armed, and the sim's SPOILERS ARMED
  follows it), `L:FSL_FCU_SPD/HDG/ALT/VS` the FCU's windows, `L:FSL_EFIS_FO_BARO` the first officer's baro (hundredths
  of an inch), `L:FSL_RMP1.VHF.STBY` RMP 1's standby (the sim's COM STANDBY follows RMP 1; the sim's
  COM_STBY_RADIO_SET_HZ doesn't reach the RMP). The sim's LIGHT LANDING etc. never move. Its local HTTP server
  (port 8080) lists L:vars at `/Sim/ListLvar`, which is how the names were found.
- `debug hands` on battery: lights, flaps lever, heading, altitude, speed, V/S, COM standby and active pass. The FCU's
  altitude knob is 100 or 1,000 a click by its 100/1000 switch (`L:VC_GSLD_FCU_100_1000_Switch` 0 = 100); RMP 1's
  inner knob speeds up from the third quick click (2 clicks 15 kHz, 12 clicks 250), so it's turned two at a time.
- Open: the transponder's digits (CLR took, digits didn't, cold: needs AC?), the gear lever (not moved on the
  ground: +1 up, by every other lever), the FCU's push/pull (no answer with the FMGC off), the autopilot's engagement
  (no variable found).

## 2026-10-09: traffic control (shadow, reinject) at RJTT, Live Traffic with FSLTL
Run against the sim from scratch harnesses driving the real `TrafficControl` and `SimConnectSource` (25 min watched,
30-40 aircraft around), then `debug traffic`.
- The installed-aircraft list never came: `EnumerateSimObjectsAndLiveries` was asked for type 1 (ALL: refused,
  exception ERROR); AIRCRAFT is 2. Then its pages (RecvId 38) were all dropped: like the input events, 79 entries in
  80 x 512 bytes. Now 6,110 models, 3,441 FSLTL. FSLTL titles: "FSLTL_A359_JAL-Japan Airlines",
  "FSLTL_FAIB_B738_ASA-Alaska Airlines", "FSLTL_B738_ANA old", placeholders "-STUB"; the livery field is empty.
  `fsltl_model` matched by `\bCODE\b` (no match after "_") and the raw atc_model ("ATCCOM.AC_MODEL B737.0.tts"):
  now the type token's family and the airline code as FSLTL writes it (capitals; "Sky_Victor" isn't SKY), stubs out,
  another airline's colours never.
- Half the "traffic" is static scenery: "Asobo PassiveAircraft ...", STATE_SLEEP, no flight, made-up ids shared by
  many ("ASXGSA" x10). Flagged as duplicate callsigns before; now not followed (`Shadow.flight`).
- Bug: ANA471, taxiing out, stopped in the queue (< 2 kt) when dropped, was put back *parked on the taxiway*. Now
  anything ever seen moving, or in a TAXI/TAKEOFF/LANDING state, isn't put back parked.
- Re-spawns under a new id are sometimes renamed (JA336J -> "Japanair 259"): matched by registration too.
- What `AICreateEnrouteATCAircraft_EX1` allows (docs: `dFlightPlanPosition` = waypoint index + fraction; positions
  where it would be taxiing, taking off or landing are refused): the old plans (from a point in the air, position
  0.05) always failed (exception 22). Created where it was only with: a plan filed from a real airport other than the
  destination, through a waypoint behind it and one where it is, position 2.0, and the aircraft nearer its
  destination than its departure (filed from Kisarazu 13 nm away it came out at -900 ft as a departure; from Narita,
  at 6,900 ft). Arrivals 12 nm / 3,500 ft and 14 nm / 4,500 ft were put on the ground at RJTT; 16 nm / 5,000 ft and
  up worked: `MIN_ARRIVAL_NM` 16, `MIN_ARRIVAL_ABOVE_FT` 5,000. Departures: refused at 3,000-8,000 ft, at 35 nm
  started from -900 ft: not put back.
- A created aircraft starts at 0 kt (60 s to 196 kt) and with the model's ATC airline ("Ltu" on FSLTL's JAL A350).
  `SetDataOnSimObject` on it works: VELOCITY BODY Z (feet per second) gives it its speed at once (221 kt at 3 s), ATC
  AIRLINE / ATC FLIGHT NUMBER its callsign. New `SetAiVar` command.
- The sim's 40 nearest airports at Haneda are all heliports and strips ("RJ26P", "GESF1"): the source now adds the 8
  nearest four-letter ICAO airports to NearbyAirports (diversions get real airports too).
- In the end: parked drops at RJTT put back as FSLTL's model with their callsigns (JAL114, SKY710, ANA248, SNJ22),
  seen by ATC as "Japanair 114". An arrival put back 22 nm out at 7,000 ft flew the 34L final at its speed as
  "Japanair 901" (FSLTL A350) and landed on 34L in one test; in two others it was too high at the end and went around
  (the sim AI descends late; a waypoint on the final didn't change it).
- Open: whether Live Traffic drops arrivals 16+ nm out often enough to matter (none in 25 min at RJTT; the drops were
  parked aircraft, departures just after takeoff, and taxiing ones). Removing a copy that has landed and parked.

## 2026-10-08: stock 787-10 and FSLabs A321neo, the copilot's hands
- 787-10: every control passes with the sim's events (as the 787-9 and 747-8i).
- FSLabs A321-271NX: no input events; its package names only display L:vars (`L:FSLA320_landing_light`,
  `L:FSLA320_ParkBrake`, mouse-rect IDs like `L:FCUKnobID`). Writing `L:FSLA320_landing_light` moved nothing (LIGHT
  LANDING stayed 0); BRAKE PARKING POSITION reads 0 with its brake set. Its profile stays `hands = false`: the copilot
  says the switches are the pilot's. Its own SDK would be the way in, if FSLabs publishes one for MSFS 2024.
- Still open for every aircraft: engaging the autopilot (refused parked in all of them, as the real ones do) and V/S;
  a short flight with Quick Settings → Copilot → Its hands on is what checks them.

## 2026-10-08: stock 747-8i, HorizonSim 787-9, stock 737 MAX 8, the copilot's hands
- 747-8i and 787-9 (HorizonSim, "Boeing 787-9 (GE) Air Canada OC", on the stock Boeing systems): every control passes
  with the sim's key events, and the MCP showed the values (by eye). No profile needed.
- 737 MAX 8: all pass but landing, nav and strobe lights. New `b737_stock.toml`: `LIGHTING_LANDING_LIGHT_FIXED_L/_R`
  0 on, 1 off (each its own); `LIGHTING_POSITION_LIGHT` 0 steady, 1 off, 2 strobe and steady. Input events can now be
  named two at a time (`input = "A, B"`) like L:vars. Strobes off puts the switch at steady (nav on): one switch.
- Open on all three: the autopilot (not tried parked).

## 2026-10-08: Headwind A330-900neo, the copilot's hands
- 24 input events. Lights, squawk, COM standby and active take the sim's events. `A32NX.FCU_HDG_SET` / `SPD_SET` /
  `ALT_SET` set the FCU (by eye: 250 / 147 / 12000); the altitude shows in AUTOPILOT ALTITUDE LOCK VAR:3 only. New
  `AircraftSystems.ap_altitude_sel_3` (AIRCRAFT_MORE) and profile `altitude_index = 3`: the copilot and the checks
  read it as `ap_altitude_sel`. Flaps and parking brake as the FBW A380 (`L:A32NX_*`), spoilers toggle only.
- `debug hands` sent PARKING_BRAKE_SET from the sim's "off" here too; it didn't reach the aircraft (the lever stayed
  1). `debug hands` now also skips what a profile `cannot` do.
- Open: V/S (dashes parked), spoilers arm, autopilot.

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
