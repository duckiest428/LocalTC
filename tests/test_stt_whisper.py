"""Real Whisper on the voice recordings. Opt-in (the model is ~150 MB and takes seconds):

    pytest -m whisper -s          (localtc setup downloads the model; localtc voice eval shows the details)
"""

from pathlib import Path

import pytest

from localtc.app import build_engine
from localtc.atc_core.llm import LlmInterpreter
from localtc.config import Config, with_recorded
from localtc.replay import Recording
from localtc.sim_api import AtcTransmission, ReadbackEvaluated
from localtc.voice import evaluate_clips, format_clip_results, token_error_rate, transcribe_recording

pytestmark = pytest.mark.whisper
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def whisper(request):
    if "whisper" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("run with: pytest -m whisper -s")
    from localtc.stt.whisper import WhisperTranscriber, default_models_dir, model_path

    if not (model_path("base.en", default_models_dir()) / "model.bin").exists():
        pytest.skip("Whisper base.en isn't installed; run: localtc setup --whisper-model base.en")
    transcriber = WhisperTranscriber("base.en", device="cpu", allow_download=False)
    transcriber.warm_up()
    return transcriber


def test_the_spoken_departure_flies_the_same(whisper):
    """Each pilot call re-transcribed from its audio at the moment it was made: ATC hears every readback right."""
    recording = Recording(FIXTURES / "voice_kpae")
    engine = build_engine(with_recorded(Config(), recording.header.config))
    engine.cfg.await_transcripts = True
    run = transcribe_recording(recording, whisper, engine=engine)
    for clip in run.clips:
        print(f"{clip.stt_ms:5.0f} ms  wer {token_error_rate(clip.reference, clip.heard):4.0%}  {clip.heard}")
    readbacks = [(o.instruction_id, o.status) for o in run.outputs if isinstance(o, ReadbackEvaluated)]
    assert readbacks == [(i, "correct") for i in ("clearance.ifr", "ground.taxi_out", "ground.handoff_tower",
                                                 "tower.takeoff", "tower.handoff_departure", "departure.radar_contact")]
    assert not [o for o in run.outputs if isinstance(o, AtcTransmission) and o.instruction_id == "common.say_again"]
    assert max(c.stt_ms for c in run.clips) < 3000


def test_spoken_edge_cases(whisper):
    """Speech -> text -> meaning, with and without the aviation vocabulary. Without a running model only the
    grammar understands, so fewer cases pass."""
    from localtc.llm import OllamaBackend

    backend = OllamaBackend()
    model = backend if backend.status(timeout_s=1).has(backend.model) else None
    folder = FIXTURES / "voice_clips"
    plain = evaluate_clips(folder, whisper, LlmInterpreter(model), vocabulary=False)
    primed = evaluate_clips(folder, whisper, LlmInterpreter(model), vocabulary=True)
    print("\n" + format_clip_results(primed))
    wer = lambda results: sum(r.wer for r in results) / len(results)  # noqa: E731
    assert wer(primed) < 0.8 * wer(plain)  # the vocabulary helps (it roughly halves the errors)
    assert wer(primed) < 0.3
    understood = sum(r.understood for r in primed)
    assert understood >= (28 if model else 15)
