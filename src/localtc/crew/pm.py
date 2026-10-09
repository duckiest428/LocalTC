"""The Pilot Monitoring: hears the pilot on the intercom, works the aircraft, and says what it did.

What it hears is first read for what it is (``crew.speech_acts``): a command, a question, a report, a correction, a
"don't", an acknowledgement, a yes or no, a radio call on the wrong key, chat, or nothing usable. Only commands and
corrections are ever done, and how is decided by how sure the copilot is of the words and what's at stake:

- a low-risk command heard well enough (lights, its own altimeter, the standby frequency) is done;
- anything else heard clearly is done, unless its number disagrees with ATC (an altitude not the cleared one, a squawk
  not the one given, a QNH not the ATIS's): then it's said back for a "yes";
- heard only fairly well, it's said back first ("Heading 240?"); heard badly, "Did you say heading 240?";
- what's at high stake (the autopilot off near the ground, an emergency squawk, the speedbrakes low down) always
  waits for a "yes".

A "yes" counts only for the question just asked, within ``CONFIRM_S``, and only while what it depended on still stands
(the phase, the clearance, the frequency, the code, the flight): asked before a new clearance, it's asked again. The
safety rules (``actions.safety``) are checked again the moment before anything is done. A question is answered, never
done; a report is acknowledged, never done; "say again" repeats the last thing said; a radio call on the intercom is
offered, never sent unasked.

Every command then goes the same way: the sim commands, and a look at the sim within ``CHECK_S``: "Flaps 2." once the
handle shows it, "Flaps 2 didn't take, check it." if it never does, and where this aircraft's reading never moves,
said as sent but not seen. The radio (calls the pilot asks for, step climbs, diversions) goes through the flight deck
(``localtc.flightdeck``) the radio copilot shares, so the two never talk over each other or the pilot.

Pure: ``observe`` takes each bus event (with its ``t``) and returns what to publish (``CrewSpeech``, ``CrewAction``,
``CopilotEvent``, the copilot's ``Transcript`` on the radio) and the ``SimCommand``s to send, so a recording replays
through it the same way. A failure inside it is logged and recorded, never raised: ATC goes on.
"""

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from localtc.atc_core import region as regions
from localtc.config import PlanPerf
from localtc.crew import monitor as monitors
from localtc.crew import speech_acts
from localtc.crew.actions import Cockpit, Plan, plan, safety
from localtc.crew.answers import Picture, answer, facts
from localtc.crew.commands import Command
from localtc.crew.monitor import SAFETY, Call, Monitor
from localtc.crew.profiles import Profile, for_aircraft, load_all
from localtc.flightdeck import FlightDeck
from localtc.sim_api import (
    AircraftIdentity,
    AircraftSystems,
    AtcTransmission,
    CopilotEvent,
    CrewAction,
    CrewSpeech,
    IntercomHeard,
    OwnshipState,
    PhaseChanged,
    SimCommand,
    SimLifecycle,
    Transcript,
)

log = logging.getLogger(__name__)

CHECK_S = 3.0  # the sim shows a command within this, or it didn't take
CONFIRM_S = 10.0  # a question waits this long for its "yes"
OFFER_S = 15.0  # "send it?" for a radio call said on the intercom
OWN_HANDS_FAILED = 2  # the copilot's own switches not taking this many times, none ever taking: it can't reach them
CONTROL_FAILED = 2  # one control not taking this many times, its reading never moving: left to the pilot
PAUSE_SUMMARY_S = 120.0
REPEAT_HEARD_S = 3.0  # the same words on the intercom again this soon: one utterance, not two  # back from a pause this long: what's going on, in a sentence or two
QNH_TOLERANCE_HPA = 1.5  # a QNH this far from the ATIS's is asked about
STATION_WORDS = ("tower", "ground", "approach", "center", "centre", "departure", "clearance", "delivery", "unicom",
                 "radio", "traffic")
# The copilot's suggestions the pilot can put off ("later", "not now"): the monitor's DECLINE.
DECLINE = re.compile(r"\b(?:later|not (?:yet|now|right now)|stop (?:talking|it)|shut up|be quiet|enough|"
                     r"i didn'?t ask|chill(?: out)?|relax|cut it out|i know|we know|knock it off|give it a rest)\b")
# ... and of them, the ones that quiet the routine calls too, not just the suggestions.
HUSH = re.compile(r"\b(?:stop talking|shut up|be quiet|enough|chill(?: out)?|relax|cut it out|i know|we know|knock it off|"
                  r"give it a rest|stop it)\b")
SAID_AS = {"go_around": "go around", "emergency": "declare an emergency"}
CONTROL_WORDS = {"flaps?": "flaps", "gear": "gear", "lights?": "light", "auto ?pilot": "autopilot", "auto ?brakes?": "autobrake",
                 "spoilers?|speed ?brakes?": "spoilers", "parking brakes?": "parking_brake", "transponder|squawk": "squawk",
                 "altimeters?|qnh|baro": "altimeter", "go[ -]?around|going around": "go_around"}
FUEL_DOUBT = re.compile(r"\bfuel (?:prediction|projection|estimate|calc\w*|calls?|numbers?)s? (?:is|are|was|were|seems?)? ?"
                        r"(?:off|wrong|broken|bogus|not right)|\b(?:stop|enough|no more) (?:with )?(?:the )?fuel (?:calls?|warnings?|"
                        r"predictions?)")


# What the captain is about to do, or what's happening, said aloud: "time to start up our engines", "let's get
# rolling", "alright, boarding's complete". Nothing for the copilot to do.
REMARK = re.compile(r"^(?:(?:alright|all right|ok|okay|so|well|right|and)[,.\s]+)*(?:time to|let's|lets|we're going to|"
                    r"we are going to|we're gonna|gonna|going to|i'm going to|i'll|we'll|here we go|boarding|"
                    r"spooling|starting|engines?\b)")


@dataclass
class _Waiting:
    deadline: float
    plan: Plan
    quiet: bool = False  # the copilot's own action, already said: only a failure is reported


@dataclass
class Intent:
    """What the copilot asked the pilot about, waiting for the answer: bound to that question, its time and what it
    depended on."""

    id: int
    kind: str  # "do": the commands on yes; "send": the radio call on yes; "choose": one of ``options``
    commands: tuple[Command, ...]
    question: str
    t: float
    until: float
    context: dict[str, Any]
    utterance: str = ""
    text: str = ""  # "send": the words to say on the radio
    options: dict[str, Command] = field(default_factory=dict)


class PilotMonitoring:
    def __init__(self, engine: Any = None, *, profiles: list[Profile] | None = None, model: Any = None,
                 verbosity: str = "standard", hands: str = "pm", perf: PlanPerf | None = None, plan_source: str = "",
                 radio_mode=lambda: "off", alternate: str = "", repeat_atc: bool = False,
                 deck: FlightDeck | None = None, voice_sex: str = "") -> None:
        self.engine = engine  # the ATC engine, read only: what's been cleared, the stations, the callsign
        self.profiles = profiles if profiles is not None else load_all()
        self.model = model  # crew.model.CrewModel, or None: the grammar and the data answers only
        self.deck = deck or FlightDeck()  # shared with the radio copilot
        self.radio_mode = radio_mode
        self._settings = dict(verbosity=verbosity, hands=hands, perf=perf, plan_source=plan_source,
                              alternate=alternate, repeat_atc=repeat_atc)
        self._generation = self.deck.generation
        self.voice_sex = voice_sex  # the copilot's voice ("male"/"female"): its name goes with it
        self._aircraft = ""
        self._start()

    def _start(self) -> None:
        """Everything that belongs to one flight, new."""
        profile = getattr(getattr(self, "cockpit", None), "profile", None) or for_aircraft(self.profiles, "", "")
        self.cockpit = Cockpit(profile=profile)
        self.picture = Picture(self.cockpit, self.engine)
        s = self._settings
        # The copilot speaking first: callouts, reminders, relays, its own side of the cockpit (crew.monitor).
        self.monitor = Monitor(self.picture, verbosity=s["verbosity"], hands=s["hands"], perf=s["perf"],
                               plan_source=s["plan_source"], radio_mode=self.radio_mode, seed=_seed(self.engine),
                               alternate=s["alternate"], repeat_atc=s["repeat_atc"])
        self._waiting: list[_Waiting] = []
        self.intent: Intent | None = None
        self._intents = 0
        self._failed_said: set[str] = set()  # "didn't take" said once per control
        self._failures: dict[str, int] = {}  # times each control didn't take
        self._own_failed = self._own_took = 0
        self._no_hands_said = False
        self._heard_n = 0
        self._last_heard: tuple[float, str] | None = None
        self.utterances: deque[speech_acts.Reading | Any] = deque(maxlen=20)
        self._last_line: CrewSpeech | None = None  # for "say again"
        self._radio_queue: list[Any] = []  # the copilot's own radio calls (crew.monitor's), waiting for a free moment
        self.hands_off: set[str] = set()  # controls the pilot said not to touch
        if self.model is not None:
            self.model.history.clear()
            self.model.events.clear()
        self._persona()

    def _persona(self) -> None:
        """The first officer's own story for the model: the same all flight (crew.model.persona)."""
        if self.model is None or not hasattr(self.model, "persona"):
            return
        from localtc.crew.model import persona

        st = getattr(self.engine, "state", None)
        base = ""
        if st is not None:
            home = st.flight.origin if _seed(self.engine) % 2 == 0 else (st.flight.destination or st.flight.origin)
            geo = self.engine.geometry(home) if home else None
            base = geo.airport.name if geo is not None and geo.airport.name else (home or "")
        self.model.persona = persona(_seed(self.engine), self.voice_sex, base, self._aircraft)

    @property
    def profile(self) -> Profile:
        return self.cockpit.profile

    @property
    def _confirm(self) -> tuple[float, Command] | None:
        """The command waiting for a "confirm" (as it was known before the intents)."""
        i = self.intent
        return (i.until, i.commands[0]) if i is not None and i.kind == "do" and i.commands else None

    def observe(self, ev: Any) -> list[Any]:
        """``ev`` happened; returns the events to publish and the sim commands to send, in order. Never raises: a
        failure is logged, recorded, and the copilot carries on with the next event."""
        try:
            out = self._observe(ev)
        except Exception as exc:  # noqa: BLE001 - the copilot failing must never stop ATC
            log.exception("Copilot failed on %s", type(ev).__name__)
            self.deck.note("error", getattr(ev, "t", 0.0), f"{type(ev).__name__}: {type(exc).__name__}: {exc}")
            out = []
        return out + self.deck.drain()

    def _observe(self, ev: Any) -> list[Any]:
        out: list[Any] = []
        self.deck.observe(ev)
        if self.deck.generation != self._generation:  # a new flight, or the sim back after a disconnect
            self._generation = self.deck.generation
            self._start()
        if isinstance(ev, OwnshipState):
            self._note_own(ev)
            self.cockpit.own = ev
            self.cockpit.watch()
            self._read_clearance()
        elif isinstance(ev, AircraftSystems):
            self._note_systems(ev)
            self.cockpit.systems = ev
            self.cockpit.watch()
        elif isinstance(ev, PhaseChanged):
            self._note(ev.t, f"phase now {ev.phase.lower().replace('_', ' ')}")
        elif isinstance(ev, AircraftIdentity):
            profile = for_aircraft(self.profiles, ev.title, ev.atc_model)
            if profile is not self.cockpit.profile:
                log.info("Copilot: %s profile for %s", profile.name, ev.title or ev.atc_model)
                self.cockpit.profile = profile
            self._aircraft = monitors._aircraft_name(ev.title or ev.atc_model)
            self._persona()
        elif isinstance(ev, AtcTransmission):
            self.picture.last_atc = ev
            self._note(ev.t, f'{ev.station} said: "{ev.text}"')
        elif isinstance(ev, Transcript) and ev.radio != 0 and ev.text:
            self._note(ev.t, f'{"the copilot" if ev.source == "copilot" else "you"} said on the radio: "{ev.text}"')
        elif isinstance(ev, SimLifecycle) and ev.kind == "unpaused":
            out += self._back_from_pause(ev.t)
        elif isinstance(ev, IntercomHeard):
            self._read_clearance()
            out += self._heard(ev)
        self.monitor.observe(ev)
        out += self._tick(ev.t)
        for call in self.monitor.due(ev.t):
            out += self._callout(call, ev.t)
        out += self._radio_due(ev.t)
        out += self._radio_news(ev.t)
        if self.monitor.requests:  # what the monitor wants from the sim: an arrival's restrictions, airports around
            out += self.monitor.requests
            self.monitor.requests = []
        return out

    def _callout(self, call: Call, t: float) -> list[Any]:
        """One of the monitor's calls: the copilot's own hands first (each checked, as a command is), then the words."""
        out: list[Any] = []
        skipped = False
        for cmd in call.commands:
            if cmd.action in self.hands_off or cmd.action in self.cockpit.dead:
                skipped = True  # the pilot said not to, or it never reaches this aircraft
                continue
            p = plan(cmd, self.cockpit)
            if isinstance(p, str) or safety(cmd, self.cockpit).kind != "ok":
                continue
            if self.cockpit.believes(p.reads) and p.check(self.cockpit) is True:
                continue  # already so
            out += [*p.writes, CrewAction(t=t, action=p.action, value=p.value, outcome="sent",
                                          detail=f"{call.key}: " + "; ".join(_describe(w) for w in p.writes))]
            self.deck.memory.did(t, f"set {p.value} ({p.action})" if p.action != "light" else f"{p.value}")
            if cmd.action != "altimeter":  # an airliner's own STD and QNH buttons often leave the sim's setting alone
                self._waiting.append(_Waiting(t + CHECK_S, p, quiet=True))
        kind = "alert" if call.priority >= SAFETY else "callout"
        text, spoken = call.text, call.spoken
        if skipped and text.endswith(" set."):
            # Not done by the copilot, so not said as done: "8,000 set." became the pilot's to set.
            text, spoken = text[:-len(" set.")] + ", yours to set.", ""
        if not text:  # a hand only (the altimeter to the ATIS): nothing said
            return out
        # Only the copilot's talk is put in its own words; a callout or a warning keeps its words (reworded,
        # "moderate turbulence" became "turbulence ahead" and a heading reminder "heading's set").
        if self.model is not None and call.priority < SAFETY and not call.urgent and not call.before_readback \
                and call.key.startswith(REWORDED):
            words, exchanges = self.model.reword(t, text)
            out += exchanges
            if words:
                text, spoken = words, ""
        if self.model is not None:
            self.model.heard("You", text)
        out.append(self._line(CrewSpeech(t=t, text=text, spoken=spoken, kind=kind)))
        if call.radio:  # then on the radio: the copilot asking ATC, when the frequency's free
            self._radio_queue.append(self.deck.submit("crew", "request", call.key, t, text=call.radio))
        return out

    def _radio_due(self, t: float) -> list[Any]:
        """The copilot's own radio calls, each once the frequency is free and if it's still right to make."""
        out: list[Any] = []
        waiting = []
        for item in self._radio_queue:
            if item.done or item.cancelled:
                continue
            busy = self.monitor.ptt_down or t < self.monitor.busy_until or self.deck.queued("radio", ("readback",))
            if busy and t <= item.expires:
                waiting.append(item)
                continue
            if self.deck.check(item, t) is not None:
                if not item.cancelled:  # held (the pilot on the radio, a more urgent call first): try again
                    waiting.append(item)
                continue
            self.deck.done(item, t)
            out.append(Transcript(t=t, text=item.text, source="copilot"))
        self._radio_queue = waiting
        return out

    def _radio_news(self, t: float) -> list[Any]:
        """What the radio copilot couldn't do, told to the captain: the frequency that didn't change, the check-in
        nobody answered. (The captain tuning the radio themselves is only noted: they know.)"""
        news, self.deck.for_crew = self.deck.for_crew, []
        out = []
        for n in news:
            if n.kind == "tune_failed":
                out.append(self._say(t, f"{n.detail}. Can you set it?", "alert"))
            elif n.kind == "unanswered":
                out.append(self._say(t, n.detail + ".", "alert"))
        return out

    # --- the pilot ------------------------------------------------------------------------------------------------

    def _heard(self, ev: IntercomHeard) -> list[Any]:
        """What the captain said on the intercom: read for what it is first, then answered or done as that."""
        t, text = ev.t, ev.text
        if not text.strip():
            return []
        last = self._last_heard
        if last is not None and last[1] == text.strip().lower() and t - last[0] <= REPEAT_HEARD_S:
            self.deck.note("duplicate", t, f'"{text}" again: done once')
            return []  # the same words handed over twice: once is enough
        self._last_heard = (t, text.strip().lower())
        self._heard_n += 1
        reading = speech_acts.read(text, ev.confidence, radio_call=self._radio_call(text))
        utterance = self.deck.said(t, "pilot", "intercom", text, confidence=ev.confidence, audio_ref=ev.audio_ref,
                                   act=reading.act, extra=(("input", ev.source),))
        uid = utterance.id
        self.utterances.append(reading)
        self.deck.note("heard", t, f'"{text}" ({reading.certainty}' + (f", {ev.confidence:.2f}" if ev.confidence is not None
                                                                         else ", typed")
                       + (f", {ev.audio_ref}" if ev.audio_ref else "") + ")",
                       utterance=uid, act=reading.act, action=", ".join(str(c) for c in reading.commands))
        mode = self.model.mode if self.model is not None else "off"
        act = reading.act
        if act == "unclear":
            return []  # noise, or speech-to-text going round in circles: nothing to answer
        if FUEL_DOUBT.search(text.lower()):
            # "Your fuel prediction is off, the MCDU says we have enough": the copilot's projections stop; the
            # aircraft's own are the ones to go by.
            self.picture.fuel_doubted = True
            self.deck.memory.preferences["fuel calls"] = "none: the captain goes by the aircraft's own fuel prediction"
            self._heard_by_model(text)
            return [self._say(t, "Copy, I'll leave the fuel predictions to the box.")]
        if act != "answer" and DECLINE.search(text.lower()) and self.intent is None:
            if HUSH.search(text.lower()):
                self.monitor.hushed(t)
            else:
                self.monitor.declined(t)
            self._heard_by_model(text)
            return [self._say(t, "Copy.")]
        if self.monitor.cr is not None and act in ("acknowledgement", "answer", "report", "command", "chat") \
                and not any(c.action in speech_acts.CONTROL - {"yes", "no"} for c in reading.commands) \
                and len(text.split()) <= 8:
            # An answer to a checklist item ("on", "set", "one plus F, checked") is taken as that, whatever the words.
            done: list[Any] = []
            for cmd in reading.commands:  # "flaps one" as the answer to "Flaps?": set too
                if cmd.action not in ("yes", "no", "check"):
                    done += self._command(t, cmd, reading, uid)
            if self.monitor.respond(text, t):
                return done + self._now(t)
        if act == "answer" and (self.intent is not None or self.monitor.offer is not None):
            self._heard_by_model(text)
            return self._answer(t, reading.commands[0].action == "yes", uid)
        if act == "answer":  # "yep" with nothing asked: a word of agreement, nothing to authorise
            act = "acknowledgement"
        if act == "acknowledgement":
            if self.intent is not None or self.monitor.offer is not None:
                return self._answer(t, True, uid)  # "beacon, check": yes to what was asked
            self._heard_by_model(text)
            # Nothing to answer: in the scripted modes a word back, as before; otherwise silence, as a crew does.
            return [self._say(t, self.monitor.rng.choice(["Check.", "Copy."]))] if mode in ("off", "scripted") else []
        if act == "negation":
            return self._negation(t, reading, uid, text)
        if act == "radio":
            if mode == "llm":
                return self._ask_model(t, text, uid=uid)
            return [self._ask(t, Intent(0, "send", (), "That sounded like a radio call. I haven't sent it; want me to?",
                                        t, t + OFFER_S, self._context(()), uid, text=text), "confirm")]
        if act in ("command", "correction"):
            if act == "correction":
                self.deck.memory.correction = text
                if self.intent is not None:
                    self.deck.note("replaced", t, f"{self.intent.question} -> {text}", utterance=uid)
                    self.intent = None
            out: list[Any] = []
            for cmd in reading.commands:
                out += self._command(t, cmd, reading, uid)
            self._heard_by_model(text)
            return out
        if act == "question":
            return self._question(t, text, reading, uid, mode)
        if act == "report":
            self._heard_by_model(text)
            if reading.commands or mode in ("off", "scripted", "semi") or self.model is None:
                # The captain's own action ("I'll put flaps two") or a call ("three green"): acknowledged, not done.
                return [self._say(t, "Check.")]
            return self._ask_model(t, text, uid=uid)
        # Chat: the common questions from the data first in the scripted modes, the model for the rest.
        if act == "chat" and "correction" not in reading.why and speech_acts.CORRECTION.match(text.lower().strip()):
            self.deck.memory.correction = text
        if mode in ("off", "scripted") and (said := answer(text, self.picture)) is not None:
            return [self._say(t, said)]
        if reading.certainty == "low" and self.model is None:
            return [self._say(t, f'I heard "{text.strip(" .")}". Say again?')]
        if self.model is None and len(re.findall(r"[a-z']+", text.lower())) >= 4 and (picked := self.choose(t, text)) is None:
            return [self._say(t, "Copy.")]  # a remark, with no model to talk it over: heard, nothing to do
        return self._ask_model(t, text, uid=uid)

    def _heard_by_model(self, text: str) -> None:
        if self.model is not None:
            self.model.heard("Captain", text)

    def _question(self, t: float, text: str, reading: speech_acts.Reading, uid: str, mode: str) -> list[Any]:
        """Asked, never done: answered from what the aircraft shows (``_state``), the flight's data, or the model.
        A short question naming a control ("gear down?") might have been meant as the command: said so, and asked."""
        state = [s for c in reading.commands if (s := self._state(c)) is not None]
        if state:
            self._heard_by_model(text)
            reply = " ".join(state)
            short = len(re.findall(r"[a-z0-9]+", text.lower())) <= 4 and len(reading.commands) == 1
            cmd = reading.commands[0] if reading.commands else None
            if short and cmd is not None and cmd.action not in speech_acts.CONTROL and self._state(cmd, wanted=True) is False:
                # "Gear down?": heard as a question. What it shows, and the command offered, not done.
                words = _said(cmd)
                return [self._ask(t, Intent(0, "do", (cmd,), f"That sounded like a question. {reply} Want {words}?", t,
                                            t + CONFIRM_S, self._context((cmd,)), uid), "confirm")]
            return [self._say(t, reply)]
        if mode in ("off", "scripted") and (said := answer(text, self.picture)) is not None:
            self._heard_by_model(text)
            return [self._say(t, said)]
        if self.model is None:
            if (said := answer(text, self.picture)) is not None:
                return [self._say(t, said)]
            return [self._say(t, "I'm not sure. Say again?" if reading.certainty == "low" else "I don't have that.")]
        return self._ask_model(t, text, uid=uid)

    def _state(self, cmd: Command, wanted: bool = False) -> Any:
        """What the aircraft shows for the control ``cmd`` names, as said ("Gear's up."), or None when this isn't a
        control. ``wanted``: instead, True/False whether it shows what ``cmd`` asks for (None: can't tell)."""
        p = plan(cmd, self.cockpit) if cmd.action not in speech_acts.CONTROL | {"radio", "go_around", "emergency"} else None
        if not isinstance(p, Plan):
            return None
        shown = p.check(self.cockpit) if self.cockpit.believes(p.reads) else None
        if wanted:
            return shown
        name = {"gear": "Gear", "flaps": "Flaps", "autopilot": "The autopilot", "parking_brake": "The parking brake",
                "autobrake": "The autobrake", "spoilers": "The spoilers", "light": (p.done.split(" on")[0].split(" off")[0]),
                "squawk": "The squawk", "heading": "The heading", "altitude": "The altitude", "speed": "The speed"}
        what = name.get(cmd.action, cmd.action.replace("_", " ").capitalize())
        if shown is None:
            return f"I can't read {what.lower().removeprefix('the ')} on this aircraft."
        f = facts(self.picture)
        if cmd.action == "gear":
            return f"Gear's {f.get('gear', 'unknown')}."
        if cmd.action == "flaps" and "flaps" in f:
            return f"Flaps {f['flaps']}." if f["flaps"] != "up" else "Flaps are up."
        if cmd.action == "autopilot":
            return f"Autopilot's {f.get('autopilot', 'unknown')}."
        if shown:
            return p.already or f"{what} shows {cmd.value}."
        return f"{what} doesn't show {cmd.value or 'that'}."

    def _negation(self, t: float, reading: speech_acts.Reading, uid: str, text: str) -> list[Any]:
        """ "Don't", "never mind", "no, don't go around": whatever was waiting stops; nothing is done."""
        self._heard_by_model(text)
        out: list[Any] = []
        named = {c.action for c in reading.commands} | {action for word, action in CONTROL_WORDS.items()
                                                         if re.search(rf"\b{word}\b", text.lower())}
        if "go_around" in named:
            # The copilot doesn't read back a go-around the captain isn't flying; it's the captain's to tell tower.
            held = self.deck.cancel(t, "the captain isn't going around", owner="radio",
                                    keep=lambda item: "go_around" not in item.key)
            self.deck.wants.pop("go_around", None)
            if held:
                return [self._say(t, "Copy, I haven't read the go-around back. Tower's expecting it: tell them.", "alert")]
            return [self._say(t, "Copy, no go-around.")]
        if self.intent is not None:
            self.deck.note("cancelled", t, f"{self.intent.question}: the captain said no", utterance=uid)
            self.intent = None
            out.append(self._say(t, "Copy, leaving it."))
        stopped = self.deck.cancel(t, "the captain said not to", owner="crew")
        self._radio_queue = []
        if named - speech_acts.CONTROL:
            # "Don't touch the flaps": the copilot's own hands stay off them from now on.
            self.hands_off |= named
            self.deck.memory.preferences["hands off"] = ", ".join(sorted(self.hands_off))
            if not out:
                out.append(self._say(t, f"Copy, I'll leave the {_said(Command(sorted(named)[0])).split()[0]} alone."))
        if not out and not stopped and not named and self.model is not None:
            return self._ask_model(t, text, uid=uid)  # nothing to stop: it was conversation ("never mind, you live there")
        if not out:
            out.append(self._say(t, "Copy."))
        return out

    def _command(self, t: float, cmd: Command, reading: speech_acts.Reading | None = None, uid: str = "") -> list[Any]:
        """One command, by how sure the copilot is of it and what's at stake."""
        sure = reading.certainty if reading is not None else "high"
        if cmd.action == "check":
            return [self._say(t, "Loud and clear.")]
        if cmd.action == "say_again":
            last = self._last_line
            return [CrewSpeech(t=t, text=last.text, spoken=last.spoken, kind="reply")] if last is not None else \
                [self._say(t, "I haven't said anything.")]
        if cmd.action in ("yes", "no"):
            return self._answer(t, cmd.action == "yes", uid)
        if cmd.action == "checklist":
            if not monitors.CHECKLISTS:  # none to read yet
                return [self._say(t, "No checklists from me yet; I'll read them once we have the real ones for this aircraft.")]
            name = cmd.value or self._next_checklist()
            if name is None:
                return [self._say(t, "Which checklist?")]
            self.monitor.checklist(name, t)
            return self._now(t)
        if cmd.action == "brief":
            self.monitor.briefing(cmd.value, t)
            return self._now(t)
        if cmd.action == "status":
            self.monitor.status(t)
            return self._now(t)
        if cmd.action == "verbosity":
            self.monitor.verbosity = cmd.value
            return [self._say(t, {"quiet": "Copy, only what matters.", "chatty": "Copy, I'll keep you posted.",
                                  "standard": "Copy, the usual calls."}[cmd.value])]
        if cmd.action in ("radio", "go_around", "emergency"):
            return self._radio_command(t, cmd, sure, uid)
        self.monitor.pilot_said(cmd, t)
        self.hands_off.discard(cmd.action)  # asked for: the copilot's hands are wanted on it after all
        if not self.monitor.hands and self.monitor.hands_setting and cmd.action != "com_active":
            # Can't reach this aircraft's switches: said, not pretended (once fully, then briefly).
            if self._no_hands_said:
                return [self._say(t, "That one's yours.", "refused")]
            self._no_hands_said = True
            return [self._say(t, "I can't move this aircraft's switches from here; they're yours.", "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail="no hands in this aircraft")]
        if cmd.action in self.cockpit.dead:
            return [self._say(t, f"My {_said(cmd).split()[0]} doesn't reach this aircraft; that one's yours.", "refused")]
        verdict = safety(cmd, self.cockpit)
        if verdict.kind == "refuse":
            return [self._say(t, verdict.reason, "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=verdict.reason)]
        if cmd.action == "autobrake" and not cmd.value:
            return [self._autobrake_which(t, uid)]
        risk = speech_acts.risk(cmd, airborne=self.cockpit.airborne)
        conflict = self._conflict(cmd)
        if verdict.kind == "confirm" or risk == "high":
            question = verdict.reason or f"{_said(cmd).capitalize()}, confirm?"
        elif conflict:
            question = conflict
        elif sure == "low":
            question = f"Did you say {_said(cmd)}?"
        elif reading is not None and reading.spoken and cmd.action in speech_acts.NUMERIC and cmd.action != "com_standby" \
                and not self._corroborated(cmd):
            # A number heard, not typed, that nothing ATC gave backs up: read back before it's set ("Confirm heading
            # 240?"). One that matches the clearance, the code, the handoff or the ATIS is set straight away.
            question = f"Confirm {_said(cmd)}?"
        elif sure == "medium" and risk != "low":
            question = f"{_said(cmd).capitalize()}?"
        else:
            return self._do(t, cmd)
        return [self._ask(t, Intent(0, "do", (cmd,), question, t, t + CONFIRM_S, self._context((cmd,)), uid), "confirm"),
                CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="confirm", detail=question)]

    def _autobrake_which(self, t: float, uid: str) -> CrewSpeech:
        """ "Turn on the autobrake": the setting asked for. Before takeoff, max (the rejected takeoff setting)."""
        own = self.cockpit.own
        before_takeoff = own is not None and own.on_ground and self.monitor.f.takeoff_t is None
        names = [n for n in (self.profile.autobrake or ("off", "low", "medium", "max")) if n != "off"]
        if before_takeoff:
            cmd = Command("autobrake", "max")
            return self._ask(t, Intent(0, "do", (cmd,), "Autobrake max for takeoff?", t, t + CONFIRM_S,
                                       self._context((cmd,)), uid), "confirm")
        options = {n: Command("autobrake", n) for n in names}
        return self._ask(t, Intent(0, "choose", (), f"Autobrake {', '.join(names[:-1])} or {names[-1]}?", t,
                                   t + CONFIRM_S * 2, self._context(()), uid, options=options), "confirm")

    def _conflict(self, cmd: Command) -> str:
        """A number that disagrees with ATC, said as the question to ask ("" when it agrees, or there's nothing to
        check it against)."""
        a, v = cmd.action, cmd.value
        if a == "altimeter" and cmd.target == "hpa" and (qnh := self._atis_qnh()) is not None \
                and abs(int(v) - qnh) > QNH_TOLERANCE_HPA:
            return f"The ATIS has QNH {qnh}. Set {v}?"
        if a == "heading" and self.engine is not None:
            given = getattr(self.engine.state.assignments, "heading", None)
            if given and int(v) % 360 != int(given) % 360:
                return f"ATC gave us heading {int(given):03d}. Heading {int(v):03d}?"
        return ""

    def _corroborated(self, cmd: Command) -> bool:
        """The number in ``cmd`` is the one ATC gave (the cleared altitude, the heading, the code, a frequency it
        sent the flight to, the ATIS's QNH)."""
        if self.engine is None:
            return False
        st, a, v = self.engine.state, self.engine.state.assignments, cmd.value
        try:
            if cmd.action == "altitude":
                return a.altitude_ft is not None and int(v) == int(a.altitude_ft)
            if cmd.action == "heading":
                return getattr(a, "heading", None) is not None and int(v) % 360 == int(a.heading) % 360
            if cmd.action == "speed":
                return getattr(a, "speed_kt", None) is not None and int(v) == int(a.speed_kt)
            if cmd.action == "squawk":
                return v == a.squawk
            if cmd.action == "com_active":
                known = [f.mhz for f in getattr(self.engine, "facilities", [])]
                if st.comms.expected is not None:
                    known.append(st.comms.expected.mhz)
                return any(abs(float(v) - m) < 0.006 for m in known)
            if cmd.action == "altimeter" and cmd.target == "hpa":
                qnh = self._atis_qnh()
                return qnh is not None and abs(int(v) - qnh) <= QNH_TOLERANCE_HPA
        except (TypeError, ValueError):
            return False
        return False

    def _atis_qnh(self) -> int | None:
        """The QNH ATC gave or the ATIS has, in hectopascals: the destination's once on the way down, else the origin's."""
        if self.engine is None:
            return None
        st = self.engine.state
        arriving = st.phase in ("ARRIVAL", "APPROACH", "LANDING", "TAXI_IN") or (st.phase == "CRUISE" and self.monitor.f.takeoff_t)
        icao = st.flight.destination if arriving else st.flight.origin
        info = self.engine.current_atis(icao) if icao else None
        inhg = info.weather.altimeter_inhg if info is not None else None
        return round(inhg * 33.8639) if inhg else None

    def _radio_command(self, t: float, cmd: Command, sure: str, uid: str) -> list[Any]:
        """A call the captain wants made (pushback, taxi, the clearance, ready for departure), a go-around, or an
        emergency: made by the radio copilot when it has the radios, said whose it is otherwise."""
        mode = self.radio_mode()
        if cmd.action == "go_around":
            if sure != "high":
                return [self._ask(t, Intent(0, "do", (cmd,), "Go around?", t, t + 5.0, self._context((cmd,)), uid), "confirm")]
            return self._go_around(t)
        if cmd.action == "emergency":
            word = "pan-pan" if cmd.value == "pan" else "mayday"
            question = f"Declare a {word}" + (" with " + self._station() if self._station() else "") + "?"
            return [self._ask(t, Intent(0, "do", (cmd,), question, t, t + CONFIRM_S, self._context((cmd,)), uid), "confirm")]
        # radio: pushback, taxi, clearance, departure
        if mode == "off":
            return [self._say(t, f"Your radios: {_radio_words(cmd.value, self._station(cmd.value))}.")]
        if sure == "low":
            return [self._ask(t, Intent(0, "do", (cmd,), f"Did you want me to {_radio_words(cmd.value, '').lower()}?",
                                        t, t + CONFIRM_S, self._context((cmd,)), uid), "confirm")]
        self.deck.want(cmd.value, t)
        return [self._say(t, "Asking." if cmd.value != "departure" else "Telling tower.")]

    def _go_around(self, t: float) -> list[Any]:
        """The captain's go-around: anything queued goes, and tower is told when the copilot has the radios."""
        self.deck.cancel(t, "the captain's go-around", kinds=("call", "checkin", "request"))
        self.intent = None
        out: list[Any] = [self._say(t, "Going around.", "callout")]
        if self.radio_mode() == "full":
            self.deck.want("go_around", t)
        return out

    def _now(self, t: float) -> list[Any]:
        """What the pilot just asked the monitor for, said at once (they asked: no waiting for a gap)."""
        out: list[Any] = []
        for call in [q for q in self.monitor.queue if q.t <= t and q.key.startswith(("run:", "brief:", "status:", "landing:"))]:
            self.monitor.queue.remove(call)
            out += self._callout(call, t)
        return out

    def _next_checklist(self) -> str | None:
        offer = self.monitor.offer
        if offer is not None and offer.kind == "checklist":
            return offer.value
        phase = self.monitor.phase or "PARKED"
        return {"PARKED": "before_start", "PUSHBACK": "after_start", "TAXI_OUT": "before_takeoff",
                "RUNWAY_HOLD": "before_takeoff", "DEPARTURE": "after_takeoff", "CRUISE": "descent", "ARRIVAL": "descent",
                "APPROACH": "landing", "LANDING": "landing", "TAXI_IN": "shutdown"}.get(phase)

    # --- questions and answers --------------------------------------------------------------------------------------

    def _ask(self, t: float, intent: Intent, kind: str) -> CrewSpeech:
        """Ask the pilot; the answer counts for this question only (a new one replaces it)."""
        if self.intent is not None:
            self.deck.note("replaced", t, f"{self.intent.question} -> {intent.question}", utterance=intent.utterance)
        self._intents += 1
        intent.id = self._intents
        self.intent = intent
        self.deck.note("asked", t, intent.question, utterance=intent.utterance,
                       action=", ".join(str(c) for c in intent.commands) or intent.text)
        return self._say(t, intent.question, kind)

    def _context(self, commands: tuple[Command, ...]) -> dict[str, Any]:
        """What a question depends on: if any of it changes before the answer, the answer isn't taken for it."""
        st = self.engine.state if self.engine is not None else None
        ctx: dict[str, Any] = {"flight": self.deck.generation, "phase": st.phase if st is not None else self.monitor.phase}
        actions = {c.action for c in commands}
        if st is not None:
            a = st.assignments
            if actions & {"altitude", "vs", "go_around"}:
                ctx["cleared altitude"] = a.altitude_ft
            if "heading" in actions:
                ctx["assigned heading"] = getattr(a, "heading", None)
            if "squawk" in actions:
                ctx["squawk"] = a.squawk
            if actions & {"com_active", "com_standby", "com_swap", "radio", "emergency"}:
                ctx["frequency"] = st.comms.tuned.station if st.comms.tuned is not None else None
            if actions & {"radio"}:
                ctx["clearances"] = tuple(sorted(st.clearances))
        return ctx

    def _changed(self, intent: Intent) -> str:
        """What changed since ``intent`` was asked ("" nothing that matters)."""
        now = self._context(intent.commands)
        for key, was in intent.context.items():
            if now.get(key) != was:
                return "a new flight" if key == "flight" else f"the {key} changed" if key != "phase" else \
                    f"we're in the {str(now.get(key) or '').lower().replace('_', ' ')} now"
        return ""

    def _answer(self, t: float, yes: bool, uid: str = "") -> list[Any]:
        # The copilot's own question to the captain's words comes first; then what it offered by itself.
        if (self.intent is None or t > self.intent.until) and (offer := self.monitor.take_offer(t)) is not None:
            if not yes:
                self.monitor.declined(t)
                if offer.kind == "step":
                    self.monitor.steps_declined = True  # the plan's step climbs: not suggested again this flight
                return [self._say(t, "Copy, later then.")]
            if offer.kind == "checklist":
                self.monitor.checklist(offer.value, t)
                return self._now(t)
            if offer.kind == "brief":
                self.monitor.briefing(offer.value, t)
                return self._now(t)
            if offer.kind == "squawk":
                return self._command(t, Command("squawk", offer.value))
            if offer.kind in ("step", "divert", "sight"):
                return self._ask_atc(t, offer.kind, offer.value)
        intent, self.intent = self.intent, None
        if intent is None:
            return [self._say(t, "Copy.")] if yes else []
        if t > intent.until:
            self.deck.note("expired", t, intent.question, utterance=uid)
            return [self._say(t, "That's gone stale. Say again what you need?")] if yes else []
        if not yes:
            self.deck.note("declined", t, intent.question, utterance=uid)
            return [self._say(t, "Copy, leaving it.")]
        if (changed := self._changed(intent)):
            self.deck.note("expired", t, f"{intent.question}: {changed}", utterance=uid)
            return [self._say(t, f"That was before {changed.replace('the ', 'the ', 1)}. Say it again if you still want it."
                              if not changed.startswith("we're") else f"Not now, {changed}.")]
        if intent.kind == "send":
            self.deck.said_on_radio(intent.text, t)
            return [Transcript(t=t, text=intent.text, source="copilot")]
        if intent.kind == "choose":
            return [self._say(t, "Which one?")]
        out: list[Any] = []
        for cmd in intent.commands:
            if cmd.action == "go_around":
                out += self._go_around(t)
            elif cmd.action == "emergency":
                self.deck.want("pan" if cmd.value == "pan" else "mayday", t)
                out.append(self._say(t, "Declaring it."))
            elif cmd.action == "radio":
                self.deck.want(cmd.value, t)
                out.append(self._say(t, "Asking."))
            else:
                out += self._do(t, cmd)
        return out

    def choose(self, t: float, text: str) -> list[Any] | None:
        """The answer to "low or medium?": the option named, done; None when it names none."""
        intent = self.intent
        if intent is None or intent.kind != "choose" or t > intent.until:
            return None
        lowered = text.lower()
        picked = next((cmd for name, cmd in intent.options.items() if re.search(rf"\b{re.escape(name)}\b", lowered)), None)
        if picked is None:
            return None
        self.intent = None
        return self._do(t, picked)

    def _ask_atc(self, t: float, kind: str, value: str) -> list[Any]:
        """The pilot said yes to asking ATC (a step climb, a diversion, the field in sight): the copilot asks when it
        works the radio, else says who to tell."""
        words = self.monitor.radio_request(kind, value)
        if self.radio_mode() == "full" and words:
            self._radio_queue.append(self.deck.submit("crew", "request", f"{kind}:{value}", t, text=words))
            return [self._say(t, "Asking.")] + self._radio_due(t)
        st = self.engine.state if self.engine is not None else None
        station = st.comms.tuned.station if st is not None and st.comms.tuned is not None else "ATC"
        if kind == "sight":
            return [self._say(t, f"Tell {station}, and they'll clear us for the visual.")]
        return [self._say(t, f"Your radios: ask {station}" + (f", \"{words.split(', ', 2)[-1]}\"." if words else "."))]

    def _ask_model(self, t: float, text: str, grammar: list[Command] | None = None, uid: str = "") -> list[Any]:
        """The model's reply, or its reading of a command: done straight away when the grammar read the same command,
        otherwise said back for the pilot's "confirm"."""
        if (picked := self.choose(t, text)) is not None:
            return picked
        if self.model is None:
            return [self._say(t, "Say again?")]
        more = None
        if getattr(self.model.backend, "rich", False) and self.engine is not None and hasattr(self.engine, "_flight_more"):
            more = self.engine._flight_more(None, t)  # a cloud model: the whole flight, not just the copilot's facts
        known = {**facts(self.picture), **self.deck.memory.lines(t)}
        if (said := answer(text, self.picture)) is not None:
            known["the answer from the instruments"] = said  # the data's own answer, for the model to put in words
        reading, exchanges = self.model.ask(t, text, known, more=more)
        if reading is None:
            if grammar:  # the model had nothing; the grammar did
                out = list(exchanges)
                for cmd in grammar:
                    out += self._command(t, cmd, None, uid)
                return out
            if said is not None:
                return [*exchanges, self._say(t, said)]
            why = exchanges[-1].detail if exchanges else ""
            remark = REMARK.match(text.lower().strip()) is not None
            if why.startswith(("claims", "reassures", "names a place")) or (remark and why):
                # Its reply turned away (it said what isn't so, or made a command of a remark: "time to start up our
                # engines" was "landing lights on"): what fits the call instead, not "say again?" to words heard well.
                asked = "?" in text or re.match(r"^\s*(?:[a-z']+[,\s]+){0,2}(?:what|how|where|which|who|do|did|are|is|will|can)\b",
                                                 text.lower())
                return [*exchanges, self._say(t, "Can't tell that from here." if asked else "Copy.")]
            return [*exchanges, self._say(t, "Say again?")]  # a command it couldn't make out, or no reply at all
        if reading.command is None:
            return [*exchanges, self._say(t, reading.reply)]
        cmd = reading.command
        if grammar and any(g.action == cmd.action and g.value.lower() == cmd.value.lower() for g in grammar):
            return [*exchanges, *self._command(t, cmd, None, uid)]  # the model and the grammar read the same
        verdict = safety(cmd, self.cockpit)
        if verdict.kind == "refuse":
            return [*exchanges, self._say(t, verdict.reason, "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=verdict.reason)]
        # Read by the model only: never done without the captain's "yes".
        question = verdict.reason if verdict.kind == "confirm" else f"{reading.reply.rstrip('.!')}, confirm?"
        return [*exchanges, self._ask(t, Intent(0, "do", (cmd,), question, t, t + CONFIRM_S, self._context((cmd,)), uid),
                                      "confirm"),
                CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="confirm", detail="read by the language model")]

    def _do(self, t: float, cmd: Command) -> list[Any]:
        """Do ``cmd`` now: the safety rules once more (things may have changed since it was asked), then the sim."""
        verdict = safety(cmd, self.cockpit)
        if verdict.kind == "refuse":
            return [self._say(t, verdict.reason, "refused"),
                    CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=verdict.reason)]
        p = plan(cmd, self.cockpit)
        if isinstance(p, str):
            return [self._say(t, p, "refused"), CrewAction(t=t, action=cmd.action, value=cmd.value, outcome="refused", detail=p)]
        shown = p.check(self.cockpit)
        if shown and p.already and self.cockpit.believes(p.reads):
            return [self._say(t, p.already), CrewAction(t=t, action=p.action, value=p.value, outcome="done", detail="already")]
        if not p.writes:  # nothing to send (a toggle already as asked)
            return [self._say(t, p.already or p.done)]
        out: list[Any] = [*p.writes, CrewAction(t=t, action=p.action, value=p.value, outcome="sent",
                                                 detail="; ".join(_describe(w) for w in p.writes))]
        self.deck.memory.did(t, p.done.rstrip("."))
        if cmd.action == "com_active":
            self.deck.expect_tune(float(cmd.value), t)
        if shown is None:  # nothing to read it back from (an add-on that doesn't report it): said as done
            out.append(self._say(t, p.done, "done", p.done_spoken))
        else:
            self._waiting = [w for w in self._waiting if w.plan.action != p.action or w.plan.action == "light"]
            self._waiting.append(_Waiting(t + CHECK_S, p))
        return out

    # --- the sim ---------------------------------------------------------------------------------------------------

    def _tick(self, t: float) -> list[Any]:
        out: list[Any] = []
        still = []
        for w in self._waiting:
            shown = w.plan.check(self.cockpit)
            if shown or shown is None:
                if not w.quiet:
                    out.append(self._say(t, w.plan.done, "done", w.plan.done_spoken))
                if shown and w.quiet:
                    self._own_took += 1
                self._failures.pop(w.plan.action, None)
                out.append(CrewAction(t=t, action=w.plan.action, value=w.plan.value, outcome="done"))
            elif t >= w.deadline:
                out += self._didnt_take(t, w)
            else:
                still.append(w)
        self._waiting = still
        if self.intent is not None and t > self.intent.until + 30.0:
            self.deck.note("expired", t, self.intent.question, utterance=self.intent.utterance)
            self.intent = None
        return out

    def _didnt_take(self, t: float, w: _Waiting) -> list[Any]:
        p = w.plan
        out: list[Any] = [CrewAction(t=t, action=p.action, value=p.value, outcome="failed",
                                     detail=f"the sim didn't show it within {CHECK_S:.0f} s")]
        self._failures[p.action] = self._failures.get(p.action, 0) + 1
        what = p.done.rstrip(".").replace(" set", "")
        if w.quiet:
            self._own_failed += 1
        if w.quiet and self._own_failed >= OWN_HANDS_FAILED and not self._own_took and not self.monitor.hands_dead:
            # Its switches never reach this aircraft: calls only from now on, said once.
            self.monitor.hands_dead = True
            out.append(self._say(t, "My switches aren't reaching this aircraft. I'll leave them to you and call.", "alert"))
        elif self._failures[p.action] >= CONTROL_FAILED and not self.cockpit.trusts(p.reads) \
                and p.action not in self.cockpit.dead and not self.monitor.hands_dead:
            # This one control never moves for the copilot (an A350's autopilot knobs): left to the pilot, said once.
            self.cockpit.dead.add(p.action)
            self.deck.memory.preferences.setdefault("not reaching", "")
            out.append(self._say(t, f"My {_said(Command(p.action, p.value)).split()[0]} inputs aren't reaching this "
                                    "aircraft. I'll call them; you set them.", "alert"))
        elif p.action not in self._failed_said and not self.monitor.hands_dead:
            self._failed_said.add(p.action)  # once for each control: then it's known
            if self.cockpit.believes(p.reads):
                out.append(self._say(t, f"{what} didn't take, check it.", "alert"))
            else:
                out.append(self._say(t, f"{what} sent, but I can't see it on this aircraft. Check it.", "alert"))
        return out

    def _back_from_pause(self, t: float) -> list[Any]:
        """Back after a long pause: where things stand, in a sentence or two (what ATC said meanwhile, what's waiting)."""
        m = self.deck.memory
        paused_at, m.paused_at = m.paused_at, None
        if paused_at is None or t - paused_at < PAUSE_SUMMARY_S or self.monitor.verbosity == "quiet":
            return []
        f = facts(self.picture)
        parts = []
        if "altitude" in f and self.cockpit.airborne:
            parts.append(f"{f['altitude']}, heading {f.get('heading', '')}".rstrip(", heading "))
        if "cleared altitude" in f and self.cockpit.airborne:
            parts.append(f"cleared {f['cleared altitude']}")
        if "talking to" in f:
            parts.append(f"with {f['talking to'].split(' on ')[0]}")
        summary = ", ".join(parts)
        text = "Welcome back. " + (summary[:1].upper() + summary[1:] + "." if parts else "")
        if m.paused_atc:
            text += f" Meanwhile {m.paused_atc[-1].split(': ', 1)[0]} said: {m.paused_atc[-1].split(': ', 1)[-1]}"
        if self.intent is not None:
            text += f" Still waiting on your answer: {self.intent.question}"
        m.paused_atc = []
        return [self._say(t, text.strip(), "callout")]

    # --- what ATC knows --------------------------------------------------------------------------------------------

    def _read_clearance(self) -> None:
        if self.engine is None:
            return
        # Fuel in kilograms, the way airlines outside the US count it.
        self.picture.metric = regions.region_for(self.engine.state.flight.origin) is not regions.FAA
        a = self.engine.state.assignments
        self.cockpit.cleared_altitude_ft = a.altitude_ft
        self.cockpit.assigned_squawk = a.squawk

    def _station(self, call: str = "") -> str:
        """Who a call goes to: ground for the pushback and taxi, clearance, tower; else whoever's tuned."""
        if self.engine is None:
            return ""
        controller = {"pushback": "ground", "taxi": "ground", "clearance": "clearance", "departure": "tower"}.get(call)
        facility = self.engine.facility(controller) if controller and hasattr(self.engine, "facility") else None
        tuned = self.engine.state.comms.tuned
        return (facility or tuned).station if (facility or tuned) is not None else ""

    def _radio_call(self, text: str) -> bool:
        """Words that are a radio call, not a word to the copilot: a station's name, or the callsign first."""
        lowered = text.lower()
        stations = [f.station.lower() for f in getattr(self.engine, "facilities", [])] if self.engine is not None else []
        if any(s and s in lowered for s in stations):
            return True
        first = lowered.replace(",", " ").split()[:4]
        if any(w in STATION_WORDS for w in first) and len(lowered.split()) >= 4:
            return True
        callsign = self.engine.state.flight.callsign if self.engine is not None else None
        telephony = getattr(callsign, "telephony", "") or ""
        return bool(telephony) and lowered.startswith(telephony.lower())

    def _say(self, t: float, text: str, kind: str = "reply", spoken: str = "") -> CrewSpeech:
        if self.model is not None and kind != "reply":  # its replies the model remembers itself
            self.model.heard("You", text)
        return self._line(CrewSpeech(t=t, text=text, spoken=spoken, kind=kind))

    def _line(self, line: CrewSpeech) -> CrewSpeech:
        self._last_line = line
        self.deck.said(line.t, "copilot", "intercom", line.text)
        return line

    # --- what the model remembers of the flight deck ----------------------------------------------------------------

    def _note(self, t: float, what: str) -> None:
        if self.model is not None:
            self.model.event(t, what)

    def _note_own(self, own: OwnshipState) -> None:
        was = self.cockpit.own
        if was is None:
            return
        if was.parking_brake != own.parking_brake:
            self._note(own.t, "parking brake set" if own.parking_brake else "parking brake released")
        if was.on_ground and not own.on_ground:
            self._note(own.t, "airborne")
        elif not was.on_ground and own.on_ground:
            self._note(own.t, "touched down")
        if was.gear_down != own.gear_down:
            self._note(own.t, "gear down" if own.gear_down else "gear up")
        if was.flaps_index != own.flaps_index:
            self._note(own.t, f"flaps to {self.profile.detent_name(own.flaps_index, self.cockpit.flap_positions, on_ground=own.on_ground)}")

    def _note_systems(self, s: AircraftSystems) -> None:
        was = self.cockpit.systems
        if was is None:
            return
        if s.engines_running != was.engines_running:
            self._note(s.t, f"{s.engines_running} engine{'s' if s.engines_running != 1 else ''} running")
        if s.ap_master != was.ap_master:
            self._note(s.t, "autopilot on" if s.ap_master else "autopilot off")


REWORDED = ("greeting", "summary", "brief:", "status", "tod", "fuel_check", "divert", "step:", "clear")  # its own talk


def _said(cmd: Command) -> str:
    """A command as the copilot says it back: "heading 240", "flaps 2", "gear down", "landing lights on"."""
    a, v = cmd.action, cmd.value
    if a in SAID_AS:
        return SAID_AS[a]
    if a == "light":
        return f"{cmd.target} lights {v}" if cmd.target not in ("strobe", "beacon", "logo") else f"{cmd.target} {v}"
    if a == "altimeter":
        return f"QNH {v}" if cmd.target == "hpa" else ("standard" if v == "29.92" else f"altimeter {v}")
    if a == "ap_mode":
        return f"{cmd.target} mode"
    if a == "parking_brake":
        return "parking brake " + ("set" if v == "on" else "off")
    if a in ("com_active", "com_standby"):
        return f"{'standby ' if a == 'com_standby' else ''}{v}"
    if a == "vs":
        return f"vertical speed {v}"
    if a == "radio":
        return _radio_words(v, "").lower()
    return f"{a.replace('_', ' ')} {v}".strip()


def _radio_words(call: str, station: str) -> str:
    to = f" {station}" if station else ""
    return {"pushback": f"ask{to or ' ground'} for push and start", "taxi": f"ask{to or ' ground'} for taxi",
            "clearance": f"ask{to or ' clearance'} for the IFR clearance",
            "departure": f"tell{to or ' tower'} we're ready"}.get(call, call)


def _seed(engine: Any) -> int:
    """The copilot's wording varies, the same way each time for the same flight (replays, tests)."""
    callsign = getattr(getattr(getattr(engine, "state", None), "flight", None), "callsign", None)
    return sum(map(ord, str(callsign))) if callsign else 0


def _describe(command: SimCommand) -> str:
    name = getattr(command, "name", "") or type(command).__name__
    value = getattr(command, "value", None)
    if value is None:
        value = getattr(command, "hz", None)
    return f"{name} {value}" if value not in (None, 0) else name


__all__ = ["CHECK_S", "CONFIRM_S", "CopilotEvent", "Intent", "PilotMonitoring"]
