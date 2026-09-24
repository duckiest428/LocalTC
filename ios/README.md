# LocalTC Companion (iOS)

The phone app for LocalTC, in five tabs:

- **My Flight**: the live map (IFR or VFR) with AI traffic and the route, and a card that opens the flight's details.
- **Comms**: the radio log as it happens, by COM1 or COM2, and a box to type a call to ATC (on the same Wi-Fi).
- **Frequencies**: who you're talking to and who's next, and every frequency at the departure and arrival airports.
- **Airports**: the departure and arrival airports' runways, ILS and ATIS.
- **EFB**: charts, checklists and performance, greyed out for now: it's coming.

Banners pop up for handoffs, clearances, traffic calls and emergencies.

It needs the optional LocalTC account. Sign in with the same email on the phone and in LocalTC on the PC
(Quick Settings → Account). No password: an emailed 6-digit code signs you in.

## How it connects
- **Same Wi-Fi:** straight to LocalTC on the PC (port 47800), found by the address the account passes on or
  by Bonjour. Nothing goes through the internet.
- **Anywhere else:** through the account server's relay. The PC sends the map, traffic and radio only while a
  phone watches that way, and only if Quick Settings → Account allows it; the server keeps them in memory,
  never stored.

The messages are in [`docs/companion-protocol.md`](../docs/companion-protocol.md). Settings in the app pick
Automatic, Same Wi-Fi only, or Through the server only.

## Building
Open `LocalTC Companion.xcodeproj` in Xcode (27 or later) and run it on your phone. With a free Apple ID,
set your team under Signing & Capabilities; the app then runs for 7 days before Xcode has to install it
again. Push notifications, TestFlight and the App Store need the paid Apple Developer Program: until then
alerts show in the app, and as local notifications while it's in the background.

`LocalTCKit/` holds the logic (account API, protocol, connection, flight state) with no UIKit. The app
target compiles those same files; the package exists so the tests run on a Mac:

```bash
cd ios/LocalTCKit
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer swift test --scratch-path /tmp/localtc-kit
```

(`--scratch-path` outside iCloud Drive: code signing refuses files carrying iCloud's extended attributes.)

## End to end
`ios/run-e2e.sh` runs the whole path in the Simulator on this Mac: a local account server (`wrangler dev`),
LocalTC replaying the San Diego → Phoenix recording, and the UI test, which signs in with the emailed code,
waits for the flight, looks at every tab and signs out. `--relay` turns off the PC's local-network server,
so everything goes through the relay.

`LogbookReplayTests` plays a flight's replay (Logbook → the flight → play, scrub, the transcript at the
landing). It needs an account on the local server holding one: upload a replay from the desktop app's
Logbook, signed in to `wrangler dev`, then run the test with `TEST_RUNNER_LOCALTC_REPLAY=<flight id>` and
the same `TEST_RUNNER_LOCALTC_API`, `_MAIL_LOG` and `_EMAIL` as the script sets. Without it, it's skipped.
