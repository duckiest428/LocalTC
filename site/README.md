# The LocalTC website

A single static page, no build step and no dependencies: `index.html`, `styles.css`, `app.js`.
The colours are the app's own tokens (`src/localtc/ui/static/app.css`), so the site and the app look
like the same program.

`.github/workflows/pages.yml` publishes this folder to GitHub Pages on every push to `master` that
touches it. **It only works once Pages is switched on:** repository → *Settings* → *Pages* →
*Build and deployment* → *Source*: **GitHub Actions**. After that the site is at
<https://duckiest428.github.io/LocalTC/>, and *Actions* → *Site* → *Run workflow* republishes it by
hand.

To look at it while editing, serve the folder and open the address it prints:

```bash
python3 -m http.server 8080 --directory site
```

Things worth keeping true:

- The radio log in the hero (`app.js`, `EXCHANGE`) is real phraseology the engine actually produces.
  If the templates change, change it here too.
- The "Working today" and "Not yet" lists mirror the status block in the top-level `README.md`.
- Nothing is minified or bundled on purpose — the page should stay readable as source.
