# RAID Alliance Boss Strategy Studio

**RAID 联盟 Boss 策略工作室**

A local Windows strategy editor and battle controller for the Alliance Chimera and Hydra encounters in **RAID: Shadow Legends**.

一款面向 **RAID: Shadow Legends** 联盟奇美拉与六头蛇战斗的本地 Windows 策略编辑器和战斗控制器。

Current release: **1.0.2**.

当前版本：**1.0.2**。

Download the .exe tool in release page.

请在 Release 页面下载 .exe 工具。

The application reads live battle state from the selected RAID client, evaluates user-defined rules, and submits guarded in-game actions. It includes an English and Chinese interface, separate Chimera and Hydra workspaces, multiple saved strategy profiles, live team discovery, hero and skill catalogs, target priorities, and independent run logs.

本应用会从选定的 RAID 客户端读取实时战斗状态，评估用户定义的规则，并提交经过安全校验的游戏内操作。它提供中英文界面、相互独立的奇美拉与六头蛇工作区、多个可保存的策略组、实时队伍识别、英雄与技能目录、目标优先级以及独立的运行日志。

## Current capabilities / 当前功能

### Shared features / 通用功能

- Detects running RAID accounts and binds a controller session to both the player name and player ID.<br>
  检测正在运行的 RAID 账户，并将控制器会话同时绑定到玩家名称和玩家 ID。
- Reads the currently selected preparation team and refreshes it when heroes are changed.<br>
  读取准备界面中当前选择的队伍，并在更换英雄时刷新队伍信息。
- Builds local hero, skill, effect, trial, and visual-asset catalogs from RAID data.<br>
  根据 RAID 数据在本地建立英雄、技能、效果、试炼和视觉资源目录。
- Supports multiple named strategy profiles for different teams.<br>
  支持为不同队伍创建多个命名策略组。
- Exports and imports portable strategy files without account-specific champion instance IDs.<br>
  支持导入和导出可移植的策略文件，其中不包含账户专属的英雄实例 ID。
- Provides English as the default language, with Chinese available from the language selector.<br>
  默认使用英文，并可通过语言选择器切换为中文。
- Shows hero portraits, skill icons, buff/debuff icons, and encounter-specific boss icons.<br>
  显示英雄头像、技能图标、增益/减益图标以及各战斗专属的 Boss 图标。
- Keeps Chimera and Hydra strategies, live state, and run logs separate.<br>
  将奇美拉与六头蛇的策略、实时状态和运行日志相互独立保存。
- Supports strict ordered rules, fallback skill orders, nested AND/OR/NOT condition groups, effect and cooldown checks, remaining-turn checks, and preferred targets.<br>
  支持严格的规则顺序、备用技能顺序、可嵌套的 AND/OR/NOT 条件组、效果与冷却检查、剩余回合检查和优先目标。
- Revalidates the account, acting hero, skill readiness, legal targets, battle mode, and current turn on the game thread before executing an action.<br>
  执行动作前，会在游戏线程中再次校验账户、行动英雄、技能就绪状态、合法目标、战斗模式和当前回合。
- Allows normal manual interaction. A stale action is rejected safely when manual input or turn progression changes the battle state.<br>
  允许玩家正常进行手动操作；当手动输入或回合推进改变战斗状态时，过期动作会被安全拒绝。

### Chimera / 奇美拉

- Tracks forms, form transitions, trials, trial chains, damage, effects, cooldowns, and legal targets.<br>
  跟踪形态、形态切换、试炼、试炼链、伤害、效果、技能冷却和合法目标。
- Supports trial-aware automatic decisions and structured recipes for known trial categories.<br>
  支持能够识别当前试炼的自动决策，以及针对已知试炼类型的结构化方案。
- Handles Mythical champion transformations and keeps the two forms' skill catalogs separate.<br>
  支持神话英雄变形，并将两种形态的技能目录分开处理。
- Can perform a guarded free regroup when a required trial becomes impossible, then verify the same five champions before re-entering battle.<br>
  当必要试炼已不可能完成时，可以在安全校验下执行免费重整，并在重新进入战斗前确认仍为相同的五名英雄。

### Hydra / 六头蛇

- Identifies Hydra heads by head type rather than by screen position.<br>
  根据蛇头类型而不是屏幕位置识别六头蛇目标。
- Supports conditions and target priorities for all heads, any head, or preferred head types.<br>
  支持针对全部蛇头、任一蛇头或优先蛇头类型设置条件和目标优先级。
- Uses loose head matching so respawns and rotation changes do not invalidate a strategy unnecessarily.<br>
  使用宽松的蛇头匹配方式，避免蛇头重生或轮换变化无谓地使策略失效。
- Tracks total encounter damage without accumulating obsolete per-head health entries after respawns.<br>
  跟踪整场战斗的总伤害，并且不会在蛇头重生后不断累积已经失效的单个蛇头生命值记录。
- Can stop at the result screen when the damage target is met or safely restart with the verified six-champion team when it is not.<br>
  达成伤害目标时可以停留在结算界面；未达成时，可以在确认六名英雄队伍无误后安全地重新战斗。
- Prioritizes dead allies for revive skills and falls back to automatic legal-target selection when no preferred target is available.<br>
  复活技能会优先选择已死亡的队友；没有可用的优先目标时，则回退到自动选择合法目标。

## Repository layout / 仓库结构

- `src/agent` — native IL2CPP state agent and guarded command queue<br>
  `src/agent` — 原生 IL2CPP 状态代理与带安全校验的指令队列
- `tools/chimera_controller.py` — live state evaluation and strategy execution<br>
  `tools/chimera_controller.py` — 实时状态评估与策略执行
- `tools/chimera_web.py` — local React application service and controller bridge<br>
  `tools/chimera_web.py` — 本地 React 应用服务与控制器桥接层
- `tools/boss_modes.py` — Chimera/Hydra mode and strategy-profile handling<br>
  `tools/boss_modes.py` — 奇美拉/六头蛇模式与策略组处理
- `tools/chimera_inventory.py` — read-only offline inventory of the local RAID installation<br>
  `tools/chimera_inventory.py` — 对本地 RAID 安装内容进行只读离线清点
- `ui` — React, TypeScript, and Vite interface<br>
  `ui` — 使用 React、TypeScript 和 Vite 构建的界面
- `config` — example strategies and local user-strategy location<br>
  `config` — 示例策略与本地用户策略的存放位置
- `data` — stable encounter definitions and rotation catalogs<br>
  `data` — 稳定的战斗定义与轮换目录
- `docs` — state contracts, strategy reference, and historical research notes<br>
  `docs` — 状态契约、策略参考和历史研究记录
- `third_party` — vendored runtime/build dependencies and their licenses<br>
  `third_party` — 随项目提供的运行/构建依赖及其许可证

Local user strategies, caches, logs, compiled binaries, and build output are excluded from Git.

本地用户策略、缓存、日志、已编译的二进制文件和构建输出均不会纳入 Git。

## Safety boundaries / 安全边界

Injection and automated play may violate the game's rules and can result in account penalties. Use this project at your own risk.

注入和自动化游戏操作可能违反游戏规则，并可能导致账户受到处罚。使用本项目的风险由用户自行承担。

The project does not implement anti-detection, stealth loading, protection bypasses, network manipulation, or server-side data modification. State-changing actions are allowed only after local state validation, and every submitted action is checked again inside the game process.

本项目不实现反检测、隐蔽加载、保护绕过、网络操控或服务器端数据修改。只有通过本地状态校验后才允许执行会改变状态的操作，并且每个已提交的动作都会在游戏进程内部再次接受检查。

## License / 许可证

This project is licensed under the GNU General Public License v3.0. See `LICENSE` for the full text. Third-party licenses are listed in `THIRD_PARTY_NOTICES.md` and the corresponding directories under `third_party`.

本项目采用 GNU General Public License v3.0 许可证。完整条款请参阅 `LICENSE`。第三方许可证列于 `THIRD_PARTY_NOTICES.md` 以及 `third_party` 下的对应目录中。
