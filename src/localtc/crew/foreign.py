"""The captain talking to the copilot in another language ([crew] language): what's said put into the English the
copilot's grammar reads, and what the copilot says checked to be in that language.

``gloss`` turns the common things into English before anything else reads them: yes and no, the copilot's commands
(gear, flaps, lights, the autopilot, the checklists ...), and the questions answered from the sim (fuel, what ATC
said, the time to go, the weather at the destination). "Fahrwerk ausfahren" is "gear down", "oui, confirmé" is
"yes". It only answers when it understood every word of a command (a word it doesn't know and it says nothing, and
the language model reads the words as they were), so a command is never made of half a sentence.

``speaks`` tells whether a line is in a language, so that an English reply isn't read out by that language's voice;
``ACKS`` are the copilot's few short words, said in the language without a language model.
"""

import re
import unicodedata

# --- what each language says ------------------------------------------------------------------------------------------
# Concepts, each with its words in every language (accents don't matter: they're taken off before matching). English
# is in every language too: pilots who speak another language say "flaps", "gear", "autopilot" and "checklist".

ENGLISH = {
    "YES": "yes|yeah|yep|affirm|affirmative|correct|confirmed|confirm|go ahead|do it|ok|okay",
    "NO": "no|negative|nope",
    "CANCEL": "cancel|cancel that|disregard|forget it|stop",
    "CHECK": "check|checked",
    "GEAR": "gear|landing gear|the gear|wheels",
    "FLAPS": "flaps|flap",
    "LANDING_LIGHTS": "landing lights|landing light",
    "TAXI_LIGHTS": "taxi lights|taxi light",
    "STROBES": "strobes|strobe lights|strobe",
    "BEACON": "beacon|beacon light",
    "NAV_LIGHTS": "nav lights|navigation lights|nav",
    "LOGO_LIGHTS": "logo lights|logo light",
    "SEATBELT": "seatbelt sign|seatbelts sign|seat belt sign|seatbelts|seatbelt|seat belts",
    "AUTOPILOT": "autopilot|auto pilot|ap",
    "SPOILERS": "spoilers|speedbrakes|speed brakes|ground spoilers",
    "PARKING_BRAKE": "parking brake|park brake",
    "APU": "apu",
    "HEADING": "heading",
    "ALTITUDE": "altitude",
    "SPEED": "speed",
    "SQUAWK": "squawk|transponder",
    "CHECKLIST": "checklist|check list",
    "ON": "on|turn on|switch on",
    "OFF": "off|turn off|switch off",
    "UP": "up|retract|raise",
    "DOWN": "down|extend|lower|drop",
    "ARM": "arm|armed",
    "START": "start",
    "FULL": "full",
    "COMPLETE": "complete|completed|done|finished",
    "BEFORE_START": "before start",
    "AFTER_START": "after start",
    "BEFORE_TAKEOFF": "before takeoff|before take off|line up",
    "AFTER_TAKEOFF": "after takeoff|after take off",
    "DESCENT": "descent",
    "APPROACH": "approach",
    "LANDING": "landing",
    "AFTER_LANDING": "after landing",
    "SHUTDOWN": "shutdown|shut down|parking",
    "TAXI": "taxi",
    "FUEL": "fuel",
    "ATC": "atc|tower|ground|approach control|center|centre|controller",
    "SAID": "say|said",
    "WEATHER": "weather|metar",
    "DESTINATION": "destination",
    "HOW_LONG": "how long|when do we land|when will we land|time to go|eta",
    "FILLER": "please|the|a|and|then|now|set|put|turn|switch|give me|can you|could you|would you|let's|lets|run|do|go|"
              "us|for me|to|thanks|thank you|captain|okay then|alright|right|it|them|our|we",
}

WORDS: dict[str, dict[str, str]] = {
    "de": {
        "YES": "ja|jawohl|genau|richtig|bestatigt|bestatige|korrekt|mach das|mach es|positiv|jo",
        "NO": "nein|negativ|nee|nicht",
        "CANCEL": "abbrechen|vergiss es|vergiss das|lass das|lass es|stopp|halt",
        "CHECK": "gecheckt|gepruft|erledigt",
        "GEAR": "fahrwerk|das fahrwerk|fahrwerks|rader|die rader|rad",
        "FLAPS": "klappen|landeklappen|die klappen|die landeklappen",
        "LANDING_LIGHTS": "landescheinwerfer|landelichter|landelicht|die landescheinwerfer|die landelichter",
        "TAXI_LIGHTS": "rollscheinwerfer|taxilichter|taxi licht|taxilicht|rolllicht|rolllichter",
        "STROBES": "blitzlichter|blitzer|strobe lichter",
        "BEACON": "beacon licht|antikollisionslicht|rundumlicht|rundumleuchte",
        "NAV_LIGHTS": "navigationslichter|positionslichter|navlichter",
        "LOGO_LIGHTS": "logolicht|logolichter|logo licht",
        "SEATBELT": "anschnallzeichen|gurtzeichen|anschnallzeichen schild|die anschnallzeichen|das anschnallzeichen|anschnallen",
        "AUTOPILOT": "autopilot|den autopiloten|autopiloten|der autopilot",
        "SPOILERS": "spoiler|bremsklappen|die spoiler|die bremsklappen",
        "PARKING_BRAKE": "parkbremse|feststellbremse|die parkbremse",
        "HEADING": "kurs|steuerkurs",
        "ALTITUDE": "hohe|flughohe",
        "SPEED": "geschwindigkeit|fahrt",
        "SQUAWK": "transpondercode|transponder|squawk",
        "CHECKLIST": "checkliste|die checkliste|checklisten",
        "ON": "an|ein|einschalten|anschalten|anmachen|schalte an|mach an",
        "OFF": "aus|ausschalten|ausmachen|abschalten|losen|lose",
        "UP": "einfahren|hoch|rauf|rein",
        "DOWN": "ausfahren|runter|raus",
        "ARM": "scharf|scharf machen|scharfschalten|armieren|armed",
        "START": "starten|starte|anlassen|anwerfen",
        "FULL": "voll|ganz",
        "COMPLETE": "fertig|komplett|abgeschlossen|beendet",
        "LANDING+CHECKLIST": "landecheckliste|landungscheckliste",
        "APPROACH+CHECKLIST": "anflugcheckliste",
        "DESCENT+CHECKLIST": "sinkflugcheckliste",
        "TAXI+CHECKLIST": "rollcheckliste",
        "SHUTDOWN+CHECKLIST": "abstellcheckliste|parkcheckliste",
        "AFTER_LANDING+CHECKLIST": "nachlandecheckliste",
        "BEFORE_START": "vor dem start|vor dem anlassen|before start",
        "AFTER_START": "nach dem start|nach dem anlassen|after start",
        "BEFORE_TAKEOFF": "vor dem abflug|vor dem takeoff|vor dem abheben|before takeoff",
        "AFTER_TAKEOFF": "nach dem abflug|nach dem takeoff|nach dem abheben|after takeoff",
        "DESCENT": "sinkflug|descent",
        "APPROACH": "anflug|approach",
        "LANDING": "landung|landing|lande",
        "AFTER_LANDING": "nach der landung|after landing",
        "SHUTDOWN": "abstellen|shutdown|parken",
        "TAXI": "rollen|taxi",
        "FUEL": "treibstoff|sprit|kraftstoff|fuel|kerosin",
        "ATC": "flugsicherung|lotse|der lotse|die flugsicherung|turm|tower|atc|kontrolle",
        "SAID": "gesagt|sagen|meinte|wollte",
        "WEATHER": "wetter|das wetter|metar",
        "DESTINATION": "ziel|zielflughafen|am ziel",
        "HOW_LONG": "wie lange|wann landen wir|wann sind wir da|restflugzeit",
        "FILLER": "bitte|mal|mach|mache|lass|lasst|lass mal|die|der|das|den|dem|und|dann|jetzt|setzen|setze|setz|schalte|fahre|fahr|"
                  "kannst du|konntest du|wurdest du|lass uns|machen|bitte schon|danke|kapitan|ok|okay|in ordnung|fur|"
                  "auf|zu|uns|wir|sie|es|noch|gleich|schon|wieder|durchgehen|durch|ausfuhren",
    },
    "fr": {
        "YES": "oui|ouais|affirmatif|confirme|confirmé|correct|exact|vas y|fais le|d'accord|daccord",
        "NO": "non|negatif|pas",
        "CANCEL": "annule|annuler|laisse tomber|oublie|stop|arrete",
        "CHECK": "verifie|vérifié|fait",
        "GEAR": "train|train d'atterrissage|le train|le train d'atterrissage|train datterrissage|roues|les roues",
        "FLAPS": "volets|les volets|volet",
        "LANDING_LIGHTS": "phares d'atterrissage|feux d'atterrissage|phares datterrissage|feux datterrissage|les phares d'atterrissage|les feux d'atterrissage|phares",
        "TAXI_LIGHTS": "feux de roulage|phares de roulage|les feux de roulage",
        "STROBES": "strobes|feux stroboscopiques|flashs",
        "BEACON": "beacon|feu anticollision|feux anticollision|le beacon",
        "NAV_LIGHTS": "feux de navigation|feux de position|les feux de navigation",
        "LOGO_LIGHTS": "feux logo|eclairage logo",
        "SEATBELT": "consigne ceintures|consignes ceintures|les consignes ceintures|consignes lumineuses|signal ceintures|ceintures|les ceintures|attachez vos ceintures",
        "AUTOPILOT": "pilote automatique|le pilote automatique|autopilote",
        "SPOILERS": "spoilers|aerofreins|les spoilers|les aerofreins",
        "PARKING_BRAKE": "frein de parking|frein de stationnement|le frein de parking",
        "HEADING": "cap|le cap",
        "ALTITUDE": "altitude|l'altitude",
        "SPEED": "vitesse|la vitesse",
        "SQUAWK": "transpondeur|code transpondeur|squawk",
        "CHECKLIST": "checklist|check-list|liste de verification|la checklist|la check-list",
        "ON": "allume|allumer|active|activer|mets en marche|on",
        "OFF": "eteins|eteindre|coupe|couper|desactive|desactiver|off",
        "UP": "rentre|rentrer|remonte|monte|relever|releve",
        "DOWN": "sors|sortir|sortez|sorti|sortie|descends|descendre|baisse|baisser",
        "ARM": "arme|armer|armés|armes",
        "START": "demarre|demarrer|lance|lancer",
        "FULL": "plein|pleins|full|complet",
        "COMPLETE": "terminee|terminée|termine|fini|finie|complete|completée",
        "BEFORE_START": "avant mise en route|avant demarrage|before start",
        "AFTER_START": "apres mise en route|apres demarrage|after start",
        "BEFORE_TAKEOFF": "avant decollage|avant le decollage|before takeoff",
        "AFTER_TAKEOFF": "apres decollage|apres le decollage|after takeoff",
        "DESCENT": "descente|de descente",
        "APPROACH": "approche|d'approche|dapproche",
        "LANDING": "atterrissage|d'atterrissage|datterrissage",
        "AFTER_LANDING": "apres atterrissage|apres l'atterrissage|after landing",
        "SHUTDOWN": "arret|parking|arret moteurs|shutdown",
        "TAXI": "roulage|de roulage",
        "FUEL": "carburant|kerosene|fuel|essence",
        "ATC": "controle|le controle|controleur|le controleur|la tour|tour|atc|l'atc",
        "SAID": "dit|a dit|dire",
        "WEATHER": "meteo|la meteo|temps|metar",
        "DESTINATION": "destination|a destination|l'arrivee|arrivee",
        "HOW_LONG": "combien de temps|quand atterrissons nous|quand est ce qu'on atterrit|temps restant",
        "FILLER": "s'il te plait|s'il vous plait|stp|svp|le|la|les|l'|de|du|des|et|puis|maintenant|mets|mettre|"
                  "peux tu|pourrais tu|tu peux|on|nous|merci|commandant|ok|bon|alors|fais|faire|lance la|en|au|a",
    },
    "es": {
        "YES": "si|sí|afirmativo|confirmado|confirmo|correcto|claro|hazlo|dale|vale|de acuerdo",
        "NO": "no|negativo",
        "CANCEL": "cancela|cancelar|olvidalo|déjalo|dejalo|para|alto",
        "CHECK": "comprobado|chequeado|hecho|listo",
        "GEAR": "tren|tren de aterrizaje|el tren|el tren de aterrizaje|ruedas|las ruedas",
        "FLAPS": "flaps|los flaps|flap",
        "LANDING_LIGHTS": "luces de aterrizaje|las luces de aterrizaje|luz de aterrizaje",
        "TAXI_LIGHTS": "luces de rodaje|las luces de rodaje|luz de rodaje",
        "STROBES": "estrobos|luces estroboscopicas|strobes",
        "BEACON": "beacon|luz anticolision|baliza",
        "NAV_LIGHTS": "luces de navegacion|las luces de navegacion",
        "LOGO_LIGHTS": "luces del logo|luz del logo",
        "SEATBELT": "senal de cinturones|cinturones|los cinturones|aviso de cinturones|señal de cinturones",
        "AUTOPILOT": "piloto automatico|el piloto automatico|autopiloto",
        "SPOILERS": "spoilers|aerofrenos|los spoilers|los aerofrenos",
        "PARKING_BRAKE": "freno de estacionamiento|freno de parking|el freno de estacionamiento|freno de mano",
        "HEADING": "rumbo|el rumbo",
        "ALTITUDE": "altitud|la altitud|altura",
        "SPEED": "velocidad|la velocidad",
        "SQUAWK": "transpondedor|codigo|squawk",
        "CHECKLIST": "checklist|lista de chequeo|lista de comprobacion|la checklist|la lista",
        "ON": "enciende|encender|activa|activar|prende|prender|conecta|conectar|on",
        "OFF": "apaga|apagar|desactiva|desactivar|desconecta|desconectar|off",
        "UP": "sube|subir|retrae|retraer|recoge|recoger|arriba",
        "DOWN": "baja|bajar|extiende|extender|saca|sacar|abajo",
        "ARM": "arma|armar|armados",
        "START": "arranca|arrancar|enciende la|pon en marcha",
        "FULL": "completos|full|todo",
        "COMPLETE": "completa|completada|terminada|terminado|lista",
        "BEFORE_START": "antes del arranque|antes de arrancar|before start",
        "AFTER_START": "despues del arranque|despues de arrancar|after start",
        "BEFORE_TAKEOFF": "antes del despegue|antes de despegar|before takeoff",
        "AFTER_TAKEOFF": "despues del despegue|despues de despegar|after takeoff",
        "DESCENT": "descenso|de descenso",
        "APPROACH": "aproximacion|de aproximacion",
        "LANDING": "aterrizaje|de aterrizaje",
        "AFTER_LANDING": "despues del aterrizaje|after landing",
        "SHUTDOWN": "apagado|parada|estacionamiento|shutdown",
        "TAXI": "rodaje|de rodaje",
        "FUEL": "combustible|fuel|queroseno",
        "ATC": "control|el control|controlador|el controlador|la torre|torre|atc",
        "SAID": "dijo|dicho|decir|ha dicho",
        "WEATHER": "tiempo|el tiempo|meteo|clima|metar",
        "DESTINATION": "destino|en destino|el destino",
        "HOW_LONG": "cuanto falta|cuanto tiempo|cuando aterrizamos|tiempo restante",
        "FILLER": "por favor|el|la|los|las|y|luego|ahora|pon|poner|puedes|podrias|nos|gracias|capitan|vale|bueno|"
                  "de|del|al|a|haz|hacer|vamos|con|en",
    },
    "it": {
        "YES": "si|sì|affermativo|confermato|confermo|corretto|esatto|fallo|vai|va bene|ok",
        "NO": "no|negativo",
        "CANCEL": "annulla|lascia perdere|lascia stare|stop|fermati",
        "CHECK": "controllato|verificato|fatto",
        "GEAR": "carrello|il carrello|carrello di atterraggio|ruote|le ruote",
        "FLAPS": "flap|flaps|i flap|ipersostentatori",
        "LANDING_LIGHTS": "luci di atterraggio|le luci di atterraggio|fari di atterraggio",
        "TAXI_LIGHTS": "luci di rullaggio|le luci di rullaggio",
        "STROBES": "strobo|luci strobo|strobes",
        "BEACON": "beacon|luce anticollisione",
        "NAV_LIGHTS": "luci di navigazione|le luci di navigazione",
        "LOGO_LIGHTS": "luci del logo|luce del logo",
        "SEATBELT": "segnale cinture|cinture|le cinture|allacciate le cinture",
        "AUTOPILOT": "pilota automatico|il pilota automatico|autopilota",
        "SPOILERS": "spoiler|diruttori|aerofreni|gli spoiler",
        "PARKING_BRAKE": "freno di parcheggio|freno di stazionamento|il freno di parcheggio",
        "HEADING": "prua|direzione|heading",
        "ALTITUDE": "altitudine|quota|la quota",
        "SPEED": "velocita|la velocita",
        "SQUAWK": "transponder|codice|squawk",
        "CHECKLIST": "checklist|lista di controllo|la checklist",
        "ON": "accendi|accendere|attiva|attivare|inserisci|inserire|on",
        "OFF": "spegni|spegnere|disattiva|disattivare|disinserisci|disinserire|stacca|staccare|off",
        "UP": "su|retrai|retrarre|alza|alzare|ritira|ritirare",
        "DOWN": "giu|estendi|estendere|abbassa|abbassare|fuori",
        "ARM": "arma|armare|armati",
        "START": "avvia|avviare|accendi l",
        "FULL": "tutti|full|completi",
        "COMPLETE": "completata|completa|finita|terminata",
        "BEFORE_START": "prima dell'avviamento|prima dell avviamento|before start",
        "AFTER_START": "dopo l'avviamento|dopo l avviamento|after start",
        "BEFORE_TAKEOFF": "prima del decollo|before takeoff",
        "AFTER_TAKEOFF": "dopo il decollo|after takeoff",
        "DESCENT": "discesa|di discesa",
        "APPROACH": "avvicinamento|di avvicinamento",
        "LANDING": "atterraggio|di atterraggio",
        "AFTER_LANDING": "dopo l'atterraggio|dopo l atterraggio|after landing",
        "SHUTDOWN": "spegnimento|parcheggio|shutdown",
        "TAXI": "rullaggio|di rullaggio",
        "FUEL": "carburante|benzina|fuel|cherosene",
        "ATC": "controllo|il controllo|controllore|la torre|torre|atc",
        "SAID": "detto|ha detto|dire",
        "WEATHER": "meteo|tempo|il tempo|metar",
        "DESTINATION": "destinazione|a destinazione",
        "HOW_LONG": "quanto manca|quanto tempo|quando atterriamo|tempo rimanente",
        "FILLER": "per favore|il|lo|la|i|gli|le|e|poi|ora|adesso|metti|mettere|puoi|potresti|ci|grazie|comandante|"
                  "bene|allora|di|del|della|fai|fare|facciamo|a|al",
    },
    "pt": {
        "YES": "sim|afirmativo|confirmado|confirmo|correto|certo|faz isso|pode|ok|está bem|esta bem",
        "NO": "nao|não|negativo",
        "CANCEL": "cancela|cancelar|esquece|deixa|para|pare",
        "CHECK": "verificado|feito|checado",
        "GEAR": "trem|trem de aterragem|trem de pouso|o trem|rodas|as rodas",
        "FLAPS": "flaps|os flaps|flap",
        "LANDING_LIGHTS": "luzes de aterragem|luzes de pouso|as luzes de aterragem|as luzes de pouso",
        "TAXI_LIGHTS": "luzes de rolagem|luzes de taxi|as luzes de rolagem",
        "STROBES": "strobes|luzes estroboscopicas|estrobos",
        "BEACON": "beacon|luz anticolisao",
        "NAV_LIGHTS": "luzes de navegacao|as luzes de navegacao",
        "LOGO_LIGHTS": "luzes do logo|luz do logo",
        "SEATBELT": "sinal de cintos|cintos|os cintos|aviso de cintos",
        "AUTOPILOT": "piloto automatico|o piloto automatico|autopiloto",
        "SPOILERS": "spoilers|freios aerodinamicos|os spoilers",
        "PARKING_BRAKE": "travao de estacionamento|freio de estacionamento|travão de parque|freio de mao",
        "HEADING": "rumo|proa|o rumo",
        "ALTITUDE": "altitude|a altitude",
        "SPEED": "velocidade|a velocidade",
        "SQUAWK": "transponder|codigo|squawk",
        "CHECKLIST": "checklist|lista de verificacao|a checklist|a lista",
        "ON": "liga|ligar|acende|acender|ativa|ativar|on",
        "OFF": "desliga|desligar|apaga|apagar|desativa|desativar|off",
        "UP": "sobe|subir|recolhe|recolher|em cima",
        "DOWN": "baixa|baixar|desce|descer|estende|estender|em baixo|ca fora|fora",
        "ARM": "arma|armar|armados",
        "START": "arranca|arrancar|liga a|ligar a",
        "FULL": "todos|full|completos",
        "COMPLETE": "completa|concluida|concluída|terminada|feita",
        "BEFORE_START": "antes do arranque|antes da partida|before start",
        "AFTER_START": "depois do arranque|depois da partida|after start",
        "BEFORE_TAKEOFF": "antes da descolagem|antes da decolagem|before takeoff",
        "AFTER_TAKEOFF": "depois da descolagem|depois da decolagem|after takeoff",
        "DESCENT": "descida|de descida",
        "APPROACH": "aproximacao|de aproximacao",
        "LANDING": "aterragem|pouso|de aterragem|de pouso",
        "AFTER_LANDING": "depois da aterragem|depois do pouso|after landing",
        "SHUTDOWN": "corte|estacionamento|shutdown",
        "TAXI": "rolagem|de rolagem|taxi",
        "FUEL": "combustivel|combustível|fuel|querosene",
        "ATC": "controlo|controle|o controlo|o controle|controlador|a torre|torre|atc",
        "SAID": "disse|dito|dizer",
        "WEATHER": "tempo|meteorologia|o tempo|metar",
        "DESTINATION": "destino|no destino",
        "HOW_LONG": "quanto falta|quanto tempo|quando aterramos|quando pousamos",
        "FILLER": "por favor|o|a|os|as|e|depois|agora|poe|põe|pode|podes|nos|obrigado|comandante|bem|entao|de|"
                  "do|da|faz|fazer|vamos|em|no|na",
    },
    "nl": {
        "YES": "ja|jawel|bevestigd|bevestig|klopt|correct|doe maar|doe het|prima|oke",
        "NO": "nee|negatief|neen",
        "CANCEL": "annuleer|annuleren|laat maar|vergeet het|stop",
        "CHECK": "gecontroleerd|gedaan|check",
        "GEAR": "landingsgestel|het landingsgestel|gear|wielen|de wielen",
        "FLAPS": "kleppen|flaps|de kleppen|de flaps",
        "LANDING_LIGHTS": "landingslichten|de landingslichten|landingslicht",
        "TAXI_LIGHTS": "taxilichten|de taxilichten|taxilicht",
        "STROBES": "strobes|flitslichten",
        "BEACON": "beacon|anticollisielicht|zwaailicht",
        "NAV_LIGHTS": "navigatielichten|de navigatielichten|positielichten",
        "LOGO_LIGHTS": "logolichten|logolicht",
        "SEATBELT": "gordelteken|riemen vast|gordels|de gordels|stoelriemen",
        "AUTOPILOT": "automatische piloot|de automatische piloot|autopiloot|de autopiloot",
        "SPOILERS": "spoilers|luchtremmen|de spoilers",
        "PARKING_BRAKE": "parkeerrem|de parkeerrem|handrem",
        "HEADING": "koers|de koers",
        "ALTITUDE": "hoogte|de hoogte",
        "SPEED": "snelheid|de snelheid",
        "SQUAWK": "transponder|transpondercode|squawk",
        "CHECKLIST": "checklist|controlelijst|de checklist",
        "ON": "aan|inschakelen|aanzetten|zet aan",
        "OFF": "uit|uitschakelen|uitzetten|zet uit",
        "UP": "op|omhoog|intrekken|in",
        "DOWN": "neer|omlaag|uitklappen|uitschuiven|naar beneden",
        "ARM": "armen|bewapenen|gearmd",
        "START": "starten|start de",
        "FULL": "vol|volledig",
        "COMPLETE": "klaar|compleet|afgerond|voltooid",
        "LANDING+CHECKLIST": "landingschecklist",
        "APPROACH+CHECKLIST": "naderingschecklist",
        "BEFORE_START": "voor de start|voor het starten|before start",
        "AFTER_START": "na de start|na het starten|after start",
        "BEFORE_TAKEOFF": "voor vertrek|voor het opstijgen|before takeoff",
        "AFTER_TAKEOFF": "na vertrek|na het opstijgen|after takeoff",
        "DESCENT": "daling|dalen",
        "APPROACH": "nadering|naderings",
        "LANDING": "landing|landings",
        "AFTER_LANDING": "na de landing|after landing",
        "SHUTDOWN": "uitzetten motoren|parkeren|shutdown",
        "TAXI": "taxien|taxi",
        "FUEL": "brandstof|kerosine|fuel",
        "ATC": "de verkeersleiding|verkeersleiding|toren|de toren|atc|luchtverkeersleider",
        "SAID": "gezegd|zei|zeggen",
        "WEATHER": "weer|het weer|metar",
        "DESTINATION": "bestemming|op de bestemming",
        "HOW_LONG": "hoe lang nog|hoelang nog|wanneer landen we|resterende tijd",
        "FILLER": "alsjeblieft|alstublieft|graag|de|het|en|dan|nu|zet|doe|kun je|kan je|kunt u|we|ons|"
                  "bedankt|dank je|gezagvoerder|oke|goed|nou|maar|even|naar|met",
    },
    "pl": {
        "YES": "tak|potwierdzam|potwierdzone|zgadza sie|dobrze|zrob to|jasne|ok|okej",
        "NO": "nie|negatyw|negatywnie",
        "CANCEL": "anuluj|odwolaj|zostaw|nie rob|stop",
        "CHECK": "sprawdzone|zrobione|gotowe",
        "GEAR": "podwozie|podwozia|kola",
        "FLAPS": "klapy|klap|flapy",
        "LANDING_LIGHTS": "swiatla do ladowania|swiatla ladowania|reflektory do ladowania",
        "TAXI_LIGHTS": "swiatla do kolowania|swiatla kolowania",
        "STROBES": "stroboskopy|strobe",
        "BEACON": "beacon|swiatlo antykolizyjne",
        "NAV_LIGHTS": "swiatla nawigacyjne",
        "LOGO_LIGHTS": "swiatla logo|podswietlenie logo",
        "SEATBELT": "znak zapiecia pasow|pasy|zapiac pasy|zapiecie pasow",
        "AUTOPILOT": "autopilot|autopilota",
        "SPOILERS": "spojlery|spoilery|hamulce aerodynamiczne",
        "PARKING_BRAKE": "hamulec postojowy|hamulec parkingowy|reczny",
        "HEADING": "kurs|kursu",
        "ALTITUDE": "wysokosc|wysokosci",
        "SPEED": "predkosc|predkosci",
        "SQUAWK": "transponder|kod transpondera|squawk",
        "CHECKLIST": "checklista|checkliste|liste kontrolna|lista kontrolna|checklist",
        "ON": "wlacz|wlaczyc|zapal|zapalic|on",
        "OFF": "wylacz|wylaczyc|zgas|zgasic|off",
        "UP": "schowaj|schowac|w gore|podnies|podniesc",
        "DOWN": "wypusc|wypuscic|opusc|opuscic|w dol|wysun|wysunac",
        "ARM": "uzbroj|uzbroic|uzbrojone",
        "START": "uruchom|uruchomic|odpal|odpalic",
        "FULL": "pelne|full|calkowicie",
        "COMPLETE": "zakonczona|ukonczona|gotowa|skonczona",
        "BEFORE_START": "przed uruchomieniem|before start",
        "AFTER_START": "po uruchomieniu|after start",
        "BEFORE_TAKEOFF": "przed startem|before takeoff",
        "AFTER_TAKEOFF": "po starcie|after takeoff",
        "DESCENT": "zejscie|zejscia|znizanie",
        "APPROACH": "podejscie|podejscia",
        "LANDING": "ladowanie|ladowania",
        "AFTER_LANDING": "po ladowaniu|after landing",
        "SHUTDOWN": "wylaczenie|postoj|shutdown",
        "TAXI": "kolowanie|kolowania",
        "FUEL": "paliwo|paliwa|fuel",
        "ATC": "kontrola|kontroler|wieza|atc|kontroli",
        "SAID": "powiedzial|powiedziala|mowil|mowila",
        "WEATHER": "pogoda|pogode|pogody|metar",
        "DESTINATION": "cel|celu|lotnisko docelowe|na miejscu",
        "HOW_LONG": "ile jeszcze|jak dlugo|kiedy ladujemy|pozostaly czas",
        "FILLER": "prosze|i|a|potem|teraz|ustaw|daj|mozesz|czy mozesz|nam|dzieki|dziekuje|kapitanie|dobra|no|"
                  "to|zrob|przeprowadz|na|do|z",
    },
    "sv": {
        "YES": "ja|japp|bekraftat|bekraftar|stammer|korrekt|gor det|kor|okej",
        "NO": "nej|negativt|nae",
        "CANCEL": "avbryt|glom det|strunta i det|stopp",
        "CHECK": "kontrollerat|klart|check",
        "GEAR": "stall|stallet|landstall|landstallet|hjul|hjulen",
        "FLAPS": "klaff|klaffar|klaffarna|flaps",
        "LANDING_LIGHTS": "landningsljus|landningsljusen",
        "TAXI_LIGHTS": "taxiljus|taxiljusen",
        "STROBES": "strobe|strobeljus|blixtljus",
        "BEACON": "beacon|antikollisionsljus",
        "NAV_LIGHTS": "navigationsljus|navigationsljusen|positionsljus",
        "LOGO_LIGHTS": "logoljus|logoljuset",
        "SEATBELT": "baltesskylt|baltesskylten|baltena|baltesskyltarna",
        "AUTOPILOT": "autopilot|autopiloten",
        "SPOILERS": "spoilers|luftbromsar|spoilrarna",
        "PARKING_BRAKE": "parkeringsbroms|parkeringsbromsen",
        "HEADING": "kurs|kursen",
        "ALTITUDE": "hojd|hojden",
        "SPEED": "fart|hastighet|farten",
        "SQUAWK": "transponder|transponderkod|squawk",
        "CHECKLIST": "checklista|checklistan|checklist",
        "ON": "pa|tand|sla pa|satt pa|koppla in",
        "OFF": "av|slack|sla av|stang av|koppla ur|koppla bort",
        "UP": "upp|in|fall in",
        "DOWN": "ner|ned|ut|fall ut",
        "ARM": "arma|armera|armerade",
        "START": "starta|starta upp",
        "FULL": "full|fullt|alla",
        "COMPLETE": "klar|fardig|slutford",
        "LANDING+CHECKLIST": "landningschecklista|landningschecklistan",
        "APPROACH+CHECKLIST": "inflygningschecklista|inflygningschecklistan",
        "BEFORE_START": "fore start|before start",
        "AFTER_START": "efter start|after start",
        "BEFORE_TAKEOFF": "fore avgang|fore start bana|before takeoff",
        "AFTER_TAKEOFF": "efter avgang|after takeoff",
        "DESCENT": "nedstigning|nedstigningen",
        "APPROACH": "inflygning|inflygningen",
        "LANDING": "landning|landningen",
        "AFTER_LANDING": "efter landning|after landing",
        "SHUTDOWN": "avstangning|parkering|shutdown",
        "TAXI": "taxning|taxi",
        "FUEL": "bransle|branslet|fuel",
        "ATC": "flygledningen|flygledning|flygledaren|tornet|atc",
        "SAID": "sa|sagt|sade",
        "WEATHER": "vader|vadret|metar",
        "DESTINATION": "destination|destinationen|framme",
        "HOW_LONG": "hur lange|nar landar vi|hur lang tid",
        "FILLER": "tack|snalla|och|sedan|nu|satt|kan du|vi|oss|kapten|bra|da|den|det|gor|kor|till|med",
    },
    "ru": {
        "YES": "да|подтверждаю|подтверждено|верно|так точно|правильно|давай|делай|хорошо|ок|окей",
        "NO": "нет|отрицательно|не надо",
        "CANCEL": "отмена|отменить|отставить|забудь|стоп",
        "CHECK": "проверено|сделано|готово",
        "GEAR": "шасси|колеса|колесо",
        "FLAPS": "закрылки|закрылков|закрылок",
        "LANDING_LIGHTS": "посадочные фары|посадочные огни|фары",
        "TAXI_LIGHTS": "рулежные фары|рулёжные фары|рулежные огни",
        "STROBES": "стробы|стробоскопы",
        "BEACON": "маяк|проблесковый маяк|бикон",
        "NAV_LIGHTS": "аэронавигационные огни|навигационные огни|бано",
        "LOGO_LIGHTS": "подсветку логотипа|подсветка логотипа",
        "SEATBELT": "табло пристегнуть ремни|ремни|табло ремней",
        "AUTOPILOT": "автопилот|автопилота",
        "SPOILERS": "спойлеры|интерцепторы|спойлеров",
        "PARKING_BRAKE": "стояночный тормоз|стояночный",
        "HEADING": "курс",
        "ALTITUDE": "высоту|высота",
        "SPEED": "скорость",
        "SQUAWK": "транспондер|код ответчика|ответчик|сквок",
        "CHECKLIST": "чеклист|чек-лист|карту контрольных проверок|карта контрольных проверок|карту|карта|карты",
        "ON": "включи|включить|включаем|вкл",
        "OFF": "выключи|выключить|выключаем|выкл|отключи|отключить|отключаем",
        "UP": "убери|убрать|убираем|убираю|убраны|убрано|вверх",
        "DOWN": "выпусти|выпустить|выпускаем|выпускаю|выпускай|выпущено|выпущены|вниз",
        "ARM": "взвести|взведи|армировать",
        "START": "запусти|запустить|запускаем",
        "FULL": "полностью|полные",
        "COMPLETE": "выполнена|завершена|закончена",
        "BEFORE_START": "перед запуском",
        "AFTER_START": "после запуска",
        "BEFORE_TAKEOFF": "перед взлетом|перед взлётом|на исполнительном",
        "AFTER_TAKEOFF": "после взлета|после взлёта",
        "DESCENT": "снижение|снижения|перед снижением",
        "APPROACH": "заход|захода|перед заходом",
        "LANDING": "посадка|посадки|перед посадкой",
        "AFTER_LANDING": "после посадки",
        "SHUTDOWN": "выключение|стоянка|на стоянке",
        "TAXI": "руление|руления|перед рулением",
        "FUEL": "топливо|топлива|керосин",
        "ATC": "диспетчер|диспетчера|вышка|атс|контроль",
        "SAID": "сказал|сказала|говорил|передал",
        "WEATHER": "погода|погоду|погоды|метар",
        "DESTINATION": "в пункте назначения|пункт назначения|аэродром назначения|назначения",
        "HOW_LONG": "сколько осталось|сколько еще|сколько ещё|когда садимся|когда посадка",
        "FILLER": "пожалуйста|и|потом|теперь|сейчас|поставь|установи|можешь|давай|нам|спасибо|командир|ну|"
                  "ладно|выполни|выполняем|проведи|на|в",
    },
    "zh": {
        "YES": "是的|是|对|确认|好的|好|没错|可以|行|执行",
        "NO": "不是|不|否|不用|不要",
        "CANCEL": "取消|算了|停止|别做",
        "CHECK": "检查完毕|已检查|完成了",
        "GEAR": "起落架|轮子",
        "FLAPS": "襟翼",
        "LANDING_LIGHTS": "着陆灯|落地灯",
        "TAXI_LIGHTS": "滑行灯",
        "STROBES": "频闪灯|闪光灯",
        "BEACON": "防撞灯|信标灯",
        "NAV_LIGHTS": "航行灯|导航灯",
        "LOGO_LIGHTS": "标志灯|logo灯",
        "SEATBELT": "安全带指示灯|安全带灯|安全带",
        "AUTOPILOT": "自动驾驶仪|自动驾驶",
        "SPOILERS": "扰流板|减速板",
        "PARKING_BRAKE": "停留刹车|停机刹车|手刹",
        "HEADING": "航向",
        "ALTITUDE": "高度",
        "SPEED": "速度",
        "SQUAWK": "应答机|应答机编码",
        "CHECKLIST": "检查单|清单",
        "ON": "打开|开启|接通|开",
        "OFF": "关闭|关掉|断开|关",
        "UP": "收起|收上|收",
        "DOWN": "放下来|放下|放出|放",
        "ARM": "预位|待命",
        "START": "启动|起动",
        "FULL": "全|全放",
        "COMPLETE": "完成|做完",
        "BEFORE_START": "启动前",
        "AFTER_START": "启动后",
        "BEFORE_TAKEOFF": "起飞前",
        "AFTER_TAKEOFF": "起飞后",
        "DESCENT": "下降",
        "APPROACH": "进近",
        "LANDING": "着陆|落地",
        "AFTER_LANDING": "着陆后|落地后",
        "SHUTDOWN": "关车|停机",
        "TAXI": "滑行",
        "FUEL": "燃油|油量|燃料",
        "ATC": "管制|塔台|管制员|空管",
        "SAID": "说了|说|讲",
        "WEATHER": "天气",
        "DESTINATION": "目的地",
        "HOW_LONG": "还要多久|多久|什么时候落地|什么时候到",
        "FILLER": "请|吧|一下|的|了|把|我们|帮我|你|可以吗|谢谢|机长|现在|然后|和|再|执行一下|做|呢|吗",
    },
}

DIGITS: dict[str, dict[str, int]] = {
    "en": {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "niner": 9, "ten": 10},
    "de": {"null": 0, "eins": 1, "ein": 1, "eine": 1, "zwei": 2, "zwo": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6,
           "sieben": 7, "acht": 8, "neun": 9, "zehn": 10},
    "fr": {"zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
           "huit": 8, "neuf": 9, "dix": 10},
    "es": {"cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
           "ocho": 8, "nueve": 9, "diez": 10},
    "it": {"zero": 0, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7,
           "otto": 8, "nove": 9, "dieci": 10},
    "pt": {"zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
           "sete": 7, "oito": 8, "nove": 9, "dez": 10},
    "nl": {"nul": 0, "een": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
           "negen": 9, "tien": 10},
    "pl": {"zero": 0, "jeden": 1, "jedna": 1, "jedynka": 1, "dwa": 2, "dwie": 2, "dwojka": 2, "trzy": 3, "trojka": 3,
           "cztery": 4, "piec": 5, "szesc": 6, "siedem": 7, "osiem": 8, "dziewiec": 9, "dziesiec": 10},
    "sv": {"noll": 0, "ett": 1, "en": 1, "tva": 2, "tre": 3, "fyra": 4, "fem": 5, "sex": 6, "sju": 7, "atta": 8,
           "nio": 9, "tio": 10},
    "ru": {"ноль": 0, "один": 1, "одна": 1, "раз": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
           "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10},
    "zh": {"零": 0, "〇": 0, "一": 1, "幺": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
           "九": 9, "十": 10},
}

# Words that join two commands ("gear down and flaps one").
JOINERS = {"en": "and|then", "de": "und|dann", "fr": "et|puis", "es": "y|luego", "it": "e|poi", "pt": "e|depois",
           "nl": "en|dan", "pl": "i|potem", "sv": "och|sen|sedan", "ru": "и|потом|затем", "zh": "和|然后|再"}

QUESTION = {"en": "what|how|when|where|is|are|do|did|any", "de": "was|wie|wann|wo|wieviel|gibt|hat|haben|ist",
            "fr": "quoi|que|qu|quel|quelle|combien|comment|quand|ou|est ce|a t il|qu'est ce",
            "es": "que|qué|cual|cuanto|cuánto|como|cuando|donde|hay", "it": "cosa|che|quanto|come|quando|dove|qual",
            "pt": "o que|que|quanto|como|quando|onde|qual", "nl": "wat|hoe|hoeveel|wanneer|waar|welk",
            "pl": "co|ile|jak|kiedy|gdzie|jaki|jaka", "sv": "vad|hur|nar|var|vilken|hur mycket",
            "ru": "что|сколько|как|когда|где|какая|какой", "zh": "什么|多少|怎么|几|吗|呢|如何"}

# The copilot's short words, said in the language with no language model.
ACKS: dict[str, dict[str, str]] = {
    "de": {"Check.": "Check.", "Copy.": "Verstanden.", "Roger.": "Verstanden.", "Say again?": "Bitte nochmal?",
           "Can't tell that from here.": "Das kann ich von hier nicht sehen.", "Will do.": "Mach ich."},
    "fr": {"Check.": "Check.", "Copy.": "Compris.", "Roger.": "Bien reçu.", "Say again?": "Tu peux répéter ?",
           "Can't tell that from here.": "Je ne peux pas le voir d'ici.", "Will do.": "Je m'en occupe."},
    "es": {"Check.": "Check.", "Copy.": "Copiado.", "Roger.": "Recibido.", "Say again?": "¿Puedes repetir?",
           "Can't tell that from here.": "No lo puedo ver desde aquí.", "Will do.": "Hecho."},
    "it": {"Check.": "Check.", "Copy.": "Ricevuto.", "Roger.": "Ricevuto.", "Say again?": "Puoi ripetere?",
           "Can't tell that from here.": "Da qui non lo vedo.", "Will do.": "Ci penso io."},
    "pt": {"Check.": "Check.", "Copy.": "Copiado.", "Roger.": "Recebido.", "Say again?": "Podes repetir?",
           "Can't tell that from here.": "Daqui não consigo ver isso.", "Will do.": "Fica comigo."},
    "nl": {"Check.": "Check.", "Copy.": "Begrepen.", "Roger.": "Ontvangen.", "Say again?": "Kun je dat herhalen?",
           "Can't tell that from here.": "Dat kan ik van hieruit niet zien.", "Will do.": "Doe ik."},
    "pl": {"Check.": "Check.", "Copy.": "Zrozumiałem.", "Roger.": "Przyjąłem.", "Say again?": "Możesz powtórzyć?",
           "Can't tell that from here.": "Stąd tego nie widzę.", "Will do.": "Zrobi się."},
    "sv": {"Check.": "Check.", "Copy.": "Uppfattat.", "Roger.": "Uppfattat.", "Say again?": "Kan du upprepa?",
           "Can't tell that from here.": "Det kan jag inte se härifrån.", "Will do.": "Jag fixar det."},
    "ru": {"Check.": "Check.", "Copy.": "Понял.", "Roger.": "Принял.", "Say again?": "Повтори, пожалуйста?",
           "Can't tell that from here.": "Отсюда мне этого не видно.", "Will do.": "Сделаю."},
    "zh": {"Check.": "Check.", "Copy.": "收到。", "Roger.": "收到。", "Say again?": "请再说一遍？",
           "Can't tell that from here.": "我这边看不到。", "Will do.": "好的，我来。"},
}

# The copilot's set lines, each with what's in it kept as said (aviation English: "flaps 1", "gear down").
TEMPLATES: tuple[tuple[str, dict[str, str]], ...] = (
    (r"^I couldn't get (?P<x>.+) from here\. Can you do it\?$", {
        "de": "Ich konnte {x} von hier nicht setzen. Kannst du das machen?",
        "fr": "Je n'ai pas réussi à mettre {x} d'ici. Tu peux le faire ?",
        "es": "No he podido poner {x} desde aquí. ¿Puedes hacerlo tú?",
        "it": "Non sono riuscito a mettere {x} da qui. Puoi farlo tu?",
        "pt": "Não consegui pôr {x} daqui. Podes fazer tu?",
        "nl": "Ik kreeg {x} hier niet gezet. Kun jij het doen?",
        "pl": "Nie udało mi się ustawić {x} stąd. Możesz to zrobić?",
        "sv": "Jag fick inte till {x} härifrån. Kan du göra det?",
        "ru": "У меня не получилось поставить {x} отсюда. Сделаешь сам?",
        "zh": "我这边没能设置{x}。你能操作一下吗？"}),
    (r"^I couldn't set (?P<x>.+)\. Can you set it\?$", {
        "de": "Ich konnte {x} nicht einstellen. Kannst du das einstellen?",
        "fr": "Je n'ai pas pu régler {x}. Tu peux le régler ?",
        "es": "No he podido ajustar {x}. ¿Puedes ajustarlo tú?",
        "it": "Non sono riuscito a impostare {x}. Puoi impostarlo tu?",
        "pt": "Não consegui ajustar {x}. Podes ajustar tu?",
        "nl": "Ik kon {x} niet instellen. Kun jij het instellen?",
        "pl": "Nie mogłem ustawić {x}. Możesz to ustawić?",
        "sv": "Jag kunde inte ställa in {x}. Kan du ställa in det?",
        "ru": "Не смог выставить {x}. Выставишь сам?",
        "zh": "我没能设置{x}。你能设置一下吗？"}),
    (r"^Unable, speed (?P<v>\d+), gear limit (?P<l>\d+)\.$", {
        "de": "Geht nicht, Geschwindigkeit {v}, Fahrwerksgrenze {l}.",
        "fr": "Impossible, vitesse {v}, limite du train {l}.",
        "es": "Imposible, velocidad {v}, límite del tren {l}.",
        "it": "Impossibile, velocità {v}, limite del carrello {l}.",
        "pt": "Impossível, velocidade {v}, limite do trem {l}.",
        "nl": "Kan niet, snelheid {v}, limiet landingsgestel {l}.",
        "pl": "Nie mogę, prędkość {v}, limit podwozia {l}.",
        "sv": "Går inte, fart {v}, gräns för stället {l}.",
        "ru": "Не могу, скорость {v}, ограничение по шасси {l}.",
        "zh": "不行，速度{v}，起落架限速{l}。"}),
    (r"^Unable, speed (?P<v>\d+), flaps (?P<f>\S+) limit (?P<l>\d+)\.$", {
        "de": "Geht nicht, Geschwindigkeit {v}, Grenze für Klappen {f} {l}.",
        "fr": "Impossible, vitesse {v}, limite volets {f} {l}.",
        "es": "Imposible, velocidad {v}, límite de flaps {f} {l}.",
        "it": "Impossibile, velocità {v}, limite flap {f} {l}.",
        "pt": "Impossível, velocidade {v}, limite de flaps {f} {l}.",
        "nl": "Kan niet, snelheid {v}, limiet flaps {f} {l}.",
        "pl": "Nie mogę, prędkość {v}, limit klap {f} {l}.",
        "sv": "Går inte, fart {v}, gräns för klaff {f} {l}.",
        "ru": "Не могу, скорость {v}, ограничение закрылков {f} {l}.",
        "zh": "不行，速度{v}，襟翼{f}限速{l}。"}),
    (r"^(?P<x>.{1,80}), confirm\?$", {
        "de": "{x}, bestätigen?", "fr": "{x}, tu confirmes ?", "es": "{x}, ¿confirmas?", "it": "{x}, confermi?",
        "pt": "{x}, confirmas?", "nl": "{x}, bevestig je?", "pl": "{x}, potwierdzasz?", "sv": "{x}, bekräftar du?",
        "ru": "{x}, подтверждаешь?", "zh": "{x}，确认吗？"}),
    (r"^That sounded like a radio call\. I haven't sent it; want me to\?$", {
        "de": "Das klang nach einem Funkspruch. Ich habe ihn nicht gesendet. Soll ich?",
        "fr": "Ça ressemblait à un message radio. Je ne l'ai pas envoyé. Je le fais ?",
        "es": "Eso sonaba a una llamada de radio. No la he enviado. ¿La hago?",
        "it": "Sembrava una chiamata radio. Non l'ho trasmessa. La faccio io?",
        "pt": "Isso pareceu uma chamada rádio. Não a enviei. Queres que envie?",
        "nl": "Dat klonk als een radiobericht. Ik heb het niet verstuurd. Zal ik?",
        "pl": "To brzmiało jak wywołanie radiowe. Nie nadałem go. Mam nadać?",
        "sv": "Det lät som ett radioanrop. Jag har inte skickat det. Ska jag?",
        "ru": "Похоже на радиообмен. Я его не передавал. Передать?",
        "zh": "这听起来像是无线电呼叫。我还没发出去，要我发吗？"}),
    (r"^I don't have that\.$", {
        "de": "Das habe ich nicht.", "fr": "Je n'ai pas cette information.", "es": "No tengo ese dato.",
        "it": "Non ce l'ho.", "pt": "Não tenho essa informação.", "nl": "Dat heb ik niet.", "pl": "Nie mam tego.",
        "sv": "Det har jag inte.", "ru": "У меня этого нет.", "zh": "我没有这个信息。"}),
    (r"^I'm not sure\. Say again\?$", {
        "de": "Bin mir nicht sicher. Nochmal?", "fr": "Je ne suis pas sûr. Tu peux répéter ?",
        "es": "No estoy seguro. ¿Repites?", "it": "Non sono sicuro. Ripeti?", "pt": "Não tenho a certeza. Repetes?",
        "nl": "Ik weet het niet zeker. Herhaal?", "pl": "Nie jestem pewien. Powtórzysz?", "sv": "Jag är osäker. Upprepa?",
        "ru": "Не уверен. Повтори?", "zh": "我不太确定。请再说一遍？"}),
)


def templated(text: str, code: str) -> str | None:
    """One of the copilot's set lines in language ``code``, what's in it kept as said; None if it isn't one."""
    for pattern, said in TEMPLATES:
        if code in said and (m := re.match(pattern, text)):
            return said[code].format(**m.groupdict())
    return None


# "Seattle Approach said: ...": what ATC said stays as said (English), the rest in the language.
SAID = {"de": "{who} hat gesagt: {words}", "fr": "{who} a dit : {words}", "es": "{who} dijo: {words}",
        "it": "{who} ha detto: {words}", "pt": "{who} disse: {words}", "nl": "{who} zei: {words}",
        "pl": "{who} powiedział: {words}", "sv": "{who} sa: {words}", "ru": "{who} сказал: {words}",
        "zh": "{who}说：{words}"}

# Units the copilot says, and what a translation mustn't turn them into (pounds aren't kilograms).
_METRES = r"\bm[eè]t(?:er|re|ro|r)|\bметр|米"  # (not "altimeter")
UNITS = (("pound", r"\bkilo|\bkg\b|\bкг\b|килограм|公斤|千克"), ("feet", _METRES), ("foot", _METRES),
         ("knot", r"km/?h|километр|公里"), ("mile", r"\bkilom[eè]t|километр|公里"))

# Told once when the captain calls ATC in this language: ATC only works in English.
ENGLISH_ONLY = {
    "de": "Die Flugsicherung spricht nur Englisch: ruf sie bitte auf Englisch.",
    "fr": "Le contrôle ne parle qu'anglais : appelle-les en anglais.",
    "es": "El control solo habla inglés: llámalos en inglés.",
    "it": "Il controllo parla solo inglese: chiamali in inglese.",
    "pt": "O controlo só fala inglês: chama-os em inglês.",
    "nl": "De verkeersleiding spreekt alleen Engels: roep ze in het Engels op.",
    "pl": "Kontrola mówi tylko po angielsku: wołaj ich po angielsku.",
    "sv": "Flygledningen pratar bara engelska: anropa dem på engelska.",
    "ru": "Диспетчер говорит только по-английски: вызывай его по-английски.",
    "zh": "管制只讲英语：请用英语呼叫他们。",
}

# The everyday small words of each language, to tell what a line is in.
STOPWORDS = {
    "en": "the|and|is|are|we|you|it|to|of|that|this|with|have|for|on|at|be|was|our|a|an|in|about|just|it's|we're|"
          "i'm|there|what|so|but|not|bit|over|hours|minutes|said|should|would|will|can|let|me|my|your|of|up|down",
    "de": "der|die|das|und|ist|sind|wir|du|ich|es|nicht|mit|ein|eine|haben|auf|zu|noch|auch|schon|bitte|sehr|"
          "gut|alles|aber|dein|mein|wie|was|ja|nein|von|im|den|dem|des|sie|hat|wird|kann|mal|etwas|ein bisschen",
    "fr": "le|la|les|et|est|sont|nous|tu|je|il|ne|pas|avec|un|une|des|du|de|au|pour|on|ça|c'est|très|bien|tout|"
          "mais|ton|mon|que|qui|oui|non|dans|sur|a|à|vous|avons|reste|encore",
    "es": "el|la|los|las|y|es|son|nosotros|tú|yo|no|con|un|una|de|del|al|para|que|muy|bien|todo|pero|tu|mi|sí|"
          "en|por|hay|está|estamos|tenemos|vamos|unos|algo",
    "it": "il|lo|la|i|gli|le|e|è|sono|noi|tu|io|non|con|un|una|di|del|della|per|che|molto|bene|tutto|ma|tuo|mio|"
          "sì|in|su|abbiamo|siamo|c'è|ancora",
    "pt": "o|a|os|as|e|é|são|nós|tu|eu|não|com|um|uma|de|do|da|para|que|muito|bem|tudo|mas|teu|meu|sim|em|no|"
          "na|temos|estamos|está|ainda",
    "nl": "de|het|een|en|is|zijn|we|wij|je|jij|ik|niet|met|van|voor|op|dat|die|heel|goed|alles|maar|jouw|mijn|"
          "ja|nee|in|aan|hebben|nog|er|ook",
    "pl": "i|jest|są|my|ty|ja|nie|z|na|w|do|że|to|bardzo|dobrze|wszystko|ale|twój|mój|tak|mamy|jesteśmy|się|jeszcze",
    "sv": "och|är|vi|du|jag|det|inte|med|en|ett|för|på|att|som|mycket|bra|allt|men|din|min|ja|nej|i|har|vi har|"
          "också|lite",
}

# The words of a radio call in each language, to tell a call made in it from one in English.
RADIO_WORDS = {
    "de": "bereit|piste|abflug|rollen|rollfreigabe|freigabe|erbitte|erbitten|steigen|sinken|zum|nach|halten|vor|"
          "verlassen|anflug|landung|frequenz|wechsle|ruf|rufe|turm|vorfeld|startbahn|landebahn|kurs|hohe",
    "fr": "pret|piste|depart|demande|demandons|roulage|autorisation|pour|vers|montee|descente|maintenir|maintiens|"
          "atterrissage|decollage|frequence|tour|sol|approche|point|attente|alignement|cap",
    "es": "listo|listos|pista|despegue|solicito|solicitamos|rodaje|autorizacion|para|hacia|ascenso|descenso|"
          "mantener|aterrizaje|frecuencia|torre|rumbo|punto|espera",
    "it": "pronto|pronti|pista|decollo|richiedo|chiediamo|rullaggio|autorizzazione|per|verso|salita|discesa|"
          "mantenere|atterraggio|frequenza|torre|prua|attesa",
    "pt": "pronto|prontos|pista|descolagem|decolagem|solicito|rolagem|autorizacao|para|subida|descida|manter|"
          "aterragem|pouso|frequencia|torre|rumo|espera",
    "nl": "klaar|baan|vertrek|verzoek|verzoeken|taxien|klaring|naar|klimmen|dalen|aanhouden|landing|frequentie|toren",
    "pl": "gotowy|gotowi|pas|start|prosze|prosimy|kolowanie|zezwolenie|do|wznoszenie|znizanie|utrzymac|ladowanie|"
          "czestotliwosc|wieza|kurs",
    "sv": "klar|klara|bana|avgang|begar|taxning|klarering|till|stig|sjunk|behall|landning|frekvens|tornet",
}

_JOIN_CACHE: dict[str, list[tuple[str, str]]] = {}


def _plain(text: str) -> str:
    """Lowercase, accents off (not in Russian or Chinese, where the marks are letters), apostrophes and dashes as
    spaces."""
    text = text.lower().replace("ё", "е")
    if not re.search(r"[Ѐ-ӿ一-鿿]", text):
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        text = text.replace("ł", "l")
    text = re.sub(r"[’'`´\-]", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s.,;]", " ", text)).strip()


def _phrases(code: str) -> list[tuple[str, str]]:
    """Every (words, concept) of the language and English, the longest first (so "landing lights" is one thing)."""
    if code not in _JOIN_CACHE:
        found: dict[str, str] = {}
        for table in (ENGLISH, WORDS.get(code, {})):
            for concept, words in table.items():
                for w in words.split("|"):
                    w = _plain(w)
                    if w and w not in found:
                        found[w] = concept
        _JOIN_CACHE[code] = sorted(found.items(), key=lambda kv: -len(kv[0]))
    return _JOIN_CACHE[code]


def _tokens(clause: str, code: str) -> list[str] | None:
    """The clause as concepts ("§GEAR"), numbers and words not known; None if empty."""
    cjk = code == "zh"
    text = re.sub(r"[.,;:](?=\s|$)", " ", f" {clause} ")  # (a sentence's own stops; not "29.92")
    for words, concept in _phrases(code):
        pattern = re.escape(words) if cjk else rf"(?<=\s){re.escape(words)}(?=\s)"
        text = re.sub(pattern, f" §{concept} ", text)
    digits = {**DIGITS["en"], **DIGITS.get(code, {})}
    out: list[str] = []
    for tok in text.split():
        if tok.startswith("§"):
            out.extend(f"§{c}" for c in tok[1:].split("+"))
        elif re.fullmatch(r"\d+(?:[.,]\d+)?", tok):
            out.append(tok.replace(",", "."))
        elif tok in digits:
            out.append(str(digits[tok]))
        elif cjk and all(ch in digits for ch in tok):
            out.extend(str(digits[ch]) for ch in tok)
        elif cjk:
            out.extend(ch if ch not in digits else str(digits[ch]) for ch in tok)
        else:
            out.append(tok)
    return out or None


def _number(tokens: list[str]) -> str:
    """The numbers in a clause as one value: digits said one by one are one number ("zwei sieben null" is 270)."""
    nums = [t for t in tokens if re.fullmatch(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return ""
    if len(nums) > 1 and all(len(n) == 1 for n in nums):
        return "".join(nums)
    return nums[0]


LIGHTS = {"LANDING_LIGHTS": "landing lights", "TAXI_LIGHTS": "taxi lights", "STROBES": "strobes", "BEACON": "beacon",
          "NAV_LIGHTS": "nav lights", "LOGO_LIGHTS": "logo lights"}
CHECKLISTS = {"BEFORE_START": "before start", "AFTER_START": "after start", "BEFORE_TAKEOFF": "before takeoff",
              "AFTER_TAKEOFF": "after takeoff", "DESCENT": "descent", "APPROACH": "approach", "LANDING": "landing",
              "AFTER_LANDING": "after landing", "SHUTDOWN": "shutdown", "TAXI": "taxi"}
SWITCHES = {"ON": "on", "OFF": "off"}


def _command(concepts: set[str], number: str) -> str | None:
    """One clause's concepts as the English command they make, or None."""
    on = "ON" in concepts or "START" in concepts
    off = "OFF" in concepts or "CANCEL" in concepts
    state = "on" if on and not off else "off" if off and not on else ""
    if "CHECKLIST" in concepts:
        names = [CHECKLISTS[c] for c in concepts if c in CHECKLISTS]
        if "COMPLETE" in concepts and not names:
            return "checklist complete"
        if len(names) == 1:
            return f"run the {names[0]} checklist"
        if len(names) == 2 and "after landing" in names:
            return "run the after landing checklist"
        return None
    if "GEAR" in concepts:
        up, down = "UP" in concepts, "DOWN" in concepts or "OFF" in concepts and "ON" not in concepts  # nl "uit": out
        return "gear up" if up and not down else "gear down" if down and not up else None
    if "FLAPS" in concepts:
        if number and number.isdigit() and int(number) <= 40:
            return f"flaps {number}"
        if "FULL" in concepts:
            return "flaps full"
        if "UP" in concepts and "DOWN" not in concepts:
            return "flaps up"
        return None
    lights = [LIGHTS[c] for c in concepts if c in LIGHTS]
    if len(lights) == 1 and state:
        return f"{lights[0]} {state}"
    if "SEATBELT" in concepts and state:
        return f"seatbelt sign {state}"
    if "AUTOPILOT" in concepts and state:
        return f"autopilot {state}"
    if "PARKING_BRAKE" in concepts and state:
        return f"parking brake {state}"
    if "APU" in concepts:
        return "start the apu" if on and not off else "apu off" if off and not on else None
    if "SPOILERS" in concepts:
        if "ARM" in concepts:
            return "arm the spoilers"
        if "DOWN" in concepts or "ON" in concepts:
            return "spoilers extend"
        if "UP" in concepts or "OFF" in concepts:
            return "speedbrakes retract"
        return None
    for concept, word in (("HEADING", "heading"), ("ALTITUDE", "altitude"), ("SPEED", "speed"), ("SQUAWK", "squawk")):
        if concept in concepts and number:
            return f"{word} {number}"
    return None


def _question(concepts: set[str], text: str, code: str) -> str | None:
    """The questions answered from the sim, in English."""
    asked = "?" in text or "？" in text or re.search(
        rf"(?:^|\s)(?:{'|'.join(map(re.escape, (QUESTION.get(code, '') + '|' + QUESTION['en']).split('|')))})(?:\s|$)",
        f" {_plain(text)} ") is not None or (code == "zh" and re.search(QUESTION["zh"], text) is not None)
    if not asked:
        return None
    if "FUEL" in concepts:
        return "how much fuel do we have?"
    if "ATC" in concepts and "SAID" in concepts:
        return "what did atc say?"
    if "WEATHER" in concepts:
        return "what is the weather at destination?" if "DESTINATION" in concepts else "how's the weather at destination?"
    if "HOW_LONG" in concepts:
        return "how long to destination?"
    return None


def gloss(text: str, code: str) -> str | None:
    """``text`` (said in language ``code``) as the English the copilot reads, when it's one of the common things and
    every word of it is understood; None otherwise (the language model reads it as said)."""
    if not text.strip() or code in ("", "en") or code not in WORDS:
        return None
    plain = _plain(text) if code != "zh" else re.sub(r"[\s，。！？、,.!?]+", " ", text.lower()).strip()
    joiners = "|".join(re.escape(_plain(j) if code != "zh" else j) for j in JOINERS[code].split("|") + JOINERS["en"].split("|"))
    split = r"\s*(?:[,;]|(?<=\s)(?:%s)(?=\s))\s*" % joiners if code != "zh" else r"\s*(?:[,;，]|%s)\s*" % joiners
    clauses = [c.strip(" .") for c in re.split(split, f" {plain} ") if c.strip(" .")]
    every: set[str] = set()
    commands: list[str] = []
    answer = ""
    for clause in clauses:
        tokens = _tokens(clause, code) or []
        concepts = {t[1:] for t in tokens if t.startswith("§")} - {"FILLER"}
        every |= concepts
        unknown = [t for t in tokens if not t.startswith("§") and not re.fullmatch(r"\d+(?:\.\d+)?", t)]
        if concepts <= {"YES", "CHECK"} and concepts and not unknown and not commands:
            answer = answer or ("yes" if "YES" in concepts else "check")
            continue
        if concepts == {"NO"} and not unknown:
            answer = answer or "no"
            continue
        if concepts == {"CANCEL"} and not unknown:
            answer = answer or "cancel that"
            continue
        made = _command(concepts, _number(tokens)) if not unknown else None
        if made is None:
            commands = []
            break
        commands.append(made)
    else:
        if commands:
            return ", ".join(([answer] if answer in ("yes", "no") else []) + commands)
        if answer:
            return answer
    return _question(every, text, code)


# English words that aren't the small ones, but that a line left in English has (aviation English that pilots keep in
# every language, "flaps", "gear", "autopilot", "Captain", isn't here).
EN_CONTENT = ("feet|foot|knots|knot|over|ground|busy|quite|speed|managed|indicated|nothing|still|right|good|fine|thanks|"
              "sounds|about|minutes|hours|pounds|ahead|behind|weather|fuel|burn|level|climbing|descending|turning|"
              "everything|looks|smooth|bumpy|ride|sure|maybe|think|know|yeah|really|already|left|again|from|here")


# English words that are words of these languages too ("over" in Dutch, "was" in German).
SHARED = {"de": "was|so|am|bin|hat|also|fast|still|ruhig", "nl": "over|was|we|is|op|hoe|still", "sv": "over|still|from",
          "pl": "on|no|to", "it": "come|sono|fine|ore", "fr": "sur|plus|fine", "es": "sur|fin", "pt": "sur"}


def _english_words(words: list[str], own: set[str], code: str = "") -> int:
    """The English words in ``words``: not the short ones ("a", "in", "is" are words of many languages) nor those of
    the language's own."""
    eng = set(STOPWORDS["en"].split("|")) | set(EN_CONTENT.split("|"))
    own = own | set(SHARED.get(code, "").split("|"))
    return sum(len(w) > 2 and w in eng and w not in own for w in words)


def speaks(text: str, code: str) -> bool:
    """Whether ``text`` is in language ``code``, not English: its script with no English words left in it (Russian,
    Mandarin), or more of its small words than English words. A line of no words either way (a number, "Check.")
    counts as in it. ``en_strict``: plain English (no Cyrillic or Chinese in it)."""
    if code == "en_strict":
        return re.search(r"[\u0400-\u04ff\u4e00-\u9fff]", text) is None and re.search(r"[A-Za-z]", text) is not None
    if code in ("", "en"):
        return True
    latin = [w.lower() for w in re.findall(r"(?<![\w])[A-Za-z]{2,}(?:'[A-Za-z]+)?(?![\w])", text)]  # (not "A320")
    if code in ("zh", "ru"):
        script = r"[\u4e00-\u9fff]" if code == "zh" else r"[\u0400-\u04ff]"
        return re.search(script, text) is not None and _english_words(latin, set()) == 0
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text.lower())
    own = set(STOPWORDS.get(code, "").split("|"))
    mine = sum(w in own for w in words)
    english = _english_words(words, own, code)
    return english == 0 or mine > english


NAMES = {"GEAR": "gear", "FLAPS": "flaps", "SEATBELT": "seatbelt sign", "AUTOPILOT": "autopilot", "SPOILERS": "spoilers",
         "PARKING_BRAKE": "parking brake", "APU": "apu", "HEADING": "heading", "ALTITUDE": "altitude", "SPEED": "speed",
         "SQUAWK": "squawk", "CHECKLIST": "checklist", **LIGHTS}


def named(text: str, code: str) -> str:
    """The English names of the controls ``text`` names in language ``code`` ("Lass die Räder runter": "gear"), for
    the checks that a command was asked for by name."""
    if code in ("", "en") or code not in WORDS:
        return ""
    plain = _plain(text) if code != "zh" else text
    concepts = {t[1:] for t in (_tokens(plain, code) or []) if t.startswith("§")}
    return " ".join(sorted({NAMES[c] for c in concepts if c in NAMES}))


def claims(text: str, code: str) -> str:
    """What a reply in language ``code`` says of the controls, as English ("Ich fahre das Fahrwerk aus": "gear down"),
    for the checks that the copilot doesn't say it did what it didn't. "" when it says nothing of them."""
    if code in ("", "en") or code not in WORDS:
        return ""
    out = []
    for sentence in re.split(r"[.!?;。！？；，、]+", text):  # (Chinese numbers run on between commas: "280节，180节")
        plain = _plain(sentence) if code != "zh" else sentence
        tokens = _tokens(plain, code) or []
        concepts = {t[1:] for t in tokens if t.startswith("§")}
        if "NO" in concepts:
            continue  # "not yet": no claim
        if "CHECKLIST" in concepts and "COMPLETE" in concepts:
            out.append("checklist complete")
        elif (made := _command(concepts, _number(tokens))) is not None and not made.startswith("run the"):
            out.append(made.replace("start the apu", "apu started"))
    return ". ".join(out)


def localized_numbers(text: str) -> str:
    """Numbers as English writes them, for the checks: "12.000" and "12 000" are 12,000, "29,92" is 29.92."""
    text = re.sub(r"(?<=\d)[.\s\u00a0\u202f](?=\d{3}\b)", ",", text)
    return re.sub(r"(?<=\d),(?=\d{1,2}\b)", ".", text)


_ZH_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0, "两": 2}
_ZH_PLACES = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def zh_numbers(text: str) -> list[str]:
    """The numbers written in Chinese characters, as digits: read digit by digit ("二八零": 280) or by place
    ("一万二千": 12000, "二十五": 25)."""
    out = []
    for run in re.findall(r"[零〇一二两三四五六七八九十百千万]+", text):
        if not any(c in _ZH_PLACES for c in run):
            if len(run) > 1 or run not in "一两":  # (a lone 一 is "a", not a number)
                out.append("".join(str(_ZH_DIGITS[c]) for c in run))
            continue
        total = section = digit = 0
        for c in run:
            if c in _ZH_DIGITS:
                digit = _ZH_DIGITS[c]
            elif c == "万":
                total, section, digit = (total + section + digit) * 10000, 0, 0
            else:
                section += (digit or 1) * _ZH_PLACES[c]
                digit = 0
        out.append(str(total + section + digit))
    return out


def mostly(text: str, code: str) -> bool:
    """Whether ``text`` is mostly in language ``code``, some English left in it or not: for the voice it's read in
    ("12,000 feet, Captain." isn't, "Сегодня уже quite busy." is)."""
    if code in ("zh", "ru"):
        return re.search(r"[\u4e00-\u9fff]" if code == "zh" else r"[\u0400-\u04ff]", text) is not None
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text.lower())
    own = set(STOPWORDS.get(code, "").split("|"))
    mine = sum(w in own for w in words)
    return mine > 0 and mine >= _english_words(words, own, code)


def native_numbers(text: str, code: str) -> list[str]:
    """The numbers said as words in language ``code`` ("Klappen drei": 3), as digits."""
    plain = _plain(text) if code != "zh" else text
    return [t for t in (_tokens(plain, code) or []) if re.fullmatch(r"\d+", t)]


def called_in(text: str, code: str) -> bool:
    """Whether a radio call was clearly made in language ``code``, not English (its script, or two or more of its own
    small words and more of them than English's)."""
    if code in ("zh", "ru"):
        return speaks(text, code)
    words = [_plain(w) for w in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text.lower())]
    eng = set(STOPWORDS["en"].split("|")) | {"tower", "ground", "approach", "runway", "ready", "departure", "request",
                                             "taxi", "clearance", "climb", "descend", "maintain", "heading", "with"}
    own = {_plain(w) for w in STOPWORDS.get(code, "").split("|") + RADIO_WORDS.get(code, "").split("|")
           + WORDS.get(code, {}).get("FILLER", "").split("|")} - eng
    mine = sum(w in own and w not in eng for w in words)
    return mine >= 2 and mine > sum(w in eng and w not in own for w in words)


THOUSAND = {"de": r"tausend", "fr": r"\bmille\b", "es": r"\bmil\b", "it": r"\bmila\b|\bmille\b", "pt": r"\bmil\b",
            "nl": r"duizend", "pl": r"\btysi[aąeę]c", "sv": r"tusen", "ru": r"тысяч"}


def thousands_in_words(text: str, code: str) -> bool:
    """A number of thousands said in words ("zwölftausend Fuß", "десять тысяч"): the copilot's numbers are digits, so
    they can be checked against the facts (Mandarin's are read: ``zh_numbers``)."""
    return code in THOUSAND and re.search(THOUSAND[code], text.lower()) is not None


def metric(text: str) -> bool:
    """Whether ``text`` gives metres, kilograms or kilometres: the copilot's numbers are all feet, pounds, knots and
    miles, so a reply that has them changed a unit."""
    return any(re.search(wrong, text.lower()) for _, wrong in UNITS)


def units_kept(english: str, said: str) -> bool:
    """A translation keeps the copilot's units: pounds aren't said as kilograms, feet as metres."""
    return not any(unit in english.lower() and re.search(wrong, said.lower()) for unit, wrong in UNITS)


def numbers(text: str) -> list[str]:
    """The numbers in a line as digits ("41,300" and "41.300" are both 41300), for checking a translation."""
    return [re.sub(r"[.,\s ](?=\d{3}\b)", "", n) for n in re.findall(r"\d[\d.,  ]*\d|\d", text)]
