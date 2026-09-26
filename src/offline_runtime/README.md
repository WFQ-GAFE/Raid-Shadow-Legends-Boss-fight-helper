# Isolated original-engine runner (Hydra battle-start forecast)

`raid_offline_probe.exe` runs a copy of the game's own `GameAssembly.dll` to
simulate one captured Hydra battle. It never logs in and never receives a live
game PID or handle. The desktop app ships only this executable; the game files
(`GameAssembly.dll`, `baselib.dll`, `il2cpp_data/Metadata/global-metadata.dat`)
and the newest local static-data cache (`static-data.msgpack`) are copied from
the local installation into a content-addressed folder beside it at run time
(`tools/hydra_forecast_live.py`). Never add game binaries to version control
or a release package.

## Isolation

The launcher creates a temporary AppContainer profile with no capabilities,
grants it read/execute access to the executable's folder and the input folder
only, and starts a hidden worker in a job with one active process and a 2 GB
memory limit (30 s for `json-convert`, 300 s for `forecast`). The worker
verifies its AppContainer token, zero capabilities and denied access to the
launcher's memory before loading any game library. ACLs and the profile are
restored and removed after the worker exits. The program is built for the
Windows GUI subsystem so no caller can make it open a console window; run by
hand from a terminal it attaches to that terminal for its output.

## Modes

- `json-convert <input-directory>` reads `battle-setup.json` and
  `battle-settings.json` (the agent's capture of this battle) and converts them
  with the original serializer to `BattleSetup`/`BattleSettings` MessagePack,
  returned base64-encoded in the report (`tools/convert_hydra_replay_source.py`).
- `forecast <input-directory> [max-game-turn]` loads `battle-setup.msgpack` and
  `battle-settings.msgpack`, runs the original `BattleProcessor` until the battle
  finishes or the game turn limit (1–1000, default 1000), and returns the
  HungerCounter mark events, the final Hydra damage and a per-command trace.

In `forecast`, the launcher duplicates its own stdin/stdout as the only
inheritable handles and lends them to the worker
(`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`); no named pipe, file or process is
shared. At each player window the worker writes one line
`{"type":"decision_request","sequence":N,"state":{...}}`, where `state` is a
controller-format `decision_state` built by `policy_state.hpp`, and reads one
reply line: `command<TAB>N<TAB>actorId<TAB>skillTypeId<TAB>targetId` or
`stop<TAB>N<TAB>reason`. Player commands are built with the original
`SkillCommand.FromInput` and `SetOnCooldown=true`, the constant the client's
`ClientCommandGenerator` passes; enemy turns use the original
`EnemyTurnActionGenerator`. There is no default-skill fallback: a stop or an
illegal reply ends the run. The launcher prints one wrapper line with the exit
code, timing, the worker's report and any native fault after the worker exits.

## Files

- `probe.cpp` — launcher, AppContainer worker and the two modes.
- `forecast_battle.hpp` — the policy-driven battle loop and report.
- `policy_state.hpp` — controller-format decision state and Hydra damage.
- `policy_channel.hpp` — the line protocol over the inherited pipe ends.
- `battle_commands.hpp`, `enemy_ai_command.hpp` — original command construction.
- `json_to_pack_probe.hpp`, `static_data_probe.hpp` — original serializer and static data.
- `managed_runtime.hpp` — IL2CPP calls with per-command GC-root release.
- `crash_observation.hpp` — fault report of this isolated process only.

Build with CMake and MSVC x64 into `build/offline-runtime`. Validation against
the saved gafee battle and the live damage counter is described in
`docs/1.0.6-hydra-devour-forecast.md`.
