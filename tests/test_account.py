"""The optional account's client, against a fake server: sign in, sync the logbook, sign out, delete."""

import pytest

from localtc.account import LOCAL_ONLY, Account, AccountError, TokenStore
from localtc.logbook import FlightRecord, Logbook


class MemoryStore(TokenStore):
    def __init__(self) -> None:
        super().__init__()
        self._keyring = lambda: None  # never the real credential store in tests


class FakeServer:
    """Just enough of server/ to exercise the client."""

    def __init__(self) -> None:
        self.flights: dict[str, dict] = {}
        self.replays: dict[str, bytes] = {}
        self.tokens = {"good-token"}
        self.requests: list[tuple[str, str, dict | None, dict]] = []

    def __call__(self, method: str, url: str, body: dict | None, headers: dict) -> tuple[int, object]:
        path = url.split("https://api.test", 1)[1]
        self.requests.append((method, path, body, headers))
        authed = headers.get("Authorization", "").removeprefix("Bearer ") in self.tokens
        if path == "/v1/auth/start":
            return 202, {"message": "Check your email."}
        if path == "/v1/auth/finish":
            if body["code"] != "123456":
                return 401, {"error": "That code is wrong or has expired."}
            return 200, {"token": "good-token", "user": {"email": body["email"]}}
        if not authed:
            return 401, {"error": "Sign in again."}
        if path == "/v1/flights" and method == "POST":
            for f in body["flights"]:
                assert set(f) == set(FlightRecord.__dataclass_fields__) - set(LOCAL_ONLY)  # summaries, nothing else
                self.flights[f["id"]] = f
            return 200, {"accepted": [f["id"] for f in body["flights"]]}
        if path == "/v1/auth/logout":
            self.tokens.discard("good-token")
            return 200, {}
        if path == "/v1/me" and method == "DELETE":
            if body["email"] != "pilot@example.com":
                return 400, {"error": "Type the account's email address to confirm."}
            self.flights.clear()
            return 200, {}
        if path == "/v1/live":
            return 200, {"watchers": 0}
        if path.startswith("/v1/flights/") and path.endswith("/replay"):
            flight = path.split("/")[3]
            if flight not in self.flights:
                return 404, {"error": "Sync the flight's logbook line first."}
            if method == "PUT":
                assert isinstance(body, bytes)
                self.replays[flight] = body
                return 200, {"ok": True}
            if method == "DELETE" and self.replays.pop(flight, None) is not None:
                return 200, {"ok": True}
            return 404, {"error": "No replay for this flight."}
        if path == "/v1/support":
            return 200, {"message": "Sent. Thanks!"}
        return 404, {"error": "no"}


def record(i: int) -> FlightRecord:
    return FlightRecord(id=f"f{i}", started_at=f"2026-09-{10 + i:02d}T10:00:00Z", ended_at=f"2026-09-{10 + i:02d}T12:00:00Z",
                        origin="KSAN", destination="KPHX", air_min=60.0, landed=True)


@pytest.fixture
def setup(tmp_path):
    book = Logbook(tmp_path / "logbook.db")
    server = FakeServer()
    return Account("https://api.test", store=MemoryStore(), transport=server, logbook=book), server, book


def test_nothing_is_sent_until_signed_in(setup):
    account, server, book = setup
    book.add(record(1))
    assert not account.signed_in
    assert account.live({"active": True}) is False
    with pytest.raises(AccountError):
        account.sync()
    assert server.requests == []


def sign_in(account: Account) -> None:
    account.start("pilot@example.com")
    account.finish("pilot@example.com", "123456", "PC")


def test_a_wrong_code_is_said_plainly(setup):
    account, _, _ = setup
    account.start("pilot@example.com")
    with pytest.raises(AccountError, match="wrong or has expired"):
        account.finish("pilot@example.com", "000000", "PC")
    assert not account.signed_in


def test_sign_in_and_sync_uploads_each_flight_once(setup):
    account, server, book = setup
    for i in range(1, 4):
        book.add(record(i))
    sign_in(account)
    assert account.signed_in and account.email == "pilot@example.com"
    assert account.sync().uploaded == 3
    assert set(server.flights) == {"f1", "f2", "f3"}
    book.add(record(4))
    assert account.sync().uploaded == 1
    assert account.sync().uploaded == 0


def test_a_token_revoked_on_the_website_signs_out_here(setup):
    account, server, book = setup
    sign_in(account)
    server.tokens.clear()
    book.add(record(1))
    with pytest.raises(AccountError):
        account.sync()
    assert not account.signed_in


def test_signing_out_makes_every_flight_local_again(setup):
    account, _, book = setup
    book.add(record(1))
    sign_in(account)
    account.sync()
    account.logout()
    assert not account.signed_in and len(book.unsynced()) == 1


def test_deleting_the_account_keeps_the_local_logbook(setup):
    account, server, book = setup
    book.add(record(1))
    sign_in(account)
    account.sync()
    with pytest.raises(AccountError):
        account.delete_account("someone@else.com")
    account.delete_account("pilot@example.com")
    assert server.flights == {} and not account.signed_in
    assert [f.id for f in book.flights()] == ["f1"]


def test_the_companion_gets_changes_not_a_stream(setup):
    account, server, _ = setup
    sign_in(account)
    status = {"active": True, "phase": "CRUISE"}
    assert account.live(status, now=100.0)
    assert not account.live(status, now=108.0)  # nothing changed
    assert not account.live({**status, "phase": "ARRIVAL"}, now=101.0)  # too soon
    assert account.live({**status, "phase": "ARRIVAL"}, now=101.0, force=True)  # a handoff: at once
    assert account.live({**status, "phase": "ARRIVAL"}, now=116.0)  # the heartbeat: is a phone watching?
    live = [r for r in server.requests if r[1] == "/v1/live"]
    assert len(live) == 3 and all("lat" not in r[2] for r in live)


def test_support_messages_need_the_account(setup):
    account, server, _ = setup
    with pytest.raises(AccountError, match="Sign in"):
        account.support({"kind": "bug", "message": "Tower cleared me onto the wrong runway."})
    assert server.requests == []
    sign_in(account)
    assert account.support({"kind": "bug", "message": "Tower cleared me onto the wrong runway."}) == "Sent. Thanks!"
    method, path, body, headers = server.requests[-1]
    assert (method, path) == ("POST", "/v1/support") and headers["Authorization"] == "Bearer good-token"
    assert "email" not in body  # the server answers the account's own address


def test_a_replay_goes_up_only_when_asked_and_its_path_never_does(setup):
    account, server, book = setup
    book.add(record(1))
    book.mark_replay("f1", None)
    with pytest.raises(AccountError):
        account.upload_replay("f1", b"\x1f\x8b")  # signed out: nothing goes anywhere
    sign_in(account)
    account.sync()
    assert "recording" not in server.flights["f1"]  # where the recording is stays on this computer
    account.upload_replay("f1", b"\x1f\x8bgz")
    assert server.replays == {"f1": b"\x1f\x8bgz"}
    account.delete_replay("f1")
    assert server.replays == {}
