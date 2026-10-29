# Voices: Piper and Kokoro

ATC, the ATIS, the other pilots and the copilot are spoken by one of two synthesizers behind one interface
([tts/providers.py](../src/localtc/tts/providers.py)), both on this PC. Piper stays the default and the last resort;
Kokoro is the more natural voice. Researched and measured 2026-10-08. Azure AI Speech (a cloud voice with your own key)
was offered until 0.5.5 and taken out: see the end.

## How a line is spoken

1. **The words**: [tts/aviation.py](../src/localtc/tts/aviation.py) `speakable()`. ATC's own lines arrive already
   spoken (atc_core's phraseology). Whatever still has digits or codes in it (the copilot's lines, a model's wording)
   is finished here, the same for every provider: "119.2" → "one one niner point two", "FL350" → "flight level three
   five zero", "27L" → "two seven left", "2LT" → "two lima tango", "via A, B" → "via alpha, bravo". No digit leaves
   it, so no synthesizer can read a number its own way; the tests check every digit survives, in order.
2. **The person**: [tts/persona.py](../src/localtc/tts/persona.py) `persona_for()`. From the station's name (with the
   shift, `[atc] personalities`): sex, pace and manner (as before: `voices.DELIVERY`), pitch (±6 %), and the
   region's English (`locale`, set by the
   engine from the airport or FIR: `atc_core/region.py` `accent()`; en-US, en-GB, en-AU, en-CA, en-IE, en-IN, en-NZ,
   en-ZA, en-SG, en-HK, en-PH; "" where English is nobody's first language). A person's sex is the one Piper's default
   voice gives them, so a substitute keeps it. Roles: ATC, ATIS, other pilots, copilot; cabin and ground crew are
   reserved (nothing speaks for them yet).
3. **The voice**: each provider casts personas from its own voices (`persona.Casting`): the first choice from the
   name, the next free one if taken, so no two people share a voice while another is free, and a flight (and its
   replay) is cast the same every time. Piper keeps its old mapping (`speaker_for`), so its voices haven't changed.
4. **The chain**: `VoiceChain` asks the providers in a fixed order: Kokoro (when it's `[tts] provider`), then
   Piper, then nothing (text). Each answer is checked (`check_audio`: mono, finite, not silent, not far too long for
   its words, scaled into -1..1). Failures step aside the same way every time: an error 30 s doubling to 10 min; bad
   audio just that line (three in a row: 60 s); a line it ran out of time for (`[tts] timeout_s`, 8 s) is left to
   finish while Piper speaks.
5. **The radio**: unchanged ([dsp/radio.py](../src/localtc/dsp/radio.py)): band-pass, compression, static, squelch
   for ATC; band-limited without hiss for the copilot on the radio; the intercom dry. The player's queue, ATC-first
   priority, the ATIS loop and its cut, and the replay clips (as heard, `dsp/clips.py`) are as they were. Any provider's
   rate works (Piper 22.05 kHz, Kokoro 24 kHz).

A voice failing never stops ATC: the chain answers None and the line stays text; an exception anywhere in a line is
logged and the next line is spoken.

## The two

| | Piper (default) | Kokoro-82M |
|---|---|---|
| Licence | piper-tts GPL-3.0; voices per voice (LibriTTS-R: CC BY 4.0) | weights Apache-2.0; kokoro-onnx MIT; phonemizer + espeak-ng GPL-3.0 |
| Download | 80 MB (the 904-speaker LibriTTS voice) | 337 MB (fp32 model 310 MB + voices 27 MB), or 115 MB with the int8 model |
| Voices | 904 speakers, US English | 54 voices, English: 20 US and 8 British (graded A to F; the pools use C and up, plus blends of two) |
| Pace / pitch | pace and expressiveness (length/noise scales) | pace |
| Latency (M4 Mac, CPU) | 100-200 ms a line, real-time factor 0.03 | 1.1-1.8 s a line, real-time factor 0.21-0.30 (fp32; int8 was slower: 0.44) |
| Memory | about 280 MB | about 210 MB more |
| Whisper's word error after the radio (5 ATC lines, small.en) | 0.29 | 0.17 |

Piper's single-voice models (Lessac, Ryan, Alan) were offered until 0.5.5 and taken out: every station sounded the
same. A settings file that chose one goes back to LibriTTS.

The word error is how the ATC lines came back from Whisper after the radio effect, the same check Piper's speakers were
picked by (`localtc tts bench --whisper`); with base.en the two were close (0.26 and 0.24). Kokoro's voices sound
more natural; its weak points are few good male voices (hence the blends), only US and British English, and the time
it takes.

### Looked at and not used

- **MeloTTS** (MIT): its last release (0.1.1) pins `torch<2.0` and `transformers==4.27.4`, which don't install on the
  Python 3.11-3.13 LocalTC uses, and it isn't maintained. Its English accents (US, GB, AU, IN) would have been useful.
- **The `kokoro` package** (hexgrad's own, PyTorch): needs Python under 3.13 and PyTorch (about 2 GB); kokoro-onnx
  runs the same weights on ONNX Runtime, which Piper already brings.
- Free web demos and unofficial endpoints of cloud voices: not supported APIs.
- **Azure AI Speech** (offered until 0.5.5): the most natural voices, every regional English, free up to 500,000
  characters a month with your own key, but it needed an Azure account and a key pasted in, and sent the spoken words
  to Microsoft. Taken out to keep every voice on this PC; a settings file that chose it now has Kokoro (when it stood
  in for Azure) or Piper.
- **Piper's pinyin Mandarin voices** (chaowen, xiao_ya): need `piper-tts[zh]` (g2pW and Hugging Face's tokenizers),
  far bigger than the voice. Mandarin has huayan (espeak's pronunciation) for both of the copilot's sexes.

## The copilot in your language

When the copilot talks with you in your language (`[crew] language`, chosen with the window's), what it says to you in
conversation is spoken by that language's own Piper voices ([languages.py](../src/localtc/languages.py)): a pack of a
woman's and a man's voice, downloaded together once (Quick Settings → Models, or by itself when the language is
picked), the copilot's voice setting choosing between them. ATC, the callouts, the checklists and the radio calls stay
in aviation English, in the voices above. Kokoro steps aside for these lines (its voices speak English only), and the
words aren't put into aviation English (a German voice reads "4000" its own way). They're spoken at the voice's own
pace (1.0), not ATC's brisker one.

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

## Recommendation

- **Default: Piper.** Instant, small, and the fallback that's always there.
- **Better local voice: Kokoro**, on a PC with CPU to spare. It speaks more naturally and Whisper understood it
  better, but a long clearance starts a second or two later than with Piper, and the sim's own CPU load will make that
  worse: measure on the sim PC (`localtc tts bench`) before making it anyone's default.

## Commands

```
localtc tts kokoro                      # download Kokoro (once)
localtc tts bench --whisper             # every voice that can speak, on the same ATC lines
localtc tts say "..." --provider kokoro --locale en-GB --station "Heathrow Tower"
```
