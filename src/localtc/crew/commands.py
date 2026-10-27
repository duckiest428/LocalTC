"""What the pilot asks the copilot to do, read with a fixed grammar: no language model, nothing guessed.

"gear down", "flaps three", "flaps one plus F", "landing lights on", "arm the spoilers", "autopilot on",
"approach mode", "set heading two seven zero", "altitude one zero thousand", "flight level two four zero",
"speed two fifty", "vertical speed minus one thousand five hundred", "squawk four five five three",
"tune one two one point niner", "standby one one eight seven", "swap", "altimeter two niner niner two",
"QNH one zero one three", "standard", "parking brake set", "autobrake max". Several in one breath ("gear down, flaps
three") are read in order. "Request pushback" / "get us taxi" asks the copilot to make that call on the radio; "go
around" and "declare an emergency" are the captain's calls (the copilot confirms the emergency before saying it). "Confirm" / "negative" answer the copilot's question, and "how do you hear me" checks the intercom.
"Before takeoff checklist" (or "run the checklist": the one that's due) reads a checklist, "brief" / "approach
briefing" a briefing, "status" how the flight's going; "quiet please" / "keep me posted" / "normal callouts" set how
much the copilot says by itself.
"""

import re
from dataclasses import dataclass

from localtc.atc_core.readback.normalize import Token, normalize


@dataclass(frozen=True)
class Command:
    action: str  # gear, flaps, light, spoilers, autopilot, ap_mode, autothrottle, heading, altitude, speed, vs,
    #              squawk, com_active, com_standby, com_swap, altimeter, parking_brake, autobrake, yes, no, check,
    #              seatbelt (the sign), checklist, brief, status, verbosity, say_again, radio (a call to make: pushback, taxi,
    #              clearance, departure), go_around, emergency (mayday, pan), knob (an FCU knob pushed or pulled:
    #              value "push" / "pull", target "speed", "altitude", "heading")
    value: str = ""  # "down", "2", "on", "270", "10000", "-1500", "4553", "121.9", "29.92"
    target: str = ""  # which light, which autopilot mode; "hpa" for an altimeter setting in hectopascals

    def __str__(self) -> str:
        return " ".join(p for p in (self.action, self.target, self.value) if p)


ON = {"on": "on", "set": "on", "engage": "on", "engaged": "on", "arm": "on", "armed": "on",
      "off": "off", "release": "off", "released": "off", "disengage": "off", "disconnect": "off", "disarm": "off"}
LIGHTS = {"landing": "landing", "taxi": "taxi", "strobe": "strobe", "strobes": "strobe", "beacon": "beacon",
          "nav": "nav", "navigation": "nav", "position": "nav", "logo": "logo"}
AP_MODES = {"heading": "heading", "hdg": "heading", "nav": "nav", "lnav": "nav", "approach": "approach",
            "app": "approach", "appr": "approach", "loc": "approach", "altitude": "altitude", "alt": "altitude",
            "vertical": "vs", "vs": "vs", "level": "flc", "flch": "flc", "flc": "flc"}
# The last word of a statement that only reports or acknowledges ("flaps one, check", "gear down, three green").
ACK_WORDS = ("check", "checked", "set", "checks", "noted", "copy", "roger", "okay", "ok", "green")
YES = {"confirm", "confirmed", "affirm", "affirmative", "yes", "yeah", "correct", "go", "do"}
NO = {"negative", "no", "cancel", "disregard", "stop", "belay"}
FILLER = {"to", "the", "at", "please", "our", "my", "your", "for", "me", "us", "now", "new"}  # "set the altimeter to 30.10"
CHECK = ("how do you hear", "how do you read", "do you read", "radio check", "intercom check", "you there",
         "can you hear")
QUIET = ("quiet please", "be quiet", "less chatter", "talk less", "only the essentials", "keep it quiet", "go quiet",
         "sterile cockpit")
CHATTY = ("keep me posted", "talk more", "more chatter", "chatty", "chat more")
STANDARD = ("normal callouts", "standard callouts", "usual callouts", "back to normal")
STATUS = ("status report", "status update", "give me a status", "how are we doing", "how's it going", "hows it going",
          "how is it going", "status")
SAY_AGAIN = re.compile(r"^(?:\w+,?\s+)?(?:say again|what did you say|come again|repeat that|pardon|sorry\?|what was that)\b")
# Calls the pilot asks the copilot to make on the radio: "request pushback", "get us taxi", "ask for the clearance".
RADIO_ASK = r"(?:request|get us|get|ask for|call for|call (?:them )?for|ask (?:ground|tower|them) for)"
RADIO_REQUESTS = (
    (re.compile(rf"\b{RADIO_ASK} (?:a |the |our )?(?:push ?back|push and start|push|start ?up|startup)\b"), "pushback"),
    (re.compile(rf"\b{RADIO_ASK} (?:a |the |our )?taxi\b|\bcall (?:for )?taxi\b"), "taxi"),
    (re.compile(rf"\b{RADIO_ASK} (?:a |the |our )?(?:ifr |)clearance\b|\bget (?:us )?cleared\b"), "clearance"),
    (re.compile(rf"\b{RADIO_ASK} (?:a |the |our )?(?:takeoff|take off|departure)(?: clearance)?\b|\btell tower (?:we're|we are) ready\b"),
     "departure"),
)
WITH_CHECKLIST_NOT = ("checklist", "yes", "no", "check", "say_again", "verbosity", "status", "brief", "radio")
# "Ask clearance for a radio check", "go on the radio and ask ATC for a temperature update": asked on the radio.
ASK_ATC = re.compile(r"\b(?:ask|check with|request from) (?:the )?(?:atc|air traffic control|controller|clearance|delivery|"
                     r"ground|tower|approach|departure|cent(?:er|re)|them)\b(?: (?:on the radio|please))?"
                     r"(?: (?=for |what |how |when |where |if |whether |which |about )| to (?:give us |tell us |say )?)"
                     r"(?P<what>.+)")
# "Can you do the call?", "make the call to ground": which call by the words around it, else the next one due.
RADIO_ANY = re.compile(r"\b(?:do|make|give|handle|take) (?:the |a |them a |ground a |tower a )?(?:radio )?call\b|"
                       r"\b(?:call|talk to|speak to) (?:ground|tower|clearance|delivery) (?:for (?:us|me)|please)\b")
RADIO_WHICH = ((re.compile(r"\bpush"), "pushback"), (re.compile(r"\btaxi"), "taxi"),
               (re.compile(r"\bclearance\b|\bifr\b|\bcleared\b"), "clearance"),
               (re.compile(r"\btake ?off\b|\bdeparture\b|\bready\b"), "departure"))
AUTOBRAKE_LEVELS = {"off": "off", "disarm": "off", "low": "low", "lo": "low", "medium": "medium", "med": "medium",
                    "high": "max", "hi": "max", "max": "max", "maximum": "max", "rto": "max", "on": "", "arm": "",
                    "armed": "", "set": "", "one": "1", "two": "2", "three": "3", "four": "4"}


KNOB_TARGETS = {"speed": "speed", "altitude": "altitude", "descent": "altitude", "climb": "altitude", "des": "altitude",
                "clb": "altitude", "heading": "heading", "nav": "heading"}


def _knob(tokens, i: int):
    """An FCU knob pushed or pulled, the way it's asked for: "speed managed", "managed speed please", "set the speed as
    managed", "selected speed", "pull the speed"; "open descent", "op des", "pull the altitude", "managed descent",
    "push the altitude"; "pull heading". (Command, the next token) or None."""
    w = _word(tokens, i)
    if w not in ("managed", "selected", "push", "pull", "open", "op"):
        return None
    near = [_word(tokens, j) for j in range(max(0, i - 4), i + 4)]
    target = next((KNOB_TARGETS[x] for x in near if x in KNOB_TARGETS), None)
    if target is None:
        return None
    if w in ("open", "op") and target != "altitude":
        return None
    value = "push" if w in ("managed", "push") else "pull"
    return Command("knob", value, target), i + 1


def _number(tokens: list[Token], i: int) -> float | None:
    if i < len(tokens) and tokens[i].kind == "number":
        try:
            return float(tokens[i].text)
        except ValueError:
            return None
    return None


def _word(tokens: list[Token], i: int) -> str:
    return tokens[i].text if 0 <= i < len(tokens) else ""


def _on_off(tokens: list[Token], start: int, end: int) -> str:
    """The on/off word near a switch: "landing lights on", "turn on the landing lights", "engage autopilot"."""
    for j in range(max(0, start), min(len(tokens), end)):
        if tokens[j].text in ON:
            return ON[tokens[j].text]
    return ""


def parse(text: str) -> list[Command]:
    """Every command in ``text``, in order; [] when it isn't one (a question, chat, a radio call)."""
    # "Q and H", "Q N H", "auto brake": speech-to-text's spellings, one word each.
    text = re.sub(r"\b(?:q|queue|cue)[\s.]*(?:and|n|en)[\s.]*h\b", "qnh", text, flags=re.IGNORECASE)
    text = re.sub(r"\bauto[\s-]?brakes?\b", "autobrake", text, flags=re.IGNORECASE)
    text = re.sub(r"\bseat[\s-]?belts?(?:\s+signs?)?\b", "seatbelt", text, flags=re.IGNORECASE)
    # speech-to-text: "seapot signs off", "seat pelt sign"
    text = re.sub(r"\b(?:sea\s?pots?|sea\s?pelts?|seat\s?pelts?|seat\s?bolts?)(?:\s+signs?)?\b", "seatbelt", text,
                  flags=re.IGNORECASE)
    text = re.sub(r"\bparking (?:bakes?|breaks?|brakes|break)\b", "parking brake", text, flags=re.IGNORECASE)
    lowered = " ".join(text.lower().replace("/", " ").split())
    if (asked := ASK_ATC.search(lowered)) is not None and asked.group("what").strip(" ?.!,"):
        # "Can you ask clearance for a radio check?": the copilot asks on the radio (not an intercom check).
        return [Command("radio", "ask", asked.group("what").strip(" ?.!,"))]
    if any(phrase in lowered for phrase in CHECK):
        return [Command("check")]
    if SAY_AGAIN.search(lowered.strip(" ?!.")) and len(lowered.split()) <= 5:
        return [Command("say_again")]
    for pattern, call in RADIO_REQUESTS:
        if pattern.search(lowered):
            return [Command("radio", call)]
    if RADIO_ANY.search(lowered):
        return [Command("radio", next((call for pattern, call in RADIO_WHICH if pattern.search(lowered)), "next"))]
    if re.search(r"\b(?:declare (?:an )?emergency|mayday|pan[ -]?pan)\b", lowered):
        return [Command("emergency", "pan" if re.search(r"\bpan[ -]?pan\b", lowered) else "mayday")]
    if re.search(r"\bgo[ -]?around\b|\bgoing around\b", lowered):
        return [Command("go_around")]
    if re.search(r"\bpositive (?:rate|climb)\b", lowered) and "gear" not in lowered:
        return [Command("gear", "up")]  # "positive rate": the gear's called up straight after it, and put up
    for phrases, level in ((QUIET, "quiet"), (CHATTY, "chatty"), (STANDARD, "standard")):
        if any(phrase in lowered for phrase in phrases):
            return [Command("verbosity", level)]
    if "checklist" in lowered or "check list" in lowered:
        from localtc.crew.checklists import named  # checklists builds on this module

        # "Put down flaps 3 and let's do the approach checklist": what's asked for with it too, first (only the
        # checklist was run).
        controls = [c for part in re.split(r"\b(?:and|then)\b|[,.;]", lowered)
                    if part.strip() and "checklist" not in part and "check list" not in part
                    for c in parse(part) if c.action not in WITH_CHECKLIST_NOT]
        return [*controls, Command("checklist", named(lowered) or "")]
    if re.search(r"\bbrief(?:ing)?\b", lowered):
        which = "departure" if re.search(r"departure|takeoff|take off", lowered) else \
            "approach" if re.search(r"approach|arrival|landing", lowered) else ""
        return [Command("brief", which)]
    if any(re.search(rf"\b{re.escape(phrase)}\b", lowered) for phrase in STATUS) and len(lowered.split()) <= 6:
        return [Command("status")]
    tokens = [t for t in normalize(text.replace("/", " ")) if t.text not in FILLER or t.kind == "number"]
    words = [t.text for t in tokens]
    if len(words) <= 3 and words and words[0] in YES:
        return [Command("yes")]
    if len(words) <= 3 and words and words[0] in NO:
        return [Command("no")]
    out: list[Command] = []
    i = 0
    while i < len(tokens):
        found, i = _at(tokens, i)
        if found is not None and (not out or out[-1] != found):  # "set QNH 30.18, the altimeter to 30.18": once
            out.append(found)
    return out


def _at(tokens: list[Token], i: int) -> tuple[Command | None, int]:  # noqa: C901 - one branch per command, flat
    """The command starting at token ``i`` (or None), and where to read on from."""
    w = tokens[i].text
    nxt = _word(tokens, i + 1)
    if w == "gear" and nxt in ("up", "down"):
        return Command("gear", nxt), i + 2
    if w in ("put", "bring", "raise", "retract") and (nxt in ("flaps", "flap") or (nxt == "up" and _word(tokens, i + 2) in ("flaps", "flap"))
                                                       or (w in ("raise", "retract") and nxt == "the" and _word(tokens, i + 2) in ("flaps", "flap"))):
        # "put up flaps", "raise the flaps", "retract flaps": all the way up ("put flaps two" is read below)
        j = i + (2 if nxt in ("flaps", "flap") else 3)
        if w in ("raise", "retract") or nxt == "up" or _word(tokens, j) == "up":
            return Command("flaps", "up"), j + (1 if _word(tokens, j) == "up" else 0)
        return None, i + 1
    if w in ("flaps", "flap"):
        if nxt in ("up", "full", "zero"):
            return Command("flaps", "up" if nxt == "zero" else nxt), i + 2
        if (n := _number(tokens, i + 1)) is not None:
            value = str(int(n))
            if _word(tokens, i + 2) == "plus" and _word(tokens, i + 3) in ("f", "foxtrot"):
                return Command("flaps", f"{value}+f"), i + 4
            return Command("flaps", value), i + 2
        return None, i + 1
    if w in LIGHTS and (nxt in ("light", "lights") or w in ("strobe", "strobes", "beacon", "logo")):
        named = 2 if nxt in ("light", "lights") else 1
        state = _on_off(tokens, i - 2, i + named + 2)
        return (Command("light", state, LIGHTS[w]) if state else None), i + named
    if (knob := _knob(tokens, i)) is not None:
        return knob
    if w in ("spoilers", "spoiler", "speedbrakes", "speedbrake", "speed") and (w != "speed" or nxt in ("brake", "brakes")):
        j = i + (2 if w == "speed" else 1)
        near = [t.text for t in tokens[max(0, i - 2):j + 2]]
        if "disarm" in near:
            return Command("spoilers", "disarm"), j + 1
        if "arm" in near or "armed" in near:
            return Command("spoilers", "arm"), j + 1
        if any(x in near for x in ("extend", "out", "deploy", "extended")):
            return Command("spoilers", "extend"), j + 1
        if any(x in near for x in ("retract", "in", "stow", "retracted", "stowed")):
            return Command("spoilers", "retract"), j + 1
        if w == "speed":  # "speed" alone is a speed setting, read below
            pass
        else:
            return None, j
    if w in ("autopilot", "ap") and (state := _on_off(tokens, i - 1, i + 3)):
        return Command("autopilot", state), i + 2
    if w in ("autothrottle", "autothrust", "athr") and (state := _on_off(tokens, i - 1, i + 3)):
        return Command("autothrottle", state), i + 2
    if w in AP_MODES and (nxt in ("mode", "hold") or (w == "vertical" and nxt == "speed" and _word(tokens, i + 2) == "mode")
                          or w in ("lnav", "appr", "flch", "flc")
                          or (w == "level" and nxt == "change")):
        if w == "altitude" and nxt == "hold":
            return Command("ap_mode", "on", "altitude"), i + 2
        if w != "altitude" or nxt == "mode":
            return Command("ap_mode", "on", AP_MODES[w]), i + (3 if w == "vertical" else 2)
    if w == "arm" and nxt in ("approach", "app", "appr", "loc"):
        return Command("ap_mode", "on", "approach"), i + 2
    if w == "heading" and (n := _number(tokens, i + 1 if nxt != "bug" else i + 2)) is not None:
        return Command("heading", str(int(n) % 360 or 360)), i + (2 if nxt != "bug" else 3)
    if w == "flight" and nxt == "level" and (n := _number(tokens, i + 2)) is not None:
        return Command("altitude", str(int(n) * 100)), i + 3
    if w in ("altitude", "climb", "descend") and (n := _number(tokens, i + 1)) is not None:
        return Command("altitude", str(int(n))), i + 2
    if w in ("climb", "descend") and nxt in ("to", "and") and (n := _number(tokens, i + 2)) is not None:
        return Command("altitude", str(int(n))), i + 3
    if w == "vertical" and nxt == "speed":
        j, sign = i + 2, 1
        if _word(tokens, j) in ("minus", "negative", "down"):
            j, sign = j + 1, -1
        elif _word(tokens, j) in ("plus", "up"):
            j += 1
        if (n := _number(tokens, j)) is not None:
            return Command("vs", str(sign * int(n))), j + 1
        return None, i + 2
    if w == "speed" and (n := _number(tokens, i + 1)) is not None:
        return Command("speed", str(int(n))), i + 2
    if w in ("squawk", "transponder", "code") and (n := _number(tokens, i + 1)) is not None:
        code = tokens[i + 1].text.split(".")[0]
        if len(code) == 4 and all(c in "01234567" for c in code):
            return Command("squawk", code), i + 2
        return None, i + 2
    if w in ("tune", "frequency", "active") and (n := _number(tokens, i + 1)) is not None and 118 <= n < 137:
        return Command("com_active", f"{n:.3f}".rstrip("0").rstrip(".")), i + 2
    if w == "standby" and (n := _number(tokens, i + 1)) is not None and 118 <= n < 137:
        return Command("com_standby", f"{n:.3f}".rstrip("0").rstrip(".")), i + 2
    if w in ("swap", "flip") and (nxt in ("", "flop", "frequencies", "radios", "com", "it") or w == "swap"):
        return Command("com_swap"), i + (2 if nxt in ("flop", "frequencies", "radios", "com", "it") else 1)
    if w in ("altimeter", "qnh", "baro") and (n := _number(tokens, i + 1)) is not None:
        raw = tokens[i + 1].text
        if 900 <= n <= 1070:  # hectopascals, kept as said
            return Command("altimeter", str(int(n)), "hpa"), i + 2
        inhg = n / 100 if "." not in raw and 2700 <= n <= 3150 else n if 27 <= n <= 31.5 else None
        return (Command("altimeter", f"{inhg:.2f}") if inhg else None), i + 2
    # "standard", "set standard", "put standard altimeter", "enable STD on your altimeter"
    if w in ("standard", "std") and (i == 0 or _word(tokens, i - 1) in ("set", "altimeter", "altimeters", "baro", "put",
                                                                         "enable", "go", "select")
                                     or {_word(tokens, i + 1), _word(tokens, i + 2), _word(tokens, i + 3)}
                                     & {"altimeter", "altimeters", "baro", "setting"}):
        return Command("altimeter", "29.92"), i + 1
    if w in ("minimums", "minimum", "minima", "mins", "da", "dh", "mda") or (w == "decision" and nxt in ("altitude", "height")):
        # "minimums 280", "set minimums to 650", "decision altitude 380": the chart's, for the call
        for j in range(i + 1, min(len(tokens), i + 5)):
            if (n := _number(tokens, j)) is not None and 50 <= n <= 15000:
                return Command("minimums", str(int(n))), j + 1
        return None, i + 1
    if w == "apu":  # "start the APU", "APU bleed on", "APU off"
        if nxt == "bleed":
            return Command("apu_bleed", _on_off(tokens, i - 3, i + 4) or "on"), i + 2
        stop = {_word(tokens, j) for j in range(max(0, i - 3), min(len(tokens), i + 3))} & {"stop", "shut", "shutdown", "off"}
        return Command("apu", "stop" if stop else "start"), i + 1
    if w == "seatbelt":  # "seatbelt sign on", "turn on the seatbelts" (no on or off: on)
        return Command("seatbelt", _on_off(tokens, i - 3, i + 3) or "on"), i + 1
    if w == "parking" and nxt in ("brake", "brakes"):
        state = _on_off(tokens, i - 2, i + 4)
        return (Command("parking_brake", state) if state else None), i + 2
    if w == "autobrake":
        # "autobrake max", "autobrake to low", "set autobrake three", "turn on the autobrake" (no setting: asked)
        near = [*range(i + 1, min(len(tokens), i + 4)), *range(max(0, i - 2), i)]  # after it first, then before
        for j in near:
            word = tokens[j].text
            if AUTOBRAKE_LEVELS.get(word):
                return Command("autobrake", AUTOBRAKE_LEVELS[word]), max(i, j) + 1
            if j > i and tokens[j].kind == "number" and word in ("1", "2", "3", "4", "5", "6"):
                return Command("autobrake", word), j + 1
        if any(tokens[j].text in AUTOBRAKE_LEVELS or tokens[j].text == "turn" for j in near):
            return Command("autobrake", ""), i + 1  # "turn on the autobrake": which setting is asked
        return None, i + 1
    return None, i + 1
