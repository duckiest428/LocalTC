# Changelog

Every release of LocalTC, newest first. The app shows a version's section when an update is available,
and the website's changelog page is built from this file. Versions follow [semantic versioning](https://semver.org):
a new minor version adds features, a patch fixes them.

## [0.3.0] - 2026-09-23

### Added
- **Flight replay.** Pick a flight in the Logbook and watch it again: the aircraft moving along its track on
  the map, the whole radio transcript beside it in step (ATC, your calls, and whether each readback was
  right), and a timeline to scrub with the calls, handoffs, takeoff, landing and alerts marked on it. Play at
  1× to 64×, skip the quiet stretches, and jump from call to call (J and K). It plays from the flight's
  recording, so it's there for every recorded flight, older ones included.
- **Replays on localtc.tech and the phone.** Upload a flight's replay from the Logbook (or turn on "upload
  each flight's replay" in Account) and it plays on the Dashboard's Logbook and in the companion app's new
  Logbook tab. Only the track and the transcript go up, never audio; remove it any time.
- **VFR.** Pick IFR or VFR in New Flight (a SimBrief plan fills it in). VFR flights get VFR ATC: a way out
  ("southbound departure approved"), "frequency change approved" or flight following with a code, radar
  contact and handoffs, a Class Bravo clearance when you ask (and an alert if you fly into Class B without
  one), "radar service terminated" near the destination, and pattern entry from wherever you are (downwind
  on your side, or straight-in). Pattern work too: closed traffic, touch and go, the option, full stop.
- **A VFR map.** The Live Map has an IFR / VFR switch. VFR shows terrain (OpenTopoMap) and the airspace
  class around every airport in view (Class B shelves, C, D, or an ICAO control zone or traffic zone) with
  its ceiling and floor like a sectional. The map opens on your flight's rules and switches with them; the
  switch works any time.
- **Emergency diversions.** Declare an emergency far from your destination and ATC finds the nearest
  suitable airport (a runway long enough for your aircraft, an ILS and a tower preferred), offers it, and
  vectors you there when you ask ("request vectors to the nearest suitable airport", or "divert to" a place
  you name), with a new heading if you wander off.
- **ICAO phraseology.** Outside the US and Canada, ATC talks ICAO: QNH in hectopascals, "holding point",
  "decimal", "vacate", flight levels above the local transition altitude, circuits instead of patterns.
  Automatic by region (Quick Settings > ATC), or forced to FAA or ICAO.
- **LocalTC Companion for iPhone** (in `ios/`): sign in with the same email, and the phone shows the map,
  the radio and the flight, with alerts for handoffs and clearances. On the same Wi-Fi it talks to the PC
  directly; elsewhere through the account server, which holds position and radio in memory only. The map
  has the same IFR / VFR switch. Cockpit and Flight Bag tabs are previews for now.

- **The website's Dashboard** (was Logbook), behind the sign-in, with a sidebar of sections: a live **Flight
  Tracker** for the flight you're flying (the map, traffic, frequencies and radio, like the companion app), the
  **Logbook** with an interactive map of every flight, **Support**, and the account.
- **A map of your flights** in the app's Logbook tab too: click a route for its flights, a flight for its route.
- **Support**: write to the developer from the Dashboard or the app (Quick Settings > Support & feedback), signed
  in to the account; the answer comes to its address. It's emailed on; nothing is stored.
- **The companion app's new tabs**: My Flight, Comms (with COM1/COM2 and typing a call to ATC on the same
  Wi-Fi), Frequencies, Airports, and EFB (coming soon).
- A **Buy me a coffee** button in the app. One click and it's gone for good.

### Changed
- The companion app's EFB preview moved to Settings, so the new Logbook fits on the tab bar.
- **Approach vectors you like a real controller**: onto a downwind on your side, a base turn, then a 30 degree
  intercept that carries the clearance ("maintain 4,000 until established on the localizer, cleared ILS runway
  16L"), with step-down altitudes along the way and an extended downwind when you're too high. Then over to
  tower once you're established.
- **Centres hand you on across the country** (Denver, Salt Lake, Seattle), following the real airspace, even
  with the local altimeter left in up high. Centre descends you via your arrival (or to about 10,000 ft above
  the field), "request descent" near the top of descent gets the descent, and checking in on the way down gets
  "continue descent".
- **Controllers with a bit of personality**: some say "good afternoon" on their first call, handoffs end with
  "good day", "have a good one" or nothing, and common calls vary their wording.
- **Clearance delivery sends you to ground** after the readback: "readback correct, contact Denver Ground
  120.15 when ready".
- **The language model reads only what the grammar can't** (the new default): a correct readback never waits
  for it, and the sim keeps its frames.
- **Taxi readbacks are forgiving** on long routes: a letter or a taxiway lost to speech-to-text is fine, a wrong
  taxiway still isn't.
- The account server passes the flight rules and the flight's airports to the phone, handles support
  messages, and lets the Dashboard's tracker connect (redeploy the Worker, and set its SUPPORT_EMAIL secret).

### Fixed
- The Dashboard on a phone: the section bar at the top no longer makes the page wider than the screen.
- An airport's ATIS works when another airport's controller shares its frequency (Denver's ATIS on 125.6).
- "Request gate" and "request parking" are understood; stopping on a taxiway after landing is no longer
  "parked", and moving on isn't a taxi out.
- Ground warns about an aircraft stopped on the taxiway ahead of you.
- Aircraft that never report engine combustion (the CS300) are seen running by their N1 or RPM.
- Tower keeps the parallel runway approach cleared you for; "Seattle-Tacoma" is said properly.

## [0.2.0] - 2026-09-22

### Added
- **Gates.** Ground sends airline flights to a free gate at the destination ("taxi to Gate C14 via ..."),
  sized for the aircraft (heavies get heavy gates) and never one an AI aircraft is parked on. The taxi
  route ends at that gate, and the Live Map pins it. GA still taxis to parking.
- **LocalTC-Setup.exe.** A small Windows installer that fetches the latest release from GitHub, checks it
  against the release's SHA256SUMS and sets it up. No git, no administrator rights.
- **Updates.** The app checks GitHub for a new version once a day (Quick Settings > Updates): tell me,
  install by itself when LocalTC closes, or never ask. It never updates during a flight.
- **A logbook** of every flight (the Logbook tab), kept on your computer.
- **An optional account** that copies the logbook to [localtc.tech](https://localtc.tech/dashboard.html) and
  feeds the companion app. No password: you sign in with a code sent to your email.
- **ATC zones on the Live Map**: the real centres and approach areas the handoffs follow, colour coded,
  with who you're talking to and who's next.

### Changed
- Handoffs follow real airspace (VATSpy and SimAware data, CC BY-SA 4.0) instead of fixed distances.
- The IFR clearance's "expect ... minutes after departure" and the descent come from the SimBrief plan's
  top of climb and top of descent.

### Fixed
- Check-ins that speech recognition mangled, approach clearances given on the downwind, landing clearances
  before final, departures cleared onto a runway with traffic on it, and taxi routes across the runway.

## [0.1.0] - 2026-09-19

The first version: deterministic IFR ATC from clearance to parking, Whisper speech recognition, Piper
voices, a local language model, the copilot, SimBrief plans and the LocalTC app.
