# Changelog

Every release of LocalTC, newest first. The app shows a version's section when an update is available,
and the website's changelog page is built from this file. Versions follow [semantic versioning](https://semver.org):
a new minor version adds features, a patch fixes them.

## [0.5.0] - Upcoming

### Added
- **LocalTC's traffic** (Quick Settings → ATC → LocalTC's traffic; off by default), in place of the experimental traffic control. The real flights around you, live from free ADS-B sources (adsb.lol, adsb.fi, with routes from adsbdb.com: no account, no key), fly in the sim with their own airline's FSLTL model when FSLTL is installed (else FSLTL's unpainted one, else the sim's own), under their own callsigns, moved smoothly every frame with their gear and lights as a crew has them. The gates of the airport you're at or going to fill as busy as it is at that hour (nearly full overnight, a hub busier than a small field), with the airlines that fly there; a real departure coming to life at a gate takes the place of the one parked there, and the gate ATC gives you is kept clear. They answer to ATC: with you on the runway, a real arrival on short final is sent around and a real departure about to go onto it holds short, then goes once you're done, and tower says so on the frequency. An arrival that drops out of the receivers' sight low on final is landed rather than left hanging. Turn MSFS's own air traffic off while it's on (LocalTC tells you if it's still there); everything it made is taken out when it's turned off or the flight ends.
- **Nothing to install for SimConnect.** LocalTC now talks to MSFS itself (Settings → Sim → Built-in SimConnect, on by default), over the sim's own local pipe or the ports in its SimConnect.xml, so the MSFS SDK isn't needed any more. Everything works through it: ATC, the copilot's hands, airport data and traffic. Turn it off to go through the SDK's SimConnect.dll as before.
- **More natural voices, optional** (Quick Settings → ATC voice → Voices). **Kokoro** runs on your PC like Piper, and sounds more like a person (a 337 MB download; it takes longer to speak, a second or two before a long clearance). **Azure AI Speech** is Microsoft's neural voices in the cloud, with your own Speech resource's key (the free tier gives 500,000 characters a month, about 50-60 hours of flying); while it's chosen, the words ATC and the copilot say go to Microsoft to be spoken, nothing else. Off unless chosen; Piper stays the default. If the chosen voice can't speak a line (no network, out of allowance, too slow, a bad answer), Kokoro (when on) and then Piper do, and with none ATC carries on as text. The status shows each voice's speed, failures and Azure's characters this month, and a Test button speaks the same line with each.
- **Stations speak their region's English** where the voice has it: British at Heathrow, Australian in Sydney, Irish in Dublin, Indian in Delhi (Azure; Kokoro has American and British).
- **Each station is one person whichever voice speaks**: the same voice all flight and in its replay, a different one for every station while there are enough, and the same sex when another voice stands in.
- **ATC works in shifts**: each station keeps the same controller, voice and manner all flight (and in its replay), and when you fly again 5 hours or more after your last flight ended, other people are on: other personalities and other voices at every station.
- **Checks against the sim** (`localtc debug aircraft`, `hands` and `traffic`): the aircraft's copilot profile and its MSFS 2024 input events; every control the copilot moves, read back and put back as it was; the AI traffic as LocalTC sees it, and an aircraft created and removed. Each writes a report.
- **A close button on the Wrapped banner** in the logbook, which also goes away for good once Wrapped is opened.
- **"Where are we?"** is answered from the aircraft's position and a list of towns and cities ("about 20 miles north-northeast of Charlotte, North Carolina"), never guessed.
- **The FSLabs Airbus works with the copilot's hands**: it clicks the FSLabs' own cockpit controls (the lights, the flap and gear levers, the FCU's speed, heading, altitude and V/S knobs, the first officer's baro, RMP 1 and the transponder) a step at a time until the aircraft's own variables show the position, and reads the cockpit by those variables, since the sim's flap, light and FCU readings don't follow the FSLabs.
- **The copilot works its own side only.** The parking brake, the speedbrake lever (spoilers), the thrust and engaging the autopilot are yours: asked for one, it says "Your side." and leaves it; after landing it leaves the spoilers to you with a word.
- **A gate taken on the way in**: the sim parks aircraft at the gates only as you get close, so ground checks the gate again on the taxi in and sends you to another, saying the first is occupied.
- **The copilot reads what you say before doing anything.** Each thing said on the intercom is a command, a question, a report, a correction, a "don't", an acknowledgement, a yes or no, a radio call on the wrong key, chat, or nothing usable, and only commands and corrections are ever done. "Did you set the gear?" and "is the autopilot on?" are answered from what the aircraft shows; "I'll put flaps two" and "gear down, three green" are acknowledged, not done; "gear down?" is answered and offered ("That sounded like a question. Gear's up. Want gear down?"); speech-to-text going round in circles ("3,000. 3,000. 3,000 ...") gets no answer.
- **Every utterance is kept** (yours on the intercom and the radio, the copilot's, ATC's): who said it to whom, speech-to-text's confidence and the audio, when, and the flight's state at that moment, with what was made of it.
- **It asks when it isn't sure.** A spoken number nothing from ATC backs up is read back before it's set ("Confirm heading 240?"; one that matches the clearance, the code, the handoff or the ATIS is set at once, and a typed one as written); the same words handed over twice are done once. A command heard clearly is done; heard only fairly well it's said back first ("Heading 240?"), heard badly "Did you say heading 240?". A number that disagrees with ATC is asked about ("The ATIS has QNH 1012. Set 1020?"), and what's at high stake (the autopilot off near the ground, an emergency squawk, a go-around heard unclearly, declaring an emergency) always waits for your yes. Several commands in one breath are each done ("flaps two and QNH 1013": the QNH was dropped before).
- **A yes counts only for the question just asked**: within ten seconds, and only while what it depended on still stands. Asked before ATC changed the clearance, or before the phase changed, the copilot says so and asks again; a new question replaces the old one; "no, flaps two" replaces "Flaps 1?"; "never mind" drops it; a yes with nothing asked does nothing.
- **"Say again"** repeats what the copilot last said; **"don't touch the flaps"** keeps its own hands off them; **"no, don't go around"** stops it reading the go-around back on the radio, and it tells you tower is expecting it.
- **The radio and intercom copilots work from one flight deck**: anything either is about to say or do is checked again the moment before. Your own call on the radio, tuning the radio yourself, a new instruction from ATC, a go-around, a phase change, a new flight or the sim reconnecting, and changing the copilot's mode each drop what no longer fits; the same words aren't said twice in a row, nothing goes out while you hold the push-to-talk, and what ATC is waiting for goes first (a readback, then a check-in, a call, a request). Every decision (heard, asked, done, dropped and why) is recorded for the replay.
- **Calls on request**: "request pushback", "get us taxi", "ask for the clearance", "tell tower we're ready" (the radio copilot makes them in assist or full mode; with the radios yours, it says who to call), "go around" (it tells tower when it has the radios) and "declare an emergency" (after your yes).
- **The autobrake is the copilot's to set**: "autobrake max / low / medium / off"; "turn on the autobrake" before takeoff asks "Autobrake max for takeoff?".
- **Back from a long pause** (two minutes or more), the copilot says where things stand: altitude and heading, the clearance, who you're with, and what ATC said meanwhile.
- **Head, tail and crosswind on the landing runway** are worked out from the wind and the runway, and the copilot's model can't give other numbers (it said 12 knots of crosswind with the wind straight down the runway).
- **The copilot is one person**: its model is given a fixed first officer (a name that goes with its voice, a base, years and types) and answers about itself the same way all flight.
- **When the radio copilot's frequency change doesn't take, or a check-in gets no answer**, you're told on the intercom; with no answer twice it goes back to the frequency it came from.
- **The destination's real weather**: LocalTC fetches the departure's and the destination's METAR (aviationweather.gov's free public service; only the airport codes are sent) every half hour. "What's the weather at the destination?" (or "at KJLN", "at Joplin", "at the airport" once you're on your way) is answered from it ("Joplin Regional wind 120 at 3, visibility 10, temperature 18, altimeter 30.01"), and the runway and approach to expect are chosen from it. Close in, the sim's own weather there takes over. Offline, there's no report, and ATC says the weather isn't available; a far airport's runway is then the one for calm wind.
- **Where an airport has no approach controller, the centre works the approach**: vectors onto the final, the descent and speed, "maintain 4,000 until established on the localizer, cleared ILS runway 13 approach", then "contact Joplin Tower".
- **"Requesting engine startup"** from a stand you taxi out of (a GA ramp) gets "start up approved, advise ready to taxi", with no push.
- **Departure turns you on course**: off on runway heading with no SID, radar contact comes with "proceed direct" the first fix of your route (or your destination when the route has none).
- **GA flights taxi to the GA ramp** ("taxi to the general aviation ramp via C, D, B"), the scenery's GA parking, never an airliner's gate.
- **"Requesting radio to the tower"**, "request frequency change to tower" and the like are asked for a frequency change; from a centre working the approach, it comes with the approach clearance.
- **ATIS source** (Quick Settings → ATC): **Hybrid** (the default) gives the airport's real ATIS where it has one (US airports, the FAA's digital ATIS), else LocalTC's ATIS from the airport's real METAR, else from the simulator; **Real-world ATIS** gives the real one where there is one and the simulator's otherwise; **Simulator only** downloads nothing. The real ATIS keeps its letter and runways in use, spoken in LocalTC's words. Real-world data never overrides what the sim is giving you: once you're at or near an airport, the sim's own weather there wins, and the ATIS says when the real report differs; a real runway with a tailwind in the sim's wind isn't used. Every ATIS shows where it's from and how old it is (in the radio log, and under the airport on the ATC tab).
- **A welcome**, once, the first time LocalTC starts (existing installs too): what to expect, and the recommended language model, voices and speech-to-text.
- **"Stop immediately"**: on the takeoff roll, still below 80 knots, with an aircraft about to be on the runway ahead, tower stops you.

### Changed
- **Numbers are put into words before any voice speaks them** (frequencies digit by digit, "niner", runways, flight levels, callsigns and taxiway letters in the phonetic alphabet), the same for every voice, so none reads "119.2" or "FL350" its own way.
- **A voice failing never stops ATC**: a line no voice could speak stays text, and the next is spoken.
- **The copilot talks less, and only when it should.** No more prompts for the briefings and the clearance after you've said "later" (or "not now", "quiet"): its suggestions wait a quarter of an hour, while the callouts and warnings carry on. No ATIS read out unasked (it still sets its side of the altimeter), no "they're waiting for a readback", no "autopilot's available", no lights asked about one by one. A "check", "roger" or "yep" gets no reply.
- **No checklists until there are real ones**: the copilot doesn't offer or read its made-up checklists; asked for one, it says so.
- **The copilot only says what it knows.** Its language model gets the aircraft's position, the autopilot, the localizer and glideslope, the autobrake and what it can't see (the weather radar, outside), and every reply is checked against them: one claiming the autoland is armed, that we're on the glideslope, that the spoilers or autobrake are set, naming a place, or saying "we'll be fine" without the facts for it isn't said ("Can't tell that from here." instead). What you tell it you did ("I've deployed the speedbrakes") is acknowledged, never taken as a request, and a command it reads must name the thing ("auto brake off" is no longer the autopilot).
- **Only the copilot's own talk is put in its words**: callouts and warnings keep theirs (reworded, "moderate turbulence" had become "turbulence ahead").
- **The copilot's fuel calls**: the fuel against the plan's block is checked when the engines start (an add-on's tanks are often filled after the flight loads), and said only when it's off; the cruise burn is measured over level flight only and the descent counted at its own burn, so a level-off no longer projects "we'd land with 0".
- **A switch that doesn't take is said once**; if none of the copilot's own ever take, it stops reaching for them and says so. A single control that never moves for it, with the aircraft's reading never moving either (an A350's autopilot knobs), is left to you after two tries ("My altitude inputs aren't reaching this aircraft. I'll call them; you set them."), and its callouts no longer say "set" for what it didn't set. A reading that has never moved in an aircraft without a profile of its own isn't believed: "flaps up" is sent, not answered "flaps are already up" (the A350's flaps read up at flaps 1).
- **In full mode the copilot waits for the crew at the gate**: after the clearance it asks for push and start once the beacon is on (or you ask), and for taxi once the taxi light is on (or you ask), instead of calling "ready to taxi" from the gate. A pushback or taxi you started yourself counts as ready. Its check-in says "level" when a clearance higher or lower has gone unflown for five minutes (five hours of "35,000 climbing 37,000" at 35,000), and the copilot says when you're still level below (or above) the cleared altitude.
- **Step climbs are always your call**: the plan's step climb is suggested ("Want me to ask?"), never asked for by itself (it asked for the plan's FL370 with the flight meant to stay at FL350); a no and none are suggested again that flight.
- **The copilot's fuel predictions** start only in the last hour before the destination (from a long way out they were guesses: "we'd land with 0 pounds"), stop when you say they're off ("your fuel prediction is off"), and the fuel against the plan's block is checked once the fuel has stopped going up after engine start (a refuel still running read "120,800 pounds under the plan's block").
- **No engine anti-ice prompt at -40 °C and colder.**
- **A copilot failure never stops ATC**: an error in either copilot is logged and recorded, and the next event carries on.
- **Reminders that no longer apply aren't said**: a heading or speed reminder after ATC gave a new one, "gear up" after you called it; the assigned speed isn't nagged once cleared for the approach. Turbulence is called at most every ten minutes, and "smooth again" after two calm minutes. A step climb is asked for only once checked in with the new controller. The taxi speed call starts at 5 knots over the limit.
- **The app clears the last flight** 10 minutes after it ends (the map, the aircraft, the radio log and the flight's details), and opens clean: a flight plan that's been flown isn't loaded again.
- **Replays follow without the map going grey**: Follow moves the map only when the aircraft nears the edge and zooms in animated half steps, so the map's tiles stay loaded (a new zoom every frame had them reloading endlessly, worst on a phone); and it keeps your own zoom when you zoom in or out while following (the mouse wheel zooms on the aircraft then).

### Fixed
- **Yoke and joystick buttons for push-to-talk and the intercom work while MSFS has focus.** MSFS 2024 accepts a button bound through SimConnect and then never reports it, so LocalTC now reads the buttons from Windows itself (and the sim still gets them). Detect in Settings reads them the same way: press it and it's set.
- **The copilot's hands in MSFS 2024 airliners**: the aircraft's list of cockpit controls (its input events) was thrown away because the sim sends a little more than it counts, so no aircraft could be worked through them. The stock A320neo's and A350's FCU (heading, speed, altitude) ignores the autopilot key events: the copilot now turns the FCU's knobs until the window shows the value, so what it sets is what you see. The A350 has a profile of its own (flap speeds, its landing lights). The airliners it's been checked with, control by control, are listed in the README (the stock 747-8i, 787-10 and the HorizonSim 787-9 needed nothing).
- **The FlyByWire A380 has a copilot profile**: its FCU through FlyByWire's own events (heading, speed, altitude), its flap lever and parking brake. Spoilers and COM standby are left to you.
- **The Headwind A330-900 has a copilot profile**: its FCU through FlyByWire's events (the altitude read where its FCU keeps it), its flap lever and parking brake. Spoilers are left to you.
- **The stock 737 MAX's landing, nav and strobe lights** are switched by the copilot (they ignored it).
- **The Fenix A319/A320/A321 has a copilot profile** (it used to get the stock A320neo's, whose events don't reach it): its lights, flap lever, and the FCU's speed and altitude through the Fenix's own switches. Its heading, V/S, autopilot, spoilers and COM standby can't be set from outside yet, and the copilot says they're yours from the start instead of trying.
- **The app's window shows LocalTC's icon** in its title bar and on the taskbar, as an app of its own, not Python's.
- **Vectors from far out**: an arrival 30 miles out at 12,000 feet was judged too high from the join point alone and turned away on a downwind, heading north from an airport to its south. The miles before the join count now (a light aircraft still gets no steep descent).
- **Runways on the taxi in** are crossed with a clearance, as on the way out, and the taxi-in route replaces the one out (the copilot quoted the departure airport's taxiways at the arrival).
- "Vectors" heard as "factors", and "taxi via" as "taxiviate".
- **A shared flight's whole replay** shows the map, the aircraft pointing the way it flies, and the calls and phases along the timeline in their places.
- **A replay's map fills its whole area** when the player opens or the window changes size, instead of a corner of it.
- **Readbacks**: "125 decimal to" is 125.2 (and "point for" is 4); a taxiway speech-to-text turned into a word ("trile" for Charlie) no longer cuts the route short and fails a good readback; a taxiway's number ("delta eight") is never taken for the runway to hold short of. A second request for a missing item asks for that item ("I still need the readback of frequency 125.2"), not "a full readback".
- **Taking back a request**: "can we abort our taxi request, we're not quite ready" (abort, withdraw and belay join cancel and disregard) cancels the taxi clearance, and ground says "advise when ready" instead of giving it again and asking "did you copy?".
- **"Request back and start"** (speech-to-text's "push back and start") is the pushback request.
- **A centre on a frequency an airport also uses**: after the handoff to Gander Oceanic on 120.4, LocalTC took the frequency for Heathrow Director's, 1,900 miles away, and nobody answered the check-in.
- **No handing back and forth along a boundary**: a route along two centres' boundary was handed Shannon, Scottish, Shannon, Scottish in eight minutes; going back to the centre just left now takes ten minutes in its airspace.
- **The landing order**: tower clears you to land only with nobody still ahead on the same final; traffic that appears ahead after the clearance (the sim adds AI aircraft late) gets "number two, follow the A321 on a two mile final, continue", and the clearance comes again once the runway's free. Other traffic is cleared for the runway it's lined up with, not the one in use.
- **The taxi in across a runway** says "hold short runway 09R" in the clearance, and the clearance to cross comes as you reach it, not as you leave the runway you landed on (the same runway, crossed further along).
- **A gate taken on the way in** is swapped for the free one nearest to it, with an apology first ("sorry, Gate 403 is occupied, taxi to Gate 402 via W, T"), not one across the airfield.
- **The squawk is different every flight.** It came from the callsign alone, so flying as the same callsign gave the same code each time. It's the same for the whole flight once given.
- **The runway in use follows the wind**: it changed only once the tailwind on it was too strong, so a wind swinging from 070 to 110 at 16 knots left runway 05 in use with 14 knots across it and 13 nearly into the wind. With a crosswind of 12 knots or more and another runway nearly into the wind, the runway changes, with a new ATIS.
- **Taxiway names**: where the scenery leaves most taxiways unnamed or has old names, the real ones (OpenStreetMap's) are used: Des Moines's taxiway P had been called "A" and its D "B". Taxiways the scenery spells out ("Charlie", "Delta3", "Charlie1vDelta") are said as letters ("C", "D3", "C1"): ATC had said "vacate right Charlie1vDelta" and the copilot read every name back letter by letter.
- **No level-off waiting for a handoff**: a flight given 15,000 sat level there for four minutes, departure leaving the next climb to the centre and the centre waiting for the handoff. It's handed over while still climbing.
- **A far airport's runway isn't chosen from the wind up high**: with nothing known of Joplin's weather, "expect RNAV runway 31" came from the wind at FL300.
- **The weather asked for in the cruise** is the destination's, not the departure's 300 miles behind, and never the wind where the aircraft is.
- **The first call to ground after landing** ("Ground, good evening, on Charlie") gets the taxi in; it got "copy that". "Requesting taxi" is understood.
- **The taxi in asked for again** after reading it back is said again without asking for another readback ("how do you read?" followed); "loud and clear" to ATC's "how do you read?" is taken as the answer, not answered "say again".
- **No runway crossing left from the departure**: just off Joplin's runway 13, ground cleared the flight across "runway 13", Des Moines's 13/31 crossed on the way out.
- **"Direct" to a place with a phonetic-alphabet word in its name** ("direct Quebec") is read back right; it was taken for the letter Q.
- **No takeoff clearance with traffic landing across your runway**: tower waits for an arrival close in on a runway that crosses yours (San Francisco's 28s and 01s), and for an aircraft rolling along it onto yours.
- **No go-around for landed traffic well down the runway**: an aircraft ahead rolling on is judged where it will be as you cross the threshold (about 5,000 ft down is enough), not where it is two miles out.
- **The runway to cross is named by the end on your side** ("hold short runway 01L", not 19R), and a readback naming either end is right. The clearance to cross comes about 90 m from the hold line, not 200.
- **Departure climbs in big steps**: 6,000 to 12,000 ft at a time up to its top, not 3,000.
- **Controllers are mostly men**, as in the job: about a fifth of stations are women (by region), and other pilots on the frequency about one in twenty. It was half and half.
- **The departure frequency is listed as DEP** on the ATC tab where the sim has it as an approach frequency.
- **Readbacks**: "heading 3500" is 350; "ILS young Yankee for the runway 26L" is the ILS Y 26L; "an I-less for 26 left" is an ILS; "we're probably going to do another go around ... can we get 26 left" asks for 26L instead of going around; "heading 230 as well as the other stuff" isn't answered "unable". The model can't ask for a readback of something ATC didn't give ("read back the altitude" to an approach).
- **No takeoff clearance said again once airborne**, and no "Harry Reid International Airport airport".
- **The copilot**: turbulence only when the aircraft is jolted back and forth, not when it pitches over at a level-off; "shut up", "chill out" and "I know" keep it to safety calls for a quarter of an hour; no "speed, we're assigned 180" while slowing down to it; restrictions said plainly ("We're high for COKTL: between 16,000 and FL190."); no ATIS read out once ATC has given the approach, nor after a go-around, and its letter said as a word; a remark like "time to start up our engines" whose model reply was turned away gets "Copy.", not "Say again?".

## [0.4.0] - 2026-09-28

### Added
- **A cloud language model, recommended for quality** (Quick Settings → Cloud language model; off by default). A large model understands your calls and words ATC's and the copilot's replies far better than one small enough to run beside the sim, and it's given the whole flight: the route, every clearance, both ATIS, where the aircraft is and what was said on the radio, where the model on your PC gets only the essentials. Mistral goes first (the most generous free limits) and Pollinations, which needs no key but has the tightest allowance, last; with a free key, Groq, Google AI Studio (Gemini), Cloudflare Workers AI, NVIDIA NIM and SiliconFlow. Each service's own model list is read when a flight starts, so a renamed or retired model is skipped. A model that's rate limited, out of allowance, slow or down steps aside for a while (a model your plan doesn't include, for the flight) and the next answers (each try gets at most half the wait, so one slow service never costs the call); a refused key or a model that's gone is skipped for the flight; and, if you turn it on, the model on your PC answers when every service fails. Every answer goes through the same checks as the local model's. Keys are kept in Windows Credential Manager; each service has a Test button. While it's on, your words and the flight's details go to the service answering (see the privacy page).
- **The copilot on the cloud model too** (Quick Settings → Cloud language model → "The copilot uses it too"; on with the cloud): its own connection to the services, so its questions never hold up ATC's, and the whole flight as context like ATC's. Off: the copilot uses the model on this PC.
- **Traffic control, EXPERIMENTAL** (Quick Settings → ATC → Traffic control; off by default). SimConnect can't remove or take over MSFS's own Live Traffic, so it's never moved. *Shadow* follows every aircraft around you (who it is, what it's doing) and notes anything odd: a teleport, a callsign changing, two with the same callsign, one vanishing. *Reinject* also puts back an aircraft MSFS drops nearby: the same model and livery (FSLTL's model when FSLTL is installed), parked where it was, or flying on to one of your flight's airports with the runway from LocalTC's ATIS in its flight plan. Never one that was taxiing, taking off or landing, never twice, never more than eight; a copy is taken out if MSFS's own comes back, and all of them when it's turned off, the sim disconnects or the flight ends. A replay only watches. The status shows what's shadowed, put back and dropped.
- **Controllers with personalities** (Quick Settings → ATC; on by default): each station has its own controller, the same every flight and in replays: calm, formal, friendly, strict, hurried, dry or conversational. It shows in their greetings ("good evening", "hello", or nothing), acknowledgements ("roger", "copy", "roger, thanks"), how they ask for a repeat, how firm a second correction gets ("negative, I say again ..."), their sign-offs, their pace and cadence on the voice, and how the language model words their replies (it's told who's talking and how busy the frequency is). A busy frequency makes any of them shorter and quicker. The instruction itself (every value, runway, route, frequency and readback) is never touched, and the model's words are checked as before.
- **Settings and Quick Settings apart**: the gear opens Settings, for what isn't about flying (the account, appearance, the sim connection, updates, support, SimBrief, developer mode); the Quick Settings tab keeps what changes how a flight goes (the models, the cloud model, push-to-talk, the voices, the copilot, ATC).
- **The whole replay on a shared flight's page**: when you share a flight, pick the short replay that plays by itself, the whole replay in the logbook's player (scrub, follow, every call), or none; in the app, on the website and on the phone.
- **Replays follow like the phone's map**: Follow keeps the aircraft in view and zooms with it, close on the ground and wider up high.
- **The copilot's language model works like ATC's** (Quick Settings → Copilot → Script or model): fully LLM, mostly LLM, semi, fully scripted, or off; automatic is mostly LLM with the cloud model and fully scripted with the one on this PC. In the LLM modes it understands what you say in your own words, remembers the conversation and what's happened on the flight deck (engines started, the pushback, ATC's last calls), answers like a first officer ("Check.", not the cockpit recited back), says it can't do what isn't on its list instead of doing something else, and puts its own routine calls in its own words (every number and name kept; safety calls stay as they are).
- **Checklists are challenge and response**: the copilot asks each of your items and waits for your answer, holding the checklist on one the aircraft doesn't show yet, and answers (and sets) its own side itself. Asked once more if there's no answer.
- **"Beacon, check", "set", "checked"** are taken as a yes to what the copilot just asked.
- **The copilot moves controls the way MSFS 2024 aircraft do**: through the aircraft's input events where its profile names them, and every aircraft's list of them goes in the log (for its profile).
- **The copilot's settings** (Quick Settings → Copilot): a master switch for the intercom copilot (off: only the radio copilot), and its own language model section like ATC's: what it uses the model for, which model (the cloud or this PC), whether its answers may go beyond what it knows from the sim and ATC, and how long it waits.
- **Find a yoke or joystick button by pressing it**: for push-to-talk and the intercom, pick the device from the list of connected controllers, press Detect, then the button; the MSFS name (joystick:0:button:3) is filled in.
- **The cloud services fold away** in Quick Settings: one line each until opened.
- **An account, suggested once**: after your third flight, if you have no account, the app asks once whether you'd like one and what it adds (the logbook online, the companion app, the Flight Tracker, Wrapped, replays, support). Either answer, it never asks again.
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
- **Themes** for the app (Settings (the gear) → Appearance: Radio panel, Midnight blue, Black OLED, Amber avionics, Slate, Daylight) and the companion (Settings → Appearance, the same, or following the phone).
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
- Replays across the date line (New York to Tokyo): the track, the route and the airports are drawn the short way, in the logbook's player, the shared page's short replay and the phone's replay, instead of a line across the whole world.
- The logbook's map of every flight puts each airport on the same side of the date line as its routes, and leaves out a name that would sit on another until you zoom in.
- The copilot no longer shouts "Speed, speed!" in the climb out of an A320: the sim's clean stall speed for it is its green dot, and the warning now comes from the sim's own stall warning, backed up only well below a plausible stall speed.
- Fuel: no "below final reserve" or "45 minutes left" just after takeoff; the copilot projects the landing fuel from the burn measured over minutes of level cruise, against the plan's reserve.
- The copilot settles in before it talks: the greeting, the ATIS, the briefing and clearance prompts come one at a time with room between them, never right after something on the radio, and a new ATIS letter is mentioned only when the runway, approach or altimeter changed. "Waiting for a readback" waits long enough to write a clearance down; checklist prompts wait until after it.
- The copilot doesn't set the standby frequency on the pilot's radio, sets only its own altimeter, says "approaching FL280" instead of "one thousand to go", doesn't call altitude deviations while descending via an arrival, and says a short word at the gate instead of reading out statistics.
- The radio copilot checks in "descending via the ANJLL4" (not "climbing" to an old altitude), and doesn't make a call the pilot already made.
- ATC: the model can't turn "tail right" into "tailwind right" (phrases like that, "hold short" and "radar contact" are kept word for word), a number said digit by digit counts as said, "Los Angeles altimeter", not "Los altimeter", and runways in a model's words are said "two five right".
- ATC answers a callsign one digit off ("Frontier 1649" for 1629) when no other aircraft around flies that number, instead of "say again your callsign".
- Ground checks a gate you ask for ("can we get gate 148 instead?") and the one it gave: an occupied one is said so and another given; "there's an aircraft at our gate" gets a new one.
- Tower sends you off the runway toward your gate (or the terminal), not across to the far side.
- Departure hands you to centre while still climbing to its top altitude, instead of leaving you level at 17,000 waiting.
- Approach keeps you on the STAR you were cleared to descend via instead of a new altitude and a vector.
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
