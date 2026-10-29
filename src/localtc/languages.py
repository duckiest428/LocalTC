"""The languages LocalTC's window comes in, and the copilot can talk with you in.

The window's words are translated from English (``ui/static/i18n/<code>.json``, made with ``tools/i18n.py``). ATC
always speaks aviation English, as it does on the real radio, and so do the copilot's callouts, its checklists and its
radio calls: what the copilot says to you in conversation is in your language (``[crew] language``), heard with a
speech-to-text model that knows it and spoken by a voice of that language.

Each language's voice pack: Piper voices native to it (rhasspy/piper-voices), a woman's and a man's where it has
both, downloaded together once (Quick Settings, or when the copilot first needs them). The copilot's sex setting picks
between them. Kokoro is left out: its voices here speak English only. Mandarin has only huayan: Piper's other Mandarin
voices need a Chinese pronunciation model (g2pW, with PyTorch's tokenizers) far bigger than the voice.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # ISO 639-1, as Whisper takes it ("de")
    native: str  # its own name ("Deutsch")
    english: str  # its English name, for the copilot's language model ("German")
    locale: str = ""  # its region ("de-DE")
    woman: str = ""  # the pack's Piper voices: "<voice>" or "<voice>#<speaker>" for one of a voice's speakers
    man: str = ""
    sample: str = ""  # what the copilot says in it for a preview

    @property
    def voices(self) -> tuple[str, ...]:
        """The pack's voice files (each once)."""
        return tuple(dict.fromkeys(v.partition("#")[0] for v in (self.woman, self.man) if v))

    def voice_for(self, sex: str) -> tuple[str, int | None]:
        """The pack's voice for ``sex`` ("F"/"M"; anything else the man's), and its speaker (None: a voice of one)."""
        name, _, speaker = (self.woman if sex == "F" else self.man or self.woman).partition("#")
        return name, int(speaker) if speaker else None


LANGUAGES: dict[str, Language] = {lang.code: lang for lang in (
    Language("en", "English", "English", "en-US"),
    Language("de", "Deutsch", "German", "de-DE", "de_DE-kerstin-low", "de_DE-thorsten-medium",
             "Hallo, ich bin heute dein Copilot. Sag mir Bescheid, wenn du bereit bist."),
    Language("fr", "Français", "French", "fr-FR", "fr_FR-siwis-medium", "fr_FR-tom-medium",
             "Bonjour, je suis ton copilote aujourd'hui. Dis-moi quand tu es prêt."),
    Language("es", "Español", "Spanish", "es-ES", "es_ES-sharvard-medium#1", "es_ES-sharvard-medium#0",
             "Hola, hoy soy tu copiloto. Avísame cuando estés listo."),
    Language("it", "Italiano", "Italian", "it-IT", "it_IT-paola-medium", "it_IT-riccardo-x_low",
             "Ciao, oggi sono il tuo copilota. Dimmi quando sei pronto."),
    Language("pt", "Português", "Portuguese", "pt-PT", "pt_PT-tugão-medium", "pt_PT-tugão-medium",
             "Olá, hoje sou o teu copiloto. Diz-me quando estiveres pronto."),
    Language("nl", "Nederlands", "Dutch", "nl-NL", "nl_BE-nathalie-medium", "nl_NL-pim-medium",
             "Hallo, ik ben vandaag je copiloot. Laat me weten wanneer je klaar bent."),
    Language("pl", "Polski", "Polish", "pl-PL", "pl_PL-gosia-medium", "pl_PL-darkman-medium",
             "Cześć, jestem dziś twoim drugim pilotem. Daj znać, kiedy będziesz gotowy."),
    Language("sv", "Svenska", "Swedish", "sv-SE", "sv_SE-lisa-medium", "sv_SE-nst-medium",
             "Hej, jag är din andrepilot i dag. Säg till när du är redo."),
    Language("ru", "Русский", "Russian", "ru-RU", "ru_RU-irina-medium", "ru_RU-ruslan-medium",
             "Привет, сегодня я твой второй пилот. Скажи, когда будешь готов."),
    Language("zh", "中文 (普通话)", "Mandarin Chinese (Simplified)", "zh-CN", "zh_CN-huayan-medium", "zh_CN-huayan-medium",
             "你好，今天我是你的副驾驶。准备好了就告诉我。"),
)}


def language(code: str) -> Language | None:
    """The language ``code`` names (None for English or one LocalTC hasn't): what the copilot talks in."""
    lang = LANGUAGES.get((code or "").split("-")[0].lower())
    return None if lang is None or lang.code == "en" else lang


def listing() -> list[dict[str, str]]:
    """For the window: each language's code and names."""
    return [{"code": lang.code, "native": lang.native, "english": lang.english} for lang in LANGUAGES.values()]
