# What to test before release

Nothing below has been tried on a real Windows PC with MSFS yet. Work top to bottom: each part needs the
one before it. Tick what works. For anything that doesn't, note what you did, what happened and what you
expected, and send the files listed under each part.

**Before you start:** in Quick Settings, turn on **Developer mode**. Every flight is then recorded with
its audio. When something goes wrong, press **Mark** at that moment; afterwards, **Export session** makes a
zip in Downloads. The zip is the most useful thing you can send back.

## 1. Install and update (Windows)

- [ ] `installer\LocalTC-Setup.exe` (in the LocalTC folder after pulling; a test build carrying the code itself, so it
      needs no GitHub release) installs without admin rights into `%LOCALAPPDATA%\Programs\LocalTC`, and the
      Start menu shortcut opens the app. Windows SmartScreen warns (it isn't code-signed): More info → Run anyway.
      Settings → Apps lists LocalTC, and uninstalling it from there works.
      *If not:* a screenshot, plus `%LOCALAPPDATA%\LocalTC\logs\`.
- [ ] The app window opens; all five tabs load (ATC, Quick Settings, Live Map, Airport Lookup, Logbook).
- [ ] Quick Settings → Performance: the models download (language model, Whisper, voice) and **Preview** speaks.
- [ ] Windows asks once about the firewall for LocalTC (that's the companion app's port, 47800). Allow it on private networks.
- [ ] The **Buy me a coffee** button in the header opens the page in your browser and then disappears for good,
      including after restarting the app.
- [ ] Updating: after a newer release exists, Quick Settings → Updates offers it, and **Install** restarts into the new version.
      (Test this with the next release.)

## 2. The IFR flight (the big one)

Fly a SimBrief flight you know, for example KSAN → KPHX, with the copilot **off** so you do all the talking.

- [ ] **Start** connects to MSFS 2024, and COM1/COM2 and the transponder follow the sim.
- [ ] Clearance: "... IFR to Phoenix, with information X, ready to copy" gets the full clearance, and reading it back gets "readback correct".
- [ ] Ground: pushback (at a gate), taxi with the route and the hold-short, and the handoff to tower.
- [ ] Tower: line up and wait / cleared for takeoff, then the handoff to departure once airborne.
- [ ] Departure → Center → Center: each handoff comes at a sensible point, and checking in gets "radar contact".
- [ ] Descent: "descend via" or a descend-and-maintain around the SimBrief top of descent, then approach.
- [ ] Approach: vectors or the approach clearance, then over to tower on final.
- [ ] Tower: cleared to land, then "exit and contact ground" once you're off the runway.
- [ ] Ground: taxi to the gate; the Live Map pins the gate.
- [ ] Push-to-talk works with your key or yoke button, and what you said appears correctly in the radio log.
- [ ] ATC's voice is clear, each station sounds different, and nothing talks over you.
- [ ] Send me the **Export session** zip from this flight either way. It's the best test data I can get.

- [ ] Emergency diversion (on a later flight, or at the end of this one, over open country far from the
      destination): "Mayday mayday mayday, [callsign], engine fire". ATC asks nature, fuel and souls, then
      within about 15 seconds names the nearest suitable airport. "Request vectors to the nearest suitable
      airport" gets a heading and a descent; the flight's destination (ATC tab, phone) changes to it; flying well off the
      heading for a minute gets a new one; approach and tower take you in.

Things to watch for: calls ATC never answers, "say again" when you said it right, readbacks marked wrong
when they were right, handoffs too early or late, ATC talking when it shouldn't.

### Re-check from the Denver to Seattle flight (fixed since)

- [ ] Centre hands you on as you cross into the next one (Denver → Salt Lake → Seattle), with the altimeter
      on STD or not, and near the top of descent gives "descend via" your arrival (or "request descent" gets it).
- [ ] Approach vectors: a downwind, a base turn, then "turn ... heading ..., maintain X until established on the
      localizer, cleared ILS ..." and "contact tower" once you're on the localizer. Fly the headings.
- [ ] Parked at the gate, tuning the ATIS frequency plays that airport's ATIS.
- [ ] After the clearance readback: "readback correct, contact Ground ... when ready".
- [ ] A long taxi route read back naturally is accepted, even with a slip; a wrong taxiway still isn't.
- [ ] After landing: "request gate" (or "request parking") gets the gate and the route.
- [ ] The frame rate no longer drops when you talk (the language model only reads what the grammar can't).

## 3. VFR (short hops are fine)

- [ ] New Flight → Manual, set the rules to **VFR**. The Live Map switches itself to the **VFR** map (terrain plus airspace circles).
- [ ] The IFR/VFR switch on the map works any time, and the pick holds until the next flight's rules differ.
- [ ] Pattern work at a towered field: "ready for departure, closed traffic" → "make left closed traffic, cleared for takeoff";
      "midfield left downwind, touch and go" → cleared touch and go; after the touch and go → "make left closed traffic".
- [ ] A hop with flight following: tower gives "frequency change approved" after takeoff; calling departure with
      "request flight following" gets a squawk, then "radar contact ..."; near the destination "radar service terminated".
- [ ] Inside a Class B without asking (for example near KSEA or KLAX): an alert. Asking "request Class Bravo clearance" gets it.
- [ ] Look over the Class B and Class C airport lists in `src/localtc/atc_core/airport/classes.py`: I wrote them by hand.

## 4. ICAO wording

- [ ] A flight outside the US and Canada (for example LIRF, EGLL or EDDF): QNH instead of altimeter, "holding point",
      "decimal" in frequencies, "vacate", flight levels above the transition altitude.
- [ ] Quick Settings → ATC → Phraseology **ICAO everywhere** at a US airport switches the wording; **By region** switches it back.

## 5. Account, dashboard and support

- [ ] Quick Settings → Account: the email code signs you in, and a finished flight syncs (the cloud mark in the Logbook).
- [ ] The Logbook tab's map shows your flights; clicking a route marks its rows, and clicking a row shows its route.
- [ ] localtc.tech → **Dashboard**: signed out, only the sign-in shows. Sign in with the code or the link; the sidebar
      switches between Flight Tracker, Logbook, Support and Account, and Logbook shows the same flights on the map.
- [ ] Dashboard → **Flight Tracker** while flying: the phase, the frequencies, the aircraft on the map, traffic and the radio log.
      (The map needs Quick Settings → Account → "Away from this Wi-Fi, send the map ..." on.)
- [ ] Dashboard → **Support**: send yourself a test message. It arrives at your email with the account's address as the reply-to.
- [ ] Quick Settings → **Support & feedback** in the app sends one too (signed in; signed out, Send is greyed).
- [ ] Dashboard → devices: signing out another device works; **Download my data** gives a JSON file.

## 6. The iPhone companion app

It installs from Xcode onto your own phone (free Apple ID: plug in the phone, open `ios/LocalTC Companion.xcodeproj`,
pick your phone and your team, press Run). A free-account install stops working after 7 days; run it again from Xcode.

- [ ] Sign in with the same email; the badge says **Same Wi-Fi** when the phone and PC share a network.
- [ ] **My Flight**: the aircraft and traffic on the map, the IFR/VFR switch, and the card at the bottom opens the details.
- [ ] **Comms**: the radio log, the COM1/COM2 filter, and typing a call (on the same Wi-Fi) makes ATC answer it on the PC.
- [ ] **Frequencies**: the tuned and next stations light up in the airport lists.
- [ ] **Airports**: the departure and arrival airports with runways, ILS and ATIS.
- [ ] **EFB** is greyed out ("Coming soon").
- [ ] Turn the phone's Wi-Fi off (use cellular): the badge changes to **Via server** and the map still moves.
- [ ] Handoffs and clearances pop up as banners.

## What to send back

- The **Export session** zip from the IFR flight (and from any flight where something went wrong)
- Screenshots of anything that looks off
- `%LOCALAPPDATA%\LocalTC\logs\` if the app itself misbehaved
- This list, ticked, with notes
