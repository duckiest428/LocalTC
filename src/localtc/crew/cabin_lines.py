"""The cabin crew's words: each announcement and each call to the flight deck, in the manner of the crew member
leading the cabin ([cabin] crew), and the announcements in the languages of the airlines that make them in more than
English.

Each line is a template: ``{airline}``, ``{flight}`` (the flight number in words), ``{dest}`` (the destination's city),
``{dest_about}`` (the city with a word about it), ``{time}`` (the flight time in words; ``{ltime}`` in the
announcement's language), ``{captain}`` ("Captain Reyes", or "the captain"; ``{Captain}`` at the start of a sentence),
``{name}`` (the crew member),
``{program}`` (the loyalty programme), ``{alliance}``, ``{pax}`` (passengers on board), ``{local}`` (the local time),
``{weather}`` (the temperature and the sky), ``{exits}`` (how many exits and where), ``{drink}``, ``{minutes}``,
``{message}``, ``{left}`` (minutes of boarding), ``{reason}``, ``{plain}`` (nothing: the wording for when those aren't
known), ``{greeting}`` ("Good morning"), ``{aircraft}`` and ``{aircraft_fact}``, ``{overwing}``. A template naming one that isn't known for this flight isn't used
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
    "exit_row": {  # who sits at an exit is asked (14 CFR 121.585)
        "polished": ("If you are seated in an emergency exit row, you must be willing and able to help in an emergency. "
                     "If you would rather not, please tell one of us and we will find you another seat.",),
        "cheerful": ("A word for those of you enjoying the extra legroom in the exit rows: it comes with a job. You need to "
                     "be willing and able to help in an emergency. If that's not for you, tell us and we'll find you "
                     "another seat, no hard feelings.",),
        "brisk": ("Exit rows: you must be willing and able to help in an emergency. If not, tell us and we'll move you.",),
    },
    "safety_more": {  # the rest of what a briefing has to say (14 CFR 121.571; the FAA on lithium batteries)
        "polished": ("Please keep your seatbelt fastened whenever the sign is on, low and tight across your hips. "
                     "{overwing}Phones and other devices go into flight mode now, and larger items like laptops must be "
                     "stowed for takeoff and landing. If a device with a lithium battery slips down beside your seat, "
                     "please do not move the seat: call one of us. It is against the law to tamper with, disable or "
                     "destroy the smoke detectors in the washrooms.",),
        "cheerful": ("Keep your belt low and tight across your hips whenever the sign is on. {overwing}Flight mode "
                     "on those phones, please, and laptops away for takeoff. If your phone falls down beside the seat, "
                     "don't go moving the seat to get it: those batteries don't like being crushed, so call us. And "
                     "tampering with the washroom smoke detectors is against the law, however good that cigarette "
                     "sounds.",),
        "brisk": ("Belts low and tight when the sign is on. {overwing}Devices in flight mode, laptops stowed. A device "
                  "lost beside your seat: don't move the seat, call us. Tampering with washroom smoke detectors is "
                  "illegal.",),
    },
    "aircraft_talk": {  # in the cruise: about the aircraft they're on
        "polished": ("Ladies and gentlemen, a word about your aircraft today. This is an {aircraft}: {aircraft_fact}. Our "
                     "cruising altitude is {cruise}. Please keep your seatbelt fastened while you are seated, even "
                     "with the sign off.",),
        "cheerful": ("Folks, since you're stuck with us for a while: you're riding on an {aircraft}, and {aircraft_fact}. "
                     "We're up at {cruise}. Keep those belts on while you're seated, the sign's off but the sky "
                     "doesn't always tell us first.",),
        "warm": ("Ladies and gentlemen, I hope you're comfortable. In case you were wondering, this is an {aircraft}, "
                 "and {aircraft_fact}. We're cruising at {cruise}. When you're in your seat, please keep your belt "
                 "fastened, just in case.",),
        "storyteller": ("A little something for the curious: you're on an {aircraft}, and {aircraft_fact}. We're at "
                        "{cruise}, on our way to {dest_about}. Do keep your belt fastened while seated, even with the sign off.",),
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
                     "seat in front of you, laptops stowed, and the washrooms are now closed. Thank you.",),
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
    "delay": {
        "polished": ("Ladies and gentlemen, we apologise for the delay to our departure: {reason}. We'll be on our way "
                     "as soon as we can. Thank you for your patience.",
                     "Ladies and gentlemen, we apologise for the delay to our departure.{plain} We'll be on our way as soon as "
                     "we can. Thank you for your patience.",),
        "cheerful": ("Thanks for bearing with us, everyone. We're running a little behind today: {reason}. We'll get "
                     "going just as soon as we can!",
                     "Thanks for bearing with us, everyone. We're running a little behind today,{plain} and we'll get going "
                     "just as soon as we can!",),
        "warm": ("We know you're keen to be on your way, and we're sorry for the wait: {reason}. We'll be off as soon as "
                 "we can. Thank you for being so patient.",
                 "We know you're keen to be on your way, and we're sorry for the wait.{plain} We'll be off as soon as we can. "
                 "Thank you for being so patient.",),
        "brisk": ("Ladies and gentlemen, our departure is delayed. We'll update you shortly.",),
    },
    "late_not_boarding": {
        "polished": ("Captain, it's {name}. We're {minutes} minutes from off-block and boarding hasn't started. Shall we "
                     "begin?",),
        "cheerful": ("Hi Captain, {name} here! {minutes} minutes to off-block and nobody on board yet. Shall we let them on?",),
        "warm": ("Captain, it's {name}. It's {minutes} minutes to off-block and the passengers are still waiting at the "
                 "gate. Are we all right to board?",),
        "brisk": ("{minutes} minutes to off-block, boarding not started. Your call.",),
    },
    "late_boarding": {
        "polished": ("Captain, it's {name}. That's our off-block time and we're still boarding, about {left} minutes to go.",
                     "Captain, it's {name}. That's our off-block time and we're still boarding.{plain}",),
        "cheerful": ("Captain, {name} here. We've hit off-block and they're still coming down the aisle. About {left} "
                     "more minutes!",
                     "Captain, {name} here. We've hit off-block and they're still coming down the aisle.{plain}",),
        "warm": ("Captain, it's {name}. We've reached our off-block time and we're still boarding, about {left} minutes "
                 "more. I'll tell you as soon as everyone's settled.",
                 "Captain, it's {name}. We've reached our off-block time and we're still boarding.{plain} I'll tell you as soon "
                 "as everyone's settled.",),
        "brisk": ("Off-block time, still boarding. About {left} minutes.", "Off-block time, still boarding.{plain}",),
    },
    "late_ready": {
        "polished": ("Captain, it's {name}. We're {minutes} minutes past our off-block time and all ready in the cabin, "
                     "whenever you are.",),
        "cheerful": ("Captain, {name} here! We're {minutes} minutes down and everyone's asking when we're off. We're all "
                     "ready back here.",),
        "warm": ("Captain, it's {name}. We're {minutes} minutes behind now. The cabin's ready and everyone's settled, so "
                 "just let me know.",),
        "brisk": ("{minutes} minutes late. Cabin ready.",),
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

# More wordings of what's heard every flight, each crew member's manner: the same words flight after flight gave it away.
for _key, _wordings in {
    "boarding": {
        "polished": ("{greeting} and welcome on board {airline} flight {flight}, with service to {dest}. Please find your seat as "
                     "quickly as you can, and stow your hand luggage in the overhead lockers or under the seat ahead of you.",
                     "{greeting}, ladies and gentlemen. This is {airline} flight {flight} to {dest}. To help us leave on time, "
                     "please step out of the aisle while you stow your bags. Thank you.",),
        "cheerful": ("{greeting}, folks! You've found {airline} flight {flight} to {dest}, so that's the hard part done. Bags up "
                     "top, or under the seat in front. If it won't fit, give us a shout before you start wrestling it.",
                     "{greeting}, everyone, and welcome aboard! This one's headed for {dest}, flight {flight}. Grab your seat, stow "
                     "your bag, and let's keep that aisle moving. There's no prize for last on, I promise.",),
        "warm": ("{greeting}, everyone, and welcome aboard flight {flight} to {dest}. It's lovely to have you with us. Please "
                 "settle in at your own pace, and do ask if you'd like some help with anything.",
                 "{greeting}, and welcome. This is {airline} flight {flight} to {dest}. If you're travelling with little ones or need "
                 "a hand with a bag, just catch one of us; we'd be glad to help.",),
        "brisk": ("{greeting}. Flight {flight} to {dest}. Stow your bags and take your seat, please, so the aisle stays clear.",
                  "{greeting}. This is flight {flight} to {dest}. Bags stowed, aisle clear, seats as soon as you can.",),
    },
    "welcome": {
        "polished": ("Good to have you with us, ladies and gentlemen. On behalf of {captain} and the crew, welcome to {airline} "
                     "flight {flight} to {dest}, a flight of {time} today. {members}I'm {name}, your cabin manager. Please check that "
                     "your seatbelt is fastened, and your seat back and tray table are upright.",),
        "cheerful": ("Right then, everyone, welcome aboard {airline} flight {flight} to {dest}! {Captain} tells me it's about {time} "
                     "today. {members}I'm {name}, and I'm in charge back here, which surprises me as much as you. Belts on, seats "
                     "up, tables away, and we'll get going.",
                     "Hi again, folks! You're on {airline} flight {flight}, bound for {dest}, and that's about {time} from here. "
                     "{members}My name's {name}. If you need anything, press the call button; if you just want a chat, press it "
                     "anyway.",),
        "warm": ("Hello again, everyone, and welcome aboard {airline} flight {flight} to {dest}. We'll be together for about {time} "
                 "today. {members}I'm {name}, and it's our pleasure to look after you. Do let us know if there's anything you need.",),
        "brisk": ("Welcome aboard {airline} flight {flight} to {dest}. Flight time {time}. {members}I'm {name}, your cabin manager. "
                  "Belts fastened, seats upright, tables stowed.",),
    },
    "safety": {
        "polished": ("Ladies and gentlemen, for your safety, please give us your full attention for the next few moments. Your "
                     "seatbelt fastens by inserting the metal tip into the buckle; pull the strap to tighten it, and lift the buckle "
                     "to release it. This aircraft has {exits}. Please find your nearest exit now, and remember it may be behind you. "
                     "Lighting along the floor will guide you to an exit if it's needed. If the cabin loses pressure, oxygen masks "
                     "will drop from above: pull one towards you, cover your nose and mouth, and breathe normally, and fit your own "
                     "before helping anyone else. Your life vest is under your seat; inflate it only as you leave the aircraft. "
                     "Smoking, and electronic cigarettes, are not permitted on board. Please read the safety card in your seat "
                     "pocket. Thank you.",),
        "cheerful": ("All right, everybody, this is the bit we practise in the mirror. Seatbelt: metal tip into the buckle, pull to "
                     "tighten, lift the buckle to get out. If you've ever been in a car, you've got this. This aircraft has {exits}. "
                     "Find the one nearest you now, and bear in mind it might be behind you. If we need them, lights on the floor "
                     "show the way. If the cabin loses pressure, masks drop from above: pull one towards you, over your nose and "
                     "mouth, breathe normally, and sort yours out before helping anyone else. Your life vest is under your seat; "
                     "don't inflate it until you're leaving the aircraft, or you'll never get through the door. No smoking anywhere, "
                     "e-cigarettes included. The card in your seat pocket has it all, with lovely drawings. Thanks for watching!",),
        "warm": ("Ladies and gentlemen, please take a moment with us for your safety; it only takes a minute. To fasten your "
                 "seatbelt, slide the metal tip into the buckle and pull it snug; lift the buckle to undo it. This aircraft has "
                 "{exits}. Have a look for the one closest to you, which may be behind you, and lights along the floor will lead the "
                 "way if you ever need them. If the cabin loses pressure, masks will come down from above. Pull one towards you, put "
                 "it over your nose and mouth, breathe normally, and put yours on before helping those around you. Your life vest is "
                 "under your seat; inflate it only as you leave the aircraft. There's no smoking on board, electronic cigarettes "
                 "included. The card in your seat pocket has the details. Thank you, and do ask if you have any questions.",),
        "brisk": ("Safety briefing. Seatbelt: metal tip into the buckle, pull tight, lift to release. This aircraft has {exits}: find "
                  "your nearest, it may be behind you. Floor lights lead to the exits. If the cabin loses pressure, masks drop from "
                  "above: pull one down, cover nose and mouth, breathe normally, your own before helping others. Life vest under "
                  "your seat: inflate it only as you leave. No smoking, e-cigarettes included. Safety card in the seat pocket.",),
    },
    "exit_row": {
        "polished": ("A reminder to passengers in the emergency exit rows: you must be willing and able to assist in an emergency. "
                     "If you are not, please let us know and we will gladly move you.",),
        "cheerful": ("Exit row friends, a quick word. That legroom's lovely, but you may be asked to help in an emergency, so you "
                     "have to be willing and able. Not for you? Just say so and we'll find you another seat.",),
        "brisk": ("Passengers at the exits must be willing and able to help in an emergency. Tell us if you'd like to move.",),
    },
    "safety_more": {
        "polished": ("Whenever the seatbelt sign is on, please keep your belt fastened, low and tight across your hips. {overwing}"
                     "Please set your phones and devices to flight mode, and stow larger items such as laptops for takeoff and "
                     "landing. Should a device with a lithium battery fall beside your seat, do not move the seat; call a crew "
                     "member. Tampering with, disabling or destroying a washroom smoke detector is against the law.",),
        "cheerful": ("Two more things, then you're free. Belt low and tight whenever the sign's on. {overwing}Phones in flight mode, "
                     "and laptops tucked away for takeoff. Lost your phone down the side of the seat? Don't move the seat, those "
                     "batteries hate it; call us instead. And no tampering with the washroom smoke detectors. It's the law, and "
                     "they always win.",),
        "brisk": ("Belt low and tight with the sign on. {overwing}Flight mode on, laptops stowed for takeoff. If a device falls beside "
                  "your seat, leave the seat and call us. Don't tamper with the washroom smoke detectors: it's against the law.",),
    },
    "secure_takeoff": {
        "cheerful": ("{name} here! Cabin's ready for takeoff. Everyone's strapped in and looking keen.", "Cabin's good to go, Captain. Off we pop!",),
        "warm": ("Captain, we're all settled and secure back here for takeoff. Safe flight.",),
        "brisk": ("Secure for takeoff.", "Cabin ready for takeoff.",),
    },
    "secure_landing": {
        "cheerful": ("Cabin's ready for landing, Captain. Everyone's strapped in and hoping for a greaser!", "{name} here. All secure back here for landing. Over to you!",),
        "warm": ("Captain, it's {name}. Everyone's settled and secure for landing. Thank you.",),
        "brisk": ("Secure for landing.", "Cabin ready for landing.",),
    },
    "boarded": {
        "polished": ("Captain, it's {name}. We have {pax} passengers on board and the cabin is ready. We can close the doors when "
                     "you're ready.",),
        "cheerful": ("Captain, {name} here! That's the last of them: {pax} on board, bags all squeezed in somehow. Ready to close up "
                     "when you are.", "Hi Captain, it's {name}! All {pax} passengers are in. No stragglers, no drama. Say the word and we'll "
                     "close the doors.",),
        "warm": ("Captain, it's {name}. Everyone's found their seat, {pax} passengers in all, and they're nicely settled. Ready when "
                 "you are.",),
        "brisk": ("{pax} on board. Cabin ready, doors when you say.", "Boarding done. {pax} passengers.",),
    },
    "late_not_boarding": {
        "cheerful": ("Captain, it's {name}! Off-block's in {minutes} minutes and the cabin's still empty. Shall we let the crowd in?",),
        "polished": ("Captain, {name} here. Off-block is in {minutes} minutes and boarding has not begun. Would you like us to start?",),
    },
    "late_ready": {
        "polished": ("Captain, it's {name}. We're now {minutes} minutes behind schedule. The cabin is ready when you are.",
                     "Captain, {name} here. {minutes} minutes past off-block, and the cabin remains ready.",),
        "cheerful": ("Captain, {name} here! {minutes} minutes behind now. The cabin's ready, and I've run out of small talk.",
                     "Hi Captain, it's {name}. We're {minutes} minutes down. All set back here, whenever the flight deck's happy.",
                     "Captain, {name} again! {minutes} minutes late, and a few people have started looking at their watches. "
                     "We're ready when you are.",),
        "warm": ("Captain, it's {name}. {minutes} minutes behind so far. Everyone's being very patient, and we're ready when you are.",
                 "Captain, it's {name}. No rush from us: we're {minutes} minutes late and the cabin's all set.",),
        "brisk": ("{minutes} minutes down. Cabin's ready.", "Cabin ready, {minutes} minutes late.",),
    },
    "seatbelt_off": {
        "polished": ("Ladies and gentlemen, the seatbelt sign has now been switched off. You may move about the cabin, but please "
                     "keep your belt fastened while seated, and take care with the overhead lockers.",),
        "cheerful": ("That's the seatbelt sign off, everyone! Stretch, wander, visit the washroom. When you sit back down, keep your "
                     "belt on loosely; turbulence never sends a warning.",
                     "Ding! That's your cue, folks: the sign's off and you're free to roam. Belts stay on when you're seated, please.",),
        "warm": ("The seatbelt sign is off now, everyone, so do get up and stretch if you'd like. While you're sitting down, please "
                 "keep your belt on, just to be safe.",),
        "brisk": ("Seatbelt sign off. Belts on when seated.",),
    },
    "service_call": {
        "polished": ("Captain, it's {name}. We'll begin the service shortly. May I bring anything to the flight deck?",),
        "cheerful": ("Captain, {name} here! Trolley's nearly out. Last orders for the flight deck: coffee, tea, a cold one? Soft "
                     "drink, I mean.", "Hi, it's {name}! Service time. What can I get you up there?",),
        "warm": ("Captain, it's {name}. We're about to serve the cabin. Can I look after you two first?",),
        "brisk": ("Service starting. Need anything?",),
    },
    "service": {
        "polished": ("Ladies and gentlemen, we'll shortly begin our service of complimentary drinks and snacks. Please have your "
                     "tray table ready as we come to your row.",),
        "cheerful": ("Who's hungry? The trolley's coming your way with drinks and snacks, on us. Tray tables down when you see it!",),
        "brisk": ("Drinks and snacks coming through now. Have your tray table ready.",),
    },
    "descent": {
        "polished": ("Ladies and gentlemen, we have started our descent towards {dest}. {local}{weather}Please take your seat, fasten "
                     "your belt, and stow your tray table. Thank you.",),
        "cheerful": ("Nearly there, everyone: we've started down towards {dest}! {local}{weather}Back to your seats, belts on, tables "
                     "up, and gather up whatever's made its way under the seat in front.",
                     "That's the top of the descent, folks. {dest} is coming up! {local}{weather}Time to put things away and buckle in.",),
        "warm": ("We're on our way down into {dest} now, everyone. {local}{weather}Please head back to your seat when you can and "
                 "fasten your belt. Thank you for your patience today.",),
        "brisk": ("Descending into {dest}. {local}{weather}Please return to your seat and fasten your belt.",),
    },
    "landing": {
        "polished": ("Ladies and gentlemen, we'll be landing shortly. Please check that your belt is fastened, your seat is upright, "
                     "your table is stowed and your blind is open, and put your bags under the seat ahead. The washrooms are now closed.",),
        "cheerful": ("Final stretch, folks! Belts on, seats upright, tables up, blinds open, bags under the seat in front.",
                     "Almost down, everyone. One last check: belt, seat, table, blind. Thanks!",),
        "brisk": ("Landing shortly. Belts, seats, tables, blinds.",),
    },
    "after_landing": {
        "polished": ("Welcome to {dest}, ladies and gentlemen. {local}{weather}Please stay in your seat with your belt fastened until the "
                     "aircraft has stopped and the sign is off, and open the overhead lockers with care.",),
        "cheerful": ("And we're down! Welcome to {dest}, everybody. {local}{weather}Belts stay on until the sign goes off. I can hear "
                     "those buckles, you know.",
                     "Welcome to {dest}, folks! {local}{weather}Hold tight until we've stopped at the gate and the sign's off. Your bags "
                     "aren't going anywhere without you.",),
        "warm": ("Welcome to {dest}, everyone. {local}{weather}Please keep your belt fastened until the sign is off, and have a look "
                 "around for anything you may have left.",),
        "brisk": ("{dest}. {local}{weather}Belts on until the sign is off.",),
    },
    "goodbye": {
        "polished": ("Thank you for choosing {airline} today, ladies and gentlemen. {members_bye}From all of the crew, we wish you a "
                     "pleasant onward journey, and we look forward to seeing you again.",),
        "cheerful": ("Thanks for putting up with us, everyone! {members_bye}Enjoy {dest}, mind your head on the way out, and come fly "
                     "{airline} again soon.",
                     "That's us! Thanks for flying {airline} today. {members_bye}Have a great time in {dest}, and please take all your "
                     "things with you. We've got enough phone chargers.",),
        "warm": ("Thank you for being with us today, everyone. {members_bye}We hope you have a lovely time in {dest}, and we'd love to "
                 "see you on board again.",),
        "brisk": ("Thanks for flying {airline}. {members_bye}Please take all your belongings.",),
    },
    "deboarded": {
        "polished": ("Captain, it's {name}. All passengers have left. Thank you, it was a pleasure.",),
        "cheerful": ("All gone, Captain! Only one lost scarf to report. Thanks for the ride.", "Cabin's clear, Captain! Nice flying. Same time tomorrow?",),
        "warm": ("Captain, it's {name}. Everyone's safely off. Thank you, that was a lovely flight.",),
        "brisk": ("Everyone's off.",),
    },
}.items():
    for _manner, _more in _wordings.items():
        LINES[_key][_manner] = (*LINES[_key].get(_manner, ()), *_more)

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
    "boarding_left": ("About {left} minutes of boarding to go, Captain.", "Another {left} minutes or so for the boarding.",),
    "on_time": ("We're on time so far, Captain.", "On time, as far as I can see.",),
    "behind": ("We're {minutes} minutes behind the plan, Captain.", "About {minutes} minutes late so far.",),
    "no_schedule": ("I don't have a departure time that fits today, Captain.",),
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
        "delay": "Mesdames et messieurs, nous vous prions de nous excuser pour ce retard au départ. Nous partirons dès "
                 "que possible. Merci de votre patience.",
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
        "delay": "Meine Damen und Herren, wir bitten die Verspätung unseres Abflugs zu entschuldigen. Wir starten, sobald "
                 "es möglich ist. Vielen Dank für Ihre Geduld.",
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
        "delay": "Señoras y señores, les pedimos disculpas por el retraso de nuestra salida. Saldremos en cuanto sea "
                 "posible. Gracias por su paciencia.",
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
        "delay": "Signore e signori, ci scusiamo per il ritardo della partenza. Partiremo non appena possibile. Grazie "
                 "per la vostra pazienza.",
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
        "delay": "Senhoras e senhores, pedimos desculpa pelo atraso da nossa partida. Partiremos assim que possível. "
                 "Obrigado pela vossa paciência.",
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
        "delay": "Dames en heren, onze excuses voor de vertraging van ons vertrek. We vertrekken zo snel mogelijk. Dank "
                 "u voor uw geduld.",
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
        "delay": "Szanowni Państwo, przepraszamy za opóźnienie naszego odlotu. Wyruszymy tak szybko, jak to możliwe. "
                 "Dziękujemy za cierpliwość.",
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
        "delay": "Mina damer och herrar, vi ber om ursäkt för förseningen av vår avgång. Vi åker så snart som möjligt. "
                 "Tack för ert tålamod.",
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
        "delay": "Дамы и господа, приносим извинения за задержку вылета. Мы отправимся, как только это будет возможно. "
                 "Благодарим за терпение.",
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
        "delay": "女士们，先生们，我们对航班延误深表歉意。我们将尽快出发。感谢您的耐心等待。",
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
