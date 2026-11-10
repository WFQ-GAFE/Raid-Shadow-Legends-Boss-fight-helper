"""Trial conditions at the moments a trial could progress (trial_diagnosis.py), on run_timeline-shaped rows."""
from __future__ import annotations

import chimera_controller as controller
from trial_diagnosis import diagnose_run, merge, trial_form

RECIPES = controller.load_trial_recipes()
PLAYERS = {1, 2, 3}


def row(boss_turns, form, source, actor, *, damage=0, trials=(), state=None, uses=(), health=()):
    return {"bossTurns": boss_turns, "form": form, "source": source, "actorId": actor, "targetId": 9, "damage": damage,
            "trials": list(trials), "uses": list(uses), "health": list(health), **({"state": state} if state else {})}


def state(boss_fx=(), hero_fx=None, dead=()):
    heroes = [{"id": hero, "t": 7000 + hero, "s": "a", "fx": [[effect, 2, 1] for effect in (hero_fx or {}).get(hero, [])],
               **({"d": 1} if hero in dead else {})} for hero in sorted(PLAYERS)]
    return {"actors": [*heroes, {"id": 9, "t": 26906, "s": "e", "fx": [[effect, 2, 1] for effect in boss_fx]}]}


def test_recipe_sources_name_known_effects_special_tags_and_damage_bases() -> None:
    """Trials naming no effect list "sources" for the trial champions window (any of them helps)."""
    import hero_data
    from chimera_icons import EFFECT_OPTIONS
    effects = {int(option["token"]) for option in EFFECT_OPTIONS if str(option["token"]).isdigit()}
    specials = set(hero_data.SPECIAL_KINDS.values())
    scalings = set(hero_data.SCALING.values())
    sourced = {trial: recipe["sources"] for trial, recipe in RECIPES.items() if recipe.get("sources")}
    # Magma Shield counts for the shield trial; damage from enemy max HP, Poison, HP Burn and Smite for Ram's wing.
    assert {selector["effectTypeId"] for selector in RECIPES[8000511]["requirements"][0]["anyOf"]} == {280, 880}
    assert set(sourced) == {8000502, 8000515, 8000518, 8000524}
    for trial, sources in sourced.items():
        assert not RECIPES[trial]["requirements"], trial  # sources never replace the controller's requirements
        for source in sources:
            assert len(source) == 1, (trial, source)
            ids = source.get("buff", []) + source.get("debuff", [])
            assert all(effect in effects for effect in ids), (trial, source)
            assert source.get("special", "Revive") in specials and source.get("scaling", "HP") in scalings, (trial, source)


def test_trial_ids_name_their_form() -> None:
    assert [trial_form(8000610), trial_form(8000501), trial_form(8000621), trial_form(8000600)] == [2, 1, 3, None]


def test_damage_trial_checks_the_chimera_at_each_damaging_action_while_it_runs() -> None:
    # Lion Tail Normal (8000614): Decrease SPD and Decrease DEF on the Chimera when a skill hits it.
    start = {"trialId": 8000614, "before": 0, "after": 0, "start": True, "started": True, "completed": False}
    gain = {"trialId": 8000614, "before": 0, "after": 0.5, "started": True, "completed": False}
    rows = [
        row(14, 2, "policy", 1, damage=100, state=state([150, 170])),  # before it starts: not a moment
        row(15, 0, "enemy", 9, trials=[start]),
        row(16, 2, "policy", 1, damage=100, state=state([150])),
        row(16, 2, "policy", 2, damage=0, state=state([150, 170])),  # no damage: not a moment
        row(16, 2, "policy", 2, damage=100, state=state([151, 171]), trials=[gain]),
        row(21, 0, "policy", 3, damage=100, state=state([150, 170])),  # another form
    ]
    [entry] = diagnose_run(rows, [8000614], RECIPES, PLAYERS)
    assert (entry["started"], entry["startBossTurn"], entry["moments"], entry["allMet"], entry["progressed"]) == (True, 15, 2, 1, 1)
    assert [(check["kind"], check["on"], check["effectTypeIds"], check["met"]) for check in entry["conditions"]] == [
        ("effect", "boss", [170, 171], 1), ("effect", "boss", [150, 151], 2)]


def test_blocked_damage_counts_each_hero_the_chimera_hit_and_whether_it_could_kill() -> None:
    # Lion Wing Hard (8000612): Block Damage or Unkillable on the hero hit, and only a hit that could kill is blocked.
    start = {"trialId": 8000612, "before": 0, "after": 0, "start": True, "started": True, "completed": False}
    hit = {"actorId": 9, "skillTypeId": 3266902, "targetId": 1, "trigger": "input", "damage": 0,
           "hits": [1, 2], "hitDamage": [30000, 5000]}
    rows = [
        row(19, 2, "enemy", 9, trials=[start]),
        row(20, 2, "enemy", 9, uses=[hit], state=state(hero_fx={1: [320], 2: []}),
            health=[{"actorId": 1, "before": 20000, "after": 1, "max": 50000}, {"actorId": 2, "before": 40000, "after": 35000, "max": 40000}]),
        # The Chimera's skill set off during the player's action hits too.
        row(20, 2, "policy", 3, uses=[{**hit, "trigger": "activate", "hits": [3], "hitDamage": [100]}], state=state(hero_fx={3: [60]})),
    ]
    [entry] = diagnose_run(rows, [8000612], RECIPES, PLAYERS)
    assert (entry["moments"], entry["allMet"]) == (3, 1)
    assert [(check["kind"], check["on"], check["met"]) for check in entry["conditions"]] == [("effect", "target", 2), ("lethal", "target", 1)]


def test_survive_checks_each_boss_turn_and_manual_trials_are_not_checked() -> None:
    start = {"trialId": 8000623, "before": 0, "after": 0, "start": True, "started": True, "completed": False}
    rows = [row(25, 0, "enemy", 9, trials=[start]),
            row(26, 3, "enemy", 9, state=state()), row(26, 3, "policy", 1, state=state()),
            row(27, 3, "enemy", 9, state=state(dead=[2]))]
    survive, manual = diagnose_run(rows, [8000623, 8000515], RECIPES, PLAYERS)
    assert (survive["moments"], survive["conditions"][0]["kind"], survive["conditions"][0]["count"]) == (2, "living", 5)
    assert survive["conditions"][0]["met"] == 0  # three heroes here, five needed
    assert (manual["checked"], manual["moments"]) == (False, 0)


def test_runs_merge_per_trial() -> None:
    one = [{"trialId": 8000612, "goal": "absorbDamage", "checked": True, "started": True, "moments": 3, "unknown": 0, "allMet": 1,
            "progressed": 1, "conditions": [{"key": "0:effect:target", "kind": "effect", "on": "target", "met": 2}]}]
    two = [{**one[0], "started": False, "moments": 0, "allMet": 0, "progressed": 0,
            "conditions": [{**one[0]["conditions"][0], "met": 0}]}]
    [entry] = merge([one, two])
    assert (entry["runs"], entry["startedRuns"], entry["moments"], entry["allMet"], entry["conditions"][0]["met"]) == (2, 1, 3, 1, 2)
