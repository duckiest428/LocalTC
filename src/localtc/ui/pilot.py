"""The app's logbook tab and the optional account (Quick Settings > Account).

The logbook works for everyone. The account calls only happen after the pilot signs in; until then this
never contacts the server.
"""

import asyncio
import logging
import platform
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from localtc.account import Account, AccountError
from localtc.config import Config
from localtc.logbook import FlightRecord, Logbook
from localtc.ui.server import HttpError

log = logging.getLogger(__name__)

CALLS_POLL_S = 2.0  # while somebody watches through the server: their typed calls are picked up at least this often
RETRY_REPLAYS = 5  # with "upload replays" on, the last this many flights whose replay isn't on the account yet go up after a flight


class PilotRoutes:
    def __init__(self, cfg: Callable[[], Config], publish: Callable[[str, Any], None],
                 logbook: Logbook | None = None, account: Account | None = None, *, hub: Any = None,
                 on_signed_in: Callable[[], Awaitable[None]] | None = None,
                 on_signed_out: Callable[[], Awaitable[None]] | None = None) -> None:
        self.cfg = cfg
        self.publish = publish
        self._logbook = logbook
        self._account = account
        self.sync_state = ""  # the last sync's outcome, for the settings card
        self.hub = hub  # ui.companion.CompanionHub: what goes to a phone watching through the server
        self.on_signed_in = on_signed_in
        self.on_signed_out = on_signed_out
        # A call typed on the phone or the website: (words, "atc" to transmit on COM1 or "crew" for the copilot).
        self.on_call: Callable[[str, str], None] | None = None
        self._outbox: list[tuple[str, Any]] = []
        self._relay_task: asyncio.Task | None = None
        self._linked = False
        if hub is not None:
            hub.remote = self.relay

    @property
    def logbook(self) -> Logbook:
        if self._logbook is None:
            self._logbook = Logbook()
        return self._logbook

    @property
    def account(self) -> Account:
        if self._account is None or self._account.api_url != self.cfg().account.api_url.rstrip("/"):
            self._account = Account(self.cfg().account.api_url, logbook=self.logbook)
        return self._account

    def routes(self) -> dict:
        get, post = "GET", "POST"
        return {
            (get, "logbook"): self.api_logbook,
            (post, "logbook/delete"): self.api_logbook_delete,
            (post, "logbook/rebuild"): self.api_logbook_rebuild,
            (get, "replay"): self.api_replay,
            (post, "replay/upload"): self.api_replay_upload,
            (post, "replay/remove"): self.api_replay_remove,
            (post, "share"): self.api_share,
            (post, "share/image"): self.api_share_image,
            (post, "share/remove"): self.api_share_remove,
            (get, "wrapped"): self.api_wrapped,
            (post, "wrapped/share"): self.api_wrapped_share,
            (get, "account"): self.api_account,
            (post, "account/start"): self.api_start,
            (post, "account/finish"): self.api_finish,
            (post, "account/logout"): self.api_logout,
            (post, "account/sync"): self.api_sync,
            (post, "account/delete"): self.api_delete,
        }

    # --- logbook ------------------------------------------------------------------------------------------------

    async def api_logbook(self, args: dict) -> dict:
        if not self._linked:  # lines from before the logbook kept its recordings: once a run
            self._linked = True
            await asyncio.to_thread(self.logbook.link_recordings, Path(self.cfg().recorder.dir).resolve())
            await asyncio.to_thread(self.logbook.fill_aircraft)
        flights = await asyncio.to_thread(self.logbook.flights)
        from localtc.logbook import totals

        return {"flights": [_row(f) for f in flights], "totals": totals(flights)}

    # --- replays ------------------------------------------------------------------------------------------------

    def _replay(self, flight_id: str) -> tuple[FlightRecord, bytes]:
        from localtc.replay.rewatch import replay_for

        record = self.logbook.get(flight_id)
        if record is None:
            raise HttpError(404, "No such flight")
        if not _has_recording(record):
            raise HttpError(404, "This flight's recording isn't on this computer, so there's nothing to replay.")
        try:
            return record, replay_for(record.recording, record)
        except (OSError, ValueError) as exc:
            raise HttpError(422, f"Couldn't read this flight's recording: {exc}") from None

    async def api_replay(self, args: dict) -> dict:
        from localtc.replay.rewatch import decode

        _, data = await asyncio.to_thread(self._replay, str(args.get("id", "")))
        return decode(data)

    async def api_replay_upload(self, args: dict) -> dict:
        flight_id = str(args.get("id", ""))
        await self.upload_replay(flight_id, raise_errors=True)
        return await self.api_logbook({})

    async def api_replay_remove(self, args: dict) -> dict:
        flight_id = str(args.get("id", ""))
        await self._do(self.account.delete_replay, flight_id)
        await asyncio.to_thread(self.logbook.mark_replay, flight_id, None)
        return await self.api_logbook({})

    async def upload_replay(self, flight_id: str, *, raise_errors: bool = False) -> bool:
        """A flight's replay to the account. Its logbook line goes first: the server keeps replays of its
        flights only."""
        try:
            record, data = await asyncio.to_thread(self._replay, flight_id)
            if record.synced_at is None:
                await self._do(self.account.sync)
            await self._do(self.account.upload_replay, flight_id, data)
        except HttpError as exc:
            log.info("Couldn't upload the replay of %s: %s", flight_id, exc)
            if raise_errors:
                raise
            return False
        await asyncio.to_thread(self.logbook.mark_replay, flight_id, _now())
        return True

    # --- sharing and Wrapped: through the account ---------------------------------------------------------------

    def _signed_in(self) -> None:
        if not self.account.signed_in:
            raise HttpError(401, "Sign in to the account first (Quick Settings > Account).")

    async def api_share(self, args: dict) -> dict:
        """A public card for a flight: the page has already shown the pilot the card and the line it quotes."""
        self._signed_in()
        flight_id = str(args.get("id", ""))
        record = await asyncio.to_thread(self.logbook.get, flight_id)
        if record is None:
            raise HttpError(404, "No such flight")
        if record.synced_at is None:  # the server shares its own copy of the line
            await self._do(self.account.sync)
        with_replay = args.get("replay") is True
        if with_replay and record.replay_uploaded_at is None and record.recording:
            # The page's mini replay is cut from the account's copy of the replay: it goes up first.
            await self.upload_replay(flight_id, raise_errors=True)
        made = await self._do(self.account.share, {"kind": "flight", "ref": flight_id, "quote": args.get("quote"),
                                                    "names": args.get("names") or {}, "replay": with_replay})
        await asyncio.to_thread(self.logbook.mark_shared, flight_id, made["url"])
        return made

    async def api_share_image(self, args: dict) -> dict:
        """The card's picture, drawn by the page (a data: URL of a PNG), for the link's preview."""
        self._signed_in()
        import base64

        slug, url = str(args.get("slug", "")), str(args.get("png", ""))
        if not slug.isalnum() or not url.startswith("data:image/png;base64,"):
            raise HttpError(400, "Send the card as a PNG.")
        await self._do(self.account.share_image, slug, base64.b64decode(url.partition(",")[2]))
        return {"ok": True}

    async def api_share_remove(self, args: dict) -> dict:
        self._signed_in()
        flight_id = str(args.get("id", ""))
        record = await asyncio.to_thread(self.logbook.get, flight_id)
        slug = (record.share_url or "").rstrip("/").rpartition("/")[2] if record else str(args.get("slug", ""))
        if slug:
            try:
                await self._do(self.account.unshare, slug)
            except HttpError as exc:
                if exc.status != 404:  # already gone (unshared on the website): fine
                    raise
        if record is not None:
            await asyncio.to_thread(self.logbook.mark_shared, flight_id, None)
        return {"ok": True}

    async def api_wrapped(self, args: dict) -> dict:
        self._signed_in()
        if self.cfg().account.sync:  # the recap is of the synced logbook: bring it up to date first
            try:
                await self._do(self.account.sync)
            except HttpError as exc:
                log.info("Couldn't sync before Wrapped: %s", exc)
        return await self._do(self.account.wrapped, _period(args))

    async def api_wrapped_share(self, args: dict) -> dict:
        self._signed_in()
        return await self._do(self.account.share, {"kind": "wrapped", **_period(args)})

    async def api_logbook_rebuild(self, args: dict) -> dict:
        """A line measured again from its recording; synced again if the account is signed in."""
        if await asyncio.to_thread(self.logbook.rebuild, str(args.get("id", ""))) is None:
            raise HttpError(404, "This flight's recording isn't on this computer, or has no flight in it.")
        if self.account.signed_in and self.cfg().account.sync:
            try:
                await self._do(self.account.sync)
            except HttpError as exc:
                log.info("Couldn't sync the rebuilt line: %s", exc)
        return await self.api_logbook({})

    async def api_logbook_delete(self, args: dict) -> dict:
        """From this computer's logbook only. A synced copy is deleted on the website."""
        if not await asyncio.to_thread(self.logbook.delete, str(args.get("id", ""))):
            raise HttpError(404, "No such flight")
        return await self.api_logbook({})

    # --- account ------------------------------------------------------------------------------------------------

    def view(self) -> dict:
        a, c = self.account, self.cfg().account  # reads the credential store; no network
        return {"signed_in": a.signed_in, "email": a.email, "api_url": c.api_url, "dashboard": c.dashboard_url,
                "sync": c.sync, "companion": c.companion, "companion_lan": c.companion_lan,
                "companion_remote_map": c.companion_remote_map, "upload_replays": c.upload_replays,
                "last_sync": self.sync_state}

    async def _do(self, fn: Callable, *args: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args)
        except AccountError as exc:
            raise HttpError(exc.status if 400 <= exc.status < 500 else 502, str(exc)) from None

    async def api_account(self, args: dict) -> dict:
        view = await asyncio.to_thread(self.view)
        view["unsynced"] = len(await asyncio.to_thread(self.logbook.unsynced))
        return view

    async def api_start(self, args: dict) -> dict:
        message = await self._do(self.account.start, _email(args))
        return {**(await self.api_account({})), "message": message}

    async def api_finish(self, args: dict) -> dict:
        code = "".join(ch for ch in str(args.get("code", "")) if ch.isdigit())
        if len(code) != 6:
            raise HttpError(400, "Enter the 6-digit code from the email.")
        await self._do(self.account.finish, _email(args), code, f"LocalTC on {platform.node() or 'this PC'}")
        if self.cfg().account.sync:
            await self.sync()
        if self.on_signed_in is not None:
            await self.on_signed_in()
        return await self.api_account({})

    async def api_logout(self, args: dict) -> dict:
        await self._do(self.account.logout)
        self.sync_state = ""
        if self.on_signed_out is not None:
            await self.on_signed_out()
        return await self.api_account({})

    async def api_sync(self, args: dict) -> dict:
        await self.sync(raise_errors=True)
        return await self.api_account({})

    async def api_delete(self, args: dict) -> dict:
        await self._do(self.account.delete_account, _email(args))
        self.sync_state = ""
        if self.on_signed_out is not None:
            await self.on_signed_out()
        return await self.api_account({})

    async def sync(self, *, raise_errors: bool = False) -> None:
        """Upload new logbook lines. After a flight this runs by itself; a failure waits for the next one."""
        if not self.account.signed_in:
            return
        try:
            result = await self._do(self.account.sync)
            self.sync_state = f"Synced {result.uploaded} new flight{'s' if result.uploaded != 1 else ''}."
        except HttpError as exc:
            self.sync_state = f"Sync failed: {exc}"
            if raise_errors:
                raise
        self.publish("account", await self.api_account({}))

    async def after_flight(self) -> None:
        c = self.cfg().account
        if c.sync:
            await self.sync()
        if c.upload_replays and self.account.signed_in:
            # This flight's, and any of the last few that didn't go up (refused, or the PC went offline): a replay
            # once turned away for a mark too long stayed off the account for good.
            recent = await asyncio.to_thread(self.logbook.flights, RETRY_REPLAYS)
            waiting = [r for r in recent if _has_recording(r) and r.replay_uploaded_at is None]
            uploaded = [await self.upload_replay(r.id) for r in waiting]
            if any(uploaded):
                self.publish("account", await self.api_account({}))

    async def live(self, status: dict, *, force: bool = False) -> None:
        """The companion app's view of the flight, when signed in and the companion is on."""
        if not self.cfg().account.companion or self._account is None or not self._account.signed_in:
            return
        try:
            await asyncio.to_thread(self._account.live, status, force=force)
            if status.get("active") and self._account.watchers > 0 and self.on_call is not None \
                    and time.monotonic() - self._account.relayed_at >= CALLS_POLL_S:
                # Somebody watching who may type a call, and nothing else went up just now to bring it back.
                await asyncio.to_thread(self._account.poll_calls)
        except AccountError as exc:
            log.debug("Companion update failed: %s", exc)
        self._deliver_calls()
        if self.hub is not None:
            self.hub.watching(self._account.watchers)

    def _deliver_calls(self) -> None:
        """The radio calls typed on the phone or the website, transmitted as if typed here. One that can't go (no
        flight running) is said on the phone's radio log, so whoever typed it knows."""
        if self._account is None or self.on_call is None:
            return
        for text, to in self._account.take_calls():
            try:
                self.on_call(text, to)
            except (RuntimeError, ValueError) as exc:
                log.info("A call from the phone couldn't be transmitted: %s", exc)
                if self.hub is not None:
                    self.hub.add_radio({"kind": "alert", "t": None, "text": f"Not transmitted: {exc or 'no flight running'}"})

    async def share_connect(self, lan: list[str], key: str) -> None:
        """Tell the account where the phone can find this PC on the local network."""
        if not lan or not self.cfg().account.companion:
            return
        try:
            await self._do(self.account.connect_info, lan, key)
        except HttpError as exc:
            log.info("Couldn't share the companion address with the account: %s", exc)

    def relay(self, kind: str, data: Any) -> None:
        """Queue something for a phone watching through the server. One sender drains the queue, merging
        position updates, so a slow connection falls behind by frames rather than piling up requests."""
        if not self.cfg().account.companion or self._account is None:
            return
        self._outbox.append((kind, data))
        if self._relay_task is None or self._relay_task.done():
            self._relay_task = asyncio.get_running_loop().create_task(self._drain())

    async def _drain(self) -> None:
        while self._outbox:
            batch, self._outbox = self._outbox, []
            frame: dict = {}
            radio: list = []
            alerts: list = []
            airports: list | None = None
            live_map: dict = {}  # route and zones: the latest of each (None: gone)
            clips: list = []  # (id, wav): transmissions to play again
            for kind, data in batch:
                if kind == "clip":
                    clips.append(data)
                elif kind == "frame":
                    frame.update(data)
                elif kind == "radio":
                    radio.extend(data)
                elif kind == "alert":
                    alerts.append(data)
                elif kind == "airports":
                    airports = data
                elif kind in ("route", "zones"):
                    live_map[kind] = data
            # The path on its own: with the traffic around a big airport in the same request it went over the
            # server's size limit, and the whole frame (path included) was refused.
            trail = frame.pop("trail", None)
            try:
                if trail:
                    await asyncio.to_thread(self._account.frame, trail=trail)
                if frame:
                    await asyncio.to_thread(self._account.frame, **frame)
                if radio:
                    await asyncio.to_thread(self._account.radio, radio[-50:])
                if airports is not None:
                    await asyncio.to_thread(self._account.airports, airports)
                if live_map:
                    await asyncio.to_thread(self._account.live_map, **{k: v for k, v in live_map.items() if v is not None},
                                            clear=tuple(k for k, v in live_map.items() if v is None))
                for alert in alerts:
                    await asyncio.to_thread(self._account.alert, alert)
                for key, wav in clips[-6:]:
                    await asyncio.to_thread(self._account.clip, key, wav)
            except AccountError as exc:
                log.debug("Companion relay failed: %s", exc)
            self._deliver_calls()
            if self.hub is not None:
                self.hub.watching(self._account.watchers)


def _has_recording(record: FlightRecord) -> bool:
    return bool(record.recording) and Path(record.recording).is_dir()


def _row(record: FlightRecord) -> dict:
    """A logbook line for the page: whether it can be replayed here, not where the recording is."""
    row = record.to_dict()
    row["has_recording"] = _has_recording(record)
    row.pop("recording", None)
    return row


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _period(args: dict) -> dict:
    """A Wrapped period from the page, only the fields the server takes."""
    return {k: str(args[k]) for k in ("period", "from", "to", "tz", "label") if args.get(k) not in (None, "")}


def _email(args: dict) -> str:
    email = str(args.get("email", "")).strip()
    if "@" not in email or len(email) > 254:
        raise HttpError(400, "Enter an email address.")
    return email

