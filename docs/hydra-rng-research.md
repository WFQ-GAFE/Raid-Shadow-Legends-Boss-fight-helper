# 六头蛇标记与随机状态：本机离线核对

> 2026-09-25 清理说明：本文是研究阶段的历史记录。文中提到的研究期工具（历史重放 `replay-observed`、首回合投影、选择器探针、动作脚本校验等）和 `out/` 下的研究证据已删除；离线引擎现在只保留 `json-convert` 与 `forecast` 两种模式，验证基线与现行实现见 [1.0.6 开局吞噬顺序推演](1.0.6-hydra-devour-forecast.md)。

日期：2026-09-21。目标仍是 1.0.6 的开局离线预测及“前 N 次标记排除指定英雄”；下面的单次计算不是完整功能。

## 直接检查的版本

- 本机 `GameAssembly.dll` SHA256：`d6c57a399a95c5c15048b5592577cfc7447f6a63b2d7675b2be01a4fdd5aebda`。
- Unity 6000.3.18f1，IL2CPP 元数据版本 39。
- 使用官方 Cpp2IL 2022.1.0-pre-release.21 读取磁盘文件，没有加载游戏 DLL 或连接正在运行的游戏。
- 符号、带地址的反汇编、方法映射、工具来源及数值验证报告保存在 `out/hydra-research-20260921`。这些是本机研究材料，不随应用打包或上传游戏二进制。

## 已由方法体确认

1. `BattleContext.GetRandom()` 直接返回 `BattleContext.State.Random`。
2. `FixedRandom` 的状态为四个无符号 32 位整数 `_x/_y/_z/_w`。重置时 x 为 seed 与 `0xD523648A` 的 32 位乘积；其余三个初值为 842502087、3579807591、273326509。
3. 每次推进为 xorshift128 的移位组合 11、19、8。整数输出清除最高位；有界整数取模；`NextFixed()` 的 Q32.32 原始值是该整数乘 2。
4. `PlaceHungerCounterProcessor.Process()` 排除 `IsDeadOrAbsent` 英雄，并按 `IterationsBetweenDevouring` 排除最近被救出的可用英雄。只有一名候选人时直接选中，不消耗抽取随机数。若全被最近救出名单排除，则恢复该名单末尾、最早救出的那一名。
5. 首次处理的判定查询战斗统计中是否已有当前效果被处理的记录，并非查看当前有没有存活英雄带标记。首次在候选人中选择当前暴击伤害最大者；只有并列时随机选一名。
6. 后续选择使用当前 `BattleHero.Stats.CriticalDamage`，不是账号面板或基础属性。权重为 `10 + Round((暴击伤害比例 × 100 − 50) × 0.2)`，其中每一步按游戏 Q32.32 数值运算处理。0.2 的原始值是 858993459，Round 是中点取偶；不能改成普通浮点运算后认为结果完全相同。
7. 权重除以总权重后，`TakeRandom` 调用一次同一个 `FixedRandom.NextFixed()`，按队伍候选顺序比较 `(累计下界, 累计上界]`。零抽样或累计边界未命中时原实现走异常分支；离线模块保留无法判断，不硬塞给最后一人。
8. `DamageCalculator.CalculateHitType()` 也从 `EffectContext.BattleContext.State.Random` 调用 `NextFixed()`。标记并非只消耗一个互不相关的专用随机流。不同技能/目标/状态可能改变中间调用和后续结果。

关键方法虚拟地址（仅对应上述磁盘版本，生产采集不硬编码这些地址）：

| 方法 | 地址 |
| --- | --- |
| BattleContext.GetRandom | 0x1818E4BD0 |
| FixedRandom.Reinitialise | 0x182C86D70 |
| FixedRandom.Next | 0x182C86A60 |
| FixedRandom.NextFixed | 0x182C868E0 |
| PlaceHungerCounterProcessor.Process | 0x182D8B980 |
| RandomHungerVictimSelectedFrom | 0x182D8BC20 |
| Weight | 0x182D8CCA0 |
| TakeRandom(BattleHero, Fixed) | 0x182D706F0 |

不能把“每次选人依赖随机数”推断成“开局已有固定目标队列”。实际选择方法同时依赖战斗状态、救出历史、当前属性及共享 RNG。现有证据支持按当前策略完整推演；不支持从开局状态仅连续抽取 N 个名字。

## 本轮实现与验证

- `offline_sim/hydra_mark.py`：不可变 RNG、精确定点运算、给定时刻候选选择、前 N 次排除条件。没有游戏控制或进程访问接口。
- 通过 Unicorn 2.1.4 的独立 CPU 模拟内存执行本机磁盘中的原始算术方法，对照 Python 实现。该测试不装载 DLL、不调用真实游戏进程、不推进真实战斗。
- 对照通过：6 个种子初始化；整数、有界整数和定点随机数各 1024 组（同时检查四个后状态）；权重 1031 组；Round 边界 55 组；定点除法和乘法各 1024 组。另有 6 个种子各 8 步完整随机序列，已保存为独立回归向量。
- 随后在隔离运行库中执行了原版 `RandomHungerVictimSelectedFrom`，24 组人工状态的目标和 RNG 后状态与离线实现全部一致，包含首次唯一最高值、首次并列、后续加权，以及历史记录属于候选人之外的英雄。这验证了该选人方法，不含其调用前的候选过滤，也不能替代新战斗预测验证。
- 新增原生只读 RNG 记录：反射读取字段，连续两次一致才输出有效，最多尝试三次。缺字段、持续变化、全零状态都明确无效。种子未知与种子 0 分开处理。双读稳定不等于整个战斗快照原子一致。
- 新增英雄当前八项战斗属性原始值，使用十进制字符串保存 Q32.32，避免前端数字转换损失精度。读取失败输出 null。
- 普通决策与终止记录均可保留随机状态；记录器和分析器只在字段完整、回合对应时报告 `rngCaptured`，永远不会据此自动宣称预测完成。
- 研究采集 DLL 只编译到 `out/agent-hydra-research-20260921/Release`，没有加载进现有游戏或替换发布 EXE。

## 精确选人记录与离线核对

- 早期研究构建曾使用 `RAID_HYDRA_RESEARCH_CAPTURE` 启用实时选择器观察，并通过运行时元数据检查方法签名；候选顺序、暴击伤害、RNG 前后状态、处理历史和最终选中英雄只在旧采集数据中有效。该游戏内研究路径已退役，当前只能使用离线运行库和历史采集文件。
- 观察失败不会取消或重试实际方法：游戏原调用只执行一次，其异常照常传播。保留最近 16 条、最多 24 KB 的配对记录；结算快照最多携带最近 4 条，同时明确丢弃和窗口省略数量。
- 历史扫描有数量和时间上限；字典读取跳过任何项，都不能据此认定“没有历史”。读到匹配记录可确认非首次，完整读取且没有匹配才确认首次，否则明确未知。
- RNG 双读还包含 `BattleSetup.Id` 原始 16 字节。这个场次标识解决开局标记发生时工具本地 generation 尚未更新的归属问题；不是随机种子或未来目标队列。
- `offline_sim/hydra_trace.py` 核对目标和四个 RNG 后状态，拒绝上下文变化、读数不完整、精度损失及同 ID 不同内容的记录。通过独立战斗快照绑定 PID、代理实例、场次标识/旧 generation、模型对象、种子、时间和回合；历史中的上一场记录不能自动算进当前样本。
- `tools/analyze_hydra_capture.py` 已接入该核对。524 个旧快照重新分析后仍没有 RNG 或配对选人记录；不会把旧数据标为验证成功。
- 单候选和无候选路径不调用此选择器，必须另补上层 `Process` 的完整标记记录。当前报告始终保留 `completeMarkStream=false`、`futurePredictionVerified=false`。

## 独立运行原版战斗库的进展

`src/offline_runtime` 是独立研究程序，不是游戏内代理。它将本机 `GameAssembly.dll`、`baselib.dll` 和元数据的副本放在 `out/hydra-offline-runtime-20260921/Release`，只在 Windows AppContainer 子进程中加载。

子进程必须确认自己处于 AppContainer、能力数为 0，且读取测试主进程内存被拒绝，才会加载运行库。父进程为临时 profile 授予副本目录读取/执行权限，使用一个活动子进程、2 GB 内存及 30 秒时间限制；退出后恢复目录权限并清理 profile。没有授予网络能力；该隔离模型参考 [Microsoft AppContainer 文档](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)。没有连接、修改或重新加载用户正在运行的 RAID。

实测已完成：

1. 原版 IL2CPP 在隔离子进程中初始化成功，域可用，读取到 109 个程序集。
2. 原版 `FixedRandom` 实际运行 8 步，所有输出及四个后状态与之前独立 CPU 模拟的向量一致。
3. 配置原版日志适配器后，构造人工英雄、属性及统计历史，调用原版选人方法；24 组结果与离线模块逐项一致。结果保存在 `out/hydra-offline-runtime-20260921/verification.json`，人工回归向量保存在 `offline_sim/tests/hydra_selector_vectors.json`。

当前已进一步在隔离子进程中调用原版 `BattleProcessor`：用静态 Hydra 关卡配置合成 Normal 六头蛇队伍，执行普通 `SkillCommand`，一直推进到原版结算。状态中的 `AppliedEffects` 也能读出 `HungerCounter` 的应用回合、目标 actor、英雄类型和实例位。固定 seed=42 的样例在默认 Kael 队伍中执行 11 条指令并正常进入 Defeat 结算；另用 0、1、43、99 确认不同种子会改变后续事件数量。对固定 seed 的独立子进程重放结果一致，种子、标记事件、每条指令及 RNG 后状态逐项相同。合成 `BattleSetup` 也通过原版压缩 MessagePack 序列化和反序列化，再送入处理器。

这证明“原版处理器可在本机离线跑完一场合成 Hydra 战斗”，还没有证明真实账号预测：合成英雄没有真实装备、祝福、精通、用户战斗设置，指令使用 `GetDefaultSkill`，不是当前 Raid Chimera 工具的策略。使用上次已保存的六个英雄类型、但仍无真实装备配置的试跑在 21–27 回合结束，首个标记一致，样本过早结束，不能覆盖所需 N 次。输入来源也需绑定到对应游戏版本；当前本地静态缓存生成于旧战斗样本之后，不能冒充那场战斗的静态版本。

静态缓存现可用游戏原本的 `MessagePack.Init` 和 `FromPackedMessagePack<ClientStaticData>` 读取，并通过 `SharedModelManager.SetStaticData` 完成索引与公式缓存初始化。新增 `replay` 离线入口，读取用户采集到的 `battle-setup.msgpack` 和 `battle-settings.msgpack`，使用原版 MessagePack 反序列化并设置本地 `GameParameters.BattleSettings`。当前缓存包含 8,427 个英雄、5,679 个技能和 2,900 个关卡。该缓存只留在被忽略的 `out` 下，不进入版本控制。

新 `replay` 入口尚未用真实捕获文件验证。它目前仍用默认技能请求逐步驱动处理器，不会执行 Raid Chimera 工具的外部策略。下一步需要加入并验证该策略的离线决策接口，再把真实开战 `BattleSetup`、本局正确 `GameParameters.BattleSettings`、每个英雄完整技能/装备/精通/属性交给同一个离线引擎，核对从开局状态推进的标记序列。缺少其中任一项仍必须返回无法判断。

另外两项直接来自方法体的约束：

- `StartBattle` 在开始阶段处理前，按 `BattleSettings.WarmupBattleRandomCount` 推进 RNG；直接从 seed 的第一个输出开始会错位。
- `BattleContext` 的 MessagePack 格式包含 Setup、State、Statistics；State 的格式包含四字 RNG。但 CurrentRound、IsAutoBattleMode、IsPlayerTeamFirst、PhaseOnTurnId、IsExtraTurn 等运行字段未保存。带 State 的 `BattleProcessor` 构造函数又会新建统计对象。因此不能仅反序列化 Context 或把 State 传入构造函数，就假定中途战斗及救出历史已完整恢复。

## 仍需完成的关键工作

### 已记录战斗的策略决策回放（2026-09-23）

新增的 `tools/verify_hydra_policy_replay.py` 从两份重叠的旧采集目录中绑定当时的完整策略快照、原生战斗状态及控制器决策，再用现有策略判断器重新计算。去重后 239 个已提交动作全部复现相同技能与目标，0 个动作未配对，0 个策略分歧。结果记录在 `out/hydra-observation-20260920-2352-gafee-detail/strategy-replay-validation.json`，采集总分析中也包含该校验。

该报告现在另含规范化的 `recordedActionTrace`：每条提交命令绑定战斗回合、行动英雄、零基技能 ID/技能类型、目标、前置状态摘要及可选 RNG 快照；同时验证技能当时就绪且目标在合法目标集合。旧样本的 239 条动作都满足策略一致和合法性检查，并另导出 `recorded-actions.tsv`，供后续原版处理器命令回放接口使用。该 TSV 只含控制器已提交的玩家命令，不是完整内部战斗事件流；分析器也显式报告尚未验证处理器回放。旧采集没有开战 MessagePack，也没有任何提交前 RNG 状态，所以仍不可执行真实种子的回放。

此项补上的是“策略规则面对记录状态时会选什么”的可重复检查，不是引擎回放。报告仍明确标记 `battleProcessorReplayVerified=false`、`rngCallTraceVerified=false`、`futureMarkPredictionVerified=false`。旧样本没有初始随机状态、完整开战输入或选择器配对轨迹，故不能据此算出下一次战斗的完整吞噬顺序。

1. 真实战斗验证新增的选择器配对记录；补上 `Process` 的候选过滤、最近救出历史、效果参数和单候选路径。现有普通快照不能替代内部事件。
2. 取得可离线复原的开局战斗输入：英雄实例与技能/装备/被动、Boss 参数、速度/行动条、效果与计数器、共享 RNG 状态及策略版本。
3. 复现并验证到所需第 N 次标记之前的所有相关行动和随机调用。战斗统计历史也是选人输入，普通 `BattleStateSnapshot` 不含这些全部信息，且不含 RNG。
4. 在新战斗中先保存预测，再等待真实事件逐项核对；中途改规则/手动操作导致轨迹改变时使旧预测失效。
5. 接入简洁的开关、N、禁止英雄多选和正常免费重整。预测缺失/覆盖不足/有分歧时显示无法判断，不误放行或无限重试。重整后恢复并核验保存的具体英雄实例。

用户已指定的“前 N 次”包含初始标记，以标记应用次数计数；标记后死亡仍计数，再次标记同一英雄也计数，不用实际吞噬次数替代。

## 2026-09-24 实机崩溃与安全调整

最初两次崩溃发生在启用吞噬选择器拦截的研究代理中，故障均为 `GameAssembly.dll+0x28E240`。选择器 detour 已从可用构建路径退役，但 1.0.6 安全预览仍误开了 `RAID_HYDRA_RESEARCH_CAPTURE`，因此只关闭了其中一条研究路径。

第三份转储（PID 44424，01:03:49）确认加载的是安全预览包内的代理，仍在同一 `GameAssembly.dll+0x28E240` 发生读访问冲突。为这份转储按精确代理机器码重新生成并核对了链接映射后，异常线程可还原为 `hook_get_acceptable_targets` → `capture_decision_state` → `capture_hydra_replay_input` → `pack_with_game_messagepack` → `GcRoot` → 游戏 `il2cpp_gchandle_get_target`。原始转储保留在 `C:\Users\12908\AppData\Local\CrashDumps\Raid.exe.44424.dmp`，事件记录保存在 `out/hydra-observation-20260923-2359-gafee-preflight/incident-review.json`。

离线进一步确认了该访问冲突的直接原因：代理当时把 `il2cpp_gchandle_new` 的返回值和 `il2cpp_gchandle_get_target/free` 的参数声明为 32 位，实际游戏导出按 64 位指针使用句柄。磁盘 `GameAssembly.dll` 的 `get_target` 导出跳入 `+0x28E210`，先把完整 `RCX` 复制到 `RSI`，按 8 KiB 对齐后在 `+0x28E240` 读取 `[RSI+0x20]`。转储中 `RAX=0x1DC2A1EB598`，但传入的 `RCX=0x2A1EB598` 恰为其低 32 位，故实际读取无效地址 `0x2A1EA020`。离线运行库此前一直使用 64 位 `uintptr_t`，与此吻合。代理源码中的三个 GC 句柄签名和句柄成员现已统一改为 `uintptr_t`；实时研究采集仍被构建和打包关卡禁止，不能因为修正了类型就直接恢复实机采集。本修正尚未做新的游戏内验证。

现在 `RAID_HYDRA_RESEARCH_CAPTURE` 与 `RAID_HYDRA_SELECTOR_HOOK` 都已退役。启用任一 CMake 选项都会使构建失败；原生源文件含编译期保护；桌面打包还会拒绝 CMake 缓存开启或二进制包含研究采集标记的代理。离线输入回放和原版处理器模拟仍在独立进程中运行，不需要从游戏进程调用 MessagePack/GC 句柄采集。新代理 build ID 为 `2026092402`，旧代理不能被当作兼容版本在线重载；旧版或状态无法读取时，控制器会要求彻底重启 Raid 客户端。

本次只在离线环境编译和检查修复包，没有重新启动或控制 Raid。后续实机验证必须先关闭所有旧 Raid 客户端，再用新包从全新进程启动；旧版 1.0.6 包不应再用于战斗测试。

## 2026-09-24 本地开局输入来源审查

磁盘版游戏元数据和方法体显示 `CreateAllianceBossBattleCmd` 继承 `CreateBattleCmd<TRequest>`，而后者在正常编辑与完整用户刷新路径都会调用 `BattleSetupsCache.CacheOnDevice`。该缓存把 `List<BattleSetup>` 序列化为 JSON、压缩，写到 `Application.DeprecatedSettings.Local`；本机 `LocalSettings` 实现直接调用 Unity `PlayerPrefs`。缓存键以 `BattleSetupCache_{0}` 为基础，并可能加环境前缀。游戏的 `FinishBattleCmd` 与 `CancelHydraBattleCmd` 又会调用 `BattleSetupsCache.Clear`，说明该值主要供未完成战斗恢复使用，正常结算或取消后会被清除。因此，用户已经退出的旧战斗不应期待仍有可用缓存；只有开战后、清除前确实存在的值才可能在游戏外解压并按场次 ID、种子和英雄实例核对。

当前只读检查没有找到可用的该值：`HKCU\Software\Plarium` 及 `HKCU\Software` 下除 Microsoft/Classes 外的值名没有 `BattleSetupCache`；本机 `raidV2.db` 的唯一 Dictionary 键为 `UserId`。`battle-results/battleResults` 当前是 LZ4 包裹的空 MessagePack 数组；`workers-serialization/serialization` 解压后只有 47 字节的 MessagePack 映射，容纳不了完整 `BattleSetup`。三份约 127 MB 的崩溃转储都不是全内存转储，PID 44424 的异常栈所指向的多个托管对象地址不在转储的可读范围内，不能据此可靠恢复完整 `BattleSetup`/`BattleSettings` 对象图。现有磁盘静态缓存可供离线引擎加载关卡、技能和英雄类型，但不含当局种子、六名英雄的完整实例配置与本局服务器 `BattleSettings`。未取得这些数据时，1000 回合模拟仍不能声称对应真实账号战斗。

修正 GC 句柄宽度后，在隔离 AppContainer 中用原生代理打包器打包合成 Hydra `BattleSetup`，与私有运行库直接调用的原版打包结果逐字节一致，长度均为 1026 字节，记录见 `out/hydra-offline-runtime-20260921/agent-pack-compare-20260924.json`。这是离线功能验证，不能替代实机采集路径的线程和稳定性验证，也未恢复任何实时研究采集开关。
