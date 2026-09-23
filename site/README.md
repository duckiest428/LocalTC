# The LocalTC website

Static files; the only library is Leaflet, served from `vendor/`. One page is built: `changelog.html`, from `CHANGELOG.md`, by
`python tools/build_site.py` (the Pages workflow runs it; it isn't committed).

```
index.html      the landing page
privacy.html    what we know about you (nothing, without an account)
terms.html      the AGPL, the no-warranty, the "not for real flight", the optional account
cookies.html    there are none, except the dashboard's sign-in cookie, and how to check
dashboard.html  the optional account (dashboard.js): the Flight Tracker (the flight in progress, live), the
                logbook (stats, the flights map, the flights), devices, export, delete
support.html    feedback and support requests, emailed to the developer by the account server
flightsmap.js   the flights map, also used by the app's Logbook tab (the same file: a test checks)
vendor/leaflet  Leaflet, for the maps (served from here; the tiles come from OpenStreetMap)
changelog.html  built from CHANGELOG.md
styles.css      the whole design system
app.js          reveals, the typing radio log, the subscription sum, copy buttons
fonts/          Inter and JetBrains Mono, served from here on purpose
logo.svg        the mark (the same file the app serves)
icon-180.png    apple-touch-icon
og.png          the link preview card
```

`logo.svg` is the source of truth for the mark. `icon-180.png`, `og.png` and the Windows icon at
`install/localtc.ico` are all generated from it by `python tools/make_icons.py` — change the SVG,
rerun that, and nothing drifts.

## Publishing

`.github/workflows/pages.yml` deploys this folder on every push to `master` that touches it.
**It only works once Pages is switched on:** repository → *Settings* → *Pages* →
*Build and deployment* → *Source*: **GitHub Actions**. The site is then at
<https://duckiest428.github.io/LocalTC/>, and *Actions* → *Site* → *Run workflow* republishes by
hand.

To look at it while editing, serve the folder and open the address:

```bash
python3 -m http.server 8080 --directory site
```

## The rules it keeps

- **No third-party requests.** Nothing is loaded from a CDN, an analytics service or a font host.
  The one exception is the logbook page calling LocalTC's own account server, `api.localtc.tech`
  (`server/`), and only while someone uses it.
  The fonts are files in `fonts/` for exactly this reason. Adding an embed or a script tag pointing
  somewhere else makes the privacy and cookie pages untrue, so don't, or change them first.
- **No cookies and no browser storage.** Same reason. There is no consent banner because there is
  nothing to consent to. Signing in to the logbook sets one strictly necessary, HttpOnly cookie on
  `api.localtc.tech`; the page itself stores nothing.
- **Contrast.** Every text colour clears WCAG AA against the surface it sits on (4.5:1 for body,
  3:1 for large text). The three `--text*` tokens are the palette; new greys need checking.
- **One radius scale** (`--r1`…`--r4`) and **elevation instead of borders**. If something needs
  separating, reach for a shadow or a background shift before a 1px line.
- **Colour is rationed.** Green marks one thing per section — the brand, the primary action, a
  correct readback, the winning column. Cyan is machine output, amber is money and caveats.
- **Motion is optional.** Everything animated is off under `prefers-reduced-motion`, and the reveal
  class is added by JavaScript so a page without it shows everything rather than nothing.

## Keeping it honest

- The radio log in the hero (`app.js`, `EXCHANGE`) is real phraseology the engine produces. If the
  templates change, change it here too.
- The "Working today" and "Not yet" lists mirror the status block in the top-level `README.md`.
- The claims in `privacy.html` about what the software does on the network were checked against the
  source. If LocalTC ever calls out to somewhere new, that page needs a line.
