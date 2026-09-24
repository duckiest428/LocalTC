"""The optional LocalTC account: sign in, sync the logbook, and feed the companion app.

Nothing here runs unless the pilot signs in (Quick Settings > Account). Without an account LocalTC is
exactly the same, and the logbook is still kept on this computer.

What an account sends to the LocalTC server (``[account] api_url``, a Cloudflare Worker, ``server/``):
- the logbook's summary lines (``logbook.FlightRecord``: airports, times, distance, the landing rate);
- while flying, if the companion app is on: the phase, the frequency tuned and next, and ATC's last line;
- only while a phone is watching away from the PC's network, and if the pilot allows it
  (``[account] companion_remote_map``): the aircraft's position, nearby traffic and the radio log. The
  server passes these to the phone and keeps them in memory only; they're never stored.
- a flight's replay, only when the pilot uploads it from the Logbook or turns on ``[account]
  upload_replays``: the track (thinned to every few seconds) and the radio transcript, stored with the
  flight until either is deleted (``replay.rewatch``).
Never audio, recordings or settings. On the same network the phone talks to the PC directly
(``ui/companion.py``) and none of that goes through the server.

There are no passwords. Signing in sends a 6-digit code to the email address; typing it in the app signs
this computer in. The sign-in token that comes back is kept in the system's credential store (Windows
Credential Manager, the macOS Keychain) through ``keyring``, never in a file. Without a credential store the token lasts until LocalTC
closes.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from localtc import __version__
from localtc.logbook import Logbook

log = logging.getLogger(__name__)

KEYRING_SERVICE = "LocalTC"
BATCH = 100  # flights per upload
LOCAL_ONLY = ("synced_at", "recording", "replay_uploaded_at")  # a logbook line's fields that stay on this computer
LIVE_EVERY_S = 5.0  # the companion's status at most this often, except when something changes
LIVE_HEARTBEAT_S = 15.0  # and at least this often while flying: the answer says whether a phone is watching


class AccountError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class TokenStore:
    """Where the sign-in lives between launches: the system credential store, or memory without one."""

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self.service = service
        self._memory: dict[str, str] = {}

    def _keyring(self):
        """The system credential store, or None without a usable one (the "null" and "fail" backends
        accept a token and forget it: memory is better than that)."""
        try:
            import keyring

            return keyring if getattr(keyring.get_keyring(), "priority", 1) > 0 else None
        except ImportError:
            return None

    @staticmethod
    def _errors() -> tuple[type[BaseException], ...]:
        """What a credential store raises when it's locked, missing or refuses (backends differ)."""
        try:
            from keyring.errors import KeyringError

            return KeyringError, RuntimeError, OSError
        except ImportError:
            return RuntimeError, OSError

    def get(self, key: str) -> str | None:
        kr = self._keyring()
        if kr is not None:
            try:
                return kr.get_password(self.service, key)
            except self._errors() as exc:
                log.info("Credential store unavailable: %s", exc)
        return self._memory.get(key)

    def set(self, key: str, value: str) -> None:
        kr = self._keyring()
        if kr is not None:
            try:
                kr.set_password(self.service, key, value)
                return
            except self._errors() as exc:
                log.info("Credential store unavailable, keeping the sign-in until LocalTC closes: %s", exc)
        self._memory[key] = value

    def delete(self, key: str) -> None:
        self._memory.pop(key, None)
        kr = self._keyring()
        if kr is not None:
            try:
                kr.delete_password(self.service, key)
            except self._errors():  # not there: nothing to do
                log.debug("No stored %s to delete", key)


Transport = Callable[[str, str, dict | bytes | None, dict[str, str]], tuple[int, Any]]


def _http(method: str, url: str, body: dict | bytes | None, headers: dict[str, str], timeout_s: float = 15.0) -> tuple[int, Any]:
    """JSON both ways; a ``bytes`` body (a replay) goes as it is, gzipped."""
    raw_body = isinstance(body, bytes)
    data = body if raw_body else json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/gzip" if raw_body else "application/json", "User-Agent": f"LocalTC/{__version__}",
        **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except ValueError:
            return exc.code, {"error": raw.decode("utf-8", "replace")[:200]}


@dataclass
class SyncResult:
    uploaded: int
    remaining: int


class Account:
    def __init__(self, api_url: str, *, store: TokenStore | None = None, transport: Transport = _http,
                 logbook: Logbook | None = None) -> None:
        self.api_url = api_url.rstrip("/")
        self.store = store or TokenStore()
        self.transport = transport
        self._logbook = logbook
        self.email: str | None = self.store.get("email")
        self.last_sync: str | None = None
        self._live_sent = 0.0
        self._live_last: dict | None = None
        self.watchers = 0  # phones following the flight through the server, as of the last answer

    @property
    def logbook(self) -> Logbook:
        if self._logbook is None:
            self._logbook = Logbook()
        return self._logbook

    @property
    def token(self) -> str | None:
        return self.store.get("token")

    @property
    def signed_in(self) -> bool:
        return self.token is not None

    def _call(self, method: str, path: str, body: dict | bytes | None = None, *, auth: bool = True) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if auth and self.token else {}
        try:
            status, data = self.transport(method, f"{self.api_url}{path}", body, headers)
        except (OSError, urllib.error.URLError) as exc:
            raise AccountError(f"Can't reach the LocalTC server ({exc}).") from None
        if status == 401 and auth:
            self._forget()  # revoked from the website, or expired: signed out here too
        if status >= 400:
            message = data.get("error") if isinstance(data, dict) else None
            raise AccountError(message or f"The server said {status}.", status)
        return data

    def _forget(self) -> None:
        self.store.delete("token")
        self.store.delete("email")
        self.email = None
        self.logbook.forget_sync()  # a different account may sign in next: everything uploads again

    def start(self, email: str) -> str:
        """Email a sign-in code to the address (creating the account the first time)."""
        data = self._call("POST", "/v1/auth/start", {"email": email}, auth=False)
        return (data or {}).get("message", "Check your email for the sign-in code.")

    def finish(self, email: str, code: str, device: str) -> None:
        """Sign this computer in with the code from the email."""
        data = self._call("POST", "/v1/auth/finish", {"email": email, "code": code, "kind": "desktop",
                                                      "device": device}, auth=False)
        if self.email is not None and self.email != data["user"]["email"]:
            self.logbook.forget_sync()
        self.store.set("token", data["token"])
        self.store.set("email", data["user"]["email"])
        self.email = data["user"]["email"]

    def logout(self) -> None:
        try:
            if self.signed_in:
                self._call("POST", "/v1/auth/logout")
        except AccountError as exc:
            log.info("Signing out on the server failed (signed out here anyway): %s", exc)
        self._forget()

    def me(self) -> dict:
        return self._call("GET", "/v1/me")

    def delete_account(self, confirm_email: str) -> None:
        """Deletes the account and every flight synced to it, on the server. The local logbook stays.
        ``confirm_email`` is the account's address, typed again."""
        self._call("DELETE", "/v1/me", {"email": confirm_email})
        self._forget()

    def sync(self) -> SyncResult:
        """Upload the flights the server doesn't have yet, a batch at a time."""
        if not self.signed_in:
            raise AccountError("Not signed in.")
        pending = self.logbook.unsynced()
        uploaded = 0
        for i in range(0, len(pending), BATCH):
            batch = pending[i:i + BATCH]
            payload = [{k: v for k, v in f.to_dict().items() if k not in LOCAL_ONLY} for f in batch]
            data = self._call("POST", "/v1/flights", {"flights": payload})
            accepted = list((data or {}).get("accepted", []))
            self.logbook.mark_synced(accepted)
            uploaded += len(accepted)
        self.last_sync = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return SyncResult(uploaded=uploaded, remaining=len(pending) - uploaded)

    def upload_replay(self, flight_id: str, replay: bytes) -> None:
        """A flight's replay (its track and radio transcript, gzipped), for localtc.tech and the phone. Only when
        the pilot asks: one flight's Upload, or the "upload replays" setting."""
        if not self.signed_in:
            raise AccountError("Sign in to your LocalTC account first (Account, in Quick Settings).", 401)
        self._call("PUT", f"/v1/flights/{flight_id}/replay", replay)

    def delete_replay(self, flight_id: str) -> None:
        """The replay off the account; the flight's logbook line stays."""
        self._call("DELETE", f"/v1/flights/{flight_id}/replay")

    def live(self, status: dict, *, force: bool = False, now: float | None = None) -> bool:
        """The companion app's view of the flight: sent when it changes (at most every few seconds, at once
        when ``force``), and as a heartbeat otherwise. Each answer says how many phones watch remotely."""
        if not self.signed_in:
            return False
        now = time.monotonic() if now is None else now
        since = now - self._live_sent
        changed = status != self._live_last
        if not (changed and (force or since >= LIVE_EVERY_S)) and since < LIVE_HEARTBEAT_S:
            return False
        data = self._call("PUT", "/v1/live", status)
        self._live_sent, self._live_last = now, status
        self.watchers = int((data or {}).get("watchers", 0) or 0)
        return True

    def frame(self, *, own: dict | None = None, traffic: list | None = None) -> None:
        """Position and traffic for a phone watching through the server. Held in memory there, never stored."""
        data = self._call("PUT", "/v1/live/frame", {k: v for k, v in (("own", own), ("traffic", traffic)) if v is not None})
        self.watchers = int((data or {}).get("watchers", 0) or 0)

    def radio(self, lines: list[dict]) -> None:
        """Radio log lines for a phone watching through the server. Held in memory there, never stored."""
        data = self._call("POST", "/v1/live/radio", {"lines": lines})
        self.watchers = int((data or {}).get("watchers", 0) or 0)

    def airports(self, airports: list[dict]) -> None:
        """The flight's airports (frequencies, runways, ATIS) for a phone watching through the server."""
        data = self._call("PUT", "/v1/live/airports", {"airports": airports})
        self.watchers = int((data or {}).get("watchers", 0) or 0)

    def support(self, message: dict) -> str:
        """Feedback or a support request, emailed to LocalTC's maintainer. The answer goes to the account's email."""
        if not self.signed_in:
            raise AccountError("Sign in to your LocalTC account first (Account, above).", 401)
        data = self._call("POST", "/v1/support", message)
        return (data or {}).get("message", "Sent. Thanks!")

    def alert(self, alert: dict) -> None:
        """A handoff, clearance, traffic call or emergency, for the phone's banner (and later, push)."""
        self._call("POST", "/v1/live/alert", alert)

    def connect_info(self, lan: list[str], key: str) -> None:
        """Where the phone can reach this PC on the local network, and the key it needs there."""
        self._call("PUT", "/v1/live/connect", {"lan": lan, "key": key})
