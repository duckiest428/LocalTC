# The turnaround schedule, cabin incidents, and the ATC's newer conditions, routing and terrain

## The turnaround (`src/localtc/turnaround_plan.py`)

Twelve milestones, each with a duration and the reason for it:

1. Parked, doors available
2. Deboarding
3. Cabin cleaning
4. Catering
5. Fueling
6. Baggage and cargo
7. Crew arrival
8. Boarding
9. Doors closed
10. Pushback clearance
11. De-icing
12. Ready for departure

`DEPENDS` says what waits on what. Boarding waits for the cleaning, the catering and the crew. The doors wait for boarding, the bags and the fuel.

| Milestone | Worked out from |
|---|---|
| Boarding, deboarding | The passengers, the doors in use (two on a wide body) and the profile's rate a door |
| Cleaning | The seats |
| Catering | Short or long haul, from the plan's block times |
| Fueling | The plan's block fuel less what's in the tanks, at a wide or narrow body's truck rate |
| Baggage | About 1.1 bags a passenger, on one or two belts |

The airport's size (its gate count) adds up to 10 %. The only variation is `variation()`: a seeded ±8 % that is the same every
time the flight is flown, and is shown in the reason.

Against the plan's off-block time (`crew.turnaround.off_block_in`):
- **Backward:** the latest each milestone can start, and its slot's opening (`opens`: the profile's `early_min` ahead of
  that, 10 minutes for boarding).
- **Forward:** each milestone's `eta`, from GSX's state and its head count where there is one, and what's holding the push
  (`driver`).

A plan more than 3 hours from the sim's clock isn't held to: the durations stand, the windows and the lateness don't.

| Mode | What it does |
|---|---|
| `realistic` (default) | The ramp holds a service asked for before its slot, or ahead of what it waits on. It says why and calls the service when it can come. Asked again within 5 minutes, they come anyway. |
| `advisory` | Only the times and the reminders. |
| `off` | None of it. |

Who uses it:
- **The copilot** reminds you when a milestone has passed its latest start (`reminder`). A question like "are we on time?",
  "how long for catering?" or "turnaround status?" is answered from it.
- **The ramp** answers status, including the bags, with times and reasons.
- **The cabin** answers for boarding, the doors and the crew.
- **The EFB's Airport Ops** shows the timeline. `TurnaroundState` carries it, and the recording keeps it.

Profiles (`[turnaround] profile`): `full_service`, `low_cost`, `regional` or `cargo`. On `auto` it comes from the airline
(`LOW_COST`, `CARGO`), and an aircraft of 100 seats or fewer is regional.

What nothing reports runs on the clock and is marked `modelled`: the crew's arrival, the baggage, and every service when GSX
isn't running. With GSX, a service not called before boarding is "not called".

## Cabin incidents (`src/localtc/crew/cabin_incidents.py`)

`[cabin] incidents`: `off` (default), `rare`, `normal` or `frequent`. The kinds are a medical event, an unruly passenger,
smoke or an odour, suspicious behaviour, a lavatory or cabin system fault, and a passenger needing assistance at the gate.

**When one comes.** Each chance (`slot_of`) is looked at once: every 5 minutes of boarding or deboarding, and every 20
minutes of the cruise from 15 minutes after levelling off to 200 nm out. A seeded hash of the flight and the slot says yes
or no. `rates()` sets the odds from:
- the length of the flight
- how many are on board
- rough weather at the airports
- how late it's running
- whether the passengers are boarding
- a wide body's galleys

Every incident records its reasons (`why`).

**Limits.** One at a time, an hour apart, one a flight (two on frequent). None starts during an emergency. None starts in
a sterile phase: pushback, taxi, takeoff, approach, landing. Reminders wait for a quiet moment. Only a worsening fire is
called during the descent, and it says why it's calling.

**The captain's decision.** It's said to the cabin: carry on, get assistance, divert, or declare. The decision is a
`CabinIncident` with stage `decided`, and it reaches:
- **ATC** (`AtcEngine._cabin_incident`): help to meet the aircraft, a diversion, or an emergency.
- **Airport Ops**: a message.
- **The turnaround**: an incident at the gate holds up boarding, by its severity and whether assistance came.

Unanswered for 6 minutes, the crew carry on. Each incident ends with a recorded outcome (stage `resolved`). The ones that
need the ground (medical, smoke) end at the gate.

The crew's lines are in English only.

## Conditions (`atc_core/conditions.py`)

New kinds:
- `behind_departure`: ICAO's "behind the departing ..., line up and wait behind".
- `taxi_follow`: ground's "follow the ...". Catching it up gets "slow down".
- `spacing`: from the flow, below.

A condition that runs out with the pilot still waiting on ATC ends `needs_instruction`. Each one records the
`next_instruction` that followed it.

## Dynamic routing (`atc_core/flow.py`)

Five moves, each an `Amendment` or a `Condition`, watched until it's over:

| Move | What happens |
|---|---|
| Vectors for descent | More than 4,000 ft above a 320 ft-a-mile profile: a 40° turn away from the airport, on a terrain-safe heading. Direct to the next fix once the flight is back within 1,500 ft. |
| Speed for spacing | Catching an arrival to the same airport within 8 nm: 250 knots. Resume normal speed once the gap is 11 nm. |
| A turn for traffic | Only after ATC has called the traffic: 20° when it's still coming within 5 nm. Direct once it's by. |
| Flow levels | With 3 or more arrivals ahead, the descent stops at FL240 first, then is cleared down. |
| Mountain caution | Descending near the minimum in mountainous terrain: a caution, once in 15 minutes. |

`validate()` checks every direct, altitude and heading for:
- a fix that exists
- no hazard on the line at its level
- the terrain

Ground that isn't known is never held against a clearance.

## Terrain (`atc_core/terrain.py`)

New calculations, alongside the existing MVA, MSA and corridor:
- `sample_ft` and `clearance_ft`: the ground at a point, and how high above it an altitude is.
- `route_corridor_ft`: the highest minimum along a route's legs.
- `minimum_safe_ft`: by phase and region. En route, 4 statute miles for the FAA and 8 km for ICAO; in the terminal area,
  the MVA.
- `mountainous`: ground of 3,000 ft or more around a point.
- `safe_heading`: the nearest heading clear of the terrain at an altitude.

The engine keeps a descent at or above the route's terrain floor until the way to the arrival is clear. A vector into rising
ground is turned to the nearest safe heading, or not given at all. An altitude asked for below the minimum is refused with
it. The tiles are cached on this PC (`ops/terrain.py`). The grids reach the ATC as `TerrainGrid` events, which are recorded,
so a replay has the same terrain.
