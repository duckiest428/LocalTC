"""Synthetic airports with a real-looking taxi graph, laid out in runway-aligned meters.

Coordinates: ``u`` along the main runway toward its secondary (higher-numbered)
end, ``v`` perpendicular to the right of that direction. Good enough for phase
detection, taxi routing and dialogue tests; real layouts come from
``localtc debug airport`` dumps.
"""

import math

from localtc.sim_api import Airport, ApproachProcedure, Frequency, ParkingSpot, Runway, RunwayEnd, TaxiPath, TaxiPoint

M_PER_DEG = 111_320.0


def _to_latlon(ref_lat: float, ref_lon: float, heading: float, u: float, v: float) -> tuple[float, float]:
    h = math.radians(heading)
    east = u * math.sin(h) + v * math.sin(h + math.pi / 2)
    north = u * math.cos(h) + v * math.cos(h + math.pi / 2)
    return (
        round(ref_lat + north / M_PER_DEG, 7),
        round(ref_lon + east / (M_PER_DEG * math.cos(math.radians(ref_lat))), 7),
    )


def make_airport(
    icao: str,
    name: str,
    ref: tuple[float, float],
    elev_ft: float,
    runway_heading: float,  # true heading toward the secondary end, e.g. 340 for 16R/34L
    runway: tuple[str, str],  # (primary, secondary) idents, e.g. ("16R", "34L")
    frequencies: dict[str, tuple[float, str]],
    length_m: float = 2700.0,
    magvar: float = 16.0,
    ils: str = "",
    ils_end: str = "secondary",
    approaches: tuple = (),  # (kind, runway) pairs, e.g. (("ils", "34L"), ("rnav", "16R"))
) -> Airport:
    lat0, lon0 = ref
    half = length_m / 2

    def pt(u: float, v: float) -> tuple[float, float]:
        return _to_latlon(lat0, lon0, runway_heading, u, v)

    def end(ident: str, ils_ident: str = "") -> RunwayEnd:
        digits = "".join(c for c in ident if c.isdigit())
        return RunwayEnd(number=int(digits), designator=ident[len(digits):], ils_ident=ils_ident)

    rwy = Runway(
        lat=lat0, lon=lon0, elev_ft=elev_ft, heading_true=(runway_heading + 180) % 360, length_m=length_m, width_m=45.0,
        primary=end(runway[0], ils if ils_end == "primary" else ""),
        secondary=end(runway[1], ils if ils_end == "secondary" else ""),
    )
    # Parallel taxiway "A" 150 m to the right, ramp beyond it, hold-short points 60 m from the centerline.
    layout = [  # index, u, v, kind
        (0, 0, 300, "normal"),  # ramp exit
        (1, 0, 150, "normal"),  # A at C
        (2, -half + 50, 150, "normal"),  # A south
        (3, -half + 50, 60, "hold_short"),  # A1 hold short (secondary-end departure)
        (4, -half + 50, 0, "normal"),  # runway, near secondary threshold... (u negative = primary side)
        (5, half - 50, 150, "normal"),  # A north
        (6, half - 50, 60, "hold_short"),  # A3 hold short
        (7, half - 50, 0, "normal"),  # runway north
        (8, 0, 60, "hold_short"),  # A2 hold short
        (9, 0, 0, "normal"),  # runway mid
    ]
    points = tuple(TaxiPoint(index=i, kind=k, lat=pt(u, v)[0], lon=pt(u, v)[1]) for i, u, v, k in layout)
    paths = (
        TaxiPath(kind="taxi", start=0, end=1, name="C", width_m=15),
        TaxiPath(kind="taxi", start=1, end=2, name="A", width_m=20),
        TaxiPath(kind="taxi", start=2, end=3, name="A1", width_m=20),
        TaxiPath(kind="taxi", start=3, end=4, name="A1", width_m=20),
        TaxiPath(kind="taxi", start=1, end=5, name="A", width_m=20),
        TaxiPath(kind="taxi", start=5, end=6, name="A3", width_m=20),
        TaxiPath(kind="taxi", start=6, end=7, name="A3", width_m=20),
        TaxiPath(kind="taxi", start=1, end=8, name="A2", width_m=20),
        TaxiPath(kind="taxi", start=8, end=9, name="A2", width_m=20),
        TaxiPath(kind="runway", start=4, end=9, runway=runway[1], width_m=45),
        TaxiPath(kind="runway", start=9, end=7, runway=runway[1], width_m=45),
        TaxiPath(kind="parking", start=0, end=0, name="", width_m=15),
        TaxiPath(kind="parking", start=0, end=1, name="", width_m=15),
    )
    parking = (
        ParkingSpot(index=0, name="PARKING 1", kind="ramp_ga_small", lat=pt(-40, 340)[0], lon=pt(-40, 340)[1],
                    heading_true=runway_heading, radius_m=8),
        ParkingSpot(index=1, name="PARKING 2", kind="ramp_ga_small", lat=pt(40, 340)[0], lon=pt(40, 340)[1],
                    heading_true=runway_heading, radius_m=8),
    )
    return Airport(
        icao=icao, name=name, region="K1", lat=lat0, lon=lon0, elev_ft=elev_ft, magvar=magvar, runways=(rwy,),
        frequencies=tuple(Frequency(kind=k, mhz=mhz, name=n) for k, (mhz, n) in frequencies.items()),
        taxi_points=points, taxi_paths=paths, parking=parking,
        approaches=tuple(ApproachProcedure(kind=kind, runway=rw) for kind, rw in approaches),
    )


def kpae() -> Airport:
    return make_airport(
        "KPAE", "SNOHOMISH CO (PAINE FLD)", (47.9063, -122.2816), 606.0, 340.0, ("16R", "34L"),
        {
            "atis": (128.65, "PAINE ATIS"), "clearance": (127.25, "PAINE CLEARANCE"), "ground": (121.8, "PAINE GROUND"),
            "tower": (120.2, "PAINE TOWER"), "departure": (124.675, "SEATTLE DEPARTURE"),
            "approach": (124.675, "SEATTLE APPROACH"),
        },
        ils="IPAE",
    )


def kbfi() -> Airport:
    return make_airport(
        "KBFI", "BOEING FLD/KING CO INTL", (47.5300, -122.3020), 21.0, 320.0, ("14R", "32L"),
        {
            "atis": (127.75, "BOEING ATIS"), "ground": (121.9, "BOEING GROUND"), "tower": (120.6, "BOEING TOWER"),
            "approach": (119.2, "SEATTLE APPROACH"),
        },
        length_m=3000.0,
        ils="IBFI",
        ils_end="primary",
    )
