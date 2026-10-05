# Changelog

Every release of LocalTC, newest first. The app shows a version's section when an update is available,
and the website's changelog page is built from this file. Versions follow [semantic versioning](https://semver.org):
a new minor version adds features, a patch fixes them.

## [0.4.0] - 2026-09-28

### Added
- **A cloud language model, recommended for quality** (Quick Settings → Cloud language model; off by default). A large model understands your calls and words ATC's and the copilot's replies far better than one small enough to run beside the sim, and it's given the whole flight: the route, every clearance, both ATIS, where the aircraft is and what was said on the radio, where the model on your PC gets only the essentials. Pollinations needs no key and is tried first; with a key, LongCat, Qwen (Alibaba Model Studio), Cerebras, Mistral, NVIDIA NIM, SiliconFlow, Tencent Hunyuan, iFlytek Spark, Baidu Qianfan and OpenCode Zen. A service that's rate limited, out of allowance, slow or down steps aside for a while and the next answers (each try gets at most half the wait, so one slow service never costs the call); a refused key or a model that's gone is skipped for the flight; and when every service fails, the model on your PC answers. Every answer goes through the same checks as the local model's. Keys are kept in Windows Credential Manager; each service has a Test button. While it's on, your words and the flight's details go to the service answering (see the privacy page).
- **The copilot speaks first**, the way a Pilot Monitoring does: a greeting with the route and the fuel against the SimBrief plan (and the zero fuel weight), the ATIS, "call for the clearance when you're ready", checklist and briefing prompts; the takeoff callouts (one hundred or eighty knots by aircraft, V1 and rotate from the plan or the aircraft, positive rate); one thousand to go, ten thousand, the transition; handoffs, top of climb and descent, the destination's ATIS, localizer and glideslope alive; on approach "one thousand, stable" or what isn't, hundred above and minimums, spoilers and the rollout call; a summary at the gate. And the warnings: config on the roll, a runway ahead without a clearance, wrong runway, gear not down at 1,000 ft, not cleared to land at 500 ft, stall, overspeed, flap and gear speeds, an engine failure, off the cleared altitude or heading, a missed readback or handoff, traffic closing, fuel below the plan or the reserve. Each has its trigger, is said once (reminders twice at most), never over ATC or you; safety calls cut in. Below 10,000 ft only what matters.
- **How much the copilot says** (Quick Settings → Copilot): quiet (safety only), standard, or chatty; or say "quiet please" / "keep me posted" mid-flight.
- **The copilot works its own side** (Quick Settings → Copilot → "Its hands"): the next frequency in standby on a handoff, the transponder code, the altimeter, the exterior lights, gear up on positive rate and flaps on schedule after takeoff, the cleared altitude and heading, the after-landing flow. Your side (parking brake, engines, the autopilot, flaps for takeoff and landing) it never touches, but it tells you when something there is missed. Or calls only.
- **Checklists and briefings on the intercom**: "before takeoff checklist" (or just "checklist") is read against the aircraft, the copilot setting its side and holding on anything of yours that isn't right until it is; "brief" gives the departure or arrival briefing from the clearance, the plan and the ATIS; "status" the fuel, distance and ETA.
- **SimBrief's fuel, weights and takeoff and landing speeds** are read with the plan, for the copilot's checks and calls.
- **Talk to the copilot from the phone and the website**: Comms → Copilot in the companion, and Copilot in the website's Flight Tracker; its answers and callouts show in the radio log as intercom lines.
- **The Flight Tracker full screen** on the website (/tracker): the map takes the screen, with the traffic labelled as on the app's Live Map, and the radio and intercom beside it as on the phone: COM1, COM2 and INT (the copilot) along the bottom, each the channel you see and the one you talk on, with a dot where something new was said. The dashboard's tracker is now only a preview: the map and the facts.
- **Play buttons on the radio log** (Quick Settings → ATC voice → Play buttons; off by default): ATC and the copilot as you heard them, and you as the microphone took you, in the app, on the phone and on the website. Nothing plays by itself; the last 80 are kept in memory only.
- **COM1, COM2 and INT on the phone**: the Comms tab has the channels along the bottom like an audio panel, the copilot on INT, a play button on kept transmissions, and a microphone in the message box that turns what you say into the message with Apple's speech recognition (on the phone where it can be).
- **The copilot watches more**: moderate and severe turbulence (and "smooth again"), wind shear below 1,500 ft, ice building up, and the destination's and alternate's weather from their ATIS (gusts, low visibility, low cloud, freezing rain; the sim gives no weather along the route, so it never claims to see storms there).
- **Step climbs**: at the SimBrief plan's step points (or every 5 percent of the weight burned) the copilot suggests the next level, and asks ATC for it itself when it works the radio. Not when you're already up there or ATC just said unable.
- **The arrival's restrictions**: the copilot fetches the STAR from the sim's navdata, says when a restriction was missed while descending via it ("Missed the at or below 12,000 at CEPIN"), and warns before one the descent won't make. Vectors or a plain "descend and maintain" cancel them. If the sim's STAR has none, it says so.
- **"Field in sight?"** on the way in to a visual approach, within 10 miles and below the clouds; your "yes" goes to approach, which clears the visual. "Minimums, approach lights in sight" only when the weather makes it plausible.
- **Autobrake and reversers**: the autobrake setting confirmed before landing (a warning when it's off on a short runway), then "reversers" (or "no reversers") and "autobrake engaged" / "autobrake off" on the rollout, for aircraft whose autobrake positions are known (the stock A320 family).
- **Off the taxi route**: "We're on C, cleared via B B1" when you leave the route ground gave, and a turn that leaves it.
- **Where to divert**: on an emergency, an engine failure or fuel below the reserve, the nearest suitable airports with their distance, runway, ILS and weather where it's known: "Want me to ask for it?"
- **ATC's instruction said back to you** before your readback (Quick Settings → Copilot; chatty only, off by default).
- **Wrapped goes back in time**: the arrows step through every week, month or year back to your first flight, on the website, in the app and on the phone.
- **The copilot answers questions and understands you better**: "how much fuel do we have", "how far to go", "what did ATC say", the weather at the destination, the time, the runway and gate come straight from the flight's data; anything else goes to the language model, which only answers from what the copilot knows (Quick Settings → Copilot → "Language model on the intercom"). Commands in your own words ("drop the gear for me") are read back for your "confirm" first. Filler words no longer stop a command ("set the altimeter to 30.10").
- **Themes** for the app (Quick Settings → Appearance: Radio panel, Midnight blue, Black OLED, Amber avionics, Slate, Daylight) and the companion (Settings → Appearance, the same, or following the phone).
- **Replay speeds from 0.5× to 1024×** on the website, in the app and on the phone.
- **Taxiway names from OpenStreetMap** where the scenery has none, so ground gives a real route to a gate; and the tower says which way to vacate ("vacate right onto E4").
- **The copilot on the intercom, as Pilot Monitoring**: a second key (Right Alt by default, or a yoke button) talks to the copilot instead of ATC, and it answers in a dry headset voice. Say "flaps two", "gear down", "landing lights on", "autopilot on", "set heading 270", "flight level 240", "squawk 4521", "tune 121.9", "QNH 1013" and more: it does it in the sim and says so once the sim shows it, or that it didn't take. It refuses what isn't safe, with the reason (flaps above their speed, gear up without a positive rate, speedbrakes low), and asks you to confirm an emergency squawk, a squawk or altitude other than ATC's, or the autopilot off near the ground. A radio call said on the intercom by mistake is offered to be sent. Aircraft profiles name the flap settings and limits (the stock A320neo first).
- **The copilot's voice: female or male**, eight voices each, the same one on the intercom and for its readbacks on the radio (Quick Settings → Copilot).
- **Real gate names and international gates**: ground sends you to the airport's real gates ("Gate E9", not the scenery's "Gate 88"), international arrivals to the gates that take them, and finds the gate you ask for by its real name. From OpenStreetMap, fetched once per airport and kept; on by default (Quick Settings → ATC → "Real gate names").
- **Language model timing in the app** (Quick Settings → ATC): how long ATC waits for the model's answer, for all its tries, and for the second, patient try.
- **The app tells you whenever the language model times out**: a notice on screen, a line in the radio log and an alert, saying whether it's being asked again or ATC answered without it.
- **Every kind of instrument approach** the sim's airport data lists: ILS, localizer, LDA and SDF, localizer back course, RNAV (GPS) with its minima lines (LPV, LNAV/VNAV, LP, LNAV), RNAV (RNP) for aircraft that can fly RNP AR, VOR and VOR/DME, NDB and NDB/DME, and circling-only procedures (VOR-A). Each is named the way ATC says it ("cleared localizer back course runway 26 approach", "cleared RNAV Yankee runway 16R approach") and joined the right way: "until established on the localizer" or "on the final approach course".
- **Minima decide the approach**: ATC gives the one with the lowest minima your aircraft can fly among those the weather allows. A GA navigator flies the LPV, an airliner the LNAV/VNAV; the RNP AR approaches are for airliners only.
- **Circle to land**: with no instrument approach to the runway in use and weather too poor for a visual, an approach to another runway and a circle: "cleared VOR runway 16 approach, circle west of the airport for a left downwind to runway 34". Circling minima and the circling area grow with the aircraft's approach category.
- Ask for any approach by name: the VOR, the NDB, the localizer, the back course, the RNAV Yankee.
- **A new ATIS**, built to the FAA's format in the US and Canada and ICAO's elsewhere (or as the phraseology setting says), from everything the sim's weather gives: variable winds and gusts, visibility in miles with fractions or in metres, RVR in low visibility, rain and snow by intensity, freezing rain and fog, thunderstorms, mist, haze and smoke, the clouds the aircraft flew through near the airport (few, scattered, broken, overcast, the ceiling, a sky obscured by fog, CAVOK), temperature and dew point, altimeter or QNH, the transition level, density altitude and a rapid pressure change.
- **The ATIS carries the airport's operations**: the approaches and runways in use, simultaneous approaches to parallel runways, a runway change in progress, low visibility procedures, wind shear, runway condition codes and braking action, de-icing, bird activity, and "read back all runway hold short instructions".
- **Notices on the ATIS** (`[atc] notams`, on by default): a few ordinary ones per airport and session, which ATC works to. A closed runway is never used and asking for it gets "unable, runway 16R is closed"; taxi routes go round a closed taxiway; an ILS out is no approach, and a glideslope out makes it the localizer approach; approach lights out raise the visibility needed.
- **Separate arrival and departure ATIS** where a US airport has two ATIS frequencies, each with its own letters.
- **Script or model** (Quick Settings → ATC): how much ATC leans on the language model, for understanding you and for the words of its replies.
  - **Mostly LLM** (the default): the model reads most calls and words the replies to them. The script answers on its own only a call it's sure is routine (a clear "ready to taxi", a correct readback).
  - **Fully LLM**: the model reads every call and words every reply, routine ones included.
  - **Semi script/LLM**: the script for calls it clearly recognises, the model for anything ambiguous, compound or off the script, and for wording what has no template.
  - **Fully scripted**: standard phraseology throughout. The model is asked only about a call the script can't read, and what it makes of it goes back through the script.
  - Whatever the model says is checked before it's on the air. A reworded clearance must keep every number, runway, flight level, taxiway (in order), fix and instruction the script decided, and add none. Anything that fails is said in the script's words. The readback ATC expects is always the script's.
  - The old setting carries over: "the model reads every call" is Fully LLM, "the grammar first" is Semi.
- **Let the model answer beyond the sim's data** (Quick Settings → ATC, `[llm] beyond_facts`, off by default): the model's answers may use what it knows itself, not only what the sim tells it, and a reply saying something the facts don't (a number, a closure, a runway in use) goes out instead of being turned away. It still never gives an instruction, an answer the sim's data has exactly (a runway's length) must still match it, and a clearance it rewords is still checked against the script. When the setting is off and an answer is turned away for going past the facts, the notice says this setting would let it through.
- **Stop the flight at the gate** (Quick Settings → ATC, on by default): parked at a gate or stand at the destination after landing (stopped, off the runway, no taxi instruction still going), the flight ends as if you'd pressed Stop, a few seconds after ATC's last words. Not before departure, not at another airport, and not in a replay unless `[session] auto_stop_in_replay` says so. `[session] gate_radius_m` sets how close counts as at the gate.
- **A record of how ATC reached each answer** in the recording and the flight report (`atc_decision`): what the grammar read, what the model read after the checks, which reading ATC acted on, what it decided, who worded the reply, and why anything of the model's wasn't used. Each ATC transmission says whether the model or the template worded it.
- **Runway lengths and the field elevation** are among what the model is told, and "how long is runway 24R?" is answered from the sim's airport data.
- **Talk to ATC from the phone or the website, anywhere**: a call typed in the companion app's Comms tab away from the PC's Wi-Fi, or in the website's Flight Tracker (a new box under its radio log), goes through the account to LocalTC, which transmits it on COM1 as if typed in the app; ATC's answer comes back in the radio log. The account holds a call for a minute at most, in memory only. A call that can't go out (no flight running) says so in the radio log.
- **The runway in use follows the sim's own traffic** (Quick Settings → ATC, `[atc] traffic_runways`, on by default): ATC watches which way the AI aircraft take off and land at your airports and, when the wind allows, sends you the same way instead of head-on into their stream. Fewer conflicting finals and go-arounds.
- **Go around when you never switched to tower**: on short final, still on approach's frequency and without a landing clearance, approach sends you around ("go around, you're not cleared to land"), once.
- **"Check heading"**: a heading ATC gave and you're not flying (30° off for 20 s, after time to turn) gets "check heading, fly heading 100", twice at most for one heading.
- **Approach answers your check-in after a go-around**: "radar contact, maintain 2,200, expect vectors for the ILS runway 25R approach", and the copilot checks in with "going around".
- **Ask for a gate**: "we'd like gate Echo 9" gets that gate when the scenery has one by that name; when it doesn't, ground sends you to parking rather than making up a gate number. "Negative, we'd like gate E9" to a taxi instruction asks for that gate instead.
- **"Disregard the request"** after asking for a climb or descent takes it back: "maintain FL360", and whatever else you asked is answered. "Disregard" on its own gets "roger".
- **The model knows the time** (the sim's, in Zulu), your filed cruise altitude and your cleared altitude, and, when you ask about them, the traffic taxiing near you and which way the pushback will turn.
- **The Flight Tracker and the companion app show everything the Live Map does**: the ATC zones (the centres on the route, the departure and approach areas, the stretch of final, each tower's control zone), the flight plan route and its fixes, the runways, the taxi route ground gave, the gate, each airport's controllers with the one you're tuned to and the next one marked, and the airspace classes on the VFR map, along with the path flown. The website's tracker also gets the app's IFR/VFR switch, the zones on/off, Route and Follow buttons, and the map key; the companion app gets a zones button and a key.

### Changed
- **The ATIS letter advances with every hourly observation**, as well as with a change in the weather, the runways, the approaches or the notices. Its time is the observation's.
- **ATC and the ATIS agree**: the approach ATC tells you to expect is the one the ATIS advertises for your runway, unless your aircraft can't fly it or you asked for another.
- **The ATIS loop is read a little differently each time round**, the same information in slightly different words, like a controller's recording.
- **The model reads calls; the controller decides.** The model fills in a small form (what kind of call, which request, which values), and ATC acts on that exactly as on the grammar's reading. The model never picks an instruction. "Say again" comes only once the model couldn't make the call out either: a model that runs out of time gets a second, longer try while the app shows the controller working on it.
- **What makes the grammar's reading not enough** (in Semi, every one of these goes to the model; in Fully scripted, only those where the grammar can't read the call): nothing matched, two requests that don't go together, a readback that isn't right, a request riding along with a readback ("cleared to land, and can we make it a low approach?"), the pilot correcting themselves or hesitating, the callsign missing or one digit off, an altimeter given as "standard", a call that makes no sense to this controller now (a pushback request to a tower), or speech-to-text unsure of the words or of the numbers in them.
- **The model may only answer with calls that fit the controller and the phase**: on approach, "cleared ILS 34R" can't come back as a request for an IFR clearance. It's shown the moment in fixed order: the callsign, the phase, who it's talking to, what's cleared (altitude, heading, squawk, runway, approach), traffic called, the last few exchanges on the frequency, and the readback expected.
- **The language model is built for a PC that has little to spare**: its prompt is a third smaller and the same for every call (readbacks, requests and questions alike), so Ollama reads it once and only reads each call's own few lines after that. Its working memory is a quarter smaller (a 3,072-token context instead of 4,096). After a call that ran out of time or a phrased reply, it's read again in the background, so the next call doesn't start from nothing and time out too. Before, once Ollama had lost it, every call could time out in a row.
- **ATC's wait follows how fast the model really is on your PC while you fly**: the flight's warm-up times an ordinary call, and if the model is slower than the timing setting allows, ATC waits longer for it (up to the "second try" time) and the radio log says so, with what to change for quicker answers.
- **Faster answers from the model**: it writes only the fields the pilot said instead of a dozen empty ones. On a CPU, the median call went from 2.8 to 1.7 seconds in the test set, with more of it right.
- **Readbacks with a number one slip off** (a digit wrong, two swapped, one dropped or added): when speech-to-text wasn't sure of what it heard, ATC asks "confirm squawk 5015" instead of "negative"; heard clearly, it's "negative" with the right value, as before.
- A request or question along with a readback is answered too: the readback is taken, then the request.
- **The Flight Tracker shows the path flown before it was opened**: the app keeps the flight's path and hands it over when the website (or the companion app) starts watching mid-flight; the relay keeps it in memory only, like the position.
- **One language model: Llama 3.2 3B**, the one LocalTC's prompts and checks are built and tested around. The others are gone from Quick Settings → Models and the setup profiles.
- **Other aircraft are named as controllers say them**: "Boeing 737", "Airbus A321", "Embraer 170", "regional jet", never the type code spelled out ("bravo seven three seven", "echo one seven zero").
- **Giving way on the ground only when there's something to give way to**: never to an aircraft on a runway (lining up, taking off or landing), never while you're turning, and only when its way and yours really meet close ahead; once for each aircraft. An aircraft taxiing to the same runway ahead of you gets "give way to the Boeing 737, then follow it", once, not a caution for every aircraft in the line. "Crossing left to right" is said only when it clearly is.
- **The ATIS letter changes less often**: a special between the hourly observations at most every 30 minutes (a runway change sooner), and only for a real change (the wind by 10 kt or 60°, the altimeter by 0.03). ATC no longer announces each new letter on its own; the next clearance mentions it, or your check-in with the old letter gets the new one.
- **The ATIS names the instrument approach in visual weather**: "ILS runway 08 and visual approaches in use", not just "visual approaches in use".
- **No more levels offered for no reason**: the centre no longer asks if you can accept a higher level in the middle of the cruise. Ask, and it's given as before.
- **"Stand by for your clearance" means a while**: the clearance comes 40 seconds to a minute and a half later, not a dozen seconds.
- **The initial altitude isn't always 5,000**: it's set by the departure airport's elevation and the SID, the same every time for the same airport and SID, between 4,000 and 10,000 ft above most fields.
- **The model's answers stay on what you asked**: it's given the facts that matter to the call (who's talking, the time, the weather and runway where you are or the approach where you're going), and runway lengths, closures, the runways in use or the field elevation only when your words are about them. It's told to use only what answers the call. (Given everything, it recited "parallel landings are not available" and runway closures to remarks that had nothing to do with them.) Parallel landings are no longer among the facts at all.
- **The model rewords a clearance more reliably**: it's shown exactly what it must keep (the numbers, levels, frequencies, taxiways and fixes), and no example level it could copy (it wrote "FL240" for FL360, over and over). "Radar contact" isn't the instruction "contact" (a dropped "radar contact" was turned away, an added "contact approach" got through). "Readback correct" must be kept, the same thing mustn't be said twice, and "pushback" is the "push" of "push and start approved".
- **One stray value no longer loses the model's reading of a call**: a value it copied from the context ("Looking" with the cleared altitude, a readback with the expected runway) or wrote wrongly ("expect 390, 25 minutes" for a cruise level) is left out and the rest kept. Only a made-up value for the very thing a request is about (the level of a climb request) still has the call read again. A flight level said as three digits ("climb and maintain 390") counts as said.
- **A reading of your call that the checks turned away, with the grammar reading it instead, is no longer an alert**: it's in the radio log and the decision record, and the model still words the reply. The "turned away" notice is for when you hear the script instead of the model.
- **The installer carries only what LocalTC runs**: the release is an allowlist (the app, its settings and its setup), so the website, the server, the iOS app, the tests and anything new in the repository stay out of it.

### Fixed
- The radio log on the app, the phone and the website no longer fills with the sim's own hiccups ("SimConnect exception", "Timed out waiting for airport data"); they're in the log file. Behind them: the sim's list of nearby airports was read misaligned when it came with a few bytes of padding, giving idents like "P@" that LocalTC then asked the sim about.
- **The map across the date line**: a flight from New York to Tokyo drew its route the wrong way round the world, lit up every centre from Montreal to Magadan and lined their names up across the map. The route, the path flown, the zones and the traffic now join up across the Pacific, on the website, in the app and on the phone.
- "SimConnect exception" and "Timed out waiting for airport data for J@" in the far north: an airport ident from the sim's list that isn't letters and digits is no longer asked for.
- The website's pages have addresses without `.html` (localtc.tech/dashboard, /tracker); the old ones still work.
- The path flown on the website and the phone lost the taxi a few hours into a flight (it was thinned by dropping every other point) and took a point only every 200 m: now every 10 m on the ground, and simplified by shape, so the gate, the taxi and the turns stay. Around a big airport the path was also refused by the server for coming in one request with all the traffic.
- Fuel on board and fuel flow were never read from the sim, so an emergency never had an endurance estimate.
- Smoke or fire on board is an emergency whatever words come with it.
- A flight level the model wrote as said ("360") was taken as 360 feet.
- **A question the model filed as a request was turned away.** "How long is the 24 right runway?" came back as a runway request, the check wanted a word like "can" or "request", and the retry said the same, so ATC answered from the script with "expect runway 24L for departure". A call that asks something and asks for nothing is now the question.
- **When the model's answer is turned away, ATC no longer answers a different question.** It gives the sim's data only when that answers what was asked; otherwise it says the information isn't available. ("Weather at Quebec" isn't Montreal's wind; "how long is the runway" isn't "which runway".)
- The model's answer must include the data's own answer when there is one: a reply giving another runway's length is turned away.
- **In the model's modes, the script still answered calls the model had read.**
  - One junk value in the model's reading (the ATIS letter filled with "doing well", "none" or the whole sentence) threw the reading away; the value is now left out and the rest kept, and the form allows only a letter there.
  - A question with "can" in it ("can we get a wind report?") filed as another request was turned away; with a topic named, it's the question.
  - Small talk got the template's line, and a remark nothing could place ("would you like a coffee after your shift?", "that's not parallel, you'd need both 28s") got "say again". In Mostly and Fully LLM, any call nothing can classify goes to the model for its reply, however short or unclear, and with a readback still owed (the model is told what readback ATC is waiting for).
- **A turned-away model answer is never replaced silently, or by something unrelated.** What ATC says instead fits the call:
  - the same clearance in the script's words;
  - the sim's data when it answers the question;
  - "unable, that information is not available" to a question;
  - "roger" to a remark;
  - "say again" only when the words were unclear or a readback is still owed.

  The radio log and a notice say each time that the model's answer was turned away, why, and what ATC said instead.
- **The model's answers say only what LocalTC knows.** A reply that calls something closed, restricted, active, delayed or "expected shortly" without the facts saying so is turned away, as is a runway pairing the airport doesn't have or a runway "in use" that isn't. The model is told the runways in use and the ATIS notices when the pilot asks about them.
- The sim's wind and altimeter answer a question only when the pilot's own words are about them, not just because the model labelled it "weather".
- A reply ending with the callsign was turned away for the callsign's digits.
- The warm-up warning said ATC would wait "up to 35 s instead" of a 40 s setting: it only ever waits longer, and only says so then.
- **Mid-conversation, a question about another flight ("what aircraft is United 2117 in?") got "station calling, say again your callsign"**, from the script, before the model ever saw it. A question in the middle of a conversation with that controller is now taken as yours. And when ATC does ask for the callsign and you answer with it, ATC answers the call you made instead of repeating its last line.
- A question the model filed as "say again" got ATC's last transmission repeated: "say again" now needs words that ask for a repeat.
- A question to Clearance was taken as a request for the IFR clearance, because of the station's name, whether the grammar or the model read it.
- "One last thing, how long is runway 19L?" also gave runways 01L and 01R: "one" was taken as runway 1.
- A request with a question in it went to the grammar alone, which answered the question by its keyword.
- A registration said in full ("November one seven two lima tango, IFR to Boeing Field") wasn't recognised as the flight's own when words followed it.
- The path flown before the Flight Tracker was opened never reached it, or a phone away from the PC's network: the app sent it in a form the account connection turned down.
- The Live Map's ATC zones didn't redraw after zooming while the map followed the aircraft.
- Dark lines across the dark maps at low zoom, where the map tiles meet.
- **The flight path lost its start on long flights**: the Live Map, the Flight Tracker, the account's relay and the companion app each kept only the latest stretch (the companion about ten minutes), so the beginning of the green line moved along behind the aircraft. Now the whole path stays, thinned as it grows.
- **Replays of some flights never reached the account**: one long alert in the flight was more than the account takes for a replay's marker, and the whole replay was refused, every time. Long text is now cut short on both sides, and with "upload replays" on, the replays of your last few flights that didn't go up are sent after the next flight.
- **A readback of the taxi to the gate was taken as a request for it**, and ATC said the whole taxi again, to every readback; the flight then never stopped at the gate. A request heard along with a readback now needs words that ask for something, and is never the request the instruction answered.
- **Stop the flight at the gate** waited forever when the taxi-in's readback was still owed: parked at the gate, the taxi is over.
- **A false "go around, traffic on the runway"**: for an aircraft rolling out well down the runway, or beside the pavement at a holding point. Now only for one on the runway in the stretch you'll land on, or stopped on it.
- **"Hold short, traffic landing" for an aircraft that had just taken off**: traffic climbing away low over the runway is a departure; you line up behind it.
- **After a go-around**: "go around" was said again (and "how do you read?") after landing; tower cleared the flight to land right after sending it around; a go-around started from higher up the final wasn't seen as one, so ATC treated the flight as landing for a quarter of an hour; "have a good flight" came with a go-around you'd be back from in minutes.
- **The copilot read back instructions long gone**, half an hour late or on another controller's frequency: a readback is now said only while the controller who gave it is still waiting for it.
- **A handoff to the next centre on the current centre's own frequency**: the pilot "contacted" the new centre, the old one answered, and the handoff went round and round. The next centre is always on another frequency.
- **An approach flown without the vectors was never handed to tower** and landed unannounced: established on the final, the flight is cleared for the approach and sent to tower.
- **A pushback with the tail asked for after a correction** ("tail left, actually, can we get a tail right?") got "unable", then tail left. The last tail asked for after "actually" or "sorry" is the one given, and "can we get a tail right?" at the gate is the pushback. The route's direction behind the stand is read further along when the lane behind the tail doesn't say left or right.
- A call cut off mid-word ("but sh-") got the model's answer to half a sentence; now "say again".
- Saying "negative" to an instruction and asking for something else left that instruction waiting, and ATC asked for it again a minute later.
- "Are we clear to land?" on final read as clear of the runway got "contact ground" in the air.
- "What runway can we expect?" and "what direction will we tail?" read as requests got "unable"; they're questions.
- The model's "question" for a call that asked nothing ("not sure") got the facts recited at it.
- "Expect runway 33R" asked for a readback and then "did you copy?": an expected runway is information, not a clearance.
- After landing, the destination's frequencies were named after the departure airport's controllers.

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
