"""Build LocalTC's list of the world's airlines (src/localtc/atc_core/airlines.tsv.gz): every ICAO airline designator,
flying today or gone, with its radio callsign, its name, its IATA code (for its logo), its country's airports' ICAO
prefix (its crews' English), whether it's defunct and its logo on Wikimedia Commons.

    python tools/make_airlines.py               # download the sources below and rebuild
    python tools/make_airlines.py --from DIR    # from DIR/part_*.wiki, DIR/countries.csv and DIR/logos.json

The sources: Wikipedia's "List of airline codes" pages (CC BY-SA 4.0: the designators, callsigns, names and
countries), OurAirports' countries.csv (public domain: a country's name to its code) and LocalTC's own
airports/world.tsv.gz (a country's code to its airports' ICAO prefix), and Wikidata (CC0: the airlines' logos, files on
Wikimedia Commons, for the ones no logo service has: a defunct airline's two letters are another's now). Written, tab
separated and gzipped: ICAO code, IATA code, telephony, name, home prefix, 1 if defunct, the Wikipedia article, the
logo's Commons file name. A code given to more than one airline
keeps the one flying today (the defunct ones after it), so a reused code means today's airline.

A one-word callsign that is also a word of ATC's or a pilot's ("TAXI", "SPEED", "LINE") is left out: a readback's
"speed 250" isn't another flight's callsign.
"""

import argparse
import csv
import gzip
import io
import json
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "src" / "localtc" / "atc_core" / "airlines.tsv.gz"
WORLD = ROOT / "src" / "localtc" / "airports" / "world.tsv.gz"
PAGES = ["0–9", *"ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
WIKI = "https://en.wikipedia.org/w/index.php"
COUNTRIES = "https://davidmegginson.github.io/ourairports-data/countries.csv"
AGENT = "LocalTC-tools/1.0 (airline list builder)"
WIKIDATA = "https://query.wikidata.org/sparql"
# Every airline Wikidata gives an ICAO code and a logo, with its English Wikipedia article.
LOGOS_QUERY = """SELECT ?item ?icao ?logo ?article WHERE { ?item wdt:P230 ?icao . ?item wdt:P154 ?logo .
  OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . } }"""

# Wikipedia's country names that OurAirports writes otherwise.
COUNTRY_ALIASES = {
    "united states": "US", "usa": "US", "united kingdom": "GB", "uk": "GB", "russia": "RU", "russian federation": "RU",
    "south korea": "KR", "republic of korea": "KR", "north korea": "KP", "iran": "IR", "syria": "SY", "laos": "LA",
    "vietnam": "VN", "viet nam": "VN", "taiwan": "TW", "republic of china": "TW", "hong kong": "HK", "macau": "MO",
    "macao": "MO", "czech republic": "CZ", "czechia": "CZ", "ivory coast": "CI", "côte d'ivoire": "CI",
    "cote d'ivoire": "CI", "democratic republic of the congo": "CD", "dr congo": "CD", "republic of the congo": "CG",
    "congo": "CG", "congo-brazzaville": "CG", "zaire": "CD", "moldova": "MD", "macedonia": "MK",
    "north macedonia": "MK", "republic of macedonia": "MK", "bolivia": "BO", "venezuela": "VE", "tanzania": "TZ",
    "brunei": "BN", "east timor": "TL", "timor-leste": "TL", "cape verde": "CV", "cabo verde": "CV",
    "eswatini": "SZ", "swaziland": "SZ", "the bahamas": "BS", "bahamas": "BS", "the gambia": "GM", "gambia": "GM",
    "micronesia": "FM", "federated states of micronesia": "FM", "palestine": "PS", "vatican": "VA",
    "kosovo": "XK", "myanmar": "MM", "burma": "MM", "kyrgyzstan": "KG", "kirghizia": "KG", "netherlands antilles": "CW",
    "curaçao": "CW", "curacao": "CW", "sint maarten": "SX", "saint martin": "MF", "st. lucia": "LC",
    "saint lucia": "LC", "st. vincent and the grenadines": "VC", "saint vincent and the grenadines": "VC",
    "saint kitts and nevis": "KN", "st. kitts and nevis": "KN", "antigua": "AG", "trinidad": "TT",
    "bosnia": "BA", "bosnia-herzegovina": "BA", "turkey": "TR", "türkiye": "TR", "turkiye": "TR",
    "united arab emirates": "AE", "uae": "AE", "falkland islands": "FK", "faroe islands": "FO", "greenland": "GL",
    "french polynesia": "PF", "new caledonia": "NC", "réunion": "RE", "reunion": "RE", "guadeloupe": "GP",
    "martinique": "MQ", "french guiana": "GF", "puerto rico": "PR", "guam": "GU", "isle of man": "IM",
    "jersey": "JE", "guernsey": "GG", "gibraltar": "GI", "bermuda": "BM", "cayman islands": "KY",
    "british virgin islands": "VG", "us virgin islands": "VI", "u.s. virgin islands": "VI", "aruba": "AW",
    "anguilla": "AI", "montserrat": "MS", "turks and caicos islands": "TC", "west germany": "DE", "east germany": "DE",
    "soviet union": "RU", "ussr": "RU", "yugoslavia": "RS", "serbia and montenegro": "RS", "czechoslovakia": "CZ",
    "laos": "LA", "lao pdr": "LA", "lao people's democratic republic": "LA", "syrian arab republic": "SY",
    "somalia": "SO", "monaco": "FR", "san marino": "IT", "denmark, norway, sweden": "SE", "scandinavia": "SE",
    "rhodesia": "ZW", "ceylon": "LK", "zaïre": "CD", "são tomé and príncipe": "ST", "sao tome and principe": "ST",
}
# Words ATC and pilots say that a one-word callsign mustn't be (a readback's "speed 250", "taxi 3", "line up").
SAID = """runway heading level flight climb descend maintain contact squawk taxi hold speed direct cleared clearance
information approach tower ground departure departures center centre radar delivery wind gate stand apron ramp expect
via cross request ready line wilco roger affirm affirmative negative unable standby traffic vector turn left right
altimeter frequency monitor report final base downwind crosswind upwind pattern circuit visual established localizer
glideslope miles mile knots feet thousand hundred decimal point flaps gear minimum minimums missed go around continue
reduce increase fast slow high low up down north south east west heavy super
emergency mayday pan priority medical fuel souls passengers ok okay thanks thank you good day morning evening night
over out break correction say again confirm repeat check radio check push pushback start engine engines start-up
startup de-icing deicing icing service services charter cargo air jet aero airline airlines airways airway aviation
express regional international national transport freight shuttle flying fly first global world sky blue red star
gold golden sun city island coast mountain wing wings bird eagle phoenix royal united trans inter new""".split()
LETTERS = """alpha alfa bravo charlie delta echo foxtrot golf hotel india juliet juliett kilo lima mike november oscar papa
quebec romeo sierra tango uniform victor whiskey whisky xray x-ray yankee zulu zero one two three four five six seven
eight nine niner ten""".split()
# A light aircraft's callsign is its type's name and its registration ("Baron 5AB", "Citation 12X"): not an airline's.
AIRCRAFT = """cessna skyhawk skylane caravan citation stationair centurion cardinal piper cherokee warrior archer arrow
seneca seminole saratoga malibu meridian mirage navajo comanche cub supercub husky beech beechcraft bonanza baron king
queen duchess debonair mooney cirrus diamond katana pilatus tbm daher socata robin extra lear learjet gulfstream falcon
challenger global hawker phenom praetor legacy lineage embraer bombardier dash twin otter islander trislander dornier
piaggio avanti eclipse honda hondajet vision kodiak quest grumman tiger cheetah lancair glasair vans rv aeronca champ
citabria decathlon luscombe ercoupe stinson taylorcraft helicopter heli robinson bell sikorsky airbus boeing antonov
ilyushin tupolev sukhoi yakovlev douglas lockheed fokker saab atr""".split()


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - fixed https hosts
        return r.read()


def page(letter: str) -> str:
    query = urllib.parse.urlencode({"title": f"List of airline codes ({letter})", "action": "raw"})
    return fetch(f"{WIKI}?{query}").decode("utf-8")


def cells(chunk: str) -> list[str]:
    """A table row's cells: split on a new line's "|" and on "||", but not inside {{ }} or [[ ]]."""
    out, cur, depth, i = [], [], 0, 0
    text = chunk.strip("\n")
    if text.startswith("|"):
        text = text[1:]
    while i < len(text):
        two = text[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            depth = max(0, depth - 1)
            cur.append(two)
            i += 2
            continue
        if depth == 0 and (two == "||" or text[i:i + 2] == "\n|"):
            out.append("".join(cur))
            cur = []
            i += 2
            continue
        cur.append(text[i])
        i += 1
    out.append("".join(cur))
    return [c.strip() for c in out]


def clean(s: str) -> str:
    s = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"\{\{\s*(?:ill|interlanguage link)\s*\|([^|}]*)[^}]*\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\{\{\s*(?:lang|nowrap)\s*\|(?:[^|}]*\|)?([^|}]*)\}\}", r"\1", s, flags=re.I)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", s)
    s = re.sub(r"<[^>]+>", "", s).replace("''", "").replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(s.replace("\t", " ").split()).strip(" ,;")


def article(s: str) -> str:
    """The Wikipedia article an airline's name links to ("" for none)."""
    m = re.search(r"\[\[([^|\]#]+)", s) or re.search(r"\{\{\s*ill\s*\|([^|}]+)", s, flags=re.I)
    return m[1].strip() if m else ""


def spoken(callsign: str, codes: set[str]) -> str:
    """ "SPEEDBIRD" -> "Speedbird", "AIR SAFAR" -> "Air Safar"; an acronym ("TWA", "KLM") stays in capitals."""
    words = []
    for w in callsign.split():
        letters = re.sub(r"[^A-Za-z]", "", w)
        acronym = len(letters) <= 4 and (w.upper() in codes or not re.search(r"[AEIOUY]", letters.upper()))
        words.append(w.upper() if acronym or any(ch.isdigit() for ch in w) else w[:1].upper() + w[1:].lower())
    return " ".join(words)


def homes(countries_csv: str) -> dict[str, str]:
    """A country's name (lower case) -> its airports' ICAO prefix: its own letter where no other country's airports
    start with it ("K", "C", "Y"), else the two letters most of its airports start with ("EG", "VI")."""
    by_iso: dict[str, Counter] = defaultdict(Counter)
    with gzip.open(WORLD, "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 8 and re.fullmatch(r"[A-Z]{4}", parts[0]):
                by_iso[parts[7]][parts[0][:2]] += 1
    letter_of: dict[str, set[str]] = defaultdict(set)
    for iso, twos in by_iso.items():
        for two in twos:
            letter_of[two[0]].add(iso)
    prefix: dict[str, str] = {}
    for iso, twos in by_iso.items():
        letters = Counter()
        for two, n in twos.items():
            letters[two[0]] += n
        letter, _ = letters.most_common(1)[0]
        top = max((two for two in twos if two[0] == letter), key=lambda two: twos[two])
        prefix[iso] = letter if letter_of[letter] == {iso} else top
    names: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(countries_csv)):
        iso = row["code"]
        if iso in prefix:
            names[row["name"].strip().lower()] = prefix[iso]
            for word in (row.get("keywords") or "").split(","):
                if word.strip():
                    names.setdefault(word.strip().lower(), prefix[iso])
    for name, iso in COUNTRY_ALIASES.items():
        if iso in prefix:
            names.setdefault(name, prefix[iso])
    return names


def vocabulary() -> set[str]:
    """Words ATC and pilots say: the phraseology's templates, the readback corpus and the lists above."""
    words = set(SAID) | set(LETTERS) | set(AIRCRAFT)
    for path in (ROOT / "src" / "localtc" / "atc_core" / "phraseology" / "templates").glob("*.toml"):
        words |= set(re.findall(r"[a-z]+", path.read_text(encoding="utf-8").lower()))
    corpus = tomllib.loads((ROOT / "tests" / "data" / "readbacks.toml").read_text(encoding="utf-8"))
    for case in corpus.get("case", corpus.get("cases", [])):
        words |= set(re.findall(r"[a-z]+", str(case.get("text", case.get("heard", ""))).lower()))
    return words


# Gone, though Wikipedia's lists still have them flying (the code, and the name its row gives).
GONE = {("TWA", "Trans World Airlines"), ("JAI", "Jet Airways"), ("TCX", "Thomas Cook Airlines"), ("WOW", "WOW air"),
        ("VTI", "Vistara"), ("BEE", "Flybe"), ("VRD", "Virgin America"), ("FPY", "Play"), ("WSW", "Swoop"),
        ("EAL", "Eastern Air Lines"), ("EAL", "European Air Express"), ("JAT", "Jat Airways"),
        ("MEP", "Midwest Airlines"), ("LTU", "LTU International"), ("TSO", "Transaero Airlines"), ("GMI", "Germania"),
        ("GOW", "GoAir"), ("AMC", "Air Malta"), ("MNO", "Mango"), ("DLH", "Deutsche Luft Hansa"),
        ("AIJ", "Interjet"), ("ONE", "Avianca Brasil")}
# Not in the lists: (ICAO, IATA, callsign, name, home prefix, "1" defunct, article).
EXTRA = [("VRG", "RG", "Varig", "Varig", "SB", "1", "Varig")]
# Flying, though the lists have them gone.
ALIVE = {("SDM", "Rossiya")}
# A code two airlines flying today have in the lists: the one it's heard as.
FIRST = {"BTK": "Batik Air", "CTV": "Citilink", "BAV": "Bamboo Airways", "VXP": "Avelo Airlines",
         "VRE": "Air Côte d'Ivoire", "DIR": "Dirgantara Air Service", "WOW": "WOW air", "FPY": "Play",
         "AWE": "US Airways"}
DEFUNCT = re.compile(r"defunct|ceased|merged|bankrupt|liquidat|dissolved|no longer|absorbed|closed|shut down|"
                     r"folded|withdrawn|suspended operations|replaced by|succeeded by", re.I)


def logos(sparql: dict) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Wikidata's answer -> (an article's logo, a code's articles). An airline with more than one logo: its wordmark,
    else the one called a logo, else the first; "historic" ones last."""
    files: dict[str, list[str]] = defaultdict(list)
    by_code: dict[str, set[str]] = defaultdict(set)
    for b in sparql["results"]["bindings"]:
        item = b.get("article", b["item"])["value"]
        key = urllib.parse.unquote(item.rsplit("/", 1)[-1]).replace("_", " ").lower()
        files[key].append(urllib.parse.unquote(b["logo"]["value"].rsplit("/", 1)[-1]))
        by_code[b["icao"]["value"].strip().upper()].add(key)

    def rank(f: str) -> tuple:
        low = f.lower()
        return ("histor" in low or "old" in low, "wordmark" not in low, "logo" not in low, low)

    return {key: sorted(set(fs), key=rank)[0] for key, fs in files.items()}, by_code


def build(pages: list[str], countries_csv: str, sparql: dict | None = None) -> list[tuple[str, ...]]:
    home_of = homes(countries_csv)
    said = vocabulary()
    rows = []
    for text in pages:
        body = text.split("<onlyinclude>", 1)[-1].split("</onlyinclude>", 1)[0]
        for chunk in re.split(r"\n\|-[^\n]*", body)[1:]:
            chunk = chunk.split("\n|}", 1)[0]
            c = cells(chunk)
            if len(c) < 5:
                continue
            iata, icao, name, callsign, country = c[:5]
            comment = c[5] if len(c) > 5 else ""
            icao = clean(icao).upper()
            if not re.fullmatch(r"[A-Z]{3}", icao):
                continue
            iata = clean(iata).replace("*", "").upper()
            iata = iata if re.fullmatch(r"[A-Z0-9]{2}", iata) else ""
            dead = name.strip().startswith("''") or bool(DEFUNCT.search(clean(comment)))
            title, name = article(name), clean(name)
            if not name or name.startswith("|"):
                continue  # a row the table's markup broke (its cells shifted)
            name = re.sub(r"\s*\((?:\d{4}[–-]?\d{0,4}|airline|cargo airline|company)\)$", "", name)
            dead = (dead or (icao, name) in GONE) and (icao, name) not in ALIVE
            cs = clean(callsign)
            if not re.search(r"[A-Za-z]", cs) or cs.lower() in ("n/a", "na", "multiple", "none", "unknown", "-", "?") \
                    or len(cs) > 30:
                cs = ""
            cs = spoken(re.sub(r"[^\w' -]", " ", cs), {icao, iata}) if cs else ""
            if cs and " " not in cs and cs.lower() in said:
                cs = ""
            rows.append((icao, iata, cs, name, home_of.get(clean(country).lower(), ""), "1" if dead else "0", title))
    rows += [r for r in EXTRA if r[0] not in {o[0] for o in rows}]
    # A code held by more than one: the one flying today first; then the one with a callsign.
    rows.sort(key=lambda r: (r[0], FIRST.get(r[0]) != r[3], r[5], r[2] == "", r[3]))
    logo_of, articles_of = logos(sparql) if sparql else ({}, {})
    out = []
    by_code: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for r in rows:
        by_code[r[0]].append(r)
    for code, group in by_code.items():
        logos_here = [logo_of.get(r[6].lower(), "") for r in group]
        # The code's airline in use with no logo of its own article (renamed since): the one Wikidata gives that code,
        # when it gives it only one airline and that isn't another row of the code's.
        wikidata = articles_of.get(code, set())
        if not logos_here[0] and len(wikidata) == 1 and not any(r[6].lower() in wikidata for r in group[1:]):
            logos_here[0] = logo_of.get(next(iter(wikidata)), "")
        out.extend((*r, logo) for r, logo in zip(group, logos_here))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path)
    args = parser.parse_args()
    if args.source:
        pages = [p.read_text(encoding="utf-8") for p in sorted(args.source.glob("part_*.wiki"))]
        countries_csv = (args.source / "countries.csv").read_text(encoding="utf-8")
        sparql = json.loads((args.source / "logos.json").read_text(encoding="utf-8"))
    else:
        pages = [page(letter) for letter in PAGES]
        countries_csv = fetch(COUNTRIES).decode("utf-8")
        query = urllib.parse.urlencode({"query": LOGOS_QUERY, "format": "json"})
        sparql = json.loads(fetch(f"{WIKIDATA}?{query}"))
    rows = build(pages, countries_csv, sparql)
    OUT.write_bytes(gzip.compress(("\n".join("\t".join(r) for r in rows) + "\n").encode("utf-8"), mtime=0))
    print(f"{OUT}: {len(rows)} airlines, {len({r[0] for r in rows})} codes, "
          f"{sum(r[5] == '1' for r in rows)} defunct, {sum(not r[4] for r in rows)} without a home, "
          f"{sum(bool(r[7]) for r in rows)} with a logo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
