"""The LocalTC app: a window with the radio log, flight plan, settings, map and airport lookup.

The page is plain HTML/JS (``ui/static``) served on 127.0.0.1 by ``ui.server`` and shown in a
window of its own (pywebview, Edge WebView2 on Windows), or in the default browser without it.
The flight runs in the same process as ``localtc run``, through ``app.run_session``.

    localtc app              # the window
    localtc app --browser    # in the default browser instead (development, or without WebView2)
"""

import asyncio
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

log = logging.getLogger(__name__)

WINDOW_SIZE = (620, 860)
WINDOW_MIN = (460, 560)


def run_app(*, config_path: str | None = None, port: int | None = None, browser: bool = False,
            open_page: bool = True) -> int:
    from localtc.ui.controller import AppController
    from localtc.ui.server import AppServer

    controller = AppController(config_path=config_path)
    ready = threading.Event()
    stop: dict = {}
    box: dict = {}

    async def serve() -> None:
        loop = asyncio.get_running_loop()
        controller.attach(loop)
        server = AppServer(controller.routes(), controller.stream, controller.on_connect)
        await server.start(port if port is not None else controller.cfg.ui.port)
        box["url"] = server.url
        stopped = asyncio.Event()
        stop["set"] = lambda: loop.call_soon_threadsafe(stopped.set)
        ready.set()
        log.info("LocalTC app at %s", server.url)
        try:
            await stopped.wait()
        finally:
            await controller.shutdown()
            await server.close()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), name="localtc-app", daemon=True)
    thread.start()
    if not ready.wait(timeout=20):
        print("LocalTC couldn't start its window server")
        return 1
    url = box["url"]
    window = not browser and controller.cfg.ui.window and open_page

    def quit_app() -> None:
        """Close LocalTC from the page (to install an update): the window if there is one, then the server."""
        try:
            import webview

            for w in list(webview.windows):
                w.destroy()
        except Exception as exc:  # no pywebview (browser mode): stopping the server is enough
            log.debug("No window to close: %s", exc)
        if "set" in stop:
            stop["set"]()

    controller.updates.quit = quit_app
    try:
        if window and _open_window(url):
            pass  # returns when the window is closed
        else:
            print(f"LocalTC is running at {url}  (Ctrl-C to quit)")
            if open_page:
                webbrowser.open(url)
            while thread.is_alive():
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if "set" in stop:
            stop["set"]()
        thread.join(timeout=20)
        controller.updates.on_exit()
    return 0


def _open_window(url: str) -> bool:
    """A native window (pywebview). False when there's none to be had; the browser is used instead."""
    try:
        import webview
    except ImportError:
        log.info("pywebview isn't installed: opening LocalTC in the browser")
        return False
    try:
        webview.create_window("LocalTC", url, width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN,
                              background_color="#1b1e22")
        webview.start()
        return True
    except Exception as exc:  # no WebView2 runtime, no GUI
        log.warning("No app window (%s): opening LocalTC in the browser", exc)
        return False


def main() -> int:
    """The windowed launcher (``localtc-app``, no console)."""
    from localtc.console import log_dir, setup_logging

    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:  # started without a console (pythonw)
            setattr(sys, name, open(os.devnull, "w"))
    use_project_folder()
    setup_logging(quiet_console=True, log_file=log_dir() / "localtc.log")
    return run_app()


def use_project_folder() -> None:
    """Work in the LocalTC folder (config/, recordings/) even when started from somewhere else."""
    root = Path(__file__).resolve().parents[3]
    if not Path("config/localtc.toml").is_file() and (root / "config" / "localtc.toml").is_file():
        os.chdir(root)
