"""Airline ICAO codes and the names they are called by on the radio.

A flight plan gives a callsign as an ICAO code and a number ("ACA216"). Spelled out that is
"alpha charlie alpha two one six", which no controller would say: it is "Air Canada 216". This
table turns the code into the telephony name, so `Callsign` can carry both.

Codes not in the table keep their letters, which is right for a registration ("N172LT") and the
best that can be done for an airline nobody has heard of.
"""

import re

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
    "BAW": "Speedbird", "BCS": "Eurotrans", "BEE": "Jersey", "EFW": "Rednose", "EIN": "Shamrock",
    "EXS": "Channex", "EZY": "Easy", "LOG": "Logan", "NPT": "Newport", "RYR": "Ryanair", "TOM": "Tomjet",
    "VIR": "Virgin", "WUK": "Wizz Go",
    # Western Europe
    "AEA": "Europa", "AFR": "Airfrans", "AUA": "Austrian", "BEL": "Beeline", "BER": "Air Berlin",
    "BTI": "Air Baltic", "CFG": "Condor", "CLH": "Hansaline", "CTN": "Croatia", "DLH": "Lufthansa",
    "EWG": "Eurowings", "FIN": "Finnair", "IBE": "Iberia", "IBS": "Iberexpress", "ICE": "Iceair",
    "ITY": "Itarrow", "KLM": "KLM", "LOT": "Lot", "NAX": "Nortrans", "NOZ": "Nordic", "NSZ": "Shuttle",
    "SAS": "Scandinavian", "SWR": "Swiss", "TAP": "Air Portugal", "TRA": "Transavia", "TVF": "France Soleil",
    "VLG": "Vueling", "WZZ": "Wizzair", "SXS": "Sunexpress", "PGT": "Sunturk", "AEE": "Aegean",
    "OAL": "Olympic", "LDM": "Lauda", "SDR": "Sundair", "MSR": "Egyptair", "RAM": "Royalair Maroc",
    # Eastern Europe, Russia, Central Asia
    "AFL": "Aeroflot", "AZA": "Alitalia", "BTK": "Bektas", "CSA": "CSA", "ELY": "Elal", "FPO": "Askair",
"PBD": "Pobeda", "SBI": "Siberian", "SVR": "Sverdlovsk", "SWN": "Sunwing",
    "UTA": "Utair", "AUI": "Ukraine International", "TSO": "Transo",
    # Middle East
    "ABY": "Arabia", "ETD": "Etihad", "FDB": "Skydubai", "GFA": "Gulf Air", "IRA": "Iranair",
    "KAC": "Kuwaiti", "MEA": "Cedar Jet", "OMA": "Oman Air", "QTR": "Qatari", "RJA": "Jordanian",
    "SVA": "Saudia", "THY": "Turkish", "UAE": "Emirates", "JZR": "Jazeera",
    # Africa
    "ETH": "Ethiopian", "KQA": "Kenya", "SAA": "Springbok", "TUN": "Tunair", "DAH": "Air Algerie",
    "MAU": "Airmauritius", "AMC": "Afrigiya",
    # South and East Asia
    "AAR": "Asiana", "AIC": "Airindia", "ANA": "All Nippon", "APJ": "Air Peach", "BBC": "Bangladesh",
    "CAL": "Dynasty", "CCA": "Air China", "CES": "China Eastern", "CSN": "China Southern", "CSZ": "Shenzhen Air",
    "CXA": "Xiamen Air", "EVA": "Eva", "HVN": "Viet Nam Airlines", "IGO": "Ifly", "JAL": "Japanair",
    "JJP": "Orange Liner", "KAL": "Koreanair", "MAS": "Malaysian", "PAL": "Philippine", "SIA": "Singapore",
    "SJX": "Starjet", "THA": "Thai", "TGW": "Go Cat", "TWB": "Teeway", "VJC": "Vietjet",
    "AKX": "Air Asia", "AXM": "Asian Express", "SEJ": "Spicejet", "AIQ": "Red Phoenix",
    # Oceania
    "ANZ": "New Zealand", "JST": "Jetstar", "QFA": "Qantas", "FJI": "Fiji",
    # Cargo and charter often seen in the sim
    "ABW": "Airbridge Cargo", "BOX": "German Cargo", "CLX": "Cargolux", "NCA": "Nippon Cargo",
    "SQC": "Singcargo", "ATN": "Air Transport", "ABX": "Abex", "LTG": "Longtail",
}

CALLSIGN_RE = re.compile(r"^([A-Z]{3})(\d{1,4}[A-Z]?)$")


def split(ident: str) -> tuple[str, str, str] | None:
    """ "ACA216" -> ("ACA", "Air Canada", "216"), or None when it isn't an airline flight number.

    Only codes in the table count. "N172LT" and "DP32" must stay registrations, and a three-letter
    code nobody knows is better spelled out than given a name that was guessed.
    """
    match = CALLSIGN_RE.match(ident.strip().upper())
    if match is None:
        return None
    code, number = match.groups()
    telephony = TELEPHONY.get(code)
    return (code, telephony, number) if telephony else None


def telephony_for(code: str) -> str | None:
    return TELEPHONY.get(code.strip().upper())


def code_for(telephony: str) -> str | None:
    """The ICAO code an airline name belongs to, for a sim that gives the name but no callsign."""
    wanted = " ".join(telephony.strip().lower().split())
    if not wanted:
        return None
    for code, name in TELEPHONY.items():
        # The sim writes the company ("American Airlines") where the radio says the short name.
        if name.lower() == wanted or wanted.startswith(name.lower() + " "):
            return code
    return None
