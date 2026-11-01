# Voices: the LocalTC voice library

ATC, the ATIS, the other pilots and the copilot are spoken by the LocalTC voice library: Kokoro-82M on this PC, with
every person speaking English in the accent of where they're from. Piper stands in, with the same accents, until the
library is downloaded or when it can't speak a line. Both sit behind one interface
([tts/providers.py](../src/localtc/tts/providers.py)). The two were researched and measured 2026-10-08, the accents
2026-11-01. Azure AI Speech (a cloud voice with your own key) was offered until 0.5.2 and taken out: see the end.

## How a line is spoken

1. **The words**: [tts/aviation.py](../src/localtc/tts/aviation.py) `speakable()`. ATC's own lines arrive already
   spoken (atc_core's phraseology). Whatever still has digits or codes in it (the copilot's lines, a model's wording)
   is finished here, the same for every provider: "119.2" → "one one niner point two", "FL350" → "flight level three
   five zero", "27L" → "two seven left", "2LT" → "two lima tango", "via A, B" → "via alpha, bravo". No digit leaves
   it, so no synthesizer can read a number its own way; the tests check every digit survives, in order.
2. **The person**: [tts/persona.py](../src/localtc/tts/persona.py) `persona_for()`. From the station's name (with the
   shift, `[atc] personalities`): sex, pace and manner (as before: `voices.DELIVERY`), pitch (±6 %), and the
   region (`locale`, set by the engine from the airport or FIR: `atc_core/region.py` `accent()`, a locale for every
   ICAO prefix in the world: en-GB, de-DE, ja-JP, es-MX, ar-AE ...; for the other crews on the frequency their
   airline's country, `airline_accent()`; "" none in particular). A person's sex is the one Piper's default
   voice gives them, so a substitute keeps it. Roles: ATC, ATIS, other pilots, copilot; cabin and ground crew are
   reserved (nothing speaks for them yet).
3. **The voice**: each provider casts personas from its own voices (`persona.Casting`): the first choice from the
   name, the next free one if taken, so no two people share a voice while another is free, and a flight (and its
   replay) is cast the same every time. Piper keeps its old mapping (`speaker_for`), so its voices haven't changed.
4. **The accent**: the locale's accent ([tts/accents.py](../src/localtc/tts/accents.py), below): espeak's English
   phonemes of the line moved onto the speaker's language's sounds, the same for Kokoro and Piper.
5. **The chain**: `VoiceChain` asks the providers in a fixed order: the voice library (Kokoro), then Piper, then
   nothing (text). Each answer is checked (`check_audio`: mono, finite, not silent, not far too long for
   its words, scaled into -1..1). Failures step aside the same way every time: an error 30 s doubling to 10 min; bad
   audio just that line (three in a row: 60 s); a line it ran out of time for (`[tts] timeout_s`, 8 s) is left to
   finish while Piper speaks.
6. **The radio**: unchanged ([dsp/radio.py](../src/localtc/dsp/radio.py)): band-pass, compression, static, squelch
   for ATC; band-limited without hiss for the copilot on the radio; the intercom dry. The player's queue, ATC-first
   priority, the ATIS loop and its cut, and the replay clips (as heard, `dsp/clips.py`) are as they were. Any provider's
   rate works (Piper 22.05 kHz, Kokoro 24 kHz).

A voice failing never stops ATC: the chain answers None and the line stays text; an exception anywhere in a line is
logged and the next line is spoken.

## The voice library and Piper

| | The voice library (Kokoro-82M) | Piper (its stand-in) |
|---|---|---|
| Licence | weights Apache-2.0; kokoro-onnx MIT; phonemizer + espeak-ng GPL-3.0 | piper-tts GPL-3.0; voices per voice (LibriTTS-R: CC BY 4.0) |
| Download | 337 MB (fp32 model 310 MB + voices 27 MB), or 115 MB with the int8 model | 80 MB (the 904-speaker LibriTTS voice) |
| Voices | 54 in 9 languages, and blends of two: each accent a woman's and a man's pool | 904 speakers, US English |
| Accents | all of them, in voices of the region | all of them, in US voices |
| Pace / pitch | pace; pitch (a person's ±6 %, by resampling) | pace and expressiveness (length/noise scales) |
| Latency (M4 Mac, CPU) | 1.1-1.8 s a line, real-time factor 0.21-0.30 (fp32; int8 was slower: 0.44) | 100-200 ms a line, real-time factor 0.03 |
| Memory | about 210 MB more | about 280 MB |
| Whisper's word error after the radio (5 ATC lines, small.en) | 0.17 | 0.29 |

Piper's single-voice models (Lessac, Ryan, Alan) were offered until 0.5.2 and taken out: every station sounded the
same. A settings file that chose one goes back to LibriTTS; one that chose Piper or Kokoro as the voice has the
library (the only choice since 0.5.4).

## Accents

A real controller in Paris speaks fluent English with a French accent; one in Tokyo a Japanese one. LocalTC makes each
accent the way a speaker carries their first language into English (L1 transfer), on the phonemes rather than the
spelling:

1. espeak turns the line into English phonemes: American or British, the English the region learns (Latin America,
   Japan, Korea, China and the Philippines American; Europe, Africa, the Middle East and most of Asia British).
2. The accent's rules move them onto the language's sounds ([tts/accents.py](../src/localtc/tts/accents.py)): French
   drops the h, says "s" and "z" for the two "th"s, a French r and tense vowels (ɪ → i); German "v" for "w", a uvular
   r, the ends of words devoiced ("wind" → "vint") and an initial s voiced; Spanish "b" for "v", a tapped r, five
   vowels and an "e" before "s" and a consonant ("speed" → "espeed"); Japanese one tapped r for r, "b" for "v", a
   vowel after a final t or d; Indian English retroflex t and d, ʋ for both w and v, a rhotic r; and so on for 33
   accents. They're kept light: a controller is fluent, and every number has to come through the radio.
3. The phonemes are spoken by voices of that language ([tts/library.py](../src/localtc/tts/library.py)): Kokoro's own
   French, Spanish, Italian, Brazilian Portuguese, Hindi, Japanese and Mandarin voices (a voice of the language
   speaking English carries its rhythm and timbre), and blends of the nearest elsewhere (continental Europe: Kokoro's
   Romance and British voices blended; Korea: its Japanese and Mandarin; the Middle East: its Hindi and Spanish).

A voice alone does little: Whisper heard Kokoro's French woman reading plain English as English half the time, and
its Japanese, Hindi, Spanish and Italian voices as English nearly always. With the rules the French was heard as
French nine times in ten, at the same word error. The first, heavier rules (every vowel full, Japanese clusters split,
l and r one sound) lost up to three words in four: the ones kept lose about one in five, where Kokoro's own American
loses one in twenty (Whisper base.en through the radio; a pilot follows an accent better than Whisper does).

Every locale of `region.py` has an accent (`accent_for`; a test checks), the rest of the world grouped by first
language: Arabic from Morocco to Oman, the Slavic and other languages of central and eastern Europe, mainland
Southeast Asia, East and West Africa. The other crews on the frequency speak their airline's (`airline_accent`: an
Air France crew is French at JFK), and the copilot's English its personality's, else that of the language it talks
with you in.

Measured with `localtc tts accents` (each accent's preview line through the radio, a woman's and a man's voice,
Whisper base.en reading it back; `--out` writes them to WAV files) and, while they were made, Whisper small's guess of
the language on three ATC lines (the share it gave English: how foreign it sounds):

| Accent | Woman (word error, share heard as English) | Man |
|---|---|---|
| American | af_heart (0.15, 1.00) | am_michael (0.08, 1.00) |
| Canadian | af_heart (0.17, 1.00) | am_michael (0.08, 1.00) |
| British | bf_emma (0.09, 1.00) | bm_george (0.11, 1.00) |
| Irish | bf_emma (0.13, 0.99) | bm_george (0.06, 0.99) |
| Australian | bf_emma (0.21, 1.00) | bm_george (0.27, 0.99) |
| South African | bf_emma (0.15, 0.99) | bm_george (0.13, 0.99) |
| Indian | hf_alpha (0.21, 0.99) | hm_omega (0.17, 0.84) |
| Singaporean and Malaysian | zf_xiaoxiao+af_kore (0.11, 0.94) | zm_yunjian (0.23, 0.48) |
| Filipino | hf_beta+zf_xiaoni (0.17, 0.89) | zm_yunjian+hm_psi (0.21, 0.41) |
| East African | af_kore+hf_beta (0.21, 0.99) | am_onyx+hm_omega (0.13, 0.96) |
| West African | af_kore+hf_beta (0.15, 0.98) | am_onyx+hm_omega (0.15, 0.95) |
| French | ff_siwis (0.34, 0.30) | em_alex (0.25, 0.97) |
| German | ff_siwis+if_sara (0.26, 0.23) | em_alex+bm_george (0.15, 0.88) |
| Dutch | ff_siwis+if_sara (0.13, 0.45) | em_alex+bm_george (0.13, 0.90) |
| Scandinavian | ff_siwis+if_sara (0.13, 0.58) | em_alex+bm_george (0.13, 0.95) |
| Finnish | ff_siwis+if_sara (0.27, 0.14) | em_alex+bm_george (0.17, 0.81) |
| Russian | ff_siwis+if_sara (0.19, 0.15) | em_alex+bm_george (0.19, 0.81) |
| Central and Eastern European | ff_siwis+if_sara (0.17, 0.12) | em_alex+bm_george (0.06, 0.71) |
| Greek | ff_siwis+if_sara (0.17, 0.58) | em_alex+bm_george (0.17, 0.95) |
| Spanish | ef_dora (0.19, 0.81) | em_alex (0.15, 0.76) |
| Latin American | ef_dora (0.13, 0.78) | em_alex (0.13, 0.72) |
| Portuguese | pf_dora (0.35, 0.96) | pm_alex (0.39, 0.93) |
| Brazilian | pf_dora (0.15, 0.98) | pm_alex (0.17, 0.96) |
| Italian | if_sara (0.27, 0.77) | im_nicola (0.32, 0.38) |
| Turkish | hf_beta+ef_dora (0.21, 0.19) | hm_omega+em_alex (0.13, 0.17) |
| Arabic | hf_beta+ef_dora (0.27, 0.77) | hm_omega+em_alex (0.15, 0.33) |
| Persian | hf_beta+ef_dora (0.21, 0.32) | hm_omega+em_alex (0.17, 0.12) |
| Israeli | ff_siwis+if_sara (0.30, 0.30) | em_alex+bm_george (0.23, 0.79) |
| Chinese | zf_xiaoxiao+af_kore (0.19, 0.99) | zm_yunjian (0.23, 0.78) |
| Japanese | jf_gongitsune+bf_emma (0.31, 0.71) | jm_kumo (0.27, 0.12) |
| Korean | zf_xiaoni+bf_emma (0.23, 0.92) | jm_kumo+zm_yunjian (0.23, 0.67) |
| Southeast Asian | hf_beta+zf_xiaoni (0.27, 0.86) | zm_yunjian+hm_psi (0.23, 0.24) |
| Indonesian | hf_beta+zf_xiaoni (0.31, 0.58) | zm_yunjian+hm_psi (0.15, 0.08) |

### Looked at and not used

- **MeloTTS** (MIT): its last release (0.1.1) pins `torch<2.0` and `transformers==4.27.4`, which don't install on the
  Python 3.11-3.13 LocalTC uses, and it isn't maintained. Its English accents (US, GB, AU, IN) would have been useful.
- **The `kokoro` package** (hexgrad's own, PyTorch): needs Python under 3.13 and PyTorch (about 2 GB); kokoro-onnx
  runs the same weights on ONNX Runtime, which Piper already brings.
- Free web demos and unofficial endpoints of cloud voices: not supported APIs.
- **Azure AI Speech** (offered until 0.5.2): the most natural voices, every regional English, free up to 500,000
  characters a month with your own key, but it needed an Azure account and a key pasted in, and sent the spoken words
  to Microsoft. Taken out to keep every voice on this PC; a settings file that chose it now has Kokoro (when it stood
  in for Azure) or Piper.
- **Piper's pinyin Mandarin voices** (chaowen, xiao_ya): need `piper-tts[zh]` (g2pW and Hugging Face's tokenizers),
  far bigger than the voice. Mandarin has huayan (espeak's pronunciation) for both of the copilot's sexes.

## The copilot in your language

When the copilot talks with you in your language (`[crew] language`, chosen with the window's), what it says to you in
conversation is spoken by the voice library where Kokoro speaks the language well, and by that language's own Piper
voices elsewhere ([languages.py](../src/localtc/languages.py)). ATC, the callouts, the checklists and the radio calls
stay in aviation English, in the voices above (in the accent of the copilot's language). The words aren't put into
aviation English (a German voice reads "4000" its own way), and they're spoken at the voice's own pace (1.0), not
ATC's brisker one.

Kokoro was trained on nine languages but speaks others from espeak's phonemes; Whisper small reading a copilot's line
back decided which (`library.NATIVE`, 2026-11-01; the error is per word):

| Language | The library's woman | The library's man | Whisper's error (woman, man) | Else |
|---|---|---|---|---|
| Deutsch | ff_siwis+if_sara | bm_george | 0.11, 0.11 | |
| Français | ff_siwis | em_alex | 0.11, - | |
| Español | ef_dora | em_alex | 0.00, 0.00 | |
| Italiano | if_sara | im_nicola | 0.00, 0.00 | |
| Português | pf_dora | pm_alex | 0.24, 0.12 | |
| Polski | ef_dora | em_alex | 0.07, 0.14 | |
| Nederlands | | | 0.16, and not heard as Dutch | Piper |
| Svenska | | | 0.35-0.59 | Piper |
| Русский | | | 0.36-0.50 | Piper |
| 中文 | | | 0.66 (Kokoro's Mandarin needs its own pronunciation, misaki, not espeak's) | Piper |

The Piper packs: the stand-in where the library speaks the language, the voice where it doesn't.

| Language | Woman | Man | Download |
|---|---|---|---|
| Deutsch | de_DE-kerstin-low | de_DE-thorsten-medium | about 120 MB |
| Français | fr_FR-siwis-medium | fr_FR-tom-medium | about 125 MB |
| Español | es_ES-sharvard-medium (speaker F) | the same file, speaker M | 73 MB |
| Italiano | it_IT-paola-medium | it_IT-riccardo-x_low | about 90 MB |
| Português | pt_PT-tugão-medium (the only European Portuguese voice) | the same | 63 MB |
| Nederlands | nl_BE-nathalie-medium | nl_NL-pim-medium | about 125 MB |
| Polski | pl_PL-gosia-medium | pl_PL-darkman-medium | about 125 MB |
| Svenska | sv_SE-lisa-medium | sv_SE-nst-medium | about 125 MB |
| Русский | ru_RU-irina-medium | ru_RU-ruslan-medium | about 125 MB |
| 中文 | zh_CN-huayan-medium | the same | 63 MB |

The sexes were checked by each voice's pitch reading a line of its language (2026-10-29). The voices are fetched by
LocalTC itself (`voices.download`: the address quoted, each file written beside its place and moved there whole), as
piper's own downloader can't ask for "tugão".

## Commands

```
localtc tts kokoro                      # download the voice library (once)
localtc tts accents --out accents/      # each accent through the radio, scored by Whisper, and written as WAVs
localtc tts bench --whisper             # every voice that can speak, on the same ATC lines
localtc tts say "..." --provider kokoro --locale de-DE --station "Frankfurt Tower"
```
