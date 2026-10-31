"""MSFS 2020 beside 2024: the same bridge for both, told apart by the sim's own version, and what 2020 hasn't done
another way (its AI aircraft by the calls before 2024's, its installed aircraft read from its packages)."""

import asyncio
import struct

from helpers.sim_fakes import FakeSimConnect

from localtc import simpaths
from localtc.config import LiveConfig
from localtc.sim_api import EnumerateModels, ModelList, SpawnAiAircraft
from localtc.sim_bridge import wire
from localtc.sim_bridge.simconnect_source import SimConnectSource
from localtc.traffic.models import ModelPicker

FAST = LiveConfig(ownship_hz=50, traffic_interval_s=0.05, retry_max_s=0.05)


def run(fake: FakeSimConnect, commands, until):
    """Connect to ``fake``, send ``commands``, and collect events until ``until(events)`` (or 3 s)."""
    async def main():
        source = SimConnectSource(FAST, dll_factory=lambda: fake)
        info = await asyncio.wait_for(source.start(), 3)
        for c in commands:
            await source.send(c)
        seen = []

        async def collect():
            async for ev in source.events():
                seen.append(ev)
                if until(seen, fake):
                    return
        try:
            await asyncio.wait_for(collect(), 3)
        except TimeoutError:
            pass
        await source.stop()
        return info, seen

    return asyncio.run(main())


SPAWN = SpawnAiAircraft(request_id=7, kind="parked", title="Airbus A320 Neo Asobo", livery="", tail="N1")


def test_msfs_2020_is_told_apart_and_gets_the_older_ai_calls(caplog):
    fake = FakeSimConnect(sim_major=11)
    info, _ = run(fake, [SPAWN], lambda seen, f: f.created)
    assert info.sim_product == "MSFS 2020"
    assert fake.created == [("parked", "Airbus A320 Neo Asobo", "", False)]  # without the livery: 2020 ignores EX1
    assert "targets" not in caplog.text and "WARNING" not in caplog.text  # nothing to warn about


def test_msfs_2024_keeps_its_own():
    fake = FakeSimConnect(sim_major=12)
    info, _ = run(fake, [SPAWN, EnumerateModels()], lambda seen, f: f.created and f.enumerated)
    assert info.sim_product == "MSFS 2024"
    assert fake.created == [("parked", "Airbus A320 Neo Asobo", "", True)] and fake.enumerated == 1


def test_msfs_2020s_aircraft_are_read_from_its_packages(monkeypatch, tmp_path):
    roots = []
    monkeypatch.setattr(simpaths, "package_roots", lambda sim="": roots.append(sim) or [tmp_path])
    monkeypatch.setattr(simpaths, "aircraft_titles", lambda r: ["FSLTL_A320_DAL-Delta", "Airbus A320 Neo Asobo"])
    fake = FakeSimConnect(sim_major=11)
    _, seen = run(fake, [EnumerateModels()], lambda seen, f: any(isinstance(e, ModelList) for e in seen))
    models = next(e for e in seen if isinstance(e, ModelList)).models
    assert models == (("FSLTL_A320_DAL-Delta", ""), ("Airbus A320 Neo Asobo", "")) and roots == ["2020"]
    assert fake.enumerated == 0  # never asked of a sim that can't answer
    picker = ModelPicker(list(models))
    assert picker.pick("A20N", "DAL") == ("FSLTL_A320_DAL-Delta", "")  # FSLTL's, as in 2024
    assert picker.pick("B738", "RYR") == ("Airbus A320 Neo Asobo", "")  # else 2020's own, of the size


def test_the_older_ai_calls_are_the_ex1_ones_without_the_livery(monkeypatch):
    """SimConnect_AICreate*: the same arguments as their _EX1 (whose packets are the DLL's, test_wire) less the
    256-byte livery, under the call numbers before them (0x27 parked ATC, 0x28 enroute, 0x29 non-ATC)."""
    sent = []

    class Rec:
        name = "test"

        def write(self, data):
            sent.append(data)

        def read(self):
            return b""

        def close(self):
            pass

    monkeypatch.setattr(wire.SimConnectWire, "_connect", lambda self: Rec())
    sc = wire.SimConnectWire()
    h = sc.open("LocalTC")
    for kind, kw in (("parked", {"lat": 47.1, "lon": -122.2, "alt_ft": 400, "heading": 90}),
                     ("enroute", {"flight_number": 123, "plan": "C:\\x\\plan", "plan_position": 2.5}),
                     ("parked_atc", {"plan": "KSEA"})):
        sc.ai_create(h, kind, 0x71, "Airbus A320 Neo Asobo", "LIV", "TAIL1", **kw)
        sc.ai_create(h, kind, 0x71, "Airbus A320 Neo Asobo", "LIV", "TAIL1", ex1=False, **kw)
        new, old = sent[-2], sent[-1]
        call = struct.unpack_from("<I", old, 8)[0] - 0xF0000000
        assert call == {"parked": 0x29, "enroute": 0x28, "parked_atc": 0x27}[kind]
        assert old[16:] == new[16:16 + 256] + new[16 + 512:]  # the title, then all but the livery
        assert struct.unpack_from("<I", old)[0] == len(old) == len(new) - 256


def test_aircraft_titles_from_the_packages_and_each_sims_roots(monkeypatch, tmp_path):
    def cfg(path, text):
        path.mkdir(parents=True)
        (path / "aircraft.cfg").write_text(text, encoding="utf-8")

    root = tmp_path / "Packages"
    cfg(root / "Official/Steam/asobo-aircraft-a320-neo/SimObjects/Airplanes/Asobo_A320_NEO",
        '[FLTSIM.0]\ntitle = "Airbus A320 Neo Asobo" ; Variation name\n[FLTSIM.1]\nTitle="Airbus A320 Neo Asobo AirTraffic 00"\n'
        'ui_title = "not this"\n')
    cfg(root / "Community/fsltl-traffic-base/SimObjects/Airplanes/FSLTL_A320_DAL",
        "[fltsim.0]\ntitle=FSLTL_A320_DAL-Delta\n[fltsim.1]\ntitle = Airbus A320 Neo Asobo\n")
    assert simpaths.aircraft_titles([root]) == ["FSLTL_A320_DAL-Delta", "Airbus A320 Neo Asobo",  # Community's first
                                                "Airbus A320 Neo Asobo AirTraffic 00"]

    appdata, local = tmp_path / "Roaming", tmp_path / "Local"
    for folder, packages in (("Microsoft Flight Simulator", "D:/MSFS2020"), ("Microsoft Flight Simulator 2024", "E:/MSFS2024")):
        (appdata / folder).mkdir(parents=True)
        (appdata / folder / "UserCfg.opt").write_text(f'InstalledPackagesPath "{packages}"\n', encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert [p.as_posix() for p in simpaths.package_roots()] == ["E:/MSFS2024", "D:/MSFS2020"]
    assert [p.as_posix() for p in simpaths.package_roots("2020")] == ["D:/MSFS2020"]
