"""Strategy groups per game account.

From 1.1.1 every game account keeps its own strategy file,
``config/accounts/<userId>/raid-boss-strategies.user.json`` (with the usual
rotating backups). The account is the one logged in to the chosen game client;
without a client the last account seen is used.

The shared file of earlier versions (``config/raid-boss-strategies.user.json``)
is no longer written: an account seen for the first time starts with a copy of
it, so every account keeps the strategy groups it had. Other per-strategy data
(the team saved with a strategy group) lives under the account as well.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
import time
from typing import Any

from boss_modes import PROJECT_ROOT, STRATEGY_STORE, load_strategy_store
from strategy_storage import atomic_write_json, write_strategy_store

ACCOUNTS_ROOT = PROJECT_ROOT / "config" / "accounts"
ACCOUNT_INDEX = PROJECT_ROOT / "config" / "raid-boss-accounts.json"
STORE_NAME = "raid-boss-strategies.user.json"


@dataclass(frozen=True)
class StrategyAccount:
    key: str | None
    name: str | None
    path: Path

    def public(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name}


def account_key(account: Any) -> str | None:
    user_id = account.get("userId") if isinstance(account, dict) else None
    if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0:
        return str(user_id)
    return None


class AccountStores:
    def __init__(self, shared: Path = STRATEGY_STORE, root: Path = ACCOUNTS_ROOT, index: Path = ACCOUNT_INDEX):
        self.shared = shared
        self.root = root
        self.index = index
        self.lock = threading.RLock()

    def path(self, key: str) -> Path:
        return self.root / key / STORE_NAME

    def _read_index(self) -> dict[str, Any]:
        try:
            value = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema": 1, "last": None, "accounts": {}}
        if not isinstance(value, dict) or not isinstance(value.get("accounts"), dict):
            return {"schema": 1, "last": None, "accounts": {}}
        return value

    def known(self) -> dict[str, Any]:
        return self._read_index()

    def resolve(self, account: Any) -> StrategyAccount:
        """The strategy file of this account (created from the shared one), else the last account's."""
        key = account_key(account)
        with self.lock:
            index = self._read_index()
            if key is None:
                last = index.get("last")
                if isinstance(last, str) and self.path(last).is_file():
                    return StrategyAccount(last, (index["accounts"].get(last) or {}).get("name"), self.path(last))
                return StrategyAccount(None, None, self.shared)
            name = account.get("accountName") if isinstance(account.get("accountName"), str) else None
            path = self.path(key)
            if not path.is_file():
                self._seed(path)
            entry = index["accounts"].get(key) or {}
            if index.get("last") != key or entry.get("name") != name:
                index["accounts"][key] = {**entry, "name": name, "seenAt": time.strftime("%Y-%m-%d %H:%M:%S")}
                index["last"] = key
                try:
                    atomic_write_json(self.index, index)
                except OSError:
                    pass
            return StrategyAccount(key, name, path)

    def _seed(self, path: Path) -> None:
        """A new account starts with the strategy groups of the shared file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        write_strategy_store(path, load_strategy_store(self.shared))


__all__ = ["AccountStores", "StrategyAccount", "account_key", "ACCOUNTS_ROOT", "ACCOUNT_INDEX", "STORE_NAME"]
