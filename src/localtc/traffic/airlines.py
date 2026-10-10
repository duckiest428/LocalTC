"""Which airlines' aircraft stand at an airport's gates: the ones that fly there today (the live flights' routes), the
airlines based there (a hub's own fill most of its gates), else the country's own.

The live data comes first: a flight whose route (adsbdb.com) starts or ends here counts three, one about to land or
just off counts one. The hubs and the countries are a small list of the big ones, for when the live data says little
(no internet, a quiet hour) and to keep a hub's own carrier the one seen most.
"""

from collections import Counter

# The airlines based at the big airports (ICAO codes), the main one first.
HUBS: dict[str, tuple[str, ...]] = {
    "KATL": ("DAL",), "KDFW": ("AAL",), "KORD": ("UAL", "AAL"), "KDEN": ("UAL", "SWA", "FFT"), "KLAX": ("UAL", "AAL", "DAL", "SWA", "ASA"),
    "KJFK": ("JBU", "DAL", "AAL"), "KEWR": ("UAL",), "KSFO": ("UAL", "ASA"), "KSEA": ("ASA", "DAL"), "KIAH": ("UAL",),
    "KCLT": ("AAL",), "KPHX": ("AAL", "SWA"), "KMIA": ("AAL",), "KDTW": ("DAL",), "KMSP": ("DAL", "SCX"),
    "KBOS": ("JBU", "DAL"), "KLAS": ("SWA",), "KMCO": ("SWA", "JBU"), "KPHL": ("AAL",), "KSLC": ("DAL",),
    "KDCA": ("AAL",), "KIAD": ("UAL",), "KBWI": ("SWA",), "KMDW": ("SWA",), "KSAN": ("SWA", "ASA"),
    "KHOU": ("SWA",), "KDAL": ("SWA",), "KFLL": ("JBU", "SWA", "NKS"), "KPDX": ("ASA",), "PHNL": ("HAL",),
    "PANC": ("ASA",), "CYYZ": ("ACA", "WJA"), "CYVR": ("ACA", "WJA"), "CYUL": ("ACA",), "CYYC": ("WJA", "ACA"),
    "MMMX": ("AMX", "VOI"), "MMUN": ("VOI", "AMX"), "EGLL": ("BAW", "VIR"), "EGKK": ("EZY", "BAW"),
    "EGCC": ("EZY", "RYR"), "LFPG": ("AFR",), "LFPO": ("AFR", "TVF"), "EHAM": ("KLM", "TRA"), "EDDF": ("DLH", "CFG"),
    "EDDM": ("DLH",), "LEMD": ("IBE", "AEA"), "LEBL": ("VLG",), "LIRF": ("ITY",), "LSZH": ("SWR",), "LOWW": ("AUA",),
    "EBBR": ("BEL",), "EKCH": ("SAS",), "ESSA": ("SAS",), "ENGM": ("SAS", "NOZ"), "EFHK": ("FIN",), "EIDW": ("EIN", "RYR"),
    "LPPT": ("TAP",), "LPPR": ("TAP", "RYR"), "LTFM": ("THY",), "LTFJ": ("PGT",), "EPWA": ("LOT",), "LKPR": ("CSA",),
    "OMDB": ("UAE", "FDB"), "OTHH": ("QTR",), "OMAA": ("ETD",), "OERK": ("SVA",), "OEJN": ("SVA",), "LLBG": ("ELY",),
    "HECA": ("MSR",), "FAOR": ("SAA",), "VHHH": ("CPA",), "RJTT": ("JAL", "ANA"), "RJAA": ("ANA", "JAL"),
    "RKSI": ("KAL", "AAR"), "RCTP": ("EVA", "CAL"), "WSSS": ("SIA",), "VTBS": ("THA",), "ZBAA": ("CCA",),
    "ZSPD": ("CES",), "ZGGG": ("CSN",), "VIDP": ("AIC", "IGO"), "VABB": ("AIC", "IGO"), "YSSY": ("QFA", "VOZ"),
    "YMML": ("QFA", "VOZ"), "NZAA": ("ANZ",), "SBGR": ("TAM", "GLO"), "SCEL": ("LAN",), "SKBO": ("AVA",),
    "SAEZ": ("ARG",), "MPTO": ("CMP",),
}
# A country's own airlines, by the first letters of its airports' codes (the longest that fits).
COUNTRY: dict[str, tuple[str, ...]] = {
    "K": ("AAL", "DAL", "UAL", "SWA"), "PH": ("HAL",), "PA": ("ASA",), "C": ("ACA", "WJA"), "MM": ("AMX", "VOI"),
    "EG": ("BAW", "EZY"), "LF": ("AFR",), "EH": ("KLM",), "ED": ("DLH",), "LE": ("IBE", "VLG"), "LI": ("ITY",),
    "LS": ("SWR",), "LO": ("AUA",), "EB": ("BEL",), "EK": ("SAS",), "ES": ("SAS",), "EN": ("SAS", "NOZ"), "EF": ("FIN",),
    "EI": ("EIN", "RYR"), "LP": ("TAP",), "LT": ("THY", "PGT"), "EP": ("LOT",), "LK": ("CSA",), "OM": ("UAE", "ETD"),
    "OT": ("QTR",), "OE": ("SVA",), "LL": ("ELY",), "HE": ("MSR",), "FA": ("SAA",), "VH": ("CPA",), "RJ": ("JAL", "ANA"),
    "RK": ("KAL",), "RC": ("EVA", "CAL"), "WS": ("SIA",), "VT": ("THA",), "Z": ("CCA", "CES", "CSN"), "VI": ("AIC", "IGO"),
    "VA": ("AIC", "IGO"), "Y": ("QFA", "VOZ"), "NZ": ("ANZ",), "SB": ("TAM", "GLO"), "SC": ("LAN",), "SK": ("AVA",),
    "SA": ("ARG",), "MP": ("CMP",),
}
ROUTE_WEIGHT = 3.0  # a flight to or from here (its route known)
NEAR_WEIGHT = 1.0  # one close by, its route not known
HUB_SHARE = 0.4  # the based airlines' part of the gates at their hub, whatever the live data says


def country_airlines(icao: str) -> tuple[str, ...]:
    icao = icao.upper()
    return COUNTRY.get(icao[:2]) or COUNTRY.get(icao[:1]) or ()


def weights(icao: str, flown: Counter, near: Counter) -> Counter:
    """Each airline's share of the gates at ``icao``: ``flown`` the live flights to or from it (by airline),
    ``near`` the ones close by whose route isn't known."""
    out: Counter = Counter()
    for airline, n in flown.items():
        out[airline] += ROUTE_WEIGHT * n
    for airline, n in near.items():
        out[airline] += NEAR_WEIGHT * n
    based = HUBS.get(icao.upper(), ())
    if based:
        # The based airlines get at least HUB_SHARE between them, the main one most (all of it when it's alone).
        hub = max(1.0, sum(out.values()) * HUB_SHARE / (1 - HUB_SHARE))
        for rank, airline in enumerate(based):
            part = 1.0 if len(based) == 1 else 0.6 if rank == 0 else 0.4 / (len(based) - 1)
            out[airline] = max(out[airline], hub * part)
    if not out:
        for rank, airline in enumerate(country_airlines(icao)):
            out[airline] = 1.0 / (rank + 1)
    return out


__all__ = ["COUNTRY", "HUBS", "country_airlines", "weights"]
