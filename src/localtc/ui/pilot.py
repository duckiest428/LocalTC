"""The app's logbook tab and the optional account (Quick Settings > Account).

The logbook works for everyone. The account calls only happen after the pilot signs in; until then this
never contacts the server.
"""

import asyncio
import logging
import platform
from collections.abc import Awaitable, Callable
from typing import Any

from localtc.account import Account, AccountError
from localtc.config import Config
from localtc.logbook import Logbook
from localtc.ui.server import HttpError

log = logging.getLogger(__name__)


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
        self._outbox: list[tuple[str, Any]] = []
        self._relay_task: asyncio.Task | None = None
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
            (get, "account"): self.api_account,
            (post, "account/start"): self.api_start,
            (post, "account/finish"): self.api_finish,
            (post, "account/logout"): self.api_logout,
            (post, "account/sync"): self.api_sync,
            (post, "account/delete"): self.api_delete,
        }

    # --- logbook ------------------------------------------------------------------------------------------------

    async def api_logbook(self, args: dict) -> dict:
        flights = await asyncio.to_thread(self.logbook.flights)
        from localtc.logbook import totals

        return {"flights": [f.to_dict() for f in flights], "totals": totals(flights)}

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
                "companion_remote_map": c.companion_remote_map, "last_sync": self.sync_state}

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
        if self.cfg().account.sync:
            await self.sync()

    async def live(self, status: dict, *, force: bool = False) -> None:
        """The companion app's view of the flight, when signed in and the companion is on."""
        if not self.cfg().account.companion or self._account is None or not self._account.signed_in:
            return
        try:
            await asyncio.to_thread(self._account.live, status, force=force)
        except AccountError as exc:
            log.debug("Companion update failed: %s", exc)
        if self.hub is not None:
            self.hub.watching(self._account.watchers)

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
            for kind, data in batch:
                if kind == "frame":
                    frame.update(data)
                elif kind == "radio":
                    radio.extend(data)
                elif kind == "alert":
                    alerts.append(data)
            try:
                if frame:
                    await asyncio.to_thread(self._account.frame, **frame)
                if radio:
                    await asyncio.to_thread(self._account.radio, radio[-50:])
                for alert in alerts:
                    await asyncio.to_thread(self._account.alert, alert)
            except AccountError as exc:
                log.debug("Companion relay failed: %s", exc)
            if self.hub is not None:
                self.hub.watching(self._account.watchers)


def _email(args: dict) -> str:
    email = str(args.get("email", "")).strip()
    if "@" not in email or len(email) > 254:
        raise HttpError(400, "Enter an email address.")
    return email

