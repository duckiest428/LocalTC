"""An airport's ground for the Live Map close in: runways at their width with their numbers where they're painted,
the taxiways and their names, the hold-short lines across them, the gates by ATC's names."""

from localtc.sim_api.airport import Airport, ParkingSpot, Runway, RunwayEnd, TaxiPath, TaxiPoint
from localtc.ui.airport_layout import layout

LAT, LON = 47.0, -122.0
DLAT = 0.001  # about 111 m


def airport() -> Airport:
    points = [TaxiPoint(index=i, kind="hold_short" if i == 0 else "normal", lat=LAT + i * DLAT, lon=LON - 0.003)
              for i in range(10)]
    paths = [TaxiPath(kind="taxi", start=i, end=i + 1, name="A") for i in range(9)]
    paths += [TaxiPath(kind="runway", start=0, end=9, runway="16"), TaxiPath(kind="vehicle", start=1, end=2)]
    return Airport(icao="KTST", lat=LAT, lon=LON, elev_ft=400,
                   runways=(Runway(lat=LAT, lon=LON, elev_ft=400, heading_true=0.0, length_m=2000, width_m=45,
                                   primary=RunwayEnd(number=36), secondary=RunwayEnd(number=18)),),
                   taxi_points=tuple(points), taxi_paths=tuple(paths),
                   parking=(ParkingSpot(index=0, name="GATE A 12", kind="gate_medium", lat=LAT, lon=LON - 0.004),
                            ParkingSpot(index=1, name="FUEL 1", kind="fuel", lat=LAT, lon=LON - 0.005)))


def test_a_runways_numbers_are_at_the_ends_they_are_landed_on():
    rw = layout(airport())["runways"][0]
    ends = {e["ident"]: e["at"] for e in rw["ends"]}
    assert ends["36"][0] < LAT < ends["18"][0]  # 36 is landed on flying north: its number at the south end
    assert len(rw["outline"]) == 4
    width_deg = abs(rw["outline"][0][1] - rw["outline"][3][1])
    assert 40 < width_deg * 111_320 * 0.682 < 50  # its real width (45 m)


def test_taxiways_are_drawn_named_and_held_short_of():
    got = layout(airport())
    assert len(got["taxiways"]) == 9  # the runway and the vehicle lane aren't taxiways
    labels = [lab for lab in got["taxiway_labels"] if lab["name"] == "A"]
    assert 1 <= len(labels) <= 3  # along a kilometre: a few times, not on every piece
    hold = got["holds"][0]
    assert abs(hold["a"][0] - hold["b"][0]) < 1e-5  # square to a north-south taxiway: an east-west bar


def test_gates_by_atcs_names_and_yours_from_the_flight():
    plain = layout(airport())["gates"]
    assert [g["label"] for g in plain] == ["A12"]  # the fuel stand isn't a gate
    gate_map = {"assigned": 0, "stands": [{"index": 0, "name": "Gate E9", "label": "E9", "word": "gate", "lat": LAT,
                                           "lon": LON, "occupied": False, "taken": False, "international": True}]}
    flight = layout(airport(), gate_map)["gates"]
    assert flight[0]["label"] == "E9" and flight[0]["yours"] and flight[0]["international"]
