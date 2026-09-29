"""Shared Hydra state interpretation for execution and display."""
from typing import Any


def hydra_head_is_devouring(entity: dict[str, Any]) -> bool:
    devoured_id = entity.get("devouredHeroId")
    if entity.get("isDevouring") is True or (
        isinstance(devoured_id, int)
        and not isinstance(devoured_id, bool)
        and devoured_id >= 0
    ):
        return True
    # Recent clients can leave BattleHero.get_IsDigestingNow and the mirrored
    # top-level fields false even while the model still carries the Digestion
    # effect.  The effect identity is the authoritative fallback and remains
    # available even when the swallowed ally is absent from the UI actor map.
    for effect in entity.get("effects", []):
        if not isinstance(effect, dict):
            continue
        effect_devoured_id = effect.get("devouredHeroId")
        if (
            effect.get("effectKind") == "Digestion"
            or effect.get("effectKindId") == 9025
            or effect.get("effectTypeId") == 700
            or (
                effect.get("skillTypeId") == 260007
                and isinstance(effect_devoured_id, int)
                and not isinstance(effect_devoured_id, bool)
                and effect_devoured_id >= 0
            )
        ):
            return True
    return False


def hydra_swallowed_hero_ids(state: dict[str, Any]) -> set[int]:
    """Heroes inside a Hydra head right now: actually swallowed (a devour mark alone is not).

    The victim carries the Devoured effect (kind 9024); the digesting head
    names it as well (devouredHeroId).
    """
    result: set[int] = set()
    for hero in [item for item in state.get("heroes", []) if isinstance(item, dict)]:
        actor_id = hero.get("id")
        if not isinstance(actor_id, int) or isinstance(actor_id, bool):
            continue
        for effect in hero.get("effects", []):
            if isinstance(effect, dict) and (effect.get("effectKind") == "Devoured"
                                             or effect.get("effectKindId") == 9024):
                result.add(actor_id)
    for boss in [item for item in state.get("bosses", []) if isinstance(item, dict)]:
        if boss.get("dead") is True:
            continue
        devoured = boss.get("devouredHeroId")
        if isinstance(devoured, int) and not isinstance(devoured, bool) and devoured >= 0:
            result.add(devoured)
    return result


def hydra_devouring_head_ids(state: dict[str, Any]) -> set[int]:
    """Return authoritative Hydra head actor IDs that currently hold a hero.

    A newly spawned head can briefly be present in a skill's legal target IDs
    before its UI metadata is published.  The Devoured effect on the victim
    still identifies its producer (the head) and is therefore the most stable
    link for rescue targeting.
    """
    result: set[int] = set()
    for boss in [item for item in state.get("bosses", []) if isinstance(item, dict)]:
        actor_id = boss.get("id")
        if (
            isinstance(actor_id, int)
            and not isinstance(actor_id, bool)
            and actor_id >= 0
            and boss.get("dead") is not True
            and hydra_head_is_devouring(boss)
        ):
            result.add(actor_id)
    for hero in [item for item in state.get("heroes", []) if isinstance(item, dict)]:
        for effect in hero.get("effects", []):
            if not isinstance(effect, dict):
                continue
            if effect.get("effectKind") != "Devoured" and effect.get(
                "effectKindId"
            ) != 9024:
                continue
            producer_id = effect.get("producerId")
            if (
                isinstance(producer_id, int)
                and not isinstance(producer_id, bool)
                and producer_id >= 0
            ):
                result.add(producer_id)
    return result
