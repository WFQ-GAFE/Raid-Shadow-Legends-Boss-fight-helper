from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from chimera_controller import (
    AccountBinding,
    LIFECYCLE_PREPARE_FREE_REGROUP,
    LIFECYCLE_REFRESH_TEAM_SELECTION,
    LIFECYCLE_SELECT_HEROES,
    ObjectiveReport,
    TakeoverInterrupted,
    account_binding,
    free_regroup_and_retry_manual,
    lifecycle_nonce,
    process_state,
    require_account_binding,
    refresh_team_selection,
    result_screen_reached,
    select_team_heroes,
    start_first_battle_if_ready,
    submit_free_regroup,
)
from chimera_runtime import lifecycle_team_ids


SESSION = 99112233
AGENT = Path("RaidChimeraAgent.dll")
HERO_IDS = [39104, 21597, 34700, 21826, 26679]
HERO_TYPE_IDS = [8896, 4716, 10436, 9906, 8256]


class FakeIpc:
    def __init__(self, lifecycle: dict) -> None:
        self.current_lifecycle = lifecycle

    def lifecycle(self) -> dict:
        return self.current_lifecycle


class FakeAccountIpc:
    def __init__(self, account: dict) -> None:
        self.current_account = account

    def account(self) -> dict:
        return self.current_account


class FakeResultIpc(FakeIpc):
    def __init__(self, damage: int) -> None:
        super().__init__(
            active_lifecycle(
                "result",
                {"result": {"bossMode": "hydra", "context": 9001}},
            )
        )
        self.damage = damage

    def battle_ledger(self) -> dict:
        return {"bossMode": "hydra", "damage": self.damage}


def active_lifecycle(screen: str, body: dict) -> dict:
    return {
        "takeoverState": "active",
        "sessionId": SESSION,
        "screen": screen,
        **body,
    }


def test_nonce_uniqueness() -> None:
    values = {
        lifecycle_nonce(seed, offset)
        for seed in range(1, 100)
        for offset in (1, 2, 300, 400)
    }
    assert len(values) == 396
    assert all(value & 0x80000000 for value in values)


def test_team_reader_never_falls_back_from_partial_type_ids() -> None:
    complete = {
        "screen": "team_selection",
        "selection": {
            "heroIds": HERO_IDS,
            "heroTypeIds": HERO_TYPE_IDS,
        },
    }
    assert lifecycle_team_ids(complete) == HERO_TYPE_IDS
    switching = {
        "screen": "team_selection",
        "selection": {
            "heroIds": HERO_IDS[:-1] + [99999],
            "heroTypeIds": HERO_TYPE_IDS[:-1] + [0],
        },
    }
    assert lifecycle_team_ids(switching) == []
    legacy = {
        "screen": "battle",
        "battle": {"heroIds": HERO_TYPE_IDS},
    }
    assert lifecycle_team_ids(legacy) == HERO_TYPE_IDS


def test_hydra_start_retries_selection_settle() -> None:
    hydra_ids = HERO_IDS + [31001]
    ipc = FakeIpc(
        active_lifecycle(
            "team_selection",
            {
                "selection": {
                    "bossMode": "hydra",
                    "context": 2001,
                    "valid": True,
                    "filled": True,
                    "autoBattle": True,
                    "quickBattle": False,
                    "heroIds": hydra_ids,
                }
            },
        )
    )
    queued_nonces: list[int] = []
    acknowledgements = iter(
        [
            {"status": "rejected", "reason": "auto_battle_disable_pending"},
            {"status": "submitted", "reason": "battle_start_submitted"},
        ]
    )

    def queue(*args, **kwargs):
        queued_nonces.append(kwargs["nonce"])
        return {"queued": True}

    def acknowledge(*args, **kwargs):
        value = next(acknowledgements)
        if value["status"] == "submitted":
            ipc.current_lifecycle = active_lifecycle(
                "battle", {"battle": {"bossMode": "hydra", "context": 3001}}
            )
        return value

    with (
        patch("chimera_controller.queue_lifecycle_command", side_effect=queue),
        patch("chimera_controller.wait_for_command_ack", side_effect=acknowledge),
    ):
        assert start_first_battle_if_ready(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            boss_mode="hydra",
        )
    assert len(queued_nonces) == 2
    assert len(set(queued_nonces)) == 2


def test_hydra_saved_type_ids_match_different_instance_ids() -> None:
    hydra_instance_ids = HERO_IDS + [31001]
    hydra_type_ids = HERO_TYPE_IDS + [10056]
    ipc = FakeIpc(
        active_lifecycle(
            "team_selection",
            {
                "selection": {
                    "bossMode": "hydra",
                    "context": 2001,
                    "valid": True,
                    "filled": True,
                    "quickBattle": False,
                    "heroIds": hydra_instance_ids,
                    "heroTypeIds": hydra_type_ids,
                }
            },
        )
    )

    def acknowledge(*args, **kwargs):
        ipc.current_lifecycle = active_lifecycle(
            "battle", {"battle": {"bossMode": "hydra", "context": 3001}}
        )
        return {"status": "submitted", "reason": "battle_start_submitted"}

    with (
        patch(
            "chimera_controller.queue_lifecycle_command",
            return_value={"queued": True},
        ),
        patch("chimera_controller.wait_for_command_ack", side_effect=acknowledge),
    ):
        assert start_first_battle_if_ready(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            desired_hero_type_ids=hydra_type_ids,
            boss_mode="hydra",
        )


def test_partial_hydra_team_can_start() -> None:
    ipc = FakeIpc(
        active_lifecycle(
            "team_selection",
            {
                "selection": {
                    "bossMode": "hydra",
                    "context": 2001,
                    "valid": True,
                    "filled": False,
                    "autoBattle": False,
                    "quickBattle": False,
                    "heroIds": [39104],
                }
            },
        )
    )
    def acknowledge(*args, **kwargs):
        ipc.current_lifecycle = active_lifecycle(
            "battle", {"battle": {"bossMode": "hydra", "context": 3001}}
        )
        return {"status": "submitted", "reason": "battle_start_submitted"}

    with (
        patch(
            "chimera_controller.queue_lifecycle_command",
            return_value={"queued": True},
        ) as queue,
        patch("chimera_controller.wait_for_command_ack", side_effect=acknowledge),
    ):
        assert start_first_battle_if_ready(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            boss_mode="hydra",
        )
    queue.assert_called_once()


def test_account_binding_rejects_pid_only_and_account_change() -> None:
    snapshot = {
        "pid": 123,
        "accountName": "gafee",
        "userId": 95815853,
    }
    binding = account_binding(snapshot, 123)
    assert binding == AccountBinding(123, "gafee", 95815853)
    ipc = FakeAccountIpc(dict(snapshot))
    require_account_binding(ipc, binding)
    ipc.current_account["accountName"] = "another-account"
    try:
        require_account_binding(ipc, binding)
    except TakeoverInterrupted:
        pass
    else:
        raise AssertionError("account name change did not interrupt takeover")
    for invalid in (
        None,
        {"pid": 124, "accountName": "gafee", "userId": 95815853},
        {"pid": 123, "accountName": "", "userId": 95815853},
        {"pid": 123, "accountName": "gafee", "userId": True},
    ):
        try:
            account_binding(invalid, 123)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid account binding accepted: {invalid}")


def test_prepare_transition_does_not_repeat_execute() -> None:
    ipc = FakeIpc(
        active_lifecycle(
            "battle", {"battle": {"context": 1001, "heroIds": HERO_IDS}}
        )
    )
    queued_actions: list[int] = []

    def queue(*args, **kwargs):
        queued_actions.append(kwargs["action"])
        ipc.current_lifecycle = active_lifecycle(
            "team_selection",
            {"selection": {"context": 2001, "heroIds": HERO_IDS}},
        )
        return {"queued": True}

    with (
        patch("chimera_controller.queue_lifecycle_command", side_effect=queue),
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "submitted"},
        ),
    ):
        acknowledgement = submit_free_regroup(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            nonce=7,
        )
    assert acknowledgement["status"] == "submitted"
    assert queued_actions == [LIFECYCLE_PREPARE_FREE_REGROUP]


def test_refresh_selection_uses_read_only_action() -> None:
    lifecycle = active_lifecycle(
        "team_selection",
        {"selection": {"context": 2001, "heroIds": HERO_IDS}},
    )
    ipc = FakeIpc(lifecycle)
    calls: list[dict] = []

    def queue(*args, **kwargs):
        calls.append(kwargs)
        return {"queued": True}

    with (
        patch("chimera_controller.queue_lifecycle_command", side_effect=queue),
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "validated"},
        ),
    ):
        refreshed = refresh_team_selection(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            nonce=8,
        )
    assert refreshed is lifecycle
    assert calls[0]["action"] == LIFECYCLE_REFRESH_TEAM_SELECTION


def test_select_team_uses_five_bound_hero_ids() -> None:
    lifecycle = active_lifecycle(
        "team_selection",
        {
            "selection": {
                "context": 2001,
                "valid": True,
                "filled": False,
                "heroIds": [],
            }
        },
    )
    ipc = FakeIpc(lifecycle)
    calls: list[dict] = []

    def queue(*args, **kwargs):
        calls.append(kwargs)
        ipc.current_lifecycle = active_lifecycle(
            "team_selection",
            {
                "selection": {
                    "context": 2001,
                    "valid": True,
                    "filled": True,
                    "heroIds": HERO_IDS,
                }
            },
        )
        return {"queued": True}

    with (
        patch("chimera_controller.queue_lifecycle_command", side_effect=queue),
        patch(
            "chimera_controller.wait_for_command_ack",
            return_value={"status": "submitted"},
        ),
    ):
        selected = select_team_heroes(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            hero_ids=HERO_IDS,
            nonce=19,
        )
    assert selected["selection"]["heroIds"] == HERO_IDS
    assert calls[0]["action"] == LIFECYCLE_SELECT_HEROES
    assert calls[0]["hero_ids"] == HERO_IDS


def test_retry_rejects_changed_team_before_start() -> None:
    ipc = FakeIpc(
        active_lifecycle(
            "battle", {"battle": {"context": 1001, "heroIds": HERO_IDS}}
        )
    )
    changed = HERO_IDS[:-1] + [99999]

    def submit(*args, **kwargs):
        ipc.current_lifecycle = active_lifecycle(
            "team_selection", {"selection": {"context": 2001}}
        )
        return {"status": "submitted"}

    refreshed = active_lifecycle(
        "team_selection",
        {
            "selection": {
                "valid": True,
                "filled": True,
                "areaTypeId": 13,
                "quickBattle": False,
                "heroIds": changed,
            }
        },
    )
    with (
        patch("chimera_controller.submit_free_regroup", side_effect=submit),
        patch(
            "chimera_controller.wait_for_lifecycle_screen",
            return_value=ipc.current_lifecycle,
        ),
        patch("chimera_controller.refresh_team_selection", return_value=refreshed),
        patch("chimera_controller.start_first_battle_if_ready") as start,
    ):
        try:
            free_regroup_and_retry_manual(
                ipc,
                pid=1,
                agent=AGENT,
                session_id=SESSION,
                nonce=9,
            )
        except RuntimeError as error:
            assert "队伍发生变化" in str(error)
        else:
            raise AssertionError("changed team was not rejected")
    start.assert_not_called()


def test_retry_accepts_same_team_and_new_context() -> None:
    ipc = FakeIpc(
        active_lifecycle(
            "battle", {"battle": {"context": 1001, "heroIds": HERO_IDS}}
        )
    )

    def submit(*args, **kwargs):
        ipc.current_lifecycle = active_lifecycle(
            "team_selection", {"selection": {"context": 2001}}
        )
        return {"status": "submitted"}

    refreshed = active_lifecycle(
        "team_selection",
        {
            "selection": {
                "valid": True,
                "filled": True,
                "areaTypeId": 13,
                "quickBattle": False,
                "heroIds": HERO_IDS,
            }
        },
    )
    new_battle = active_lifecycle(
        "battle", {"battle": {"context": 3001, "heroIds": HERO_IDS}}
    )
    with (
        patch("chimera_controller.submit_free_regroup", side_effect=submit),
        patch(
            "chimera_controller.wait_for_lifecycle_screen",
            side_effect=[ipc.current_lifecycle, new_battle],
        ),
        patch("chimera_controller.refresh_team_selection", return_value=refreshed),
        patch("chimera_controller.start_first_battle_if_ready", return_value=True),
    ):
        free_regroup_and_retry_manual(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            nonce=10,
        )


def test_retry_uses_configured_team_after_agent_reload() -> None:
    ipc = FakeIpc(active_lifecycle("battle", {"battle": {"context": 1001}}))

    def submit(*args, **kwargs):
        ipc.current_lifecycle = active_lifecycle(
            "team_selection", {"selection": {"context": 2001}}
        )
        return {"status": "submitted"}

    refreshed = active_lifecycle(
        "team_selection",
        {
            "selection": {
                "valid": True,
                "filled": True,
                "areaTypeId": 13,
                "quickBattle": False,
                "heroIds": HERO_IDS,
            }
        },
    )
    new_battle = active_lifecycle(
        "battle", {"battle": {"context": 3001, "heroIds": HERO_IDS}}
    )
    with (
        patch("chimera_controller.submit_free_regroup", side_effect=submit),
        patch(
            "chimera_controller.wait_for_lifecycle_screen",
            side_effect=[ipc.current_lifecycle, new_battle],
        ),
        patch("chimera_controller.refresh_team_selection", return_value=refreshed),
        patch(
            "chimera_controller.start_first_battle_if_ready", return_value=True
        ) as start,
    ):
        free_regroup_and_retry_manual(
            ipc,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            nonce=12,
            desired_hero_ids=HERO_IDS,
        )
    start.assert_called_once_with(
        ipc,
        pid=1,
        agent=AGENT,
        session_id=SESSION,
        command_nonce=lifecycle_nonce(12, 400),
        desired_hero_ids=HERO_IDS,
    )


def test_retry_budget_stops_before_mutation() -> None:
    ipc = FakeIpc(active_lifecycle("battle", {"battle": {"context": 1001}}))
    config = {
        "mode": "execute",
        "objectives": {
            "onMandatoryTrialImpossible": "free_regroup_and_retry_manual",
            "maxRegroupRetries": 1,
        },
        "safety": {"requireFreshSnapshotMs": 500},
    }
    report = ObjectiveReport(
        mandatory_trial_ids=(1,),
        completed_trial_ids=(),
        missing_trial_ids=(1,),
        impossible_trial_ids=(1,),
        current_damage=0,
        minimum_damage=0,
    )
    runtime = {"regroupRetries": 1}
    with (
        patch("chimera_controller.require_takeover_active"),
        patch("chimera_controller.safety_reason", return_value=None),
        patch("chimera_controller.evaluate_objectives", return_value=report),
        patch("chimera_controller.free_regroup_and_retry_manual") as regroup,
    ):
        try:
            process_state(
                config,
                {"pid": 1},
                agent=AGENT,
                ipc=ipc,
                session_id=SESSION,
                execute_requested=True,
                nonce=11,
                runtime_state=runtime,
            )
        except RuntimeError as error:
            assert "重试上限" in str(error)
        else:
            raise AssertionError("retry budget was not enforced")
    regroup.assert_not_called()


def test_retry_success_updates_session_budget() -> None:
    ipc = FakeIpc(active_lifecycle("battle", {"battle": {"context": 1001}}))
    config = {
        "mode": "execute",
        "objectives": {
            "onMandatoryTrialImpossible": "free_regroup_and_retry_manual",
            "maxRegroupRetries": 2,
        },
        "safety": {"requireFreshSnapshotMs": 500},
    }
    report = ObjectiveReport(
        mandatory_trial_ids=(1,),
        completed_trial_ids=(),
        missing_trial_ids=(1,),
        impossible_trial_ids=(1,),
        current_damage=0,
        minimum_damage=0,
    )
    runtime = {"regroupRetries": 0}
    with (
        patch("chimera_controller.require_takeover_active"),
        patch("chimera_controller.safety_reason", return_value=None),
        patch("chimera_controller.evaluate_objectives", return_value=report),
        patch("chimera_controller.free_regroup_and_retry_manual") as regroup,
    ):
        submitted = process_state(
            config,
            {"pid": 1},
            agent=AGENT,
            ipc=ipc,
            session_id=SESSION,
            execute_requested=True,
            nonce=12,
            runtime_state=runtime,
        )
    assert submitted is False
    assert runtime["regroupRetries"] == 1
    regroup.assert_called_once()


def test_hydra_result_holds_after_damage_target() -> None:
    ipc = FakeResultIpc(7_500_000_000)
    config = {
        "mode": "execute",
        "objectives": {
            "minimumDamage": 7_000_000_000,
            "maxRegroupRetries": 100,
            "onTeamDefeatedBeforeMinimumDamage":
                "free_regroup_and_retry_manual",
        },
    }
    with patch("chimera_controller.restart_hydra_from_result") as restart:
        should_stop = result_screen_reached(
            ipc,
            config=config,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            boss_mode="hydra",
            execute_requested=True,
            runtime_state={"regroupRetries": 0},
        )
    assert should_stop is True
    restart.assert_not_called()


def test_hydra_result_retries_below_damage_target() -> None:
    ipc = FakeResultIpc(6_500_000_000)
    config = {
        "mode": "execute",
        "objectives": {
            "minimumDamage": 7_000_000_000,
            "maxRegroupRetries": 100,
            "onTeamDefeatedBeforeMinimumDamage":
                "free_regroup_and_retry_manual",
        },
    }
    runtime = {"regroupRetries": 0}
    with patch("chimera_controller.restart_hydra_from_result") as restart:
        should_stop = result_screen_reached(
            ipc,
            config=config,
            pid=1,
            agent=AGENT,
            session_id=SESSION,
            boss_mode="hydra",
            execute_requested=True,
            runtime_state=runtime,
            desired_hero_ids=HERO_IDS + [31001],
            desired_hero_type_ids=HERO_TYPE_IDS + [9996],
            nonce=18,
        )
    assert should_stop is False
    assert runtime["regroupRetries"] == 1
    restart.assert_called_once()


def main() -> int:
    test_nonce_uniqueness()
    test_team_reader_never_falls_back_from_partial_type_ids()
    test_hydra_start_retries_selection_settle()
    test_partial_hydra_team_can_start()
    test_account_binding_rejects_pid_only_and_account_change()
    test_prepare_transition_does_not_repeat_execute()
    test_refresh_selection_uses_read_only_action()
    test_select_team_uses_five_bound_hero_ids()
    test_retry_rejects_changed_team_before_start()
    test_retry_accepts_same_team_and_new_context()
    test_retry_budget_stops_before_mutation()
    test_retry_success_updates_session_budget()
    test_hydra_result_holds_after_damage_target()
    test_hydra_result_retries_below_damage_target()
    print("chimera-lifecycle-flow-tests-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
