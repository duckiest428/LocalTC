"""LocalTC's own SimConnect client (sim_bridge/wire.py): every packet byte for byte as the SDK's DLL sends it, and the
sim's replies read back whole however the stream splits them."""

import socket
import struct
import threading
from pathlib import Path

import pytest

from localtc.sim_bridge import wire
from localtc.sim_bridge.dll import SimConnectError
from localtc.sim_bridge.protocol import DataType, FacilityListType, OpenInfo, Period, parse_message

PACKETS = {name: bytes.fromhex(hexes) for name, hexes in (
    line.split() for line in (Path(__file__).parent / "fixtures" / "simconnect_packets.txt").read_text().splitlines()
    if line and not line.startswith("#"))}


class Recorder:
    name = "test"

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    def read(self) -> bytes:
        return b""

    def close(self) -> None:
        pass


def same(got: bytes, want: bytes) -> None:
    """Equal but for the running packet number (the 4th header field)."""
    assert len(got) == len(want)
    assert got[:12] == want[:12] and got[16:] == want[16:]


def test_every_call_is_the_packet_the_dll_sends(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(wire.SimConnectWire, "_connect", lambda self: rec)
    sc = wire.SimConnectWire()
    h = sc.open("LocalTC spy")
    calls = [
        ("Open", None),
        ("AddToDataDefinition_f64", lambda: sc.add_to_data_definition(h, 0x11, "PLANE LATITUDE", "degrees", DataType.FLOAT64)),
        ("AddToDataDefinition_str256_nounit", lambda: sc.add_to_data_definition(h, 0x11, "TITLE", None, DataType.STRING256)),
        ("RequestDataOnSimObject", lambda: sc.request_data_on_sim_object(h, 0x21, 0x11, 0, Period.SECOND, 1, 2, 3, 4)),
        ("RequestDataOnSimObjectType", lambda: sc.request_data_on_sim_object_type(h, 0x22, 0x11, 50000, 1)),
        ("SubscribeToSystemEvent", lambda: sc.subscribe_to_system_event(h, 0x31, "SimStart")),
        ("RequestFacilityData_region", lambda: sc.request_facility_data(h, 0x41, 0x43, "KSEA", "K1")),
        ("RequestFacilitiesList_EX1", lambda: sc.request_facilities_list(h, FacilityListType.AIRPORT, 0x44)),
        ("MapClientEventToSimEvent", lambda: sc.map_client_event_to_sim_event(h, 0x51, "COM_RADIO_SET_HZ")),
        ("TransmitClientEvent", lambda: sc.transmit_client_event(h, 0, 0x51, 0x12345678, 1, 16)),
        ("TransmitClientEvent_EX1", lambda: sc.transmit_client_event_ex1(h, 0, 0x51, 1, 16, 0x0A0B0C0D, 2)),
        ("EnumerateInputEvents", lambda: sc.enumerate_input_events(h, 0x61)),
        ("SetInputEvent", lambda: sc.set_input_event(h, 0x1122334455667788, 3.5)),
        ("AICreateNonATCAircraft_EX1", lambda: sc.ai_create(h, "parked", 0x71, "Airbus A320 Neo Asobo", "LIV", "TAIL1",
                                                            lat=47.1, lon=-122.2, alt_ft=400, heading=90)),
        ("AICreateEnrouteATCAircraft_EX1", lambda: sc.ai_create(h, "enroute", 0x72, "Airbus A320 Neo Asobo", "LIV", "TAIL2",
                                                                flight_number=123, plan="C:\\x\\plan", plan_position=2.5)),
        ("AIRemoveObject", lambda: sc.ai_remove(h, 0x0BADBEEF, 0x73)),
        ("EnumerateSimObjectsAndLiveries", lambda: sc.enumerate_models(h, 0x74)),
        ("SetDataOnSimObject", lambda: sc.set_data_on_sim_object(h, 0x12, 0, bytes(range(8)))),
        ("AICreateParkedATCAircraft_EX1", lambda: sc.ai_create(h, "parked_atc", 0x75, "Airbus A320 Neo Asobo", "LIV",
                                                               "TAIL3", plan="KSEA")),
        ("AIReleaseControl", lambda: sc.ai_release_control(h, 0x0BADBEEF, 0x76)),
        ("ClearDataDefinition", lambda: sc.clear_data_definition(h, 0x12)),
    ]
    for name, call in calls:
        if call is not None:
            call()
        same(rec.sent[-1], PACKETS[name])
    numbers = [struct.unpack_from("<I", p, 12)[0] for p in rec.sent]
    assert numbers == list(range(1, len(rec.sent) + 1))  # a running packet number, as the DLL counts


def test_replies_come_back_whole_however_the_stream_splits_them():
    """The sim's packets over a socket, cut anywhere: each handed over whole, as the DLL does; then the sim gone is
    an error (the bridge reconnects)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    reply = struct.pack("<3I", 308, 6, 2) + b"SunRise".ljust(256, b"\0") + struct.pack("<12I", 12, 2, 282174, 999, 12, 2,
                                                                                    0, 0, 0, 0, 0, 0)[: 308 - 12 - 256]
    received = []

    def sim():
        conn, _ = server.accept()
        received.append(conn.recv(4096))
        for cut in (reply[:5], reply[5:200], reply[200:] + reply[:10]):
            conn.sendall(cut)
            threading.Event().wait(0.05)
        conn.sendall(reply[10:])
        threading.Event().wait(0.2)
        conn.close()

    thread = threading.Thread(target=sim, daemon=True)
    thread.start()
    sc = wire.SimConnectWire([("tcp", server.getsockname())])
    h = sc.open("LocalTC")
    got = []
    for _ in range(200):
        try:
            packet = sc.get_next_dispatch(h)
        except SimConnectError:
            break
        if packet is not None:
            got.append(packet)
        threading.Event().wait(0.01)
    else:
        pytest.fail("the closed connection wasn't noticed")
    thread.join(2)
    assert got == [reply, reply]
    assert isinstance(parse_message(got[0]), OpenInfo) and parse_message(got[0]).app_name == "SunRise"
    assert received[0][:4] == struct.pack("<I", 296) and b"LocalTC" in received[0]


def test_no_sim_is_a_connection_error_the_bridge_retries():
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(SimConnectError, match="isn't running"):
        wire.SimConnectWire([("tcp", ("127.0.0.1", port))]).open("LocalTC")


def test_the_sims_ports_and_pipes_come_from_its_simconnect_xml(tmp_path, monkeypatch):
    cache = tmp_path / "Packages" / "Microsoft.Limitless_8wekyb3d8bbwe" / "LocalCache"
    cache.mkdir(parents=True)
    (cache / "SimConnect.xml").write_text("""<SimBase.Document Type="SimConnect">
  <SimConnect.Comm><Protocol>IPv4</Protocol><Scope>local</Scope><Port>5012</Port></SimConnect.Comm>
  <SimConnect.Comm><Protocol>Pipe</Protocol><Scope>local</Scope><Port>Custom\\SimConnect</Port></SimConnect.Comm>
  <SimConnect.Comm><Protocol>IPv4</Protocol><Scope>local</Scope><Port>0</Port></SimConnect.Comm>
</SimBase.Document>""", encoding="cp1252")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "none"))
    assert wire.server_addresses() == [("pipe", wire.PIPE), ("tcp", ("127.0.0.1", 5012)),
                                       ("pipe", "\\\\.\\pipe\\Custom\\SimConnect"), ("tcp", ("127.0.0.1", 500))]
