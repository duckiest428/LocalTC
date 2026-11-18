"""More of the copilot's set lines in the captain's language, with no language model (``foreign.templated`` reads these
first): what it says when it leaves something, when it's already so, when it won't, what it asks to make sure, what it
says about the radios, and where we are.

A pattern's named groups are filled in by their name: ``thing`` (what the copilot leaves or can't reach: "the flaps"),
``light`` (a light or sign by its name), ``onoff``, ``updown``, ``phase``, ``key`` (what changed), ``point`` (of the
compass), ``wind`` (headwind and crosswind), ``call`` (a radio call the copilot offers to make), ``emergency`` (what a
squawk means) and ``reply`` (another set line, put into the language itself). Any other group is kept as said, its
numbers grouped the language's way. A name a table doesn't have and the line isn't one of these: the language model's,
or English.
"""

import re
from collections.abc import Callable

CODES = ("de", "fr", "es", "it", "pt", "nl", "pl", "sv", "ru", "zh")


def _by_code(*said: str) -> dict[str, str]:
    return dict(zip(CODES, said, strict=True))


# What the copilot leaves alone or can't reach, the way each language's two sentences take it (the word's case).
_THINGS = {
    "heading": ("den Kurs", "le cap", "el rumbo", "la prua", "o rumo", "de koers", "kurs", "kursen", "курс", "航向"),
    "altitude": ("die Höhe", "l'altitude", "la altitud", "la quota", "a altitude", "de hoogte", "wysokość", "höjden", "высоту", "高度"),
    "speed": ("die Geschwindigkeit", "la vitesse", "la velocidad", "la velocità", "a velocidade", "de snelheid", "prędkość", "farten",
              "скорость", "速度"),
    "flaps": ("die Klappen", "les volets", "los flaps", "i flap", "os flaps", "de flaps", "klapy", "klaffarna", "закрылки", "襟翼"),
    "gear": ("das Fahrwerk", "le train", "el tren", "il carrello", "o trem", "het landingsgestel", "podwozie", "stället", "шасси", "起落架"),
    "lights": ("die Lichter", "les feux", "las luces", "le luci", "as luzes", "de lichten", "światła", "ljusen", "фары", "灯"),
    "autopilot": ("den Autopiloten", "le pilote automatique", "el piloto automático", "il pilota automatico", "o piloto automático",
                  "de autopiloot", "autopilota", "autopiloten", "автопилот", "自动驾驶"),
    "autobrake": ("die Autobrake", "l'autobrake", "el autofreno", "l'autobrake", "o autobrake", "de autobrake", "autobrake", "autobromsen",
                  "автотормоз", "自动刹车"),
    "spoilers": ("die Spoiler", "les spoilers", "los spoilers", "gli spoiler", "os spoilers", "de spoilers", "spoilery", "spoilrarna",
                 "спойлеры", "扰流板"),
    "transponder": ("den Transponder", "le transpondeur", "el transpondedor", "il transponder", "o transponder", "de transponder",
                    "transponder", "transpondern", "ответчик", "应答机"),
    "parking": ("die Parkbremse", "le frein de parking", "el freno de estacionamiento", "il freno di parcheggio",
                "o travão de estacionamento", "de parkeerrem", "hamulec postojowy", "parkeringsbromsen", "стояночный тормоз", "停留刹车"),
    "vertical": ("die Vertikalgeschwindigkeit", "la vitesse verticale", "la velocidad vertical", "la velocità verticale",
                 "a velocidade vertical", "de verticale snelheid", "prędkość pionową", "vertikalfarten", "вертикальную скорость", "垂直速度"),
    "altimeter": ("den Höhenmesser", "l'altimètre", "el altímetro", "l'altimetro", "o altímetro", "de hoogtemeter", "wysokościomierz",
                  "höjdmätaren", "высотомер", "高度表"),
    "apu": ("die APU", "l'APU", "la APU", "l'APU", "a APU", "de APU", "APU", "APU:n", "ВСУ", "APU"),
    "seatbelt": ("das Anschnallzeichen", "les consignes ceintures", "la señal de cinturones", "il segnale cinture", "o sinal de cintos",
                 "het gordelteken", "znak zapięcia pasów", "bältesskylten", "табло ремней", "安全带指示灯"),
}
_THINGS.update({w: _THINGS["lights"] for w in ("light", "landing", "taxi", "strobe", "strobes", "beacon", "logo", "nav")})
_THINGS.update({"squawk": _THINGS["transponder"], "qnh": _THINGS["altimeter"], "standard": _THINGS["altimeter"]})
THING = {code: {k: v[i] for k, v in _THINGS.items()} for i, code in enumerate(CODES)}

# A light or a sign by its name, as a line starts with it.
_LIGHTS = {
    "landing lights": ("Landescheinwerfer", "Phares d'atterrissage", "Luces de aterrizaje", "Luci di atterraggio", "Luzes de aterragem",
                       "Landingslichten", "Światła do lądowania", "Landningsljus", "Посадочные фары", "着陆灯"),
    "taxi lights": ("Rollscheinwerfer", "Phares de roulage", "Luces de rodaje", "Luci di rullaggio", "Luzes de rolagem", "Taxilichten",
                    "Światła kołowania", "Taxiljus", "Рулёжные фары", "滑行灯"),
    "strobes": ("Strobes", "Strobes", "Estrobos", "Strobo", "Strobes", "Strobes", "Stroboskopy", "Strobe", "Стробы", "频闪灯"),
    "beacon": ("Beacon", "Beacon", "Beacon", "Beacon", "Beacon", "Beacon", "Beacon", "Beacon", "Маяк", "防撞灯"),
    "nav lights": ("Navigationslichter", "Feux de navigation", "Luces de navegación", "Luci di navigazione", "Luzes de navegação",
                   "Navigatielichten", "Światła nawigacyjne", "Navigationsljus", "Навигационные огни", "航行灯"),
    "logo lights": ("Logolichter", "Feux logo", "Luces del logo", "Luci del logo", "Luzes do logótipo", "Logolichten", "Światła logo",
                    "Logoljus", "Подсветка логотипа", "标志灯"),
    "seatbelt sign": ("Anschnallzeichen", "Consignes ceintures", "Señal de cinturones", "Segnale cinture", "Sinal de cintos",
                      "Gordelteken", "Znak zapięcia pasów", "Bältesskylten", "Табло ремней", "安全带指示灯"),
}
LIGHT = {code: {k: v[i] for k, v in _LIGHTS.items()} for i, code in enumerate(CODES)}
_LIGHT_NAMES = "|".join(n.capitalize() for n in _LIGHTS)

_UPDOWN = {"up": ("eingefahren", "rentré", "arriba", "su", "em cima", "op", "schowane", "uppe", "убраны", "收上"),
           "down": ("ausgefahren", "sorti", "abajo", "giù", "em baixo", "neer", "wypuszczone", "nere", "выпущены", "放下")}
UPDOWN = {code: {k: v[i] for k, v in _UPDOWN.items()} for i, code in enumerate(CODES)}

# Where the copilot is with the flight ("Not now, we're in the landing now"), as each language says "we're ...".
_PHASES = {
    "parked": ("geparkt", "au parking", "estacionados", "al parcheggio", "estacionados", "geparkeerd", "na postoju", "parkerade",
               "на стоянке", "在停机位"),
    "pushback": ("im Pushback", "en repoussage", "en el remolque", "in pushback", "no pushback", "aan het pushen", "w trakcie wypychania",
                 "i pushback", "на буксировке", "正在推出"),
    "taxi out": ("beim Rollen", "au roulage", "rodando", "in rullaggio", "a rolar", "aan het taxiën", "w trakcie kołowania",
                 "under taxning", "на рулении", "正在滑出"),
    "runway hold": ("am Rollhalt", "au point d'arrêt", "en el punto de espera", "al punto attesa", "no ponto de espera",
                    "bij het wachtpunt", "przed pasem", "vid väntpunkten", "на предварительном старте", "在等待点"),
    "takeoff": ("im Start", "au décollage", "en el despegue", "in decollo", "na descolagem", "aan het opstijgen", "w trakcie startu",
                "i starten", "на взлёте", "正在起飞"),
    "departure": ("im Abflug", "en montée initiale", "en la salida", "in partenza", "na saída", "in de vertrekfase", "na odlocie",
                  "i utflygningen", "в наборе высоты", "正在离场"),
    "cruise": ("im Reiseflug", "en croisière", "en crucero", "in crociera", "em cruzeiro", "op kruishoogte", "w przelocie",
               "i marschflygningen", "в крейсерском полёте", "在巡航"),
    "arrival": ("im Sinkflug", "en arrivée", "en la llegada", "in arrivo", "na chegada", "in de aankomst", "w dolocie",
                "i ankomsten", "на снижении", "正在进场"),
    "approach": ("im Anflug", "en approche", "en la aproximación", "in avvicinamento", "na aproximação", "in de nadering",
                 "na podejściu", "i inflygningen", "на заходе", "正在进近"),
    "landing": ("in der Landung", "à l'atterrissage", "en el aterrizaje", "in atterraggio", "na aterragem", "aan het landen",
                "w trakcie lądowania", "i landningen", "на посадке", "正在着陆"),
    "taxi in": ("beim Einrollen", "au roulage vers le parking", "rodando a la plataforma", "in rullaggio verso il parcheggio",
                "a rolar para o stand", "aan het binnentaxiën", "w trakcie kołowania na stanowisko", "under intaxningen",
                "на рулении к стоянке", "正在滑入"),
}
PHASE = {code: {k: v[i] for k, v in _PHASES.items()} for i, code in enumerate(CODES)}

# What changed since the copilot asked ("That was before the cleared altitude changed").
_KEYS = {
    "cleared altitude": ("freigegebene Höhe", "altitude autorisée", "altitud autorizada", "quota autorizzata", "altitude autorizada",
                         "geklaarde hoogte", "przydzielona wysokość", "klarerad höjd", "разрешённая высота", "许可高度"),
    "assigned heading": ("zugewiesener Kurs", "cap assigné", "rumbo asignado", "prua assegnata", "rumo atribuído", "toegewezen koers",
                         "przydzielony kurs", "tilldelad kurs", "заданный курс", "指定航向"),
    "frequency": ("Frequenz", "fréquence", "frecuencia", "frequenza", "frequência", "frequentie", "częstotliwość", "frekvens", "частота", "频率"),
    "squawk": ("Squawk", "code transpondeur", "código transpondedor", "codice transponder", "código transponder", "transpondercode",
               "kod transpondera", "transponderkod", "код ответчика", "应答机编码"),
    "clearances": ("Freigaben", "autorisations", "autorizaciones", "autorizzazioni", "autorizações", "klaringen", "zezwolenia",
                   "klareringar", "разрешения", "许可"),
}
KEY = {code: {k: v[i] for k, v in _KEYS.items()} for i, code in enumerate(CODES)}

# The compass, as "about 20 miles north-northeast of Charlotte" takes it (Polish, Russian and Mandarin by eight points).
_POINTS = ("north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast", "south-southeast",
           "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest", "northwest", "north-northwest")
_EIGHT = {"pl": ("na północ", "na północny wschód", "na wschód", "na południowy wschód", "na południe", "na południowy zachód",
                 "na zachód", "na północny zachód"),
          "ru": ("к северу от", "к северо-востоку от", "к востоку от", "к юго-востоку от", "к югу от", "к юго-западу от",
                 "к западу от", "к северо-западу от"),
          "zh": ("以北", "东北", "以东", "东南", "以南", "西南", "以西", "西北")}
_SIXTEEN = {
    "de": ("nördlich", "nordnordöstlich", "nordöstlich", "ostnordöstlich", "östlich", "ostsüdöstlich", "südöstlich", "südsüdöstlich",
           "südlich", "südsüdwestlich", "südwestlich", "westsüdwestlich", "westlich", "westnordwestlich", "nordwestlich",
           "nordnordwestlich"),
    "fr": ("au nord", "au nord-nord-est", "au nord-est", "à l'est-nord-est", "à l'est", "à l'est-sud-est", "au sud-est", "au sud-sud-est",
           "au sud", "au sud-sud-ouest", "au sud-ouest", "à l'ouest-sud-ouest", "à l'ouest", "à l'ouest-nord-ouest", "au nord-ouest",
           "au nord-nord-ouest"),
    "es": ("al norte", "al nornoreste", "al noreste", "al estenoreste", "al este", "al estesureste", "al sureste", "al sursureste",
           "al sur", "al sursuroeste", "al suroeste", "al oestesuroeste", "al oeste", "al oestenoroeste", "al noroeste", "al nornoroeste"),
    "it": ("a nord", "a nord-nordest", "a nordest", "a est-nordest", "a est", "a est-sudest", "a sudest", "a sud-sudest", "a sud",
           "a sud-sudovest", "a sudovest", "a ovest-sudovest", "a ovest", "a ovest-nordovest", "a nordovest", "a nord-nordovest"),
    "pt": ("a norte", "a nor-nordeste", "a nordeste", "a lés-nordeste", "a leste", "a lés-sudeste", "a sudeste", "a su-sudeste", "a sul",
           "a su-sudoeste", "a sudoeste", "a oés-sudoeste", "a oeste", "a oés-noroeste", "a noroeste", "a nor-noroeste"),
    "nl": ("ten noorden", "ten noordnoordoosten", "ten noordoosten", "ten oostnoordoosten", "ten oosten", "ten oostzuidoosten",
           "ten zuidoosten", "ten zuidzuidoosten", "ten zuiden", "ten zuidzuidwesten", "ten zuidwesten", "ten westzuidwesten",
           "ten westen", "ten westnoordwesten", "ten noordwesten", "ten noordnoordwesten"),
    "sv": ("norr", "nordnordost", "nordost", "ostnordost", "öster", "ostsydost", "sydost", "sydsydost", "söder", "sydsydväst", "sydväst",
           "västsydväst", "väster", "västnordväst", "nordväst", "nordnordväst"),
}
POINT = {code: dict(zip(_POINTS, said, strict=True)) for code, said in _SIXTEEN.items()}
POINT.update({code: {p: said[((i + 1) // 2) % 8] for i, p in enumerate(_POINTS)} for code, said in _EIGHT.items()})

_EMERGENCIES = {"hijack": ("Entführung", "détournement", "secuestro", "dirottamento", "sequestro", "kaping", "porwanie", "kapning",
                           "захват", "劫机"),
                "radio failure": ("Funkausfall", "panne radio", "fallo de radio", "avaria radio", "falha de rádio", "radiostoring",
                                  "awaria radia", "radiobortfall", "отказ связи", "通信失效"),
                "emergency": ("Notfall", "urgence", "emergencia", "emergenza", "emergência", "noodgeval", "niebezpieczeństwo", "nödläge",
                              "бедствие", "紧急")}
EMERGENCY = {code: {k: v[i] for k, v in _EMERGENCIES.items()} for i, code in enumerate(CODES)}

# The radio call the copilot offers to make, with the station ({s}): "ask ground for push and start".
_CALLS = {
    "push": ("bei {s} Push und Anlassen anfragen", "demander le repoussage et la mise en route à {s}",
             "pedir el remolque y la puesta en marcha a {s}", "chiedere a {s} pushback e messa in moto", "pedir pushback e arranque a {s}",
             "bij {s} pushback en starten aanvragen", "poprosić {s} o wypychanie i uruchomienie", "be {s} om pushback och start",
             "запросить у {s} буксировку и запуск", "向{s}申请推出开车"),
    "taxi": ("bei {s} das Rollen anfragen", "demander le roulage à {s}", "pedir rodaje a {s}", "chiedere il rullaggio a {s}",
             "pedir rolagem a {s}", "bij {s} taxiën aanvragen", "poprosić {s} o kołowanie", "be {s} om taxning",
             "запросить у {s} руление", "向{s}申请滑行"),
    "clearance": ("bei {s} die IFR-Freigabe anfragen", "demander la clairance IFR à {s}", "pedir la autorización IFR a {s}",
                  "chiedere l'autorizzazione IFR a {s}", "pedir a autorização IFR a {s}", "bij {s} de IFR-klaring aanvragen",
                  "poprosić {s} o zezwolenie IFR", "be {s} om IFR-klareringen", "запросить у {s} разрешение по ППП", "向{s}申请IFR放行"),
    "ready": ("{s} sagen, dass wir bereit sind", "dire à {s} que nous sommes prêts", "decirle a {s} que estamos listos",
              "dire a {s} che siamo pronti", "dizer a {s} que estamos prontos", "{s} laten weten dat we klaar zijn",
              "powiedzieć {s}, że jesteśmy gotowi", "säga till {s} att vi är klara", "сказать {s}, что мы готовы", "告诉{s}我们准备好了"),
}
CALL = {code: {k: v[i] for k, v in _CALLS.items()} for i, code in enumerate(CODES)}
_CALLED = ((r"^ask (?P<s>.+) for push and start$", "push"), (r"^ask (?P<s>.+) for taxi$", "taxi"),
           (r"^ask (?P<s>.+) for the IFR clearance$", "clearance"), (r"^tell (?P<s>.+) we're ready$", "ready"))
# The station when the copilot doesn't know its name.
_STATIONS = {"ground": ("Ground", "Sol", "Rodadura", "Ground", "Ground", "Ground", "Ground", "Ground", "Ground", "地面"),
             "clearance": ("Delivery", "Prévol", "Autorizaciones", "Delivery", "Delivery", "Delivery", "Delivery", "Delivery", "Delivery",
                           "放行"),
             "tower": ("Tower", "la Tour", "la Torre", "la Torre", "a Torre", "de toren", "Tower", "tornet", "Tower", "塔台")}
STATION = {code: {k: v[i] for k, v in _STATIONS.items()} for i, code in enumerate(CODES)}

# The wind on the runway: (headwind, tailwind, crosswind from the left, from the right), each after its knots.
_WIND = {
    "de": ("Gegenwind", "Rückenwind", "Seitenwind von links", "Seitenwind von rechts"),
    "fr": ("de vent de face", "de vent arrière", "de vent de travers de la gauche", "de vent de travers de la droite"),
    "es": ("de viento en cara", "de viento en cola", "de viento cruzado por la izquierda", "de viento cruzado por la derecha"),
    "it": ("di vento in prua", "di vento in coda", "di vento al traverso da sinistra", "di vento al traverso da destra"),
    "pt": ("de vento de frente", "de vento de cauda", "de vento cruzado da esquerda", "de vento cruzado da direita"),
    "nl": ("tegenwind", "rugwind", "zijwind van links", "zijwind van rechts"),
    "pl": ("wiatru czołowego", "wiatru tylnego", "wiatru bocznego z lewej", "wiatru bocznego z prawej"),
    "sv": ("motvind", "medvind", "sidvind från vänster", "sidvind från höger"),
    "ru": ("встречного ветра", "попутного ветра", "бокового ветра слева", "бокового ветра справа"),
    "zh": ("顶风", "顺风", "左侧风", "右侧风"),
}
_KNOT = {"de": ("Knoten", "Knoten", "Knoten"), "fr": ("nœud", "nœuds", "nœuds"), "es": ("nudo", "nudos", "nudos"),
         "it": ("nodo", "nodi", "nodi"), "pt": ("nó", "nós", "nós"), "nl": ("knoop", "knopen", "knopen"),
         "pl": ("węzeł", "węzły", "węzłów"), "sv": ("knop", "knop", "knop"), "ru": ("узел", "узла", "узлов")}


def _knots(n: int, code: str) -> str:
    from localtc.crew.foreign import _form

    return f"{n} {_form(n, *_KNOT[code], code)}"


def _wind(text: str, code: str) -> str | None:
    """ "23 knots headwind, 1 knot crosswind from the left" in the language."""
    m = re.match(r"^(\d+) knots? (headwind|tailwind), (\d+) knots? crosswind from the (left|right)$", text)
    if m is None:
        return None
    head, tail, left, right = _WIND[code]
    along, cross = head if m[2] == "headwind" else tail, left if m[4] == "left" else right
    if code == "zh":
        return f"{along}{m[1]}节，{cross}{m[3]}节"
    return f"{_knots(int(m[1]), code)} {along}, {_knots(int(m[3]), code)} {cross}"


def _call(text: str, code: str) -> str | None:
    for pattern, call in _CALLED:
        if (m := re.match(pattern, text)) is not None:
            station = STATION[code].get(m["s"], m["s"])
            return CALL[code][call].format(s=station)
    return None


_ONOFF = {"de": ("schon an", "schon aus"), "fr": ("c'est déjà allumé", "c'est déjà éteint"),
          "es": ("eso ya está encendido", "eso ya está apagado"), "it": ("già su ON", "già su OFF"),
          "pt": ("isso já está ligado", "isso já está desligado"), "nl": ("al aan", "al uit"),
          "pl": ("to już jest włączone", "to już jest wyłączone"), "sv": ("redan på", "redan av"),
          "ru": ("уже включено", "уже выключено"), "zh": ("已经打开了", "已经关了")}

LINES: tuple[tuple[str, dict[str, str]], ...] = (
    # --- leaving things, and what isn't the copilot's ---
    (r"^Copy, I'll leave the (?P<thing>\w+) alone\.$", _by_code(
        "Verstanden, ich lasse {thing} in Ruhe.", "Compris, je laisse {thing} comme ça.", "Copiado, dejo {thing} así.",
        "Ricevuto, lascio stare {thing}.", "Copiado, deixo {thing} assim.", "Begrepen, ik laat {thing} met rust.",
        "Zrozumiałem, zostawiam {thing} w spokoju.", "Uppfattat, jag låter {thing} vara.", "Понял, {thing} не трогаю.",
        "收到，{thing}我不动。")),
    (r"^My (?P<thing>\w+) doesn't reach this aircraft; that one's yours\.$", _by_code(
        "An {thing} komme ich bei diesem Flugzeug nicht ran; das machst du.",
        "Je n'ai pas la main sur {thing} dans cet avion ; c'est pour toi.", "En este avión no controlo {thing}; eso es tuyo.",
        "Su questo aereo non controllo {thing}; tocca a te.", "Neste avião não controlo {thing}; isso é contigo.",
        "In dit toestel kan ik {thing} niet bedienen; dat doe jij.", "W tym samolocie nie obsłużę stąd {thing}; to twoje.",
        "I det här flygplanet når jag inte {thing}; det får du ta.", "В этом самолёте я не могу трогать {thing}; это на тебе.",
        "这架飞机上我操作不了{thing}，你来。")),
    (r"^Copy, I'll leave the fuel predictions to the box\.$", _by_code(
        "Verstanden, die Treibstoffprognosen überlasse ich dem Computer.", "Compris, je laisse les prévisions carburant à l'ordinateur.",
        "Copiado, dejo las previsiones de combustible al ordenador.", "Ricevuto, lascio le previsioni carburante al computer.",
        "Copiado, deixo as previsões de combustível ao computador.", "Begrepen, de brandstofprognoses laat ik aan de computer over.",
        "Zrozumiałem, prognozy paliwa zostawiam komputerowi.", "Uppfattat, bränsleprognoserna lämnar jag åt datorn.",
        "Понял, прогнозы по топливу оставлю вычислителю.", "收到，油量预测交给电脑。")),
    (r"^Copy, no go-around\.$", _by_code(
        "Verstanden, kein Durchstarten.", "Compris, pas de remise des gaz.", "Copiado, sin motor y al aire.", "Ricevuto, niente riattaccata.",
        "Copiado, sem borrego.", "Begrepen, geen doorstart.", "Zrozumiałem, bez odejścia na drugi krąg.", "Uppfattat, vi går inte om.",
        "Понял, без ухода на второй круг.", "收到，不复飞。")),
    (r"^Copy, leaving it\.$", _by_code(
        "Verstanden, ich lasse es.", "Compris, je laisse.", "Copiado, lo dejo.", "Ricevuto, lascio stare.", "Copiado, deixo estar.",
        "Begrepen, ik laat het.", "Zrozumiałem, zostawiam.", "Uppfattat, jag låter det vara.", "Понял, оставляю.", "收到，不动它。")),
    (r"^Copy, later then\.$", _by_code(
        "Verstanden, dann später.", "Compris, plus tard alors.", "Copiado, luego entonces.", "Ricevuto, più tardi allora.",
        "Copiado, então mais tarde.", "Begrepen, later dan.", "Zrozumiałem, to później.", "Uppfattat, senare då.", "Понял, тогда позже.",
        "收到，那就晚点再说。")),
    (r"^Copy, only what matters\.$", _by_code(
        "Verstanden, nur das Wichtige.", "Compris, seulement l'essentiel.", "Copiado, solo lo importante.", "Ricevuto, solo l'essenziale.",
        "Copiado, só o essencial.", "Begrepen, alleen wat belangrijk is.", "Zrozumiałem, tylko to, co ważne.", "Uppfattat, bara det viktiga.",
        "Понял, только главное.", "收到，只说重要的。")),
    (r"^Copy, I'll keep you posted\.$", _by_code(
        "Verstanden, ich halte dich auf dem Laufenden.", "Compris, je te tiens au courant.", "Copiado, te mantengo informado.",
        "Ricevuto, ti tengo aggiornato.", "Copiado, vou-te mantendo informado.", "Begrepen, ik hou je op de hoogte.",
        "Zrozumiałem, będę cię informował na bieżąco.", "Uppfattat, jag håller dig uppdaterad.", "Понял, буду держать в курсе.",
        "收到，我会随时告诉你。")),
    (r"^Copy, the usual calls\.$", _by_code(
        "Verstanden, die üblichen Ansagen.", "Compris, les annonces habituelles.", "Copiado, los avisos de siempre.",
        "Ricevuto, le solite chiamate.", "Copiado, as chamadas do costume.", "Begrepen, de gebruikelijke calls.",
        "Zrozumiałem, standardowe zapowiedzi.", "Uppfattat, de vanliga utropen.", "Понял, обычные доклады.", "收到，按平常的报告。")),
    (r"^That one's yours\.$", _by_code(
        "Das machst du.", "Ça, c'est pour toi.", "Eso es tuyo.", "Quello tocca a te.", "Isso é contigo.", "Die is voor jou.", "To twoje.",
        "Den är din.", "Это на тебе.", "这个你来。")),
    (r"^I can't move this aircraft's switches from here; they're yours\.$", _by_code(
        "Die Schalter in diesem Flugzeug erreiche ich von hier nicht; die machst du.",
        "Je ne peux pas actionner les commandes de cet avion d'ici ; c'est pour toi.",
        "Desde aquí no puedo mover los interruptores de este avión; son cosa tuya.",
        "Da qui non posso muovere gli interruttori di questo aereo; tocca a te.",
        "Daqui não consigo mexer nos interruptores deste avião; isso é contigo.",
        "De schakelaars van dit toestel kan ik van hieruit niet bedienen; die zijn voor jou.",
        "Stąd nie przestawię przełączników w tym samolocie; to twoja działka.", "Jag når inte det här flygplanets brytare härifrån; de är dina.",
        "Отсюда я не могу трогать переключатели этого самолёта; это на тебе.", "我从这边动不了这架飞机的开关，你来。")),
    (r"^Your side\.$", _by_code("Deine Seite.", "Ton côté.", "Tu lado.", "Lato tuo.", "O teu lado.", "Jouw kant.", "Twoja strona.",
                                "Din sida.", "Твоя сторона.", "你那边的。")),
    (r"^That's yours\.$", _by_code("Das ist deins.", "C'est pour toi.", "Eso es tuyo.", "È roba tua.", "Isso é teu.", "Die is van jou.",
                                   "To twoje.", "Det är ditt.", "Это твоё.", "这是你的。")),
    (r"^Yours\.$", _by_code("Deins.", "À toi.", "Tuyo.", "Tuo.", "Teu.", "Jij.", "Twoje.", "Ditt.", "Твоё.", "你的。")),
    (r"^Loud and clear\.$", _by_code("Laut und deutlich.", "Cinq sur cinq.", "Alto y claro.", "Forte e chiaro.", "Alto e claro.",
                                     "Luid en duidelijk.", "Słyszę cię głośno i wyraźnie.", "Högt och tydligt.", "Слышу отлично.", "听得很清楚。")),
    (r"^I haven't said anything\.$", _by_code(
        "Ich habe nichts gesagt.", "Je n'ai rien dit.", "No he dicho nada.", "Non ho detto niente.", "Não disse nada.", "Ik heb niets gezegd.",
        "Nic nie mówiłem.", "Jag har inte sagt något.", "Я ничего не говорил.", "我什么也没说。")),
    (r"^Minimums (?P<x>[\d,]+) set\.$", _by_code(
        "Minimums {x} gesetzt.", "Minima {x} affichés.", "Mínimos {x} puestos.", "Minimi {x} impostati.", "Mínimos {x} definidos.",
        "Minima {x} ingesteld.", "Minima {x} ustawione.", "Minima {x} inställda.", "Минимум {x} установлен.", "最低标准{x}已设置。")),
    (r'^I heard "(?P<x>.+)"\. Say again\?$', _by_code(
        "Ich habe „{x}“ verstanden. Nochmal?", "J'ai entendu « {x} ». Tu peux répéter ?", "He oído «{x}». ¿Puedes repetir?",
        "Ho sentito «{x}». Puoi ripetere?", "Ouvi «{x}». Podes repetir?", "Ik hoorde ‘{x}’. Kun je dat herhalen?",
        "Usłyszałem „{x}”. Możesz powtórzyć?", "Jag hörde ”{x}”. Kan du upprepa?", "Я услышал «{x}». Повтори, пожалуйста?",
        "我听到的是“{x}”。请再说一遍？")),
    (r"^That's gone stale\. Say again what you need\?$", _by_code(
        "Das ist nicht mehr aktuell. Sag nochmal, was du brauchst?", "Ce n'est plus d'actualité. Tu me redis ce qu'il te faut ?",
        "Eso ya no vale. ¿Me repites lo que necesitas?", "Non vale più. Mi ripeti cosa ti serve?", "Isso já não vale. Repetes o que precisas?",
        "Dat is niet meer actueel. Zeg nog eens wat je nodig hebt?", "To już nieaktualne. Powtórz, czego potrzebujesz?",
        "Det gäller inte längre. Säg igen vad du behöver?", "Это уже неактуально. Повтори, что нужно?", "那个已经过时了。你再说一遍要什么？")),
    (r"^That was before the (?P<key>.+) changed\. Say it again if you still want it\.$", _by_code(
        "Das war vor einer Änderung ({key}). Sag es nochmal, wenn du es noch willst.",
        "C'était avant un changement ({key}). Redis-le si tu le veux toujours.", "Eso era antes de un cambio ({key}). Repítelo si aún lo quieres.",
        "Era prima di un cambio ({key}). Ripetilo se lo vuoi ancora.", "Isso foi antes de uma mudança ({key}). Repete se ainda quiseres.",
        "Dat was van vóór een wijziging ({key}). Zeg het nog eens als je het nog wilt.", "To było przed zmianą ({key}). Powtórz, jeśli nadal chcesz.",
        "Det var före en ändring ({key}). Säg det igen om du fortfarande vill.", "Это было до изменения ({key}). Повтори, если ещё нужно.",
        "那是在{key}变更之前。如果还需要，请再说一遍。")),
    (r"^That was before a new flight\. Say it again if you still want it\.$", _by_code(
        "Das war vor dem neuen Flug. Sag es nochmal, wenn du es noch willst.", "C'était avant le nouveau vol. Redis-le si tu le veux toujours.",
        "Eso era antes del nuevo vuelo. Repítelo si aún lo quieres.", "Era prima del nuovo volo. Ripetilo se lo vuoi ancora.",
        "Isso foi antes do novo voo. Repete se ainda quiseres.", "Dat was van vóór de nieuwe vlucht. Zeg het nog eens als je het nog wilt.",
        "To było przed nowym lotem. Powtórz, jeśli nadal chcesz.", "Det var före den nya flygningen. Säg det igen om du fortfarande vill.",
        "Это было до нового рейса. Повтори, если ещё нужно.", "那是新航班之前的事。如果还需要，请再说一遍。")),
    (r"^Not now, we're in the (?P<phase>.+) now\.$", _by_code(
        "Jetzt nicht, wir sind {phase}.", "Pas maintenant, on est {phase}.", "Ahora no, estamos {phase}.", "Non adesso, siamo {phase}.",
        "Agora não, estamos {phase}.", "Nu niet, we zijn {phase}.", "Nie teraz, jesteśmy {phase}.", "Inte nu, vi är {phase}.",
        "Не сейчас, мы {phase}.", "现在不行，我们{phase}。")),

    # --- already so ---
    (r"^Gear's already (?P<updown>up|down)\.$", _by_code(
        "Das Fahrwerk ist schon {updown}.", "Le train est déjà {updown}.", "El tren ya está {updown}.", "Il carrello è già {updown}.",
        "O trem já está {updown}.", "Het landingsgestel is al {updown}.", "Podwozie jest już {updown}.", "Stället är redan {updown}.",
        "Шасси уже {updown}.", "起落架已经{updown}了。")),
    (r"^Flaps are already up\.$", _by_code(
        "Die Klappen sind schon eingefahren.", "Les volets sont déjà rentrés.", "Los flaps ya están arriba.", "I flap sono già retratti.",
        "Os flaps já estão recolhidos.", "De flaps zijn al op.", "Klapy są już schowane.", "Klaffarna är redan uppe.", "Закрылки уже убраны.",
        "襟翼已经收上了。")),
    (r"^Flaps are already (?P<x>.+)\.$", _by_code(
        "Die Klappen stehen schon auf {x}.", "Les volets sont déjà sur {x}.", "Los flaps ya están en {x}.", "I flap sono già su {x}.",
        "Os flaps já estão em {x}.", "De flaps staan al op {x}.", "Klapy są już na {x}.", "Klaffarna står redan på {x}.",
        "Закрылки уже на {x}.", "襟翼已经在{x}了。")),
    (rf"^(?P<light>{_LIGHT_NAMES})(?:'s)? already (?P<onoff>on|off)\.$", _by_code(
        "{light}: {onoff}.", "{light} : {onoff}.", "{light}: {onoff}.", "{light}: {onoff}.", "{light}: {onoff}.", "{light}: {onoff}.",
        "{light}: {onoff}.", "{light}: {onoff}.", "{light}: {onoff}.", "{light}{onoff}。")),
    (r"^Autopilot's already on\.$", _by_code(
        "Der Autopilot ist schon drin.", "Le pilote automatique est déjà engagé.", "El piloto automático ya está conectado.",
        "Il pilota automatico è già inserito.", "O piloto automático já está ligado.", "De autopiloot staat al aan.",
        "Autopilot jest już włączony.", "Autopiloten är redan inkopplad.", "Автопилот уже включён.", "自动驾驶已经接通了。")),
    (r"^Autopilot's already off\.$", _by_code(
        "Der Autopilot ist schon draußen.", "Le pilote automatique est déjà désengagé.", "El piloto automático ya está desconectado.",
        "Il pilota automatico è già disinserito.", "O piloto automático já está desligado.", "De autopiloot staat al uit.",
        "Autopilot jest już wyłączony.", "Autopiloten är redan urkopplad.", "Автопилот уже выключен.", "自动驾驶已经断开了。")),
    (r"^Autothrottle's already armed\.$", _by_code(
        "Der Autothrust ist schon scharf.", "L'autothrottle est déjà armé.", "El autothrottle ya está armado.", "L'autothrottle è già armato.",
        "O autothrottle já está armado.", "De autothrottle is al ingeschakeld.", "Autothrottle jest już uzbrojony.",
        "Autothrottle är redan armerad.", "Автомат тяги уже взведён.", "自动油门已经预位了。")),
    (r"^Autothrottle's already off\.$", _by_code(
        "Der Autothrust ist schon aus.", "L'autothrottle est déjà coupé.", "El autothrottle ya está desconectado.",
        "L'autothrottle è già disinserito.", "O autothrottle já está desligado.", "De autothrottle is al uit.", "Autothrottle jest już wyłączony.",
        "Autothrottle är redan av.", "Автомат тяги уже выключен.", "自动油门已经断开了。")),
    (r"^APU's already running\.$", _by_code(
        "Die APU läuft schon.", "L'APU tourne déjà.", "La APU ya está en marcha.", "L'APU è già in moto.", "A APU já está a trabalhar.",
        "De APU draait al.", "APU już pracuje.", "APU:n går redan.", "ВСУ уже работает.", "APU已经在运转了。")),
    (r"^APU's already off\.$", _by_code(
        "Die APU ist schon aus.", "L'APU est déjà coupée.", "La APU ya está apagada.", "L'APU è già spenta.", "A APU já está desligada.",
        "De APU is al uit.", "APU jest już wyłączone.", "APU:n är redan avstängd.", "ВСУ уже выключена.", "APU已经关了。")),
    (r"^Spoilers are already armed\.$", _by_code(
        "Die Spoiler sind schon scharf.", "Les spoilers sont déjà armés.", "Los spoilers ya están armados.", "Gli spoiler sono già armati.",
        "Os spoilers já estão armados.", "De spoilers zijn al gearmd.", "Spoilery są już uzbrojone.", "Spoilrarna är redan armerade.",
        "Спойлеры уже взведены.", "扰流板已经预位了。")),
    (r"^Spoilers aren't armed\.$", _by_code(
        "Die Spoiler sind nicht scharf.", "Les spoilers ne sont pas armés.", "Los spoilers no están armados.", "Gli spoiler non sono armati.",
        "Os spoilers não estão armados.", "De spoilers zijn niet gearmd.", "Spoilery nie są uzbrojone.", "Spoilrarna är inte armerade.",
        "Спойлеры не взведены.", "扰流板没有预位。")),
    (r"^Parking brake's already set\.$", _by_code(
        "Die Parkbremse ist schon gesetzt.", "Le frein de parking est déjà serré.", "El freno de estacionamiento ya está puesto.",
        "Il freno di parcheggio è già inserito.", "O travão de estacionamento já está aplicado.", "De parkeerrem staat er al op.",
        "Hamulec postojowy jest już zaciągnięty.", "Parkeringsbromsen är redan ansatt.", "Стояночный тормоз уже включён.", "停留刹车已经设置了。")),
    (r"^Parking brake's already off\.$", _by_code(
        "Die Parkbremse ist schon gelöst.", "Le frein de parking est déjà desserré.", "El freno de estacionamiento ya está quitado.",
        "Il freno di parcheggio è già rilasciato.", "O travão de estacionamento já está solto.", "De parkeerrem is al los.",
        "Hamulec postojowy jest już zwolniony.", "Parkeringsbromsen är redan släppt.", "Стояночный тормоз уже снят.", "停留刹车已经松开了。")),
    (r"^Transponder's already (?P<x>.+)\.$", _by_code(
        "Der Transponder ist schon auf {x}.", "Le transpondeur est déjà sur {x}.", "El transpondedor ya está en {x}.",
        "Il transponder è già su {x}.", "O transponder já está em {x}.", "De transponder staat al op {x}.", "Transponder jest już na {x}.",
        "Transpondern står redan på {x}.", "Ответчик уже в режиме {x}.", "应答机已经在{x}了。")),
    (r"^Squawk's already (?P<x>\d{4})\.$", _by_code(
        "Der Squawk ist schon {x}.", "Le transpondeur est déjà sur {x}.", "El transpondedor ya está en {x}.", "Il transponder è già su {x}.",
        "O transponder já está em {x}.", "De transponder staat al op {x}.", "Transponder jest już na {x}.", "Transpondern står redan på {x}.",
        "Ответчик уже на {x}.", "应答机已经是{x}了。")),
    (r"^Autobrake's already (?P<x>.+)\.$", _by_code(
        "Die Autobrake steht schon auf {x}.", "L'autobrake est déjà sur {x}.", "El autofreno ya está en {x}.", "L'autobrake è già su {x}.",
        "O autobrake já está em {x}.", "De autobrake staat al op {x}.", "Autobrake jest już na {x}.", "Autobromsen står redan på {x}.",
        "Автотормоз уже на {x}.", "自动刹车已经在{x}了。")),

    # --- refused ---
    (r"^Negative, we're on the ground\.$", _by_code(
        "Negativ, wir sind am Boden.", "Négatif, on est au sol.", "Negativo, estamos en tierra.", "Negativo, siamo a terra.",
        "Negativo, estamos no chão.", "Negatief, we staan op de grond.", "Negatyw, jesteśmy na ziemi.", "Negativt, vi är på marken.",
        "Отрицательно, мы на земле.", "不行，我们还在地面。")),
    (r"^Negative, no positive rate yet\.$", _by_code(
        "Negativ, noch keine positive Steigrate.", "Négatif, pas encore de vario positif.", "Negativo, aún no hay régimen positivo.",
        "Negativo, ancora nessun rateo positivo.", "Negativo, ainda sem razão de subida positiva.", "Negatief, nog geen positieve klimsnelheid.",
        "Negatyw, jeszcze nie ma dodatniej prędkości wznoszenia.", "Negativt, ingen positiv stighastighet än.",
        "Отрицательно, ещё нет положительной вертикальной.", "不行，还没有正上升率。")),
    (r"^Negative, not on the roll\.$", _by_code(
        "Negativ, nicht während wir auf der Bahn rollen.", "Négatif, pas pendant qu'on roule sur la piste.",
        "Negativo, no mientras rodamos por la pista.", "Negativo, non mentre corriamo sulla pista.", "Negativo, não enquanto rolamos na pista.",
        "Negatief, niet terwijl we over de baan rollen.", "Negatyw, nie podczas rozbiegu ani dobiegu.", "Negativt, inte medan vi rullar på banan.",
        "Отрицательно, не на разбеге и не на пробеге.", "不行，滑跑中不能操作。")),
    (r"^Not on the roll\.$", _by_code(
        "Nicht während wir auf der Bahn rollen.", "Pas pendant qu'on roule sur la piste.", "No mientras rodamos por la pista.",
        "Non mentre corriamo sulla pista.", "Não enquanto rolamos na pista.", "Niet terwijl we over de baan rollen.",
        "Nie podczas rozbiegu ani dobiegu.", "Inte medan vi rullar på banan.", "Не на разбеге и не на пробеге.", "滑跑中不行。")),
    (r"^Negative, not below (?P<x>[\d,]+) feet\.$", _by_code(
        "Negativ, nicht unter {x} {feet}.", "Négatif, pas en dessous de {x} {feet}.", "Negativo, no por debajo de {x} {feet}.",
        "Negativo, non sotto i {x} {feet}.", "Negativo, não abaixo de {x} {feet}.", "Negatief, niet onder {x} {feet}.",
        "Negatyw, nie poniżej {x} {feet}.", "Negativt, inte under {x} {feet}.", "Отрицательно, не на высоте ниже {x} {feet}.",
        "不行，{x}{feet}以下不能用。")),
    (r"^Negative, not while we're moving\.$", _by_code(
        "Negativ, nicht während wir rollen.", "Négatif, pas tant qu'on roule.", "Negativo, no mientras nos movemos.",
        "Negativo, non mentre ci muoviamo.", "Negativo, não enquanto estamos em movimento.", "Negatief, niet terwijl we rijden.",
        "Negatyw, nie w trakcie ruchu.", "Negativt, inte medan vi rullar.", "Отрицательно, не на ходу.", "不行，飞机还在动。")),
    (r"^Unable, there's no flaps (?P<x>.+) on this aircraft\.$", _by_code(
        "Geht nicht, dieses Flugzeug hat keine Klappen {x}.", "Impossible, pas de volets {x} sur cet avion.",
        "Imposible, este avión no tiene flaps {x}.", "Impossibile, questo aereo non ha flap {x}.", "Impossível, este avião não tem flaps {x}.",
        "Gaat niet, dit toestel heeft geen flaps {x}.", "Nie da się, ten samolot nie ma klap {x}.",
        "Går inte, det här flygplanet har inga klaffar {x}.", "Не могу, на этом самолёте нет закрылков {x}.", "做不到，这架飞机没有襟翼{x}。")),
    (r"^Unable, there's no autobrake (?P<x>.+) on this aircraft\.$", _by_code(
        "Geht nicht, dieses Flugzeug hat keine Autobrake {x}.", "Impossible, pas d'autobrake {x} sur cet avion.",
        "Imposible, este avión no tiene autofreno {x}.", "Impossibile, questo aereo non ha autobrake {x}.",
        "Impossível, este avião não tem autobrake {x}.", "Gaat niet, dit toestel heeft geen autobrake {x}.",
        "Nie da się, ten samolot nie ma autobrake {x}.", "Går inte, det här flygplanet har ingen autobroms {x}.",
        "Не могу, на этом самолёте нет автотормоза {x}.", "做不到，这架飞机没有自动刹车{x}档。")),
    (r"^Which setting for the autobrake\?$", _by_code(
        "Welche Stufe für die Autobrake?", "Quel réglage pour l'autobrake ?", "¿Qué posición para el autofreno?",
        "Che impostazione per l'autobrake?", "Que posição para o autobrake?", "Welke stand voor de autobrake?", "Jakie ustawienie autobrake?",
        "Vilket läge för autobromsen?", "Какой режим автотормоза?", "自动刹车放哪一档？")),

    # --- asked first ---
    (r"^Autopilot off at (?P<x>[\d,]+) feet, confirm\?$", _by_code(
        "Autopilot aus bei {x} {feet}, bestätigst du?", "Pilote automatique coupé à {x} {feet}, tu confirmes ?",
        "Piloto automático fuera a {x} {feet}, ¿confirmas?", "Pilota automatico disinserito a {x} {feet}, confermi?",
        "Piloto automático desligado a {x} {feet}, confirmas?", "Autopiloot uit op {x} {feet}, bevestig je?",
        "Autopilot wyłączony na {x} {feet}, potwierdzasz?", "Autopiloten ur på {x} {feet}, bekräftar du?",
        "Отключить автопилот на {x} {feet}, подтверждаешь?", "在{x}{feet}断开自动驾驶，确认吗？")),
    (r"^Squawk (?P<x>\d{4}), (?P<emergency>hijack|radio failure|emergency), confirm\?$", _by_code(
        "Squawk {x}, {emergency}, bestätigst du?", "Transpondeur {x}, {emergency}, tu confirmes ?", "Transpondedor {x}, {emergency}, ¿confirmas?",
        "Transponder {x}, {emergency}, confermi?", "Transponder {x}, {emergency}, confirmas?", "Transponder {x}, {emergency}, bevestig je?",
        "Transponder {x}, {emergency}, potwierdzasz?", "Transponder {x}, {emergency}, bekräftar du?", "Ответчик {x}, {emergency}, подтверждаешь?",
        "应答机{x}，{emergency}，确认吗？")),
    (r"^ATC gave us (?P<a>\d{4})\. Squawk (?P<x>\d{4}), confirm\?$", _by_code(
        "ATC hat uns {a} gegeben. Squawk {x}, bestätigst du?", "Le contrôle nous a donné {a}. Transpondeur {x}, tu confirmes ?",
        "Control nos dio {a}. Transpondedor {x}, ¿confirmas?", "Il controllo ci ha dato {a}. Transponder {x}, confermi?",
        "O controlo deu-nos {a}. Transponder {x}, confirmas?", "De verkeersleiding gaf ons {a}. Transponder {x}, bevestig je?",
        "Kontrola dała nam {a}. Transponder {x}, potwierdzasz?", "Flygledningen gav oss {a}. Transponder {x}, bekräftar du?",
        "Диспетчер дал нам {a}. Ответчик {x}, подтверждаешь?", "管制给的是{a}。应答机{x}，确认吗？")),
    (r"^We're cleared to (?P<a>.+?)\. Set (?P<x>.+), confirm\?$", _by_code(
        "Wir sind für {a} freigegeben. {x} setzen, bestätigst du?", "Nous sommes autorisés à {a}. J'affiche {x}, tu confirmes ?",
        "Estamos autorizados a {a}. ¿Pongo {x}? ¿Confirmas?", "Siamo autorizzati a {a}. Imposto {x}, confermi?",
        "Estamos autorizados para {a}. Ponho {x}, confirmas?", "We hebben klaring naar {a}. {x} instellen, bevestig je?",
        "Mamy zezwolenie na {a}. Ustawić {x}, potwierdzasz?", "Vi är klarerade till {a}. Ställa in {x}, bekräftar du?",
        "Нам разрешено {a}. Ставлю {x}, подтверждаешь?", "我们的许可高度是{a}。设置{x}，确认吗？")),
    (r"^The ATIS has QNH (?P<a>\d+)\. Set (?P<x>.+)\?$", _by_code(
        "Laut ATIS QNH {a}. {x} setzen?", "L'ATIS donne QNH {a}. J'affiche {x} ?", "El ATIS da QNH {a}. ¿Pongo {x}?",
        "L'ATIS dà QNH {a}. Imposto {x}?", "O ATIS dá QNH {a}. Ponho {x}?", "De ATIS geeft QNH {a}. {x} instellen?",
        "ATIS podaje QNH {a}. Ustawić {x}?", "ATIS:en ger QNH {a}. Ställa in {x}?", "По ATIS QNH {a}. Ставлю {x}?", "通播的QNH是{a}。设置{x}吗？")),
    (r"^ATC gave us heading (?P<a>\d+)\. Heading (?P<x>\d+)\?$", _by_code(
        "ATC hat uns Kurs {a} gegeben. Kurs {x}?", "Le contrôle nous a donné cap {a}. Cap {x} ?", "Control nos dio rumbo {a}. ¿Rumbo {x}?",
        "Il controllo ci ha dato prua {a}. Prua {x}?", "O controlo deu-nos rumo {a}. Rumo {x}?", "De verkeersleiding gaf ons koers {a}. Koers {x}?",
        "Kontrola dała nam kurs {a}. Kurs {x}?", "Flygledningen gav oss kurs {a}. Kurs {x}?", "Диспетчер дал нам курс {a}. Курс {x}?",
        "管制给的航向是{a}。航向{x}？")),
    (r"^Autobrake max for takeoff\?$", _by_code(
        "Autobrake MAX für den Start?", "Autobrake MAX pour le décollage ?", "¿Autofreno MAX para el despegue?", "Autobrake MAX per il decollo?",
        "Autobrake MAX para a descolagem?", "Autobrake MAX voor de start?", "Autobrake MAX do startu?", "Autobroms MAX för starten?",
        "Автотормоз MAX на взлёт?", "起飞用自动刹车MAX吗？")),
    (r"^Autobrake (?P<x>.+) or (?P<y>.+)\?$", _by_code(
        "Autobrake {x} oder {y}?", "Autobrake {x} ou {y} ?", "¿Autofreno {x} o {y}?", "Autobrake {x} o {y}?", "Autobrake {x} ou {y}?",
        "Autobrake {x} of {y}?", "Autobrake {x} czy {y}?", "Autobroms {x} eller {y}?", "Автотормоз {x} или {y}?", "自动刹车{x}还是{y}？")),
    (r"^Go around\?$", _by_code("Durchstarten?", "Remise des gaz ?", "¿Motor y al aire?", "Riattaccata?", "Borrego?", "Doorstarten?",
                                "Odejście na drugi krąg?", "Gå om?", "Уход на второй круг?", "复飞？")),
    (r"^Declare a (?P<x>mayday|pan-pan) with (?P<s>.+)\?$", _by_code(
        "Einen {x} an {s} absetzen?", "Déclarer un {x} à {s} ?", "¿Declaro {x} con {s}?", "Dichiaro {x} con {s}?", "Declaro {x} a {s}?",
        "Een {x} melden bij {s}?", "Nadać {x} do {s}?", "Deklarera {x} till {s}?", "Объявить {x} для {s}?", "向{s}宣布{x}？")),
    (r"^Declare a (?P<x>mayday|pan-pan)\?$", _by_code(
        "Einen {x} absetzen?", "Déclarer un {x} ?", "¿Declaro {x}?", "Dichiaro {x}?", "Declaro {x}?", "Een {x} melden?", "Nadać {x}?",
        "Deklarera {x}?", "Объявить {x}?", "宣布{x}？")),
    (r"^That sounded like a question\. (?P<reply>.+?) Want (?P<x>.+)\?$", _by_code(
        "Das klang wie eine Frage. {reply} Willst du {x}?", "Ça ressemblait à une question. {reply} Tu veux {x} ?",
        "Eso sonó a pregunta. {reply} ¿Quieres {x}?", "Sembrava una domanda. {reply} Vuoi {x}?", "Pareceu uma pergunta. {reply} Queres {x}?",
        "Dat klonk als een vraag. {reply} Wil je {x}?", "To brzmiało jak pytanie. {reply} Chcesz {x}?", "Det lät som en fråga. {reply} Vill du ha {x}?",
        "Это прозвучало как вопрос. {reply} Хочешь {x}?", "听起来像个问题。{reply}要{x}吗？")),
    (r"^Did you say (?P<x>.+)\?$", _by_code(
        "Hast du {x} gesagt?", "Tu as dit {x} ?", "¿Has dicho {x}?", "Hai detto {x}?", "Disseste {x}?", "Zei je {x}?", "Powiedziałeś {x}?",
        "Sa du {x}?", "Ты сказал {x}?", "你是说{x}吗？")),
    (r"^(?P<x>(?:Gear|Flaps|Heading|Altitude|Speed|Squawk|Autopilot|Autobrake|Spoilers|Landing lights|Taxi lights|Strobe|Beacon|"
     r"Nav lights|Logo lights|Parking brake|Seatbelt sign|Vertical speed|Standard|Altimeter|QNH|Standby)\b[^?,.]{0,30})\?$", _by_code(
        "{x}, richtig?", "{x}, c'est bien ça ?", "{x}, ¿verdad?", "{x}, giusto?", "{x}, certo?", "{x}, toch?", "{x}, tak?", "{x}, eller hur?",
        "{x}, верно?", "{x}，对吗？")),

    # --- the radios ---
    (r"^Which call\?$", _by_code("Welcher Funkspruch?", "Quel appel ?", "¿Qué llamada?", "Quale chiamata?", "Que chamada?", "Welke oproep?",
                                 "Które wywołanie?", "Vilket anrop?", "Какой вызов?", "哪个呼叫？")),
    (r"^Which one\?$", _by_code("Welches?", "Lequel ?", "¿Cuál?", "Quale?", "Qual?", "Welke?", "Który?", "Vilken?", "Какой?", "哪个？")),
    (r"^Asking\.$", _by_code("Ich frage.", "Je demande.", "Lo pido.", "Chiedo.", "Vou pedir.", "Ik vraag het.", "Pytam.", "Jag frågar.",
                             "Запрашиваю.", "我来申请。")),
    (r"^Telling tower\.$", _by_code("Ich sage es dem Tower.", "Je préviens la tour.", "Aviso a la torre.", "Avviso la torre.", "Aviso a torre.",
                                    "Ik meld het aan de toren.", "Informuję wieżę.", "Jag säger till tornet.", "Сообщаю на вышку.", "我告诉塔台。")),
    (r"^Declaring it\.$", _by_code("Ich setze ihn ab.", "Je le déclare.", "Lo declaro.", "Lo dichiaro.", "Vou declarar.", "Ik meld het.",
                                   "Ogłaszam.", "Jag gör det.", "Объявляю.", "我来宣布。")),
    (r"^Who do you want me to ask\?$", _by_code(
        "Wen soll ich fragen?", "Je demande à qui ?", "¿A quién quieres que pregunte?", "A chi devo chiedere?", "A quem queres que pergunte?",
        "Wie moet ik het vragen?", "Kogo mam zapytać?", "Vem ska jag fråga?", "Кого спросить?", "要我问谁？")),
    (r"^Ask them what\?$", _by_code("Was soll ich sie fragen?", "Leur demander quoi ?", "¿Qué les pregunto?", "Cosa devo chiedergli?",
                                    "Pergunto-lhes o quê?", "Wat moet ik ze vragen?", "O co mam ich zapytać?", "Fråga dem om vad?",
                                    "О чём их спросить?", "问他们什么？")),
    (r"^We're on (?P<s>\S+), you'll have to call them yourself\.$", _by_code(
        "Wir sind auf {s}, die musst du selbst rufen.", "On est sur {s}, il faudra les appeler toi-même.", "Estamos en {s}, tendrás que llamarlos tú.",
        "Siamo su {s}, dovrai chiamarli tu.", "Estamos na {s}, vais ter de os chamar tu.", "We zitten op {s}, die moet je zelf oproepen.",
        "Jesteśmy na {s}, musisz ich wywołać sam.", "Vi är på {s}, du får ropa dem själv.", "Мы в {s}, вызывать их придётся тебе самому.",
        "我们在{s}上，得你自己呼叫他们。")),
    (r'^Your radios: ask (?P<s>[^,"]+), (?P<x>".+")\.$', _by_code(
        "Den Funk machst du: frag {s}: {x}.", "La radio, c'est toi : demande à {s} : {x}.", "La radio es tuya: pídele a {s}: {x}.",
        "La radio è tua: chiedi a {s}: {x}.", "O rádio é contigo: pede a {s}: {x}.", "De radio is voor jou: vraag {s}: {x}.",
        "Radio jest twoje: poproś {s}: {x}.", "Radion är din: fråga {s}: {x}.", "Радио на тебе: запроси у {s}: {x}.", "无线电你来：问{s}：{x}。")),
    (r"^Your radios: (?P<call>(?:ask|tell) .+)\.$", _by_code(
        "Den Funk machst du: {call}.", "La radio, c'est toi : {call}.", "La radio es tuya: {call}.", "La radio è tua: {call}.",
        "O rádio é contigo: {call}.", "De radio is voor jou: {call}.", "Radio jest twoje: {call}.", "Radion är din: {call}.",
        "Радио на тебе: {call}.", "无线电你来：{call}。")),
    (r"^Your radios: ask (?P<s>[^,\"]+)\.$", _by_code(
        "Den Funk machst du: frag {s}.", "La radio, c'est toi : demande à {s}.", "La radio es tuya: pregunta a {s}.", "La radio è tua: chiedi a {s}.",
        "O rádio é contigo: pergunta a {s}.", "De radio is voor jou: vraag het {s}.", "Radio jest twoje: zapytaj {s}.", "Radion är din: fråga {s}.",
        "Радио на тебе: запроси {s}.", "无线电你来：问{s}。")),
    (r"^Did you want me to (?P<call>.+)\?$", _by_code(
        "Soll ich {call}?", "Je dois {call} ?", "¿Me encargo de {call}?", "Devo {call}?", "Devo {call}?", "Moet ik {call}?", "Mam {call}?",
        "Ska jag {call}?", "Мне {call}?", "要我{call}吗？")),
    (r"^Tell (?P<s>.+), and they'll clear us for the visual\.$", _by_code(
        "Sag es {s}, dann geben sie uns den Sichtanflug frei.", "Dis-le à {s}, ils nous autoriseront pour l'approche à vue.",
        "Díselo a {s} y nos autorizarán la aproximación visual.", "Dillo a {s} e ci autorizzeranno all'avvicinamento a vista.",
        "Diz a {s} e eles autorizam-nos a aproximação visual.", "Zeg het tegen {s}, dan klaren ze ons voor de visuele nadering.",
        "Powiedz {s}, a dadzą nam zgodę na podejście z widzialnością.", "Säg till {s}, så klarerar de oss för visuell inflygning.",
        "Скажи {s}, и нам разрешат визуальный заход.", "告诉{s}，他们会许可我们目视进近。")),

    # --- answers ---
    (r"^Squawk (?P<x>\d{4})\.$", _by_code("Squawk {x}.", "Transpondeur {x}.", "Transpondedor {x}.", "Transponder {x}.", "Transponder {x}.",
                                          "Transponder {x}.", "Transponder {x}.", "Transponder {x}.", "Ответчик {x}.", "应答机{x}。")),
    (r"^We're about (?P<d>\d+) miles (?P<point>[a-z-]+) of (?P<s>.+)\.$", _by_code(
        "Wir sind etwa {d} {miles} {point} von {s}.", "Nous sommes à environ {d} {miles} {point} de {s}.",
        "Estamos a unas {d} {miles} {point} de {s}.", "Siamo a circa {d} {miles} {point} di {s}.", "Estamos a cerca de {d} {miles} {point} de {s}.",
        "We zitten ongeveer {d} {miles} {point} van {s}.", "Jesteśmy około {d} {miles} {point} od {s}.", "Vi är ungefär {d} {miles} {point} om {s}.",
        "Мы примерно в {d} милях {point} {s}.", "我们在{s}{point}约{d}{miles}。")),
    (r"^We're over (?P<s>.+)\.$", _by_code(
        "Wir sind über {s}.", "Nous survolons {s}.", "Estamos sobre {s}.", "Siamo sopra {s}.", "Estamos sobre {s}.", "We vliegen boven {s}.",
        "Jesteśmy nad {s}.", "Vi är över {s}.", "Мы над {s}.", "我们在{s}上空。")),
    (r"^Nowhere near a town; (?P<x>[\d.]+[NS] [\d.]+[EW])\.$", _by_code(
        "Keine Stadt in der Nähe; {x}.", "Aucune ville à proximité ; {x}.", "No hay ninguna ciudad cerca; {x}.", "Nessuna città nelle vicinanze; {x}.",
        "Nenhuma cidade por perto; {x}.", "Geen stad in de buurt; {x}.", "W pobliżu nie ma żadnego miasta; {x}.", "Ingen stad i närheten; {x}.",
        "Рядом нет городов; {x}.", "附近没有城镇；{x}。")),
    (r"^Runway (?P<x>\S+): (?P<wind>.+)\.$", _by_code(
        "Piste {x}: {wind}.", "Piste {x} : {wind}.", "Pista {x}: {wind}.", "Pista {x}: {wind}.", "Pista {x}: {wind}.", "Baan {x}: {wind}.",
        "Pas {x}: {wind}.", "Bana {x}: {wind}.", "Полоса {x}: {wind}.", "{x}号跑道：{wind}。")),
    (r"^(?:Gate |Stand )?(?P<x>[A-Z]{0,2}\d{1,3}[A-Z]?)\.$", _by_code(
        "Gate {x}.", "Porte {x}.", "Puerta {x}.", "Gate {x}.", "Porta {x}.", "Gate {x}.", "Stanowisko {x}.", "Gate {x}.", "Стоянка {x}.",
        "{x}号机位。")),
)


def templated(text: str, code: str, again: Callable[[str, str], str | None], plain: Callable[[str], str],
              units: dict[str, str]) -> str | None:
    """One of these lines in language ``code``; None if ``text`` isn't one (or has a word no table here has). ``again``:
    a set line put into the language (foreign.templated); ``plain``: a value kept as said, its numbers the language's;
    ``units``: what feet and miles are called."""
    for pattern, said in LINES:
        if code not in said or (m := re.match(pattern, text)) is None:
            continue
        values = {}
        for name, value in m.groupdict().items():
            values[name] = _value(name, value or "", code, again, plain, units)
            if values[name] is None:
                break
        else:
            return said[code].format(**values, **units)
    return None


def _value(name: str, value: str, code: str, again: Callable[[str, str], str | None], plain: Callable[[str], str],
           units: dict[str, str]) -> str | None:
    if name == "thing":
        return THING[code].get(value.lower())
    if name == "light":
        return LIGHT[code].get(value.lower())
    if name == "onoff":
        return _ONOFF[code][value != "on"]
    if name == "updown":
        return UPDOWN[code].get(value)
    if name == "phase":
        return PHASE[code].get(value)
    if name == "key":
        return KEY[code].get(value)
    if name == "point":
        return POINT[code].get(value)
    if name == "emergency":
        return EMERGENCY[code].get(value)
    if name == "wind":
        return _wind(value, code)
    if name == "call":
        return _call(value, code)
    if name == "reply":
        return again(value, code)
    said = plain(value)
    for unit in ("feet", "knots", "miles"):
        said = re.sub(rf"{' ' if code == 'zh' else ''}\b{unit}\b", units.get(unit, unit), said)
    return said
