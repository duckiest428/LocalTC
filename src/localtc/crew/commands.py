"""What the pilot asks the copilot to do, read with a fixed grammar: no language model, nothing guessed.

"gear down", "flaps three", "flaps one plus F", "landing lights on", "arm the spoilers", "autopilot on",
"approach mode", "set heading two seven zero", "altitude one zero thousand", "flight level two four zero",
"speed two fifty", "vertical speed minus one thousand five hundred", "squawk four five five three",
"tune one two one point niner", "standby one one eight seven", "swap", "altimeter two niner niner two",
"QNH one zero one three", "standard", "parking brake set". Several in one breath ("gear down, flaps three") are
read in order. "Confirm" / "negative" answer the copilot's question, and "how do you hear me" checks the intercom.
"""

from dataclasses import dataclass

from localtc.atc_core.readback.normalize import Token, normalize


@dataclass(frozen=True)
class Command:
    action: str  # gear, flaps, light, spoilers, autopilot, ap_mode, autothrottle, heading, altitude, speed, vs,
    #              squawk, com_active, com_standby, com_swap, altimeter, parking_brake, yes, no, check
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
YES = {"confirm", "confirmed", "affirm", "affirmative", "yes", "yeah", "correct", "go", "do"}
NO = {"negative", "no", "cancel", "disregard", "stop", "belay"}
CHECK = ("how do you hear", "how do you read", "do you read", "radio check", "intercom check", "you there",
         "can you hear")


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
    lowered = " ".join(text.lower().replace("/", " ").split())
    if any(phrase in lowered for phrase in CHECK):
        return [Command("check")]
    tokens = normalize(text.replace("/", " "))
    words = [t.text for t in tokens]
    if len(words) <= 3 and words and words[0] in YES:
        return [Command("yes")]
    if len(words) <= 3 and words and words[0] in NO:
        return [Command("no")]
    out: list[Command] = []
    i = 0
    while i < len(tokens):
        found, i = _at(tokens, i)
        if found is not None:
            out.append(found)
    return out


def _at(tokens: list[Token], i: int) -> tuple[Command | None, int]:  # noqa: C901 - one branch per command, flat
    """The command starting at token ``i`` (or None), and where to read on from."""
    w = tokens[i].text
    nxt = _word(tokens, i + 1)
    if w == "gear" and nxt in ("up", "down"):
        return Command("gear", nxt), i + 2
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
    if w in ("standard", "std") and (i == 0 or _word(tokens, i - 1) in ("set", "altimeter", "altimeters", "baro")):
        return Command("altimeter", "29.92"), i + 1
    if w == "parking" and nxt == "brake":
        state = _on_off(tokens, i - 2, i + 4)
        return (Command("parking_brake", state) if state else None), i + 2
    return None, i + 1
