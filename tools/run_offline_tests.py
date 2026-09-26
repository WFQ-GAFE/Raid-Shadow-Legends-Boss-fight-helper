"""Explicit offline suite. Live game scripts are never auto-discovered."""
import os
import subprocess
import sys
from pathlib import Path

MODULES = (
    "test_boss_modes", "test_chimera_strategy_tree", "test_chimera_strategy_fuzz",
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
    "test_strategy_flow",
    "test_hydra_forecast",
    "test_chimera_capture",
    "test_chimera_simulation",
    "test_chimera_forecast_live",
    "test_team_preview",
)

def main() -> int:
    directory = Path(__file__).resolve().parent
    failed = []
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    for name in MODULES:
        code = (
            "import importlib, inspect; "
            f"m=importlib.import_module({name!r}); "
            "tests=[fn for key,fn in vars(m).items() if key.startswith('test_') and inspect.isfunction(fn) and fn.__module__ == m.__name__]; "
            "[fn() for fn in tests] if tests else m.main(); "
            "print('discovered-tests=' + str(len(tests)) if tests else 'script-tests=1')"
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
