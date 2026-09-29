"""Explicit offline suite. Live game scripts are never auto-discovered."""
import os
import subprocess
import sys
from pathlib import Path

MODULES = (
    "test_boss_modes", "test_boss_skills", "test_chimera_strategy_tree", "test_chimera_strategy_fuzz",
    "test_chimera_lifecycle_flow", "test_strategy_transfer", "test_effect_catalog",
    "test_inject_protocol", "test_controller_pause", "test_studio_regressions", "test_decision_diagnostics",
    "test_agent_version_safety",
    "test_release_publish",
    "test_effect_count_conditions",
    "test_result_confirmation",
    "test_lifecycle_observability",
    "test_skill_catalog_positions",
    "test_desktop_lifecycle",
    "test_regroup_transition",
    "test_trial_skill_reservation",
    "test_retired_flow",
    "test_data_root",
    "test_simulation_common",
    "test_hydra_simulation",
    "test_hydra_forecast",
    "test_chimera_capture",
    "test_chimera_simulation",
    "test_chimera_forecast_live",
    "test_team_preview",
    "test_team_setups",
    "test_account_stores",
    "test_forecast_advice",
    "test_lydia_trial_rules",
    "test_decision_observability_rng",
    "test_hydra_replay_source",
    "test_convert_hydra_replay_source",
    "test_extract_hydra_playerprefs",
    "test_hydra_offline_policy",
    "test_verify_hydra_policy_replay",
)

def main() -> int:
    directory = Path(__file__).resolve().parent
    failed = []
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    for name in MODULES:
        # Plain test functions, or unittest cases when the module has them.
        code = (
            "import importlib, inspect, sys, unittest; "
            f"m=importlib.import_module({name!r}); "
            "tests=[fn for key,fn in vars(m).items() if key.startswith('test_') and inspect.isfunction(fn) and fn.__module__ == m.__name__]; "
            "suite=unittest.defaultTestLoader.loadTestsFromModule(m); "
            "result=None if tests or not suite.countTestCases() else unittest.TextTestRunner(stream=sys.stdout, verbosity=0).run(suite); "
            "[fn() for fn in tests] if tests else (None if result else m.main()); "
            "sys.exit(1) if result and not result.wasSuccessful() else None; "
            "print('discovered-tests=' + str(len(tests) or suite.countTestCases()) if tests or result else 'script-tests=1')"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=directory,
                                capture_output=True, text=True, encoding="utf-8", env=environment, timeout=120)
        if result.returncode:
            failed.append(name)
            print(f"FAIL {name}\n{result.stdout[-4000:]}\n{result.stderr[-5000:]}", flush=True)
        else:
            print(f"PASS {name}: {result.stdout.strip().splitlines()[-1]}", flush=True)
    return int(bool(failed))

if __name__ == "__main__":
    raise SystemExit(main())
