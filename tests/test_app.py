import asyncio
import io
from pathlib import Path

import pytest

from localtc.app import make_source, run_session
from localtc.cli import EventPrinter, main
from localtc.config import Config, ConfigError, load_config
from localtc.replay import Recording, ReplaySource

FIXTURE = Path(__file__).parent / "fixtures" / "pattern_short"


def replay_config(tmp_path: Path, **replay) -> Config:
    cfg = Config()
    cfg.replay.path = str(FIXTURE)
    cfg.replay.speed = 0
    for key, value in replay.items():
        setattr(cfg.replay, key, value)
    cfg.recorder.dir = str(tmp_path / "recordings")
    cfg.atc.enabled = False  # these tests check plain replay/record
    return cfg


def test_replay_then_record_reproduces_the_recording(tmp_path):
    printed = io.StringIO()
    out_dir = asyncio.run(run_session(replay_config(tmp_path), record=True, on_event=EventPrinter(out=printed)))
    assert out_dir is not None and out_dir.parent == tmp_path / "recordings"
    assert list(Recording(out_dir).events()) == list(Recording(FIXTURE).events())
    assert Recording(out_dir).header.session.source_kind == "replay"
    assert Recording(out_dir).resolve_audio("audio/0001.wav").is_file()
    assert "PILOT \"Paine tower" in printed.getvalue()


def test_stop_event_ends_session(tmp_path):
    async def main():
        stop = asyncio.Event()
        asyncio.get_running_loop().call_later(0.2, stop.set)
        return await run_session(replay_config(tmp_path, speed=1.0), record=True, stop=stop)

    out_dir = asyncio.run(asyncio.wait_for(main(), 5))
    events = list(Recording(out_dir).events())
    assert events and max(e.t for e in events) < 5


def test_make_source_selects_by_config(tmp_path):
    assert isinstance(make_source(replay_config(tmp_path)), ReplaySource)
    cfg = Config()
    cfg.source.kind = "live"
    assert type(make_source(cfg)).__name__ == "SimConnectSource"
    with pytest.raises(ConfigError):
        make_source(Config())  # replay without a path


def test_config_file_and_env_override(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('[source]\nkind = "replay"\n[live]\nownship_hz = 2.0\n[replay]\npath = "x"\n', encoding="utf-8")
    cfg = load_config(path, env={"LOCALTC_SOURCE": "live"})
    assert cfg.source.kind == "live" and cfg.live.ownship_hz == 2.0 and cfg.replay.path == "x"
    assert cfg.recorder.enabled is True


def test_config_errors(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("[live]\nownship_hertz = 2.0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="ownship_hertz"):
        load_config(path, env={})
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.toml", env={})
    with pytest.raises(ConfigError):
        load_config(None, env={"LOCALTC_SOURCE": "cloud", "LOCALTC_CONFIG": str(tmp_path / "missing.toml")})


def test_shipped_config_is_valid():
    cfg = load_config(Path(__file__).parents[1] / "config" / "localtc.toml", env={})
    assert cfg.source.kind == "replay" and cfg.replay.path == "tests/fixtures/pattern_short"


def test_cli_inspect_and_replay(capsys, tmp_path):
    assert main(["inspect", str(FIXTURE)]) == 0
    assert "ownship_state        241" in capsys.readouterr().out
    assert main(["replay", str(FIXTURE), "--speed", "0", "--config", str(_empty_config(tmp_path))]) == 0
    assert "cleared for takeoff" in capsys.readouterr().out
    assert main(["inspect", str(tmp_path / "nope")]) == 2


def _empty_config(tmp_path: Path) -> Path:
    path = tmp_path / "empty.toml"
    path.write_text("", encoding="utf-8")
    return path


def test_a_part_of_the_session_that_doesnt_end_is_stopped(monkeypatch):
    """The real-world data's loop never ended: the flight's end waited on it, the app gave up after 15 s and
    cancelled the flight, and its logbook line was never written."""
    from localtc import app

    monkeypatch.setattr(app, "CONSUMERS_END_S", 0.1)

    async def main():
        stuck = asyncio.create_task(asyncio.sleep(60))
        done = asyncio.create_task(asyncio.sleep(0))
        await asyncio.wait_for(app._ended([stuck, done]), 2)
        return stuck.cancelled()

    assert asyncio.run(main())


def test_the_real_world_data_ends_with_the_flight():
    from types import SimpleNamespace

    from localtc.bus import EventBus
    from localtc.config import OpsConfig
    from localtc.ops.service import OpsService

    async def main():
        bus = EventBus()
        ops = OpsService(SimpleNamespace(cache=SimpleNamespace(save=lambda: None)), bus, flight=dict,
                         cfg=OpsConfig(weather=False, notams=False, restrictions=False, terrain=False))
        task = asyncio.create_task(ops.run())
        await asyncio.sleep(0.1)
        bus.close()
        await asyncio.wait_for(task, 3)

    asyncio.run(main())


def test_the_logbook_line_is_written_as_the_flight_goes(monkeypatch):
    from localtc import app
    from localtc.bus import EventBus

    monkeypatch.setattr(app, "SAVE_EVERY_S", 0.0)
    saved = []

    async def main():
        bus = EventBus()
        task = asyncio.create_task(app._keep_saving(bus, lambda: saved.append(1)))
        await asyncio.sleep(1.2)
        bus.close()
        await asyncio.wait_for(task, 3)

    asyncio.run(main())
    assert saved
