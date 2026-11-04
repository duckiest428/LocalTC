"""The same things said to the copilot in every language it talks in, read without a language model: every language gets what
any one of them does."""

import pytest

from localtc.crew import foreign

SAID: list[tuple[str, dict[str, list[str]]]] = [
    ("gear down", {
        "de": ["Fahrwerk ausfahren", "Fahrwerk raus bitte", "Fahrwerk runter"],
        "fr": ["Sors le train", "Train sorti", "Sortir le train d'atterrissage"],
        "es": ["Baja el tren", "Tren abajo", "Saca el tren de aterrizaje"],
        "it": ["Carrello giù", "Abbassa il carrello", "Fuori il carrello"],
        "pt": ["Baixa o trem", "Trem em baixo", "Baixar o trem de aterragem"],
        "nl": ["Landingsgestel neer", "Gear down", "Landingsgestel uit"],
        "pl": ["Wypuść podwozie", "Podwozie w dół", "Podwozie wypuścić"],
        "sv": ["Fäll ut stället", "Stället ner", "Landställ ut"],
        "ru": ["Выпусти шасси", "Шасси выпустить", "Шасси выпустить, пожалуйста"],
        "zh": ["放下起落架", "起落架放下", "放起落架"]}),
    ("gear up", {
        "de": ["Fahrwerk einfahren", "Fahrwerk rein"],
        "fr": ["Rentre le train", "Train rentré"],
        "es": ["Sube el tren", "Tren arriba"],
        "it": ["Carrello su", "Retrai il carrello"],
        "pt": ["Recolhe o trem", "Trem em cima"],
        "nl": ["Landingsgestel op", "Landingsgestel omhoog"],
        "pl": ["Schowaj podwozie", "Podwozie w górę"],
        "sv": ["Fäll in stället", "Stället upp"],
        "ru": ["Убери шасси", "Шасси убрать"],
        "zh": ["收起落架", "起落架收上"]}),
    ("flaps 2", {
        "de": ["Klappen zwei", "Klappen zwei bitte", "Klappen auf zwei"],
        "fr": ["Volets deux", "Volets deux s'il te plaît"],
        "es": ["Flaps dos", "Flaps dos, por favor"],
        "it": ["Flap due", "Flap due per favore"],
        "pt": ["Flaps dois", "Flaps dois, por favor"],
        "nl": ["Flaps twee", "Kleppen twee alsjeblieft"],
        "pl": ["Klapy dwa", "Klapy dwa proszę"],
        "sv": ["Klaff två", "Klaffar två tack"],
        "ru": ["Закрылки два", "Закрылки два, пожалуйста"],
        "zh": ["襟翼二", "襟翼两", "襟翼2"]}),
    ("flaps up", {
        "de": ["Klappen einfahren"], "fr": ["Rentre les volets"], "es": ["Sube los flaps"], "it": ["Retrai i flap"],
        "pt": ["Recolhe os flaps"], "nl": ["Flaps op"], "pl": ["Schowaj klapy"], "sv": ["Klaff upp"], "ru": ["Убери закрылки"],
        "zh": ["收襟翼"]}),
    ("flaps full", {
        "de": ["Klappen voll"], "fr": ["Volets pleins", "Volets full"], "es": ["Flaps completos"], "it": ["Flap tutti", "Flap full"],
        "pt": ["Flaps todos", "Flaps full"], "nl": ["Flaps vol"], "pl": ["Klapy pełne"], "sv": ["Klaff full"], "ru": ["Закрылки полностью"],
        "zh": ["襟翼全放"]}),
    ("landing lights on", {
        "de": ["Landescheinwerfer an", "Mach die Landelichter an"],
        "fr": ["Allume les phares d'atterrissage", "Phares d'atterrissage allumés"],
        "es": ["Enciende las luces de aterrizaje", "Luces de aterrizaje encendidas"],
        "it": ["Accendi le luci di atterraggio", "Luci di atterraggio accese"],
        "pt": ["Liga as luzes de aterragem", "Acende as luzes de pouso", "Luzes de aterragem ligadas"],
        "nl": ["Landingslichten aan", "Zet de landingslichten aan"],
        "pl": ["Włącz światła do lądowania", "Światła lądowania włączone"],
        "sv": ["Landningsljus på", "Tänd landningsljusen"],
        "ru": ["Включи посадочные фары", "Посадочные фары включить"],
        "zh": ["打开着陆灯", "着陆灯打开"]}),
    ("taxi lights off", {
        "de": ["Rollscheinwerfer aus"], "fr": ["Éteins les feux de roulage"], "es": ["Apaga las luces de rodaje"],
        "it": ["Spegni le luci di rullaggio"], "pt": ["Desliga as luzes de rolagem"], "nl": ["Taxilichten uit"],
        "pl": ["Wyłącz światła kołowania"], "sv": ["Taxiljus av"], "ru": ["Выключи рулёжные фары"], "zh": ["关闭滑行灯"]}),
    ("strobes on", {
        "de": ["Strobes an", "Blitzlichter an"], "fr": ["Allume les strobes"], "es": ["Enciende los estrobos"],
        "it": ["Accendi le strobo"], "pt": ["Liga os strobes"], "nl": ["Strobes aan"], "pl": ["Włącz stroboskopy"],
        "sv": ["Strobe på"], "ru": ["Включи стробы"], "zh": ["打开频闪灯"]}),
    ("beacon on", {
        "de": ["Beacon an"], "fr": ["Allume le beacon"], "es": ["Enciende el beacon"], "it": ["Accendi il beacon"],
        "pt": ["Liga o beacon"], "nl": ["Beacon aan"], "pl": ["Włącz beacon"], "sv": ["Beacon på"], "ru": ["Включи маяк"],
        "zh": ["打开防撞灯"]}),
    ("seatbelt sign on", {
        "de": ["Anschnallzeichen an"], "fr": ["Allume les consignes ceintures", "Allume le signal ceintures"],
        "es": ["Enciende la señal de cinturones"], "it": ["Accendi il segnale cinture"], "pt": ["Liga o sinal de cintos"],
        "nl": ["Gordelteken aan"], "pl": ["Włącz znak zapięcia pasów", "Włącz pasy"], "sv": ["Bältesskylten på"],
        "ru": ["Включи табло ремней"], "zh": ["打开安全带指示灯"]}),
    ("seatbelt sign off", {
        "de": ["Anschnallzeichen aus"], "fr": ["Éteins les consignes ceintures"], "es": ["Apaga la señal de cinturones"],
        "it": ["Spegni il segnale cinture"], "pt": ["Desliga o sinal de cintos"], "nl": ["Gordelteken uit"],
        "pl": ["Wyłącz znak zapięcia pasów"], "sv": ["Bältesskylten av"], "ru": ["Выключи табло ремней"], "zh": ["关闭安全带指示灯"]}),
    ("autopilot on", {
        "de": ["Autopilot ein", "Autopilot einschalten", "Schalte den Autopiloten ein"], "fr": ["Active le pilote automatique"],
        "es": ["Conecta el piloto automático", "Piloto automático conectado"], "it": ["Inserisci il pilota automatico", "Attiva il pilota automatico"],
        "pt": ["Liga o piloto automático", "Ativa o piloto automático"], "nl": ["Autopiloot aan", "Automatische piloot aan"],
        "pl": ["Włącz autopilota", "Autopilot włączyć"], "sv": ["Autopilot på", "Koppla in autopiloten"],
        "ru": ["Включи автопилот", "Автопилот включить"], "zh": ["接通自动驾驶", "打开自动驾驶仪"]}),
    ("autopilot off", {
        "de": ["Autopilot aus"], "fr": ["Coupe le pilote automatique"], "es": ["Desconecta el piloto automático"],
        "it": ["Disinserisci il pilota automatico"], "pt": ["Desliga o piloto automático"], "nl": ["Autopiloot uit"],
        "pl": ["Wyłącz autopilota"], "sv": ["Koppla ur autopiloten"], "ru": ["Выключи автопилот"], "zh": ["断开自动驾驶"]}),
    ("arm the spoilers", {
        "de": ["Spoiler scharf", "Spoiler armieren"], "fr": ["Arme les spoilers"], "es": ["Arma los spoilers"], "it": ["Arma gli spoiler"],
        "pt": ["Arma os spoilers"], "nl": ["Spoilers armen"], "pl": ["Uzbrój spoilery"], "sv": ["Arma spoilers"], "ru": ["Взведи спойлеры"],
        "zh": ["扰流板预位"]}),
    ("parking brake on", {
        "de": ["Parkbremse setzen", "Parkbremse an"], "fr": ["Mets le frein de parking", "Serre le frein de parking"],
        "es": ["Pon el freno de estacionamiento"], "it": ["Inserisci il freno di parcheggio"], "pt": ["Põe o travão de estacionamento", "Liga o freio de estacionamento"],
        "nl": ["Parkeerrem aan", "Zet de parkeerrem aan"], "pl": ["Włącz hamulec postojowy", "Zaciągnij hamulec postojowy"],
        "sv": ["Parkeringsbromsen på", "Sätt parkeringsbromsen"], "ru": ["Включи стояночный тормоз", "Поставь стояночный тормоз"],
        "zh": ["设置停留刹车", "打开停留刹车"]}),
    ("parking brake off", {
        "de": ["Parkbremse lösen", "Parkbremse aus"], "fr": ["Desserre le frein de parking", "Relâche le frein de parking"],
        "es": ["Quita el freno de estacionamiento", "Suelta el freno de estacionamiento"], "it": ["Rilascia il freno di parcheggio", "Togli il freno di parcheggio"],
        "pt": ["Solta o travão de estacionamento", "Tira o freio de estacionamento"], "nl": ["Parkeerrem los", "Parkeerrem eraf"],
        "pl": ["Zwolnij hamulec postojowy"], "sv": ["Släpp parkeringsbromsen"], "ru": ["Сними стояночный тормоз", "Отпусти стояночный тормоз"],
        "zh": ["松开停留刹车", "解除停留刹车"]}),
    ("start the apu", {
        "de": ["APU starten"], "fr": ["Démarre l'APU"], "es": ["Arranca la APU"], "it": ["Avvia l'APU"], "pt": ["Arranca a APU", "Liga a APU"],
        "nl": ["APU starten"], "pl": ["Uruchom APU"], "sv": ["Starta APU"], "ru": ["Запусти APU", "Запусти ВСУ"], "zh": ["启动APU", "启动辅助动力装置"]}),
    ("heading 270", {
        "de": ["Kurs zwei sieben null", "Kurs 270"], "fr": ["Cap deux sept zéro", "Cap 270"], "es": ["Rumbo dos siete cero"],
        "it": ["Prua due sette zero", "Prua 270"], "pt": ["Rumo dois sete zero", "Proa 270"], "nl": ["Koers twee zeven nul"],
        "pl": ["Kurs dwa siedem zero"], "sv": ["Kurs två sju noll"], "ru": ["Курс два семь ноль"], "zh": ["航向二七零", "航向两七洞", "航向270"]}),
    ("heading 180", {
        "de": ["Kurs einhundertachtzig"], "fr": ["Cap cent quatre-vingts"], "es": ["Rumbo ciento ochenta"], "it": ["Prua centottanta"],
        "pt": ["Rumo cento e oitenta"], "nl": ["Koers honderdtachtig"], "pl": ["Kurs sto osiemdziesiąt"], "sv": ["Kurs etthundraåttio"],
        "ru": ["Курс сто восемьдесят"], "zh": ["航向一百八十"]}),
    ("altitude 10000", {
        "de": ["Höhe zehntausend Fuß", "Steigen auf zehntausend Fuß", "Höhe 10000"], "fr": ["Altitude dix mille pieds", "Monte à dix mille pieds"],
        "es": ["Sube a diez mil pies", "Altitud diez mil"], "it": ["Quota diecimila piedi", "Sali a diecimila piedi"],
        "pt": ["Altitude dez mil pés", "Sobe para dez mil pés"], "nl": ["Hoogte tienduizend voet", "Klim naar tienduizend voet"],
        "pl": ["Wysokość dziesięć tysięcy stóp", "Wznoś na dziesięć tysięcy stóp"], "sv": ["Höjd tiotusen fot", "Stig till tiotusen fot"],
        "ru": ["Высота десять тысяч футов", "Набери десять тысяч футов"], "zh": ["高度一万英尺", "高度10000英尺", "上升到一万英尺"]}),
    ("altitude 35000", {
        "de": ["Flugfläche drei fünf null", "Flugfläche dreihundertfünfzig"], "fr": ["Niveau de vol trois cinq zéro", "Niveau trois cent cinquante"],
        "es": ["Nivel de vuelo tres cinco cero"], "it": ["Livello di volo tre cinque zero", "Livello trecentocinquanta"],
        "pt": ["Nível de voo três cinco zero"], "nl": ["Vliegniveau drie vijf nul"], "pl": ["Poziom lotu trzy pięć zero"],
        "sv": ["Flygnivå tre fem noll"], "ru": ["Эшелон три пять ноль", "Эшелон триста пятьдесят"], "zh": ["飞行高度层三五零"]}),
    ("speed 250", {
        "de": ["Geschwindigkeit zweihundertfünfzig", "Fahrt zwei fünf null", "Zweihundertfünfzig Knoten"], "fr": ["Vitesse deux cent cinquante", "Deux cent cinquante nœuds"],
        "es": ["Velocidad doscientos cincuenta", "Velocidad 250 nudos"], "it": ["Velocità duecentocinquanta", "Duecentocinquanta nodi"],
        "pt": ["Velocidade duzentos e cinquenta", "Duzentos e cinquenta nós"], "nl": ["Snelheid tweehonderdvijftig", "Tweehonderdvijftig knopen"],
        "pl": ["Prędkość dwieście pięćdziesiąt", "Dwieście pięćdziesiąt węzłów"], "sv": ["Fart tvåhundrafemtio", "Tvåhundrafemtio knop"],
        "ru": ["Скорость двести пятьдесят", "Двести пятьдесят узлов"], "zh": ["速度二百五十", "速度两百五十节", "二百五十节"]}),
    ("squawk 7000", {
        "de": ["Transponder siebentausend", "Squawk sieben null null null"], "fr": ["Transpondeur sept mille"], "es": ["Pon el transpondedor siete mil"],
        "it": ["Transponder settemila"], "pt": ["Transponder sete mil"], "nl": ["Transponder zevenduizend"], "pl": ["Transponder siedem tysięcy"],
        "sv": ["Transponder sjutusen"], "ru": ["Ответчик семь тысяч"], "zh": ["应答机七千", "应答机七零零零"]}),
    ("gear down, flaps 2", {
        "de": ["Fahrwerk raus und Klappen zwei"], "fr": ["Sors le train et volets deux"], "es": ["Tren abajo y flaps dos"], "it": ["Carrello giù e flap due"],
        "pt": ["Trem em baixo e flaps dois"], "nl": ["Landingsgestel neer en flaps twee"], "pl": ["Podwozie w dół i klapy dwa"], "sv": ["Stället ner och klaff två"],
        "ru": ["Шасси выпустить и закрылки два"], "zh": ["放下起落架，襟翼二", "放下起落架然后襟翼二"]}),
    ("run the before start checklist", {
        "de": ["Checkliste vor dem Start", "Checkliste vor dem Anlassen"], "fr": ["Checklist avant mise en route"], "es": ["Lista de chequeo antes del arranque", "Checklist antes de arrancar"],
        "it": ["Checklist prima dell'avviamento"], "pt": ["Checklist antes do arranque", "Lista de verificação antes da partida"], "nl": ["Checklist voor het starten"],
        "pl": ["Checklista przed uruchomieniem"], "sv": ["Checklista före start"], "ru": ["Карта перед запуском"], "zh": ["启动前检查单"]}),
    ("run the approach checklist", {
        "de": ["Anflugcheckliste", "Checkliste Anflug"], "fr": ["Checklist approche", "Checklist d'approche"], "es": ["Lista de aproximación", "Checklist de aproximación"],
        "it": ["Checklist di avvicinamento"], "pt": ["Checklist de aproximação"], "nl": ["Naderingschecklist"], "pl": ["Checklista podejścia"],
        "sv": ["Inflygningschecklista"], "ru": ["Карта перед заходом"], "zh": ["进近检查单", "执行进近检查单"]}),
    ("run the landing checklist", {
        "de": ["Landecheckliste bitte"], "fr": ["Checklist atterrissage", "Checklist d'atterrissage"], "es": ["Lista de aterrizaje"], "it": ["Checklist di atterraggio"],
        "pt": ["Checklist de aterragem", "Checklist de pouso"], "nl": ["Landingschecklist"], "pl": ["Checklista lądowania"], "sv": ["Landningschecklista"],
        "ru": ["Карта перед посадкой"], "zh": ["着陆检查单"]}),
    ("run the after landing checklist", {
        "de": ["Nachlandecheckliste"], "fr": ["Checklist après atterrissage"], "es": ["Lista después del aterrizaje"], "it": ["Checklist dopo l'atterraggio"],
        "pt": ["Checklist depois da aterragem"], "nl": ["Checklist na de landing"], "pl": ["Checklista po lądowaniu"], "sv": ["Checklista efter landning"],
        "ru": ["Карта после посадки"], "zh": ["着陆后检查单"]}),
    ("checklist complete", {
        "de": ["Checkliste fertig", "Checkliste abgeschlossen"], "fr": ["Checklist terminée"], "es": ["Lista completada"], "it": ["Checklist completata"],
        "pt": ["Checklist concluída"], "nl": ["Checklist klaar"], "pl": ["Checklista zakończona"], "sv": ["Checklista klar"], "ru": ["Карта выполнена"], "zh": ["检查单完成"]}),
    ("yes", {
        "de": ["ja", "ja, bestätigt", "richtig"], "fr": ["oui", "oui, confirmé"], "es": ["sí", "sí, confirmado"], "it": ["sì", "sì, confermo"],
        "pt": ["sim", "sim, confirmado"], "nl": ["ja", "ja, bevestigd"], "pl": ["tak", "tak, potwierdzam"], "sv": ["ja", "ja, bekräftat"],
        "ru": ["да", "да, подтверждаю"], "zh": ["是的", "确认"]}),
    ("no", {
        "de": ["nein"], "fr": ["non"], "es": ["no"], "it": ["no"], "pt": ["não"], "nl": ["nee"], "pl": ["nie"], "sv": ["nej"], "ru": ["нет"], "zh": ["不"]}),
    ("cancel that", {
        "de": ["abbrechen", "vergiss es"], "fr": ["annule", "laisse tomber"], "es": ["cancela", "olvídalo"], "it": ["annulla", "lascia perdere"],
        "pt": ["cancela", "esquece"], "nl": ["annuleer", "laat maar"], "pl": ["anuluj"], "sv": ["avbryt"], "ru": ["отмена", "отставить"], "zh": ["取消", "算了"]}),
    ("how much fuel do we have?", {
        "de": ["Wie viel Treibstoff haben wir?", "Wie viel Sprit haben wir noch?"], "fr": ["Combien de carburant avons-nous ?", "Il nous reste combien de carburant ?"],
        "es": ["¿Cuánto combustible tenemos?"], "it": ["Quanto carburante abbiamo?"], "pt": ["Quanto combustível temos?"], "nl": ["Hoeveel brandstof hebben we?"],
        "pl": ["Ile mamy paliwa?"], "sv": ["Hur mycket bränsle har vi?"], "ru": ["Сколько у нас топлива?", "Сколько топлива осталось?"], "zh": ["我们还有多少燃油？", "油量多少？"]}),
    ("what did atc say?", {
        "de": ["Was hat die Flugsicherung gesagt?", "Was hat der Lotse gesagt?"], "fr": ["Qu'est-ce que le contrôle a dit ?"], "es": ["¿Qué ha dicho la torre?"],
        "it": ["Cosa ha detto il controllo?"], "pt": ["O que disse o controlo?", "O que disse a torre?"], "nl": ["Wat zei de verkeersleiding?"],
        "pl": ["Co powiedziała wieża?"], "sv": ["Vad sa flygledningen?"], "ru": ["Что сказал диспетчер?"], "zh": ["管制说了什么？", "塔台说什么？"]}),
    ("what altitude are we at?", {
        "de": ["Wie hoch sind wir?", "Welche Höhe haben wir?"], "fr": ["Quelle est notre altitude ?", "On est à quelle altitude ?"], "es": ["¿A qué altitud estamos?"],
        "it": ["A che quota siamo?"], "pt": ["A que altitude estamos?"], "nl": ["Hoe hoog zitten we?", "Wat is onze hoogte?"], "pl": ["Na jakiej jesteśmy wysokości?"],
        "sv": ["Vilken höjd har vi?"], "ru": ["На какой мы высоте?", "Какая у нас высота?"], "zh": ["我们现在什么高度？", "高度多少？"]}),
    ("what speed are we doing?", {
        "de": ["Wie schnell sind wir?", "Welche Geschwindigkeit haben wir?"], "fr": ["Quelle est notre vitesse ?"], "es": ["¿Qué velocidad llevamos?"], "it": ["A che velocità siamo?"],
        "pt": ["Qual é a nossa velocidade?"], "nl": ["Hoe snel gaan we?"], "pl": ["Jaka jest nasza prędkość?"], "sv": ["Vilken fart har vi?"], "ru": ["Какая у нас скорость?"], "zh": ["我们现在什么速度？", "速度多少？"]}),
    ("what heading are we on?", {
        "de": ["Welchen Kurs haben wir?"], "fr": ["Quel est notre cap ?"], "es": ["¿Qué rumbo llevamos?"], "it": ["Che prua abbiamo?"], "pt": ["Qual é o nosso rumo?"],
        "nl": ["Wat is onze koers?"], "pl": ["Jaki mamy kurs?"], "sv": ["Vilken kurs har vi?"], "ru": ["Какой у нас курс?"], "zh": ["航向多少？"]}),
    ("how long to destination?", {
        "de": ["Wie lange noch?", "Wann landen wir?"], "fr": ["Combien de temps reste-t-il ?", "On arrive quand ?"], "es": ["¿Cuánto queda?", "¿Cuándo aterrizamos?"], "it": ["Quanto manca?", "Quando atterriamo?"],
        "pt": ["Quanto falta?", "Quando aterramos?"], "nl": ["Hoe lang nog?", "Wanneer landen we?"], "pl": ["Ile jeszcze?", "Kiedy lądujemy?"], "sv": ["Hur länge till?", "När landar vi?"],
        "ru": ["Сколько осталось?", "Когда садимся?"], "zh": ["还要多久？", "什么时候落地？"]}),
    ("what is the weather at destination?", {
        "de": ["Wie ist das Wetter am Ziel?"], "fr": ["Quelle est la météo à destination ?"], "es": ["¿Qué tiempo hace en destino?"], "it": ["Che tempo fa a destinazione?"],
        "pt": ["Como está o tempo no destino?"], "nl": ["Hoe is het weer op de bestemming?"], "pl": ["Jaka jest pogoda na lotnisku docelowym?"], "sv": ["Hur är vädret på destinationen?"],
        "ru": ["Какая погода в пункте назначения?"], "zh": ["目的地天气怎么样？"]}),
]

CASES = [(code, text, english) for english, by_code in SAID for code, texts in by_code.items() for text in texts]


@pytest.mark.parametrize("code, text, english", CASES)
def test_said_in_every_language(code, text, english):
    assert foreign.gloss(text, code) == english


def test_the_list_covers_every_language():
    for english, by_code in SAID:
        assert set(by_code) == set(foreign.WORDS), english


ANSWERS = ["We're at 5,000 feet.", "We're at 5,000 feet, cleared to FL350.", "We've got 41,300 pounds, about 8 hours 16 minutes at this burn.",
           "We've got 41,300 pounds.", "The fuel quantity isn't showing here.", "1,234 miles, about 2 hours 5 minutes to KLAX.", "1,234 miles to KLAX.",
           "I don't have the destination's position yet.", "Nothing from ATC yet.", "We're with Zurich Ground.", "We're not on an ATC frequency.",
           "No code assigned yet.", "At KLAX: wind 250 at 8.", "No ATIS for the destination yet.", "It's 1435 Zulu.", "No runway assigned yet.", "Runway 16.",
           "No gate assigned yet.", "I don't have the runway's wind yet.", "Gear's down.", "Gear's up.", "Flaps 2.", "Autopilot's on.", "Autopilot's off.",
           "I can't read the autopilot on this aircraft.", "280 knots indicated, 310 over the ground.", "Heading 270."]


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
@pytest.mark.parametrize("english", ANSWERS)
def test_the_common_answers_need_no_model_and_keep_their_numbers(code, english):
    said = foreign.templated(english, code)
    assert said is not None and "{" not in said
    assert said != english or english == "Flaps 2."  # (the same word in some of them)
    if not english.startswith(("We're with", "At KLAX")):  # (a station's name and the weather stay as they are)
        assert foreign.speaks(said, code)
    assert sorted(foreign.numbers(said.replace("\u202f", " "))) == sorted(foreign.numbers(english))


@pytest.mark.parametrize("code, hours", [("pl", "2 godziny 5 minut"), ("ru", "2 часа 5 минут"), ("de", "2 Stunden 5 Minuten"), ("zh", "2小时5分钟"), ("es", "2 horas 5 minutos")])
def test_hours_as_the_language_says_them(code, hours):
    assert hours in foreign.templated("1,234 miles, about 2 hours 5 minutes to KLAX.", code)


@pytest.mark.parametrize("code, said, digits", [
    ("de", "Wir haben einundvierzigtausenddreihundert Pfund", "41300"), ("fr", "Nous avons quarante et un mille trois cents livres", "41300"),
    ("es", "Tenemos cuarenta y un mil trescientas libras", "41300"), ("it", "Abbiamo quarantunomilatrecento libbre", "41300"),
    ("pt", "Temos quarenta e um mil e trezentas libras", "41300"), ("nl", "We hebben eenenveertigduizend driehonderd pond", "41300"),
    ("pl", "Mamy czterdzieści jeden tysięcy trzysta funtów", "41300"), ("sv", "Vi har fyrtioettusentrehundra pund", "41300"),
    ("ru", "У нас сорок одна тысяча триста фунтов", "41300"),
])
def test_a_translation_with_its_numbers_in_words_is_checked_as_digits(code, said, digits):
    assert foreign.numbers(foreign.in_digits(said, code)) == [digits]


def test_a_word_that_only_looks_like_a_number_isnt_one():
    assert foreign.numbers(foreign.in_digits("Sei pronto?", "it")) == []
    assert foreign.numbers(foreign.in_digits("una hora", "es")) == []
    assert foreign.numbers(foreign.in_digits("ein bisschen", "de")) == []


@pytest.mark.parametrize("code, english, said", [
    ("de", "Altimeter 29.92.", "Höhenmesser auf 29,92."), ("fr", "Altimeter 29.92.", "Altimètre sur 29,92."), ("pl", "Altimeter 29.92.", "Wysokościomierz 29,92."),
    ("ru", "Altimeter 29.92.", "Альтиметр двадцать девять целых девяносто два."), ("ru", "About 1 hour 20 minutes.", "Примерно час двадцать."),
    ("pl", "About 1 hour 20 minutes.", "Około godzinę dwadzieścia."), ("zh", "Flaps 2.", "襟翼放到两档。"), ("zh", "41,300 pounds.", "四万一千三百磅。"),
    ("de", "About 1 hour 20 minutes.", "Etwa eine Stunde zwanzig Minuten."), ("it", "About 1 hour 20 minutes.", "Circa un'ora e venti."),
])
def test_a_good_translation_is_kept_however_it_writes_its_numbers(code, english, said):
    assert foreign.numbers_kept(english, said, code)


# --- the ground crew ---------------------------------------------------------------------------------------------------


RAMP: list[tuple[list[str], dict[str, str]]] = [
    (["request", "Catering", "Refueling", "GPU"], {"de": "Rampe, Catering, Treibstoff und eine GPU bitte", "fr": "Rampe, catering, carburant et un GPU s'il vous plaît",
     "es": "Rampa, catering, combustible y un GPU por favor", "it": "Rampa, catering, carburante e un GPU per favore", "pt": "Rampa, catering, combustível e um GPU por favor",
     "nl": "Platform, catering, brandstof en een GPU graag", "pl": "Rampa, catering, paliwo i GPU proszę", "sv": "Ramp, catering, bränsle och en GPU tack", "ru": "Перрон, кейтеринг, топливо и ГПУ, пожалуйста", "zh": "地勤，配餐、加油和地面电源"}),
    (["push", "nose_left"], {"de": "Rampe, Pushback, Nase links", "fr": "Rampe, repoussage, nez à gauche", "es": "Rampa, remolque, morro a la izquierda", "it": "Rampa, pushback, muso a sinistra",
     "pt": "Rampa, pushback, nariz à esquerda", "nl": "Platform, pushback, neus links", "pl": "Rampa, wypychanie, nos w lewo", "sv": "Ramp, pushback, nosen vänster", "ru": "Перрон, буксировка, нос влево", "zh": "地勤，推出，机头向左"}),
    (["push", "nose_left"], {"de": "Heck rechts", "fr": "Queue à droite", "es": "Cola a la derecha", "it": "Coda a destra", "pt": "Cauda à direita", "nl": "Staart rechts", "pl": "Ogon w prawo", "sv": "Stjärten höger", "ru": "Хвост вправо", "zh": "机尾向右"}),
    (["status"], {"de": "Rampe, was ist noch offen?", "fr": "Rampe, qu'est-ce qui reste ?", "es": "Rampa, ¿qué falta?", "it": "Rampa, cosa manca?", "pt": "Rampa, o que falta?", "nl": "Platform, wat staat er nog open?",
     "pl": "Rampa, co zostało?", "sv": "Ramp, vad är kvar?", "ru": "Перрон, что осталось?", "zh": "地勤，还剩什么？"}),
    (["cancel", "Catering"], {"de": "Rampe, Catering abbrechen", "fr": "Rampe, annule le catering", "es": "Rampa, cancela el catering", "it": "Rampa, annulla il catering", "pt": "Rampa, cancela o catering", "nl": "Platform, annuleer de catering",
     "pl": "Rampa, anuluj catering", "sv": "Ramp, avbryt catering", "ru": "Перрон, отмени кейтеринг", "zh": "地勤，取消配餐"}),
    (["off", "OperateJetways"], {"de": "Rampe, Fluggastbrücke entfernen", "fr": "Rampe, retire la passerelle", "es": "Rampa, retira la pasarela", "it": "Rampa, togli il finger", "pt": "Rampa, retira a manga", "nl": "Platform, verwijder de slurf",
     "pl": "Rampa, odłącz rękaw", "sv": "Ramp, ta bort flygbryggan", "ru": "Перрон, убери телетрап", "zh": "地勤，撤走廊桥"}),
    (["request", "Water", "Lavatory"], {"de": "Rampe, Wasser und Toiletten bitte", "fr": "Rampe, eau et toilettes s'il te plaît", "es": "Rampa, agua y lavabos por favor", "it": "Rampa, acqua e toilette per favore", "pt": "Rampa, água e sanitários por favor",
     "nl": "Platform, water en toiletten graag", "pl": "Rampa, woda i toalety proszę", "sv": "Ramp, vatten och toaletter tack", "ru": "Перрон, вода и туалеты, пожалуйста", "zh": "地勤，清水和污水"}),
    (["level", "60"], {"de": "Sechzig Prozent", "fr": "Soixante pour cent", "es": "Sesenta por ciento", "it": "Sessanta per cento", "pt": "Sessenta por cento", "nl": "Zestig procent", "pl": "Sześćdziesiąt procent", "sv": "Sextio procent", "ru": "Шестьдесят процентов", "zh": "百分之六十"}),
    (["yes"], {"de": "ja", "fr": "oui", "es": "sí", "it": "sì", "pt": "sim", "nl": "ja", "pl": "tak", "sv": "ja", "ru": "да", "zh": "确认"}),
    (["no"], {"de": "nein", "fr": "non", "es": "no", "it": "no", "pt": "não", "nl": "nee", "pl": "nie", "sv": "nej", "ru": "нет", "zh": "不用"}),
]
RAMP_CASES = [(code, text, want) for want, by_code in RAMP for code, text in by_code.items()]


@pytest.mark.parametrize("code, text, want", RAMP_CASES)
def test_said_to_the_ramp_in_every_language(code, text, want):
    from localtc.ground import grammar, words

    asks = grammar.parse(words.as_english(text, code))
    assert asks, words.as_english(text, code)
    ask = asks[0]
    assert ask.action == want[0]
    if want[0] in ("request", "cancel", "off"):
        assert ask.services == tuple(want[1:])
    elif want[0] == "push":
        assert ask.direction == want[1]
    elif want[0] == "level":
        assert ask.option == int(want[1])


@pytest.mark.parametrize("code, text", [(c, t) for w, by in RAMP for c, t in by.items() if t.split(",")[0].lower() in ("rampe", "rampa", "platform", "ramp", "перрон") or t.startswith("地勤")])
def test_the_ramp_is_called_by_name_in_every_language(code, text):
    from localtc.ground import grammar, words

    assert grammar.is_addressed(words.as_english(text, code))


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
def test_every_ramp_line_is_there_and_fills_in(code):
    from localtc.ground import lines, words

    missing = set(lines.LINES) - set(words.LINES[code])
    assert not missing
    table = lines.Words(1, code)
    values = {"list": "a", "name": "b", "low": "35", "high": "100", "percent": "60", "message": "m", "choices": "c", "direction": "d", "why": "w", "title": "t", "choice": "c",
              "parked": "p", "gate": "g", "report": "r", "door": "R4", "fuel": ""}
    for key in lines.LINES:
        assert "{" not in table.line(key, **values)
        assert table.in_language(key)
    assert not table.in_language("set_brake")  # the headset's calls stay as they are
    assert set(words.NAMES[code]) == set(lines.SPOKEN) and code in words.TERMS and set(words.WHY[code]) == {"never_opened", "missing", "disabled", "no_change", "ambiguous"}


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
def test_the_ramp_answers_in_the_pilots_language_and_the_headset_in_english(code):
    from test_ground import services, state_with

    from localtc.ground.api import GroundState
    from localtc.ground.crew import RampCrew
    from localtc.ground import words
    from localtc.sim_api import OwnshipState, RampSpeech

    c = RampCrew(seed=1, language=code)
    base = {f: 0.0 for f in ("t", "lat", "lon", "alt_msl_ft", "alt_indicated_ft", "alt_agl_ft", "altimeter_inhg", "hdg_mag", "hdg_true", "ias_kt", "gs_kt", "vs_fpm", "com2_mhz")}
    c.own = OwnshipState(**base, on_ground=True, parking_brake=False, engine_running=False, squawk="1000", xpdr_mode="standby", com1_mhz=122.8)
    c.state = GroundState(connected=True, running=True, services=services())
    line = [o for o in c.do(__import__("localtc.ground.grammar", fromlist=["x"]).Ask("request", ("Water",)), 0.0) if isinstance(o, RampSpeech)][0]
    assert line.language == code and words.NAMES[code]["Water"] in line.text
    headset = [o for o in c.do(__import__("localtc.ground.grammar", fromlist=["x"]).Ask("push"), 1.0) if isinstance(o, RampSpeech)][0]
    assert headset.language == "" and "parking brake" in headset.text


# --- the cabin crew ----------------------------------------------------------------------------------------------------


CABIN: dict[str, dict[str, str]] = {
    "drink": {"de": "Kabine, einen Kaffee bitte", "fr": "Cabine, un café s'il vous plaît", "es": "Cabina, un café por favor", "it": "Cabina, un caffè per favore", "pt": "Cabine, um café por favor",
              "nl": "Cabine, een koffie alsjeblieft", "pl": "Kabina, kawę proszę", "sv": "Kabin, en kaffe tack", "ru": "Салон, кофе, пожалуйста", "zh": "客舱，来杯咖啡"},
    "prepare_landing": {"de": "Kabine, auf die Landung vorbereiten", "fr": "Cabine, préparez la cabine pour l'atterrissage", "es": "Cabina, prepara la cabina para el aterrizaje", "it": "Cabina, prepara la cabina per l'atterraggio",
                        "pt": "Cabine, prepara a cabine para a aterragem", "nl": "Cabine, maak de cabine klaar voor de landing", "pl": "Kabina, przygotuj kabinę do lądowania", "sv": "Kabin, förbered kabinen för landning",
                        "ru": "Салон, подготовьте салон к посадке", "zh": "客舱，准备着陆"},
    "count": {"de": "Kabine, wie viele Passagiere haben wir?", "fr": "Cabine, combien de passagers avons-nous ?", "es": "Cabina, ¿cuántos pasajeros llevamos?", "it": "Cabina, quanti passeggeri abbiamo?", "pt": "Cabine, quantos passageiros temos?",
              "nl": "Cabine, hoeveel passagiers hebben we?", "pl": "Kabina, ilu mamy pasażerów?", "sv": "Kabin, hur många passagerare har vi?", "ru": "Салон, сколько пассажиров?", "zh": "客舱，多少乘客？"},
    "ground_boarding": {"de": "Kabine, Boarding starten", "fr": "Cabine, commencez l'embarquement", "es": "Cabina, empieza el embarque", "it": "Cabina, inizia l'imbarco", "pt": "Cabine, começa o embarque",
                        "nl": "Cabine, begin met boarden", "pl": "Kabina, rozpocznij boarding", "sv": "Kabin, börja boarda", "ru": "Салон, начинайте посадку", "zh": "客舱，开始登机"},
    "turbulence": {"de": "Kabine, Turbulenzen voraus", "fr": "Cabine, turbulences devant", "es": "Cabina, turbulencias por delante", "it": "Cabina, turbolenze in arrivo", "pt": "Cabine, turbulência à frente",
                   "nl": "Cabine, turbulentie verwacht", "pl": "Kabina, turbulencje przed nami", "sv": "Kabin, turbulens framför oss", "ru": "Салон, впереди турбулентность", "zh": "客舱，前方颠簸"},
}


def _cabin(code):
    from test_cabin import facts

    from localtc.crew.cabin import CabinCrew, CabinSettings

    c = CabinCrew(CabinSettings(), lambda: facts(), seed=1)
    c.talks_in = code
    c.ground_call = lambda service, t: True
    return c


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
@pytest.mark.parametrize("key", sorted(CABIN))
def test_said_to_the_cabin_in_every_language(code, key):
    from localtc.crew import cabin_words
    from localtc.sim_api import CabinSpeech, IntercomHeard

    c = _cabin(code)
    text = CABIN[key][code]
    assert c.claims(text, 1.0)
    out = c.heard(IntercomHeard(t=1.0, text=text))
    line = [o for o in out if isinstance(o, CabinSpeech)][0]
    assert line.language == code and line.channel == "interphone"
    if key == "drink":  # (the drink in their language too)
        assert cabin_words.DRINKS[code]["coffee"] in line.text
    else:
        own = cabin_words.REPLIES[code][key].split("{")[0]
        assert line.text.startswith(own)


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
def test_every_cabin_answer_is_there_and_fills_in(code):
    from localtc.crew.cabin_lines import REPLIES
    from localtc.crew import cabin_words

    assert set(cabin_words.REPLIES[code]) == set(REPLIES)
    for key, line in cabin_words.REPLIES[code].items():
        assert line not in REPLIES[key]


def test_ramp_words_are_the_ramps_in_the_captains_language_too():
    c = _cabin("de")
    assert not c.claims("Rampe, Treibstoff bitte", 1.0)


@pytest.mark.parametrize("code, heard, english", [
    ("es", "sube a 10.000 pies.", "altitude 10000"), ("it", "Quota 10.000 piedi.", "altitude 10000"), ("pt", "altitude de 10.000 pés.", "altitude 10000"),
    ("fr", "altitude de 10 000 pieds.", "altitude 10000"), ("de", "Höhe 10.000 Fuß", "altitude 10000"), ("fr", "CAP270", "heading 270"), ("de", "Kurs 2, 7, 0", "heading 270"),
    ("es", "RUMBO 270", "heading 270"), ("de", "FL350", "altitude 35000"), ("fr", "sort le train.", "gear down"), ("ru", "Высота 10 000 футов", "altitude 10000"), ("zh", "高度10,000英尺", "altitude 10000"),
])
def test_numbers_as_whisper_writes_them(code, heard, english):
    assert foreign.gloss(heard, code) == english


def test_decimals_and_flaps_stay_what_they_are():
    from localtc.spoken_numbers import digits_as_written

    assert digits_as_written("29.92") == "29.92" and digits_as_written("A320") == "A320"
    assert digits_as_written("flaps 2, gear down") == "flaps 2, gear down"


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
def test_whisper_listens_for_the_crews_words_in_every_language(code):
    from localtc.stt.vocabulary import CREW_STYLE_IN, MAX_PROMPT_CHARS

    assert 100 < len(CREW_STYLE_IN[code]) <= MAX_PROMPT_CHARS


@pytest.mark.parametrize("code, said", [
    ("de", "Rampenfrequenz einstellen"), ("fr", "Règle la rampe"), ("es", "Sintoniza la rampa"), ("it", "Sintonizza la rampa"), ("pt", "Sintoniza a rampa"),
    ("nl", "Platformfrequentie"), ("pl", "Ustaw rampę"), ("sv", "Ställ in rampen"), ("ru", "Настрой перрон"), ("zh", "调机坪频率"),
])
def test_the_copilot_tunes_the_ramp_in_every_language(code, said):
    from localtc.crew.commands import parse

    assert foreign.gloss(said, code) == "tune ramp"
    assert [(c.action, c.value) for c in parse("tune ramp")] == [("com_active", "119.999")]


# --- the copilot, whole ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(foreign.WORDS))
def test_the_copilot_works_in_every_language_with_no_model(code):
    from test_crew import own, said, started

    from localtc.sim_api import AircraftSystems, CrewAction, IntercomHeard

    pm = started()
    pm._language = code
    pm.observe(own(2.0, fuel_lb=41300.0, fuel_flow_pph=2500.0))
    pm.observe(AircraftSystems(t=2.0, flaps_positions=4, engines_running=2))
    fuel = next(t for c, t, e in CASES if c == code and e == "how much fuel do we have?")
    answer = said(pm.observe(IntercomHeard(t=30.0, text=fuel)))
    assert answer and foreign.speaks(answer[0], code) and foreign.numbers(answer[0].replace(" ", " "))[0] == "41300"
    lights = next(t for c, t, e in CASES if c == code and e == "landing lights on")
    assert any(isinstance(o, CrewAction) for o in pm.observe(IntercomHeard(t=60.0, text=lights)))


def test_polish_is_spoken_by_its_own_pack():
    from localtc.tts.library import NATIVE

    assert "pl" not in NATIVE

