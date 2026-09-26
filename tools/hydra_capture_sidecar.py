"""Bind normal battle observations to a read-only on-disk setup-cache probe.

This module observes only records already published by the standard agent.
It never calls the game, opens its process memory, or drives a battle.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import time
from typing import Any

from extract_hydra_playerprefs import capture


def _six_ids(value: object, *, unique: bool = False) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 6:
        return None
    if any(type(item) is not int or item <= 0 for item in value):
        return None
    return value if not unique or len(set(value)) == 6 else None


class BattleCacheCollector:
    """Save at most one independently identified natural BattleSetup cache."""

    def __init__(self, output: Path, *, interval_seconds: float = 5.0):
        self.output = output
        self.interval_seconds = interval_seconds
        self.lifecycle: dict[str, Any] | None = None
        self.decision: dict[str, Any] | None = None
        self.last_attempt = float("-inf")
        self.attempts = 0
        self.saved = False
        self.status: dict[str, Any] = {"status": "waiting_for_battle_identity",
                                       "attempts": 0, "captured": False}

    def observe(self, channel: str, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if channel == "lifecycle" and payload.get("screen") == "battle":
            battle = payload.get("battle")
            if isinstance(battle, dict) and battle.get("bossMode") == "hydra":
                self.lifecycle = payload
        elif channel == "decision" and payload.get("type") == "decision_state":
            battle = payload.get("battle")
            if isinstance(battle, dict) and battle.get("hydraBattle") is True:
                self.decision = payload

    def _identity(self) -> dict[str, Any] | None:
        lifecycle, decision = self.lifecycle, self.decision
        if lifecycle is None or decision is None:
            return None
        from observe_hydra import published_rng

        rng = published_rng(decision)
        battle = lifecycle.get("battle")
        pointers = decision.get("pointers")
        if not isinstance(rng, dict) or not isinstance(battle, dict) or not isinstance(pointers, dict):
            return None
        if (rng.get("battleSetupIdAvailable") is not True
                or not isinstance(rng.get("battleSetupId"), str)
                or len(rng["battleSetupId"]) != 32
                or rng.get("seedAvailable") is not True
                or type(rng.get("seed")) is not int):
            return None
        generation = decision.get("battleGeneration")
        context = battle.get("context")
        if (type(generation) is not int or generation <= 0
                or generation != lifecycle.get("battleGeneration")
                or type(context) is not int or context <= 0
                or context != pointers.get("context")):
            return None
        instances = _six_ids(battle.get("heroIds"), unique=True)
        types = _six_ids(battle.get("heroTypeIds"))
        stage = battle.get("stageId")
        if instances is None or types is None or type(stage) is not int or stage <= 0:
            return None
        return {"battle_setup_id": rng["battleSetupId"], "seed": rng["seed"],
                "hero_instance_ids": instances, "hero_type_ids": types,
                "stage_id": stage, "battle_generation": generation}

    def maybe_capture(self, now: float | None = None) -> dict[str, Any]:
        if self.saved:
            return self.status
        identity = self._identity()
        if identity is None:
            return self.status
        now = time.monotonic() if now is None else now
        if now - self.last_attempt < self.interval_seconds:
            return self.status
        self.last_attempt = now
        self.attempts += 1
        args = argparse.Namespace(
            registry_subkey=None, output=self.output / "natural-battle-cache",
            battle_setup_id=identity["battle_setup_id"], seed=identity["seed"],
            hero_instance_ids=identity["hero_instance_ids"],
            hero_type_ids=identity["hero_type_ids"], stage_id=identity["stage_id"],
        )
        try:
            result = capture(args)
        except (OSError, ValueError, RuntimeError) as error:
            result = {"status": "probe_error", "reason": f"{type(error).__name__}: {error}"}
        self.saved = result.get("status") == "verified_battle_setup_saved"
        self.status = {"status": result.get("status", "probe_error"),
                       "reason": result.get("reason"), "attempts": self.attempts,
                       "captured": self.saved,
                       "battleGeneration": identity["battle_generation"],
                       "registryKeysScanned": result.get("registryKeysScanned"),
                       "matchingCacheValueNames": result.get("matchingCacheValueNames")}
        if self.saved:
            self.status["outputDirectory"] = result.get("outputDirectory")
        return self.status
