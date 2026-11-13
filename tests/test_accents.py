"""The voice library: every region an accent, each accent its rules and its voices."""

import pytest

from localtc.atc_core.region import ACCENTS as REGION_LOCALES
from localtc.atc_core.region import accent, airline_accent
from localtc.tts.accents import ACCENTS, accent_for, accented
from localtc.tts.library import NATIVE, SAMPLES, VOICES, sample, voices_for
from localtc.tts.persona import persona_for

# Kokoro v1.0's voices (voices-v1.0.bin).
KOKORO = {
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
    "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck",
    "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily", "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "ef_dora", "em_alex", "em_santa", "ff_siwis", "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo", "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei",
    "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
}
# Kokoro's voices of each sex, by the second letter of the name ("af_": a woman, "am_": a man).
WOMEN = {"af", "bf", "ef", "ff", "hf", "if", "jf", "pf", "zf"}


def test_every_region_of_the_world_has_an_accent():
    for prefix, locale in REGION_LOCALES.items():
        assert accent_for(locale) is not None, (prefix, locale)
    assert accent_for("") is None  # none in particular: Kokoro's own American English


@pytest.mark.parametrize(("icao", "accent_id"), [
    ("EGLL", "gb"), ("KJFK", "us"), ("CYYZ", "ca"), ("YSSY", "au"), ("NZAA", "au"), ("EIDW", "ie"), ("FAOR", "za"),
    ("VIDP", "in"), ("OPKC", "in"), ("WSSS", "sg"), ("RPLL", "ph"), ("HKJK", "eaf"), ("DNMM", "waf"),
    ("LFPG", "fr"), ("GOOY", "fr"), ("EDDF", "de"), ("LOWW", "de"), ("LSZH", "de"), ("EHAM", "nl"), ("EKCH", "nordic"),
    ("ENGM", "nordic"), ("EFHK", "fi"), ("LEMD", "es"), ("MMMX", "latam"), ("SCEL", "latam"), ("LPPT", "pt"),
    ("SBGR", "br"), ("LIRF", "it"), ("UUEE", "ru"), ("EPWA", "cee"), ("LHBP", "cee"), ("LGAV", "gr"), ("LTFM", "tr"),
    ("OMDB", "me"), ("HECA", "me"), ("GMMN", "me"), ("OIII", "fa"), ("LLBG", "il"), ("ZBAA", "zh"), ("RCTP", "zh"),
    ("RJTT", "ja"), ("RKSI", "ko"), ("VTBS", "th"), ("VVNB", "sea"), ("TJSJ", "carib"), ("MDSD", "carib"),
    ("MUHA", "carib"), ("WIII", "id"), ("WMKK", "sg"),
])
def test_each_airport_speaks_its_regions_accent(icao, accent_id):
    assert accent_for(accent(icao)).id == accent_id


def test_every_accent_has_women_and_men_and_a_sample_and_every_voice_is_kokoros():
    assert set(VOICES) == set(ACCENTS) == set(SAMPLES)
    for accent_id, pools in VOICES.items():
        for sex in ("F", "M"):
            assert pools[sex], (accent_id, sex)
            for voice in pools[sex]:
                parts = voice.split("+")
                assert all(p in KOKORO for p in parts), voice
                assert all((p[:2] in WOMEN) == (sex == "F") for p in parts), (accent_id, sex, voice)  # never a blend across sexes
        station, locale, _ = SAMPLES[accent_id]
        assert accent_for(locale).id == accent_id and station.endswith("Tower")
    for code, (_, voices) in NATIVE.items():
        assert all(p in KOKORO for v in voices.values() for p in v.split("+")), code


def test_the_english_speaking_worlds_own_english_is_left_as_it_is():
    words = "wˈɪnd tˈuː fˈɔː zˈiəɹəʊ, θɹˈiː"
    for accent_id in ("us", "gb"):
        assert accented(words, ACCENTS[accent_id]) == words
    assert accented(words, None) == words


@pytest.mark.parametrize(("accent_id", "english", "said"), [
    ("fr", "hˈɛdɪŋ θɹˈiː", "ˈɛdiŋ sʁˈiː"),  # no h, "s" for "th", a French r
    ("de", "wˈɪnd", "vˈɪnt"),  # "v" for "w", the end devoiced
    ("es", "spˈiːd", "espˈiːd"),  # a vowel before "s" and a consonant
    ("ja", "lˈand", "lˈando"),  # a word ends on a vowel
    ("in", "tˈuː", "ʈˈuː"),  # retroflex
    ("it", "lˈand", "lˈandə"),
    ("ru", "wˈɪnd", "vˈint"),
    ("carib", "pˈɔːɹt", "pˈɔːlt"),  # an r before a consonant nearly an l
    ("carib", "ɪstˈæblɪʃt", "ihtˈabliʃt"),  # an s before one breathed out ("ehtablished")
    ("th", "ɹˈʌnweɪ", "ɾˈʌnwei"),  # a tapped r
])
def test_an_accent_moves_english_onto_its_languages_sounds(accent_id, english, said):
    assert accented(english, ACCENTS[accent_id]) == said


def test_a_voice_of_the_region_speaks_its_accent():
    for accent_id, station_locale, prefix in (("fr", "fr-FR", "ff_"), ("ja", "ja-JP", "j"), ("in", "en-IN", "h"),
                                              ("zh", "zh-CN", "z"), ("latam", "es-MX", "e")):
        for sex in ("F", "M"):
            pool = voices_for(accent_for(station_locale), sex)
            if accent_id == "fr" and sex == "M":
                continue  # Kokoro has no French man: the nearest voices carry the accent
            assert pool[0].startswith(prefix), (accent_id, sex, pool)
    assert voices_for(None, "F")[0] == "af_heart"


def test_other_crews_sound_like_their_airline():
    assert airline_accent("AFR123") == "fr-FR" and airline_accent("DLH4") == "de-DE" and airline_accent("JAL5") == "ja-JP"
    assert airline_accent("BAW9") == "en-GB" and airline_accent("XYZ1") == "" and airline_accent("") == ""


def test_the_copilots_english_is_its_languages_accent():
    from localtc.app import copilot_locale

    assert copilot_locale("", "de") == "de-DE" and copilot_locale("", "") == "" and copilot_locale("", "en") == ""
    assert copilot_locale("en-GB", "de") == "en-GB"  # a personality's own English stands


def test_each_accent_preview_is_its_region():
    station, locale, line = sample("fr")
    assert station == "Charles de Gaulle Tower" and locale == "fr-FR" and "cleared to land" in line
    assert sample("nowhere")[1] == "en-US"


def test_a_persona_of_a_region_is_cast_from_its_accents_voices():
    from pathlib import Path

    from localtc.tts.kokoro import KokoroVoices, pool_for

    lang, found, pool = pool_for(persona_for("Haneda Tower", locale="ja-JP"))
    assert lang == "en-us" and found.id == "ja" and pool == list(voices_for(found, persona_for("Haneda Tower").sex))
    voices = KokoroVoices(Path("/x"), engine=object())
    assert voices.voice_for(persona_for("Haneda Tower", locale="ja-JP"))[1] in pool


def test_piper_speaks_the_accent_too():
    from localtc.tts.providers import PiperVoices

    class Synth:
        rate = 1.15

        def __init__(self):
            self.accents = []

        def synthesize(self, text, speaker, *, rate=None, noise_scale=None, noise_w=None, accent=None):
            import numpy as np

            from localtc.tts.synth import Speech

            self.accents.append(accent.id if accent else None)
            return Speech(np.zeros(100, dtype=np.float32), 22050, 1.0)

    synth = Synth()
    PiperVoices(synth).synthesize("cleared to land", persona_for("Frankfurt Tower", locale="de-DE"))
    PiperVoices(synth).synthesize("cleared to land", persona_for("Paine Tower"))
    assert synth.accents == ["de", None]


def test_the_settings_offer_the_library_and_every_accent_to_hear(tmp_path, monkeypatch):
    import asyncio

    from localtc.config import Config
    from localtc.ui.controller import AppController

    monkeypatch.setenv("LOCALTC_SETTINGS", str(tmp_path / "settings.toml"))
    cfg = Config()
    cfg.tts.voices_dir = str(tmp_path)
    got = asyncio.run(AppController(cfg, plan_path=tmp_path / "f.json", cache=None).api_tts({}))
    library = got["library"]
    assert not library["installed"] and library["size_mb"] > 300
    assert {a["id"] for a in library["accents"]} == set(ACCENTS)
    assert [a["name"] for a in library["accents"]] == sorted(a["name"] for a in library["accents"])
