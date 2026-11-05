"""What the ground crew says, several ways each so it's never one canned line. The headset's words on the push (``FIXED``)
are the standard ones, the same every time and never put in a language model's words: on a real ramp a brake call misheard
is an aircraft rolling with a tug under it.
"""

import random

from localtc.ground.words import LINES as FOREIGN

# How each service is spoken of.
SPOKEN = {"GPU": "the GPU", "OperateJetways": "the jetway", "OperateStairs": "the stairs", "Deboarding": "deboarding",
          "Boarding": "boarding", "Cleaning": "the cleaners", "Catering": "catering", "Refueling": "the fuel truck",
          "Water": "the water truck", "Lavatory": "the lav truck", "DeIce": "de-icing", "Departure": "the tug"}

FIXED = {
    "brakes_set_first": ("Set parking brake before we connect.",),
    "engines_off_first": ("Shut down the engines first: we don't connect to a running aircraft.",),
    "stopped_first": ("Come to a full stop on the stand first.",),
    "release_brakes": ("Tug connected. Release parking brake.",),
    "pushing": ("Brakes released, pushing back.",),
    "cleared_start": ("Cleared to start engines.",),
    "set_brake": ("Pushback complete. Set parking brake.",),
    "brake_again": ("Set parking brake, please confirm when set.",),
    "confirm_start": ("Confirm good start?",),
    "disconnecting": ("Disconnecting. Watch for my hand signal on the {side}.",),
    "disconnecting_no_side": ("Disconnecting. Watch for my hand signal.",),
    "stopping": ("Stopping the push. Set parking brake.",),
}

LINES = {
    "ack": ("Copy, {list} coming up.", "No problem: {list}. On the way.", "Sure, I'll get {list} moving.",
            "You got it, {list}.", "{List}, on it."),
    "fuel_level": ("Fuel truck's in position. How much do you want? {low} to {high} percent.",
                   "Truck's connected. How much fuel, {low} to {high} percent?"),
    "fuel_loading": ("Copy, {percent} percent.", "{percent} percent it is, loading now."),
    "fuel_noted": ("{percent} percent, got it. I'll tell the truck.",),
    "already": ("{Name}'s already on it.", "{Name} is under way already.", "Already sorted, {name} is working."),
    "already_done": ("{Name} is done already.", "That's finished: {name}."),
    "not_here": ("I can't get you {name} here.", "{Name} isn't available right now."),
    "no_gsx": ("We've got no ground handling set up here: without GSX running I can't get you {name}.",
               "No GSX running, so no {name}, I'm afraid."),
    "gsx_says": ("They say: {message}", "Word from the ramp: {message}"),
    "cleaning_after": ("The cleaners can't start with passengers on board. I'll get them on as soon as everyone's off.",),
    "no_take": ("I've called for {name}, nothing from GSX yet. If nothing turns up, ask me again.",
                "No sign of {name} yet. If nothing turns up, ask me again."),
    "on_stand_first": ("Call us once you're stopped on the stand.", "Not until you're parked on the stand."),
    "fuel_engines": ("No fuel truck with an engine running: shut down first.",),
    "parked_at": ("You're on {parked}.", "We've got you on {parked}."),
    "parked_unknown": ("I can't see which stand you're on from here.",),
    "brake_for_bridge": ("Parking brake first, then I'll bring that up.",),
    "armed": ("Tug's standing by. Call ground for your push, and I'll connect as soon as you're cleared.",
              "Tug's ready when you are. We just need you cleared by ground first."),
    "cleared_push": ("You're cleared, connecting the tug now.", "Ground's cleared you, tug's coming on."),
    "warn_push": ("{List} still going. Push anyway?", "We've still got {list} on. Do you want to push regardless?"),
    "ask_direction": ("Which way do you want it: {choices}?", "Which way on the push? I can do {choices}."),
    "direction": ("Copy, {direction}.", "{Direction}, got it."),
    "no_direction": ("I can't push {direction} from this stand: only {choices}.",),
    "push_under_way": ("Tug's on it already.", "We're on the push already."),
    "no_push": ("There's no push going on.",),
    "cancelled": ("Okay, {name} cancelled.", "No problem, I've called off {name}.", "{Name} stood down."),
    "cancel_nothing": ("{Name} isn't running, nothing to cancel.", "Nothing to call off: {name} isn't on."),
    "cancel_failed": ("I couldn't call off {name}: {why}", "{Name} won't stand down: {why}"),
    "finished": ("Okay, {name} wrapped up now.", "Finishing {name} now."),
    "finish_failed": ("I couldn't finish {name} early: {why}",),
    "off": ("Okay, taking away {name}.", "{Name} coming off now."),
    "off_nothing": ("{Name} isn't on.", "{Name} isn't connected."),
    "off_failed": ("I couldn't take off {name}: {why}",),
    "cant_hurry": ("They're going as fast as they can, I can't hurry them from here. Want me to finish {name} now instead?",
                   "No way to speed up {name} from here. Shall I wrap it up early?"),
    "ok": ("Okay.", "Fine, leaving it.", "Understood."),
    "say_again": ("Say again?", "Didn't catch that, say again?", "Sorry, say again? I can do fuel, catering, water, lavatory, "
                  "cleaning, de-icing, the GPU, the jetway, stairs and the push."),
    "not_understood_llm": ("Sorry, I lost that one. Say again?",),
    "question": ("GSX is asking: {title}? {choices}.", "{title}? {choices}."),
    "picked": ("Okay: {choice}.", "{Choice}, done."),
    "menu_failed": ("Trouble with GSX on that one: {why}", "GSX won't play on that: {why}"),
    "gate_ok": ("{why}", "Copy. {why}"),
    "gate_refused": ("Can't do that one: {why}", "Unable: {why}"),
    "gate_told": ("I've got you down for that, but this GSX can't follow gate changes from here until it's updated.",),
    "gate_differs": ("You're on {parked}, ATC has you down for {gate}. We'll work you here.",
                     "We've got you at {parked}, not {gate}. Services will come here."),
    "status_none": ("Nothing on the go. Tell me what you need.", "All quiet here, nothing running."),
    "status": ("{report}",),
    "door": ("They're waiting on you: {door}.", "Holding for you: {door}."),
    # Done
    "done_GPU": ("GPU's connected, you've got power.", "Ground power's on."),
    "done_Refueling": ("Fuel's done{fuel}, truck's clear.", "Fuelling complete{fuel}.", "That's the fuel on{fuel}."),
    "done_Catering": ("Catering's finished.", "Catering's done, doors are yours.", "Catering all loaded."),
    "done_Water": ("Water's topped up.", "Potable water's done."),
    "done_Lavatory": ("Lavs are serviced.", "Lavatory service is done."),
    "done_Cleaning": ("Cleaners are finished.", "Cabin's clean, crew's off."),
    "done_DeIce": ("De-icing's complete.", "You're de-iced."),
    "done_OperateJetways": ("Jetway's done.", "That's the jetway moved."),
    "done_OperateStairs": ("Stairs are done.", "That's the stairs sorted."),
    "done_Boarding": ("Boarding's complete.",),
    "done_Deboarding": ("Everyone's off.", "Deboarding's complete."),
    "done_Departure": ("All clear on the ramp, have a good flight.", "That's you clear, have a good one."),
    "relay_boarding": ("That's one for the cabin crew, I'll pass it on.", "Cabin crew handle that, I'll let them know."),
}


class Words:
    def __init__(self, seed: int = 0, language: str = "") -> None:
        self.rng = random.Random(seed)
        self.language = language
        self.last: dict[str, str] = {}

    def in_language(self, key: str) -> bool:
        """Whether this line is said in the pilot's language: the headset's calls stay in aviation English."""
        return key in FOREIGN.get(self.language, {}) and key not in FIXED

    def line(self, key: str, **values: str) -> str:
        options = FIXED.get(key) or ((FOREIGN[self.language][key],) if self.in_language(key) else LINES[key])
        pick = options[0] if len(options) == 1 else self.rng.choice([o for o in options if o != self.last.get(key)])
        self.last[key] = pick
        capitalised = {k[0].upper() + k[1:]: v[0].upper() + v[1:] if v else v for k, v in values.items()}
        return pick.format(**values, **capitalised)


def is_fixed(key: str) -> bool:
    return key in FIXED


def spoken_list(names: list[str]) -> str:
    if len(names) < 2:
        return names[0] if names else ""
    return ", ".join(names[:-1]) + " and " + names[-1]
