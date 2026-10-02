# RSL-Boss-helper

<img src="branding/alliance-boss-strategy-icon-v3.png" alt="RSL-Boss-helper" width="96">

**RAID: Shadow Legends Boss 战斗助手**

**官网 / Website：[中文](https://wfq-gafe.github.io/Raid-Shadow-Legends-Boss-fight-helper/zh/) · [English](https://wfq-gafe.github.io/Raid-Shadow-Legends-Boss-fight-helper/)**

**[下载最新版 / Download the latest release](https://github.com/WFQ-GAFE/Raid-Shadow-Legends-Boss-fight-helper/releases/latest)** · **[加入 Discord 社区 / Join the Discord community](https://discord.gg/Tv2rWKgbVr)**

A Windows helper for the Chimera and Hydra fights in **RAID: Shadow Legends**: write skill rules for your team, let the tool fight by them, and test the rules offline before a real battle.

**RAID: Shadow Legends** 奇美拉与六头蛇战斗的 Windows 助手：为队伍编写技能规则，让工具按规则出手，并在实战前离线检验规则。

Current version: **1.1.2** ([release notes / notas da versão](docs/1.1.2-release-notes.md)) · 当前版本：**1.1.2**（[更新说明](docs/1.1.2-release-notes.md)）

## Getting started / 开始使用

1. Download `RSL-Boss-helper-1.1.2.exe` from the latest release and run it; no installation is needed (Windows x64 with the Microsoft Edge WebView2 Runtime).<br>
   从最新版本下载 `RSL-Boss-helper-1.1.2.exe` 直接运行，无需安装（Windows x64，需要 Microsoft Edge WebView2 运行库）。
2. Start RAID, pick your account in the tool and choose Chimera or Hydra. Open the boss preparation screen and the tool reads your team.<br>
   打开游戏，在工具中选择账号和奇美拉或六头蛇模式。打开 Boss 准备界面，工具会读取你的队伍。
3. Write rules in **Strategy Tree** (or import a strategy someone shared) and set **Battle Goals**. On the preparation screen, click **Start** under **Controller**: the tool starts the battle and plays it from the opening.<br>
   在「策略树」编写规则（或导入别人分享的策略），设好「战斗目标」。在准备界面点「接管控制」里的「开始执行」，工具会开始战斗并从开局接管。
4. After updating the tool, restart the game once. Your strategies are kept in `%LOCALAPPDATA%\RSL-Boss-helper` and carried over from earlier versions automatically.<br>
   更新工具后请重启一次游戏。策略保存在 `%LOCALAPPDATA%\RSL-Boss-helper`，旧版本的策略会自动带过来。

## What's new in 1.1.2 / 1.1.2 新功能

- **Complete simulation packages:** **Save** packages the team setup, account bonuses and an existing Boss opening when available. Export and share them together; importing a complete package selects the author's team and opening for simulation. Missing data is clearly shown and filled in automatically when available; a compatible local game runtime is still required.<br>
  **完整模拟包：** 数据可用时，「保存」会一起打包队伍配置、账号加成和已有 Boss 开局。导出后一并分享，导入完整包会自动选好作者队伍与开局。缺少数据会明确提示，数据可用后自动补全；仍需本机兼容游戏引擎。
- **New champion picker and profiles:** drag champions into team slots, reorder or remove them, and filter by affinity, role, faction, damage basis, auras and skill effects. Double-click a champion for base stats, skill descriptions, readable damage formulas and book bonuses. Team slots do not determine actual turn order.<br>
  **新英雄选择与简介：** 拖入英雄组队、调整站位或移出，按属性、定位、阵营、伤害基于、光环和技能效果筛选。双击查看基础属性、技能说明、伤害公式的文字解释与技能书加成。队伍站位不等于实际出手顺序。
- **Full Hydra opening forecast reports:** open a generated report to inspect devour marks, damage, deaths and the rule behind each action; stalled rules show the champion, turn and reasons.<br>
  **六头蛇开局推演完整报告：** 打开已生成的报告，查看吞噬标记、伤害、阵亡和每次出手的规则；卡住时定位英雄、回合及规则未执行的原因。
- **Português (Brasil):** the interface, tool messages and new logs now support three languages. Game-provided names and descriptions follow the game client's language.<br>
  **巴西葡萄牙语：** 界面、工具提示和新日志支持三种语言；游戏提供的名称与说明跟随游戏客户端语言。
- **More reliable editing and scrolling:** explicit save and unsaved-change prompts, rules that keep matching after ascension, skill details on hover and corrected skill icons, plus consistent scrolling over cards and nested lists.<br>
  **编辑和滚动更可靠：** 显式保存与未保存提醒、觉醒后继续匹配的规则、技能悬浮说明与图标修正，以及卡片和内嵌列表的稳定滚轮体验。

[Read the full release notes in 简体中文, English or Português (Brasil) / 查看完整三语更新说明](docs/1.1.2-release-notes.md).

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
