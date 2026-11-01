"""The models and voices fetched where huggingface.co can't be reached, and a flight that starts without them."""

import asyncio
import io
import types
import urllib.error

import pytest

from localtc.config import Config
from localtc.stt import whisper
from localtc.tts import voices


def _model(root, name):
    (root / f"faster-whisper-{name}").mkdir(parents=True)
    (root / f"faster-whisper-{name}" / "model.bin").write_bytes(b"x")


def test_whisper_comes_from_the_mirror_when_hugging_face_is_unreachable(monkeypatch, tmp_path):
    import huggingface_hub

    asked = []

    def snapshot(repo, *, local_dir, allow_patterns, endpoint):
        asked.append(endpoint)
        if "huggingface.co" in endpoint:
            raise ConnectionError("ConnectTimeout: [WinError 10060]")
        return local_dir

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    path = whisper.download("base.en", tmp_path)
    assert asked == ["https://huggingface.co", "https://hf-mirror.com"]
    assert path == whisper.model_path("base.en", tmp_path)


def test_an_installed_model_stands_in_for_one_that_cant_be_downloaded(tmp_path):
    assert whisper.stand_in("base.en", tmp_path) is None
    for name in ("small", "tiny.en"):
        _model(tmp_path, name)
    assert whisper.installed(tmp_path) == ["tiny.en", "small"]
    assert whisper.stand_in("small.en", tmp_path) == "small"  # the same size, the other kind
    assert whisper.stand_in("base.en", tmp_path) == "tiny.en"  # the largest no bigger
    _model(tmp_path, "base")
    assert whisper.stand_in("base.en", tmp_path) == "base"
    assert whisper.stand_in("medium.en", tmp_path) == "small"


def test_the_flight_starts_on_an_installed_model_when_the_chosen_one_cant_be_downloaded(monkeypatch, tmp_path):
    import sys

    _model(tmp_path, "small")
    loaded = []
    fake = types.SimpleNamespace(WhisperModel=lambda path, **kw: loaded.append(path) or object())
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)

    def offline(model, models_dir):
        raise ConnectionError("ConnectTimeout")

    monkeypatch.setattr(whisper, "download", offline)
    t = whisper.WhisperTranscriber("small.en", device="cpu", models_dir=tmp_path)
    t.load()
    assert t.choice.model == "small" and t.stand_in_for == "small.en"
    assert loaded == [str(whisper.model_path("small", tmp_path))]

    empty = whisper.WhisperTranscriber("small.en", device="cpu", models_dir=tmp_path / "none")
    with pytest.raises(ConnectionError):
        empty.load()


def test_a_piper_voice_comes_from_the_mirror_when_hugging_face_is_unreachable(monkeypatch, tmp_path):
    asked = []

    class Reply(io.BytesIO):
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(url, timeout):
        asked.append(url)
        if "huggingface.co" in url:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return Reply(b"data")

    monkeypatch.setattr(voices.urllib.request, "urlopen", urlopen)
    path = voices.download("zh_CN-huayan-medium", tmp_path)
    assert path.read_bytes() == b"data" and path.with_suffix(".onnx.json").is_file()
    assert [u.split("/")[2] for u in asked] == ["huggingface.co", "hf-mirror.com"] * 2


def test_every_setting_the_app_reads_exists():
    """Saving the settings with a flight running failed on a setting of the voices that had been removed
    (``cfg.tts.chatter_cloud``): every ``cfg.<section>.<name>`` the app reads is one Config has."""
    import re
    from pathlib import Path

    import localtc

    cfg = Config()
    root = Path(localtc.__file__).parent
    missing = []
    for path in [root / "app.py", *(root / "ui").glob("*.py")]:
        for section, name in set(re.findall(r"\bcfg\.([a-z_]+)\.([a-z_]+)\b", path.read_text(encoding="utf-8"))):
            part = getattr(cfg, section, None)
            if part is not None and not callable(part) and not hasattr(part, name):
                missing.append(f"{path.name}: cfg.{section}.{name}")
    assert not missing
