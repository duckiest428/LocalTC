# Website changelog

Every version of localtc.tech, newest first. It carries the same version as LocalTC; the website's changelog page
(Website) is built from this file.

## [0.5.0] - Upcoming

### Added
- **The Flight Tracker's taxi route** is a bold green line, outlined, and the other aircraft's labels sit in boxes that read over the map.
- **A new logo**: a control tower, across the site, its icon and the link preview.
- **Weather, areas & NOTAMs on the Flight Tracker**: a button shows the SIGMETs, TFRs and restricted areas around the flight and its airports' NOTAMs (a closed runway red), from what the app sends on.
- **The Flight Tracker shows ATC's hold and reroutes**: the racetrack with its fix and EFC, and a direct or vectors
  around an area as a line, as the app's Live Map does.
- **ChartFox sign-in for the app**: the account server holds ChartFox's client secret and does the sign-in's exchange,
  so the app's EFB can show your ChartFox charts; the tokens wait a few minutes for the app to collect them once, and
  nothing is kept after. No LocalTC account needed.
- **The phone's EFB through the account**: its checklist commands, notes, gate requests and notes to Ops wait for the
  app like a typed call, and the EFB as the app has it is passed on to the phone, held in memory only.
- **One changelog for everything**: the changelog page has a section each for the Website, the Companion App and
  the Main App, each with its own versions and its upcoming one.
- **A shared flight's whole replay** shows the map, the aircraft pointing the way it flies, and the calls and phases
  along the timeline in their places.

- **Real flights on the home page**: screenshots from the developer's own MSFS 2024 flights behind the hero (a
  slideshow that slowly pushes in), beside each group of features, and behind the local, install and support bands.
- **Gate to gate, station by station**: the eight controllers from clearance to the gate, each with a photo of that
  part of a flight and its call typed out in the callsign of the airline in the photo; it plays through by itself, or
  pick a leg.

### Changed
- **More life on the home page**: the header turns to glass as you scroll, with a line for how far down you are; the
  headline arrives word by word, the radio log leans toward the pointer, the photos move slower than the page, cards
  catch a light under the pointer, sections arrive with a blur, the subscription sum counts up, and light drifts
  behind every page. All of it stands still with reduced motion.
- **The pricing page's head** has a photo behind it.
- **Why pay monthly, install and where it is, redone**: one spacing for every section, LocalTC beside a subscription
  with a tick or a cross on each row, the subscription sum beside a photo with LocalTC's $0, the install steps joined by
  a line, and what works today ticked off beside what's still on the way.
- **The features in even pairs**: the copilot answers questions about the flight, and LocalTC flies on the grammar
  alone without a model.
- **No em dashes anywhere on the site.**
- **The changelog shows what's coming**: the upcoming version is on the page first, filled in as the work is done, and one product's tab is always the one showing.
- **Wrapped's icon** on the Dashboard is a gift box, no longer bars that looked like a phone's signal.

## [0.4.0] - 2026-09-28

### Added
- **The Flight Tracker full screen** (/tracker): the map takes the screen, with the traffic labelled, and the radio
  and intercom beside it.
- **Talk to ATC and the copilot from the Flight Tracker.**
- **The whole replay on a shared flight's page**, or the short one that plays by itself.
- **Wrapped goes back in time**, and **replay speeds from 0.5× to 1024×**.

### Changed
- The pages have addresses without `.html` (localtc.tech/dashboard, /tracker); the old ones still work.

### Fixed
- The path flown kept its start on long flights, and the part flown before the Flight Tracker was opened.
- The map across the date line.

## [0.3.2] - 2026-09-25

### Added
- **Share a flight**: a public card for any flight at a link like `localtc.tech/f/…`, and a page with more.
- **ATC Wrapped**: a week, month or year of flying as a story.
- **Display name** (Dashboard → Account), shown on what you share.

### Fixed
- Pages no longer mix a new page with an old cached script.
- The Dashboard on a phone no longer scrolls sideways.

## [0.3.0] - 2026-09-23

### Added
- **The Dashboard** (was Logbook), behind the sign-in: a live Flight Tracker, the logbook, and Support.

## [0.2.0] - 2026-09-22

### Added
- **An optional account** that copies the logbook to localtc.tech.
