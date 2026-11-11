# The sim's toolbar panel (VR)

A LocalTC button on the sim's toolbar opens a window inside the sim with the radio log (ATC, you, the copilot, the
other traffic, the cabin and the ramp) and the radios, so a pilot in VR can read what was said without taking the
headset off. It's how SayIntentions does it: a small Community package whose toolbar window shows the app's own page.

- **The page:** `src/localtc/ui/static/panel.html` and `panel.js`, plain script for the sim's own browser. It asks the
  app for `/api/panel` each second.
- **The port:** the app serves it on `127.0.0.1:47810` as well as its own window's port (`[ui] panel_port`; 0 turns it
  off). Nothing listens on the network.
- **The package:** `tools/sim_panel` is the SDK project (the panel definition, the wrapper page with its iframe, the
  toolbar icon). The definition has to be compiled to the sim's `.spb`, which only the SDK's package tool does, and
  MSFS 2024's package tool builds through a running sim. So, once, with MSFS 2024 running:

  ```
  .venv\Scripts\python tools\build_sim_panel.py
  ```

  That puts the built package in `src/localtc/simpkg/localtc-panel`; commit it. From then on the app copies it into
  each sim's Community folder when it starts (MSFS 2024's and 2020's), and the button shows from the sim's next start.
