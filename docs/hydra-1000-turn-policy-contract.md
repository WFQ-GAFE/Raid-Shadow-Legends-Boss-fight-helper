# 六头蛇原版引擎千回合回放：策略接口与验收契约

> 2026-09-25 清理说明：本文是研究阶段的历史记录。文中提到的研究期工具（历史重放 `replay-observed`、首回合投影、选择器探针、动作脚本校验等）和 `out/` 下的研究证据已删除；离线引擎现在只保留 `json-convert` 与 `forecast` 两种模式，验证基线与现行实现见 [1.0.6 开局吞噬顺序推演](1.0.6-hydra-devour-forecast.md)。

状态（2026-09-25 更新）：策略驱动的 `forecast` 模式已实现（`src/offline_runtime/policy_state.hpp`、`policy_channel.hpp`，`tools/hydra_forecast.py`），并接入控制器（`tools/hydra_forecast_live.py`，默认关闭）。在同一场 gafee 样本上，仅凭开局输入与策略快照推演到第 1000 回合：自主选出的 233 次玩家指令与实战一致，74 个 RNG 检查点一致，三次可见标记一致；74 个实战原生快照与离线 `decision_state` 逐字段 0 差异。上表的 `setupLoaded`、`settingsLoaded`、`policyAppliedEveryPlayerTurn`、`historicalActionParity`、`historicalMarkParity`、`rngTraceCompared`（74 个检查点）在该样本上成立；`futurePredictionVerified` 仍待一场新战斗在标记发生前保存预测后验证。实现与验证细节见 [1.0.6 开局吞噬顺序推演](1.0.6-hydra-devour-forecast.md)。以下为原契约文本。

原状态（2026-09-25）：已用一场保存的真实战斗做离线历史重放与抽样核对，仍不能预测新开局的未来标记。本文的“通过”只描述未来实现必须达到的条件。

这场 gafee 样本的原版引擎重放到了 `CurrentTurn=306`，按历史记录执行 233 次玩家指令。233 次的回合、行动英雄、技能、目标均相符；前 74 次另有实战行动前 RNG 四字核对，第 75 次起的 159 次没有该检查点。三次实战日志可见的标记变化（回合 1、155、291，actor 5、1、3）与离线重放相符。重放仍使用记录下来的玩家指令，并对玩家和敌方命令的 `SetOnCooldown=true` 作研究假设；没有从离线状态自动生成玩家决策，也没有实测完整效果事件流或预测新战斗。因此不得把 233 次指令吻合或三次标记吻合展示成“已能预测吞噬顺序”。

首个玩家回合的只读状态投影也已与同场实战快照核对：819 个已覆盖字段一致；同一状态的原版短键 JSON 有 560 个可比字段一致。投影不包含与客户端同义的顶层技能槽位和有序合法目标，不能直接喂给策略生成后续指令。未来每回合均需做同样核对，而不能把首回合成功外推至第 1000 回合。

## 计算范围

“1000 回合”以原版 `BattleState.CurrentTurn` 为准，不用 Python 策略调用次数、`ApplyCommand` 次数或共享内存发布序号代替。离线引擎从原始开战输入运行，直到原版结算，或者 `CurrentTurn` 达到 1000。如果战斗提前结算，输出完整结算结果及结束回合；如果到 1000 仍未结算，输出明确的 `turn_limit`，不能称为完整战斗结果。每个候选开局状态必须独立新建运行库战斗，不能在两次尝试之间沿用随机流或策略记忆。

六头蛇标记序列是 `HungerCounter` 的**应用事件**，从开局第一枚开始计数。使用效果实例 ID、目标 actor ID、应用回合与事件顺序去重；死亡后改选属于新应用。不能用吞噬事件或间隔快照代替，因为被标记英雄可能在实际吞噬前死亡，或在两次快照之间消失并重新获标。

## 开局输入与场次绑定

每次回放需要同一局的完整 `BattleSetup`、`BattleSettings`、静态数据版本和原版引擎二进制版本。`BattleSetup` 必须包含六个具体英雄实例及其装备、技能等级、精通、祝福和战斗修正；`BattleSettings` 包含 `WarmupBattleRandomCount` 等会改变开局 RNG 位置的设置。把类型 ID、种子和默认装备拼成的合成队伍只能用来测引擎接口，不能作为用户账号预测。

输入清单至少记录以下 SHA-256 与身份：`GameAssembly.dll`、IL2CPP 元数据、静态数据、`battle-setup.msgpack`、`battle-settings.msgpack`、策略快照、技能能力缓存、场次 Setup ID、种子、游戏引擎版本及六个英雄实例 ID。缺少任一实际开局输入、哈希不匹配或场次/队伍不一致时，结果为 `unknown`。新的安全版代理已停用导致游戏崩溃的实时 MessagePack 采集；旧研究构建可能留下的输入不能自动视为这局数据。

## 每次玩家决策的离线策略状态

隔离的原版 `BattleProcessor` 每到玩家可操作时刻，应导出与工具 `decision_state` 同义的策略快照，再调用原有 `chimera_controller.evaluate`。最低字段与语义如下：

| 范围 | 必需数据 | 缺少时的处理 |
| --- | --- | --- |
| 回合与当前英雄 | `battle.round/turn/playerTurnCount/hydraBattle/finished/waitingForManualCommand`；`activeHeroId/activeHeroTypeId/activeHeroTurnCount/activeHeroFormIndex` | 拒绝本步，不能选默认技能补位 |
| 当前英雄技能 | 每个可见主动技能的 `skillId/slot/typeId/ready/passive/blocked/cooldown/validTargetIds`；合法目标保留原版返回顺序 | 不能决定技能或目标，拒绝本步 |
| 己方六名英雄 | actor ID、英雄类型和实例 ID、队伍位置、存活/生命、当前形态、效果列表与状态、冷却 | 无法正确计算规则及救出/死亡变化，拒绝本步 |
| 场上全部蛇头 | actor ID、类型、存活/生命、断颈、吞噬/消化目标、效果列表与状态；新生蛇头要及时出现 | 不能复现 `devouringHead`、`exposedNeck` 和 `hydraHeadPriority` 目标，拒绝本步 |
| 效果 | 效果实例 ID、类型/种类、施加者、来源技能、应用回合、剩余回合；保留同类型的多个实例 | 无法复现效果条件和标记事件，拒绝本步 |
| 战斗状态 | `hydra.turnCount`、当前 RNG 四个 32 位状态字、随机种子与场次 Setup ID | 无法校验共享随机流和场次，拒绝预测 |

这些字段是必要条件，实际规则可能需要更多字段。适配器应按**当前策略快照实际引用的条件、目标选择器和自动动作**逐项覆盖；遇到未实现的字段不能把它当作 false 或空列表。历史 gafee 策略有 17 条规则（11 条 `cast`、6 条 `defaultSkillPriority`），实际依赖效果条件树、技能冷却，以及 `exposedNeck`、`devouringHead`、`hydraHeadPriority` 等目标，因此只输出技能类型和 RNG 的引擎摘要远远不够。

策略记忆是战斗状态的一部分。离线会话从与本次策略版本绑定的 `SkillCapabilityMemory` 快照初始化，并在每一步保留同一个对象和 `runtime_state`；每场独立重置。不得每回合新建，也不得读取预测运行期间被真实控制器修改的缓存。规则返回无动作、目标不合法、技能不在当前可用列表、或快照版本不支持时，标记 `unknown` 并停止这次预测。禁止静默改用 `GetDefaultSkill`，因为多消耗一个随机数就可能改变后续标记。

隔离引擎应把策略所选 `skillTypeId` 与当前英雄的原版技能对象、所选 `targetId` 与原版 `BattleHero` 对应，按原版输入路径构造 `SkillCommand.FromInput`、`BattleCommand`，再调用 `ApplyCommand`。敌方和被动动作仍由原版处理器执行。每步至少记录：原版 `CurrentTurn`、英雄 actor/实例、技能类型与技能位、目标 actor、RNG 前后四字、策略规则 ID、结果状态摘要；战斗结束记录原版 `BattleResult`。

## 首次分歧与验证门槛

回放报告必须分别表示 `setupLoaded`、`settingsLoaded`、`policyAppliedEveryPlayerTurn`、`completeMarkStream`、`rngTraceCompared`、`historicalActionParity`、`historicalMarkParity`、`futurePredictionVerified`，以及终止原因。不能由某个单项成功推断其他项成功。特别是旧报告中 239 次策略动作与历史快照一致，只证明控制器决策可重现；它不证明原版引擎走到相同状态。

真实战斗对照时，从开局第一个可操作节点逐步核对原版回放与真实记录：回合、行动英雄、技能、目标、行动前 RNG、行动后 RNG、标记应用事件和关键状态。出现第一个不一致，报告其索引、双方数值、该步输入哈希和上一致的 RNG 状态；之后的预测全部失效，不继续给出“前 N 次通过”。历史采集若缺某一步或事件流，则是 `unknown`，不能宣称直到该步完全一致。

只有在同版本真实样本上完成逐步核对、能在标记发生**之前**保存预测并逐次与实际标记验证后，才可对新的开局运行“前 N 次不能标记指定英雄”。新局仍须具备完整开局输入、相同队伍及策略、完整 N 次标记结果。N 次内命中禁止英雄才是不合格；已验证且 N 次均未命中才是合格；未运行到第 N 次、达到 1000 回合上限、提前结算且不足 N 次、数据缺失或任何分歧均为 `unknown`。`unknown` 不能触发自动重整。

## 现有实现对应位置

- `tools/verify_hydra_policy_replay.py`：历史快照与已提交指令的策略一致性核对；它不是未来模拟器。
- `tools/verify_hydra_saved_policy.py`：用这场战斗已记录的原生决策快照逐步运行保存的策略。策略快照已与两份哈希校验过的控制器原日志开局记录核对；前 74 个有 RNG 的玩家决策均选出与历史指令相同的技能位、技能类型和目标。能力缓存从新的空实例开始，不等同于当时的实时缓存。此核对仍使用现场快照，不证明引擎能自行投影策略输入。
- `tools/build_hydra_observed_action_tsv.py`：把同局历史指令及其已有的行动前 RNG 检查点整理给隔离重放；不会补造未知的行动后 RNG 或冷却字段。
- `tools/validate_hydra_action_script.py`：离线检查 `recorded-actions.tsv` 的格式、严格动作顺序和策略报告场次绑定；若有真实开局输入，还核对每步 RNG 的场次 ID/种子及两份 MessagePack 文件的 SHA-256。只有 `readyForNativeHistoricalPlayback=true` 才输出供原版离线引擎消费的规范化 `actions`。该字段只允许开始历史逐步比对，报告中的 `completeGameActionStreamVerified` 和 `thousandTurnForecastVerified` 仍为 false。后续引擎读取时必须再次核对报告给出的动作文件和输入文件哈希，避免预检后文件变化。
- `src/offline_runtime/battle_probe.hpp`：在隔离原版引擎中装入 `BattleSetup`/`BattleSettings`；当前 `replay` 已输出每个已执行命令的目标与行动前后 RNG，但玩家仍调用 `GetDefaultSkill`，缺完整策略快照与实际策略命令，因此不能据此预测真实战斗。
- `src/offline_runtime/battle_probe.hpp` 的 `replay-observed`：按历史玩家指令重放，并在缺失下一次实战 RNG 前停下；`replay-observed-weak` 明示之后的指令没有 RNG 锚点，只做行动字段与抽样标记核对。两者均不自主选择玩家技能。`tools/compare_hydra_engine_trace.py` 与 `tools/compare_hydra_historical_marks.py` 独立核对结果并保留证据缺口。
- `src/offline_runtime/decision_projection.hpp`：从隔离的原版对象只读导出首回合的已验证状态字段；`tools/compare_hydra_decision_projection.py` 先核对场次身份，再逐字段比对原版 JSON 和实战决策快照。尚无完整的每回合策略状态投影。
- `tools/chimera_controller.py`：实际工具的列表策略决策器及跨回合记忆。离线驱动应复用它，而非另写一套近似规则。
- `offline_sim/hydra_mark.py`：给定某次选择时刻的候选人和 RNG，验证单次选择算术；不负责整场战斗事件推进。

所有开发和核对都在磁盘副本及隔离子进程进行。取得下一份真实开局数据的方法需要单独验证稳定性；不能恢复已确认导致游戏崩溃的游戏内 MessagePack/GC 采集路径。
