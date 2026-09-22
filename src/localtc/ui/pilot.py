"""The app's logbook tab and the optional account (Quick Settings > Account).

The logbook works for everyone. The account calls only happen after the pilot signs in; until then this
never contacts the server.
"""

import asyncio
import logging
import platform
from collections.abc import Callable
from typing import Any

from localtc.account import Account, AccountError
from localtc.config import Config
from localtc.logbook import Logbook
from localtc.ui.server import HttpError

log = logging.getLogger(__name__)


class PilotRoutes:
    def __init__(self, cfg: Callable[[], Config], publish: Callable[[str, Any], None],
                 logbook: Logbook | None = None, account: Account | None = None) -> None:
        self.cfg = cfg
        self.publish = publish
        self._logbook = logbook
        self._account = account
        self.sync_state = ""  # the last sync's outcome, for the settings card

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
            (post, "account/register"): self.api_register,
            (post, "account/login"): self.api_login,
            (post, "account/logout"): self.api_logout,
            (post, "account/reset"): self.api_reset,
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
                "sync": c.sync, "companion": c.companion, "last_sync": self.sync_state}

    async def _do(self, fn: Callable, *args: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, *args)
        except AccountError as exc:
            raise HttpError(exc.status if 400 <= exc.status < 500 else 502, str(exc)) from None

    async def api_account(self, args: dict) -> dict:
        view = await asyncio.to_thread(self.view)
        view["unsynced"] = len(await asyncio.to_thread(self.logbook.unsynced))
        return view

    async def api_register(self, args: dict) -> dict:
        message = await self._do(self.account.register, _email(args), _password(args))
        return {**(await self.api_account({})), "message": message}

    async def api_login(self, args: dict) -> dict:
        await self._do(self.account.login, _email(args), _password(args), f"LocalTC on {platform.node() or 'this PC'}")
        if self.cfg().account.sync:
            await self.sync()
        return await self.api_account({})

    async def api_logout(self, args: dict) -> dict:
        await self._do(self.account.logout)
        self.sync_state = ""
        return await self.api_account({})

    async def api_reset(self, args: dict) -> dict:
        return {"message": await self._do(self.account.reset_password, _email(args))}

    async def api_sync(self, args: dict) -> dict:
        await self.sync(raise_errors=True)
        return await self.api_account({})

    async def api_delete(self, args: dict) -> dict:
        await self._do(self.account.delete_account, _password(args))
        self.sync_state = ""
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


def _email(args: dict) -> str:
    email = str(args.get("email", "")).strip()
    if "@" not in email or len(email) > 254:
        raise HttpError(400, "Enter an email address.")
    return email


def _password(args: dict) -> str:
    password = str(args.get("password", ""))
    if not password:
        raise HttpError(400, "Enter the password.")
    return password
