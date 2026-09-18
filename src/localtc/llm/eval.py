"""Grading the understanding path on the seeded edge cases in ``eval_cases.toml``.

``localtc llm eval`` runs them against the live model; the test suite runs them with each
case's scripted ``model`` answer (``CorpusBackend``). Either way the grade is on the final
interpretation, after the grammar and the checks, since that's what ATC acts on.
"""

import json
import re
import statistics
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from localtc.atc_core.llm import LlmInterpreter, LlmReply, LlmRequest
from localtc.atc_core.phraseology import TemplateLibrary
from localtc.atc_core.readback import Interpretation, InterpretContext, PendingReadback
from localtc.atc_core.values import Approach, Callsign, Wind

INT_SLOTS = {"altitude", "cruise", "heading"}
PILOT_RE = re.compile(r'^Pilot: "(.*)"$', re.MULTILINE)


@dataclass(frozen=True)
class Case:
    name: str
    pilot: str
    phase: str
    station: str
    expect: dict[str, Any]
    model: dict[str, Any] = field(default_factory=dict)
    instruction: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    atc: str = ""
    callsign: str = "DP69"


def load_cases(path: str | Path | None = None) -> list[Case]:
    source = Path(path) if path else resources.files("localtc.llm") / "eval_cases.toml"
    text = source.read_text(encoding="utf-8")  # not the Windows default code page
    return [Case(**c) for c in tomllib.loads(text)["case"]]


def typed_slots(slots: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, value in slots.items():
        if name == "frequency":
            out[name] = float(value)
        elif name in INT_SLOTS:
            out[name] = int(value)
        elif name == "taxi_route":
            out[name] = tuple(value)
        elif name == "approach":
            kind, runway = str(value).split()
            out[name] = Approach(kind, runway)
        elif name == "wind":
            direction, speed = str(value).split("@")
            out[name] = Wind(int(direction), int(speed))
        else:
            out[name] = value
    return out


def setup(case: Case, library: TemplateLibrary) -> tuple[PendingReadback | None, InterpretContext]:
    callsign = Callsign(case.callsign)
    atc = case.atc
    pending = None
    if case.instruction:
        rendered = library.render(case.instruction, {**typed_slots(case.slots), "callsign": callsign})
        pending = PendingReadback(case.instruction, rendered.controller, rendered.expected, rendered.required,
                                  rendered.optional)
        atc = atc or rendered.text
    context = InterpretContext(callsign=callsign, phase=case.phase, station=case.station, last_atc=atc or None)
    return pending, context


def grade(result: Interpretation, expect: dict[str, Any]) -> list[str]:
    problems = []
    if "kind" in expect and result.kind != expect["kind"]:
        problems.append(f"kind {result.kind} != {expect['kind']}")
    if "not_kind" in expect and result.kind == expect["not_kind"]:
        problems.append(f"kind is {result.kind}")
    intents = expect.get("intent_any") or ([expect["intent"]] if "intent" in expect else None)
    if intents is not None and result.intent not in intents:
        problems.append(f"intent {result.intent} not in {intents}")
    if "not_intent" in expect and result.intent == expect["not_intent"]:
        problems.append(f"intent is {result.intent}")
    if "status" in expect and result.status != expect["status"]:
        problems.append(f"status {result.status} != {expect['status']}")
    if "missing" in expect and sorted(result.missing) != sorted(expect["missing"]):
        problems.append(f"missing {list(result.missing)} != {expect['missing']}")
    if "mismatched" in expect and sorted(result.mismatched) != sorted(expect["mismatched"]):
        problems.append(f"mismatched {list(result.mismatched)} != {expect['mismatched']}")
    for key, value in expect.get("values", {}).items():
        if str(result.values.get(key)) != str(value):
            problems.append(f"{key} {result.values.get(key)!r} != {value!r}")
    return problems


@dataclass
class CaseResult:
    case: Case
    result: Interpretation
    problems: list[str]

    @property
    def passed(self) -> bool:
        return not self.problems

    @property
    def model_used(self) -> bool:
        return any(e.outcome == "used" for e in self.result.exchanges)

    @property
    def latency_ms(self) -> float:
        return sum(e.latency_ms for e in self.result.exchanges)


def run_cases(interpreter: LlmInterpreter, cases: list[Case]) -> list[CaseResult]:
    library = TemplateLibrary.load()
    results = []
    for case in cases:
        pending, context = setup(case, library)
        result = interpreter.interpret(case.pilot, pending, context)
        results.append(CaseResult(case, result, grade(result, case.expect)))
    return results


def format_results(results: list[CaseResult]) -> str:
    lines = []
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        source = "model" if r.model_used else "grammar"
        lines.append(f"{mark}  {r.latency_ms:6.0f} ms  {source:<7}  {r.case.name}")
        if not r.passed:
            lines.append(f"        heard: {r.case.pilot!r}")
            lines.append(f"        got:   {r.result.kind} {r.result.intent} {r.result.status} {dict(r.result.values)}")
            lines.append(f"        wrong: {'; '.join(r.problems)}")
            for e in r.result.exchanges:
                lines.append(f"        model: {e.outcome} {e.response} {e.detail}".rstrip())
    passed = sum(r.passed for r in results)
    latencies = sorted(r.latency_ms for r in results if r.result.exchanges)
    summary = f"\n{passed}/{len(results)} passed"
    if latencies:
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        summary += f"; model latency median {statistics.median(latencies):.0f} ms, p95 {p95:.0f} ms"
    summary += f"; model answer used in {sum(r.model_used for r in results)}/{len(results)}"
    return "\n".join(lines) + summary


class CorpusBackend:
    """Answers every case with its scripted ``model`` answer: the offline stand-in for a real model."""

    model = "scripted"

    def __init__(self, cases: list[Case]) -> None:
        self.by_pilot = {c.pilot: c.model for c in cases}
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest, *, timeout_s: float) -> LlmReply:
        self.requests.append(request)
        match = PILOT_RE.search(request.prompt)
        answer = self.by_pilot.get(match.group(1)) if match else None
        if answer is None:
            return LlmReply(None, error="error: no scripted answer")
        return LlmReply(json.dumps(fill_schema(answer, request.schema)), latency_ms=1.0)


def fill_schema(answer: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Every schema field present, as a constrained model would produce: "" or false when unsaid."""
    out = {}
    for name, spec in schema["properties"].items():
        out[name] = answer.get(name, False if spec.get("type") == "boolean" else "")
    return out
