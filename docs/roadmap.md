# LocalTC Product Roadmap

A staged roadmap for turning LocalTC into a broader flight-operations companion while preserving its strongest property: deterministic, testable ATC behavior with LLM assistance where it adds value.

This roadmap is informed by the current LocalTC architecture and the public feature set of SayIntentions.AI, especially its GSX ground-services integration, AI cabin crew, co-pilot/checklists, AI traffic, missions, companion tools, and live operational context.

## Product Direction

LocalTC should become three connected systems:

1. **ATC and traffic world**: controllers, traffic, procedures, weather, NOTAMs, terrain, and sequencing share one operational state.
2. **Aircraft and turnaround crew**: cockpit crew, cabin crew, ramp/ground crew, GSX, checklists, announcements, and turnaround timing share the same flight state.
3. **Flight operations companion**: EFB, charts, gates, scratchpad, missions, pilot profile, replay, statistics, and achievements work before, during, and after a flight.

The LLM should provide natural interpretation, dialogue, and personality. Deterministic systems must remain authoritative for simulator state, safety-critical clearances, timing, data provenance, and replay.

## Existing Foundations

LocalTC already provides useful building blocks:

- SimConnect owns live simulator polling, facility data, traffic snapshots, lifecycle events, COM tuning, and simulator commands.
- Typed events in `src/localtc/sim_api/events.py` are the correct cross-module boundary.
- `src/localtc/sim_bridge/definitions.py` can add simulator data, and `src/localtc/sim_api/commands.py` can add simulator actions.
- The ATC engine already handles IFR/VFR, taxi, runways, approaches, readbacks, traffic advisories, gates, emergencies, ATIS, and deterministic replay.
- `src/localtc/crew/` already contains a cockpit crew/PM model with facts, commands, aircraft profiles, and simulator mutations.
- Airport layouts, taxi graphs, parking spots, gates, approaches, and cached facility data already exist.
- Local Ollama, cloud models, and recorded LLM backends share an `LlmBackend` contract.
- Piper, Whisper, radio DSP, copilot, ATIS, chatter, recordings, the desktop UI, iOS companion, account server, logbook, and Wrapped provide extension points.

New features should use these boundaries instead of creating parallel state systems.

## Non-Negotiable Principles

- Keep flight-critical decisions deterministic and validated.
- Treat LLM output as a proposal until it passes domain validation.
- Every new event must be typed, timestamped, recordable, replayable, and diagnosable.
- Real-world data must include source, retrieval time, expiry/freshness, and offline behavior.
- Every external integration must have a capability check and a graceful no-integration mode.
- Features that can disrupt a flight must be independently toggleable.
- Preserve a useful experience when cloud services, GSX, charts, live data, or traffic injection are unavailable.
- Do not silently invent operational facts.
- Prefer one shared flight state over duplicated state in ATC, crew, UI, and companion code.
- Keep Windows/SimConnect-specific code behind the existing bridge boundary.

# Phase 0: Shared Operations Foundation

Build this before adding many surface features.

## Flight operations state

Create a versioned shared state model for:

- flight identity, aircraft, airline, route, phase, and schedule
- current airport, gate/stand, runway, taxi route, and parking state
- ATC clearances and traffic sequence
- weather and operational restrictions
- turnaround milestones
- ramp, cabin, cockpit, and dispatch status
- active incidents and mission objectives
- crew availability and workload

The ATC engine, cockpit crew, cabin crew, ramp services, companion app, recorder, and mission system should consume events from this model rather than querying each other directly.

## Event and replay contract

Add event types for:

- crew service requested/accepted/started/completed/failed
- gate assignment/request/change
- checklist started/item completed/paused
- cabin announcement or incident
- operational data update and expiry
- hold entry/exit and sequence position
- vector/speed restriction/weather diversion
- terrain constraint
- mission/objective progress
- achievement progress

Update the recording format with backward-compatible versioning. Replays should preserve enough state to reproduce decisions and final outcomes, even when live providers are unavailable later.

## Diagnostics

Every externally influenced action should expose:

- source: script, simulator, GSX, cloud, local model, user, or provider
- request and correlation ID
- current flight phase
- inputs/facts used
- result and failure reason
- fallback used
- latency and timestamp

# Phase 1: ATC Personality and Live Operational Context

## Persistent ATC personalities

Give each station a stable profile containing:

- region and phraseology
- tone: calm, formal, friendly, strict, hurried, dry, or conversational
- preferred acknowledgements, greetings, and sign-offs
- sentence rhythm and verbosity
- correction style
- patience and urgency
- workload level
- stable voice and delivery settings
- controlled vocabulary habits

Pass this profile to LLM prompts. It should affect wording, greetings, corrections, handoffs, and small talk without changing clearance meaning, numbers, runways, frequencies, taxi routes, or readback requirements.

Keep station behavior consistent within a flight and across replay. Use deterministic station/profile seeds plus limited controlled variation, not a new personality on every call.

## ATIS and data-source controls

Add a clear setting for:

- simulator-derived ATIS only
- real-world ATIS when available
- hybrid mode: real-world data first, simulator fallback

Show source and age to the user. Real-world ATIS must never silently override fresher simulator conditions when that would make the flight inconsistent.

## Real-world weather, NOTAM, TFR, and restriction provider

Add provider interfaces for:

- METAR and TAF
- ATIS where legally and technically available
- NOTAMs
- TFRs and airspace restrictions
- navaid and approach status
- runway and taxiway closures

Requirements:

- cache by airport/region
- show retrieval time and expiry
- rate-limit requests
- preserve a last-known snapshot for replay
- fall back to simulator data or deterministic local notices
- distinguish confirmed data from unavailable data
- avoid using data that is too old for a live flight

Current LocalTC notices are deterministic simulator/session notices, not a real NOTAM service. Keep both sources separate.

# Phase 2: Crew, Cabin, and Turnaround

## Cockpit-to-ground crew

Create a ramp/ground crew channel separate from ATC and cockpit intercom. Ground crew should have persistent identities and roles such as:

- ramp supervisor
- pushback driver
- marshaller
- gate agent
- fueler
- catering
- cleaning
- maintenance
- de-icing coordinator

They should communicate by voice and text, expose service status, and use the shared flight state.

## GSX integration

Support GSX Pro through a dedicated adapter, not by embedding GSX logic into ATC.

Integration goals:

- detect whether GSX is installed and available
- discover aircraft/airport/service capabilities
- request and monitor pushback
- jetway/stairs
- GPU
- fueling
- water and lavatory service
- catering and cleaning
- boarding/deboarding
- de-icing
- gate/stand selection and changes
- service completion and failure state

Use the safest available integration mechanism after researching GSX/MSFS support. Prefer a documented local API, process bridge, or supported command interface. Do not simulate completion when the external service has not completed.

Each request needs a correlation ID, timeout, cancellation path, duplicate protection, and a fallback when GSX is absent. GSX must never be required for ordinary LocalTC flights.

## Cockpit-to-cabin crew

Add flight-aware cabin crew with:

- named persistent personas
- voice selection and regional identity
- boarding and safety announcements
- takeoff, cruise, turbulence, descent, and landing announcements
- route, destination, weather, and timing context
- passenger-service conversation
- intercom requests
- cabin-service state
- optional custom airline announcements/audio

The cabin crew should use the same flight phase and turnaround state as ATC and the copilot.

## Cabin crisis

Add occasional, optional cabin incidents:

- medical event
- unruly passenger
- smoke/odor
- suspicious behavior
- lavatory or cabin-system problem
- passenger requiring assistance at the gate

Requirements:

- disabled by default or clearly configurable to off/rare/normal/frequent
- deterministic seeded scheduling so replay is reproducible
- strong cooldowns and no stacking of incidents
- never interrupt critical simulator operations without a clear user-facing reason
- captain chooses whether to continue, request assistance, divert, or escalate
- cabin crew can involve ATC, airport operations, and emergency services
- incident resolution produces a recorded outcome

Do not make incidents feel random. Use phase, route length, weather, aircraft type, turnaround state, and configured frequency as inputs.

## Turnaround and pushback pressure

Create a real turnaround timeline based on aircraft, airport, airline, service requirements, arrival time, scheduled departure, and available crew.

Track milestones such as:

- aircraft parked and doors available
- deboarding
- cabin cleaning
- catering
- fueling
- baggage/cargo
- crew arrival
- boarding
- doors closed
- pushback clearance
- de-icing
- ready for departure

Behavior should include:

- copilot reminders when the operation is late
- realistic crew unavailable/arrival windows when early
- service dependencies and blocking conditions
- schedule pressure without arbitrary random delays
- ability to request status from each crew group
- estimated completion times with a reason
- optional airline turnaround profile

Use airport, aircraft, service, and schedule data to determine timing. Seeded variation is acceptable, but it must be bounded and explainable.

# Phase 3: Advanced ATC and Traffic Operations

## Conditional clearances

Support conditional instructions such as:

- hold short or line up behind traffic
- follow traffic
- no-delay departure
- caution wake turbulence
- cleared after landing traffic vacates
- expedite or maintain speed for spacing
- runway crossing conditional on traffic

Every condition must be represented as structured state, not only an LLM sentence. ATC should monitor whether the condition is satisfied, expired, contradicted, or requires a new instruction.

## Dynamic routing

Add structured support for:

- vectors for descent
- speed restrictions
- weather deviations
- shortcuts/direct routing
- route amendments
- traffic-driven reroutes
- altitude changes based on sector and arrival flow

The LLM can explain or phrase a route decision, but route geometry and clearance validity remain deterministic.

## Terrain-aware vectoring

Add an elevation/terrain provider with:

- tile or local cache
- coordinate sampling
- terrain clearance calculations
- route corridor checks
- minimum safe altitude by region and phase
- mountainous-airspace warnings
- replayable terrain snapshot

ATC should keep aircraft high until descent is safe for the arrival, reject unsafe vectors, and choose terrain-safe headings/altitudes. Do not use the map display alone as a terrain model.

## Holds and sequencing

Model holds explicitly with:

- fix
- inbound course
- turn direction
- leg time or distance
- altitude
- entry type where relevant
- expected further clearance
- sequence position
- estimated delay

Use real operational triggers:

- arrival demand
- runway occupancy
- weather
- traffic spacing
- airport capacity
- missed approach traffic
- sector workload

ATC should tell the pilot why they are holding, where they sit in sequence, and when the expectation changes. Add visualization and readback support.

## Traffic control and injection

Treat this as a separate, experimental subsystem. Do not replace MSFS traffic logic.

Preferred order:

1. Continue observing native MSFS traffic.
2. Add stable identity, intent, route, and lifecycle tracking.
3. Build a synchronized control shadow for ATC sequencing.
4. Only attempt object control/reinjection where SimConnect supports it safely.
5. Preserve aircraft identity, model/livery, route, position, and continuity.

Any traffic reinjection setting must be:

- labeled EXPERIMENTAL
- optional and off by default
- capability-checked
- isolated from ordinary flights
- protected against duplicate objects and teleporting
- reversible on disable, reconnect, replay, or shutdown

Long-term traffic goals:

- commercial IFR schedules
- GA/VFR pattern traffic
- AI aircraft with individual state and radio behavior
- pushback and taxi sequencing
- runway and arrival sequencing
- gates matched to airline/aircraft
- traffic that shares the same ATC rulebook as the pilot

Use seasonal/real-world schedules only where licensing and data access permit. Provide an optional supplemental density mode for quiet airports rather than inventing traffic silently.

# Phase 4: EFB and Companion Operations

## Predeparture filing and EFB

Expand the companion into an operational EFB with:

- flight-plan creation and editing
- SimBrief import/export
- aircraft and performance data
- weight and balance where available
- fuel planning
- alternates
- departure/arrival procedures
- runway and gate information
- weather and NOTAM briefing
- ATIS source and age
- checklist access
- scratchpad
- flight documents

The EFB should be useful before connecting to MSFS and remain synchronized during the flight.

## ChartFox integration

ChartFox provides flight-simulation charts, including georeferenced charts, chart docking, mobile use, and flight-plan-based chart selection. Integrate it as an external chart source rather than copying chart data without permission.

Features:

- open the correct airport and chart based on flight phase
- departure, arrival, approach, airport diagram, and taxi charts
- chart dock in desktop and companion views
- current position overlay where permitted
- cached/offline references with source and date
- clear disclaimer that charts are for simulation, not real-world navigation

Research ChartFox authentication, links/API, rate limits, and terms before implementation.

## Gate Request Map

Build a companion gate map with:

- airport map and parking stands
- airline-specific gate filter
- aircraft-size compatibility
- current/local gate assignment
- occupied/blocked state
- arrival and departure gate view
- submit gate change request to Airport Ops
- request status and response
- integration with GSX when available

LocalTC already has parking/gate data and gate assignment. Reuse it, but separate simulated gate selection from actual GSX gate positioning.

## Checklists

Add user-editable, aircraft/profile-aware checklists:

- challenge/response
- read-only callouts
- confirmed cockpit commands
- optional automatic execution
- phase triggers
- pause/resume
- skipped-item reason
- completion state
- copilot voice
- replay support

Only execute simulator actions that are supported and explicitly confirmed. Keep aircraft profiles and command capabilities visible.

## Scratchpad

Provide a synchronized scratchpad available in:

- desktop app
- iOS companion
- replay view where appropriate

Support free text, structured quick notes, clearance extraction, pinning, timestamps, and flight-phase grouping. Do not treat scratchpad text as authoritative simulator state without confirmation.

## Airport Operations

Add an Airport Ops role that can handle:

- gate assignment and changes
- parking restrictions
- ramp closures
- de-icing queue
- fuel/catering/cleaning status
- baggage and boarding status
- late departure coordination
- maintenance/dispatch notes
- airport-specific instructions

This is the natural companion layer between ATC, GSX, cabin crew, and turnaround timing.

# Phase 5: Missions, Profiles, and Progression

## Mission and flight idea explorer

Create a searchable mission catalog based on:

- airports visited and not yet visited
- routes flown
- airlines and alliances
- aircraft type and time
- origin/destination
- distance and duration
- scenery/terrain
- weather or season
- VFR/IFR
- historical or real-world routes
- user goals

Mission types can include:

- scheduled airline leg
- cargo
- ferry
- VIP transport
- medevac
- search and rescue
- aerial firefighting
- training
- diversion/emergency practice
- regional exploration

Missions should create structured objectives, not just a text description. Objectives must be evaluable from flight events and replay.

## Pilot profile

Add a local-first pilot profile containing:

- display name and callsign preferences
- aircraft fleet
- favorite airlines/regions
- preferred ATC personality and crew
- routes and airports
- flight history
- replays and share links
- readback/communication trends
- phase completion statistics
- incident and diversion history
- privacy and synchronization controls

Use the existing logbook and server account model, but do not upload audio by default.

## Achievements

Build an idempotent achievement system evaluated from structured events, for example:

- first flight
- first IFR clearance
- first successful readback
- completed flight plan
- destination gate arrival
- safe diversion
- correct hold execution
- successful missed approach
- completed turnaround
- visited airport/region
- flown route/airline/aircraft milestones
- handled a cabin incident
- completed a mission
- streaks and personal bests

Achievements should explain exactly what was met, support replay evaluation where possible, and avoid rewarding unsafe behavior.

# Phase 6: Voice and Language Expansion

## Voice provider abstraction

Keep Piper as the reliable local default, but add a provider abstraction for optional alternate TTS sources.

Possible provider categories:

- local Piper voices
- additional local engines
- cloud TTS providers
- regional/accent voice libraries
- user-provided compatible voices

Requirements:

- provider availability check
- per-role/per-station voice mapping
- cache generated audio where allowed
- latency and quota reporting
- offline fallback
- privacy disclosure before sending text externally
- no provider lock-in
- stable replay audio references

Add regional voice profiles for ATC, cabin, cockpit, and ground crew. Voice identity should match the persona's region and wording style without stereotyping or making accents cartoonish.

## Audio behavior

Add per-persona delivery settings:

- pace
- pause/rhythm
- radio compression
- static level
- urgency
- interruption/barge-in
- headset/intercom/radio route

Keep spoken-number and clearance formatting centralized so alternate TTS providers do not change aviation meaning.

# Phase 7: Quality, Privacy, and Release Hardening

## Integration test matrix

Test at minimum:

- no cloud/no GSX/no real-world data
- local LLM only
- cloud LLM with local fallback
- replay with all external services unavailable
- GSX absent, disconnected, delayed, and partially supported
- traffic injection disabled and enabled
- duplicate/reconnect/shutdown traffic cases
- real-world data stale or unavailable
- cabin crisis disabled and enabled
- gate/turnaround timing with early and late arrivals
- chart source unavailable
- companion LAN and relay
- Windows live SimConnect and macOS replay development mode

## Scenario tests

Add deterministic scenarios for:

- busy airport sequencing and holds
- conditional takeoff/landing clearances
- weather diversion
- terrain-safe descent
- TFR/NOTAM runway or navaid closure
- GSX turnaround
- crew unavailable due to early arrival
- late departure pressure
- cabin medical event and diversion decision
- gate request/change
- checklist pause and recovery
- mission objective completion
- achievements from replay

## Safety and privacy

Document clearly:

- real-world data is informational and may be delayed
- charts are for flight simulation, not real-world navigation
- experimental traffic control may be unstable
- cloud LLM/TTS sends selected flight data externally
- camera/presence detection, if added, is optional and local-first
- audio retention and account upload behavior
- user controls for disabling incidents, cloud services, traffic, and data sharing

# Suggested Delivery Order

1. Shared flight operations state, event provenance, replay versioning, and diagnostics.
2. ATC personality profiles and improved LLM response ownership.
3. Real-world data provider abstraction plus toggleable ATIS/NOTAM/weather sources.
4. Turnaround timeline, airport operations, cockpit-to-ground, and cabin crew foundations.
5. GSX adapter with pushback and service status, then full turnaround services.
6. Checklists, EFB, scratchpad, gate request map, and ChartFox links/integration.
7. Conditional clearances, dynamic routing, holds, sequencing, and terrain safety.
8. Experimental traffic control shadow, then carefully scoped reinjection.
9. Missions, pilot profiles, route explorer, statistics, and achievements.
10. Voice/provider expansion, regional voices, and broader companion polish.

# Research and Integration References

- SayIntentions.AI ATC: https://sayintentions.ai/atc
- SayIntentions.AI ground services/GSX: https://sayintentions.ai/ground-services
- SayIntentions.AI traffic injection: https://sayintentions.ai/traffic-injection
- SayIntentions.AI co-pilot/checklists: https://sayintentions.ai/co-pilot
- SayIntentions.AI cabin crew/crisis: https://sayintentions.ai/cabin-crew
- SayIntentions.AI missions: https://sayintentions.ai/skyops
- SayIntentions.AI product overview: https://sayintentions.ai/premium
- ChartFox: https://chartfox.org/
- GSX product information: https://www.fsdreamteam.com/products_gsx.html
- LocalTC architecture: `docs/architecture.md`
- LocalTC companion protocol: `docs/companion-protocol.md`
- LocalTC replay format: `docs/replay-format.md`

SayIntentions feature descriptions above are public product claims, not a statement of its private implementation. Verify API access, licensing, compatibility, and data rights before committing to any external integration.
