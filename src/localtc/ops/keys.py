"""The keys and client ids the data services want (each a free account): in the system's credential store, as the
cloud models' keys are, never in a settings file. An environment variable wins (``LOCALTC_<NAME>``: the tests, a
developer's own).

- ``openaip``: an openAIP key (openaip.net, free): restricted, danger and prohibited areas worldwide.
- ``chartfox``: the pilot's own ChartFox token (signed in with ChartFox through the account server).
"""

import os
from dataclasses import dataclass, field

NAMES = ("openaip", "chartfox", "chartfox_refresh")
SERVICES = {
    "openaip": ("openAIP", "https://www.openaip.net/", ("openaip",), "Restricted, danger and prohibited areas"),
}


@dataclass
class DataKeys:
    """``tokens``: the credential store (``localtc.account.TokenStore``); None: the environment only."""

    tokens: object | None = None
    memory: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str:
        env = os.environ.get(f"LOCALTC_{name.upper()}")
        if env is not None:
            return env.strip()
        if name in self.memory:
            return self.memory[name]
        if self.tokens is not None:
            return self.tokens.get(f"data:{name}") or ""
        return ""

    def set(self, name: str, value: str) -> None:
        if name not in NAMES:
            raise ValueError(f"unknown key {name!r}")
        value = value.strip()
        if self.tokens is None:
            self.memory[name] = value
        elif value:
            self.tokens.set(f"data:{name}", value)
        else:
            self.tokens.delete(f"data:{name}")

    def has(self, service: str) -> bool:
        return all(self.get(n) for n in SERVICES[service][2])

    def status(self) -> list[dict]:
        """Each service: whether its keys are set (never the keys themselves)."""
        return [{"id": sid, "name": name, "signup": url, "fields": list(fields), "has_key": self.has(sid), "what": what}
                for sid, (name, url, fields, what) in SERVICES.items()]


def default_keys() -> DataKeys:
    from localtc.account import TokenStore

    return DataKeys(TokenStore())
