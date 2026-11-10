# RSL-Boss-helper

<img src="branding/alliance-boss-strategy-icon-v3.png" alt="RSL-Boss-helper" width="96">

**RAID: Shadow Legends Boss 战斗助手**

**官网 / Website：[中文](https://wfq-gafe.github.io/Raid-Shadow-Legends-Boss-fight-helper/zh/) · [English](https://wfq-gafe.github.io/Raid-Shadow-Legends-Boss-fight-helper/)**

**[下载最新版 / Download the latest release](https://github.com/WFQ-GAFE/Raid-Shadow-Legends-Boss-fight-helper/releases/latest)** · **[加入 Discord 社区 / Join the Discord community](https://discord.gg/Tv2rWKgbVr)**

A Windows helper for the Chimera and Hydra fights in **RAID: Shadow Legends**: write skill rules for your team, let the tool fight by them, and test the rules offline before a real battle.

**RAID: Shadow Legends** 奇美拉与六头蛇战斗的 Windows 助手：为队伍编写技能规则，让工具按规则出手，并在实战前离线检验规则。

Current version: **1.1.6** ([release notes / notas da versão](docs/1.1.6-release-notes.md)) · 当前版本：**1.1.6**（[更新说明](docs/1.1.6-release-notes.md)）

## Getting started / 开始使用

1. Download `RSL-Boss-helper-1.1.6.exe` from the latest release and run it; no installation is needed (Windows x64 with the Microsoft Edge WebView2 Runtime).<br>
   从最新版本下载 `RSL-Boss-helper-1.1.6.exe` 直接运行，无需安装（Windows x64，需要 Microsoft Edge WebView2 运行库）。
2. Start RAID, pick your account in the tool and choose Chimera or Hydra. Open the boss preparation screen and the tool reads your team.<br>
   打开游戏，在工具中选择账号和奇美拉或六头蛇模式。打开 Boss 准备界面，工具会读取你的队伍。
3. Write rules in **Action rules** (or import a strategy someone shared) and set **Battle Goals**. On the preparation screen, click **Start** under **Controller**: the tool starts the battle and plays it from the opening.<br>
   在「行动规则」编写规则（或导入别人分享的策略），设好「战斗目标」。在准备界面点「接管控制」里的「开始执行」，工具会开始战斗并从开局接管。
4. After updating the tool, restart the game once. Your strategies are kept in `%LOCALAPPDATA%\RSL-Boss-helper` and carried over from earlier versions automatically.<br>
   更新工具后请重启一次游戏。策略保存在 `%LOCALAPPDATA%\RSL-Boss-helper`，旧版本的策略会自动带过来。

## What's new in 1.1.6 / 1.1.6 新功能

- **Game 11.90 support:** Hydra and Chimera simulations work again after the game update (Hydra showed 0 damage, the Chimera's did not run).<br>
  **适配游戏 11.90：** 游戏更新后六头蛇模拟伤害为 0、奇美拉模拟无法运行的问题已修复。
- **One-click update:** the **Check for updates** button next to the version downloads a new version and restarts into it.<br>
  **一键更新：** 版本号旁的「检查更新」可以下载新版本并重启到新版本。
- **Trial champions (Chimera):** in **Battle Goals**, lists the champions in your roster whose skills apply what the required trials need.<br>
  **试炼可用英雄（奇美拉）：** 在「战斗目标」中列出斗士库里技能能满足必要试炼的英雄。
- **Action rules grouped by champion**, with a champion filter and a hint for champions without a default skill order.<br>
  **行动规则按英雄分组**，可以只看某个英雄的规则；没有默认技能顺序的英雄会有提示。
- **More detailed simulation reports:** why a trial did not start, a condition check per trial, and per-target damage and health in the action log.<br>
  **模拟报告更详细：** 试炼没开始的原因、每个试炼的条件检查，以及出手记录里每个目标的伤害和血量。

## Features / 主要功能

- **Skill rules:** ordered rules with conditions on buffs and debuffs, cooldowns, turns and which champions are alive, preferred targets, and a default skill order as fallback. You can still play manually at any time.<br>
  **技能规则：** 按顺序执行的规则，可设置增益/减益、冷却、回合、英雄存活等条件和优先目标，没有可用规则时按默认技能顺序出手。随时可以手动操作。
- **Chimera:** follows forms and trials, keeps skills reserved for trials, and regroups for free when the required trials or the damage goal can no longer be reached.<br>
  **奇美拉：** 跟踪形态和试炼，为试炼保留技能；必要试炼或伤害目标已无法达成时免费重整。
- **Hydra:** targets heads by type, and regroups for free when the devour order breaks your conditions or the damage goal is missed; an opening forecast can predict the devour order.<br>
  **六头蛇：** 按蛇头类型选择目标；吞噬顺序违反设定条件或伤害未达标时免费重整；开局推演可以提前预测吞噬顺序。
- **Offline simulation:** replay saved Chimera and Hydra battles in a copy of the game's own engine with your rules, and see where they succeed or stall.<br>
  **离线模拟：** 用游戏自己的引擎副本按你的规则重打保存的奇美拉和六头蛇战斗，查看规则在哪里成功、在哪里卡住。
- **Sharing:** export and import strategies with the author's team setup. Complete simulation packages also include account bonuses and a Boss opening, so you can simulate without owning the same champions or opening the game to read the author's setup; a compatible local game runtime is required.<br>
  **分享：** 导入导出策略，并附带作者的队伍配置。完整模拟包还包含账号加成与 Boss 开局，接收者无需拥有相同英雄，也无需打开游戏读取作者配置即可模拟；需要本机兼容游戏引擎。
- English, 简体中文 and Português (Brasil) interface, tool messages and new logs. Game-provided names and descriptions follow the game client's language, and custom names are preserved.<br>
  界面、工具提示和新日志支持英文、简体中文和巴西葡萄牙语。游戏提供的名称与说明跟随游戏客户端语言，用户自定义名称保留原文。

## Please note / 注意

Automating play may break the game's terms of service and can lead to account penalties; use it at your own risk. The tool does not bypass protections, change network traffic or modify server data, and every action is checked again inside the game before it is carried out.

自动化操作可能违反游戏条款并导致账号受罚，风险由使用者自行承担。本工具不绕过保护、不改动网络通信或服务器数据，每个操作在执行前都会在游戏内再次校验。

## Developers / 开发者

Source, design notes, build and release steps are in [`docs`](docs). Licensed under the GNU General Public License v3.0 (`LICENSE`); third-party licenses are listed in `THIRD_PARTY_NOTICES.md`.

源码说明、设计记录以及构建和发布步骤见 [`docs`](docs)。本项目采用 GNU General Public License v3.0（`LICENSE`），第三方许可证见 `THIRD_PARTY_NOTICES.md`。
