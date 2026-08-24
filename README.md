# RAID Alliance Boss Strategy Studio

A local Windows strategy editor and battle controller for the Alliance Chimera and Hydra encounters in **RAID: Shadow Legends**.

Current release: **1.0.1**.

The application reads live battle state from the selected RAID client, evaluates user-defined rules, and submits guarded in-game actions. It includes an English and Chinese interface, separate Chimera and Hydra workspaces, multiple saved strategy profiles, live team discovery, hero and skill catalogs, target priorities, and independent run logs.

## Current capabilities

### Shared features

- Detects running RAID accounts and binds a controller session to both the player name and player ID.
- Reads the currently selected preparation team and refreshes it when heroes are changed.
- Builds local hero, skill, effect, trial, and visual-asset catalogs from RAID data.
- Supports multiple named strategy profiles for different teams.
- Exports and imports portable strategy files without account-specific champion instance IDs.
- Provides English as the default language, with Chinese available from the language selector.
- Shows hero portraits, skill icons, buff/debuff icons, and encounter-specific boss icons.
- Keeps Chimera and Hydra strategies, live state, and run logs separate.
- Supports strict ordered rules, fallback skill orders, skill cooldown conditions, effect conditions, remaining-turn checks, and preferred targets.
- Revalidates the account, acting hero, skill readiness, legal targets, battle mode, and current turn on the game thread before executing an action.
- Allows normal manual interaction. A stale action is rejected safely when manual input or turn progression changes the battle state.

### Chimera

- Tracks forms, form transitions, trials, trial chains, damage, effects, cooldowns, and legal targets.
- Supports trial-aware automatic decisions and structured recipes for known trial categories.
- Handles Mythical champion transformations and keeps the two forms' skill catalogs separate.
- Can perform a guarded free regroup when a required trial becomes impossible, then verify the same five champions before re-entering battle.
- At the result screen, retries automatically when either the damage target or any required trial is still incomplete; it holds only after both are satisfied.

### Hydra

- Identifies Hydra heads by head type rather than by screen position.
- Supports conditions and target priorities for all heads, any head, or preferred head types.
- Uses loose head matching so respawns and rotation changes do not invalidate a strategy unnecessarily.
- Tracks total encounter damage without accumulating obsolete per-head health entries after respawns.
- Can stop at the result screen when the damage target is met or safely restart with the verified six-champion team when it is not.
- Prioritizes dead allies for revive skills and falls back to automatic legal-target selection when no preferred target is available.

## Architecture and dependencies

The runtime consists of this project's Python controller, React interface, and native IL2CPP agent. The native agent uses the bundled MinHook source and Windows system APIs. Python UI dependencies are vendored under `third_party/python`, and the web interface is built from the packages declared in `ui/package.json`.

The application is Windows-specific because the guarded state agent and process APIs are Windows x64 components. It does not assume a drive letter: supported Plarium installations are discovered from the running `Raid.exe` process. A packaged release is a single executable containing Python, the controller workers, the native agent, encounter data, and the built React interface. User strategies, preferences, and generated visual caches are kept under `%LOCALAPPDATA%\WFQ-GAFE\RaidBossStrategyStudio`, so replacing the executable does not erase them.

## Requirements

- Windows 10 or Windows 11, x64
- RAID: Shadow Legends installed through Plarium Play
- Python available as `python.exe` when running from source
- Administrator permission when requested, because the local state agent must be loaded into the selected RAID process
- Microsoft Edge WebView2 Runtime (already present on normal Windows 10/11 installations with Microsoft Edge)

Building the native agent additionally requires Visual Studio 2022 with MSVC x64, CMake, and a compatible Windows SDK. Building the React interface requires Node.js and npm.

## Run from source

Build the web interface once if `ui/dist` is not already present:

```powershell
cd ui
npm install
npm run build
cd ..
```

Then double-click:

```text
run-raid-boss-tool.cmd
```

The launcher requests administrator permission when required and starts the local interface. Select the RAID account, choose Chimera or Hydra, verify the preparation team and strategy profile, then use **Start Control**. Use the in-game pause button or **Pause Control** in the tool to stop the session.

## Build a portable Windows release

After building the x64 native agent and React interface, run:

```powershell
powershell -File .\tools\build_chimera_desktop.ps1
```

The build machine needs Python plus UnityPy, fmod-toolkit, and archspec; PyInstaller and the desktop UI dependencies are already vendored in the repository. The default output is the single file `build\release\AllianceBossStrategyStudio-1.0.1.exe`. Target PCs do not need Python, Node.js, Visual Studio, the Visual C++ runtime, UnityPy, fmod-toolkit, or archspec. The native agent itself depends only on Windows system DLLs.

On first launch the executable requests administrator permission and may take a few seconds to unpack its private runtime. Microsoft Edge WebView2 Runtime is the only external runtime prerequisite; it is already installed on normal supported Windows 10/11 systems with Microsoft Edge. Bundling a fixed WebView2 runtime would add roughly a browser-sized dependency, so it is intentionally not included.

For packaging diagnostics, `-NoUac` creates the same executable without the administrator manifest, and `-OneDir` creates the traditional folder-based form.

## Repository layout

- `src/agent` — native IL2CPP state agent and guarded command queue
- `tools/chimera_controller.py` — live state evaluation and strategy execution
- `tools/chimera_web.py` — local React application service and controller bridge
- `tools/boss_modes.py` — Chimera/Hydra mode and strategy-profile handling
- `tools/chimera_inventory.py` — read-only offline inventory of the local RAID installation
- `ui` — React, TypeScript, and Vite interface
- `config` — example strategies and local user-strategy location
- `data` — stable encounter definitions and rotation catalogs
- `docs` — state contracts, strategy reference, and historical research notes
- `third_party` — vendored runtime/build dependencies and their licenses

Local user strategies, caches, logs, compiled binaries, and build output are excluded from Git.

## Safety boundaries

Injection and automated play may violate the game's rules and can result in account penalties. Use this project at your own risk.

The project does not implement anti-detection, stealth loading, protection bypasses, network manipulation, or server-side data modification. State-changing actions are allowed only after local state validation, and every submitted action is checked again inside the game process.

## License

This project is licensed under the GNU General Public License v3.0. See `LICENSE` for the full text. Third-party licenses are listed in `THIRD_PARTY_NOTICES.md` and the corresponding directories under `third_party`.
