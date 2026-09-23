"""The airspace around an airport, as a VFR pilot meets it: US Class B, C or D, or an ICAO control zone.

US Class B and C airports are listed by hand from the FAA's airspace designations (FAA Order JO 7400.11).
Everything else is decided from the airport: a tower makes it Class D (or an ICAO aerodrome traffic zone),
a tower with an approach control over it makes an ICAO control zone.

The shapes are simplified. Real Class B is an upside-down wedding cake with published shelves, and a real
control zone follows the airport's own boundary; LocalTC uses the typical sizes below, close enough for ATC
to notice a VFR aircraft inside without a clearance, and for the Live Map to show roughly where it is.
"""

import math
from dataclasses import dataclass

from localtc.atc_core.airport.geometry import AirportGeometry

# The 37 Class B areas' primary airports (and the other airports inside them that have their own tower).
CLASS_B = frozenset({
    "KATL", "KBOS", "KBWI", "KCLT", "KORD", "KMDW", "KCVG", "KCLE", "KDFW", "KDAL", "KDEN", "KDTW", "PHNL", "KIAH",
    "KHOU", "KMCI", "KLAS", "KLAX", "KMEM", "KMIA", "KMSP", "KMSY", "KJFK", "KLGA", "KEWR", "KMCO", "KPHL", "KPHX",
    "KPIT", "KSAN", "KNKX", "KSFO", "KSEA", "KSLC", "KSTL", "KTPA", "KDCA", "KIAD", "KADW",
})

# Class C primary airports (a selection of the ~120: the ones LocalTC users are likeliest to meet).
CLASS_C = frozenset({
    "KABQ", "KALB", "KAMA", "KAUS", "KBDL", "KBHM", "KBIL", "KBNA", "KBOI", "KBTV", "KBUF", "KBUR", "KCAE", "KCAK",
    "KCHA", "KCHS", "KCMH", "KCOS", "KCRP", "KDAY", "KDSM", "KELP", "KFAT", "KFLL", "KFNT", "KFSD", "KFWA", "KGEG",
    "KGRR", "KGSO", "KGSP", "KHRL", "KICT", "KIND", "KJAX", "KLAN", "KLBB", "KLEX", "KLIT", "KMAF", "KMDT", "KMHT",
    "KMKE", "KMOB", "KMRY", "KMSN", "KMYR", "KOAK", "KOKC", "KOMA", "KONT", "KORF", "KPBI", "KPDX", "KPIA", "KPNS",
    "KPSP", "KPVD", "KRDU", "KRIC", "KRNO", "KROC", "KRSW", "KSAT", "KSAV", "KSBA", "KSBN", "KSDF", "KSJC", "KSMF",
    "KSNA", "KSPI", "KSYR", "KTOL", "KTUL", "KTUS", "KTYS", "PANC", "TJSJ",
})


@dataclass(frozen=True)
class Zone:
    kind: str  # B, C, D, CTR, ATZ, or "" (uncontrolled)
    radius_nm: float
    ceiling_agl_ft: int  # above the field; Class B's is 10,000 ft MSL, turned into AGL by the caller
    name: str  # "Class Bravo", "control zone"

    @property
    def controlled(self) -> bool:
        return bool(self.kind)


NONE = Zone("", 0.0, 0, "")


def zone_for(icao: str, *, towered: bool, approach: bool, icao_region: bool) -> Zone:
    """The airspace a VFR pilot flies into around ``icao``."""
    icao = icao.upper()
    if not icao_region:
        if icao in CLASS_B:
            return Zone("B", 30.0, 10000, "Class Bravo")
        if icao in CLASS_C:
            return Zone("C", 10.0, 4000, "Class Charlie")
        return Zone("D", 4.0, 2500, "Class Delta") if towered else NONE
    if towered and approach:
        return Zone("CTR", 10.0, 2500, "control zone")
    return Zone("ATZ", 2.5, 2000, "traffic zone") if towered else NONE


def class_b_floor_msl(zone: Zone, field_elev_ft: float, distance_nm: float) -> float | None:
    """The bottom of Class B at ``distance_nm`` from the field: the surface close in, then the shelves.
    None outside it."""
    if zone.kind != "B" or distance_nm > zone.radius_nm:
        return None
    if distance_nm <= 10:
        return field_elev_ft
    return field_elev_ft + (3000 if distance_nm <= 20 else 6000)


def inside_class_b(zone: Zone, geo: AirportGeometry, lat: float, lon: float, alt_msl_ft: float) -> bool:
    distance = geo.distance_nm(lat, lon)
    floor = class_b_floor_msl(zone, geo.airport.elev_ft, distance)
    return floor is not None and floor <= alt_msl_ft <= 10000


def bearing_words(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> str:
    """ "north", "southwest": where (to) is from (from), in eight points."""
    d_lat = to_lat - from_lat
    d_lon = (to_lon - from_lon) * math.cos(math.radians(from_lat))
    bearing = (math.degrees(math.atan2(d_lon, d_lat)) + 360) % 360
    return ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"][round(bearing / 45) % 8]
