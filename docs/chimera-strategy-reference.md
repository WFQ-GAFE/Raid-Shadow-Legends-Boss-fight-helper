# 奇美拉策略条件参考

策略节点只接受 `priority`/`selector`、`branch`/`condition`、`rule`、`cast`、`transform`、`maintainEffects` 和 `pause`。未知节点类型、未知条件名、缺少技能或目标的施法动作会在载入配置时被拒绝；控制器不会忽略拼写错误后继续施法。

同一个 `when` 中的条件全部为“并且”。`form` 等相等条件可以给单值或数组。`priority.children` 从上到下选择第一条既匹配、技能就绪且目标属于游戏即时合法目标集合的动作。

## 战斗与轮换

- `form`、`nextForm`：当前和下一奇美拉形态。
- `chimeraTurnCount`、`chimeraTurnAtLeast`、`chimeraTurnAtMost`：奇美拉 Boss 的回合及区间；只有 Boss 行动才增加，是形态准备和固定回合策略应使用的字段。
- `round`、`turn`、`playerTurnCount`、`activeHeroTurnCount`：底层战斗轮次、全体行动序号、我方行动计数和当前英雄个人行动计数，用于诊断或非常精细的内部时序。
- `turnAtLeast`、`turnAtMost`：兼容旧策略的底层全体行动序号区间；新界面不再用它表示奇美拉回合。
- `turnsUntilFormChangeAtLeast`、`turnsUntilFormChangeAtMost`：只按奇美拉 Boss 回合计算；固定周期为 `Ultimate×5 → Ram×5 → Ultimate×5 → Lion×5 → Ultimate×5 → Viper×5`，然后重复。在每段第 5 回合完成后该值为 `0`，表示奇美拉下一次行动就会切换形态。
- `nextForm`：按上述固定周期预测下一形态；内部 `Snake` 与用户配置中的 `Viper` 视为同一毒蛇形态。
- `allianceDifficultyId`、`chimeraStageId`、`stageRotationIndex`：难度、当前属性阶段及其在该难度阶段列表中的序号。
- `catalogFingerprint`、`trialDefinitionFingerprint`、`rewardRotationFingerprint`、`attributeRotationFingerprint`：总目录、稳定试炼定义、随时间变化的奖励、Boss 属性阶段版本。
- `currentDamageAtLeast`、`currentDamageBelow`：当前伤害范围。

## 英雄、生命与效果

- `activeHeroTypeId`：当前行动英雄类型；可以为单值或数组，不要求队伍固定。
- `activeHeroFormIndex`：当前行动英雄的持久形态编号；普通/原形态通常为 `0`，神话英雄切换后的形态通常为 `1`。这是区分两套三个主动技能的权威字段。
- `activeHeroIsMetamorph`：当前行动英雄是否具备神话变形能力。
- `activeHeroIsTransformed`：是否处于非零持久形态；由 `activeHeroFormIndex` 推导，不依赖只在变形动画期间有效的瞬时标记。
- `transformationReady`：当前 HUD 中是否存在已就绪、可对自己使用的最后一个主动变形技能。
- `activeHeroHpPctBelow`、`bossHpPctBelow`、`anyAllyHpPctBelow`：生命百分比阈值。
- `bossHasEffect`、`bossMissingEffect`、`activeHeroHasEffect`、`activeHeroMissingEffect`：精确效果选择器。
- `anyAllyHasEffect`、`anyAllyMissingEffect`、`allAlliesHaveEffect`：所有存活友方上的效果条件。
- `allyHasEffect`、`allyMissingEffect`：用 `heroTypeId` 或战斗内 `actorId` 选定一名友方，再在 `effect` 中描述效果。
- `bossEffectSlotsAtLeast/AtMost`、`activeHeroEffectSlotsAtLeast/AtMost`、`anyAllyEffectSlotsAtLeast`、`allAlliesEffectSlotsAtMost`：效果槽占用条件，用于在每个目标最多十个效果前预留空间。
- `allyEffectSlotsAtLeast/AtMost`：指定友方的效果槽数量；值形如 `{"heroTypeId": 12345, "count": 9}`。
- `deadAlliesAtLeast`、`livingAlliesAtLeast`：队伍存活状态。

效果选择器可使用 `kind`（名称或类别 ID）、`effectTypeId`、`turnsAtLeast`、`turnsAtMost` 和 `producerId`。界面新建的条件一律使用具体 `effectTypeId`，因此降低防御 30%/60%、增加攻击 25%/50%、恐惧/真实恐惧、隐身/完美隐身等弱强或近似效果会使用各自的游戏图标并分别判断；旧策略的 `kind` 仍按效果大类兼容。例如：

```json
{
  "kind": "IncreaseDefense",
  "turnsAtLeast": 2
}
```

## 试炼

- `completedTrialsAll/Any`、`incompleteTrialsAll`：已完成或尚未完成的试炼 ID。
- `startedTrialsAll/Any`：已经开始计数的试炼 ID。
- `activeTrialsAll/Any`：在所属 Wing/Tail/Paw 分支中已解锁、尚未完成的当前难度试炼；不要求 Boss 正处于对应形态。
- `eligibleTrialsAll/Any`：既是分支当前难度、Boss 又正处于对应形态，因此此刻允许推进的试炼。
- `lockedTrialsAny`：因同一形态、同一分支的低难度试炼尚未完成而锁定的试炼。
- `possibleTrialsAll`：运行时明确可完成，或已经完成的试炼 ID。
- `impossibleTrialsAny`：运行时明确 `possible=false` 或 `impossible=true` 的试炼 ID；缺失/未知不会当成失败。
- `trialProgressAtLeast`、`trialProgressBelow`：以试炼 ID 到 0–1 进度的对象比较，例如 `{"8000502": 0.5}`。

实时试炼状态从游戏模型读取 `canChangeProgress`、`canChangeCounter` 和 `inProgress`，但这些字段只说明当前形态/时点能否推进，不能单独证明整场战斗已经失败。`possible/impossible` 采用保守的永久可行性判断：结合静态目录中的 `formId`、当前 Boss 总回合和游戏实时 `formSequence`，只有未完成试炼所属形态的最后一个窗口已经过去，才写入 `possible=false`、`impossible=true` 与 `impossibilityReason=last_form_window_expired`。`lastEligibleBossTurn` 同时提供给策略和界面显示。

每个 Ram/Lion/Snake 形态分别有 Wing、Tail、Paw 三条可同时进行的试炼链；每条链严格按 Easy→Normal→Hard 解锁。实时状态用 `partId/part`、`difficultyId/difficulty`、`chainState`、`activeInChain`、`matchingCurrentForm` 和 `eligibleNow` 区分“链上当前试炼”与“此刻可以推进”。`requiredPrerequisiteTrialIds` 给出全部低难度前置，`blockingPrerequisiteTrialIds` 只列尚未完成的前置。把 Normal 或 Hard 设为必要目标时，控制器会递归把其前置试炼加入必要目标，不能绕过顺序。

顶层 `objectives.mandatoryTrialIds` 独立于动作规则。任一必要试炼被明确判定不可完成时，默认执行 `free_regroup_and_retry_manual`；重整后核对原五人、关闭并复核自动战斗、拒绝快速战斗，并要求产生新的战斗实例。`maxRegroupRetries` 默认 10，`0` 表示不限制。结算画面始终停留给用户决定，不调用保存或再次战斗。

界面中的“选择当前试炼”只显示当前奇美拉难度对应的 27 条试炼，并直接使用游戏当前数据里的中文说明与奖励。稳定的试炼定义和会随时间变化的奖励轮换用不同指纹识别；离开奇美拉或难度无法确认时会清空临时列表，不沿用旧轮换。策略保存时还会记录当前生命周期里核对过的五名英雄实例 ID，供首次自动入场及免费重整后复核，规则本身仍按英雄类型匹配，因此不限定固定英雄阵容。

界面规则编辑器支持当前/下一形态、固定回合、伤害、试炼完成/失败/进度、Boss/行动英雄/任一友方/指定友方的效果类型和剩余回合，以及 Boss、全体友方或指定友方的效果格数。严格规则还可以直接选择“仅当试炼当前激活”：只有该试炼已解锁、尚未完成且 Boss 正处于对应形态时，规则才参与执行和技能预留。“缺少/不足”表示找不到满足所填最低剩余回合的效果，可用于在效果到期前补充。动作方式可以直接选择“自动学习并维持必要效果”，分别填写 Boss、当前行动英雄或任一友方所需效果，不需要手写 JSON。

## 动作与目标

`cast` 必须指定 `skillSlot` 或 `skillTypeId`，并明确给出 `target`。支持：

- `{"type": "boss"}`
- `{"type": "self"}`
- `{"type": "lowestHpAlly"}`
- `{"type": "allyHeroTypeId", "heroTypeId": 12345}`

神话英雄的变形使用独立动作 `{"type": "transform"}`，不填写技能槽和目标。控制器只会在 `activeHeroIsMetamorph=true` 时，从当前形态 HUD 中选择已就绪、非被动、可对自己使用且槽位不小于 4 的最后一个主动技能。建议同时限定 `activeHeroFormIndex`，例如只在原形态 `0` 时切换；变形后的三项技能继续用普通 `cast`，并在规则中限定形态 `1`。主界面的“神话英雄：切换形态”会自动加入这些安全条件。

`maintainEffects` 是不绑定英雄的效果维护动作。它从运行时 `AppliedEffect.SkillTypeId` 自动学习“技能类型→产生的效果→Boss/自己/友方目标”，在当前行动英雄拥有已就绪的已知提供技能时补效果；尚未找到提供者时，可以有上限地试用未学习的非一技能。示例：

```json
{
  "type": "maintainEffects",
  "requirements": [
    {
      "scope": "boss",
      "effect": {"kind": "StatusReduceDefence"},
      "keepTurnsAtLeast": 1
    },
    {
      "scope": "activeHero",
      "effect": {"kind": "Invisible"},
      "keepTurnsAtLeast": 1
    }
  ],
  "probeUnknownSkills": true,
  "probeTransforms": false
}
```

`scope` 支持 `boss`、`activeHero` 和 `anyAlly`。维护动作只在效果不足时产生决策；条件已经满足或当前英雄没有合适技能时，优先级树继续匹配后面的攻击、防御或暂停规则。新能力只保存在内存中，控制器退出时若确实学到新映射，才合并写入一次 `data/chimera-skill-capabilities.json`；普通快照和回合不会写盘。目标已有十个效果且待补效果不存在时不会尝试挤入第十一个效果。

`executeTrialRecipe` 是面向当前试炼的结构化决策动作。`data/chimera-trial-recipes.json` 为当前难度的 27 类试炼定义形态、效果前置、禁用效果、效果数量、存活人数和最终动作目标；规则只需要给出试炼 ID，不需要绑定英雄名。示例：

```json
{
  "type": "executeTrialRecipe",
  "trialIds": [8000501],
  "prepareWithinBossTurns": 5,
  "probeUnknownSkills": false,
  "probeTransforms": false
}
```

该动作只处理当前链上已解锁且当前形态可推进的试炼。进入目标形态前 `prepareWithinBossTurns` 个 Boss 回合内，控制器会保留能提供必要效果的冷却技能，同时优先使用已知的护盾、增加防御、减伤、阻挡减益和免死类技能保护队伍；`ShareDamage` 不作为通用保护手段。进入试炼窗口后，先补足必要效果和效果槽条件，再攻击。未知效果技能在正式试炼窗口默认不试用，避免为了探索消耗关键冷却；未知但可直接攻击 Boss 的技能会在条件满足时各试用一次，随后按该技能对总伤害和当前试炼进度的实测指数移动平均选择。效果映射与伤害统计都在接管期间保存在内存中，控制器退出时最多合并写盘一次。

配方的 `automation` 分为 `automatic`、`assisted` 和 `manual`。`automatic` 表示当前状态模型足以闭环；`assisted` 表示可以维护大部分前置，但伤害来源、受击目标或计时语义仍需更多运行时证据；`manual` 会安全暂停，不会把反击/组队攻击、Duel 条、反弹伤害或多目标复活等尚未精确建模的机制冒充为自动完成。必要试炼失败只接受游戏明确的 `possible=false`/`impossible=true`，或游戏给出的 `lastEligibleBossTurn` 已经过期；预测伤害不足不会误触发免费重整。

外部策略选出的目标仍必须存在于游戏当回合 `GetAcceptableTargets(SkillData)` 返回值中；代理在游戏主线程再次核对区域、奇美拉模式、英雄形态、英雄、技能、冷却、目标、回合、账户和接管会话。变形后即使英雄个人回合数不变，形态编号和技能更新计数也会构成新的决策标识；上一形态的排队命令会被进程内守卫拒绝。
