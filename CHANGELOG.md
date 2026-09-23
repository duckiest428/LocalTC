# Changelog

Every release of LocalTC, newest first. The app shows a version's section when an update is available,
and the website's changelog page is built from this file. Versions follow [semantic versioning](https://semver.org):
a new minor version adds features, a patch fixes them.

## [0.3.0] - 2026-09-23

### Added
- **VFR.** Pick IFR or VFR in New Flight (a SimBrief plan fills it in). VFR flights get VFR ATC: a way out
  ("southbound departure approved"), "frequency change approved" or flight following with a code, radar
  contact and handoffs, a Class Bravo clearance when you ask (and an alert if you fly into Class B without
  one), "radar service terminated" near the destination, and pattern entry from wherever you are (downwind
  on your side, or straight-in). Pattern work too: closed traffic, touch and go, the option, full stop.
- **A VFR map.** The Live Map has an IFR / VFR switch. VFR shows terrain (OpenTopoMap) and the airspace
  class around every airport in view (Class B shelves, C, D, or an ICAO control zone or traffic zone) with
  its ceiling and floor like a sectional. The map opens on your flight's rules and switches with them; the
  switch works any time.
- **ICAO phraseology.** Outside the US and Canada, ATC talks ICAO: QNH in hectopascals, "holding point",
  "decimal", "vacate", flight levels above the local transition altitude, circuits instead of patterns.
  Automatic by region (Quick Settings > ATC), or forced to FAA or ICAO.
- **LocalTC Companion for iPhone** (in `ios/`): sign in with the same email, and the phone shows the map,
  the radio and the flight, with alerts for handoffs and clearances. On the same Wi-Fi it talks to the PC
  directly; elsewhere through the account server, which holds position and radio in memory only. The map
  has the same IFR / VFR switch. Cockpit and Flight Bag tabs are previews for now.

### Changed
- The account server passes the flight rules to the phone (redeploy the Worker for it).

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
