"""The big airlines' two-letter codes and their ICAO three."""

IATA_TO_ICAO = {
    "AA": "AAL", "DL": "DAL", "UA": "UAL", "WN": "SWA", "AS": "ASA", "B6": "JBU", "NK": "NKS", "F9": "FFT",
    "HA": "HAL", "G4": "AAY", "SY": "SCX", "AC": "ACA", "WS": "WJA", "TS": "TSC", "PD": "POE", "RV": "ROU",
    "QK": "JZA", "AM": "AMX", "Y4": "VOI", "VB": "VIV", "BA": "BAW", "VS": "VIR", "U2": "EZY", "FR": "RYR",
    "LS": "EXS", "BY": "TOM", "AF": "AFR", "KL": "KLM", "LH": "DLH", "LX": "SWR", "OS": "AUA", "SN": "BEL",
    "LO": "LOT", "OK": "CSA", "SK": "SAS", "AY": "FIN", "DY": "NOZ", "D8": "IBK", "EI": "EIN", "IB": "IBE",
    "UX": "AEA", "VY": "VLG", "TP": "TAP", "AZ": "ITY", "TK": "THY", "PC": "PGT", "A3": "AEE", "W6": "WZZ",
    "EW": "EWG", "DE": "CFG", "X3": "TUI", "HV": "TRA", "TO": "TVF", "V7": "VOE", "EK": "UAE", "QR": "QTR",
    "EY": "ETD", "SV": "SVA", "GF": "GFA", "WY": "OMA", "FZ": "FDB", "G9": "ABY", "LY": "ELY", "MS": "MSR",
    "ET": "ETH", "KQ": "KQA", "SA": "SAA", "AT": "RAM", "CX": "CPA", "JL": "JAL", "NH": "ANA", "KE": "KAL",
    "OZ": "AAR", "BR": "EVA", "CI": "CAL", "SQ": "SIA", "TR": "TGW", "TG": "THA", "MH": "MAS", "GA": "GIA",
    "PR": "PAL", "5J": "CEB", "VN": "HVN", "CA": "CCA", "MU": "CES", "CZ": "CSN", "HU": "CHH", "3U": "CSC",
    "AI": "AIC", "6E": "IGO", "UK": "VTI", "QF": "QFA", "VA": "VOZ", "JQ": "JST", "NZ": "ANZ", "LA": "LAN",
    "JJ": "TAM", "G3": "GLO", "AD": "AZU", "AV": "AVA", "CM": "CMP", "AR": "ARG", "MX": "MXA", "FX": "FDX",
    "5X": "UPS", "PO": "PAC", "CV": "CLX", "LD": "AHK", "MM": "APJ", "BC": "SKY", "7C": "JJA", "LJ": "JNA", "BX": "ABL", "RS": "ASV", "ZE": "ESR", "RF": "EOK", "TW": "TWB", "ZG": "TZP",
    "OO": "SKW", "9E": "EDV", "YX": "RPA", "MQ": "ENY", "OH": "JIA", "YV": "ASH", "ZW": "AWI", "PT": "PDT",
    "QX": "QXE", "C5": "UCA", "G7": "GJS", "CP": "CPZ", "MW": "MUA",
}


def iata_for(icao: str) -> str:
    """The IATA code the airline flying under ``icao`` has today, for its logo ("FFT" -> "F9"): the table's, else the
    world list's when no other airline flying today has it. "" for a defunct airline (its two letters went to
    another, whose logo it isn't) or one with none."""
    from localtc.atc_core.airlines import known, world

    code = icao.strip().upper()
    for iata, holder in IATA_TO_ICAO.items():
        if holder == code:
            return iata
    k = known(code)
    if k is None or k.defunct or not k.iata or k.iata in IATA_TO_ICAO:
        return ""
    others = [c for c, rows in world().items() if c != code and rows[0].iata == k.iata and not rows[0].defunct]
    return "" if others else k.iata
