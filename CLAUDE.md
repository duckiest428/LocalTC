# LocalTC

Local ATC and a copilot for MSFS 2024: Python (`src/localtc`), the app's page (`src/localtc/ui/static`), the website
(`site/`), the account Worker (`server/`, TypeScript), the companion app (`ios/`). Docs in `docs/` (architecture,
replay format, phraseology, test checklists).

Two machines work on it:

- **The Mac** writes most of the code and builds the installer. It has no sim.
- **The Windows PC** has MSFS 2024. A session there runs and fixes what only the sim can answer: start with
  [docs/windows-session.md](docs/windows-session.md).

## Rules

- Commit; never push. The user pushes and pulls with GitHub Desktop on both machines. Pull before starting.
- Never commit `.DS_Store`, `ios/Info.plist`, `ios/*.xcodeproj/project.pbxproj`, `docs/roadmap.md` or
  `.claude/launch.json` unless asked. No git repack, gc or alternates without asking.
- Commit messages and the CHANGELOG say what changed, never which test flight it came from.
- New work goes in the CHANGELOG's top section, `## [0.5.0] - Upcoming`, until the user names the next release. Don't
  bump `pyproject.toml` / `__version__` and don't build the installer (the exe) unless asked. Every commit is
  installed on the PC with `Install LocalTC.cmd` (the repo's root: it runs `install\install.ps1` on the checkout,
  so it's always this commit's; nothing to build). Keep it working, and after a commit tell the user to pull and
  double-click it.
- The language model: never silently swap a rejected model reply for an unrelated scripted line (a fallback must fit
  the call, and the pilot is told). In the LLM modes every call gets the model's reply.
- API keys only from environment variables, never written to a file or committed. Free tiers only through their
  public APIs (nothing reverse-engineered).
- `site/*.js|css` shared with the app are copied to `src/localtc/ui/static/` (tests/test_site.py checks they match).

## Commands

Mac (the repo is on iCloud Desktop: `PYTHONPATH=src`, and run tests with a timeout, targeted first):

```
PYTHONPATH=src .venv/bin/python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_llm_live.py
.venv/bin/lint-imports
cd server && npx vitest run
PYTHONPATH=src .venv/bin/python -m localtc app --browser --port 8765
python3 tools/serve_site.py 8000
```

Windows (PowerShell, the venv not activated; after a pull `.venv\Scripts\pip install -e ".[dev]"`):

```
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_llm_live.py
.venv\Scripts\localtc app
.venv\Scripts\localtc debug aircraft | hands | traffic
"Install LocalTC.cmd"   # install this commit (double-click works too)
```
