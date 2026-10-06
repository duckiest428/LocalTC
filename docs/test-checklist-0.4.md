# 0.4 test checklist: cloud models and the recent fixes

Tick each box on the Windows PC with MSFS 2024. Start with: pull in GitHub Desktop, then
`.venv\Scripts\pip install -e ".[dev]"` (or run `installer\LocalTC-Setup.exe`).

## 1. Cloud language model: setup (Quick Settings → Cloud language model)

- [ ] The card shows **Recommended for quality**, and the Models card links to it.
- [ ] **Use cloud language models** is **off** on a fresh install.
- [ ] **When every cloud service fails, the model on this PC answers** is **off** by default.
- [ ] **The copilot uses it too** is **on** by default.
- [ ] **Services** is folded; it opens and closes, and so does each service. A fold stays as you left it after saving a key.
- [ ] Services listed, in order: Mistral, Pollinations, Groq, Google AI Studio (Gemini), Cloudflare Workers AI, NVIDIA NIM, SiliconFlow. Gone: Cerebras, LongCat, Tencent, Baidu, iFlytek, Alibaba/Qwen, OpenCode.
- [ ] Pollinations shows **No key · ready** and no "Get a key" link; the others show **Free key · needs a key** until one is saved.
- [ ] Save a key (use a **new** one: the ones pasted in chat should be rotated):
  - [ ] Mistral
  - [ ] NVIDIA NIM
  - [ ] Groq
  - [ ] Google AI Studio (optional, untested so far)
  - [ ] Cloudflare (optional, untested): typed as `ACCOUNT_ID:API_TOKEN`
- [ ] After saving, the row says **ready**, has **Remove**, and the key field is empty (the key never comes back to the page).
- [ ] The keys are in Windows Credential Manager (Generic credentials, "LocalTC", `cloud:<service>`), **not** in `settings.toml`.
- [ ] **Remove** clears the key; the row goes back to "needs a key".

## 2. Cloud language model: the Test button

- [ ] **Mistral → Test** lists every model:
  - [ ] ministral-14b, 8b and 3b ✓ (under a second);
  - [ ] mistral-small ✗ "not in your plan (a limit of 0 requests ...)".
- [ ] **Groq → Test**: gpt-oss-120b and gpt-oss-20b ✓.
- [ ] **NVIDIA → Test**: gpt-oss-20b ✓ (about 1 s), nemotron ✓ (slow, around 10 s).
- [ ] **Pollinations → Test**: ✓, or a clear reason (limit, server error). Never a hang.
- [ ] A wrong key gives "the key was refused (401 ...)".

## 3. Cloud language model: in flight

Turn on **Use cloud language models**, start a flight, then check `%LOCALAPPDATA%\LocalTC\logs\localtc.log`.

- [ ] The log has "Cloud language models: Mistral, Pollinations, Groq, NVIDIA ...".
- [ ] It has no "isn't offered by" lines for models you saw ✓ in Test.
- [ ] Clearance off the script ("request runway 24L instead") gets a sensible worded reply quickly.
- [ ] Questions answered from the whole flight (things the local model only got when asked):
  - [ ] "how long is the runway?";
  - [ ] "what's our cleared altitude?";
  - [ ] "how far to destination?";
  - [ ] "what did you say the squawk was?".
- [ ] Quick Settings → Cloud card says "This flight's last answer came from **mistral/ministral-14b-latest**" (or similar).
- [ ] Fallback: remove the Mistral key mid-session or block it, and the next call comes from Pollinations/Groq without a timeout. The log shows "resting" or "out for this flight" for Mistral.
- [ ] Everything failing (Wi-Fi off):
  - [ ] with local fallback **off**: ATC still answers (script / "say again"), never silence;
  - [ ] with it **on** and Ollama running: the local model answers.
- [ ] Recordings replay (`localtc atc <recording>`) without asking the cloud.

## 4. Cloud model for the copilot (intercom)

- [ ] With the copilot on (Quick Settings → Copilot → Intercom) and **The copilot uses it too** on, the log says "Copilot's language model: the cloud".
- [ ] Free questions on the intercom are answered from the whole flight:
  - [ ] "what runway are we landing on and how long is it";
  - [ ] "what did approach just tell us";
  - [ ] "how far to go".
- [ ] A command in your own words ("drop the gear for me") is read back for "confirm".
- [ ] Asking the copilot something while ATC is talking doesn't delay ATC's reply (separate connection).
- [ ] With **The copilot uses it too** off: the log says the local model (or none without Ollama), and ATC still uses the cloud.

## 4b. Copilot settings (Quick Settings → Copilot)

- [ ] **Intercom copilot** has a master switch on the right.
  - [ ] Off greys out everything below it, and the next flight has no intercom, callouts or checklists. The radio copilot (the Copilot switch) still works.
  - [ ] On brings it all back.
- [ ] **Copilot's language model** section:
  - [ ] **What it uses the model for**: full / questions / off. Off: "what's the weather like in Paris" gets "Say again?", while "fuel?" and "flaps one" still work.
  - [ ] **Which model**: cloud or this PC. It matches "The copilot uses it too" on the Cloud card; changing one changes the other after the page redraws.
  - [ ] **Answer beyond what it knows**: off, "how long does the APU take to start" gets "I don't have that" (or "Say again?"); on, a real answer.
  - [ ] **Wait for an answer** and **At most** save, and reject values out of range.

## 5. Yoke / joystick button detection (Quick Settings → Push-to-talk, and Copilot → intercom)

- [ ] Set push-to-talk to **A yoke or joystick button**: a device list and **Detect** appear.
- [ ] The device list fills with your controllers (press any button once if it's empty).
- [ ] **Detect**, then press the yoke button: the field fills with `joystick:N:button:M` and saves.
- [ ] Picking a device first only listens to that device.
- [ ] A button already held when you press Detect isn't taken.
- [ ] Nothing pressed for 15 s gives "Nothing pressed in 15 s ...".
- [ ] The same works for the intercom button.
- [ ] **In the sim**: holding the detected button keys the radio (PTT) / the intercom. If MSFS numbers the device differently, the `N` in `joystick:N:...` is the one to change: note what worked.

## 6. Account suggestion

- [ ] Signed out: fly and end 3 flights. After the third, the "Three flights in: want an account?" popup appears, listing the benefits.
- [ ] **Create an account** opens Quick Settings at the Account card with the email box focused.
- [ ] **Not now** / ✕ closes it, and it never comes back after more flights or a restart.
- [ ] Signed in: no popup ever.

## 7. Radio log noise (the earlier fix)

- [ ] During a long flight the app, phone and website Comms show **no** "SimConnect exception ..." or "Timed out waiting for airport data for P@ ..." lines.
- [ ] The log file may still have them, and they never name junk idents like "P@", "-:P@" or "PA".
- [ ] Nearby airports still load (Airport Lookup, the frequencies list).

## 8. Earlier round, still to fly once

- [ ] Copilot watches:
  - [ ] turbulence and "smooth again";
  - [ ] wind shear below 1,500 ft;
  - [ ] icing (none with anti-ice on);
  - [ ] destination weather;
  - [ ] step climb suggestion;
  - [ ] STAR restriction missed / won't make;
  - [ ] "field in sight?" on a visual;
  - [ ] autobrake confirm (A320) and "reversers" / "autobrake off" on rollout;
  - [ ] off the taxi route;
  - [ ] divert suggestion on an emergency;
  - [ ] ATC repeated (chatty + option on).
- [ ] COM1 / COM2 / INT on the phone and the website tracker: messages land on the right channel, unread dots, talking on each.
- [ ] Play buttons (Quick Settings → ATC voice → Play buttons): play in the app, the phone and the tracker; nothing autoplays.
- [ ] Phone mic button: on-device dictation fills the message box (the `Info.plist` microphone and speech keys need committing and an Xcode rebuild).
- [ ] Maps across the date line (fly near 180°): the route and zones are drawn the short way, on the app, website and phone.
- [ ] Website links have no `.html` (tracker, dashboard, privacy ...), and the old `.html` links still work.
- [ ] The website's Worker is redeployed (`cd server && npx wrangler deploy`) so the copilot, COM2 and play buttons work through the relay.
- [ ] Privacy and Terms pages mention the cloud model; the home page's cloud card lists the current services.
