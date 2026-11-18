"""The cabin's announcements that were only made in English, in the other languages the crew speak (merged into
``cabin_lines.FOREIGN``), and the ones that are another's in them: the US doors call is the doors call."""

SAME_AS = {"doors_arm_us": "doors_arm", "doors_disarm_us": "doors_disarm", "seatbelt_off_generic": "seatbelt_off"}

MORE: dict[str, dict[str, str]] = {
    "de": {
        "night": "Meine Damen und Herren, für den Start dimmen wir das Kabinenlicht. Ihre Leselampe finden Sie in der Konsole über Ihnen.",
        "exit_row": "Wenn Sie an einem Notausgang sitzen, müssen Sie bereit und in der Lage sein, im Notfall zu helfen. Möchten Sie das nicht, sagen Sie es bitte einem von uns, dann finden wir einen anderen Platz für Sie.",
        "safety_more": "Bitte bleiben Sie angeschnallt, solange die Anschnallzeichen leuchten. Schalten Sie Telefone und andere Geräte jetzt bitte in den Flugmodus; größere Geräte wie Laptops müssen für Start und Landung verstaut sein. Das Rauchen ist an Bord verboten, auch in den Toiletten.",
        "seatbelt_again_off": "Meine Damen und Herren, die Anschnallzeichen sind wieder ausgeschaltet. Vielen Dank für Ihre Geduld.",
        "turbulence_service": "Wir unterbrechen den Service und setzen ihn fort, sobald es ruhiger ist.",
        "aircraft_talk": "Meine Damen und Herren, ein Wort zu unserem Flugzeug: Sie fliegen heute mit einer {aircraft}. Bitte bleiben Sie am Platz angeschnallt, auch wenn die Anschnallzeichen aus sind.",
    },
    "fr": {
        "night": "Mesdames et messieurs, nous allons baisser l'éclairage de la cabine pour le décollage. Votre liseuse se trouve au-dessus de vous.",
        "exit_row": "Si vous êtes assis à une rangée d'issue de secours, vous devez être disposé et apte à aider en cas d'urgence. Sinon, dites-le à l'un d'entre nous et nous vous trouverons une autre place.",
        "safety_more": "Veuillez garder votre ceinture attachée lorsque la consigne est allumée. Les téléphones et autres appareils doivent maintenant être en mode avion, et les plus grands, comme les ordinateurs portables, rangés pour le décollage et l'atterrissage. Il est interdit de fumer à bord, y compris dans les toilettes.",
        "seatbelt_again_off": "Mesdames et messieurs, la consigne lumineuse des ceintures est de nouveau éteinte. Merci de votre patience.",
        "turbulence_service": "Nous interrompons le service et le reprendrons dès que ce sera plus calme.",
        "aircraft_talk": "Mesdames et messieurs, un mot sur notre appareil : vous voyagez aujourd'hui à bord d'un {aircraft}. Même consigne éteinte, gardez votre ceinture attachée lorsque vous êtes assis.",
    },
    "es": {
        "night": "Señoras y señores, vamos a atenuar las luces de la cabina para el despegue. Si desea leer, tiene la luz de lectura en el panel sobre su asiento.",
        "exit_row": "Si está sentado en una fila de salida de emergencia, debe estar dispuesto y capacitado para ayudar en una emergencia. Si lo prefiere, díganoslo y le buscaremos otro asiento.",
        "safety_more": "Mantenga el cinturón abrochado siempre que la señal esté encendida. Ponga ahora los teléfonos y otros dispositivos en modo avión; los aparatos más grandes, como los portátiles, deben ir guardados durante el despegue y el aterrizaje. Está prohibido fumar a bordo, también en los aseos.",
        "seatbelt_again_off": "Señoras y señores, la señal de cinturones se ha vuelto a apagar. Gracias por su paciencia.",
        "turbulence_service": "Interrumpimos el servicio y lo continuaremos en cuanto haya más calma.",
        "aircraft_talk": "Señoras y señores, unas palabras sobre nuestro avión: hoy vuelan en un {aircraft}. Mientras estén sentados, mantengan el cinturón abrochado aunque la señal esté apagada.",
    },
    "it": {
        "night": "Signore e signori, abbasseremo le luci della cabina per il decollo. Se desiderate leggere, la luce di lettura si trova nel pannello sopra di voi.",
        "exit_row": "Se siete seduti in una fila di uscita di emergenza, dovete essere disposti e in grado di aiutare in caso di emergenza. Altrimenti ditelo a uno di noi e vi troveremo un altro posto.",
        "safety_more": "Tenete la cintura allacciata ogni volta che il segnale è acceso. Mettete ora telefoni e altri dispositivi in modalità aereo; gli apparecchi più grandi, come i portatili, vanno riposti per il decollo e l'atterraggio. È vietato fumare a bordo, anche nelle toilette.",
        "seatbelt_again_off": "Signore e signori, il segnale delle cinture è di nuovo spento. Grazie per la pazienza.",
        "turbulence_service": "Sospendiamo il servizio e lo riprenderemo appena sarà più tranquillo.",
        "aircraft_talk": "Signore e signori, due parole sul nostro aereo: oggi volate su un {aircraft}. Quando siete seduti, tenete la cintura allacciata anche a segnale spento.",
    },
    "pt": {
        "night": "Senhoras e senhores, vamos reduzir as luzes da cabine para a descolagem. Se quiser ler, tem a luz de leitura no painel por cima de si.",
        "exit_row": "Se estiver sentado numa fila de saída de emergência, tem de estar disposto e apto a ajudar numa emergência. Se preferir não o fazer, diga a um de nós e encontramos-lhe outro lugar.",
        "safety_more": "Mantenha o cinto apertado sempre que o sinal estiver aceso. Coloque agora telemóveis e outros aparelhos em modo de voo; os aparelhos maiores, como portáteis, devem ir arrumados na descolagem e na aterragem. É proibido fumar a bordo, incluindo nos lavabos.",
        "seatbelt_again_off": "Senhoras e senhores, o sinal de apertar cintos voltou a apagar-se. Obrigado pela vossa paciência.",
        "turbulence_service": "Vamos interromper o serviço e retomá-lo assim que estiver mais calmo.",
        "aircraft_talk": "Senhoras e senhores, uma palavra sobre o nosso avião: hoje voam num {aircraft}. Enquanto estiverem sentados, mantenham o cinto apertado, mesmo com o sinal desligado.",
    },
    "nl": {
        "night": "Dames en heren, we dimmen de cabineverlichting voor het vertrek. Wilt u lezen, dan vindt u het leeslampje in het paneel boven u.",
        "exit_row": "Zit u bij een nooduitgang, dan moet u bereid en in staat zijn om in een noodsituatie te helpen. Liever niet? Laat het een van ons weten, dan zoeken we een andere plaats voor u.",
        "safety_more": "Houd uw gordel vast zolang het lampje brandt. Zet telefoons en andere apparaten nu in vliegtuigmodus; grotere apparaten zoals laptops moeten bij het opstijgen en landen opgeborgen zijn. Roken is aan boord verboden, ook in de toiletten.",
        "seatbelt_again_off": "Dames en heren, het gordellampje is weer uit. Dank u voor uw geduld.",
        "turbulence_service": "We onderbreken de service en gaan verder zodra het rustiger is.",
        "aircraft_talk": "Dames en heren, een woord over ons toestel: u vliegt vandaag met een {aircraft}. Houd uw gordel vast als u zit, ook als het lampje uit is.",
    },
    "pl": {
        "night": "Szanowni Państwo, na czas startu przyciemnimy światła w kabinie. Lampkę do czytania znajdą Państwo w panelu nad fotelem.",
        "exit_row": "Osoby siedzące przy wyjściach awaryjnych muszą być gotowe i zdolne do pomocy w sytuacji awaryjnej. Jeśli wolą Państwo zmienić miejsce, prosimy powiedzieć o tym komuś z załogi.",
        "safety_more": "Prosimy mieć zapięte pasy zawsze, gdy świeci się sygnalizacja. Telefony i inne urządzenia należy teraz przełączyć w tryb samolotowy, a większe, takie jak laptopy, schować na czas startu i lądowania. Palenie na pokładzie jest zabronione, także w toaletach.",
        "seatbelt_again_off": "Szanowni Państwo, sygnalizacja zapięcia pasów została ponownie wyłączona. Dziękujemy za cierpliwość.",
        "turbulence_service": "Przerywamy serwis i wznowimy go, gdy tylko będzie spokojniej.",
        "aircraft_talk": "Szanowni Państwo, kilka słów o naszym samolocie: lecą dziś Państwo samolotem {aircraft}. Siedząc, prosimy mieć zapięte pasy, nawet gdy sygnalizacja jest wyłączona.",
    },
    "sv": {
        "night": "Mina damer och herrar, vi dämpar kabinbelysningen inför starten. Vill ni läsa finns läslampan i panelen ovanför er.",
        "exit_row": "Sitter ni vid en nödutgång måste ni vara villig och kapabel att hjälpa till i en nödsituation. Vill ni hellre inte, säg till någon av oss så hittar vi en annan plats åt er.",
        "safety_more": "Ha bältet fastspänt så länge skylten är tänd. Sätt nu telefoner och andra enheter i flygplansläge; större enheter som datorer ska vara undanstoppade vid start och landning. Rökning är förbjuden ombord, även på toaletterna.",
        "seatbelt_again_off": "Mina damer och herrar, bältesskylten är släckt igen. Tack för ert tålamod.",
        "turbulence_service": "Vi gör ett uppehåll i servicen och fortsätter så snart det är lugnare.",
        "aircraft_talk": "Mina damer och herrar, några ord om vårt flygplan: i dag flyger ni med en {aircraft}. Ha gärna bältet fastspänt när ni sitter, även när skylten är släckt.",
    },
    "ru": {
        "night": "Уважаемые пассажиры, на время взлёта мы приглушим свет в салоне. Лампа для чтения находится на панели над вами.",
        "exit_row": "Пассажиры у аварийных выходов должны быть готовы и способны помочь в аварийной ситуации. Если вы не хотите, сообщите кому-нибудь из экипажа, и мы подберём вам другое место.",
        "safety_more": "Пожалуйста, держите ремень застёгнутым, пока горит табло. Переведите телефоны и другие устройства в авиарежим; крупные устройства, например ноутбуки, должны быть убраны на время взлёта и посадки. Курение на борту запрещено, в том числе в туалетах.",
        "seatbelt_again_off": "Уважаемые пассажиры, табло «Застегните ремни» снова выключено. Спасибо за терпение.",
        "turbulence_service": "Мы прерываем обслуживание и продолжим, как только станет спокойнее.",
        "aircraft_talk": "Уважаемые пассажиры, несколько слов о нашем самолёте: сегодня вы летите на {aircraft}. Сидя в кресле, держите ремень застёгнутым, даже когда табло выключено.",
    },
    "zh": {
        "night": "女士们，先生们，起飞时我们将调暗客舱灯光。如需阅读，阅读灯在您头顶上方的面板上。",
        "exit_row": "坐在紧急出口座位的旅客，必须愿意并且能够在紧急情况下提供协助。如果您不愿意，请告诉我们，我们会为您调换座位。",
        "safety_more": "安全带指示灯亮着时，请系好安全带。现在请将手机和其他电子设备调至飞行模式，笔记本电脑等较大设备在起飞和着陆时必须收好。机上全程禁止吸烟，包括洗手间。",
        "seatbelt_again_off": "女士们，先生们，安全带指示灯再次熄灭。感谢您的耐心。",
        "turbulence_service": "我们暂停服务，等气流平稳后再继续。",
        "aircraft_talk": "女士们，先生们，介绍一下我们的飞机：今天您乘坐的是{aircraft}。入座时，即使安全带指示灯熄灭，也请系好安全带。",
    },
}

# What the landing was like, said back to the captain in their language ("Nice landing, too.").
LANDING: dict[str, dict[str, tuple[str, ...]]] = {
    "de": {"soft": ("Die Landung war übrigens wunderbar.", "Wunderschöne Landung. Ich habe kaum etwas gespürt."), "normal": ("Schöne Landung übrigens.", "Gute Landung."),
           "firm": ("Die Landung hat ein paar Leute aufgeweckt!", "Heute eine feste. Verschüttet hat niemand etwas. Fast niemand.")},
    "fr": {"soft": ("Au fait, l'atterrissage était superbe.", "Magnifique atterrissage. Je n'ai presque rien senti."), "normal": ("Bel atterrissage, au passage.", "Bon atterrissage."),
           "firm": ("Cet atterrissage a réveillé quelques passagers !", "Un peu ferme aujourd'hui. Personne n'a rien renversé. Ou presque.")},
    "es": {"soft": ("Por cierto, el aterrizaje ha sido precioso.", "Un aterrizaje precioso. Casi ni lo noté."), "normal": ("Buen aterrizaje, por cierto.", "Buen aterrizaje."),
           "firm": ("¡Ese aterrizaje ha despertado a unos cuantos!", "Hoy ha sido firme. Nadie ha derramado nada. Casi nadie.")},
    "it": {"soft": ("A proposito, l'atterraggio è stato splendido.", "Atterraggio bellissimo. Quasi non l'ho sentito."), "normal": ("Bell'atterraggio, tra l'altro.", "Buon atterraggio."),
           "firm": ("Quell'atterraggio ha svegliato un po' di gente!", "Un po' deciso oggi. Nessuno ha rovesciato niente. Quasi nessuno.")},
    "pt": {"soft": ("Já agora, a aterragem foi linda.", "Aterragem lindíssima. Quase nem senti."), "normal": ("Boa aterragem, já agora.", "Boa aterragem."),
           "firm": ("Essa aterragem acordou algumas pessoas!", "Hoje foi firme. Ninguém entornou nada. Quase ninguém.")},
    "nl": {"soft": ("Die landing was trouwens prachtig.", "Prachtige landing. Ik heb bijna niets gevoeld."), "normal": ("Mooie landing trouwens.", "Goede landing."),
           "firm": ("Die landing heeft een paar mensen wakker gemaakt!", "Een stevige vandaag. Niemand heeft iets gemorst. Bijna niemand.")},
    "pl": {"soft": ("Przy okazji, lądowanie było cudowne.", "Piękne lądowanie. Prawie nic nie było czuć."), "normal": ("Przy okazji, ładne lądowanie.", "Dobre lądowanie."),
           "firm": ("To lądowanie obudziło kilka osób!", "Dziś twardo. Nikt niczego nie rozlał. Prawie nikt.")},
    "sv": {"soft": ("Landningen var förresten underbar.", "Vacker landning. Jag kände knappt något."), "normal": ("Fin landning, förresten.", "Bra landning."),
           "firm": ("Den landningen väckte några stycken!", "En fast en i dag. Ingen spillde något. Nästan ingen.")},
    "ru": {"soft": ("Кстати, посадка была чудесная.", "Прекрасная посадка. Я почти ничего не почувствовал."), "normal": ("Кстати, хорошая посадка.", "Хорошая посадка."),
           "firm": ("Эта посадка разбудила пару пассажиров!", "Сегодня жёстковато. Никто ничего не пролил. Почти никто.")},
    "zh": {"soft": ("对了，刚才落地非常漂亮。", "落地很漂亮，我几乎没感觉到。"), "normal": ("落地也不错。", "落地很好。"),
           "firm": ("这次落地把几位乘客吵醒了！", "今天落得有点重。没人把东西洒了，几乎没人。")},
}

NOT_LANDED = {"de": "Fragen Sie mich nach der Landung noch einmal!", "fr": "Redemandez-moi une fois posés !", "es": "¡Pregúnteme otra vez cuando hayamos aterrizado!",
              "it": "Me lo richieda quando saremo atterrati!", "pt": "Pergunte-me outra vez quando tivermos aterrado!",
              "nl": "Vraag het me nog eens als we geland zijn!", "pl": "Proszę zapytać jeszcze raz po lądowaniu!", "sv": "Fråga mig igen när vi har landat!",
              "ru": "Спросите меня ещё раз после посадки!", "zh": "等落地后再问我吧！"}
