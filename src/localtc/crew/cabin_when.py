"""More of the cabin crew's words: each crew member's own wordings of the lines heard every flight, and lines for the
moment the flight is in. A crew member who said the same words flight after flight was a recording, and one who said
"good afternoon" at night, or "enjoy the sunshine" in the snow, wasn't looking out of the window.

``MORE``: key -> manner -> wordings, added to ``cabin_lines.LINES``.

``WHEN``: key -> condition -> manner -> wordings, said when the condition holds (``CabinCrew._conditions``); a line for
the moment is chosen over the everyday ones most of the time, never always. The conditions:

- ``night`` (dark where the aircraft is), ``morning`` (before noon, local);
- ``late`` (a quarter of an hour or more behind the plan's off-block time);
- ``full`` (nine seats in ten taken or more), ``quiet`` (half the seats or fewer);
- ``short`` (under an hour and a quarter), ``long`` (four hours or more);
- ``rain``, ``snow``, ``hot`` (30 degrees or more), ``cold`` (5 degrees or less): at the origin before departure, at
  the destination from the descent on;
- ``bumpy`` (the seatbelt sign came back on in the cruise).

The same placeholders as ``cabin_lines`` (``{dest}``, ``{name}``, ``{pax}``, ``{local}``, ``{weather}`` ...): a wording
naming one that isn't known for this flight isn't used.
"""

MORE: dict[str, dict[str, tuple[str, ...]]] = {
    "boarding": {
        "polished": ("{greeting}, ladies and gentlemen, and welcome on board this {airline} service to {dest}. Please take your "
                     "seat as soon as you can, with your hand luggage in the lockers above or under the seat in front.",),
        "cheerful": ("{greeting}, everyone, come on in! This is {airline} flight {flight} to {dest}. Find your seat, pop your bag "
                     "up top wheels first, and let's get everyone settled.",
                     "Hello and welcome aboard, folks! Flight {flight} to {dest}. The lockers are bigger than they look, "
                     "and so is your neighbour's patience, so let's keep that aisle moving.",
                     "{greeting}, all! Welcome aboard {airline} flight {flight} to {dest}. Quick tip: the faster we're "
                     "seated, the faster we're there. Bags up, coats off, smiles on."),
        "warm": ("{greeting}, and welcome. This is {airline} flight {flight} to {dest}. Please settle in at your own pace, and "
                 "if your bag won't fit, one of us will find it a home.",
                 "{greeting}, everyone. Welcome aboard flight {flight} to {dest}. We're so glad to have you with us. Take "
                 "your seat when you're ready, and give us a wave if you need anything."),
        "brisk": ("{greeting}. Flight {flight} to {dest}. Take your seat promptly, bags stowed, aisle clear.",
                  "Welcome aboard. Flight {flight}, {dest}. Seats quickly, please, so we leave on time."),
        "storyteller": ("{greeting}, and welcome aboard {airline} flight {flight}, taking you to {dest_about}. Find your seat "
                        "and get comfortable; I'll tell you more about the trip once we're on our way.",
                        "{greeting}, everyone, and welcome aboard flight {flight} to {dest}. As you find your seat, have a "
                        "look out of the window: this is your last view of here for a while."),
    },
    "welcome": {
        "polished": ("Ladies and gentlemen, good to have you with us. On behalf of {captain} and the crew, welcome aboard "
                     "{airline} flight {flight} to {dest}, with a flight time of {time}. {members}My name is {name}. Please "
                     "keep your seatbelt fastened while seated, and do let us know if there's anything you need.",),
        "cheerful": ("Hi again, everyone! Welcome aboard {airline} flight {flight} to {dest}. {Captain} says about {time} "
                     "today. {members}My name's {name}, I'm looking after the cabin, and yes, I'm always this cheerful. "
                     "It's the altitude.",
                     "Welcome aboard, folks! This is {airline} flight {flight}, heading for {dest}, about {time} in the "
                     "air. {members}I'm {name}, and along with the rest of the crew, I'm here to make sure the only "
                     "turbulence today is in the coffee."),
        "warm": ("Hello everyone, and thank you for choosing {airline} today. This is flight {flight} to {dest}, and {captain} "
                 "expects about {time} in the air. {members}I'm {name}, and if there's anything at all that would make "
                 "your flight more comfortable, please just ask.",),
        "brisk": ("Good to have you aboard {airline} flight {flight} to {dest}. {time} in the air. {members}{name}, cabin "
                  "manager. Belts fastened.",),
        "storyteller": ("Ladies and gentlemen, welcome aboard {airline} flight {flight}. In about {time} we'll be in "
                        "{dest_about}. {members}I'm {name}, your cabin manager, and if you're curious about anything on the "
                        "way, the aircraft, the route, the view, just ask.",),
    },
    "seatbelt_off": {
        "cheerful": ("We're up and cruising, everyone! The seatbelt sign is off, so stretch, wander, visit the washroom. "
                     "Just keep the belt on loosely when you're sitting down.",),
        "warm": ("The seatbelt sign is off now, everyone. Please make yourselves comfortable, and do keep your belt fastened "
                 "while seated, just to be safe.",),
        "brisk": ("Seatbelt sign off. Belts fastened while seated, please. Thank you.",),
        "storyteller": ("Ladies and gentlemen, the seatbelt sign is off. We're on our way to {dest_about}, and the "
                        "cabin is yours. Do keep your belt loosely fastened when you're in your seat.",),
    },
    "service": {
        "polished": ("Ladies and gentlemen, the cabin crew will shortly be serving drinks and a light snack. We'll come to "
                     "each row; please have your tray table ready.",),
        "cheerful": ("Snack time, everyone! The trolley's warming up its wheels. Tray tables down when it reaches you, and "
                     "no, there's no prize for the fastest drink order. But there could be.",),
        "warm": ("Ladies and gentlemen, we'll be coming round with drinks and snacks in a moment. If you'd like something "
                 "special, or there's an allergy we should know about, please just tell us.",),
        "brisk": ("Drinks service starting now. Tray tables down when we reach you.",),
    },
    "descent": {
        "polished": ("Ladies and gentlemen, we are beginning our descent into {dest}. {local}{weather}Please return to your "
                     "seat and fasten your seatbelt, stow your tray table and bring your seat back upright.",),
        "cheerful": ("Okay folks, we're heading down to {dest}! {local}{weather}Seats up, belts on, tables away, and if you "
                     "borrowed your neighbour's armrest, now's the time to give it back.",),
        "warm": ("Ladies and gentlemen, we're on our way down into {dest}. {local}{weather}When you're ready, please return to "
                 "your seat and fasten your belt. Thank you for flying with us today.",),
        "brisk": ("Descending into {dest}. {local}{weather}Back to your seats, belts on, tables stowed.",),
        "storyteller": ("Ladies and gentlemen, we've started down towards {dest_about}. {local}{weather}Please take your "
                        "seats, and if you're by a window, it's worth a look.",),
    },
    "after_landing": {
        "polished": ("Ladies and gentlemen, welcome to {dest}. {local}{weather}Please stay seated, seatbelt fastened, until "
                     "we've reached the gate and the seatbelt sign is off.",),
        "cheerful": ("And we're down! Welcome to {dest}, everyone. {local}{weather}Stay put with your belt on until we're at "
                     "the gate and the sign goes off. I know, so close.",),
        "warm": ("Welcome to {dest}, everyone. {local}{weather}Please stay in your seat until the seatbelt sign is off, and "
                 "take your time collecting your things.",),
        "brisk": ("Welcome to {dest}. {local}{weather}Remain seated, belts on, until the sign is off.",),
    },
    "goodbye": {
        "polished": ("Ladies and gentlemen, on behalf of {captain} and the crew, thank you for flying {airline}. "
                     "{members_bye}Please check around your seat for your belongings. We wish you a pleasant day in {dest}.",),
        "cheerful": ("That's a wrap, folks! Thanks for flying {airline} with us. {members_bye}Check your seat pocket, grab "
                     "everything you brought, and enjoy {dest}!",),
        "warm": ("Thank you all for flying with us today. {members_bye}Take care on the way out, and we hope {dest} treats "
                 "you kindly.",),
        "brisk": ("Thank you for flying {airline}. {members_bye}Take all your belongings. Goodbye.",),
        "storyteller": ("Thank you for flying {airline}, ladies and gentlemen. {members_bye}Whatever you're in {dest} for, "
                        "we hope it's a good one, and that we'll see you again soon.",),
    },
    "secure_takeoff": {
        "polished": ("Flight deck, {name}. Cabin secure for departure.",),
        "cheerful": ("{name} here! Cabin's secure, everyone's strapped in and looking keen.",),
        "warm": ("Captain, cabin's all secure for takeoff. Everyone's settled.",),
        "brisk": ("Cabin secure for takeoff.",),
        "storyteller": ("Captain, it's {name}. Cabin's secure for takeoff, and the window seats are very excited.",),
    },
    "secure_landing": {
        "polished": ("Flight deck, {name}. The cabin is secure for landing.",),
        "cheerful": ("Captain, {name}. Cabin's secure for landing. All tray tables up, all phones down. Mostly.",),
        "warm": ("Captain, it's {name}. We're all secure for landing back here.",),
        "storyteller": ("Captain, {name} here. Cabin's secure for landing, and everyone's looking out of the windows.",),
    },
    "boarded": {
        "polished": ("Captain, {name}. All {pax} passengers are on board and seated. Ready for the doors when you are.",),
        "cheerful": ("Captain, {name} here! That's the last of them: {pax} on board, bags all squeezed in somehow. Ready to "
                     "close up when you are.",),
        "warm": ("Captain, it's {name}. All {pax} of our passengers are on and settled. We're ready whenever you are.",),
        "brisk": ("{pax} on board. Doors when ready.",),
        "storyteller": ("Captain, {name}. {pax} on board and the bags are in. Ready when you are.",),
    },
    "deboarded": {
        "polished": ("Captain, {name} here. The cabin is clear. Thank you, and see you on the next one.",),
        "cheerful": ("Captain, last one's off! Thanks for a great flight, see you next time.",),
        "warm": ("Captain, everyone's off safely. Thank you for a lovely flight.",),
        "storyteller": ("Captain, that's everyone off. Lots of happy faces on the way out, too.",),
    },
}

WHEN: dict[str, dict[str, dict[str, tuple[str, ...]]]] = {
    "boarding": {
        "night": {
            "polished": ("Good evening, ladies and gentlemen, and welcome aboard {airline} flight {flight} to {dest}. Please "
                         "find your seat and stow your bags, and keep your voices down for those who'd like to rest.",),
            "cheerful": ("Good evening, night owls! Welcome aboard {airline} flight {flight} to {dest}. Bags up, seats "
                         "found, and if you're planning a nap, I won't take it personally.",),
            "warm": ("Good evening, everyone, and welcome aboard flight {flight} to {dest}. It's been a long day for many of "
                     "you, so settle in and get comfortable. We'll keep things calm and quiet.",),
            "brisk": ("Good evening. Flight {flight} to {dest}. Seats and bags quickly, please. A quiet cabin tonight.",),
            "storyteller": ("Good evening, and welcome aboard flight {flight} to {dest_about}. A night flight is a lovely "
                            "thing: the lights below, the stars above. Find your seat and enjoy it.",),
        },
        "morning": {
            "polished": ("Good morning, ladies and gentlemen, and welcome aboard {airline} flight {flight} to {dest}. Please "
                         "take your seat and stow your hand luggage.",),
            "cheerful": ("Good morning, everyone! Coffee's coming later, I promise. Welcome aboard {airline} flight {flight} "
                         "to {dest}. Bags up, seats found, eyes open. Two out of three is fine.",),
            "warm": ("Good morning, and welcome aboard flight {flight} to {dest}. An early start for a lot of you, so take "
                     "your time and get comfortable.",),
            "brisk": ("Good morning. Flight {flight} to {dest}. Seats and bags, please.",),
            "storyteller": ("Good morning, and welcome aboard flight {flight} to {dest_about}. The best views are in the "
                            "morning, so if you've got a window seat, you're in luck.",),
        },
        "late": {
            "polished": ("{greeting}, ladies and gentlemen, and welcome aboard {airline} flight {flight} to {dest}. We're "
                         "sorry for the late start. To help us get away as soon as possible, please take your seat promptly.",),
            "cheerful": ("{greeting}, everyone, and thanks for your patience! Flight {flight} to {dest}, finally boarding. "
                         "Let's make up some time: bags up, bums down. Seats, I mean seats.",),
            "warm": ("{greeting}, and thank you so much for waiting. Welcome aboard flight {flight} to {dest}. We know it's "
                     "been a delay, so let's get you settled and on your way.",),
            "brisk": ("{greeting}. Flight {flight} to {dest}. We're running late: seats quickly, please.",),
            "storyteller": ("{greeting}, and welcome aboard flight {flight} to {dest_about}. Sorry for the wait. Let's get "
                            "you seated, and we'll do our best to make up the time.",),
        },
        "full": {
            "polished": ("{greeting}, ladies and gentlemen. Today's flight to {dest} is full, so please place only your "
                         "larger bag in the overhead lockers and smaller items under the seat in front of you.",),
            "cheerful": ("{greeting}, everyone! Full flight today, so it's locker Tetris time: big bags up, wheels first, "
                         "small bags under the seat. Teamwork!",),
            "warm": ("{greeting}, and welcome aboard. We're completely full today, so if you don't find space for your bag, "
                     "please don't worry. Just let one of us know and we'll take care of it.",),
            "brisk": ("{greeting}. Full flight. One bag up, one under the seat. Thank you.",),
            "storyteller": ("{greeting}, and welcome aboard a full flight to {dest}. Everyone's heading that way today, it "
                            "seems. Bags up, small items under the seat, please.",),
        },
        "quiet": {
            "cheerful": ("{greeting}, everyone, welcome aboard flight {flight} to {dest}! Plenty of room today, so no "
                         "locker battles. Please do sit in your own seat until we're airborne, though.",),
            "warm": ("{greeting}, and welcome. It's a quiet flight today, so there's lots of room for your things. Please take "
                     "your assigned seat for now.",),
            "brisk": ("{greeting}. Flight {flight} to {dest}. Light load today. Take your assigned seat for departure.",),
        },
        "rain": {
            "cheerful": ("{greeting}, everyone, and welcome in out of the rain! Flight {flight} to {dest}. Shake off, sit down, "
                         "and let's get you somewhere drier.",),
            "warm": ("{greeting}, and welcome aboard. Come in out of the wet. If your coat's soaked, we can hang it for you.",),
            "storyteller": ("{greeting}, and welcome aboard flight {flight} to {dest}. Leaving the rain behind today: give it "
                            "ten minutes and we'll be above it all.",),
        },
        "snow": {
            "cheerful": ("{greeting}, everyone! Welcome aboard flight {flight} to {dest}. Stamp the snow off, get cosy, and "
                         "we'll have you on your way.",),
            "warm": ("{greeting}, and welcome aboard. It's cold out there, so get yourselves warm and comfortable.",),
        },
        "hot": {
            "cheerful": ("{greeting}, everyone, and welcome to the air conditioning! Flight {flight} to {dest}. Water's on "
                         "its way once we're up.",),
            "warm": ("{greeting}, and welcome aboard. It's a hot one today, so if you need some water before we go, please "
                     "just ask.",),
        },
        "cold": {
            "cheerful": ("{greeting}, everyone! Brrr. Welcome aboard flight {flight} to {dest}. It's warmer in here, I "
                         "promise. Bags up, coats on your lap or up top.",),
            "warm": ("{greeting}, and welcome in from the cold. If you'd like a blanket, just ask.",),
        },
    },
    "welcome": {
        "night": {
            "polished": ("Good evening, ladies and gentlemen. On behalf of {captain} and the crew, welcome aboard {airline} "
                         "flight {flight} to {dest}. Our flight time tonight is {time}. {members}My name is {name}. After "
                         "takeoff we'll dim the cabin lights; your reading light is in the panel above you.",),
            "cheerful": ("Good evening, everyone, and welcome aboard {airline} flight {flight} to {dest}. About {time} "
                         "tonight. {members}I'm {name}, and I'll keep the announcements short so the nappers can nap.",),
            "warm": ("Good evening, everyone. Welcome aboard {airline} flight {flight} to {dest}, about {time} tonight. "
                     "{members}I'm {name}. If you'd like to rest, we'll keep the cabin quiet and the lights low for you.",),
            "brisk": ("Good evening. {airline} flight {flight} to {dest}, {time}. {members}I'm {name}. Lights dimmed after "
                      "takeoff.",),
            "storyteller": ("Good evening, and welcome aboard {airline} flight {flight}. Tonight we'll be flying to "
                            "{dest_about}, about {time}. {members}I'm {name}. Look out for the city lights as we climb.",),
        },
        "morning": {
            "polished": ("Good morning, ladies and gentlemen. On behalf of {captain} and the crew, welcome aboard {airline} "
                         "flight {flight} to {dest}. Our flight time this morning is {time}. {members}My name is {name}, "
                         "and I'm your cabin manager.",),
            "cheerful": ("Good morning, early birds! Welcome aboard {airline} flight {flight} to {dest}, about {time}. "
                         "{members}I'm {name}, and the coffee is coming, I promise.",),
            "warm": ("Good morning, everyone, and welcome aboard flight {flight} to {dest}. About {time} today. {members}I'm "
                     "{name}. If you didn't manage breakfast, we'll look after you once we're up.",),
        },
        "late": {
            "polished": ("Ladies and gentlemen, welcome aboard {airline} flight {flight} to {dest}, and thank you for your "
                         "patience with today's delay. Our flight time is {time}. {members}My name is {name}. We'll do our "
                         "best to get you there as soon as possible.",),
            "cheerful": ("Welcome aboard, everyone, and thanks for hanging in there! {airline} flight {flight} to {dest}, "
                         "about {time}. {members}I'm {name}, and we're going to make up for the wait with excellent "
                         "service. And maybe a tailwind.",),
            "warm": ("Welcome aboard, everyone, and thank you for being so patient today. This is flight {flight} to {dest}, "
                     "about {time}. {members}I'm {name}. If the delay has put a connection at risk, please let us know.",),
            "brisk": ("Welcome aboard flight {flight} to {dest}. Apologies for the delay. Flight time {time}. {members}I'm "
                      "{name}.",),
        },
        "long": {
            "polished": ("Ladies and gentlemen, on behalf of {captain} and the crew, welcome aboard {airline} flight "
                         "{flight} to {dest}. We'll be with you for {time} today. {members}My name is {name}. We'll be "
                         "serving meals and drinks during the flight, and there's water in the galley any time.",),
            "cheerful": ("Welcome aboard, everyone! {airline} flight {flight} to {dest}: {time}, so get comfy. {members}I'm "
                         "{name}. Meals, drinks, and a lot of my charm are all on the way.",),
            "storyteller": ("Ladies and gentlemen, welcome aboard {airline} flight {flight}. We have {time} together on the "
                            "way to {dest_about}, which is plenty of time for a meal, a film and a nap. {members}I'm {name}.",),
        },
        "short": {
            "polished": ("Ladies and gentlemen, welcome aboard {airline} flight {flight} to {dest}. It's a short one today, "
                         "{time}. {members}My name is {name}. Please keep your seatbelt fastened throughout.",),
            "cheerful": ("Welcome aboard, everyone! Flight {flight} to {dest}, just {time}, so don't get too comfortable. "
                         "{members}I'm {name}, and I'll be quick too.",),
            "brisk": ("Welcome aboard flight {flight} to {dest}. Short one, {time}. {members}Belts on.",),
        },
        "full": {
            "cheerful": ("Welcome aboard, everyone! Every seat's taken on {airline} flight {flight} to {dest} today, so say "
                         "hello to your neighbour. About {time}. {members}I'm {name}.",),
            "warm": ("Ladies and gentlemen, welcome aboard a full flight to {dest}. About {time}. {members}I'm {name}. "
                     "Thank you for helping us get everyone settled.",),
        },
    },
    "seatbelt_off": {
        "night": {
            "polished": ("Ladies and gentlemen, the seatbelt sign is off. We've dimmed the cabin lights for those who'd like "
                         "to rest; your reading light is above you. Please keep your belt fastened while seated, visible over "
                         "your blanket.",),
            "cheerful": ("The seatbelt sign is off, everyone. Lights are low, so tiptoe if you're heading to the washroom, "
                         "and keep that belt on over your blanket if you're sleeping.",),
            "warm": ("The seatbelt sign is off now. We've turned the lights down so you can rest. If you sleep, please keep "
                     "your belt fastened over your blanket so we don't have to wake you.",),
            "brisk": ("Seatbelt sign off. Lights dimmed. Belts on over blankets while seated.",),
        },
        "short": {
            "polished": ("Ladies and gentlemen, the seatbelt sign is off, though it's a short flight and it won't be for "
                         "long. Please keep your seatbelt fastened while seated.",),
            "cheerful": ("Seatbelt sign's off! Quick, stretch now, because on a flight this short it'll be back on before "
                         "you know it.",),
            "brisk": ("Seatbelt sign off, briefly. Short flight. Stay seated if you can.",),
        },
        "long": {
            "polished": ("Ladies and gentlemen, the seatbelt sign is off. On a flight of this length, do get up and stretch "
                         "now and then, and drink plenty of water. Please keep your belt fastened while seated.",),
            "warm": ("The seatbelt sign is off. It's a long one today, so please do move about, stretch your legs, and ask us "
                     "for water whenever you like.",),
            "storyteller": ("The seatbelt sign is off, everyone. We've got {time} together, so make yourselves at home. "
                            "There's water in the galley, and a good view from the back window.",),
        },
    },
    "service": {
        "night": {
            "polished": ("Ladies and gentlemen, we'll be coming through the cabin quietly with drinks and a light snack. If "
                         "you'd rather sleep, simply leave your tray table up.",),
            "cheerful": ("Night service, everyone: drinks and snacks, delivered in a whisper. Leave your table up if you're "
                         "snoozing and we'll tiptoe past.",),
            "warm": ("We'll be round shortly with a drink and a snack. If you're resting, we won't disturb you; just call us "
                     "if you'd like something later.",),
        },
        "morning": {
            "polished": ("Ladies and gentlemen, we'll shortly be serving breakfast and hot drinks. Please lower your tray table "
                         "when we reach your row.",),
            "cheerful": ("Coffee's coming, everyone! Breakfast and hot drinks are on their way. Tray tables down, eyes "
                         "open, the good stuff is coming.",),
            "warm": ("Good morning again. We'll be round with breakfast and hot drinks in a moment. If you'd like tea, "
                     "coffee or juice, just tell us.",),
        },
        "short": {
            "polished": ("Ladies and gentlemen, as it's a short flight, we'll be offering a quick drinks service. Please have "
                         "your tray table ready.",),
            "cheerful": ("Speed round, everyone! A quick drink before we start down. Tables down, orders ready.",),
            "brisk": ("Quick drinks service. Short flight. Tables down.",),
        },
    },
    "descent": {
        "night": {
            "polished": ("Ladies and gentlemen, we've begun our descent into {dest}. {local}{weather}We'll be turning the "
                         "cabin lights up shortly. Please return to your seat and fasten your seatbelt.",),
            "cheerful": ("Wakey wakey, everyone! We're on our way down into {dest}. {local}{weather}Lights are coming up, "
                         "seats are going up, belts are going on.",),
            "warm": ("Ladies and gentlemen, we're starting our descent into {dest}. {local}{weather}We'll bring the lights up "
                     "gently. Please take your time, and fasten your seatbelt.",),
            "storyteller": ("Ladies and gentlemen, we've started down towards {dest_about}. {local}{weather}If you're by a "
                            "window, watch for the lights of the city.",),
        },
        "rain": {
            "cheerful": ("Folks, we're heading down into {dest}. {local}{weather}Umbrellas at the ready! Seats up, belts on.",),
            "warm": ("We're on our way down into {dest}. {local}{weather}It's a wet one down there, so take care on the steps. "
                     "Please fasten your seatbelt.",),
            "brisk": ("Descending into {dest}. {local}{weather}Wet on the ground. Belts on, seats up.",),
        },
        "snow": {
            "cheerful": ("We're on our way down into {dest}, everyone. {local}{weather}Snow on the ground, so coats on "
                         "before you step out! Seats up, belts on for now.",),
            "warm": ("Ladies and gentlemen, we're descending into {dest}. {local}{weather}It's snowy down there, so wrap up "
                     "warm and take care on the way out. Please fasten your seatbelt.",),
        },
        "hot": {
            "cheerful": ("Heading down into {dest}, everyone! {local}{weather}Sunglasses ready, it's a hot one. Belts on, "
                         "seats up.",),
            "warm": ("We're on our way down into {dest}. {local}{weather}It's hot down there, so do drink some water before "
                     "you go. Please fasten your seatbelt.",),
        },
        "cold": {
            "cheerful": ("We're on our way down into {dest}, folks! {local}{weather}Coats out of the lockers soon, it's "
                         "chilly. Belts on, seats up.",),
            "warm": ("We've started our descent into {dest}. {local}{weather}It's cold out there, so keep a warm layer handy. "
                     "Please fasten your seatbelt.",),
        },
        "late": {
            "polished": ("Ladies and gentlemen, we've begun our descent into {dest}. {local}{weather}Thank you again for your "
                         "patience today. Please return to your seat and fasten your seatbelt.",),
            "warm": ("We're on our way down into {dest}. {local}{weather}Thank you for bearing with us today. If you have a "
                     "connection, the ground staff will help you on arrival.",),
        },
        "bumpy": {
            "cheerful": ("We're heading down into {dest}, everyone. {local}{weather}Thanks for riding out those bumps with "
                         "us. Seats up, belts on, nearly there.",),
            "warm": ("Ladies and gentlemen, we've started our descent into {dest}. {local}{weather}Thank you for staying so "
                     "calm through the bumpy bits. Please keep your belt fastened.",),
        },
    },
    "after_landing": {
        "night": {
            "polished": ("Ladies and gentlemen, welcome to {dest}. {local}{weather}Please remain seated until the seatbelt sign "
                         "is off. Thank you for flying with us tonight.",),
            "cheerful": ("Welcome to {dest}, night owls! {local}{weather}Stay seated until the sign's off, and then it's "
                         "bed time for all of us.",),
            "warm": ("Welcome to {dest}, everyone. {local}{weather}Please stay seated until the seatbelt sign is off, and "
                     "travel home safely tonight.",),
        },
        "morning": {
            "polished": ("Ladies and gentlemen, good morning and welcome to {dest}. {local}{weather}Please stay seated until "
                         "the seatbelt sign is switched off.",),
            "cheerful": ("Good morning, {dest}! And good morning, everyone. {local}{weather}Stay seated until the sign's off, "
                         "then go and enjoy the day.",),
        },
        "rain": {
            "cheerful": ("Welcome to {dest}, everyone! {local}{weather}Bit of rain, but we brought you here safe and dry. Stay "
                         "seated until the sign's off.",),
            "warm": ("Welcome to {dest}. {local}{weather}It's wet out there, so take care on the way. Please stay seated until "
                     "the seatbelt sign is off.",),
        },
        "snow": {
            "cheerful": ("Welcome to snowy {dest}, everyone! {local}{weather}Coats on before you step out. Stay seated until "
                         "the sign's off.",),
            "warm": ("Welcome to {dest}. {local}{weather}It's snowy and slippery out there, so please take care. Stay seated "
                     "until the seatbelt sign is off.",),
        },
        "hot": {
            "cheerful": ("Welcome to {dest}, everyone! {local}{weather}It's a scorcher, so enjoy that air conditioning while "
                         "it lasts. Stay seated until the sign's off.",),
        },
        "cold": {
            "cheerful": ("Welcome to {dest}, everyone! {local}{weather}It's a cold one, so coats on. Stay seated until the "
                         "sign's off.",),
        },
        "late": {
            "polished": ("Ladies and gentlemen, welcome to {dest}. {local}{weather}Thank you for your patience with today's "
                         "delay. Please stay seated until the seatbelt sign is off.",),
            "warm": ("Welcome to {dest}, everyone. {local}{weather}We're sorry we're late. If you have a connection, the "
                     "ground staff will be there to help.",),
        },
    },
    "goodbye": {
        "night": {
            "polished": ("Ladies and gentlemen, thank you for flying {airline} tonight. {members_bye}Please take all your "
                         "belongings, and have a safe journey home.",),
            "cheerful": ("That's us, everyone! Thanks for flying {airline} tonight. {members_bye}Get home safe, and get some "
                         "sleep. We will!",),
            "warm": ("Thank you for flying with us tonight. {members_bye}Take care getting home, and good night.",),
            "brisk": ("Thank you for flying {airline}. {members_bye}Good night.",),
        },
        "morning": {
            "polished": ("Ladies and gentlemen, thank you for flying {airline} this morning. {members_bye}We wish you a "
                         "pleasant day in {dest}.",),
            "cheerful": ("Thanks for flying {airline}, everyone! {members_bye}Go and have a brilliant day in {dest}.",),
        },
        "late": {
            "polished": ("Ladies and gentlemen, thank you for flying {airline}, and for your patience today. {members_bye}We "
                         "hope to welcome you on board again soon.",),
            "warm": ("Thank you for being so patient with us today. {members_bye}We hope the rest of your day goes smoothly.",),
        },
        "long": {
            "cheerful": ("You made it, everyone! Thanks for spending {time} with us on {airline}. {members_bye}Enjoy {dest}!",),
            "warm": ("Thank you for flying with us today. It's been a long one, so take care of yourselves. {members_bye}",),
        },
        "rain": {
            "cheerful": ("Thanks for flying {airline}, everyone! {members_bye}Don't forget your umbrella, it's wet out there.",),
        },
        "snow": {
            "warm": ("Thank you for flying with us. {members_bye}It's slippery out there, so please take care.",),
        },
    },
    "boarded": {
        "full": {
            "cheerful": ("Captain, {name}! Full house: {pax} on board, every locker stuffed. Ready to close up when you are.",),
            "polished": ("Captain, {name}. Boarding complete, a full flight: {pax} passengers. Ready for door closure.",),
            "warm": ("Captain, it's {name}. We're full today, {pax} on board, and everyone's settled. Ready when you are.",),
        },
        "quiet": {
            "cheerful": ("Captain, {name} here. Just {pax} on board today, so plenty of elbow room. Ready when you are.",),
            "polished": ("Captain, {name}. Boarding complete, {pax} passengers. A light load today. Ready for the doors.",),
        },
        "late": {
            "polished": ("Captain, {name}. Boarding is complete, {pax} on board. Ready for the doors whenever you are.",),
            "cheerful": ("Captain, {name}! Everyone's finally on, {pax} of them. Let's go make up some time!",),
            "warm": ("Captain, it's {name}. All {pax} are on and settled, ready when you are. They've been very patient.",),
        },
    },
    "secure_takeoff": {
        "night": {
            "warm": ("Captain, cabin's secure for takeoff. Lights are dimmed back here.",),
            "cheerful": ("{name} here! Cabin's secure, lights are low, everyone's tucked in.",),
        },
    },
    "secure_landing": {
        "bumpy": {
            "warm": ("Captain, it's {name}. Cabin's secure for landing. Everyone's been very good about the bumps.",),
            "cheerful": ("Captain, {name}. Cabin's secure, and everyone's survived the rollercoaster. Over to you!",),
        },
        "night": {
            "polished": ("Flight deck, {name}. Cabin secure for landing, lights are up.",),
        },
    },
    "deboarded": {
        "late": {
            "warm": ("Captain, everyone's off. A long day, but they were all very understanding. Thank you.",),
            "cheerful": ("Captain, cabin's empty! Late, but in one piece. See you next time.",),
        },
        "night": {
            "cheerful": ("Captain, last one's off. Bed time! Thanks for a good one.",),
            "warm": ("Captain, everyone's off. Thank you, and get some rest.",),
        },
        "long": {
            "polished": ("Captain, {name}. The cabin is clear. Thank you for a long but lovely flight.",),
        },
    },
}
