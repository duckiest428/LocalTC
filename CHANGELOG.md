# Changelog

Every release of LocalTC, newest first. The app shows a version's section when an update is available,
and the website's changelog page is built from this file. Versions follow [semantic versioning](https://semver.org):
a new minor version adds features, a patch fixes them.

## [0.4.0] - 2026-09-28

### Changed
- **The language model only listens; the grammar and the controller decide.** Every call goes to the grammar first. When the grammar can't place it confidently, the model reads it and fills in a small form (what kind of call, which request, which values), and ATC acts on that exactly as on the grammar's reading. The model never picks an instruction. "Say again" comes only once the model couldn't make the call out either: a model that runs out of time gets a second, longer try while the app shows the controller working on it.
- **What sends a call to the model**: nothing matched, two requests that don't go together, a readback that isn't right, a request riding along with a readback ("cleared to land, and can we make it a low approach?"), the pilot correcting themselves or hesitating, the callsign missing or one digit off, an altimeter given as "standard", a call that makes no sense to this controller now (a pushback request to a tower), or speech-to-text unsure of the words or of the numbers in them.
- **The model may only answer with calls that fit the controller and the phase**: on approach, "cleared ILS 34R" can't come back as a request for an IFR clearance. It's shown the moment in fixed order: the callsign, the phase, who it's talking to, what's cleared (altitude, heading, squawk, runway, approach), traffic called, the last few exchanges on the frequency, and the readback expected.
- **Faster answers from the model**: it writes only the fields the pilot said instead of a dozen empty ones. On a CPU, the median call went from 2.8 to 1.7 seconds in the test set, with more of it right.
- **Readbacks with a number one slip off** (a digit wrong, two swapped, one dropped or added): when speech-to-text wasn't sure of what it heard, ATC asks "confirm squawk 5015" instead of "negative"; heard clearly, it's "negative" with the right value, as before.
- A request or question along with a readback is answered too: the readback is taken, then the request.

### Fixed
- Smoke or fire on board is an emergency whatever words come with it.
- A registration said in full ("November one seven two lima tango, IFR to Boeing Field") wasn't recognised as the flight's own when words followed it.

## [0.3.5] - 2026-09-25

### Added
- **Keep LocalTC above other windows** (Quick Settings → Sim): never, while flying, or always. Above the sim when it runs in a window or borderless full screen.
- **The taxi route on the map**: the route ground gave, to the runway or in to the gate, drawn on the Live Map with its taxiways.

### Changed
- **No more "stand by".** When the language model needs longer for a call, the app shows the controller working on it in the radio log, and the answer follows; nothing is said in between.
- Questions the grammar can place are answered straight away, without the model: the altimeter, the runway (in so many words: "expect runway 36R for departure"), the wind, the ATIS, the squawk. A question asked along with a report ("short final runway 32, and can we get the altimeter?") gets both.
- **The other traffic on the frequency is the sim's own**: the AI aircraft around, by their callsigns, each told what fits what it's doing (the one at a gate pushes back, the one low on final is cleared to land). With nobody around, the frequency is quiet: nobody is made up.
- **Arrivals fly their STAR.** On a STAR that runs onto the final (an RNAV STAR lined up with the ILS), approach leaves the flight on it and clears the approach on the way: no headings. On a STAR that ends off to one side, the vectors start at its end. Without one, vectors as before; an arrival still high 30 miles out is brought down, not sent out on a long downwind. "Vectors for the ILS" is said once, not with every heading.
- **The takeoff clearance fits the SID.** On an RNAV SID in the US: "RNAV to FACTS, runway 36R, cleared for takeoff". Elsewhere (ICAO, Canada) with a SID: "runway 36R, cleared for takeoff". "Fly runway heading" only without one.
- "Line up and wait" read back without the runway is fine.
- Centres no longer ask after the ride.
- The copilot works a handoff at a human pace: the readback once ATC has finished, the new frequency a few seconds later, and a listen before checking in.
- The language model runs on the CPU by default, leaving the graphics card to the sim.

### Fixed
- **Which approach to expect.** It was always the ILS where the runway had one. In the US and Canada, an arrival in good weather (known visibility of 5 miles or more, no rain or snow) now expects the visual approach, as it would there; bad or unknown weather keeps the ILS (or RNAV). ICAO regions keep the instrument approach. Ask for the ILS and you get it. Once given, the approach to expect doesn't change by itself. The visual is worded as ATC says it ("expect visual approach runway 34R", "turn left heading 320, cleared visual approach runway 34R"), without a localizer to be established on.
- "We'd like to get a taxi for the departure" got "contact tower", and after it every call on ground got "contact tower" again, even once ground had given the taxi. A taxi from ground cancels its own handoff now, and "a taxi for the departure" is a taxi request.
- At another runway's hold line (08R, crossed to get to 31), "request to cross runway 8R" got "line up and wait runway 31". Crossing requests are understood, and tower clears a departure only at its runway: from anywhere else it's "continue taxi, hold short runway 31", and the takeoff after a line up and wait waits until the aircraft is lined up.
- "Give us a little more time" is answered "roger, advise when ready".
- Tower cleared the landing and then let an A350 line up and roll in front of it: the go-around check didn't run in the last mile and a half (the landing phase) and waited for a quiet radio. It now sends the flight around, and back to approach. With the flight on final, tower's other traffic isn't cleared onto its runway.
- An airport's altimeter changed with every call on the way in (29.90, 29.95, 29.96, 30.00): it was the pressure wherever the aircraft happened to be. It's now the airport's: estimated once from afar, then measured on arrival.
- Approach answered "descending via the MARNR8" with "descend and maintain 5,000": it now says "descend via the MARNR8 arrival, expect ...". Speed instructions are "reduce speed to 250 knots", never "maintain" a speed the aircraft isn't flying. A centre no longer offers a higher level minutes before clearing the descent.
- "An estimated arrival runway to Seattle?" in the cruise was answered with Vancouver's runway: airborne, it's the arrival runway and approach.
- Gate "B251": that's the sim's name for an extra stand among Seattle's B gates. Stands numbered unlike the gates around them are no longer assigned, and a stand's letter suffix from the sim ("B7A") is read.
- Speech-to-text: "Contact Clearance" (a readback, not an IFR request), "126 decimal, 125", "this on via" (descend via), "cruising at 13,000" (a check-in), "continue approach" (an acknowledgement), "121771" for 121.7 (a "confirm", not a "negative").

- ATC went silent after "stand by" when the model timed out: the departure runway was asked for three times, and "can we get taxi?" and "can we tail left?" went unanswered. The grammar now hears all of them, and a call that nothing understood gets "say again", never silence.
- "Tail right" from a gate whose taxi route leaves to the right: the tail was sent the wrong way (it goes left, so the nose comes round to the route). A pilot who asks for the tail one way gets it.
- The tug taking up the slack (a jerk back at a few knots) was taken for taxiing off: "hold position, taxi clearance required", twice, during the pushback.
- Two position samples a fraction of a millisecond apart were taken for a teleport. At Indianapolis that put the flight back on the taxi out, talking to Orlando Ground 720 miles away; on the approach it flicked to "cruise".
- Parked aircraft ("that 737 is parked") got "stopped on the taxiway ahead" as the taxi swung past them. Aircraft the sim has parked (no flight, never seen moving) only get a caution when the aircraft is really heading into one.
- After a go-around, approach never sent the flight to tower for the second approach, and tower sent it to the departure airport's departure frequency.
- Approach gave "210 knots", then "250 knots", then "180": speeds only come down now.
- A centre's handoff read back on its own frequency went to nobody once the next sector had the flight: the old controller still hears it.
- Speech-to-text: "great for takeoff", "cut to land", "alt-terminator" (altimeter), "BTLR" for BTTLR and "ILS 32R" where there's only a 32 are all understood.
- With CPU only on, Ollama still reports a sliver of the model on the graphics card, and LocalTC reloaded it at every session start (70 s each). Sessions started close together no longer warm the model up twice at once.

From a real Montreal to Los Angeles flight:
- After landing, "request taxi to a gate" was taken as a request to taxi out, and ground gave a taxi to the departure airport's runway. Once landed, a taxi request is always to the gate; "taxi to a gate", "to our gate" and "to the stand" are understood too.
- "Hold position, there's an A320 stopped ahead of you" for an A320 parked at its gate, twice. Crossing the apron to the taxi route, the aircraft's nose sweeps past the gates, and what's parked there isn't in the way. Once on the route, anything stopped on it (or dead ahead) still gets the caution.
- A taxi readback with a correction ("06L via Charlie. Sorry, 06L at Charlie, Golf Charlie") is judged on the corrected part. "06L left" no longer puts a taxiway "L" in the route, the holding point after "at" isn't part of the route, and "a gate" isn't taxiway A.
- "Hold short, Air Canada 779" without the runway: ATC asks for it ("read back hold short runway 06L") instead of "say again".
- The altitude read back on its own ("6500, Air Canada 779") is a readback, when it's the altitude ATC gave.
- Tuning the next centre's frequency as it was given (the copilot does, and so do pilots) made a readback of the handoff go to nobody: "no ATC on this frequency", and the copilot handed itself off again. The frequency ATC just gave belongs to that controller from the moment it's given.
- No traffic calls from a controller who has already handed the flight off (Socal Approach after "contact tower"), and so no second "contact tower".
- Lining up from a holding point well back from the runway no longer flickers the phase to "taxied away from the runway" and back.

## [0.3.2] - 2026-09-25

### Added
- **Enforce FPLN runway assignments** (Quick Settings → ATC, off by default). Off, ATC gives the runways in use from each airport's ATIS (or the best for the wind), whatever the flight plan says. On, it gives the plan's departure and arrival runways (SimBrief's, or typed in), where the airport has them. A runway you ask for still wins either way.
- **Share a flight.** A public card for any flight in the account, at a link like `localtc.tech/f/…`: the route drawn across a dotted globe, the numbers, and one line from the radio you pick (the clearance, by default). Pasted into a chat, it unfurls with its own picture. Nothing is public until you press Share, never your email or the time of day, and Stop sharing, deleting the flight or the account takes it down. From the Logbook in the app, the Dashboard and the phone.
- **The shared flight's page** shows more than the card: the aircraft and livery, the runways, the cruising level, the weather ATC gave at each end and the route filed. With "Put the replay on the page" (on by default when the flight's replay is uploaded, or uploaded for you from the app), a small replay plays under the card by itself: the path drawn as it's flown, the altitude profile, and the radio scrolling alongside.
- **Display name** (Dashboard → Account): shown as "Flown by …" on the flights and Wrapped recaps you share, and in the link's preview. Empty shows nothing.
- **ATC Wrapped.** Your week, month or year of flying as a story: hours, distance put into perspective, your airports and routes, your softest landing, your readbacks against last time, and for a year your busiest month, longest streak, a standout radio moment and your pilot type. Save any slide as a picture, or share the summary as a link. In the Logbook (with an account), on the Dashboard and on the phone. Only the last finished week, month or year can be seen; the one still going is locked, with a countdown to when it unlocks.
- **Other traffic on the frequency.** Now and then, when the frequency is quiet, the controller talks to other flights, and they read back. The calls use the airport's own runway in use, wind and taxi routes, and airlines that fly there. Nothing is simulated behind them and nothing is for you to answer.
- **Radio range.** An airport's frequencies reach only so far, by the radio horizon and FAA service volumes: ground a few miles, tower 20-60 nm, approach 60-110 nm. Out of range, nobody answers, and the log says why. Centres cover their whole airspace.
- **Stepped climbs.** Departure takes you to 17,000 ft (or your cruise, if lower). Each centre then gives its own usual step, and your cruise as you reach it.
- **Callsign checks.** Another flight's callsign gets "station calling ..., say again your callsign". Speech-to-text slips on your own callsign are fine.
- **Controllers with their own style.** Each station has its usual way of wording each instruction, all standard, and its own pace and rhythm of speech: tower quick, centre measured, the ATIS flat like a recording.
- **Stand by, then an answer.** A question or off-script call the language model can't answer in time gets "stand by", then a longer try (`[llm] patience_s`) before ATC replies.
- **Flight replay.** Pick a flight in the Logbook and watch it again: the aircraft moving along its track on
  the map, the whole radio transcript beside it in step (ATC, your calls, and whether each readback was
  right), and a timeline to scrub with the calls, handoffs, takeoff, landing and alerts marked on it. Play at
  1× to 64×, skip the quiet stretches, and jump from call to call (J and K). It plays from the flight's
  recording, so it's there for every recorded flight, older ones included.
- **Replays on localtc.tech and the phone.** Upload a flight's replay from the Logbook (or turn on "upload
  each flight's replay" in Account) and it plays on the Dashboard's Logbook and in the companion app's new
  Logbook tab. Only the track and the transcript go up, never audio; remove it any time.

### Changed
- A handoff taken with the station's name or "good day" is taken, without the frequency. "Readback correct, contact ground when ready" no longer gets "did you copy?".
- Checking in on the way up gets "continue climb"; saying the altitude again after the check-in is just acknowledged.
- Greetings come with a controller's first transmission only. "Say ride conditions" is asked once a flight.
- A one-taxiway route is "runway 06L, taxi via A4", not "at A4, via A4".
- The companion app's EFB preview moved to Settings, so the new Logbook fits on the tab bar.

### Fixed
- Language model timeouts: a model Ollama had unloaded (a quiet cruise longer than the keep-loaded setting, or that setting on "unload after every call") had to load again mid-flight, 10 to 25 seconds, and the call timed out. During a flight LocalTC now keeps it loaded (refreshed every 10 minutes); the setting is for after the flight. The warm-up also reads the phrasing prompt, not just the understanding one, and a call that still finds the model unloaded says so in the log.
- **Run the language model on the CPU only** (Quick Settings → ATC): leaves the graphics card and its memory to the sim; timeouts doubled to match. The model is reloaded where it should be at the next flight. The log says where Ollama put it (all on the graphics card, on the CPU, or split between them, the slowest), and warns if Ollama holds memory for more than the two contexts LocalTC uses (OLLAMA_NUM_PARALLEL above 2).
- `localtc llm check` measures a cold call and warm ones (load, prompt and answer times), says where the model is, tries either way with `--cpu` / `--gpu`, and says whether Ollama keeps the understanding and phrasing prompts both read (with one context slot they push each other out: OLLAMA_NUM_PARALLEL=2).
- What the language model is told about the moment: every call now shows it, in fixed keys and a fixed order, the callsign, the phase, the station and its role, what the flight is cleared for (altitude, heading, squawk, runway in use), traffic ATC called in the last 3 minutes, ATC's last line and the readback expected. ATC's last line is only shown if it's from the last 5 minutes (returning to a Center sector an hour later no longer shows its old words). The phrasing model is told which controller it speaks for, the runway in use (the same one the template answers give) and the ATIS letter. Measured with `localtc llm eval` against the previous prompt: its answer is usable more often (41 of 49 cases against 35) and the slowest calls are faster (p95 2.3 s against 3.3 s); one case, a "ride's smooth" report, is now taken as an acknowledgement.
- A shared flight's page could show an old card for hours after an update (its scripts were cached by browsers and Cloudflare): the page now loads them by their content's hash.
- Flight cards: a landing with no measured rate shows "---" rather than "0 fpm" (and no Butter badge), and the card carries the real LocalTC logo.
- Standard pressure (STD) in the flight levels was an "altitude deviation" when the aircraft's avionics were on STD and the sim's altimeter setting wasn't. Up high ATC now reads the flight level. "We're on standard" is understood.
- Tower cleared a takeoff with a 777 over the threshold about to land. An aircraft low over the runway now holds the departure.
- "Stopped ahead of you on the taxiway" for aircraft parked at their gates. Only aircraft on the route ahead count now.
- Traffic calls for aircraft just off (or onto) a runway near an airport.
- ATC called with the sim paused.
- A pilot's "request climb" below the filed level lowered the cruise.
- Montreal's 06L was taxied "via A4": the sim names the connector to 06L "A4" as well as 06R's real A4. It's now "via G, C" to the hold on C. A numbered taxiway the sim has in two unconnected places leading to different runways is no longer named at all, and `airport_fixes.toml` (in the LocalTC data folder) corrects an airport's taxiway names and holding points.
- The Dashboard could mix a new page with an old script from the browser's cache: Wrapped went to the Flight Tracker, and the logbook's columns slid one to the left. The site's scripts and styles now carry a version stamp.
- A flight's aircraft was missing from the logbook when the sim wrote its type as `ATCCOM.AC_MODEL_A20N` (the A320neo). Lines without one take it from their recording, and sync again. **Rebuild** in the Logbook measures a line again from its recording (times, landing rate, distance, aircraft) when it came out wrong.
- The Dashboard on a phone: the section bar at the top no longer makes the page wider than the screen.

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
