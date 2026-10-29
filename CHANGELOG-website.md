# Website changelog

Every version of localtc.tech, newest first. It carries the same version as LocalTC; the website's changelog page
(Website) is built from this file.

## [0.5.3] - Upcoming

Nothing yet: what's done for the next version is listed here as it lands.

## [0.5.2] - 2026-10-29

### Added
- **A blog post for LocalTC 0.5.0**: what's new, and how to get it.
- **The blog's authors can edit and delete every post**, the site's own too (the ones written in the repository): an
  edit keeps the post's date and isn't emailed to the followers as a new post, and a deleted post stays gone.

### Fixed
- **A change on the blog shows at once**: after an edit or a deletion, the list could show the old posts for up to a
  minute.

## [0.5.0] - 2026-10-26

### Added
- **The new setup on the download pages**: the home page's install steps and the dashboard's Download tab say what
  LocalTC-Setup.exe does now (everything LocalTC needs to start, nothing downloaded) and that the app's setup guide
  downloads the models, the language model's engine and GPU support.
- **The Flight Tracker draws aircraft as what they are**: an airliner, a widebody, a regional or business jet, a
  turboprop, a light aircraft or a helicopter, the traffic and your own aircraft (a Cessna was an airliner).
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
- **The blog** (localtc.tech/blog): news about LocalTC, newest first, a post at a time. Its author writes on the page
  itself, signed in: a title, a summary, a cover picture and the post, with buttons for headings, bold, links, lists
  and quotes, a live preview, and pictures dropped, pasted or picked (made smaller first, then kept by the account
  server). Only the blog's author's account can write; anyone can read what's published. Posts can also be written in
  the repository (blog-posts.json).
- **Stats on the dashboard**: the logbook in numbers. Equator and date-line crossings, the farthest north and south,
  time since the last flight, the longest gap and streak, the countries flown to (out of 197) with a passport stamp
  each by continent, the top airlines and aircraft, short, medium and long flights, flights per year, a year of days
  flown, a carbon estimate, and from the sim the softest landing, the fuel burned, the top speed, night landings,
  time in cloud, the coldest air and the strongest wind. Only with Stats and metrics on in the app; off, the page says
  how to turn it on and the account holds none of it.
- **Download on the dashboard**: the installer, the version it gets, what it needs and does, updating, and the other
  ways to run it.
- **Every Wrapped**: any week, month or year can be opened back to the first flight, not only the last one, and
  "All weeks" (or months, years) charts the flights in each to pick one from.
- **Reactions and readers on the blog**: under each post, like, love, cleared for takeoff and applause, one of each per
  reader and no account needed; each post counts its readers, which its author sees on the blog. Nothing is kept on
  the reader's device: the server knows a reader only by a salted hash.
- **Follow the blog**: a small Follow button emails each new post to your account's address, from
  marketing@localtc.tech, once, the first time it's published; every email has a link that stops them, no sign-in
  needed. Signed out, it offers the sign-in and brings you back to follow.
- **A page for addresses that don't exist**: "Say again?", the address read back like a radio call, and the way home,
  in place of GitHub's.
- **The LocalTC Discord**: a button in every page's header, on the home page and on the Dashboard's Support page.
- **Attach anything to a support message** on the Dashboard: a session export, logs or screenshots, up to 5 files and
  25 MB.
- **A world map on Stats**: the countries flown to, shaded by visits.

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

### Fixed
- **The browser tab's icon is the new logo**: browsers kept the old one, so the icon has a new address, and
  `favicon.ico` (which browsers ask for on their own) is the tower too.
- **The logbook map**: every route is one unbroken line from its airport. Where the routes go all the way round the world, the map is cut where the fewest of them cross, and a route over the cut runs on to its airport drawn again there. The busiest airports' dots no longer grow so large, and an airport's visits count landing there (or a first departure), not flying in and out again as two.
- **The pricing page's "Most popular" ribbon** shows whole again, over the card's top edge.

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
