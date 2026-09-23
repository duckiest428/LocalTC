"""Loads, validates and renders the TOML phraseology templates."""

import random
import string
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import msgspec

from localtc.atc_core.phraseology.slots import SLOT_DEFAULTS, SLOTS
from localtc.atc_core.readback.extract import ELEMENTS
from localtc.atc_core.values import Phrase


class ReadbackSpec(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    required: list[str] = []
    optional: list[str] = []


class Template(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    id: str
    controller: str  # clearance, ground, tower, departure, center, approach, or "any"
    text: list[str]
    readback: ReadbackSpec = msgspec.field(default_factory=ReadbackSpec)
    pilot_readback: str = ""  # an ideal pilot readback; used by tests and scripted pilots
    ack: Literal["none", "readback_correct"] = "none"


class TemplateFile(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    template: list[Template] = []
    fragments: dict[str, str] = {}


class Rewording(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    id: str
    text: list[str] = []
    pilot_readback: str = ""


class OverlayFile(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    template: list[Rewording] = []
    fragments: dict[str, str] = {}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Rendered:
    instruction_id: str
    controller: str
    text: str
    spoken: str
    expected: dict[str, Any] = field(default_factory=dict)  # readback element -> value
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    ack: str = "none"


_formatter = string.Formatter()


def slot_names(text: str) -> list[str]:
    return [name for _, name, _, _ in _formatter.parse(text) if name]


class TemplateLibrary:
    def __init__(self, templates: dict[str, Template], fragments: dict[str, str], style: str = "faa") -> None:
        self.templates = templates
        self.fragments = fragments
        self.style = style
        self.validate()

    @classmethod
    def load(cls, *extra_dirs: str | Path, style: str = "faa") -> "TemplateLibrary":
        """Built-in templates plus any ``*.toml`` in ``extra_dirs`` (later files override ids).

        ``style="icao"`` lays ``templates/icao/*.toml`` over the FAA templates: the same ids, reworded. An
        ICAO template may change ``text`` and ``pilot_readback`` only; what must be read back is the same
        in both, so the engine and the readback checks don't depend on the style."""
        files: list[tuple[str, bytes]] = []
        package = resources.files("localtc.atc_core.phraseology") / "templates"
        for entry in sorted(package.iterdir(), key=lambda e: e.name):
            if entry.name.endswith(".toml"):
                files.append((entry.name, entry.read_bytes()))
        overlay: list[tuple[str, bytes]] = []
        if style == "icao":
            for entry in sorted((package / "icao").iterdir(), key=lambda e: e.name):
                if entry.name.endswith(".toml"):
                    overlay.append((f"icao/{entry.name}", entry.read_bytes()))
        for directory in extra_dirs:
            for path in sorted(Path(directory).glob("*.toml")):
                files.append((str(path), path.read_bytes()))
        templates: dict[str, Template] = {}
        fragments: dict[str, str] = {}
        for name, data in files:
            try:
                parsed = msgspec.convert(tomllib.loads(data.decode()), TemplateFile)
            except (tomllib.TOMLDecodeError, msgspec.ValidationError) as exc:
                raise TemplateError(f"{name}: {exc}") from exc
            for template in parsed.template:
                templates[template.id] = template
            fragments.update(parsed.fragments)
        for name, data in overlay:
            try:
                parsed = msgspec.convert(tomllib.loads(data.decode()), OverlayFile)
            except (tomllib.TOMLDecodeError, msgspec.ValidationError) as exc:
                raise TemplateError(f"{name}: {exc}") from exc
            for change in parsed.template:
                if change.id not in templates:
                    raise TemplateError(f"{name}: {change.id} rewords a template that doesn't exist")
                base = templates[change.id]
                templates[change.id] = msgspec.structs.replace(
                    base, text=change.text or base.text, pilot_readback=change.pilot_readback or base.pilot_readback)
            fragments.update(parsed.fragments)
        return cls(templates, fragments, style=style)

    def validate(self) -> None:
        problems = []
        for template in self.templates.values():
            texts = [*template.text, template.pilot_readback]
            if not template.text:
                problems.append(f"{template.id}: no text")
            for text in texts:
                for name in slot_names(text):
                    if name not in SLOTS and name != "callsign_short":
                        problems.append(f"{template.id}: unknown slot {{{name}}}")
            for element in (*template.readback.required, *template.readback.optional):
                if element not in ELEMENTS:
                    problems.append(f"{template.id}: no readback extractor for {element!r}")
            if (template.readback.required or template.readback.optional) and not template.pilot_readback:
                problems.append(f"{template.id}: expects a readback but has no pilot_readback example")
        for key, text in self.fragments.items():
            for name in slot_names(text):
                if name not in SLOTS:
                    problems.append(f"fragment {key}: unknown slot {{{name}}}")
        if problems:
            raise TemplateError("invalid templates:\n  " + "\n  ".join(problems))

    def get(self, instruction_id: str) -> Template:
        try:
            return self.templates[instruction_id]
        except KeyError:
            raise TemplateError(f"no template {instruction_id!r}") from None

    def render(
        self,
        instruction_id: str,
        slots: dict[str, Any],
        *,
        rng: random.Random | None = None,
        controller: str | None = None,
    ) -> Rendered:
        template = self.get(instruction_id)
        text = template.text[0] if rng is None or len(template.text) == 1 else rng.choice(template.text)
        display, spoken = self.fill(text, slots, context=instruction_id)
        elements = (*template.readback.required, *template.readback.optional)
        return Rendered(
            instruction_id=template.id,
            controller=controller or template.controller,
            text=display,
            spoken=spoken,
            expected={e: slots[e] for e in elements if e in slots} | {e: True for e in elements if e not in slots},
            required=tuple(template.readback.required),
            optional=tuple(template.readback.optional),
            ack=template.ack,
        )

    def fragment(self, key: str, slots: dict[str, Any]) -> Phrase:
        if key not in self.fragments:
            raise TemplateError(f"no fragment {key!r}")
        return Phrase(*self.fill(self.fragments[key], slots, context=f"fragment {key}"))

    def pilot_readback(self, instruction_id: str, slots: dict[str, Any]) -> str:
        """The template's ideal readback, in spoken words (as a speech recognizer would hear it)."""
        return self.fill(self.get(instruction_id).pilot_readback, slots, context=instruction_id)[1]

    @staticmethod
    def fill(text: str, slots: dict[str, Any], *, context: str = "") -> tuple[str, str]:
        display: dict[str, str] = {}
        spoken: dict[str, str] = {}
        for name in slot_names(text):
            if name == "callsign_short":
                slot_type, value = SLOTS["callsign"], slots.get("callsign")
                value = value.short if value is not None else None
            else:
                slot_type, value = SLOTS[name], slots.get(name, SLOT_DEFAULTS.get(name))
            if value is None:
                raise TemplateError(f"{context}: missing slot {{{name}}}")
            if not isinstance(value, slot_type.value_type):
                raise TemplateError(f"{context}: slot {{{name}}} expects {slot_type.value_type}, got {type(value).__name__}")
            display[name] = slot_type.display(value)
            spoken[name] = slot_type.spoken(value)
        return text.format_map(display), _spoken_sentence(text.format_map(spoken))


def _spoken_sentence(text: str) -> str:
    return " ".join(text.replace("\n", " ").split())
