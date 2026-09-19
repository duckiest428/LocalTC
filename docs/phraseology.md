# Adding phraseology templates

Everything ATC says on a routine call comes from a TOML template in
`src/localtc/atc_core/phraseology/templates/`, one file per controller (`clearance.toml`, `ground.toml`,
`tower.toml`, `departure.toml`, `center.toml`, `approach.toml`) plus `common.toml` for calls any
controller makes. Templates hold the words. The engine (`atc_core/engine.py`) decides when to say them
and fills in the values.

## A template

```toml
[[template]]
id = "ground.taxi_out"                     # <controller>.<what>; unique across all files
controller = "ground"                      # clearance, ground, tower, departure, center, approach, or "any"
text = [                                   # one or more variants; the seeded RNG picks one
    "{callsign}, runway {runway}, taxi via {taxi_route}.",
]
readback = { required = ["runway"], optional = ["taxi_route"] }   # what the pilot must read back
pilot_readback = "Runway {runway}, taxi via {taxi_route}, {callsign_short}."  # an ideal readback
ack = "none"                               # "readback_correct": ATC says "readback correct" after a good one
```

- **`text`**: the display text. The spoken form is made from the same text, slot by slot:
  `{runway}` shows "34L" and is said "three four left", `{frequency}` shows "124.675" and is said
  "one two four point six seven five", `{altitude}` shows "5,000" and is said "five thousand".
- **`readback.required`**: the elements a readback must contain to be `correct`. Missing ones make it
  `incomplete` ("read back ..."). Wrong ones make it `incorrect` ("negative, ..."). One that is probably
  right but misheard makes it `unclear` ("confirm ...").
- **`readback.optional`**: elements that are checked if the pilot says them, but not required.
- **`pilot_readback`**: required whenever `readback` lists anything. Scripted pilots and the copilot use it,
  and a test renders it with random values and checks that the grammar parses it as `correct`, and as
  `incomplete` when any required element is left out.
- **`ack`**: `readback_correct` for clearances that are confirmed on the air (the IFR clearance).

No logic lives in templates: no conditionals, no optional clauses. When a call has a variant, it gets a
template of its own. For example, `clearance.ifr_at_cruise` is the IFR clearance without "expect ... ten
minutes after departure", used when the cruise altitude is the initial altitude. The engine picks between
them. Scenario rules written for `on = "clearance.ifr"` also fire for `clearance.ifr_*` variants.

## Slots

Every `{name}` must be a slot in `phraseology/slots.py` (`SLOTS`). Each slot has a type that knows its
display and spoken forms:

| Slot | Value | Shown | Said |
|---|---|---|---|
| `callsign` | `Callsign` | N172LT / Cessna 2LT | november one seven two lima tango |
| `callsign_short` | (in `pilot_readback` only) | 2LT | two lima tango |
| `runway`, `hold_short` | `"34L"` | 34L | three four left |
| `frequency` | `124.675` | 124.675 | one two four point six seven five |
| `squawk` | `"4521"` | 4521 | four five two one |
| `altitude`, `cruise` | `5000` | 5,000 | five thousand |
| `heading` | `90` | 090 | zero niner zero |
| `taxi_route` | `("A", "A1")` | A, A1 | alpha, alpha one |
| `approach` | `Approach("ILS", "16R")` | ILS RWY 16R | I L S runway one six right |
| `atis` | `"C"` | Charlie | charlie |
| `wind` | `Wind(340, 8)` | 340 at 8 | three four zero at eight |
| `altimeter` | `29.92` | 29.92 | two niner niner two |
| `station`, `destination`, `fix` | `str` | as given | as given |
| `message`, `correction`, `missing` | `Phrase` | pre-rendered | pre-rendered |

Slot names double as **readback element names**: a template can require `runway` because
`readback/extract.py` has a `runway` extractor in `ELEMENTS`. Phrase elements such as
`cleared_for_takeoff`, `line_up_and_wait`, `cleared_to_land` and `hold_position` are matched by
keywords, with common Whisper variants.

**A new slot type** needs three things. Add the type and its entry in `SLOTS` (`slots.py`). Add the speech
formatting in `speech.py`. If pilots must read it back, add an extractor in `readback/extract.py` and its
`ELEMENTS` entry, plus a `[fragments]` line in `common.toml` (e.g. `heading = "fly heading {heading}"`),
so corrections can say "negative, fly heading 270".

## Using a template from the engine

```python
self._schedule(t, "tower.luaw", {"runway": runway}, facility,
               clearance="line_up",                               # recorded in state.clearances
               on_issue=lambda: self._assign(departure_runway=runway))
```

- `_schedule` renders the template when it's due (after the reply delay, when the frequency is free),
  publishes the `AtcTransmission`, and sets up the pending readback from `readback`.
- `handoff_to=facility` marks a handoff: once it's read back, the engine expects the pilot on the new
  frequency.
- `expects_readback=False` for calls that don't need one ("roger", "say again", "affirmative").
- `note=Phrase(...)` adds a sentence after the instruction (weather, a caution, the ATIS).

## Checklist

1. Add the `[[template]]` in the right file. Give it a `pilot_readback` if it expects a readback.
2. Call it from the engine (`_schedule`), or from a pilot intent in `_on_request`.
3. `pytest tests/test_phraseology.py`. The library validates every slot, extractor and readback example
   when it loads, and the round-trip test covers the new template automatically.
4. If a scenario golden changes, check the diff and run `pytest --update-goldens`.
5. For templates of your own without editing the package, `TemplateLibrary.load("my/templates")`
   loads extra `*.toml` files after the built-in ones. A later file overrides an id.
