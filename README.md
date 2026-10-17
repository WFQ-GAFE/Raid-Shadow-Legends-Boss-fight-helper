# RSL-Boss-helper

<img src="branding/alliance-boss-strategy-icon-v3.png" alt="RSL-Boss-helper" width="96">

**RAID: Shadow Legends Boss 战斗助手**

**官网 / Website：[中文](https://wfq-gafe.github.io/Raid-Shadow-Legends-Boss-fight-helper/zh/) · [English](https://wfq-gafe.github.io/Raid-Shadow-Legends-Boss-fight-helper/)**

**[下载最新版 / Download the latest release](https://github.com/WFQ-GAFE/Raid-Shadow-Legends-Boss-fight-helper/releases/latest)** · **[加入 Discord 社区 / Join the Discord community](https://discord.gg/Tv2rWKgbVr)**

A Windows helper for the Chimera and Hydra fights in **RAID: Shadow Legends**: write skill rules for your team, let the tool fight by them, and test the rules offline before a real battle.

**RAID: Shadow Legends** 奇美拉与六头蛇战斗的 Windows 助手：为队伍编写技能规则，让工具按规则出手，并在实战前离线检验规则。

Current version: **1.1.4** ([release notes / notas da versão](docs/1.1.4-release-notes.md)) · 当前版本：**1.1.4**（[更新说明](docs/1.1.4-release-notes.md)）

## Getting started / 开始使用

1. Download `RSL-Boss-helper-1.1.4.exe` from the latest release and run it; no installation is needed (Windows x64 with the Microsoft Edge WebView2 Runtime).<br>
   从最新版本下载 `RSL-Boss-helper-1.1.4.exe` 直接运行，无需安装（Windows x64，需要 Microsoft Edge WebView2 运行库）。
2. Start RAID, pick your account in the tool and choose Chimera or Hydra. Open the boss preparation screen and the tool reads your team.<br>
   打开游戏，在工具中选择账号和奇美拉或六头蛇模式。打开 Boss 准备界面，工具会读取你的队伍。
3. Write rules in **Action rules** (or import a strategy someone shared) and set **Battle Goals**. On the preparation screen, click **Start** under **Controller**: the tool starts the battle and plays it from the opening.<br>
   在「行动规则」编写规则（或导入别人分享的策略），设好「战斗目标」。在准备界面点「接管控制」里的「开始执行」，工具会开始战斗并从开局接管。
4. After updating the tool, restart the game once. Your strategies are kept in `%LOCALAPPDATA%\RSL-Boss-helper` and carried over from earlier versions automatically.<br>
   更新工具后请重启一次游戏。策略保存在 `%LOCALAPPDATA%\RSL-Boss-helper`，旧版本的策略会自动带过来。

## What's new in 1.1.4 / 1.1.4 新功能

- **More human-like pacing:** the tool waits a short random moment before each regroup, each battle start and each skill, and takes a short break after many retries in a row. Built in; nothing to set.<br>
  **更像真人的操作节奏：** 重整、开战和每次出手前都会随机停顿片刻，连续重来多次后还会稍作休息。已内置，无需设置。
- **Faster regroups:** when the opening simulation predicts the goals will fail, the tool regroups as soon as the simulation finishes, even in the middle of a skill animation, for both Hydra and Chimera.<br>
  **重整更快：** 开局模拟预计无法达成目标时，模拟一算完就立即重整，技能动画中途也可以；六头蛇和奇美拉都适用。
- **Simulations closer to real battles:** fixed Chimera simulations that could add the Hydra area bonus to your team. After a Boss rotation, simulations use the current rotation's Boss once you have opened the Boss preparation screen with the tool running. Teams use your own account's bonuses even when the base battle came from another account.<br>
  **模拟更接近实战：** 修复奇美拉模拟可能把六头蛇区域加成算进队伍的问题。Boss 换轮后，只要工具开着时打开过一次 Boss 准备界面，模拟就按本轮的 Boss 进行。用其他账号的战斗作底子时，队伍加成按本账号计算。
- **Also:** picking a run in a simulation report shows its damage and its trials or regroup result, and champion names and portraits missing on first use are now filled in automatically.<br>
  **其他：** 在模拟报告中选择某一场时显示该场伤害和试炼或重整情况；修复首次使用时英雄名称和头像缺失的问题。

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
