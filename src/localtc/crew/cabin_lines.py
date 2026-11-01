"""The cabin crew's words: each announcement and each call to the flight deck, in the manner of the crew member
leading the cabin ([cabin] crew), and the announcements in the languages of the airlines that make them in more than
English.

Each line is a template: ``{airline}``, ``{flight}`` (the flight number in words), ``{dest}`` (the destination's city),
``{dest_about}`` (the city with a word about it), ``{time}`` (the flight time in words; ``{ltime}`` in the
announcement's language), ``{captain}`` ("Captain Reyes", or "the captain"; ``{Captain}`` at the start of a sentence),
``{name}`` (the crew member),
``{program}`` (the loyalty programme), ``{alliance}``, ``{pax}`` (passengers on board), ``{local}`` (the local time),
``{weather}`` (the temperature and the sky), ``{exits}`` (how many exits and where), ``{drink}``, ``{minutes}``,
``{message}``, ``{greeting}`` ("Good morning"). A template naming one that isn't known for this flight isn't used
(another wording is, or the line goes without that sentence: ``cabin.fill``).

Each crew member's manner (``PEOPLE``) chooses among a line's wordings: polished (by the book), cheerful (a joke in the
safety demonstration), warm (looking after everyone), brisk (what's needed and no more), storyteller (the destination,
the flight, the aircraft). A manner without wordings of its own for a line says it the polished way.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Person:
    id: str
    name: str  # their name on an English-speaking airline (elsewhere a name from the airline's country)
    sex: str
    manner: str
    brief: str  # for the settings' list
    sample: str  # a line of theirs, for the settings' preview


PEOPLE: tuple[Person, ...] = (
    Person("claire", "Claire", "F", "polished", "Poised and by the book: every announcement just so",
           "Ladies and gentlemen, welcome aboard. Please make sure your seatbelt is fastened and your bags are stowed."),
    Person("jamie", "Jamie", "M", "cheerful", "Upbeat, with a joke or two in the safety demonstration",
           "Folks, if you've flown before, you know the drill. If you haven't, this is the exciting part."),
    Person("grace", "Grace", "F", "warm", "Caring and reassuring: keeps an eye on everyone, you included",
           "If there's anything at all you need today, just press the call button and one of us will be right there."),
    Person("daniel", "Daniel", "M", "brisk", "Short and efficient: says what's needed and no more",
           "Seatbelts fastened, bags stowed, phones in flight mode. Thank you."),
    Person("sophie", "Sophie", "F", "storyteller", "Chatty: tells you about where you're going and the flight on the way",
           "Fun fact for the curious: the wings of this aircraft flex by more than a metre in flight."),
)
BY_ID = {p.id: p for p in PEOPLE}
MANNERS = ("polished", "cheerful", "warm", "brisk", "storyteller")


# key -> manner -> wordings. Every key has "polished".
LINES: dict[str, dict[str, tuple[str, ...]]] = {
    "boarding": {
        "polished": ("{greeting}, ladies and gentlemen, and welcome aboard {airline} flight {flight} to {dest}. As you "
                     "find your seat, please place your bags in the overhead lockers or under the seat in front of you, "
                     "and keep the aisle clear so everyone can board. Thank you.",),
        "cheerful": ("{greeting}, everybody, and welcome aboard {airline} flight {flight} to {dest}! If {dest} isn't "
                     "where you're headed today, now's a great time to let us know. Bags in the overhead lockers, "
                     "wheels first please, and smaller items under the seat in front of you.",),
        "warm": ("{greeting}, and a very warm welcome aboard {airline} flight {flight} to {dest}. Take your time finding "
                 "your seat. If you need a hand with your bags, just ask one of us; we're happy to help.",),
        "brisk": ("{greeting}. Welcome aboard flight {flight} to {dest}. Bags in the lockers or under the seat in front, "
                  "and please keep the aisle clear.",),
    },
    "welcome": {
        "polished": ("Ladies and gentlemen, on behalf of {captain} and the whole crew, welcome aboard {airline} "
                     "flight {flight} to {dest}. Our flight time today is {time}. {members}My name is {name}, and I'm "
                     "your cabin manager. Please make sure your seatbelt is fastened, your seat back is upright and your "
                     "tray table is stowed. We'll be on our way shortly.",),
        "cheerful": ("Hello again, everyone! On behalf of {captain} and the rest of this fine crew, welcome "
                     "aboard {airline} flight {flight} to {dest}. We'll have you there in about {time}, give or take a "
                     "tailwind. {members}I'm {name}, I'm running the cabin today, and I promise the coffee is better "
                     "than the jokes.",),
        "warm": ("Ladies and gentlemen, welcome aboard {airline} flight {flight} to {dest}. {Captain} expects a "
                 "flight time of {time}. {members}My name is {name}, and the whole crew is here to make your journey "
                 "as comfortable as we can. Please don't hesitate to call us if you need anything at all.",),
        "brisk": ("Welcome aboard {airline} flight {flight} to {dest}. Flight time {time}. {members}I'm {name}, your "
                  "cabin manager. Seatbelts fastened, please.",),
        "storyteller": ("Ladies and gentlemen, welcome aboard {airline} flight {flight}, taking you to {dest_about}. "
                        "{Captain} tells us our flight time will be {time}. {members}I'm {name}, your cabin "
                        "manager, and I'll keep you posted on where we are along the way.",),
    },
    "members": {  # inside the welcome: the loyalty programme and the alliance
        "polished": ("A special welcome to our {program} members, and thank you for your loyalty. ",
                     "To our {program} members, thank you for flying with us again. "),
        "cheerful": ("And to our {program} members: welcome back, it's great to see you again! ",),
        "brisk": ("Thank you to our {program} members. ",),
        "storyteller": ("A special welcome to our {program} members{alliance_part}. ",),
    },
    "safety": {
        "polished": ("Ladies and gentlemen, please direct your attention to the cabin crew for the safety "
                     "demonstration, even if you fly with us often. To fasten your seatbelt, insert the metal tip into "
                     "the buckle and pull the strap tight; to release it, lift the buckle. This aircraft has {exits}. "
                     "Please take a moment to find the exit nearest you; it may be behind you. In an emergency, lights "
                     "along the floor will lead you to an exit. Should the cabin lose pressure, oxygen masks will drop "
                     "from above. Pull the mask towards you, place it over your nose and mouth, and breathe normally. "
                     "Put your own mask on before helping others. Your life vest is under your seat; only inflate it as "
                     "you leave the aircraft. Smoking, including electronic cigarettes, is not allowed anywhere on board. "
                     "The safety card in the seat pocket has more. Thank you for your attention.",),
        "cheerful": ("Okay folks, it's showtime. Even if you've flown more than we have, please give us a minute; we "
                     "rehearsed. Your seatbelt works just like the one in your car, except this one doesn't come with "
                     "a car. Metal tip into the buckle, pull it tight, lift the buckle to let go. This aircraft has "
                     "{exits}. Have a look for the one nearest you; it might be behind you, and it's the one time on "
                     "this flight that looking backwards gets you ahead. If the cabin loses pressure, masks will drop "
                     "from above. Pull one down, place it over your nose and mouth, and breathe normally. If you're "
                     "travelling with a child, or someone acting like one, put your own mask on first. Your life vest "
                     "is under your seat; only inflate it once you're outside. And smoking, including vaping, isn't "
                     "allowed anywhere on board, not even in the bathrooms. Especially not in the bathrooms. Thank you!",),
        "warm": ("Ladies and gentlemen, for your safety, please give us a few moments for our safety demonstration, even "
                 "if you've seen it before. Your seatbelt fastens by inserting the metal tip into the buckle and "
                 "tightening the strap; lift the buckle to release it. This aircraft has {exits}. Please find the one "
                 "nearest you now, and remember it may be behind you. Lighting along the floor will guide you there if "
                 "it's ever needed. If the cabin loses pressure, an oxygen mask will drop down in front of you; pull it "
                 "towards you, place it over your nose and mouth, and breathe normally. Please fit your own mask before "
                 "helping your children or anyone else. Your life vest is under your seat; inflate it only as you leave "
                 "the aircraft. Smoking and vaping aren't allowed on board. Thank you, and we hope you'll relax and "
                 "enjoy the flight.",),
        "brisk": ("Your attention for the safety demonstration, please. Seatbelt: tip into the buckle, pull tight, lift "
                  "to release. This aircraft has {exits}; find the nearest one, it may be behind you. Floor lighting "
                  "leads to the exits. If the cabin loses pressure, masks drop from above: pull down, over nose and "
                  "mouth, breathe normally, your own first. Life vest under the seat, inflate only outside. No smoking "
                  "or vaping anywhere. More on the card in your seat pocket. Thank you.",),
    },
    "secure_takeoff": {
        "polished": ("Flight deck, cabin's secure for takeoff.", "Captain, it's {name}. The cabin is secure for takeoff."),
        "cheerful": ("Hi, it's {name}! Cabin's all secure for takeoff, everyone's buckled in.",),
        "warm": ("Captain, it's {name}. The cabin's secure for takeoff. Have a good one up there.",),
        "brisk": ("Cabin secure.",),
    },
    "night": {
        "polished": ("Ladies and gentlemen, we'll be dimming the cabin lights for departure. If you'd like to read, "
                     "your reading light is in the panel above you.",),
        "warm": ("Ladies and gentlemen, we're dimming the cabin lights for our departure, so your eyes adjust to the "
                 "dark outside. Your reading light is just above you if you need it.",),
        "brisk": ("We're dimming the cabin lights for departure. Reading lights are above you.",),
    },
    "seatbelt_off": {
        "polished": ("Ladies and gentlemen, the captain has switched off the seatbelt sign, and you're now free to move "
                     "around the cabin. While you're seated, we recommend you keep your seatbelt fastened in case of "
                     "unexpected turbulence. Please take care opening the overhead lockers.",),
        "cheerful": ("Good news, everyone: the seatbelt sign is off! Feel free to stretch your legs. While you're "
                     "seated, keep your belt loosely fastened; the sky has a sense of humour too.",),
        "warm": ("Ladies and gentlemen, the seatbelt sign is now off, so do feel free to move around and make yourselves "
                 "comfortable. When you're in your seat, please keep your belt fastened, just in case.",),
        "brisk": ("The seatbelt sign is off. Keep your belt fastened while seated, please.",),
        "storyteller": ("Ladies and gentlemen, the captain has switched off the seatbelt sign, so you're free to move "
                        "around. We're on our way to {dest_about}. While seated, please keep your seatbelt fastened.",),
    },
    "seatbelt_off_generic": {
        "polished": ("Ladies and gentlemen, you're now free to move around the cabin. While seated, please keep your "
                     "seatbelt fastened.",),
    },
    "turbulence": {
        "polished": ("Ladies and gentlemen, the captain has switched on the seatbelt sign. Please return to your seats "
                     "and fasten your seatbelts. The washrooms should not be used while the sign is on. Thank you.",),
        "cheerful": ("Folks, the seatbelt sign is back on, so please head back to your seats and buckle up. Just a "
                     "few bumps in the road, or rather, in the air.",),
        "warm": ("Ladies and gentlemen, the seatbelt sign is back on. Please return to your seats and fasten your "
                 "belts. It's only a little bumpy, nothing to worry about.",),
        "brisk": ("Seatbelt sign is on. Back to your seats and belts fastened, please.",),
    },
    "turbulence_service": {
        "polished": ("We'll pause the service for now, and continue as soon as it's smoother.",),
    },
    "seatbelt_again_off": {
        "polished": ("Ladies and gentlemen, the seatbelt sign is off again. Thank you for your patience.",),
        "cheerful": ("And we're back! The seatbelt sign is off again.",),
    },
    "service_call": {
        "polished": ("Captain, it's {name}. We're about to start the service. Can I get you anything from the cabin?",),
        "cheerful": ("Hey, it's {name}! The trolley's about to roll. Coffee, tea, something cold for the flight deck?",),
        "warm": ("Hi, it's {name}. We're starting the service now. Can I bring you something? A coffee, some water?",),
        "brisk": ("{name} here. Starting the service. Anything for you?",),
        "storyteller": ("Hi, it's {name}. We're about to start the service. Fancy a coffee? Ours is fresh, I just made "
                        "a pot.",),
    },
    "service": {
        "polished": ("Ladies and gentlemen, in a few moments we'll be coming through the cabin with complimentary drinks "
                     "and snacks. Please lower your tray table when we reach your row.",),
        "cheerful": ("Ladies and gentlemen, the moment you've been waiting for: the trolley is on its way, with "
                     "drinks and snacks on the house. Tray tables down when we get to you!",),
        "brisk": ("Drinks and snacks are on their way. Tray tables down when we reach you, please.",),
        "storyteller": ("Ladies and gentlemen, we'll be through the cabin shortly with drinks and snacks. While you "
                        "wait: we're cruising at {cruise}, a long way above the weather.",),
    },
    "service_sale": {
        "polished": ("Ladies and gentlemen, we'll shortly be coming through the cabin with our onboard menu. You'll "
                     "find it in your seat pocket. We accept cards only.",),
        "cheerful": ("Hungry? Thirsty? Bored? We can fix the first two: our trolley is on its way, with the menu in your "
                     "seat pocket. Cards only, please!",),
        "brisk": ("Our onboard menu is in your seat pocket. The trolley is on its way. Cards only.",),
    },
    "descent": {
        "polished": ("Ladies and gentlemen, we have begun our descent into {dest}. {local}{weather}Please return to "
                     "your seat, fasten your seatbelt, and make sure your tray table is stowed and your seat back "
                     "upright. Thank you.",),
        "cheerful": ("Folks, we're on our way down into {dest}! {local}{weather}Time to get those seat backs up, tray "
                     "tables stowed, and anything you've spread across the cabin back in your bag.",),
        "warm": ("Ladies and gentlemen, we've started our descent into {dest}. {local}{weather}Please make your way back "
                 "to your seat and fasten your seatbelt. If you have a connection, the ground staff will help you on "
                 "arrival.",),
        "brisk": ("We've started our descent into {dest}. {local}{weather}Seats upright, tray tables stowed, belts "
                  "fastened.",),
        "storyteller": ("Ladies and gentlemen, we've begun our descent into {dest_about}. {local}{weather}Please "
                        "return to your seats, and have a look out of the window on the way down.",),
    },
    "landing": {
        "polished": ("Ladies and gentlemen, we are now on our approach. Please make sure your seatbelt is fastened, your "
                     "seat back is upright, your tray table is stowed and your window blind is open. Bags under the "
                     "seat in front of you, please. Thank you.",),
        "cheerful": ("Last call, everyone: belts on, seats up, tables away, blinds open. We'll be on the ground before "
                     "you know it.",),
        "brisk": ("We're on our approach. Belts fastened, seats upright, tables stowed, blinds open.",),
    },
    "secure_landing": {
        "polished": ("Flight deck, cabin's secure for landing.", "Captain, it's {name}. The cabin is secure for landing."),
        "cheerful": ("Hi, {name} again. Cabin's all set for landing. Make it a smooth one!",),
        "warm": ("Captain, cabin's secure for landing. See you on the ground.",),
        "brisk": ("Cabin secure for landing.",),
    },
    "after_landing": {
        "polished": ("Ladies and gentlemen, welcome to {dest}. {local}{weather}Please remain seated with your seatbelt "
                     "fastened until the aircraft has come to a complete stop and the seatbelt sign has been switched "
                     "off. Take care opening the overhead lockers, as items may have moved during the flight.",),
        "cheerful": ("Ladies and gentlemen, welcome to {dest}! {local}{weather}Please stay in your seats with your "
                     "belts on until we've stopped at the gate and the sign goes off. I know, it's the hardest part of "
                     "the flight.",),
        "warm": ("Ladies and gentlemen, welcome to {dest}. {local}{weather}Please stay seated with your seatbelt "
                 "fastened until the seatbelt sign is off. Do check around your seat for your belongings.",),
        "brisk": ("Welcome to {dest}. {local}{weather}Stay seated until the seatbelt sign is off, please.",),
        "storyteller": ("Ladies and gentlemen, welcome to {dest_about}. {local}{weather}Please stay seated until the "
                        "seatbelt sign is off.",),
    },
    "doors_arm": {
        "polished": ("Cabin crew, doors to automatic and cross-check.",),
    },
    "doors_arm_us": {
        "polished": ("Flight attendants, prepare doors for departure and cross-check.",),
    },
    "doors_disarm": {
        "polished": ("Cabin crew, doors to manual and cross-check.",),
    },
    "doors_disarm_us": {
        "polished": ("Flight attendants, disarm doors and cross-check.",),
    },
    "goodbye": {
        "polished": ("Ladies and gentlemen, thank you for flying with {airline}. {members_bye}On behalf of the whole "
                     "crew, we wish you a pleasant stay in {dest}, or a safe onward journey. We hope to welcome you on "
                     "board again soon.",),
        "cheerful": ("That's all from us, folks! Thanks for flying {airline}. {members_bye}Whatever brings you to "
                     "{dest}, have a fantastic time, and we hope to see you again soon.",),
        "warm": ("Ladies and gentlemen, thank you for travelling with us today. {members_bye}Please take care as you "
                 "leave the aircraft, and have a wonderful time in {dest}.",),
        "brisk": ("Thank you for flying {airline}. {members_bye}Enjoy {dest}.",),
    },
    "members_bye": {
        "polished": ("A special thank you to our {program} members. ",),
        "cheerful": ("{program} members, thanks for sticking with us! ",),
    },
    "boarded": {
        "polished": ("Captain, it's {name}. Boarding is complete: {pax} passengers on board. The cabin's ready, and "
                     "the doors can be closed whenever you are.",),
        "cheerful": ("Hi Captain, {name} here! Everyone's on: {pax} passengers, and nobody's in the wrong seat, for "
                     "once. We're ready to close up when you are.",),
        "warm": ("Captain, it's {name}. Everyone's on board: {pax} passengers today. The cabin's ready whenever you are.",),
        "brisk": ("Boarding complete, {pax} on board. Ready for door closure.",),
    },
    "deboarded": {
        "polished": ("Captain, it's {name}. Everyone's off. Thank you for a lovely flight.",),
        "cheerful": ("Captain, the cabin's empty! Great flight. Same again tomorrow?",),
        "warm": ("Captain, all the passengers are off. Thank you for looking after us all today.",),
        "brisk": ("Cabin's clear. Thanks, Captain.",),
    },
}

# What the crew member says about the landing, on the way off (by how firm it was).
LANDING_WORDS = {
    "applauded": ("That landing was lovely, by the way. Some of them clapped!", "Did you hear the applause? Lovely landing."),
    "soft": ("That landing was lovely, by the way.", "Beautiful landing. I barely felt it."),
    "normal": ("Nice landing, too.", "Good landing."),
    "firm": ("That landing woke a few people up!", "A firm one today. Nobody spilled anything. Much."),
}

# The answers on the interphone, when the pilot calls the cabin.
REPLIES: dict[str, tuple[str, ...]] = {
    "drink": ("Of course. One {drink}, coming right up.", "No problem, I'll bring a {drink} up in a moment.",
              "Sure thing. A {drink} it is."),
    "drink_both": ("Two {drink}s, coming up.", "Of course. I'll bring two {drink}s up."),
    "drink_delivered": ("Here's your {drink}, Captain. Careful, it's hot.", "One {drink}, as ordered. Mind, it's hot."),
    "drink_delivered_cold": ("Here's your {drink}, Captain.", "One {drink}, as ordered."),
    "no_drink": ("No problem. Just call if you change your mind.", "All right. Give me a call if you need anything."),
    "meal": ("Of course. I'll bring your meals up shortly.", "Sure. Crew meals coming up in a few minutes."),
    "meal_delivered": ("Here are your meals, Captain. Enjoy.",),
    "status": ("All good back here. {pax} passengers, nice and calm.", "Everything's fine in the cabin. Quiet one so far.",
               "All calm. A few people asleep already."),
    "count": ("We've got {pax} passengers on board today.", "{pax} passengers, Captain."),
    "prepare_landing": ("Will do. I'll call you when the cabin's secure.", "Understood. Preparing the cabin for landing."),
    "prepare_takeoff": ("Understood. I'll let you know when we're secure.",),
    "already_secure": ("The cabin's already secure, Captain.", "We're all secure back here already."),
    "turbulence": ("Thanks for the heads-up. I'll make an announcement and we'll stop the service.",
                   "Understood. I'll get everyone seated."),
    "delay": ("Understood. I'll let the passengers know.", "Got it. I'll make an announcement."),
    "relay": ("Will do.", "Of course, I'll pass that on."),
    "thanks": ("You're welcome!", "Any time.", "My pleasure."),
    "who": ("It's {name}, your cabin manager today.",),
    "service_start": ("Okay, we'll start the service now.",),
    "safety_done": ("We've done the safety demonstration already, Captain.",),
    "hello": ("Hi Captain, it's {name}. What can I do for you?", "{name} here. Go ahead."),
    "landing_feedback": ("Honestly? {verdict}", "{verdict}"),
    "unclear": ("Sorry, Captain, I didn't quite catch that. Could you say again?", "Sorry, say again?"),
}

# The announcements in the languages of the airlines that make them in more than English (said before or after the
# English, as the airline does). Fewer wordings than in English, and no manners: the same words for every crew.
FOREIGN: dict[str, dict[str, str]] = {
    "fr": {
        "boarding": "Bonjour et bienvenue à bord du vol {airline} {flight} à destination de {dest}. Merci de placer vos "
                    "bagages dans les coffres à bagages ou sous le siège devant vous.",
        "welcome": "Mesdames et messieurs, au nom du commandant de bord et de tout l'équipage, bienvenue à bord du vol "
                   "{airline} {flight} à destination de {dest}. Notre temps de vol est estimé à {ltime}. Merci "
                   "d'attacher votre ceinture et de redresser votre dossier.",
        "safety": "Mesdames et messieurs, merci de votre attention pour les consignes de sécurité. Pour attacher votre "
                  "ceinture, insérez la boucle et serrez la sangle. Repérez la sortie la plus proche de vous, elle peut "
                  "se trouver derrière vous. En cas de dépressurisation, des masques à oxygène tomberont devant vous : "
                  "placez-le sur votre nez et votre bouche et respirez normalement. Votre gilet de sauvetage se trouve "
                  "sous votre siège. Il est interdit de fumer à bord. Merci.",
        "seatbelt_off": "Mesdames et messieurs, le commandant a éteint la consigne lumineuse. Lorsque vous êtes assis, "
                        "nous vous recommandons de garder votre ceinture attachée.",
        "turbulence": "Mesdames et messieurs, le commandant a allumé la consigne lumineuse. Merci de regagner vos sièges "
                      "et d'attacher vos ceintures.",
        "service": "Mesdames et messieurs, nous allons passer dans la cabine avec des boissons et des collations.",
        "service_sale": "Mesdames et messieurs, notre carte se trouve dans la pochette du siège devant vous. Nous "
                        "acceptons uniquement les cartes bancaires.",
        "descent": "Mesdames et messieurs, nous avons commencé notre descente vers {dest}. Merci de regagner votre siège, "
                   "d'attacher votre ceinture et de relever votre tablette.",
        "landing": "Mesdames et messieurs, nous sommes en approche. Merci de vérifier que votre ceinture est attachée et "
                   "que votre tablette est relevée.",
        "after_landing": "Mesdames et messieurs, bienvenue à {dest}. Merci de rester assis, ceinture attachée, jusqu'à "
                         "l'extinction de la consigne lumineuse.",
        "goodbye": "Merci d'avoir voyagé avec {airline}. Au nom de tout l'équipage, nous vous souhaitons un agréable "
                   "séjour et espérons vous revoir bientôt à bord.",
        "doors_arm": "Personnel de cabine, portes en automatique, vérification de la porte opposée.",
        "doors_disarm": "Personnel de cabine, portes en manuel, vérification de la porte opposée.",
    },
    "de": {
        "boarding": "Guten Tag und herzlich willkommen an Bord des {airline} Flugs {flight} nach {dest}. Bitte verstauen "
                    "Sie Ihr Handgepäck in den Gepäckfächern oder unter dem Sitz vor Ihnen.",
        "welcome": "Meine Damen und Herren, im Namen unseres Kapitäns und der gesamten Crew begrüßen wir Sie an Bord des "
                   "{airline} Flugs {flight} nach {dest}. Unsere Flugzeit beträgt heute {ltime}. Bitte schnallen Sie "
                   "sich an und bringen Sie Ihre Rückenlehne in eine aufrechte Position.",
        "safety": "Meine Damen und Herren, wir bitten um Ihre Aufmerksamkeit für unsere Sicherheitshinweise. Zum "
                  "Anschnallen stecken Sie die Metallzunge in das Schloss und ziehen den Gurt fest. Bitte merken Sie sich "
                  "den Notausgang, der Ihnen am nächsten ist; er kann sich auch hinter Ihnen befinden. Bei Druckverlust "
                  "fallen Sauerstoffmasken aus der Decke: Ziehen Sie die Maske zu sich, setzen Sie sie über Mund und "
                  "Nase und atmen Sie normal. Ihre Schwimmweste befindet sich unter Ihrem Sitz. Rauchen ist an Bord "
                  "nicht gestattet. Vielen Dank.",
        "seatbelt_off": "Meine Damen und Herren, der Kapitän hat die Anschnallzeichen ausgeschaltet. Wir empfehlen "
                        "Ihnen, am Platz den Gurt geschlossen zu halten.",
        "turbulence": "Meine Damen und Herren, der Kapitän hat die Anschnallzeichen eingeschaltet. Bitte kehren Sie zu "
                      "Ihren Plätzen zurück und schnallen Sie sich an.",
        "service": "Meine Damen und Herren, wir kommen in Kürze mit Getränken und Snacks durch die Kabine.",
        "service_sale": "Meine Damen und Herren, unsere Bordkarte finden Sie in der Sitztasche. Wir akzeptieren nur "
                        "Kartenzahlung.",
        "descent": "Meine Damen und Herren, wir haben mit dem Sinkflug nach {dest} begonnen. Bitte kehren Sie zu Ihrem "
                   "Platz zurück, schnallen Sie sich an und klappen Sie Ihren Tisch hoch.",
        "landing": "Meine Damen und Herren, wir befinden uns im Landeanflug. Bitte schnallen Sie sich an und bringen "
                   "Sie Ihre Rückenlehne in eine aufrechte Position.",
        "after_landing": "Meine Damen und Herren, willkommen in {dest}. Bitte bleiben Sie angeschnallt sitzen, bis die "
                         "Anschnallzeichen erloschen sind.",
        "goodbye": "Vielen Dank, dass Sie mit {airline} geflogen sind. Wir wünschen Ihnen einen schönen Aufenthalt und "
                   "würden uns freuen, Sie bald wieder an Bord begrüßen zu dürfen.",
        "doors_arm": "Kabinenpersonal, Türen auf Automatik und Gegenkontrolle.",
        "doors_disarm": "Kabinenpersonal, Türen auf Manuell und Gegenkontrolle.",
    },
    "es": {
        "boarding": "Buenos días y bienvenidos a bordo del vuelo {flight} de {airline} con destino a {dest}. Por favor, "
                    "coloquen su equipaje en los compartimentos superiores o debajo del asiento delantero.",
        "welcome": "Señoras y señores, en nombre del comandante y de toda la tripulación, les damos la bienvenida a "
                   "bordo del vuelo {flight} de {airline} con destino a {dest}. Nuestro tiempo de vuelo será de "
                   "{ltime}. Por favor, abróchense el cinturón.",
        "safety": "Señoras y señores, les rogamos su atención para las instrucciones de seguridad. Para abrocharse el "
                  "cinturón, introduzcan la hebilla y ajusten la correa. Localicen la salida más cercana; puede estar "
                  "detrás de ustedes. Si la cabina pierde presión, caerán mascarillas de oxígeno: colóquenla sobre la "
                  "nariz y la boca y respiren con normalidad. Su chaleco salvavidas está debajo del asiento. Está "
                  "prohibido fumar a bordo. Gracias.",
        "seatbelt_off": "Señoras y señores, el comandante ha apagado la señal de cinturones. Mientras estén sentados, "
                        "les recomendamos mantenerlo abrochado.",
        "turbulence": "Señoras y señores, el comandante ha encendido la señal de cinturones. Por favor, regresen a sus "
                      "asientos y abróchense el cinturón.",
        "service": "Señoras y señores, en breve pasaremos por la cabina con bebidas y aperitivos.",
        "service_sale": "Señoras y señores, encontrarán nuestra carta en el bolsillo del asiento. Solo aceptamos tarjeta.",
        "descent": "Señoras y señores, hemos iniciado el descenso hacia {dest}. Por favor, regresen a sus asientos, "
                   "abróchense el cinturón y plieguen la mesa.",
        "landing": "Señoras y señores, estamos en aproximación. Comprueben que su cinturón está abrochado y su respaldo "
                   "en posición vertical.",
        "after_landing": "Señoras y señores, bienvenidos a {dest}. Por favor, permanezcan sentados con el cinturón "
                         "abrochado hasta que se apague la señal.",
        "goodbye": "Gracias por volar con {airline}. En nombre de toda la tripulación, les deseamos una feliz estancia "
                   "y esperamos verles pronto a bordo.",
        "doors_arm": "Tripulación de cabina, puertas en automático y comprobación cruzada.",
        "doors_disarm": "Tripulación de cabina, puertas en manual y comprobación cruzada.",
    },
    "it": {
        "boarding": "Buongiorno e benvenuti a bordo del volo {airline} {flight} per {dest}. Vi preghiamo di riporre il "
                    "bagaglio nelle cappelliere o sotto il sedile davanti a voi.",
        "welcome": "Signore e signori, a nome del comandante e di tutto l'equipaggio, benvenuti a bordo del volo "
                   "{airline} {flight} per {dest}. Il tempo di volo previsto è di {ltime}. Vi preghiamo di allacciare "
                   "le cinture.",
        "safety": "Signore e signori, vi preghiamo di prestare attenzione alle istruzioni di sicurezza. Per allacciare "
                  "la cintura, inserite la fibbia e stringete. Individuate l'uscita più vicina, che potrebbe essere alle "
                  "vostre spalle. In caso di depressurizzazione, le maschere per l'ossigeno scenderanno dall'alto: "
                  "posizionatela su naso e bocca e respirate normalmente. Il giubbotto di salvataggio si trova sotto il "
                  "sedile. È vietato fumare a bordo. Grazie.",
        "seatbelt_off": "Signore e signori, il comandante ha spento il segnale delle cinture. Quando siete seduti, vi "
                        "consigliamo di tenerle allacciate.",
        "turbulence": "Signore e signori, il comandante ha acceso il segnale delle cinture. Vi preghiamo di tornare al "
                      "vostro posto e di allacciare le cinture.",
        "service": "Signore e signori, a breve passeremo in cabina con bevande e snack.",
        "service_sale": "Signore e signori, troverete il nostro menù nella tasca del sedile. Accettiamo solo carte.",
        "descent": "Signore e signori, abbiamo iniziato la discesa verso {dest}. Vi preghiamo di tornare al vostro "
                   "posto e di allacciare le cinture.",
        "landing": "Signore e signori, siamo in avvicinamento. Verificate che le cinture siano allacciate e lo schienale "
                   "in posizione verticale.",
        "after_landing": "Signore e signori, benvenuti a {dest}. Vi preghiamo di restare seduti con le cinture "
                         "allacciate fino allo spegnimento del segnale.",
        "goodbye": "Grazie per aver volato con {airline}. A nome di tutto l'equipaggio vi auguriamo un buon soggiorno e "
                   "speriamo di rivedervi presto a bordo.",
        "doors_arm": "Personale di cabina, porte in automatico e controllo incrociato.",
        "doors_disarm": "Personale di cabina, porte in manuale e controllo incrociato.",
    },
    "pt": {
        "boarding": "Bom dia e bem-vindos a bordo do voo {flight} da {airline} com destino a {dest}. Por favor, coloquem "
                    "a bagagem nos compartimentos superiores ou debaixo do assento à vossa frente.",
        "welcome": "Senhoras e senhores, em nome do comandante e de toda a tripulação, bem-vindos a bordo do voo "
                   "{flight} da {airline} com destino a {dest}. O nosso tempo de voo será de {ltime}. Por favor, "
                   "apertem os cintos.",
        "safety": "Senhoras e senhores, pedimos a vossa atenção para as instruções de segurança. Para apertar o cinto, "
                  "insira a fivela e ajuste a correia. Localize a saída mais próxima, que pode estar atrás de si. Em "
                  "caso de despressurização, as máscaras de oxigénio cairão à sua frente: coloque-a sobre o nariz e a "
                  "boca e respire normalmente. O colete salva-vidas está debaixo do seu assento. É proibido fumar a "
                  "bordo. Obrigado.",
        "seatbelt_off": "Senhoras e senhores, o comandante desligou o sinal de apertar cintos. Quando estiverem "
                        "sentados, recomendamos que mantenham o cinto apertado.",
        "turbulence": "Senhoras e senhores, o comandante ligou o sinal de apertar cintos. Por favor, regressem aos "
                      "vossos lugares e apertem os cintos.",
        "service": "Senhoras e senhores, dentro de momentos passaremos pela cabine com bebidas e snacks.",
        "service_sale": "Senhoras e senhores, o nosso menu está no bolso do assento. Aceitamos apenas cartões.",
        "descent": "Senhoras e senhores, iniciámos a descida para {dest}. Por favor, regressem aos vossos lugares e "
                   "apertem os cintos.",
        "landing": "Senhoras e senhores, estamos em aproximação. Verifiquem que o cinto está apertado e a mesa recolhida.",
        "after_landing": "Senhoras e senhores, bem-vindos a {dest}. Por favor, permaneçam sentados com o cinto apertado "
                         "até o sinal se desligar.",
        "goodbye": "Obrigado por voarem com a {airline}. Em nome de toda a tripulação, desejamos-lhes uma excelente "
                   "estadia e esperamos voltar a vê-los em breve.",
        "doors_arm": "Tripulação de cabine, portas em automático e verificação cruzada.",
        "doors_disarm": "Tripulação de cabine, portas em manual e verificação cruzada.",
    },
    "nl": {
        "boarding": "Goedendag en welkom aan boord van {airline} vlucht {flight} naar {dest}. Wilt u uw handbagage in de "
                    "bagagevakken of onder de stoel voor u plaatsen?",
        "welcome": "Dames en heren, namens de gezagvoerder en de hele bemanning welkom aan boord van {airline} vlucht "
                   "{flight} naar {dest}. Onze vliegtijd is vandaag {ltime}. Wilt u uw veiligheidsgordel vastmaken?",
        "safety": "Dames en heren, mogen wij uw aandacht voor de veiligheidsinstructies. Maak uw gordel vast door de "
                  "gesp in het slot te steken en de riem aan te trekken. Zoek de dichtstbijzijnde nooduitgang; die kan "
                  "ook achter u zijn. Bij drukverlies vallen er zuurstofmaskers naar beneden: plaats het masker over "
                  "neus en mond en adem normaal. Uw zwemvest bevindt zich onder uw stoel. Roken is aan boord niet "
                  "toegestaan. Dank u wel.",
        "seatbelt_off": "Dames en heren, de gezagvoerder heeft het gordelteken uitgezet. Wij raden u aan uw gordel "
                        "vast te houden terwijl u zit.",
        "turbulence": "Dames en heren, de gezagvoerder heeft het gordelteken aangezet. Wilt u naar uw plaats terugkeren "
                      "en uw gordel vastmaken?",
        "service": "Dames en heren, wij komen zo door de cabine met drankjes en snacks.",
        "service_sale": "Dames en heren, onze menukaart vindt u in het stoelvak. Wij accepteren alleen kaartbetalingen.",
        "descent": "Dames en heren, wij zijn begonnen met de daling naar {dest}. Wilt u naar uw plaats terugkeren en uw "
                   "gordel vastmaken?",
        "landing": "Dames en heren, wij zijn in de nadering. Controleert u of uw gordel vastzit en uw tafeltje is "
                   "opgeklapt.",
        "after_landing": "Dames en heren, welkom in {dest}. Wilt u blijven zitten met uw gordel vast tot het gordelteken "
                         "uit is?",
        "goodbye": "Dank u wel dat u met {airline} heeft gevlogen. Namens de hele bemanning wensen wij u een prettig "
                   "verblijf en hopen wij u snel weer aan boord te verwelkomen.",
        "doors_arm": "Cabinepersoneel, deuren op automatisch en kruiscontrole.",
        "doors_disarm": "Cabinepersoneel, deuren op handmatig en kruiscontrole.",
    },
    "pl": {
        "boarding": "Dzień dobry, witamy na pokładzie lotu {airline} {flight} do {dest}. Prosimy o umieszczenie bagażu "
                    "w schowkach nad głową lub pod fotelem przed Państwem.",
        "welcome": "Szanowni Państwo, w imieniu kapitana i całej załogi witamy na pokładzie lotu {airline} {flight} do "
                   "{dest}. Czas lotu wyniesie {ltime}. Prosimy o zapięcie pasów.",
        "safety": "Szanowni Państwo, prosimy o uwagę podczas instrukcji bezpieczeństwa. Aby zapiąć pas, włóż metalową "
                  "końcówkę do klamry i zaciśnij pas. Prosimy zlokalizować najbliższe wyjście ewakuacyjne; może "
                  "znajdować się za Państwem. W razie spadku ciśnienia z góry wypadną maski tlenowe: należy założyć "
                  "maskę na nos i usta i oddychać normalnie. Kamizelka ratunkowa znajduje się pod fotelem. Palenie na "
                  "pokładzie jest zabronione. Dziękujemy.",
        "seatbelt_off": "Szanowni Państwo, kapitan wyłączył sygnał zapięcia pasów. Zalecamy, aby podczas siedzenia pasy "
                        "pozostały zapięte.",
        "turbulence": "Szanowni Państwo, kapitan włączył sygnał zapięcia pasów. Prosimy wrócić na miejsca i zapiąć pasy.",
        "service": "Szanowni Państwo, za chwilę przejdziemy przez kabinę z napojami i przekąskami.",
        "service_sale": "Szanowni Państwo, nasze menu znajdą Państwo w kieszeni fotela. Akceptujemy wyłącznie karty.",
        "descent": "Szanowni Państwo, rozpoczęliśmy zniżanie do {dest}. Prosimy wrócić na miejsca i zapiąć pasy.",
        "landing": "Szanowni Państwo, podchodzimy do lądowania. Prosimy sprawdzić, czy pasy są zapięte.",
        "after_landing": "Szanowni Państwo, witamy w {dest}. Prosimy pozostać na miejscach z zapiętymi pasami do czasu "
                         "wyłączenia sygnału.",
        "goodbye": "Dziękujemy za lot z {airline}. W imieniu całej załogi życzymy udanego pobytu i mamy nadzieję "
                   "wkrótce znów gościć Państwa na pokładzie.",
        "doors_arm": "Załoga kabinowa, drzwi na automat i kontrola krzyżowa.",
        "doors_disarm": "Załoga kabinowa, drzwi na manual i kontrola krzyżowa.",
    },
    "sv": {
        "boarding": "God dag och välkomna ombord på {airline} flight {flight} till {dest}. Placera gärna ert handbagage "
                    "i bagagefacken eller under stolen framför er.",
        "welcome": "Mina damer och herrar, på kaptenens och hela besättningens vägnar, välkomna ombord på {airline} "
                   "flight {flight} till {dest}. Vår flygtid i dag är {ltime}. Vänligen spänn fast säkerhetsbältet.",
        "safety": "Mina damer och herrar, vi ber om er uppmärksamhet för säkerhetsinstruktionerna. Spänn fast bältet "
                  "genom att föra in metalltungan i spännet och dra åt. Leta upp närmaste nödutgång; den kan finnas "
                  "bakom er. Vid tryckfall faller syrgasmasker ned: placera masken över näsa och mun och andas normalt. "
                  "Er flytväst finns under stolen. Rökning är förbjuden ombord. Tack.",
        "seatbelt_off": "Mina damer och herrar, kaptenen har släckt bältesskylten. Vi rekommenderar att ni har bältet "
                        "fastspänt när ni sitter.",
        "turbulence": "Mina damer och herrar, kaptenen har tänt bältesskylten. Vänligen återgå till era platser och "
                      "spänn fast bältet.",
        "service": "Mina damer och herrar, vi kommer strax genom kabinen med dryck och tilltugg.",
        "service_sale": "Mina damer och herrar, vår meny finns i stolsfickan. Vi tar endast kort.",
        "descent": "Mina damer och herrar, vi har påbörjat inflygningen mot {dest}. Vänligen återgå till era platser och "
                   "spänn fast bältet.",
        "landing": "Mina damer och herrar, vi närmar oss landning. Kontrollera att bältet är fastspänt.",
        "after_landing": "Mina damer och herrar, välkomna till {dest}. Vänligen sitt kvar med bältet fastspänt tills "
                         "bältesskylten släcks.",
        "goodbye": "Tack för att ni flög med {airline}. Hela besättningen önskar er en trevlig vistelse, och vi hoppas "
                   "att få se er ombord igen snart.",
        "doors_arm": "Kabinpersonal, dörrar till automatiskt läge och korskontroll.",
        "doors_disarm": "Kabinpersonal, dörrar till manuellt läge och korskontroll.",
    },
    "ru": {
        "boarding": "Добрый день и добро пожаловать на борт рейса {airline} {flight} в {dest}. Пожалуйста, разместите "
                    "ручную кладь на багажных полках или под сиденьем перед вами.",
        "welcome": "Уважаемые пассажиры, от имени командира и всего экипажа приветствуем вас на борту рейса {airline} "
                   "{flight} в {dest}. Время в пути составит {ltime}. Пожалуйста, пристегните ремни.",
        "safety": "Уважаемые пассажиры, просим вашего внимания к правилам безопасности. Чтобы пристегнуть ремень, "
                  "вставьте металлическую пластину в замок и затяните ремень. Найдите ближайший аварийный выход, он "
                  "может быть позади вас. При разгерметизации сверху выпадут кислородные маски: наденьте маску на нос и "
                  "рот и дышите нормально. Спасательный жилет находится под вашим сиденьем. Курение на борту "
                  "запрещено. Спасибо.",
        "seatbelt_off": "Уважаемые пассажиры, командир выключил табло «Пристегните ремни». Когда вы сидите, "
                        "рекомендуем держать ремень пристёгнутым.",
        "turbulence": "Уважаемые пассажиры, командир включил табло «Пристегните ремни». Пожалуйста, вернитесь на свои "
                      "места и пристегнитесь.",
        "service": "Уважаемые пассажиры, вскоре мы предложим вам напитки и закуски.",
        "service_sale": "Уважаемые пассажиры, меню находится в кармане кресла. Мы принимаем только карты.",
        "descent": "Уважаемые пассажиры, мы начали снижение в {dest}. Пожалуйста, займите свои места и пристегните ремни.",
        "landing": "Уважаемые пассажиры, мы заходим на посадку. Проверьте, пожалуйста, что ваш ремень пристёгнут.",
        "after_landing": "Уважаемые пассажиры, добро пожаловать в {dest}. Пожалуйста, оставайтесь на местах с "
                         "пристёгнутыми ремнями до выключения табло.",
        "goodbye": "Благодарим вас за выбор {airline}. От имени всего экипажа желаем вам приятного пребывания и "
                   "надеемся снова увидеть вас на борту.",
        "doors_arm": "Бортпроводникам: двери в автоматический режим, перекрёстная проверка.",
        "doors_disarm": "Бортпроводникам: двери в ручной режим, перекрёстная проверка.",
    },
    "zh": {
        "boarding": "女士们，先生们，欢迎乘坐{airline}{flight}次航班前往{dest}。请将您的行李放在行李架上或您前方的座椅下方。",
        "welcome": "女士们，先生们，我谨代表机长和全体机组成员，欢迎您乘坐{airline}{flight}次航班前往{dest}。"
                   "预计飞行时间为{ltime}。请您系好安全带。",
        "safety": "女士们，先生们，请注意观看安全示范。系安全带时，请将金属扣插入锁扣并拉紧。请找到离您最近的紧急出口，"
                  "它可能在您的身后。如果客舱失压，氧气面罩会自动落下，请将面罩罩在口鼻上，正常呼吸。"
                  "救生衣在您的座椅下方。机上全程禁止吸烟。谢谢。",
        "seatbelt_off": "女士们，先生们，机长已经关闭安全带指示灯。在座位上时，建议您继续系好安全带。",
        "turbulence": "女士们，先生们，机长已经打开安全带指示灯。请您回到座位上并系好安全带。",
        "service": "女士们，先生们，我们即将为您提供饮料和小吃。",
        "service_sale": "女士们，先生们，菜单在您座椅前方的口袋里。我们只接受银行卡支付。",
        "descent": "女士们，先生们，我们已经开始下降，前往{dest}。请您回到座位上，系好安全带，收起小桌板。",
        "landing": "女士们，先生们，我们正在进近。请确认您的安全带已经系好。",
        "after_landing": "女士们，先生们，欢迎来到{dest}。在安全带指示灯熄灭之前，请您继续坐好，系好安全带。",
        "goodbye": "感谢您乘坐{airline}的航班。全体机组成员祝您旅途愉快，期待再次与您相见。",
        "doors_arm": "乘务员，请将滑梯预位并交叉检查。",
        "doors_disarm": "乘务员，请解除滑梯预位并交叉检查。",
    },
}
