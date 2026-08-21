# Chimera State Contract

代理通过名为 `Local\RaidChimeraAgentState-<pid>` 的页文件共享内存发布 UTF-8 JSON。共享区包含协议头，以及账户、决策、命令回执、生命周期、战斗台账、轮换目录和最新诊断七个 JSON 槽；正常运行不追加硬盘日志。每个 JSON 槽使用奇偶序列锁避免读取半条数据。`sequence` 在同一次 DLL 载入期间递增；控制器只处理尚未见过且足够新的 `decision_state`。

## 接管生命周期

`lifecycle_state` 同时记录接管状态和当前界面。接管状态为 `idle`、`active` 或 `interrupted`；界面为 `unknown`、`team_selection`、`battle` 或 `result`。队伍界面快照包含五个英雄 ID、自动战斗、快速战斗、区域和 `canStart`；开始战斗时，这五个英雄 ID 和 `stageId` 会保留到战斗生命周期，供免费重整后逐项核对。重整使游戏重新开启自动战斗时，代理只在主线程通过原生 `Property<bool>.Set(false, false)` 关闭它，并立即重新读取确认；快速战斗仍开启或状态没有实际变为关闭都会拒绝开战。只有上述守卫全部通过且随机会话 ID 仍匹配时，开始请求才调用原生 `StartBattleClick()`。

通用 `ClientBattleViewContext` 启用时只发布 `screen=unknown` 和“待验证”原因；只有战斗模型同时确认区域 13、战斗类型 8、奇美拉预设、有效行动英雄且战斗未结束后，才升级为 `screen=battle`。因此竞技场、地下城等普通战斗不会沿用上一次奇美拉选队缓存而被误标为奇美拉战斗。热重载恢复战斗上下文时也遵循同一验证流程。

接管会话不只绑定 PID。界面把选定时的游戏内 `accountName` 和 `userId` 传给控制器；控制器持续读取账户槽并精确核对二者。开始接管时，64 位 `userId` 还会写入代理的会话守卫；每个技能或生命周期命令在游戏主线程执行前重新读取 `AppModel` 当前用户 ID。读取失败或账户变化会把接管标记为 `interrupted`，并清空技能与生命周期待执行队列。结束接管只清理代理内部状态，不执行游戏动作。

游戏窗口鼠标、键盘、手动选技能和手动开始不会解除接管。若这些操作改变了当前英雄、形态、技能、目标、回合或界面，已经排队的旧命令仍会被主线程守卫拒绝；`guard_failed`、`battle_guard_changed` 和 `duplicate_turn` 被控制器视为可恢复竞态，只等待下一份有效快照且不重复旧回合。游戏内 `BattleHUDContext.OnPauseClick()` 会把接管改为 `interrupted`、原因写为 `game_pause_clicked` 并清空待执行命令；主工具的“暂停接管”则通过每个 PID 独立的内存事件通知控制器。控制器随后在 `finally` 中解除当前随机会话。账户变化仍会立即把接管改为 `interrupted`。

生命周期动作 `3` 先调用游戏原生 `CancelChimeraBattle()` 发起服务器退出验证；如果游戏没有直接返回队伍界面，只有验证响应已经写入且不再处于处理中，动作 `2` 才调用 `ExecuteCancelChimeraBattleCmd()` 完成免费重整。两步都要求接管会话、同一战斗界面实例和模型中的区域 13、战斗类型 8、奇美拉预设、Boss 类型及未结束状态同时有效。动作 `4` 只在游戏主线程刷新队伍快照，不开始战斗；控制器据此逐项核对动态取得的原五人 ID、区域和快速战斗状态，再由动作 `1` 关闭并复核自动战斗、创建不同的新战斗实例。正式策略默认以手动模式重试，默认最多 10 次；策略层只有在运行时对必要试炼给出明确的 `possible=false` 时才允许触发，未知状态不会被推断为不可能。

结算界面发布 `battle_ledger`，当前包含伤害、竞赛积分、已完成试炼数和 `disposition=awaiting_user`。代理不调用 `OnSaveResultPressed`、`RestartBattle` 或 `QuickRestartBattle`。

## 每回合决策快照

```json
{
  "type": "decision_state",
  "sequence": 6,
  "pid": 73664,
  "observedAtTick": 1766097093,
  "battle": {
    "areaTypeId": 13,
    "regionTypeId": 1302,
    "kindId": 8,
    "round": 1,
    "turn": 1,
    "playerTurnCount": 0,
    "finished": false,
    "autoMode": false,
    "chimeraPreset": true,
    "waitingForManualCommand": true,
    "metricsAvailable": true,
    "currentDamage": 1500000,
    "currentCompetitionPoints": 42
  },
  "activeHeroId": 1,
  "activeHeroTypeId": 4716,
  "activeHeroTurnCount": 1,
  "activeHeroFormIndex": 0,
  "activeHeroSkillsUpdateCounter": 1,
  "activeHeroIsMetamorph": false,
  "activeHeroIsTransformed": false,
  "skillCatalogFresh": true,
  "activeHeroName": "死亡女妖莉迪亚",
  "pointers": {
    "context": 0,
    "generator": 0,
    "mode": 0
  },
  "skills": [],
  "heroes": [],
  "bosses": [],
  "chimera": {
    "id": 5,
    "typeId": 26866,
    "currentFormIndex": 0,
    "currentForm": "Ultimate",
    "turnCount": 0
  },
  "allianceChimeraDifficultyId": 5,
  "chimeraStageId": 1302,
  "rotationIdentity": {
    "catalogFingerprint": "fnv1a64:c1147632231496b6",
    "trialDefinitionFingerprint": "fnv1a64:0007383d9a7550b6",
    "rewardRotationFingerprint": "fnv1a64:1167bcd49c62bc43",
    "attributeRotationFingerprint": "fnv1a64:d909443be47b6244",
    "metadata": {
      "turnsBetweenForms": 5,
      "formSequence": []
    }
  },
  "trialCatalog": {
    "available": true,
    "difficulties": []
  }
}
```

`skills` 只包含本回合 HUD 提供的技能对象。每个技能的 `validTargetIds` 都来自当前 `ClientBattleMode.GetAcceptableTargets(SkillData)`，不是历史记录或外部推断。代理刚热重载、而当前英雄 HUD 尚未重新推送技能时，仍发布完整战斗与试炼状态，但写入 `skillCatalogFresh=false` 并强制 `skills=[]`；因此目标跟踪不会中断，也不可能使用旧英雄或旧形态技能：

```json
{
  "slot": 2,
  "skillId": 1,
  "typeId": 47102,
  "name": "女妖哀嚎",
  "cooldown": 0,
  "defaultCooldown": 4,
  "passive": false,
  "blocked": false,
  "ready": true,
  "skillDataPtr": 0,
  "validTargetIds": [5]
}
```

`heroes` 和 `bosses` 元素保存模型状态、英雄自身技能和效果：

实体 ID 为非负整数。奇美拉五人队中，ID `0` 可以是合法的第五名英雄；只能依靠字典有效槽、非空对象和模型映射判断，不能用 `id > 0` 过滤。

```json
{
  "id": 1,
  "typeId": 4716,
  "name": "死亡女妖莉迪亚",
  "side": "ally",
  "currentFormIndex": 0,
  "turnCount": 1,
  "healthPct": 100,
  "dead": false,
  "active": true,
  "states": {
    "stunned": false,
    "frozen": false,
    "sleep": false,
    "provoked": false,
    "activeSkillsBlocked": false,
    "duelProducer": false,
    "duelTarget": false,
    "enfeeble": false,
    "rages": false
  },
  "skills": [],
  "effects": [
    {
      "effectTypeId": 370,
      "effectKindId": 2004,
      "effectKind": "Shield",
      "turnsLeft": 3,
      "producerId": 4
    }
  ],
  "challenges": []
}
```

挑战进度的 `targetRaw/currentRaw` 是游戏的 `Fixed` 原值；`target/current` 是按 `2^32` 还原后的数值。每条实时挑战还包含 `completed`、可用时的 `counterLimit/currentCounter` 和 `selfTurnWhichCompleted`，并读取游戏模型的 `canChangeProgress`、`canChangeCounter` 与 `inProgress`。每个形态按 Wing、Tail、Paw 分成三条并行链，各链按 Easy→Normal→Hard 顺序解锁；代理发布 `part`、`difficulty`、`requiredPrerequisiteTrialIds`、`blockingPrerequisiteTrialIds`、`chainState`、`activeInChain`、`matchingCurrentForm` 与 `eligibleNow`，区分已完成、链首活跃、前置锁定和当前形态可推进。这些模型字段只代表当前时点，不直接当作整场失败。代理结合试炼 `formId`、Boss 总回合与当前版本 `formSequence` 计算 `lastEligibleBossTurn`；只有未完成试炼错过所属形态的最后窗口，才明确发布 `possible=false`、`impossible=true` 和 `impossibilityReason=last_form_window_expired`。静态身份或轮换未知时不发布失败结论。`trialCatalog` 从当前版本游戏的 `StaticAllianceData` 读取；它按当前难度提供 27 条试炼的 ID、形态、部位、难度、中文描述、关联效果和奖励，不依赖手工维护的 ID 名称表。

独立的 `rotation_catalog` 槽只在代理启动或队伍界面发现静态数据变化时发布。六个难度是同一个时间段奖励表中的六组数据，不是六个轮换。身份被拆成三个互不混淆的版本：

- `trialDefinitionFingerprint`：稳定的试炼条件、形态、部位和关联效果，不含奖励；
- `rewardRotationFingerprint`：随时间变化的 162 条“难度 + 试炼 ID → 奖励”映射；
- `attributeRotationFingerprint`：Boss 属性阶段、血量和完整换形顺序。

`catalogFingerprint` 只是以上三者的组合校验值。控制器在 `data/chimera-rotation-catalogs.json` 中只保存一份稳定试炼定义，并按 `rewardRotationFingerprint` 分别保存每个时间段的奖励表；奖励变化会产生新版本并保留旧版本，不会把难度误当成轮换。`chimeraStageId` 和 `stageRotationIndex` 另行归入属性观察记录。相同奖励版本或属性阶段不会重复写盘，因此不会按回合产生硬盘写入。

战斗中的伤害直接汇总 `ChimeraStatisticsByMultiplier`，积分读取 `BattleState.ChimeraCompetitionPoints`；两者都来自战斗模型而非屏幕或血条估算。结算时仍以结算上下文中的最终值为准。

## 动作请求

控制器将匹配到的快照字段封装为版本 4 的 `QueueCommandRequest`。请求包含随机会话 ID、回合、玩家回合计数、行动英雄、英雄回合计数和英雄形态编号。代理不信任这些指针或 ID，并在游戏主线程重新验证：

- 对象类型仍为 `ClientCommandGenerator`、`ClientBattleMode` 和 `SkillData`；
- 当前仍等待手动命令，且模式仍绑定同一个命令生成器；
- 技能槽位、类型 ID、英雄类型、冷却、被动和阻止状态未变化；
- 目标仍在该技能即时返回的合法目标集合中；
- 目标仍是当前战斗中的友方或 Boss 实体；
- 模型仍为区域 13、战斗类型 8、奇美拉预设，战斗未结束；
- 当前行动英雄、奇美拉实体和目标实体仍能在模型中找到。
- 当前回合与英雄形态标识与生成决策时完全一致；同一标识最多允许一次执行提交。神话英雄变形后即使仍在同一行动中，也会以新形态和新 HUD 技能计数产生下一条决策。

请求的执行标志关闭时，仅完成上述验证并通过内存返回 `validated`。执行标志开启时，才调用 `CreateCmdManually(targetId, skillId)` 并返回 `submitted`；外部控制器随后观察新的回合标识。若新标识暂未出现，代理的一回合一次令牌仍会阻止重复提交，控制器保持接管并继续等待。

## 暂停、恢复与安全中断

未知形态、无匹配规则、过期快照或空合法目标只会让当前快照不执行，控制器不会自动退回普通攻击。手动操作导致的冷却、目标、英雄、形态或回合变化会让旧命令被代理拒绝，并从下一快照恢复；账户变化、非法命令或无法确认游戏对象等不可恢复错误才会安全中断。
