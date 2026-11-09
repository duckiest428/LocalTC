"""The LocalTC voice library: who speaks for each accent (``accents.py``), a woman's voices and a man's, all Kokoro.

Kokoro's own voices of a language speak its accent: French, Spanish, Italian, Brazilian Portuguese, Hindi, Japanese
and Mandarin (a voice of the language speaking English is the most natural accent there is). Where Kokoro has none,
the nearest voices carry the accent's sounds: a blend of two ("a+b", 60 % a) is a voice of its own, and it gives the
regions more people than Kokoro has speakers. Every voice here was heard through the radio by Whisper
(``localtc tts accents``).

The copilot talking with you in your language (``languages.py``) speaks it with Kokoro where Kokoro speaks it well
(``NATIVE``: measured by Whisper small reading it back) and with that language's Piper voices elsewhere.
"""

from localtc.tts.accents import Accent

# Kokoro's English (the pools before accents, kept for "" and the English-speaking world), best first by Kokoro's own
# grades (D and below left out, but for a blend). Never af_nicole: it whispers, and a pilot whispering on the radio
# sounded awful.
US = {"F": ("af_heart", "af_bella", "af_aoede", "af_kore", "af_sarah", "af_nova", "af_alloy", "af_heart+af_sarah",
            "af_bella+af_kore"),
      "M": ("am_michael", "am_fenrir", "am_puck", "am_michael+am_fenrir", "am_puck+am_michael", "am_fenrir+am_puck",
            "am_michael+am_echo", "am_fenrir+am_onyx", "am_puck+am_eric", "am_michael+am_liam")}
GB = {"F": ("bf_emma", "bf_isabella", "bf_emma+bf_isabella", "bf_isabella+bf_alice", "bf_emma+bf_lily"),
      "M": ("bm_george", "bm_fable", "bm_george+bm_fable", "bm_fable+bm_lewis", "bm_george+bm_daniel", "bm_lewis")}

# Continental Europe without a Kokoro voice of its own: its Romance voices and the British, blended.
# (Four men's voices were too few: at Zurich the purser, ground and two other crews all had em_alex+bm_george.)
EUROPE = {"F": ("ff_siwis+if_sara", "bf_isabella+ff_siwis", "if_sara+bf_emma", "ef_dora+bf_isabella", "bf_emma+ff_siwis",
                "if_sara+bf_alice", "ff_siwis+bf_lily"),
          "M": ("em_alex+bm_george", "bm_george+em_alex", "pm_alex+bm_george", "em_alex+bm_fable", "bm_lewis+em_alex",
                "im_nicola+bm_george", "em_alex+bm_daniel", "bm_daniel+pm_alex", "em_santa+bm_fable",
                "bm_fable+im_nicola", "pm_santa+bm_lewis")}
# Kokoro's Mandarin voices speak English with an accent of their own, its women's hard to follow through the radio
# alone (Whisper lost a third of the words, xiaoyi more): blended with an English voice they keep their timbre and come
# through (a fifth or less).
CHINESE = {"F": ("zf_xiaoxiao+af_kore", "zf_xiaoni+bf_emma", "zf_xiaobei+af_heart", "zf_xiaobei"),
           "M": ("zm_yunjian", "zm_yunjian+zm_yunyang", "zm_yunyang", "zm_yunxi")}
JAPANESE = {"F": ("jf_gongitsune+bf_emma", "jf_nezumi", "jf_gongitsune", "jf_nezumi+af_heart", "jf_tebukuro"),
            "M": ("jm_kumo", "jm_kumo+zm_yunjian", "jm_kumo+zm_yunyang")}
SOUTH_ASIAN = {"F": ("hf_alpha", "hf_beta", "hf_alpha+hf_beta"), "M": ("hm_omega", "hm_psi", "hm_omega+hm_psi")}
EAST_ASIAN = {"F": ("zf_xiaoni+bf_emma", "jf_nezumi+af_kore", "zf_xiaoxiao+af_kore"),
              "M": ("jm_kumo+zm_yunjian", "zm_yunyang+jm_kumo", "zm_yunjian+jm_kumo")}
SOUTHEAST_ASIAN = {"F": ("hf_beta+zf_xiaoni", "hf_alpha+zf_xiaobei", "zf_xiaoni+bf_emma"),
                   "M": ("zm_yunjian+hm_psi", "hm_omega+zm_yunyang", "zm_yunjian+em_alex")}
MIDDLE_EAST = {"F": ("hf_beta+ef_dora", "hf_alpha+if_sara", "ef_dora+hf_beta"),
               "M": ("hm_omega+em_alex", "hm_psi+im_nicola", "em_alex+hm_omega")}
AFRICAN = {"F": ("af_kore+hf_beta", "af_aoede+pf_dora", "bf_alice+hf_alpha"),
           "M": ("am_onyx+hm_omega", "am_echo+pm_alex", "bm_lewis+am_onyx")}

VOICES: dict[str, dict[str, tuple[str, ...]]] = {
    "us": US, "ca": US, "gb": GB, "ie": GB, "au": GB, "za": GB,
    "in": SOUTH_ASIAN,
    "sg": CHINESE,
    "ph": SOUTHEAST_ASIAN,
    "eaf": AFRICAN, "waf": AFRICAN,
    "fr": {"F": ("ff_siwis", "ff_siwis+bf_isabella", "ff_siwis+if_sara"),
           "M": ("em_alex", "em_alex+bm_george", "pm_alex")},
    "de": EUROPE, "nl": EUROPE, "nordic": EUROPE, "fi": EUROPE, "ru": EUROPE, "cee": EUROPE, "gr": EUROPE,
    "es": {"F": ("ef_dora", "ef_dora+if_sara", "ef_dora+pf_dora"),
           "M": ("em_alex", "em_santa", "em_alex+im_nicola")},
    "latam": {"F": ("ef_dora", "ef_dora+pf_dora", "pf_dora+ef_dora"),
              "M": ("em_alex", "em_alex+pm_alex", "pm_alex+em_alex")},
    "pt": {"F": ("pf_dora", "pf_dora+ef_dora"), "M": ("pm_alex", "pm_santa", "pm_alex+em_alex")},
    "br": {"F": ("pf_dora", "pf_dora+ef_dora", "pf_dora+if_sara"), "M": ("pm_alex", "pm_santa", "pm_alex+em_alex")},
    "it": {"F": ("if_sara", "if_sara+ef_dora", "if_sara+ff_siwis"), "M": ("im_nicola", "im_nicola+em_alex",
                                                                         "im_nicola+pm_alex")},
    "tr": MIDDLE_EAST, "me": MIDDLE_EAST, "fa": MIDDLE_EAST, "il": EUROPE,
    "zh": CHINESE, "ja": JAPANESE, "ko": EAST_ASIAN, "sea": SOUTHEAST_ASIAN, "id": SOUTHEAST_ASIAN,
}

# The copilot in your language, by Kokoro: its voices for that language and espeak's name for it (others: Piper).
NATIVE: dict[str, tuple[str, dict[str, str]]] = {
    "de": ("de", {"F": "ff_siwis+if_sara", "M": "bm_george"}),
    "fr": ("fr-fr", {"F": "ff_siwis", "M": "em_alex"}),
    "es": ("es", {"F": "ef_dora", "M": "em_alex"}),
    "it": ("it", {"F": "if_sara", "M": "im_nicola"}),
    "pt": ("pt", {"F": "pf_dora", "M": "pm_alex"}),
}


def voices_for(accent: Accent | None, sex: str) -> tuple[str, ...]:
    """The voices a person of ``accent`` and ``sex`` ("F"/"M") is cast from; None: Kokoro's American English."""
    pools = VOICES.get(accent.id if accent is not None else "us", US)
    return pools["F" if sex == "F" else "M"]


# Each accent heard in the settings: a station of its region, its locale, and the flight it talks to.
SAMPLES: dict[str, tuple[str, str, str]] = {
    "us": ("New York Tower", "en-US", "Delta two three four"), "ca": ("Toronto Tower", "en-CA", "Air Canada eight one two"),
    "gb": ("Heathrow Tower", "en-GB", "Speedbird two seven alpha"), "ie": ("Dublin Tower", "en-IE", "Shamrock one two five"),
    "au": ("Sydney Tower", "en-AU", "Qantas four one"), "za": ("Johannesburg Tower", "en-ZA", "Springbok two zero one"),
    "in": ("Delhi Tower", "en-IN", "Air India one one one"), "sg": ("Changi Tower", "en-SG", "Singapore three two one"),
    "ph": ("Manila Tower", "en-PH", "Philippine five one"), "eaf": ("Nairobi Tower", "en-KE", "Kenya one zero one"),
    "waf": ("Lagos Tower", "en-NG", "Air Peace seven one"), "fr": ("Charles de Gaulle Tower", "fr-FR", "Air France one two three"),
    "de": ("Frankfurt Tower", "de-DE", "Lufthansa four two one"), "nl": ("Schiphol Tower", "nl-NL", "KLM six zero five"),
    "nordic": ("Arlanda Tower", "sv-SE", "Scandinavian nine one"), "fi": ("Helsinki Tower", "fi-FI", "Finnair three one"),
    "es": ("Madrid Tower", "es-ES", "Iberia three one five"), "latam": ("Mexico Tower", "es-MX", "Aeromexico four zero two"),
    "pt": ("Lisbon Tower", "pt-PT", "Air Portugal one three"), "br": ("Guarulhos Tower", "pt-BR", "LATAM three four seven"),
    "it": ("Fiumicino Tower", "it-IT", "Ita Airways six one"), "ru": ("Sheremetyevo Tower", "ru-RU", "Aeroflot two one"),
    "cee": ("Warsaw Tower", "pl-PL", "LOT three eight one"), "gr": ("Athens Tower", "el-GR", "Aegean six zero one"),
    "tr": ("Istanbul Tower", "tr-TR", "Turkish one niner"), "me": ("Dubai Tower", "ar-AE", "Emirates two zero one"),
    "fa": ("Tehran Tower", "fa-IR", "Iran Air seven one"), "il": ("Ben Gurion Tower", "he-IL", "El Al zero one"),
    "zh": ("Beijing Tower", "zh-CN", "Air China niner eight one"), "ja": ("Haneda Tower", "ja-JP", "Japan Air one one"),
    "ko": ("Incheon Tower", "ko-KR", "Korean Air zero one seven"), "sea": ("Suvarnabhumi Tower", "th-TH", "Thai six one five"),
    "id": ("Soekarno-Hatta Tower", "id-ID", "Garuda eight seven"),
}


def sample(accent_id: str) -> tuple[str, str, str]:
    """The station, locale and spoken line of an accent's preview."""
    station, locale, flight = SAMPLES.get(accent_id, SAMPLES["us"])
    return station, locale, f"{flight}, wind two seven zero at eight, runway two seven left, cleared to land."
