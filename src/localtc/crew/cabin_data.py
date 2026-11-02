"""What the cabin crew know about the flight they're on: the airline (its name as passengers know it, its loyalty
programme and alliance, the languages it announces in, whether drinks are free), the crew's names where the airline is
from, the aircraft's doors, and the destination (its city, from Natural Earth's places, and a word about it).

An airline that isn't here is announced by the name ATC calls it, in its country's English; a flight that isn't an
airline's (a private jet, a Cessna) has no cabin crew at all.
"""

import zlib
from dataclasses import dataclass

from localtc.crew.places import places
from localtc.sim_api.geo import haversine_nm


@dataclass(frozen=True)
class Airline:
    name: str  # as the passengers know it ("Air Canada")
    home: str  # its country's airports' ICAO prefix ("C"): its crews' English and names
    program: str = ""  # the loyalty programme ("Aeroplan"); "" none
    alliance: str = ""  # "Star Alliance", "oneworld", "SkyTeam"
    languages: tuple[str, ...] = ("en",)  # announced in these, the first first (only the ones LocalTC's voices speak)
    low_cost: bool = False  # the food and drink are for sale


STAR, ONEWORLD, SKYTEAM = "Star Alliance", "oneworld", "SkyTeam"
AIRLINES: dict[str, Airline] = {
    # North America
    "ACA": Airline("Air Canada", "C", "Aeroplan", STAR, ("en", "fr")),
    "ROU": Airline("Air Canada Rouge", "C", "Aeroplan", STAR, ("en", "fr")),
    "JZA": Airline("Air Canada Express", "C", "Aeroplan", STAR, ("en", "fr")),
    "WJA": Airline("WestJet", "C", "WestJet Rewards", languages=("en", "fr")),
    "POE": Airline("Porter", "C", "VIPorter", languages=("en", "fr")),
    "TSC": Airline("Air Transat", "C", languages=("fr", "en")),
    "AAL": Airline("American Airlines", "K", "AAdvantage", ONEWORLD),
    "DAL": Airline("Delta", "K", "SkyMiles", SKYTEAM),
    "UAL": Airline("United", "K", "MileagePlus", STAR),
    "SWA": Airline("Southwest", "K", "Rapid Rewards"),
    "ASA": Airline("Alaska Airlines", "K", "Atmos Rewards", ONEWORLD),
    "HAL": Airline("Hawaiian Airlines", "PH"),
    "JBU": Airline("JetBlue", "K", "TrueBlue"),
    "NKS": Airline("Spirit", "K", "Free Spirit", low_cost=True),
    "FFT": Airline("Frontier", "K", "Frontier Miles", low_cost=True),
    "SCX": Airline("Sun Country", "K", low_cost=True),
    "AMX": Airline("Aeromexico", "MM", "Aeromexico Rewards", SKYTEAM, ("es", "en")),
    "VOI": Airline("Volaris", "MM", languages=("es", "en"), low_cost=True),
    "VIV": Airline("Viva", "MM", languages=("es", "en"), low_cost=True),
    "CMP": Airline("Copa Airlines", "MP", "ConnectMiles", STAR, ("es", "en")),
    # Europe
    "BAW": Airline("British Airways", "EG", "British Airways Club", ONEWORLD),
    "VIR": Airline("Virgin Atlantic", "EG", "Flying Club", SKYTEAM),
    "EZY": Airline("easyJet", "EG", low_cost=True),
    "RYR": Airline("Ryanair", "EI", low_cost=True),
    "EIN": Airline("Aer Lingus", "EI", "AerClub"),
    "DLH": Airline("Lufthansa", "ED", "Miles & More", STAR, ("de", "en")),
    "EWG": Airline("Eurowings", "ED", languages=("de", "en"), low_cost=True),
    "CFG": Airline("Condor", "ED", languages=("de", "en")),
    "SWR": Airline("Swiss", "LS", "Miles & More", STAR, ("de", "en")),
    "AUA": Airline("Austrian", "LO", "Miles & More", STAR, ("de", "en")),
    "BEL": Airline("Brussels Airlines", "EB", "Miles & More", STAR, ("nl", "en")),
    "AFR": Airline("Air France", "LF", "Flying Blue", SKYTEAM, ("fr", "en")),
    "TVF": Airline("Transavia", "LF", "Flying Blue", languages=("fr", "en"), low_cost=True),
    "KLM": Airline("KLM", "EH", "Flying Blue", SKYTEAM, ("nl", "en")),
    "TRA": Airline("Transavia", "EH", "Flying Blue", languages=("nl", "en"), low_cost=True),
    "IBE": Airline("Iberia", "LE", "Iberia Club", ONEWORLD, ("es", "en")),
    "VLG": Airline("Vueling", "LE", "Vueling Club", languages=("es", "en"), low_cost=True),
    "AEA": Airline("Air Europa", "LE", "SUMA", SKYTEAM, ("es", "en")),
    "TAP": Airline("TAP Air Portugal", "LP", "Miles&Go", STAR, ("pt", "en")),
    "ITY": Airline("ITA Airways", "LI", "Volare", STAR, ("it", "en")),
    "SAS": Airline("SAS", "ES", "EuroBonus", SKYTEAM, ("sv", "en")),
    "NOZ": Airline("Norwegian", "EN", "Norwegian Reward", low_cost=True),
    "NAX": Airline("Norwegian", "EN", "Norwegian Reward", low_cost=True),
    "FIN": Airline("Finnair", "EF", "Finnair Plus", ONEWORLD),
    "ICE": Airline("Icelandair", "BI", "Saga Club"),
    "LOT": Airline("LOT Polish Airlines", "EP", "Miles & More", STAR, ("pl", "en")),
    "CSA": Airline("Czech Airlines", "LK"),
    "WZZ": Airline("Wizz Air", "LH", low_cost=True),
    "AFL": Airline("Aeroflot", "U", "Aeroflot Bonus", languages=("ru", "en")),
    "SBI": Airline("S7 Airlines", "U", "S7 Priority", languages=("ru", "en")),
    "THY": Airline("Turkish Airlines", "LT", "Miles&Smiles", STAR),
    "PGT": Airline("Pegasus", "LT", "BolBol", low_cost=True),
    "AEE": Airline("Aegean", "LG", "Miles+Bonus", STAR),
    "ELY": Airline("El Al", "LL", "Matmid"),
    # The Middle East and Africa
    "UAE": Airline("Emirates", "OM", "Emirates Skywards"),
    "FDB": Airline("flydubai", "OM", low_cost=True),
    "ETD": Airline("Etihad", "OM", "Etihad Guest"),
    "QTR": Airline("Qatar Airways", "OT", "Privilege Club", ONEWORLD),
    "SVA": Airline("Saudia", "OE", "Alfursan", SKYTEAM),
    "MSR": Airline("EgyptAir", "HE", "EgyptAir Plus", STAR),
    "RAM": Airline("Royal Air Maroc", "GM", "Safar Flyer", ONEWORLD, ("fr", "en")),
    "ETH": Airline("Ethiopian Airlines", "HA", "ShebaMiles", STAR),
    "KQA": Airline("Kenya Airways", "HK", "Asante Rewards", SKYTEAM),
    "SAA": Airline("South African Airways", "FA", "Voyager", STAR),
    # Asia and the Pacific
    "SIA": Airline("Singapore Airlines", "WS", "KrisFlyer", STAR),
    "CPA": Airline("Cathay Pacific", "VH", "Asia Miles", ONEWORLD),
    "JAL": Airline("Japan Airlines", "RJ", "JAL Mileage Bank", ONEWORLD),
    "ANA": Airline("ANA", "RJ", "ANA Mileage Club", STAR),
    "KAL": Airline("Korean Air", "RK", "SKYPASS", SKYTEAM),
    "AAR": Airline("Asiana", "RK", "Asiana Club", STAR),
    "CCA": Airline("Air China", "Z", "PhoenixMiles", STAR, ("zh", "en")),
    "CES": Airline("China Eastern", "Z", "Eastern Miles", SKYTEAM, ("zh", "en")),
    "CSN": Airline("China Southern", "Z", "Sky Pearl Club", languages=("zh", "en")),
    "EVA": Airline("EVA Air", "RC", "Infinity MileageLands", STAR, ("zh", "en")),
    "CAL": Airline("China Airlines", "RC", "Dynasty Flyer", SKYTEAM, ("zh", "en")),
    "THA": Airline("Thai Airways", "VT", "Royal Orchid Plus", STAR),
    "MAS": Airline("Malaysia Airlines", "WM", "Enrich", ONEWORLD),
    "GIA": Airline("Garuda Indonesia", "WI", "GarudaMiles", SKYTEAM),
    "PAL": Airline("Philippine Airlines", "RP", "Mabuhay Miles"),
    "HVN": Airline("Vietnam Airlines", "VV", "Lotusmiles", SKYTEAM),
    "AIC": Airline("Air India", "VI", "Maharaja Club", STAR),
    "IGO": Airline("IndiGo", "VI", "BluChip", low_cost=True),
    "AXM": Airline("AirAsia", "WM", low_cost=True),
    "QFA": Airline("Qantas", "Y", "Qantas Frequent Flyer", ONEWORLD),
    "JST": Airline("Jetstar", "Y", low_cost=True),
    "VOZ": Airline("Virgin Australia", "Y", "Velocity"),
    "ANZ": Airline("Air New Zealand", "NZ", "Airpoints", STAR),
    # South America
    "TAM": Airline("LATAM", "SB", "LATAM Pass", languages=("pt", "en")),
    "LAN": Airline("LATAM", "SC", "LATAM Pass", languages=("es", "en")),
    "GLO": Airline("GOL", "SB", "Smiles", languages=("pt", "en")),
    "AZU": Airline("Azul", "SB", "TudoAzul", languages=("pt", "en")),
    "AVA": Airline("Avianca", "SK", "lifemiles", STAR, ("es", "en")),
    "ARG": Airline("Aerolíneas Argentinas", "SA", "Aerolíneas Plus", SKYTEAM, ("es", "en")),
}
# The regionals flying for a mainline airline: announced as its partner ("Delta Connection").
REGIONALS = {"SKW": "DAL", "EDV": "DAL", "RPA": "AAL", "ENY": "AAL", "PDT": "AAL", "JIA": "AAL", "GJS": "UAL",
             "ASH": "UAL", "QXE": "ASA", "CJT": "BAW", "CFE": "BAW", "CLH": "DLH", "KLC": "KLM", "HOP": "AFR"}
CONNECTION = {"DAL": "Delta Connection", "AAL": "American Eagle", "UAL": "United Express", "ASA": "Alaska Airlines",
              "BAW": "BA CityFlyer", "DLH": "Lufthansa CityLine", "KLM": "KLM Cityhopper", "AFR": "Air France Hop"}

# The aircraft's exits, for the safety demonstration: how many, and where.
EXITS = {
    "A318": (6, "two at the front, two over the wings and two at the rear"),
    "A319": (6, "two at the front, two over the wings and two at the rear"),
    "A320": (8, "two at the front, four over the wings and two at the rear"),
    "A321": (8, "two at the front, two either side of the wings and two at the rear"),
    "B737": (8, "two at the front, four over the wings and two at the rear"),
    "B738": (8, "two at the front, four over the wings and two at the rear"),
    "B739": (8, "two at the front, four over the wings and two at the rear"),
    "B38M": (8, "two at the front, four over the wings and two at the rear"),
    "B39M": (8, "two at the front, four over the wings and two at the rear"),
    "A20N": (8, "two at the front, four over the wings and two at the rear"),
    "A21N": (8, "two at the front, two either side of the wings and two at the rear"),
    "A19N": (6, "two at the front, two over the wings and two at the rear"),
    "BCS1": (4, "two at the front and two at the rear"),
    "BCS3": (6, "two at the front, two over the wings and two at the rear"),
    "E170": (6, "two at the front, two over the wings and two at the rear"),
    "E175": (6, "two at the front, two over the wings and two at the rear"),
    "E190": (6, "two at the front, two over the wings and two at the rear"),
    "E195": (6, "two at the front, two over the wings and two at the rear"),
    "CRJ7": (4, "two at the front and two over the wings"),
    "CRJ9": (4, "two at the front and two over the wings"),
    "DH8D": (4, "two at the front and two at the rear"),
    "AT76": (4, "two at the front and two at the rear"),
    "B752": (8, "two at the front, four over the wings and two at the rear"),
    "B763": (8, "doors along both sides of the cabin: four on each side"),
    "B772": (8, "doors along both sides of the cabin: four on each side"),
    "B77W": (10, "doors along both sides of the cabin: five on each side"),
    "B788": (8, "doors along both sides of the cabin: four on each side"),
    "B789": (8, "doors along both sides of the cabin: four on each side"),
    "B78X": (8, "doors along both sides of the cabin: four on each side"),
    "B744": (10, "doors along both sides of the main deck, and two upstairs"),
    "B748": (10, "doors along both sides of the main deck, and two upstairs"),
    "A332": (8, "doors along both sides of the cabin: four on each side"),
    "A333": (8, "doors along both sides of the cabin: four on each side"),
    "A339": (8, "doors along both sides of the cabin: four on each side"),
    "A359": (8, "doors along both sides of the cabin: four on each side"),
    "A35K": (8, "doors along both sides of the cabin: four on each side"),
    "A388": (16, "doors along both sides of both decks: ten on the main deck and six upstairs"),
}
SEATS = {"A318": 110, "A319": 140, "A19N": 140, "A320": 174, "A20N": 180, "A321": 200, "A21N": 210, "B737": 140,
         "B738": 175, "B38M": 175, "B739": 185, "B39M": 190, "BCS1": 120, "BCS3": 145, "E170": 72, "E175": 76,
         "E190": 100, "E195": 120, "CRJ7": 70, "CRJ9": 76, "DH8D": 78, "AT76": 70, "B752": 190, "B763": 220,
         "B772": 300, "B77W": 360, "B788": 240, "B789": 290, "B78X": 330, "B744": 380, "B748": 360, "A332": 250,
         "A333": 290, "A339": 290, "A359": 310, "A35K": 350, "A388": 500}
# Their families, for a type the tables above don't list ("A20N" is an A320 to its passengers).
FAMILY_SEATS = (("A38", 500), ("A35", 310), ("A34", 280), ("A33", 280), ("B78", 260), ("B77", 320), ("B74", 380),
                ("B76", 220), ("B75", 190), ("B73", 160), ("A32", 180), ("A31", 140), ("A22", 130), ("E1", 90),
                ("E2", 110), ("CRJ", 70), ("DH8", 70), ("AT7", 70), ("MD8", 150), ("MD1", 280))

# The crew's names where the airline is from (by the language of its country's English: region.ACCENTS).
NAMES = {
    "en": ({"F": ("Claire", "Emma", "Sophie", "Hannah", "Olivia", "Grace", "Megan", "Lauren"),
            "M": ("James", "Daniel", "Ryan", "Tom", "Marcus", "Ben", "Chris", "Liam")}),
    "de": {"F": ("Katrin", "Julia", "Anna", "Lena", "Sabine", "Miriam"), "M": ("Lukas", "Jonas", "Stefan", "Felix", "Tobias")},
    "fr": {"F": ("Camille", "Chloé", "Élodie", "Manon", "Julie", "Céline"), "M": ("Julien", "Thomas", "Hugo", "Antoine", "Mathieu")},
    "es": {"F": ("Lucía", "María", "Carmen", "Elena", "Sofía", "Paula"), "M": ("Javier", "Pablo", "Diego", "Alejandro", "Carlos")},
    "it": {"F": ("Giulia", "Chiara", "Francesca", "Sara", "Elena"), "M": ("Marco", "Luca", "Matteo", "Andrea", "Davide")},
    "pt": {"F": ("Beatriz", "Mariana", "Inês", "Ana", "Carolina"), "M": ("João", "Tiago", "Rafael", "Pedro", "Gabriel")},
    "nl": {"F": ("Sanne", "Lotte", "Femke", "Eva", "Anouk"), "M": ("Daan", "Bram", "Joost", "Thijs", "Ruben")},
    "sv": {"F": ("Ingrid", "Sara", "Elin", "Maja", "Astrid"), "M": ("Erik", "Oskar", "Lars", "Nils", "Anders")},
    "fi": {"F": ("Aino", "Laura", "Emilia", "Sanna"), "M": ("Mikko", "Juha", "Antti", "Ville")},
    "pl": {"F": ("Kasia", "Agnieszka", "Magda", "Ola", "Zuzanna"), "M": ("Piotr", "Tomasz", "Kuba", "Marek")},
    "ru": {"F": ("Anastasia", "Olga", "Daria", "Yulia", "Irina"), "M": ("Dmitri", "Sergei", "Alexei", "Ivan")},
    "el": {"F": ("Eleni", "Maria", "Sofia", "Katerina"), "M": ("Nikos", "Giorgos", "Kostas", "Dimitris")},
    "tr": {"F": ("Elif", "Zeynep", "Ayşe", "Deniz"), "M": ("Emre", "Burak", "Mehmet", "Can")},
    "ar": {"F": ("Layla", "Noor", "Mariam", "Yasmin", "Rania"), "M": ("Omar", "Karim", "Yousef", "Tariq")},
    "he": {"F": ("Noa", "Tamar", "Shira", "Maya"), "M": ("Yoni", "Eitan", "Amir", "Daniel")},
    "hi": {"F": ("Priya", "Ananya", "Kavya", "Neha", "Divya"), "M": ("Arjun", "Rohan", "Vikram", "Rahul")},
    "zh": {"F": ("Li Na", "Wang Fang", "Chen Jing", "Zhang Wei", "Liu Yang"), "M": ("Wang Lei", "Li Jun", "Zhang Hao", "Chen Bo")},
    "ja": {"F": ("Yuki", "Haruka", "Aiko", "Sakura", "Mei"), "M": ("Kenji", "Takumi", "Hiroshi", "Daiki")},
    "ko": {"F": ("Ji-woo", "Seo-yeon", "Min-ji", "Ha-eun"), "M": ("Min-jun", "Seo-jun", "Ji-ho", "Hyun-woo")},
    "th": {"F": ("Ploy", "Nok", "Fah", "Mint"), "M": ("Krit", "Somchai", "Anan", "Niran")},
    "id": {"F": ("Putri", "Dewi", "Sari", "Ayu"), "M": ("Budi", "Agus", "Rizky", "Adi")},
    "ms": {"F": ("Aisyah", "Nurul", "Siti", "Farah"), "M": ("Hafiz", "Amir", "Faiz", "Irfan")},
    "vi": {"F": ("Linh", "Mai", "Lan", "Huong"), "M": ("Minh", "Duc", "Tuan", "Hoang")},
    "am": {"F": ("Selam", "Hana", "Meron", "Liya"), "M": ("Dawit", "Yonas", "Abel", "Samuel")},
}
NAME_LANGUAGE = {"en-in": "hi", "ur": "hi", "bn": "hi", "ne": "hi", "si": "hi", "fa": "ar", "da": "sv", "nb": "sv",
                 "is": "sv", "uk": "ru", "kk": "ru", "uz": "ru", "az": "ru", "ka": "ru", "hy": "ru", "cs": "pl",
                 "sk": "pl", "hu": "pl", "ro": "it", "bg": "ru", "hr": "pl", "sl": "pl", "sr": "pl", "bs": "pl",
                 "mk": "pl", "sq": "it", "lt": "pl", "lv": "pl", "et": "fi", "km": "th", "en-sg": "zh", "en-hk": "zh",
                 "en-ph": "en", "fil": "en"}


def crc(text: str) -> int:
    return zlib.crc32(text.encode())


def name_language(locale: str) -> str:
    """Whose names a crew of ``locale``'s English has ("de-DE": German; "en-IN": Indian)."""
    low = (locale or "en").lower()
    if low in NAME_LANGUAGE:
        return NAME_LANGUAGE[low]
    lang = low.split("-")[0]
    lang = NAME_LANGUAGE.get(lang, lang)
    return lang if lang in NAMES else "en"


def crew_name(locale: str, sex: str, seed: str) -> str:
    """A name from ``locale``'s country, of ``sex``, the same for the same ``seed`` (the flight)."""
    pool = NAMES[name_language(locale)]["F" if sex == "F" else "M"]
    return pool[crc(seed) % len(pool)]


# What the sim calls a type ("A220-300", its ATC MODEL) to its ICAO designator.
SIM_NAMES = (("A220-100", "BCS1"), ("CS100", "BCS1"), ("A220-300", "BCS3"), ("CS300", "BCS3"), ("A220", "BCS3"),
             ("A319NEO", "A19N"), ("A320NEO", "A20N"), ("A321NEO", "A21N"), ("A319", "A319"), ("A320", "A320"),
             ("A321", "A321"), ("A330-200", "A332"), ("A330-300", "A333"), ("A330-900", "A339"), ("A350-900", "A359"),
             ("A350-1000", "A35K"), ("A380", "A388"), ("737-MAX8", "B38M"), ("737 MAX 8", "B38M"), ("737-700", "B737"),
             ("737-800", "B738"), ("737-900", "B739"), ("757-200", "B752"), ("767-300", "B763"), ("777-200", "B772"),
             ("777-300", "B77W"), ("787-8", "B788"), ("787-9", "B789"), ("787-10", "B78X"), ("747-400", "B744"),
             ("747-8", "B748"))


def designator(aircraft: str) -> str:
    """The ICAO type of what the flight or the sim calls it ("A220-300": BCS3)."""
    code = (aircraft or "").upper().strip()
    if code in SEATS:
        return code
    return next((icao for name, icao in SIM_NAMES if name in code), code)


@dataclass(frozen=True)
class AircraftFacts:
    name: str  # as a passenger would say it
    fact: str  # a true line about it, for the cabin to tell
    overwing: bool = False  # the exit over the wing is a hatch or door the passengers beside it open


# By the start of the ICAO type, the longest first.
AIRCRAFT = (
    ("BCS", AircraftFacts("Airbus A220", "it seats two on one side and three on the other, and its windows are the largest "
                                         "of any single-aisle airliner", True)),
    ("A19N", AircraftFacts("Airbus A319neo", "neo stands for new engine option, and those engines burn around a fifth less fuel", True)),
    ("A20N", AircraftFacts("Airbus A320neo", "neo stands for new engine option, and those engines burn around a fifth less fuel", True)),
    ("A21N", AircraftFacts("Airbus A321neo", "neo stands for new engine option, and those engines burn around a fifth less fuel", True)),
    ("A31", AircraftFacts("Airbus A319", "its family was the first airliner flown by digital fly-by-wire, with sidesticks instead of control columns", True)),
    ("A32", AircraftFacts("Airbus A320", "it was the first airliner flown by digital fly-by-wire, with sidesticks instead of control columns", True)),
    ("A33", AircraftFacts("Airbus A330", "it's a twin-aisle built for long ocean crossings on two engines")),
    ("A35", AircraftFacts("Airbus A350", "more than half of its structure is carbon-fibre composite, and the cabin is kept at the "
                                         "pressure of about six thousand feet, so you should feel fresher when we land")),
    ("A38", AircraftFacts("Airbus A380", "it's the largest passenger airliner ever built, with two full decks from nose to tail")),
    ("B38", AircraftFacts("Boeing 737 MAX", "the tips of its wings split in two, to save fuel", True)),
    ("B39", AircraftFacts("Boeing 737 MAX", "the tips of its wings split in two, to save fuel", True)),
    ("B73", AircraftFacts("Boeing 737", "the first one flew in 1967, and it's still in production today", True)),
    ("B75", AircraftFacts("Boeing 757", "it's a single-aisle with so much power that pilots call it a rocket")),
    ("B76", AircraftFacts("Boeing 767", "it's a twin-aisle with two seats by the window on each side")),
    ("B74", AircraftFacts("Boeing 747", "they call it the Queen of the Skies, and it first flew in 1969")),
    ("B77", AircraftFacts("Boeing 777", "its engines are among the most powerful ever put on an airliner")),
    ("B78", AircraftFacts("Boeing 787 Dreamliner", "about half of it is composite, and instead of blinds its windows dim at the touch of a button")),
    ("E1", AircraftFacts("Embraer E-Jet", "it seats two and two, so nobody has a middle seat")),
    ("E2", AircraftFacts("Embraer E-Jet", "it seats two and two, so nobody has a middle seat")),
    ("CRJ", AircraftFacts("Bombardier CRJ", "it seats two and two, so nobody has a middle seat")),
    ("DH8", AircraftFacts("Dash 8", "it's a turboprop, and one of the fastest flying")),
    ("AT7", AircraftFacts("ATR 72", "it's a turboprop, among the most fuel-efficient airliners flying")),
)


def aircraft_facts(aircraft: str) -> AircraftFacts | None:
    code = designator(aircraft)
    return next((f for prefix, f in AIRCRAFT if code.startswith(prefix)), None)


def seats(aircraft: str) -> int:
    """About how many seats an aircraft type has (0: not an airliner LocalTC knows)."""
    code = designator(aircraft)
    if code in SEATS:
        return SEATS[code]
    return next((n for prefix, n in FAMILY_SEATS if code.startswith(prefix)), 0)


def exits(aircraft: str) -> tuple[int, str]:
    """The exits' count and where they are; (0, "") where the type isn't known."""
    code = designator(aircraft)
    if code in EXITS:
        return EXITS[code]
    n = seats(code)
    if n >= 220:
        return 8, "doors along both sides of the cabin"
    if n:
        return 0, "at the front, over the wings and at the rear"
    return 0, ""


def airline_of(callsign: str) -> tuple[str, Airline | None]:
    """The airline flying ``callsign`` ("ACA123") and its record (None: one LocalTC doesn't know, or not an
    airline's callsign). A regional flying for a mainline airline comes back as the mainline's partner brand."""
    code = "".join(ch for ch in (callsign or "")[:3].upper() if ch.isalpha())
    if len(code) != 3 or not (callsign or "")[3:4].isdigit():
        return code if len(code) == 3 else "", None
    if code in AIRLINES:
        return code, AIRLINES[code]
    parent = REGIONALS.get(code)
    if parent in AIRLINES:
        main = AIRLINES[parent]
        return code, Airline(CONNECTION.get(parent, main.name), main.home, main.program, main.alliance, main.languages)
    return code, None


def flight_number(callsign: str) -> str:
    """ "ACA123" -> "123"; "" where there's no number."""
    digits = (callsign or "")[3:]
    return digits if digits[:1].isdigit() else ""


@dataclass(frozen=True)
class City:
    name: str
    region: str
    country: str
    population: int


def city_of(lat: float, lon: float) -> City | None:
    """The city an airport serves: the biggest place near it (a big city a little further away wins over the town
    the runway is in)."""
    best, score = None, 0.0
    for p in places():
        if abs(p.lat - lat) > 0.8 or abs(p.lon - lon) > 1.2:
            continue
        d = haversine_nm(lat, lon, p.lat, p.lon)
        if d > 35:
            continue
        s = p.population / (1 + d / 8) ** 2
        if s > score:
            best, score = p, s
    return City(best.name, best.region, best.country, best.population) if best is not None else None


COUNTRIES = {
    "US": "the United States", "CA": "Canada", "MX": "Mexico", "GB": "the United Kingdom", "IE": "Ireland",
    "FR": "France", "DE": "Germany", "ES": "Spain", "IT": "Italy", "PT": "Portugal", "NL": "the Netherlands",
    "BE": "Belgium", "CH": "Switzerland", "AT": "Austria", "DK": "Denmark", "SE": "Sweden", "NO": "Norway",
    "FI": "Finland", "IS": "Iceland", "PL": "Poland", "CZ": "Czechia", "HU": "Hungary", "GR": "Greece", "TR": "Türkiye",
    "RU": "Russia", "AE": "the United Arab Emirates", "QA": "Qatar", "SA": "Saudi Arabia", "IL": "Israel",
    "EG": "Egypt", "MA": "Morocco", "ZA": "South Africa", "KE": "Kenya", "ET": "Ethiopia", "IN": "India",
    "CN": "China", "HK": "Hong Kong", "TW": "Taiwan", "JP": "Japan", "KR": "South Korea", "SG": "Singapore",
    "TH": "Thailand", "MY": "Malaysia", "ID": "Indonesia", "PH": "the Philippines", "VN": "Vietnam",
    "AU": "Australia", "NZ": "New Zealand", "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "PE": "Peru", "PA": "Panama", "CR": "Costa Rica", "CU": "Cuba", "DO": "the Dominican Republic", "JM": "Jamaica",
    "HR": "Croatia", "RO": "Romania", "BG": "Bulgaria", "RS": "Serbia", "UA": "Ukraine", "CY": "Cyprus", "MT": "Malta",
    "LU": "Luxembourg", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania", "SK": "Slovakia", "SI": "Slovenia",
}

# A word about a destination, for the announcements on the way down and after landing (by city, as places.tsv names it).
FACTS = {
    "London": "home to more than three hundred languages spoken on its streets",
    "Paris": "where the Eiffel Tower was only meant to stand for twenty years",
    "Munich": "the capital of Bavaria, with the Alps just an hour to the south",
    "Frankfurt": "Germany's financial capital, on the river Main",
    "Berlin": "a city with more bridges than Venice",
    "Amsterdam": "a city with more bicycles than people",
    "Madrid": "Europe's highest capital city",
    "Barcelona": "home of the Sagrada Família, still being built after more than a century",
    "Rome": "the Eternal City",
    "Milan": "Italy's capital of fashion and design",
    "Lisbon": "a city of seven hills, on the river Tagus",
    "Dublin": "Ireland's capital, on the river Liffey",
    "Zurich": "Switzerland's largest city, on the shore of its lake",
    "Geneva": "home to the United Nations' European headquarters",
    "Vienna": "the city of music, and of coffee houses",
    "Copenhagen": "the home of the Little Mermaid",
    "Stockholm": "a capital built on fourteen islands",
    "Oslo": "at the head of the Oslofjord",
    "Helsinki": "the Daughter of the Baltic",
    "Reykjavik": "the world's most northerly capital",
    "Warsaw": "Poland's capital, on the river Vistula",
    "Prague": "the city of a hundred spires",
    "Budapest": "two cities, Buda and Pest, either side of the Danube",
    "Athens": "one of the oldest cities in the world",
    "Istanbul": "the city on two continents",
    "Moscow": "Russia's capital, on the Moskva river",
    "Dubai": "home of the world's tallest building",
    "Doha": "Qatar's capital, on the Persian Gulf",
    "Tel Aviv-Yafo": "the city on the Mediterranean that never sleeps",
    "Cairo": "with the pyramids of Giza at the edge of the city",
    "Johannesburg": "the City of Gold",
    "Cape Town": "beneath Table Mountain",
    "Nairobi": "the only capital with a national park inside its limits",
    "Toronto": "Canada's largest city",
    "Montreal": "the second-largest French-speaking city in the world",
    "Vancouver": "between the mountains and the Pacific",
    "Calgary": "the home of the Stampede",
    "Ottawa": "Canada's capital",
    "New York": "the city that never sleeps",
    "Los Angeles": "the home of Hollywood",
    "Chicago": "the Windy City",
    "San Francisco": "the city by the bay",
    "Seattle": "the Emerald City",
    "Miami": "the Magic City",
    "Boston": "one of America's oldest cities",
    "Washington, D.C.": "the nation's capital",
    "Denver": "the Mile High City, exactly one mile above sea level",
    "Las Vegas": "the Entertainment Capital of the World",
    "Atlanta": "home to the world's busiest airport",
    "Dallas": "Big D",
    "Houston": "Space City",
    "Phoenix": "the Valley of the Sun",
    "Honolulu": "on the island of Oahu",
    "Mexico City": "built on the site of the Aztec capital Tenochtitlan",
    "Cancun": "on the Caribbean coast of the Yucatán",
    "Sao Paulo": "the largest city in the Southern Hemisphere",
    "Rio de Janeiro": "the Marvellous City",
    "Buenos Aires": "the home of the tango",
    "Singapore": "the Lion City",
    "Hong Kong": "home to more skyscrapers than any other city",
    "Tokyo": "one of the largest cities in the world",
    "Osaka": "Japan's kitchen",
    "Seoul": "a capital six hundred years old",
    "Beijing": "the capital of China for most of the last eight centuries",
    "Shanghai": "China's largest city, on the Huangpu river",
    "Bangkok": "the City of Angels",
    "Kuala Lumpur": "home of the Petronas Towers",
    "Sydney": "the Harbour City",
    "Melbourne": "Australia's capital of coffee and culture",
    "Auckland": "the City of Sails",
    "Delhi": "India's capital",
    "Mumbai": "the City of Dreams",
}


def describe(city: City | None) -> str:
    """ "Munich, the capital of Bavaria, ..." or "Leeds, in the United Kingdom"; "" for no city."""
    if city is None:
        return ""
    if city.name in FACTS:
        return f"{city.name}, {FACTS[city.name]}"
    country = COUNTRIES.get(city.country, "")
    return f"{city.name}, in {country}" if country else city.name
