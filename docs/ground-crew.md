# The ground crew

The turnaround and the push by voice, with no menus: on the ramp frequency (119.990 worldwide), or on the intercom starting
with "ramp". Off by default (`[ground] enabled`). The code is `src/localtc/ground/`.

## What's behind it

`api.GroundAdapter` is all the crew knows about who does the work:

| Adapter | When | What it is |
|---|---|---|
| `gsx_remote.GsxRemote` | GSX is installed | GSX's Remote API |
| `standalone.SimServices` | no GSX, `without_gsx = "standalone"` | the sim's key events: `TOGGLE_JETWAY`, `TOGGLE_PUSHBACK`, `TOGGLE_RAMPTRUCK`, `REQUEST_FUEL_KEY`, `REQUEST_CATERING`, `REQUEST_POWER_SUPPLY` |
| `mock.MockGsx` | `adapter = "mock"` | a stand-in with GSX's recorded menus, for working without a sim |

`crew.RampCrew` is pure, like the ATC: it's told what's said and what the services are doing, and says what to send.

## GSX's Remote API

FSDreamTeam documents this interface and invites ATC clients and voice assistants to use it: chapter "Couatl Remote API v2"
of `GSX_manual_MSFS.pdf`. **Nothing of GSX's is copied, shipped or opened beyond that**: never its `.pye` files, and the test
fixtures hold only short menu labels, with its icons taken out.

- `ws://127.0.0.1:<port>`, the port from `%APPDATA%\Virtuali\CouatlAddons.ini` `[gsx] remote_server_port`, else 8744.
- The server says `hello`; subscribe to `state`; a `snapshot` comes, then `patch` frames that each replace one top-level key.
- `service.trigger {service}`, `menu.open`, `menu.pick {index}` (0-based), `menu.close`. Never `menu.toggle`.
- `menu` is only real while `menuShown` is true: its title stays for a moment after it closes.

## What the real GSX does

Recorded at Zurich on GSX Pro 4.0.5 (MSFS 2024, A350). `tests/fixtures/gsx/lszh.json` holds the menus.

- **Calls in a row get dropped** (reported by others, not tried here): the crew sends one and waits for the state to change.
- **A `service.trigger` sometimes stalls** after GSX's first step through its menu (seen once, Lavatory at Geneva, cause unknown; the same call worked twice later, once with a menu left open). The crew call once more after 15 s, except the jetway, stairs and push, which a second call undoes. GSX's menu shows for about a second on every trigger: that's GSX stepping through it itself.
- **The Remote API can show a service late:** de-ice at Geneva stayed `available` for 85 s while `L:FSDT_GSX_DEICING_STATE` said requested after 5 s. So a service with one of those variables counts as taken when it says 4 or 5 (`crew.GSX_STATE_VARS`). GPU, water, lavatory, jetway and stairs have none: for those only the API can tell.
- **The rows change with what's going on:** "Request Catering service", "Catering vehicles are on their way", "Dismiss Ground
  Power Unit", "No Jetways here", "Cannot operate stairs while water/lavatory servicing". So the menu is read before every
  pick, by the row's words, and one row must match. Never a place in the list, never a wrong click.
- **Questions GSX asks, and when:**
  - "Select handling operator", "Select catering operator", "Select fueltruck operator": GSX's choice, marked `[GSX choice]`.
    The fuel truck's comes minutes later, when catering's done.
  - **"Select refueling level"**, when the truck is in position, with rows from 35% to 100%. **GSX gives up after about 30
    seconds and sends the truck away**, so the crew asks at once, or answers with what was said up front ("fuel 70 percent").
  - "Attach Pushback Tug now?" and "Do you want to board crew?": no and nobody (`crew.SETTLED`). The tug connects when the push
    is called, and the crew are already on board flying it.
  - Anything else is read out, and the pilot answers by number or name.
- **No cancel verb:** reopen the row, then "Service in progress" has "Complete now" and "Abort service". GSX bills a virtual
  calling fee if the vehicle was dispatched.
- **The GPU never completes:** it stays `performing` while connected, and goes back to `available` once dismissed.
- **`completed` lasts about 10 seconds**, then `available`. The crew keep their own list for "what's done".
- **`waiting` and `statusText`** give the reasons ("waiting for Catering") and ETAs. `message` has what the crew pass on
  (`crew.PASSED_ON`): "Waiting for your action: open R4", "Jetway not close enough to any door. Using stairs instead."

### Newer GSX

This GSX's `hello` lists `state, services, menu, prompts, billing`. Later builds add `gate`, `handlerData`, `handlerSet` and
`settings`. Those aren't used yet: older builds must keep working, and these get added once they can be tried.

- **`gate.select`**: LocalTC's gate kept in step with GSX. Until then, a gate change goes through ATC and the crew say once that
  GSX can't follow it.
- **`settings.set`** (Passengers Density): a real "speed up boarding". Until then the crew say they can't hurry them, and offer
  "complete now".
- **`handler.set`** `autoSelectOperator`: not needed while the questions are answered.

## The ramp frequency

`sim_api/ramp.py`. 119.990 is a real 8.33 kHz channel, so a radio's own knobs reach it, and "tune ramp" to the copilot sets it. A radio with only 25 kHz
steps can't dial it. It isn't a setting. **A real controller on that frequency always comes first.**

The engine stays out of it: with `engine.ramp_mhz` set, a transmission there with no real controller returns before any language
model is asked, and `RadioTuned` says "Ramp".

## Who answers

- **The ramp frequency:** the crew, on the radio (gone when tuned away, like any station). On the ground only: in the air it's
  silence, as out of range.
- **The intercom:** only "ramp, ...", "ground crew, ...", "chief, ...", "tug, ...", or the answer to what they just asked, ahead
  of the cabin's listening window. Bare "ground" stays ATC's: "ask ground for the push" is the copilot's.
- **Boarding and deboarding** are the cabin crew's (`CabinCrew.ground_relay`, and "cabin, start boarding" directly). No cabin
  crew: the copilot says it. No copilot: the ramp.

## The brake

The crew see the aircraft as the copilot does (`GroundService.as_read`, wired in `app.py`): the FSLabs's brake is its own lever, and the sim's variable never shows it set. With no copilot, or an aircraft nothing reads, the sim's variable reads off for good, so "it's set" is taken at the pilot's word until the aircraft rolls. Once the sim has shown the brake set, its reading goes before anyone's word.

## The push

Refused unless stopped, brake set and engines off. Asked for before the brake's set, it comes once it is, as the jetway and stairs do. Warns about anything still running, and asks. Then it **waits for ATC's
clearance** while there's a controller who'd give it (`wait_for_clearance`), and calls the tug once cleared, by itself.

The headset's calls (`lines.FIXED`) are the same every time and never reworded by a model: "Release parking brake", "Cleared to
start engines", "Set parking brake" (repeated until the brake is really set), then the disconnect and hand signal on the tail's
side.

**The push's pages haven't been seen yet**: their rows in `mock.py` and `crew.DIRECTIONS` come from GSX's manual ("Nose
Left/Tail Right"), and the stages from what it describes. The first real push should be recorded.

## The language model

**Without a cloud model, the grammar and nothing else.** With one (`[ground] llm`, gated like the cabin's):

| Mode | The model |
|---|---|
| `scripted` | isn't asked |
| `semi` | reads what the grammar can't |
| `mostly_llm` | also words their answers |
| `llm` | reads everything first |

A line with a condition or a fact in it is never reworded (`crew.AS_WRITTEN`): "parking brake first" came back as "parking brake set".

It answers in a closed list (`model.ACTIONS`, `SERVICES`), and what it reads goes through the same checks as plain words: it can't
invent a service or get a push past the brake or ATC. A reading turned away falls back to the grammar for that same call, and the
pilot's told (`llm_rejected`).

## In the pilot's language

With `[crew] language` set, the crew understand and answer in it (`ground/words.py`): `as_english` turns what's said into the English
the grammar reads, and each line has its translation. **The headset's calls on the push stay in aviation English** (`lines.FIXED`), and what
GSX itself says stays as GSX says it. Foreign lines are "{Name}: ..." because Polish and Russian change a noun with its place in a sentence.
One wording per line, against several in English.

## Aircraft

Handled generally by passing on `waiting` and `message`. Known differences:

| Aircraft | What to expect |
|---|---|
| PMDG 737/777 | GSX has a handler for them: calls PMDG's GPU; its own vehicles take 30-40 s to clear, so GSX can look stuck |
| Fenix A320 | GSX forces a full-freeze towbar push |
| iniBuilds A350 | at Zurich B37: "Jetway not close enough to any door. Using stairs instead." (the stand's for wingspans up to 40 m) |
| FSLabs, FlyByWire | nothing known: try them |

## Without a sim

`[ground] adapter = "mock"`, then talk to it: `tests/test_ground.py` has whole turnarounds on it.
