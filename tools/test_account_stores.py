"""Strategy groups per game account (account_stores) and the daily strategy history."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from account_stores import AccountStores, account_key
from boss_modes import default_store, load_strategy_store, update_mode_strategy, strategy_for_mode
from strategy_storage import write_strategy_store


def shared_store(path: Path, name: str) -> None:
    store = default_store()
    strategy = {**strategy_for_mode(store, "chimera"), "name": name}
    write_strategy_store(path, update_mode_strategy(store, "chimera", strategy, strategy_id="strategy-1"))


def test_accounts_start_from_the_shared_file_and_stay_apart() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        shared = root / "config" / "raid-boss-strategies.user.json"
        shared.parent.mkdir(parents=True)
        shared_store(shared, "共享")
        stores = AccountStores(shared, root / "config" / "accounts", root / "config" / "raid-boss-accounts.json")
        # No account yet: the shared file.
        assert stores.resolve(None).path == shared and stores.resolve(None).key is None
        first = stores.resolve({"accountName": "PlayerOne", "userId": 10000001})
        assert first.key == "10000001" and first.name == "PlayerOne" and first.path.is_file()
        assert strategy_for_mode(load_strategy_store(first.path), "chimera", "strategy-1")["name"] == "共享"
        # Editing one account leaves the other and the shared file alone.
        store = load_strategy_store(first.path)
        changed = {**strategy_for_mode(store, "chimera", "strategy-1"), "name": "只属于 PlayerOne"}
        write_strategy_store(first.path, update_mode_strategy(store, "chimera", changed, strategy_id="strategy-1"))
        other = stores.resolve({"accountName": "PlayerTwo", "userId": 10000002})
        assert strategy_for_mode(load_strategy_store(other.path), "chimera", "strategy-1")["name"] == "共享"
        assert strategy_for_mode(load_strategy_store(shared), "chimera", "strategy-1")["name"] == "共享"
        # Without a client: the last account seen.
        assert stores.resolve(None).key == "10000002"
        stores.resolve({"accountName": "PlayerOne", "userId": 10000001})
        last = stores.resolve(None)
        assert last.key == "10000001" and last.name == "PlayerOne"
        assert strategy_for_mode(load_strategy_store(last.path), "chimera", "strategy-1")["name"] == "只属于 PlayerOne"
        index = json.loads((root / "config" / "raid-boss-accounts.json").read_text(encoding="utf-8"))
        assert set(index["accounts"]) == {"10000001", "10000002"} and index["last"] == "10000001"
    assert account_key({"userId": 0}) is None and account_key({"userId": True}) is None and account_key(None) is None


def test_daily_history_keeps_the_first_version_of_each_day() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "raid-boss-strategies.user.json"
        shared_store(path, "第一版")
        for name in ("第二版", "第三版", "第四版", "第五版", "第六版", "第七版"):
            store = load_strategy_store(path)
            strategy = {**strategy_for_mode(store, "chimera", "strategy-1"), "name": name}
            write_strategy_store(path, update_mode_strategy(store, "chimera", strategy, strategy_id="strategy-1"))
        history = list((path.parent / "history").iterdir())
        assert len(history) == 1  # the file as the day started, however many saves follow
        kept = json.loads(history[0].read_text(encoding="utf-8"))
        assert kept["modes"]["chimera"]["strategies"]["strategy-1"]["name"] == "第一版"
