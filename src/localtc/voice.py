"""Voice glue between the ATC engine and speech-to-text: the vocabulary for this flight, and
re-transcribing recorded push-to-talk audio (``localtc voice eval``).

Lives outside ``stt`` and ``atc_core`` because it needs both.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from localtc.atc_core.engine import AtcEngine
from localtc.atc_core.phraseology import speech
from localtc.atc_core.readback.normalize import normalize
from localtc.replay import Recording
from localtc.sim_api import SIM_EVENT_TYPES, BusEvent, PttPressed, PttReleased, Transcript
from localtc.stt.audio import read_wav
from localtc.stt.vocabulary import VocabularyHints, build_prompt, fixup, hotwords

PHONETIC_WORDS = {k: v.capitalize() for k, v in speech.PHONETIC.items()}


def spoken_callsign(ident: str) -> str:
    """ "DP69" -> "Delta Papa 69", the way a pilot says it and Whisper tends to write it."""
    out, digits = [], ""
    for ch in ident.upper():
        if not ch.isalnum():
            continue
        if ch.isdigit():
            digits += ch
            continue
        if digits:
            out.append(digits)
            digits = ""
        out.append(PHONETIC_WORDS.get(ch, ch))
    return " ".join(out + ([digits] if digits else []))


def flight_hints(engine: AtcEngine) -> VocabularyHints:
    """Names in play right now. Never the values of a pending readback (see stt.vocabulary)."""
    st = engine.state
    callsign = st.flight.callsign
    airports, runways = [], []
    for icao in (st.flight.origin, st.flight.destination):
        geometry = engine.geometry(icao)
        if geometry is not None:
            airports.append(speech.airport_name(geometry.airport.name, icao))
            runways += [end.ident for end in geometry.ends]
    here = engine.tracker.context.airport
    taxiways = sorted({p.name for p in here.airport.taxi_paths if p.name and p.name.isalnum()}) if here else []
    return VocabularyHints(
        callsign=speech.callsign_display(callsign) if callsign else "",
        callsign_spoken=(f"{callsign.telephony} {callsign.flight_number}" if callsign and callsign.is_airline
                         else spoken_callsign(callsign.ident) if callsign else ""),
        stations=tuple(dict.fromkeys(f.station for f in engine.facilities)),
        airports=tuple(dict.fromkeys(airports)),
        runways=tuple(dict.fromkeys(runways)),
        taxiways=tuple(taxiways[:20]),
    )


# --- re-transcribing a recording -------------------------------------------------------------------------


@dataclass
class Clip:
    pressed_t: float
    released_t: float
    audio: Path
    reference: str  # what was recorded: the original transcript (the script, for synthetic recordings)
    heard: str = ""
    confidence: float = 0.0
    stt_ms: float = 0.0


def recorded_clips(recording: Recording) -> list[Clip]:
    """Every push-to-talk transmission with audio, paired with its recorded transcript."""
    clips, pressed, released = [], None, None
    for event in recording.events():
        if isinstance(event, PttPressed):
            pressed = event.t
        elif isinstance(event, PttReleased):
            released = event.t
        elif isinstance(event, Transcript) and event.audio_ref:
            clips.append(Clip(pressed if pressed is not None else event.t, released or event.t,
                              recording.root / event.audio_ref, event.text))
            pressed = released = None
    return clips


def token_error_rate(reference: str, heard: str) -> float:
    """Word error rate after the parser's normalization, so "5,000" and "five thousand" agree."""
    ref = [str(t) for t in normalize(reference)]
    hyp = [str(t) for t in normalize(heard)]
    if not ref:
        return 0.0 if not hyp else 1.0
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1] / len(ref)


@dataclass
class VoiceRun:
    clips: list[Clip]
    outputs: list[BusEvent] = field(default_factory=list)


def transcribe_recording(recording: Recording, transcriber: Any, *, engine: AtcEngine | None = None,
                         vocabulary: bool = True) -> VoiceRun:
    """Play a recording's sim events into ``engine`` (optional) and re-transcribe each push-to-talk clip at the
    moment it was released, so the vocabulary hints see the flight as it was then."""
    clips = {c.released_t: c for c in recorded_clips(recording)}
    run = VoiceRun(list(clips.values()))
    for event in recording.events():
        if engine is not None and isinstance(event, (*SIM_EVENT_TYPES, PttPressed)):
            run.outputs += engine.handle(event)
        elif engine is not None and isinstance(event, Transcript) and not event.audio_ref:
            run.outputs += engine.handle(event)  # typed in a voice session
        elif engine is not None and isinstance(event, PttReleased) and event.t not in clips:
            run.outputs += engine.handle(event)
        if isinstance(event, PttReleased) and event.t in clips:
            clip = clips[event.t]
            hints = flight_hints(engine) if (engine is not None and vocabulary) else VocabularyHints()
            result = transcriber.transcribe(read_wav(clip.audio), prompt=build_prompt(hints) if vocabulary else "",
                                            hotwords=hotwords(hints) if vocabulary else "")
            clip.heard, clip.confidence, clip.stt_ms = fixup(result.text), result.confidence, result.latency_ms
            if engine is not None:
                run.outputs += engine.handle(event)
                run.outputs += engine.handle(Transcript(t=event.t, text=clip.heard, confidence=clip.confidence,
                                                        source="voice"))
    return run


# --- spoken edge cases: speech -> text -> meaning ---------------------------------------------------------


@dataclass
class ClipResult:
    case: Any  # localtc.llm.eval.Case
    heard: str
    wer: float
    stt_ms: float
    confidence: float
    problems: list[str]

    @property
    def understood(self) -> bool:
        return not self.problems


def evaluate_clips(folder: Path, transcriber: Any, interpreter: Any, *, vocabulary: bool = True) -> list[ClipResult]:
    """Transcribe each spoken edge case and grade what ATC would understand from it."""
    import tomllib

    from localtc.atc_core.phraseology import TemplateLibrary
    from localtc.llm.eval import grade, load_cases, setup

    cases = {c.name: c for c in load_cases()}
    library = TemplateLibrary.load()
    results = []
    for clip in tomllib.loads((folder / "clips.toml").read_text(encoding="utf-8"))["clip"]:
        case = cases[clip["case"]]
        pending, context = setup(case, library)
        hints = VocabularyHints(callsign=case.callsign, callsign_spoken=spoken_callsign(case.callsign),
                                stations=(case.station,)) if vocabulary else VocabularyHints()
        result = transcriber.transcribe(read_wav(folder / clip["file"]), prompt=build_prompt(hints) if vocabulary else "",
                                        hotwords=hotwords(hints) if vocabulary else "")
        heard = fixup(result.text)
        from dataclasses import replace

        interpretation = interpreter.interpret(heard, pending, replace(context, confidence=result.confidence))
        results.append(ClipResult(case, heard, token_error_rate(case.pilot, heard), result.latency_ms, result.confidence,
                                  grade(interpretation, case.expect)))
    return results


def format_clip_results(results: list[ClipResult]) -> str:
    lines = []
    for r in results:
        mark = "OK  " if r.understood else "MISS"
        lines.append(f"{mark} {r.stt_ms:5.0f} ms  wer {r.wer:4.0%}  conf {r.confidence:.2f}  {r.case.name}")
        if r.wer or not r.understood:
            lines.append(f"       said:  {r.case.pilot}")
            lines.append(f"       heard: {r.heard}")
        if not r.understood:
            lines.append(f"       wrong: {'; '.join(r.problems)}")
    n = len(results)
    ms = sorted(r.stt_ms for r in results)
    mean_wer = sum(r.wer for r in results) / n if n else 0.0
    lines.append(f"\n{sum(r.understood for r in results)}/{n} understood; word error {mean_wer:.1%}; "
                 f"speech-to-text median {ms[n // 2]:.0f} ms, max {ms[-1]:.0f} ms" if n else "no clips")
    return "\n".join(lines)
