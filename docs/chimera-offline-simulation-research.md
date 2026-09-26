# 奇美拉离线战斗模拟与按试炼选技能：研究结论与重建设计

研究日期：2026-09-25。游戏版本 11.75.0（`GameAssembly.dll` sha256 `d6c57a39…`，静态数据 `11.75.0/c261935…`）。实验代码与原始结果保存在本机 `out/chimera-sim-research-20260925/`（已被 `.gitignore` 忽略，不含游戏文件）。

## 结论

1. **奇美拉可以像六头蛇一样在隔离的原版引擎里完整模拟。** 同一个 `BattleProcessor` 负责奇美拉：形态轮换、27 个试炼的开始/进度/计数/完成全部由引擎内的效果计算，不在客户端界面层。用你的六头蛇队伍前 5 名英雄对第 5 难度奇美拉（Stage `13029005`，Boss `26866`）合成一场战斗，引擎在 4.9 秒内跑完 1491 次行动：第 6 个 Boss 回合进入 Ram 时 `8000501/8000504/8000507` 同时开始（与 0938 实战记录一致），完成 Easy 后同一回合开始 Normal，计数器按 Boss 回合累加并在窗口到期时归零。
2. **试炼规则可以从游戏数据精确读出，不需要再按描述猜测。** 每个试炼就是 Boss 形态上的一个技能（`HeroForm.ChallengeSkillTypeIds`），包含：开始效果（kind 9090，`ChallengeParams` 给出目标值、计数上限、是否按伤害计），进度效果（kind 9091/9009，带游戏表达式写成的 `Condition` 和 `MultiplierFormula`），计数效果（9092）和到期重置。例如 8000501 的计入条件是 `OwnerHasEffectOfKind(Fear_KindId)&&relatedSkillSource==Hero_Skill`，进度 `+DEALT_DMG`。完整表见文末。
3. **战斗可以精确分叉。** 相同指令重放得到逐位相同的状态（300 次行动后分叉，再并行 200 次行动全程哈希一致）。游戏自带的自动战斗 AI（`EnemyTurnActionGenerator`，客户端玩家自动模式用的也是它）不消耗战斗随机数。自写的内存深拷贝在 31–108 ms 内复制整场战斗（4.3 万–13.5 万个对象），与原战斗、与重放结果在 600 次行动内指纹（RNG、每名英雄 HP/冷却/效果数/试炼进度）完全一致，并且互不影响。游戏自己的 `BattleState` MessagePack 只有 7.6 KB 摘要，不能继续战斗；带 `BattleState` 的构造函数会重新 `InitTeams`，也不是续战接口。
4. **搜索是可行的。** 单核吞吐：引擎约 2.7 ms/行动，AI 约 0.55 ms/行动，克隆 20–110 ms。本机 24 个逻辑核、31 GB 内存。从 Ram 入口起尝试 64 种策略变体、每个跑完整个形态窗口，单核共 21 秒；不同变体的窗口伤害在 3400 万到 6100 万之间，Lion 窗口里 64 个变体有 16 个丢掉了基准 AI 完成的 8000513。随机扰动不能完成需要特定前置的试炼（该队伍没有恐惧、完美隐身等提供者），说明搜索必须由试炼条件引导，但结果判定交给引擎。
5. **这意味着可以重建为“离线规划 + 精确执行”。** 战斗开始时抓取本场 `BattleSetup`/`BattleSettings`，在离线引擎里搜索一条完成所选试炼且伤害最高的指令序列；实战中逐回合核对回合、行动者和 RNG 后照计划出手。由于战斗是确定的，计划里预测的试炼完成情况就是实战结果；偏离时可以用实际已执行的指令在离线重放出实战状态再重新规划。

## 实验记录

| 实验 | 设置 | 结果 |
| --- | --- | --- |
| 合成战斗 | 六头蛇抓取的 `BattleSetup` 改为 KindId 8、Stage 13029005、`ChimeraDifficulty=5`，敌方换成 Boss 26866（4 形态技能各 1 级），我方取前 5 名；双方都由游戏 AI 出手 | 1491 次行动 / 4.9 s；形态按 `ChimeraSequenceForms` 在第 6/11/16/21/26… Boss 回合切换；8000513 在第 18 个 Boss 回合完成并立即开始 8000514 |
| 重放分叉 | 第 300 次行动处重新建战斗并重放 300 次指令 | 用时 703 ms；之后 200 次行动 `CalcHash` 全程一致 |
| MessagePack 分叉 | `BattleState` 打包/解包 + 续战构造函数 / 替换进新处理器 | 打包仅 7.6 KB；续战构造函数空引用；替换后首个指令空引用 → 不可用 |
| 深拷贝分叉 | 共享静态数据、Setup、GameParameters 可达对象，其余逐字段复制 | 300 次行动处 4.3 万对象 31–47 ms；终局 13.5 万对象 108 ms；结构校验 0 问题；扰动副本 150 次行动后原战斗不变；锁步 600 次行动 0 分歧 |
| 性能 | 整场 1491 次行动 | `ApplyCommand` 2.65 ms、AI 0.55 ms、构造指令 0.01 ms |
| 搜索原型 | Ram/Lion 入口克隆 64 份，候选 k>0 以 30% 概率随机选合法技能 | 每组 ~21 s（单核）；伤害差异最大 1.8 倍；试炼结果随策略变化 |

合成战斗只用于证明机制：没有服务器给 Boss 的属性修正（`ModifiersSetup`）和 Boss 回合上限（`MaxBossTurnsCount`），所以打到了第 89 个 Boss 回合、伤害数值也不代表真实战斗；队伍也不是奇美拉阵容。真实数值必须来自实战抓取的 Setup。

## 为什么要重建现有的试炼规划

`docs/chimera-trial-planner-design.md` 列出的问题（按列表顺序选技能、按描述子串推断机制、计数单位混用、贡献者与效果身份混淆、窗口与评分）根源相同：控制器只能从描述和实战观察推断规则。现在规则可以原样读出，结果可以精确模拟，所以：

- 不再需要 `lydia_trial_rules.py`、试炼配方、技能能力学习缓存这类按描述或按经验建立的规则；
- 条件表达式只用于**引导搜索**（例如 8000507 需要 Boss 有降低防御、出手者有完美隐身，于是优先考虑施加这两种效果的技能），是否计入进度由引擎判断；
- 技能会施加哪些效果、冷却多少，从 `SkillType.Effects` 读取，不再运行时学习。

## 新模块设计

### 1. 抓取（代理，需要新构建）

现有 `capture_hydra_replay_source`（`src/agent/agent.cpp`）已经用游戏自己的 `JsonMain.ToJsonStr` 抓取 `BattleContext.Setup` 与 `GameParameters.BattleSettings`，只是限定 `active_hydra` 且 `KindId == 5`。奇美拉只需放宽到 KindId 8，其余流程（同一个共享槽、同一套 RNG 身份核对）不变。Python 侧 `hydra_replay_source.validate_replay_source` 需要增加奇美拉版本（KindId 8、5 名英雄、1 个 Boss）。

### 2. 离线模拟服务（`raid_offline_probe.exe` 新模式）

仍在零权限 AppContainer 中运行。新增 `chimera-plan` 模式，在工作进程内提供：

- 载入 Setup 并开战；按指令推进；
- 深拷贝分叉（本次研究的 `deep_clone.hpp`，正式实现时加上结构校验和整场锁步验证）；
- 用游戏 AI 或给定策略做滚动推演；
- 读取观测：回合、形态、Boss 回合数、试炼进度/计数/完成回合、英雄 HP 与效果、伤害。

规划循环放在工作进程内的 C++ 中（逐行动走管道太慢：六头蛇推演逐决策往返约 9 ms，远慢于引擎本身）。Python 只提交“目标试炼、约束、时间预算”，取回计划与预测。需要更快时可以并行启动多个隔离工作进程（每个约 0.4 GB）。

### 3. 规划（搜索）

- **目标**：先满足所选试炼（按用户选择的优先级），再保证不减员，最后最大化奇美拉伤害/竞赛积分。
- **分段**：按 `ChimeraSequenceForms` 把整场分成形态窗口（Ultimate 1–5、Ram 6–10、…、Ultimate 61–65）；每个带试炼的窗口与它前面的 Ultimate 窗口一起规划（前置增益、冷却在窗口开始时就绪）。
- **搜索**：在每个窗口内做由条件引导的束搜索。分支只在“与目标试炼条件相关”的决策上展开（施加所需减益、给出手者上所需增益、用满足条件的技能攻击），其余行动用默认策略；每个分支用克隆 + 推演到窗口结束，由引擎给出真实进度评分。选定后提交该段指令，从该点继续规划下一段。
- **提前判断**：若搜索在预算内找不到完成必选试炼的计划，在开局就报告“本场不可完成”，可以沿用现有免费重整流程换一个种子。

### 4. 执行

实战每个玩家决策前核对：回合、行动英雄、RNG 四字（代理已发布 `battleRandom`）与计划记录一致，才提交计划中的技能和目标。不一致即停止照计划出手，退回现有反应式策略；之后可以用实际已执行的指令在离线重放得到实战状态，再重新规划。

### 5. 界面

沿用现有“选择试炼”设置；新增“开局规划”卡片：每个所选试炼的预计完成 Boss 回合、预计伤害、规划耗时、执行中是否仍与计划一致。

## 实施阶段与验收

1. **抓取与一致性验证**：新代理构建抓取奇美拉 Setup；用一场实战的已执行指令离线重放，逐决策比对 RNG、回合、行动者、试炼进度，标准与六头蛇相同（全部一致）。
2. **开局预测**：用当前策略在离线跑完整场，开局显示各试炼是否会完成、何时完成、预计伤害。每场实战都会自动检验预测。
3. **规划与执行**：实现第 3、4 节；验收为多场实战中计划预测的试炼完成情况与实际一致。
4. **清理**：规划稳定后删除旧的配方/能力学习/描述推断代码。

## 进展

- 2026-09-25：抓取构建完成（预览包 `out/chimera-forecast-preview-1.0.6-20260925-r7`，代理 2026092501）。代理对奇美拉也发布本场 `BattleSetup`/`BattleSettings`（`chimera_replay_source`）、每个决策的战斗 RNG 四字和开战时选择的 5 名英雄（`chimeraStartSelection`）；控制器校验后保存到 `cache/chimera-capture/<setup>-<ms>/`，并逐决策记录回合、行动英雄、RNG、形态、试炼进度和提交的指令（`live-trace.jsonl`）。只保存从开局接管的战斗，最多保留 20 场；不影响出手。
- 对比工具 `out/chimera-sim-research-20260925/compare_chimera_capture.py` 已用合成战斗自检：659/659 个决策窗口全部一致。
- 2026-09-25 r8：抓取目录同时保存完整决策状态（`decision-states.jsonl.gz`，静态试炼目录单独存为 `decision-static.json`）、开局时的策略（`strategy.json`）与能力记忆（`capability-memory.json`）。
- 2026-09-26：第一场真实奇美拉抓取（难度 6，15 次实战指令）通过三项核对：引擎重放 15/15 窗口一致；离线 decision_state 与实战逐字段一致；离线策略选出的 15 个指令与实战相同。在此基础上实现了“策略模拟”（开局推演阶段），见 [1.0.6 奇美拉策略模拟](1.0.6-chimera-strategy-simulation.md)。

## 需要你配合的事

- ~~同意抓取构建~~（已完成）；
- 用 r7 预览包从开局接管打一两场奇美拉（任意现有策略即可），保留日志和 `cache` 下的抓取目录。有了真实 Setup 才能确认 Boss 属性修正、回合上限和实战一致性。

## 仍未知 / 风险

- 实战 Setup 中奇美拉 Boss 的属性修正、`MaxBossTurnsCount` 等字段需要抓取确认；
- 深拷贝目前只在一场合成战斗的 600 次行动内验证，正式使用前要覆盖复活、召唤、形态切换全程和整场战斗；
- 搜索质量取决于条件引导；有些试炼（如“5 个回合内耗尽对决条”）可能需要专门的分段目标；
- 游戏更新后引擎、静态数据会变化，模拟会随本机游戏文件自动更新，但一致性校验必须保留。

## 第 5 难度 27 个试炼的游戏定义

来源：静态数据 `SkillData` 中 `8000501–8000527` 的效果；中文描述来自本机游戏本地化。计数上限来自 `ChallengeParams.CounterLimit`：多数情况下计数按 Boss 回合累加，到上限时进度归零；为 1 时表示“单个技能”。条件只列出开始后判断计入进度的效果，并去掉了多数条目共有的“试炼已开始、Boss 是受影响目标、不是 Boss 自己造成”前缀；保留前缀的条目（如 8000511 护盾吸收）计入的是 Boss 自己造成的伤害。

| 试炼 | 形态/部位/难度 | 游戏描述 | 目标 | 计数上限 | 计入条件 → 进度 |
| --- | --- | --- | --- | --- | --- |
| 8000501 | Ram/Wing/Easy | 在奇美拉受【恐惧】或【真实恐惧】减益影响时对其造成伤害。仅技能造成的伤害纳入计算。 | 1,200,000 | — | `OwnerHasEffectOfKind(Fear_KindId)&&relatedSkillSource==Hero_Skill` → `+DEALT_DMG` |
| 8000502 | Ram/Wing/Normal | 使用基于敌人最大生命值造成伤害的技能，以及【中毒】、【生命值燃烧】和【重击】减益对此形态下的奇美拉在其所有5个回合里造成伤害。 | 500,000 | 6 | `reletionEffectScalesByTargetHp` → `+DEALT_DMG` |
| 8000503 | Ram/Wing/Hard | 在5个回合内耗尽奇美拉的对决条。 | 5,973,311 | 6 | `OwnerHasEffectOfKind(DuelProducerMark_KindId)` → `+DEALT_DMG` |
| 8000504 | Ram/Tail/Easy | 在你的斗士拥有【增加精准】增益时，在奇美拉的5个回合内对其施放6个不同的减益。 | 6 | 6 | `RelationProducerHasEffectOfKind(StatusIncreaseAccuracy_KindId)&&statusEffectIsApplied` → `relationKindId` |
| 8000505 | Ram/Tail/Normal | 在你的斗士拥有【增加暴击率】或【增加暴击伤害】增益时，对受【虚弱】减益影响的奇美拉造成伤害。仅技能造成的伤害纳入计算。 | 1,500,000 | — | `OwnerHasEffectOfKind(IncreaseDamageTaken_KindId)&&(RelationProducerHasEffectOfKind(StatusIncreaseCriticalChance_KindId)\|\|RelationProducerHasEffectOfKind(StatusIncreaseCriticalDamage_KindId))&&relatedSkillSource==Hero_Skill` → `+DEALT_DMG` |
| 8000506 | Ram/Tail/Hard | 在奇美拉受【虚弱】和【降低防御】减益影响时，使用单一技能对其造成伤害。反击以及基于敌人最大生命值造成伤害的技能所造成的伤害不纳入计算。 | 500,000 | 1（单个技能） | `(OwnerHasEffectOfKind(IncreaseDamageTaken_KindId)&&OwnerHasEffectOfKind(StatusReduceDefence_KindId))&&!reletionEffectScalesByTargetHp&&单技能且非反击` → `+DEALT_DMG` |
| 8000507 | Ram/Paw/Easy | 在奇美拉受【降低防御】减益影响并且你的斗士拥有【完美隐身】增益的情况下，在奇美拉的3个回合内对其造成伤害。 | 800,000 | 4 | `OwnerHasEffectOfKind(StatusReduceDefence_KindId)&&RelationProducerHasEffectOfKind(Invisible_KindId)` → `+DEALT_DMG` |
| 8000508 | Ram/Paw/Normal | 在奇美拉的3个回合内对奇美拉施放6个不同的减益，并对你的斗士施放6个不同的增益。 | 12 | 4 | `TargetCounterWithId(80005082)<6&&statusEffectIsApplied` → `relationKindId`<br>`!isOwnerProduceRelatedEffect&&!ownerIsRelatedEffectTarget&&TargetCounterWithId(80005083)<6&&statusEffectIsApplied` → `relationKindId` |
| 8000509 | Ram/Paw/Hard | 在奇美拉的5个回合里使用【阻挡减益】、【拦截】或【石肤】增益阻挡6个【眩晕】减益。 | 6 | 6 | `!relationTargetIsAlly&&relationIsControlEffect&&effectThatBlockRelation` → `HeroCounterWithId(8000509)+1` |
| 8000510 | Lion/Wing/Easy | 在奇美拉受5个或多个减益影响且你的斗士拥有【增加防御】增益时使用单一技能对其造成伤害。反击造成的伤害不纳入计算。 | 500,000 | 1（单个技能） | `RelationProducerHasEffectOfKind(StatusIncreaseDefence_KindId)&&DEBUFF_COUNT>=5&&单技能且非反击` → `+DEALT_DMG` |
| 8000511 | Lion/Wing/Normal | 使用【护盾】增益吸收伤害。溢出伤害不纳入计算。 | 150,000 | — | `isOwnerProduceRelatedEffect&&!ownerIsRelatedEffectTarget` → `+damageAbsorbedByShield` |
| 8000512 | Lion/Wing/Hard | 使用【阻挡伤害】或【不死】增益阻挡伤害。 | 350,000 | — | `isOwnerProduceRelatedEffect&&!ownerIsRelatedEffectTarget` → `+damageAbsorbedByBlockAndUnkillable` |
| 8000513 | Lion/Tail/Easy | 在你的斗士拥有5个不同增益的情况下，在奇美拉的3个回合里对其造成伤害。基于敌人最大生命值造成伤害的技能所造成的伤害不纳入计算。 | 1,200,000 | 4 | `!reletionEffectScalesByTargetHp&&RelationProducerBuffCount>=5` → `+DEALT_DMG` |
| 8000514 | Lion/Tail/Normal | 在奇美拉受【降低速度】和【降低防御】减益影响时对其造成伤害。仅技能造成的伤害纳入计算。 | 2,000,000 | — | `OwnerHasEffectOfKind(StatusReduceSpeed_KindId)&&OwnerHasEffectOfKind(StatusReduceDefence_KindId)&&relatedSkillSource==Hero_Skill` → `+DEALT_DMG` |
| 8000515 | Lion/Tail/Hard | 使用反击和组队攻击技能对奇美拉造成伤害。 | 800,000 | — | `relationIsCounterattackDamage` → `+DEALT_DMG`<br>`!relationIsCounterattackDamage&&skillCausedByEffect&&effectThatCausedSkillKindId==TeamAttack_KindId` → `+DEALT_DMG` |
| 8000516 | Lion/Paw/Easy | 在奇美拉受【血液榨取】减益影响时对其造成伤害。 | 2,500,000 | — | `OwnerHasEffectOfKind(LifeDrainOnDamage_KindId)` → `+DEALT_DMG` |
| 8000517 | Lion/Paw/Normal | 在奇美拉的3个回合内对其施放10个减益。其中一个减益必须是【降低抗性】减益。 | 10 | 4 | `OwnerHasEffectOfKind(StatusReduceResistance_KindId)` → `DEBUFF_COUNT` |
| 8000518 | Lion/Paw/Hard | 使用【反弹伤害】增益、【苦痛连接】减益或被动反弹伤害效果对奇美拉造成伤害。 | 1,000,000 | — | `ownerIsRelatedEffectTarget&&!isOwnerProduceRelatedEffect` → `+DEALT_DMG` |
| 8000519 | Viper/Wing/Easy | 在奇美拉受【阻挡主动技能】减益影响时对其造成伤害。仅技能造成的伤害纳入计算。 | 1,500,000 | — | `OwnerHasEffectOfKind(BlockActiveSkills_KindId)&&relatedSkillSource==Hero_Skill` → `+DEALT_DMG` |
| 8000520 | Viper/Wing/Normal | 在奇美拉受【阻挡增益】和【生命值燃烧】减益影响并且没有激活的增益的情况下，在奇美拉的2个回合内对其造成伤害。 | 1,000,000 | 3 | `OwnerHasEffectOfKind(BlockBuffs_KindId)&&OwnerHasEffectOfKind(AoEContinuousDamage_KindId)&&BUFF_COUNT==0` → `+DEALT_DMG` |
| 8000521 | Viper/Wing/Hard | 在奇美拉受9个或更多减益影响下时对其造成伤害。减益中不能包含【阻挡主动技能】减益。 | 1,500,000 | — | `!OwnerHasEffectOfKind(BlockActiveSkills_KindId)&&DEBUFF_COUNT>=9` → `+DEALT_DMG` |
| 8000522 | Viper/Tail/Easy | 在奇美拉受【中毒敏感度】减益影响时使用【中毒】减益对其造成伤害。 | 200,000 | — | `ownerIsRelatedEffectTarget&&OwnerHasEffectOfKind(IncreasePoisoning_KindId)` → `+DEALT_DMG` |
| 8000523 | Viper/Tail/Normal | 在3个奇美拉的回合中保证你的所有斗士存活。 | 3 | 3 | `aliveEnemiesCount>=5&&isOwnersTurn` → `HeroCounterWithId(8000523)+1` |
| 8000524 | Viper/Tail/Hard | 在复活多名斗士时成功复活1名队友或所有队友。 | 1 | — | `!relationTargetIsAlly&&!relationProducerIsAlly&&!relatedEffectCancelled` → `+1` |
| 8000525 | Viper/Paw/Easy | 在奇美拉受【降低攻击】减益影响并且你的斗士拥有【增加速度】增益的情况下，在奇美拉的3个回合内对其造成伤害。 | 1,200,000 | 4 | `OwnerHasEffectOfKind(StatusReduceAttack_KindId)&&RelationProducerHasEffectOfKind(StatusIncreaseSpeed_KindId)` → `+DEALT_DMG` |
| 8000526 | Viper/Paw/Normal | 在你的斗士拥有【增加攻击】和【阻挡减益】增益时，使用单一技能对奇美拉造成伤害。反击造成的伤害不纳入计算。 | 600,000 | 1（单个技能） | `RelationProducerHasEffectOfKind(BlockDebuff_KindId)&&RelationProducerHasEffectOfKind(StatusIncreaseAttack_KindId)&&单技能且非反击` → `+DEALT_DMG` |
| 8000527 | Viper/Paw/Hard | 在奇美拉受【降低精准】减益影响而你的斗士拥有【增加抗性】增益的情况下抵抗18个减益。 | 18 | — | `isOwnerProduceRelatedEffect&&!relationTargetIsAlly&&OwnerHasEffectOfKind(StatusReduceAccuracy_KindId)&&RelationTargetHasEffectOfKind(StatusIncreaseResistance_KindId)` → `HeroCounterWithId(8000527)+1` |
