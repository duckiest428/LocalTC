# Voices: Piper, Kokoro and Azure

ATC, the ATIS, the other pilots and the copilot are spoken by one of three synthesizers behind one interface
([tts/providers.py](../src/localtc/tts/providers.py)). Piper stays the default and the last resort; Kokoro is the more
natural voice on the same PC; Azure AI Speech is the optional cloud voice. Researched and measured 2026-10-08.

## How a line is spoken

1. **The words**: [tts/aviation.py](../src/localtc/tts/aviation.py) `speakable()`. ATC's own lines arrive already
   spoken (atc_core's phraseology). Whatever still has digits or codes in it (the copilot's lines, a model's wording)
   is finished here, the same for every provider: "119.2" → "one one niner point two", "FL350" → "flight level three
   five zero", "27L" → "two seven left", "2LT" → "two lima tango", "via A, B" → "via alpha, bravo". No digit leaves
   it, so no synthesizer can read a number its own way; the tests check every digit survives, in order.
2. **The person**: [tts/persona.py](../src/localtc/tts/persona.py) `persona_for()`. From the station's name (with the
   shift, `[atc] personalities`): sex, pace and manner (as before: `voices.DELIVERY`), pitch (±6 %), a speaking
   style where the manner has an even one (calm, friendly, serious), and the region's English (`locale`, set by the
   engine from the airport or FIR: `atc_core/region.py` `accent()`; en-US, en-GB, en-AU, en-CA, en-IE, en-IN, en-NZ,
   en-ZA, en-SG, en-HK, en-PH; "" where English is nobody's first language). A person's sex is the one Piper's default
   voice gives them, so a substitute keeps it. Roles: ATC, ATIS, other pilots, copilot; cabin and ground crew are
   reserved (nothing speaks for them yet).
3. **The voice**: each provider casts personas from its own voices (`persona.Casting`): the first choice from the
   name, the next free one if taken, so no two people share a voice while another is free, and a flight (and its
   replay) is cast the same every time. Piper keeps its old mapping (`speaker_for`), so its voices haven't changed.
4. **The chain**: `VoiceChain` asks the providers in a fixed order: `[tts] provider`, then Kokoro (when `[tts] kokoro`
   is on, or it's the provider), then Piper, then nothing (text). Each answer is checked (`check_audio`: mono, finite,
   not silent, not far too long for its words, scaled into -1..1). Failures step aside the same way every time:
   timeout, network, service down 30 s doubling to 10 min; rate limited for its Retry-After (else 60 s); out of quota
   until the month resets; key refused until it's changed; bad audio just that line (three in a row: 60 s); a line it
   ran out of time for (`[tts] timeout_s`, 8 s) is left to finish while the next provider speaks.
5. **The radio**: unchanged ([dsp/radio.py](../src/localtc/dsp/radio.py)): band-pass, compression, static, squelch
   for ATC; band-limited without hiss for the copilot on the radio; the intercom dry. The player's queue, ATC-first
   priority, the ATIS loop and its cut, and the replay clips (as heard, `dsp/clips.py`) are as they were. Any provider's
   rate works (Piper 22.05 kHz, Kokoro and Azure 24 kHz).

A voice failing never stops ATC: the chain answers None and the line stays text; an exception anywhere in a line is
logged and the next line is spoken.

## The three

| | Piper (default) | Kokoro-82M | Azure AI Speech |
|---|---|---|---|
| Where | this PC | this PC | Microsoft's cloud |
| Licence | piper-tts GPL-3.0; voices per voice (LibriTTS-R: CC BY 4.0) | weights Apache-2.0; kokoro-onnx MIT; phonemizer + espeak-ng GPL-3.0 | Microsoft's terms; your own Azure account |
| Download | 80 MB (the 904-speaker LibriTTS voice) | 337 MB (fp32 model 310 MB + voices 27 MB), or 115 MB with the int8 model | none |
| Voices | 904 speakers, US English | 54 voices, English: 20 US and 8 British (graded A to F; the pools use C and up, plus blends of two) | hundreds; every regional English (US, GB, AU, CA, IE, IN, NZ, ZA, SG, HK, PH) |
| Pace / pitch / style | pace and expressiveness (length/noise scales) | pace | SSML: pace, pitch, pauses, `express-as` styles on some voices |
| Latency (M4 Mac, CPU) | 100-200 ms a line, real-time factor 0.03 | 1.1-1.8 s a line, real-time factor 0.21-0.30 (fp32; int8 was slower: 0.44) | 0.5-0.75 s a line from eastus (a network round trip plus synthesis), real-time factor 0.09; repeated lines from this PC's cache |
| Memory | about 280 MB | about 210 MB more | none |
| Whisper's word error after the radio (5 ATC lines, small.en) | 0.29 | 0.17 | 0.10 (base.en; Piper 0.32, Kokoro 0.24 in the same run) |

The word error is how the ATC lines came back from Whisper after the radio effect, the same check Piper's speakers were
picked by (`localtc tts bench --whisper`); with base.en the two were close (0.26 and 0.24). Kokoro's voices sound
more natural; its weak points are few good male voices (hence the blends), only US and British English, and the time
it takes.

### Azure AI Speech: limits and cost (Microsoft's pages, 2026-10)

- **Free tier (F0)**: 0.5 million characters a month of neural voices, 20 requests a minute (not adjustable).
  Paid (S0): about $15 per million characters, 30 requests a second.
- **What's billed**: every character of the text and of any markup inside `<voice>` (`<speak>` and `<voice>` aren't).
  LocalTC adds markup only for what changes the voice (pace, pitch; styles only with `[tts] azure_styles`).
- **Usage per flight**: a recorded 3-hour airliner flight spoke 265 lines, 15,500 characters (ATC 4,400, other traffic
  3,200, the copilot on the radio 2,400 and on the intercom 5,400), and the pace and pitch markup adds about 40
  characters a line: roughly 26,000 characters, or 8,000-10,000 an hour with an ATIS or two. The free tier covers
  about 50-60 flying hours a month. The ATIS loop and repeated lines come from the cache (`[tts] cache_mb`, on this PC).
- **The count is LocalTC's own** (`tts_usage.json` in the data folder, by UTC month): Azure has no API to ask what's
  left of the free allowance (the portal's Metrics show it), so another program using the same key isn't counted.
  LocalTC stops at 98 % of `[tts] azure_monthly_chars` and Piper (or Kokoro) carries on.
- **Privacy**: while Azure is chosen, the words to be spoken (with the voice name, pace and pitch) go to your Speech
  resource's region; nothing else of the flight. Microsoft states that text and audio of real-time synthesis aren't
  stored. Off unless chosen; the key lives in the credential store (Settings > Voices) or `AZURE_SPEECH_KEY`, never in
  the settings file.
- **The key**: a Speech resource's, or a Foundry / AI services resource's (its "Keys and Endpoint" page; the endpoint
  shown there, `<region>.api.cognitive.microsoft.com`, isn't needed: the region is). Either of its two keys works; the
  app says to use KEY 1 and keep KEY 2 as the spare while KEY 1 is regenerated.
- **The API**: Microsoft's documented REST endpoint (`https://<region>.tts.speech.microsoft.com/cognitiveservices/v1`,
  SSML in, `raw-24khz-16bit-mono-pcm` out; the voice list from `/cognitiveservices/voices/list`), the same service the
  Speech SDK uses, without its native library.

### Looked at and not used

- **MeloTTS** (MIT): its last release (0.1.1) pins `torch<2.0` and `transformers==4.27.4`, which don't install on the
  Python 3.11-3.13 LocalTC uses, and it isn't maintained. Its English accents (US, GB, AU, IN) would have been useful.
- **The `kokoro` package** (hexgrad's own, PyTorch): needs Python under 3.13 and PyTorch (about 2 GB); kokoro-onnx
  runs the same weights on ONNX Runtime, which Piper already brings.
- Free web demos and unofficial endpoints of cloud voices: not supported APIs.

## Recommendation

- **Default: Piper.** Instant, small, and the fallback that's always there.
- **Better local voice: Kokoro**, on a PC with CPU to spare. It speaks more naturally and Whisper understood it
  better, but a long clearance starts a second or two later than with Piper, and the sim's own CPU load will make that
  worse: measure on the sim PC (`localtc tts bench`) before making it anyone's default.
- **Cloud: Azure**, optional. The free tier fits a few flights a week; the best regional voices; needs an Azure
  account and sends the spoken words to Microsoft.

## Commands

```
localtc tts kokoro                      # download Kokoro (once)
localtc tts bench --whisper             # every voice that can speak, on the same ATC lines
localtc tts say "..." --provider kokoro --locale en-GB --station "Heathrow Tower"
```
