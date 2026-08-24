# RAID Alliance Boss Strategy Studio

A local Windows strategy editor and battle controller for the Alliance Chimera and Hydra encounters in **RAID: Shadow Legends**.

Current release: **1.0.1**.

Download the .exe tool in release page.

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

### Hydra

- Identifies Hydra heads by head type rather than by screen position.
- Supports conditions and target priorities for all heads, any head, or preferred head types.
- Uses loose head matching so respawns and rotation changes do not invalidate a strategy unnecessarily.
- Tracks total encounter damage without accumulating obsolete per-head health entries after respawns.
- Can stop at the result screen when the damage target is met or safely restart with the verified six-champion team when it is not.
- Prioritizes dead allies for revive skills and falls back to automatic legal-target selection when no preferred target is available.


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
