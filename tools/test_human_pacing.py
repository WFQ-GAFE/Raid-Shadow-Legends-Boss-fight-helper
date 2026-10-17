"""Human-like pacing of automatic retries and skill commands (1.1.4); offline, no game operations."""
import copy
import random
from pathlib import Path
from unittest.mock import patch

import chimera_controller as controller
from test_chimera_lifecycle_flow import AGENT, HERO_IDS, SESSION, FakeIpc, FakeResultIpc, active_lifecycle


def fake_clock():
    clock = [0.0]
    return clock, (patch.object(controller.time, "monotonic", side_effect=lambda: clock[0]),
                   patch.object(controller.time, "sleep", side_effect=lambda value: clock.__setitem__(0, clock[0] + value)))


def test_retry_pauses_are_random_within_range_with_a_rest_every_15_to_30_retries() -> None:
    pacing = controller.HumanPacing(random.Random(7))
    pauses = [pacing.retry() for _ in range(150)]
    # Within 1 s each; the occasional rest within 5 s.
    assert all(0.3 <= pause.before_regroup <= 1.0 for pause in pauses)
    assert all(0.3 <= pause.on_team_screen <= 1.0 for pause in pauses if not pause.rest)
    assert all(2.0 <= pause.on_team_screen <= 5.0 for pause in pauses if pause.rest)
    assert 0.3 <= pacing.before_regroup() <= 1.0
    rests = [index for index, pause in enumerate(pauses) if pause.rest]
    gaps = [after - before for before, after in zip([-1, *rests], rests)]
    assert len(rests) >= 5 and all(15 <= gap <= 30 for gap in gaps), gaps
    # Never the same wait twice.
    assert len({round(pause.on_team_screen, 6) for pause in pauses}) == len(pauses)
    assert len({round(pause.before_regroup, 6) for pause in pauses}) == len(pauses)


def test_skill_command_pauses_are_short_and_varied() -> None:
    pacing = controller.HumanPacing(random.Random(11))
    values = [pacing.action() for _ in range(4000)]
    assert all(0.05 <= value <= 0.3 for value in values)
    # The occasional extra (8%) passes 0.25 s only when the base was long: about 1-2% of commands.
    longer = sum(value > 0.25 for value in values)
    assert 0.003 * len(values) < longer < 0.04 * len(values)
    assert 0.14 < sum(values) / len(values) < 0.17


class FakeDecisionIpc(FakeIpc):
    """A battle waiting for a command; `sequences` are the snapshot ids read in turn."""

    def __init__(self, sequences: list[int]) -> None:
        super().__init__(active_lifecycle("battle", {"battle": {"context": 1001}}))
        self.sequences = list(sequences)

    def decision(self) -> dict:
        return {"sequence": self.sequences.pop(0) if len(self.sequences) > 1 else self.sequences[0]}


def test_the_command_waits_its_pause_and_a_newer_snapshot_cancels_it() -> None:
    clock, (monotonic, sleep) = fake_clock()
    with monotonic, sleep:
        assert controller.pace_before_command(FakeDecisionIpc([5]), SESSION, 5, 0.6) is True
        assert abs(clock[0] - 0.6) < 1e-9
        clock[0] = 0.0
        # The game moved on during the wait: no command now, the loop decides again.
        assert controller.pace_before_command(FakeDecisionIpc([5, 5, 6]), SESSION, 5, 0.6) is False
        assert clock[0] < 0.6


def test_in_battle_regroup_pauses_before_the_regroup_and_on_the_team_screen() -> None:
    ipc = FakeIpc(active_lifecycle("battle", {"battle": {"context": 1001, "heroIds": HERO_IDS}}))
    clock, (monotonic, sleep) = fake_clock()
    events: list[tuple[str, float]] = []

    def submit(*args, **kwargs):
        events.append(("regroup", clock[0]))
        ipc.current_lifecycle = active_lifecycle("team_selection", {"selection": {"context": 2001}})
        return {"status": "submitted"}

    refreshed = active_lifecycle("team_selection", {"selection": {"valid": True, "filled": True, "areaTypeId": 13,
                                                                  "quickBattle": False, "heroIds": HERO_IDS}})
    new_battle = active_lifecycle("battle", {"battle": {"context": 3001, "heroIds": HERO_IDS}})
    with monotonic, sleep, patch("builtins.print"), patch.object(controller, "emit_telemetry"), \
            patch.object(controller, "submit_free_regroup", side_effect=submit), \
            patch.object(controller, "wait_for_lifecycle_screen", side_effect=lambda *a, **k: ipc.current_lifecycle if not events[1:] else new_battle), \
            patch.object(controller, "refresh_team_selection", return_value=refreshed), \
            patch.object(controller, "start_first_battle_if_ready",
                         side_effect=lambda *a, **k: events.append(("start", clock[0])) or True):
        controller.free_regroup_and_retry_manual(ipc, pid=1, agent=AGENT, session_id=SESSION, nonce=12,
                                                 desired_hero_ids=HERO_IDS, pause=controller.RetryPause(0.6, 0.9, False))
    assert events[0][0] == "regroup" and abs(events[0][1] - 0.6) < 1e-6
    assert events[1][0] == "start" and abs(events[1][1] - 1.5) < 1e-6


def test_result_restart_waits_on_the_result_and_then_on_the_team_screen() -> None:
    ipc = FakeResultIpc(35521231, "chimera")
    result = copy.deepcopy(ipc.current_lifecycle)
    selection = active_lifecycle("team_selection", {"selection": {"context": 9002, "valid": True, "filled": True,
        "bossMode": "chimera", "stageId": 1, "autoBattle": False, "quickBattle": False,
        "heroIds": [1, 2, 3, 4, 5], "heroTypeIds": [11, 12, 13, 14, 15]}})
    clock, (monotonic, sleep) = fake_clock()
    queued_at: list[float] = []
    started_at: list[float] = []
    telemetry: list[dict] = []
    ipc.lifecycle = lambda: selection if queued_at else result
    with monotonic, sleep, patch("builtins.print"), \
            patch.object(controller, "queue_lifecycle_command", side_effect=lambda *a, **k: queued_at.append(clock[0]) or {"queued": True}), \
            patch.object(controller, "wait_for_command_ack", return_value={"status": "submitted"}), \
            patch.object(controller, "start_first_battle_if_ready", side_effect=lambda *a, **k: started_at.append(clock[0]) or True), \
            patch.object(controller, "emit_telemetry", side_effect=lambda **row: telemetry.append(row.get("lifecycle") or {})):
        assert controller.restart_chimera_from_result(ipc, pid=1, agent=Path("offline"), session_id=SESSION, nonce=1,
            desired_hero_ids=None, desired_hero_type_ids=None, pause=controller.RetryPause(0.5, 3.0, True)) is True
    assert abs(queued_at[0] - 0.5) < 1e-6
    # The selection must stay the same for 2 s, then the 3 s rest, then Start.
    assert 5.4 <= started_at[0] <= 5.8, started_at
    pacing = [row for row in telemetry if row.get("event") == "retry_pacing"]
    assert pacing and pacing[0]["onTeamScreen"] == 3.0 and pacing[0]["rest"] is True
