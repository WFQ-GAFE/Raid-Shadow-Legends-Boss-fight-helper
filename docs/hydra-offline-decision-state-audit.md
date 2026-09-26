# 六头蛇离线决策状态适配：字段审计

> 2026-09-25 清理说明：本文是研究阶段的历史记录。文中提到的研究期工具（历史重放 `replay-observed`、首回合投影、选择器探针、动作脚本校验等）和 `out/` 下的研究证据已删除；离线引擎现在只保留 `json-convert` 与 `forecast` 两种模式，验证基线与现行实现见 [1.0.6 开局吞噬顺序推演](1.0.6-hydra-devour-forecast.md)。

状态（2026-09-25）：原版 `JsonMain.ToJsonStr(BattleState, false)` 能保存一个短键战斗检查点，但**不能单独变成**工具的 `decision_state`。已在隔离原版引擎首个玩家回合直接读取所需原版对象，输出保守的只读投影。真实 gafee 同场样本中，该投影与原版短键 JSON 的 560 个可比字段一致，与工具原生决策快照的 819 个可比字段一致，均为 0 分歧、0 缺失。比较先要求同一 Setup ID、种子、战斗类型、回合及行动英雄。缺失的客户端派生字段仍未输出；不能据首回合核对宣称完整策略输入或未来预测。

依据：`src/offline_runtime/state_json_probe.hpp` 的原版 JSON、`src/offline_runtime/decision_projection.hpp` 的只读投影、`src/agent/agent.cpp` 的 `capture_decision_state`、`tools/compare_hydra_decision_projection.py` 的同场核对，以及同版本 IL2CPP 元数据中 `BattleState`、`BattleHero`、`HeroState`、`BattleSkill`、`AppliedEffect` 的 `[Json]`/`[JsonSkip]` 声明。通过的是首个玩家行动窗口的已列字段；后续窗口、目标合法性与策略动作尚未逐字段验收。

| 工具决策字段 | 原版 JSON 中的依据 | 严格适配所需补充 |
| --- | --- | --- |
| RNG 四字、当前回合、玩家行动次数、结束状态 | 根 `x.{x,y,z,w}`、`t`、`pt`、`b` | 同时绑定本局 Setup ID 和种子；每步对照真实 RNG。 |
| 当前轮数、战斗种类、区域/关卡、自动模式、额外回合 | `CurrentRound`、`IsAutoBattleMode`、`IsExtraTurn` 标为 `JsonSkip`；`_battleKindId` 未序列化 | 从当前 `BattleState`、`BattleContext`、`BattleSetup` 原版字段或方法读取，不能由回合数或队伍形状推算。 |
| 可提交玩家指令的时刻、是否等待手动指令 | JSON 没有对应的命令生成器状态 | 离线驱动应在原版 `BattleProcessor` 返回玩家命令请求时建立决策点，并明确校验可提交条件。 |
| 当前英雄 ID、类型、库存实例、队伍槽、形态 | 根 `a` 的 `i`、`t`、`u`、`d`、`f` | 校验 `a.i` 也存在于原版玩家队伍；英雄回合计数、技能更新计数、是否神话英雄未序列化，须读原版对象。 |
| 六名队员与当前蛇头 | `f.h`、`s.h` 中的英雄列表；每项 `i/t/u/d/f/h/z` | 以原版 `PlayerTeam`/`EnemyTeam` 确认队伍方向；读实时存活的英雄对象，不能把已替换的蛇头或列表残留当当前目标。按库存实例 ID 与实时 actor ID 建立映射。 |
| 生命比例、死亡和控制状态 | `h` 是原版 `Fixed` 生命原值，`z.d/t/f/s/p/b` 包含死亡、晕眩、冰冻、睡眠、挑衅、主动技能封锁等标志 | 最大生命和动态状态由原版 `BattleHero`/`HeroState` 读取；不可用基础生命或把缺失值当零。 |
| 效果实例与次数 | `z.e` 中 `u/p/s/e/t/l/c` 为实例 ID、施加者、技能类型、效果类型、应用回合、持续与剩余回合 | 从当前同版静态 `EffectType` 取得 effect kind/群组/名称，保留同类型的多个实例；校验头部和英雄的实际效果集合。 |
| 蛇头断颈、吞噬、消化对象及回合数 | 某些状态或消化效果在 `z`/效果中存在，但没有与工具 `headState/isHydraNeck/isDevouring/devouredHeroId/digestionTurns` 等价的完整直接 JSON 字段 | 调原版 `BattleHero` 的蛇头/断颈/正在消化方法及消化状态，逐字段对照现场快照；不得从蛇头类型 ID 单独判定断颈。 |
| 当前英雄可见技能、槽位、冷却、主动/被动、封锁与 `ready` | `a.k` 中 `t/c/m/d/l/i` 等保存技能类型和部分状态 | 工具当前使用 HUD `SkillData.Id + 1` 作为槽位，原版 `BattleSkill` JSON 不保证相同次序；从原版技能对象、英雄形态、静态技能以及对应 HUD 逻辑生成并验证目录。`Blocked`、`IsActive`、`IsReady` 等是 `JsonSkip` 的派生属性。 |
| 每个技能有序合法目标 `validTargetIds` | JSON 完全没有 | 离线调用原版目标选择/合法性路径并保留顺序。现场路径是 `ClientBattleMode.GetAcceptableTargets(SkillData)`；离线可研究 `SkillTargetSelector.GetTargets`，但只有实战逐技能与现场结果一致后才视为等价。缺目标列表不得让策略选技能。 |
| 伤害、当前积分及策略使用的其他动态指标 | JSON 根 `o` 是原版 `ChimeraCompetitionPoints` 的 `LongFixed` 值，但尚未证明与工具的 `currentCompetitionPoints` 同义；没有工具同义的 `currentDamage` | 从原版战斗上下文或相同计算方法提取、核对单位与语义，并仅在实际策略引用时强制要求；不能用显示层近似值替代。 |

建议接口是隔离原版运行库在**每次玩家命令请求**时输出一个显式版本的决策投影，而非让 Python 解读短键 JSON 后补字段。投影应带 Setup ID、种子、静态数据哈希、当前 `CurrentTurn`/`PlayerTurnCount`、RNG 四字、原版 actor/库存实例映射，以及每个字段的读取成功标志。技能和有序目标必须与将要提交给 `SkillCommand.FromInput` 的同一英雄、同一技能对象绑定。Python 端只做结构校验、策略调用及结果核对；遇到缺字段、重复 ID、非法目标、未覆盖的策略条件或状态哈希不一致，输出 `unknown` 并停止该次预测。

第一道验收应选历史战斗中连续的玩家决策点，逐项比较离线投影和现场 `decision_state`：当前英雄、形态、每个技能的类型/槽/可用性/目标顺序、队伍及蛇头、效果实例、RNG，以及工具选中的技能和目标。只有从开局至所需第 N 次标记都没有首次分歧，才允许计算“前 N 次不标记指定英雄”。现有短键 JSON 样本和短程 `default-skill` smoke test 不满足这个条件。
