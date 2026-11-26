"""Airline ICAO codes and the names they are called by on the radio.

A flight plan gives a callsign as an ICAO code and a number ("ACA216"). Spelled out that is
"alpha charlie alpha two one six", which no controller would say: it is "Air Canada 216". This
table turns the code into the telephony name, so `Callsign` can carry both.

The table below is the radio's own short names for the airlines heard most; behind it is every airline in the world
with an ICAO code, flying today or gone (``airlines.tsv.gz``, from Wikipedia's lists: ``tools/make_airlines.py``), so a
Pan Am livery's "PAA123" is "Clipper 123". A code given to a defunct airline and later to another means today's.

Codes in neither keep their letters, which is right for a registration ("N172LT") and the best that can be done for
an airline nobody has heard of.
"""

import gzip
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# ICAO code -> radio telephony. The name as ATC says it, not the company's legal name.
TELEPHONY: dict[str, str] = {
    # North America
    "AAL": "American", "AAY": "Allegiant", "ACA": "Air Canada", "AJT": "Amerijet", "ASA": "Alaska",
    "ASH": "Air Shuttle", "AWI": "Air Wisconsin", "BAF": "Bluestreak", "CJT": "Cargojet", "CPA": "Cathay",
    "DAL": "Delta", "EDV": "Endeavor", "ENY": "Envoy", "FDX": "FedEx", "FFT": "Frontier", "GJS": "Lindbergh",
    "GTI": "Giant", "HAL": "Hawaiian", "JBU": "JetBlue", "JZA": "Jazz", "MXY": "Breeze", "NKS": "Spirit",
    "PDT": "Piedmont", "PSA": "Blue Streak", "QXE": "Horizon Air", "RPA": "Brickyard", "ROU": "Rouge",
    "SCX": "Sun Country", "SKW": "Skywest", "SWA": "Southwest", "TSC": "Air Transat", "UAL": "United",
    "UPS": "UPS", "VOI": "Volaris", "WJA": "Westjet", "WSW": "Swoop", "AMX": "Aeromexico", "AIJ": "Interjet",
    "POE": "Porter", "FLE": "Flair",
    # Latin America
    "ARG": "Argentina", "AVA": "Avianca", "AZU": "Azul", "CMP": "Copa", "GLO": "Gol", "LAN": "Lan",
    "LPE": "Lanperu", "TAM": "Tam", "ONE": "Dankair", "VOZ": "Velocity",
    # United Kingdom and Ireland
    "BAW": "Speedbird", "BCS": "Eurotrans", "BEE": "Jersey", "EFW": "Griffin", "EIN": "Shamrock",
    "EXS": "Channex", "EZY": "Easy", "LOG": "Logan", "NPT": "Newport", "RYR": "Ryanair", "TOM": "Tomjet",
    "VIR": "Virgin", "WUK": "Wizz Go",
    # Western Europe
    "AEA": "Europa", "AFR": "Airfrans", "AUA": "Austrian", "BEL": "Beeline", "BER": "Air Berlin",
    "BTI": "Air Baltic", "CFG": "Condor", "CLH": "Hansaline", "CTN": "Croatia", "DLH": "Lufthansa",
    "EWG": "Eurowings", "FIN": "Finnair", "IBE": "Iberia", "IBS": "Iberexpress", "ICE": "Iceair",
    "ITY": "Itarrow", "KLM": "KLM", "LOT": "Lot", "NAX": "Norshuttle", "NOZ": "Nordic", "NSZ": "Rednose",
    "SAS": "Scandinavian", "SWR": "Swiss", "TAP": "Air Portugal", "TRA": "Transavia", "TVF": "France Soleil",
    "VLG": "Vueling", "WZZ": "Wizzair", "SXS": "Sunexpress", "PGT": "Sunturk", "AEE": "Aegean",
    "OAL": "Olympic", "LDM": "Lauda", "SDR": "Sundair", "MSR": "Egyptair", "RAM": "Royalair Maroc",
    "EDW": "Edelweiss", "OAW": "Helvetic", "LGL": "Luxair", "KLC": "City", "BCY": "Cityjet", "CFE": "Flyer",
    "DLA": "Dolomiti", "ANE": "Air Nostrum", "ADR": "Adria", "ENT": "Enter", "NOS": "Moonflower", "TFL": "Orange",
    "TUI": "Tuifly", "JAF": "Beauty", "TVS": "Skytravel", "WIF": "Wideroe", "ROT": "Tarom", "LZB": "Flying Bulgaria",
    "CAI": "Corendon", "SEH": "Air Crete", "VKG": "Viking", "IBK": "Nortrans", "NLY": "Flyniki", "HOP": "Air Hop",
    "GEC": "Lufthansa Cargo", "DHK": "World Express", "BCI": "Blue Islands", "LOF": "Trans States",
    "AHY": "Azal", "KZR": "Astanaline", "BRU": "Belarus Avia", "EZS": "Topswiss", "EJU": "Alpine",
    # Eastern Europe, Russia, Central Asia
    "AFL": "Aeroflot", "AZA": "Alitalia", "BTK": "Batik", "CSA": "CSA", "ELY": "El Al", "FPO": "Askair",
"PBD": "Pobeda", "SBI": "Siberian", "SVR": "Sverdlovsk", "SWG": "Sunwing",
    "UTA": "Utair", "AUI": "Ukraine International", "TSO": "Transoviet",
    # Middle East
    "ABY": "Arabia", "ETD": "Etihad", "FDB": "Sky Dubai", "GFA": "Gulf Air", "IRA": "Iranair",
    "KAC": "Kuwaiti", "MEA": "Cedar Jet", "OMA": "Oman Air", "QTR": "Qatari", "RJA": "Jordanian",
    "SVA": "Saudia", "THY": "Turkish", "UAE": "Emirates", "JZR": "Jazeera", "KNE": "Nas Express", "ISR": "Israir",
    "IAW": "Iraqi", "LMU": "Almasria", "IRM": "Mahan Air", "FAD": "Flyadeal", "SDM": "Rossiya",
    # Africa
    "ETH": "Ethiopian", "KQA": "Kenya", "SAA": "Springbok", "TAR": "Tunair", "DAH": "Air Algerie",
    "MAU": "Airmauritius", "AMC": "Air Malta", "AAW": "Afriqiyah",
    # South and East Asia
    "AAR": "Asiana", "AIC": "Air India", "ANA": "All Nippon", "APJ": "Air Peach", "BBC": "Bangladesh",
    "CAL": "Dynasty", "CCA": "Air China", "CES": "China Eastern", "CSN": "China Southern", "CSZ": "Shenzhen Air",
    "CXA": "Xiamen Air", "EVA": "Eva", "HVN": "Viet Nam Airlines", "IGO": "Ifly", "JAL": "Japanair",
    "JJP": "Orange Liner", "KAL": "Koreanair", "MAS": "Malaysian", "PAL": "Philippine", "SIA": "Singapore",
    "SJX": "Starwalker", "THA": "Thai", "TGW": "Scooter", "TWB": "Sonorous", "VJC": "Vietjet",
    # South Korea's low-cost airlines (Trinity Airways, formerly T'way, is "Sonorous" since September 2026)
    "ABL": "Air Busan", "ASV": "Air Seoul", "EOK": "Aerohanguk", "ESR": "Eastar", "JJA": "Jeju Air", "JNA": "Jin Air",
    "PTA": "Parata Air",
    "AKX": "Air Asia", "AXM": "Red Cap", "SEJ": "Spicejet", "AIQ": "Thai Asia", "AXB": "Express India",
    "PIA": "Pakistan", "ALK": "Srilankan",
    # Oceania
    "ANZ": "New Zealand", "JST": "Jetstar", "QFA": "Qantas", "FJI": "Fiji",
    # Cargo and charter often seen in the sim
    "ABW": "Airbridge Cargo", "BOX": "German Cargo", "CLX": "Cargolux", "NCA": "Nippon Cargo",
    "SQC": "Singcargo", "ATN": "Air Transport", "ABX": "Abex", "LTG": "Longtail",
}



@dataclass(frozen=True)
class Known:
    """One of the world's airlines, from ``airlines.tsv.gz``."""

    icao: str
    iata: str  # its two-letter code ("" none): also its logo's
    telephony: str  # its radio callsign ("Clipper"); "" none known
    name: str  # the company ("Pan American World Airways")
    home: str  # its country's airports' ICAO prefix ("K"); "" unknown
    defunct: bool
    article: str  # its Wikipedia article ("" none)
    logo: str = ""  # its logo's file on Wikimedia Commons ("" none known)


DATA = Path(__file__).with_name("airlines.tsv.gz")


@lru_cache(maxsize=1)
def world() -> dict[str, tuple[Known, ...]]:
    """Every airline by its ICAO code: the one flying today first where the code has had more than one."""
    out: dict[str, list[Known]] = {}
    with gzip.open(DATA, "rt", encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 7:
                out.setdefault(p[0], []).append(Known(p[0], p[1], p[2], p[3], p[4], p[5] == "1", p[6],
                                                      p[7] if len(p) > 7 else ""))
    return {code: tuple(rows) for code, rows in out.items()}


def known(code: str) -> Known | None:
    """The airline flying under ``code`` (today's where it was given again), or None."""
    rows = world().get(code.strip().upper())
    return rows[0] if rows else None


# The world's callsigns behind the table's own (the table's first, so a name it knows is found as it writes it).
TELEPHONY.update({code: rows[0].telephony for code, rows in world().items()
                  if code not in TELEPHONY and rows[0].telephony})

# The number: a digit, then up to three more letters or digits ("216", "48K", "9SY", "18AB"). Only a digit and one
# letter counted: Ryanair's "RYR9SY", Turkish "THY 1YH" and Etihad "ETD 1UH" were spelled out as codes.
CALLSIGN_RE = re.compile(r"^([A-Z]{3})(\d[A-Z\d]{0,3})$")


def split(ident: str) -> tuple[str, str, str] | None:
    """ "ACA216" -> ("ACA", "Air Canada", "216"), or None when it isn't an airline flight number.

    Only codes in the table count. "N172LT" and "DP32" must stay registrations, and a three-letter
    code nobody knows is better spelled out than given a name that was guessed.
    """
    match = CALLSIGN_RE.match(ident.strip().upper().replace(" ", ""))  # ("RYR 9CC" as some feeds write it)
    if match is None:
        return None
    code, number = match.groups()
    telephony = TELEPHONY.get(code)
    return (code, telephony, number) if telephony else None


def telephony_for(code: str) -> str | None:
    return TELEPHONY.get(code.strip().upper())


def code_for(telephony: str) -> str | None:
    """The ICAO code an airline name belongs to, for a sim that gives the name but no callsign: its callsign
    ("Speedbird"), its company's name ("British Airways"), else the longest callsign it starts with (the sim writes
    "American Airlines" where the radio says "American")."""
    wanted = " ".join(telephony.strip().lower().split())
    if not wanted:
        return None
    names = _names()
    if wanted in names:
        return names[wanted]
    best = max((name for name in names if wanted.startswith(name + " ") and name in _callsigns()), key=len, default="")
    return names[best] if best else None


@lru_cache(maxsize=1)
def _callsigns() -> frozenset[str]:
    return frozenset(name.lower() for name in TELEPHONY.values())


@lru_cache(maxsize=1)
def _names() -> dict[str, str]:
    """Callsigns and companies' names (lower case) -> their code: callsigns first, then today's airlines' names,
    then the defunct ones'."""
    out: dict[str, str] = {}
    for code, name in TELEPHONY.items():
        out.setdefault(name.lower(), code)
    for gone in (False, True):
        for code, rows in world().items():
            for k in rows:
                if k.defunct == gone:
                    out.setdefault(k.name.lower(), code)
    return out
