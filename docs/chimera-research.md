# 奇美拉实现调查

调查基线：RAID 11.70.0，`Raid.exe` 文件版本 6000.3.18.6209205。

## 结论

奇美拉适合使用“外部策略控制器 + 进程内只读代理 + 命名管道”的方式实现。战斗动作不需要移动系统鼠标，可以在 Unity 主线程调用游戏原有的手动技能命令路径。

首版不需要覆盖奇美拉的全部外围功能。奖励、竞赛、快速战斗、队伍推荐和所有 27 个试炼的语义解释都可以后置。最小闭环只需要：识别奇美拉战斗、读取战斗状态、读取试炼进度、判断当前是否等待手动命令，然后提交一个技能和目标。

## 内容规模

### 游戏规则层

- 4 种形态：Ultimate、Ram、Lion、Viper。
- 总计 65 个 Boss 回合，每 5 个 Boss 回合切换形态。
- Ram、Lion、Viper 各有 9 个试炼，共 27 个；Ultimate 没有试炼。
- Ram 的 Duel、Lion 的 Hunter's Gaze、Viper 的 Necrosis 会改变目标选择和生存策略。

### 已完成的试炼与奖励映射

游戏的 `StaticAllianceData.ChimeraTypes` 包含 6 个难度。每个难度都有 4 个 Stage ID、27 条 `_challengeTypeById` 记录和按形态/部位/试炼难度组织的奖励。实测共读取 162 个难度相关试炼 ID；当前游戏语言下可以直接解析中文条件，不需要维护手写名称表。

每条目录记录目前包含：试炼 ID、形态、部位、试炼难度、中文描述、本试炼引用的状态效果，以及 `FlexibleReward` 中的奖励类型、概率、数量范围和资源类型。实时 `Challenge` 再提供开始/完成状态、目标与当前进度、计数器和完成回合。静态目录在代理加载时一次性缓存到内存，正常战斗不持续写入硬盘。

0938 实战确认每个形态由 Wing、Tail、Paw 三条并行链组成，每条链按 Easy→Normal→Hard 解锁。Ultimate 第 5 回合仍无可推进试炼，进入 Boss 总回合 6 的 Ram 后，8000501、8000504、8000507 恰好同时成为 `eligibleNow=true`，其余六条 Ram 普通/苦难仍锁定。第一版定向策略通过莉迪亚技能2的降低防御和玛瓦拉技能3的完美隐身，使 8000507 获得游戏模型确认的 24327.1/800000 进度与 1/4 计数。

后续自适应实战修正了第一版的关键判断：8000507 不能用“任一队友有完美隐身”替代“当前造成伤害的英雄有完美隐身”。控制器在内存中从 `AppliedEffect.SkillTypeId` 自动学习技能能力；进入 Ram 后自动识别莉迪亚的降低防御与玛瓦拉的友方完美隐身，先补 Boss 减益，再只让当时拥有完美隐身的缇塔斯攻击。游戏把试炼直接更新为 `completed=true`、`current=800000/800000`、`progressRatio=1`，完成回合 `selfTurnWhichCompleted=7`。该闭环没有按英雄名或固定技能槽选择提供者，能力缓存只在控制器退出且映射变化时写入一次。

结构化配方的实战以 Ram/Wing/Easy `8000501` 验证“Boss 受恐惧/真实恐惧时的技能伤害”。首个通用版本能自动识别莉迪亚一技能为恐惧提供者，并且只有效果真实存在时才攻击，游戏模型把试炼推进到 `66618.1/1200000`；这证明形态、试炼链、效果前置和伤害计入条件的判断链成立。第二版在整个进入 Ram 前的五个 Boss 回合中保留关键效果技能，但允许护盾、增加防御、减伤和免死类保护技能，且不把 `ShareDamage` 当作通用防御。相同阵容的首次 Ram 窗口把进度提高到 `276532/1200000`（23.0443%），缇塔斯也成功活着进入该形态。

该实战还说明“效果存在”并不足以构成完整策略：恐惧持续时间短，英雄行动顺序可能使主要输出英雄错过有效窗口。控制器现会在内存中同时记录每个技能造成的总伤害和对当前试炼实际增加的进度；未知 Boss 攻击技能在合法试炼窗口内只探索一次，之后按实测指数移动平均选择。这个统计与效果映射共用一个版本化缓存，整次接管退出时最多写盘一次。对必要试炼的免费重整仍保持保守：只接受运行时明确失败，或运行时给出的 `lastEligibleBossTurn` 已经过期，不使用不稳定的伤害预测直接放弃战斗。

战斗内当前伤害来自 `BattleState.ChimeraStatisticsByMultiplier` 中各倍率统计的 `Damage` 汇总，竞赛积分来自 `BattleState.ChimeraCompetitionPoints.ToLong()`；结算画面另以最终上下文字段复核。

### 本地代码与数据层

- `global-metadata.dat` 中有约 930 个唯一的、包含 `Chimera` 的名称或标识。这里包括界面、奖励、竞赛、快速战斗、网络 DTO、试炼和战斗系统，并不都属于自动战斗所需范围。
- 游戏清单中没有以 `Chimera` 明文命名的本地 AssetBundle。实现不应依赖资源文件名，应依赖运行时类型和模型状态。

## 已确认的运行时入口

`GameAssembly.dll` 导出了完整的 IL2CPP 查询接口，包括：

- `il2cpp_domain_get_assemblies`
- `il2cpp_assembly_get_image`
- `il2cpp_class_from_name`
- `il2cpp_class_get_field_from_name`
- `il2cpp_class_get_method_from_name`
- `il2cpp_field_get_offset`
- `il2cpp_runtime_invoke`
- `il2cpp_thread_attach`

因此代理 DLL 可以按名称动态解析大部分对象，不必先为每个版本生成一整套固定地址。

重点类型和方法：

| 程序集 | 命名空间 | 类型/方法 | 用途 |
| --- | --- | --- | --- |
| Unity.SharedModel.dll | SharedModel.Battle.Core | BattleProcessor | 回合、英雄、技能、状态效果和战斗结果 |
| Unity.ViewModel.dll | ECS.ViewModel.BattleView | ClientBattleViewContext | 客户端战斗上下文和奇美拉结束验证 |
| Unity.ViewModel.dll | ECS.ViewModel.BattleView | ClientCommandGenerator | 手动技能命令生成 |
| Unity.ViewModel.dll | ECS.ViewModel.BattleView.ChimeraChallenges | ChimeraChallengeContext | 试炼界面与进度 |
| Unity.SharedModel.dll | SharedModel.Meta.Alliances.Chimera.Enums | ChimeraForm | 奇美拉形态枚举 |

已在本地二进制中确认的关键名称包括：

- `CurrentTurn`、`CurrentRound`、`ActiveHero`、`BattleFinished`
- `_allHeroesCache`、`_heroSkills`、`Cooldown`、`MaxCooldown`
- `AppliedEffectsByHeroes`、`TurnLeft`
- `TargetProgress`、`SelfTurnWhichCompleted`
- `IsWaitingForManualCommand`
- `CreateCmdManually`

## 真实运行时探测结果

运行时探测确认的类型位置如下：

| 类型 | 程序集 | 命名空间 |
| --- | --- | --- |
| BattleProcessor | Unity.SharedModel.dll | SharedModel.Battle.Core |
| ClientBattleViewContext | Unity.ECS.dll | ECS.ViewModel.BattleView |
| ClientCommandGenerator | Unity.ECS.dll | ECS.ViewModel.BattleView.BattleAccess |
| ClientLiveBattleMode | Unity.ECS.dll | ECS.ViewModel.BattleView.BattleAccess |
| ChimeraChallengeContext | Unity.ECS.dll | ECS.ViewModel.BattleView.ChimeraChallenges |
| ChimeraForm | Unity.SharedModel.dll | SharedModel.Meta.Alliances.Chimera.Enums |

关键运行时布局：

- `BattleViewContext.Mode`：偏移 176，类型 `BaseClientBattleMode`。
- `BattleMode.Processor`：偏移 16，类型 `BattleProcessor`。
- `ClientBattleMode._generator`：偏移 104，类型 `ClientCommandGenerator`。
- `ClientCommandGenerator.IsWaitingForManualCommand`：偏移 56，布尔字段。
- `ClientCommandGenerator.CreateCmdManually`：2 个参数。

只读捕获钩子安装在：

- `BattleViewContext.OnEnabled`：保存活动战斗上下文，并沿 `Mode -> Processor / _generator` 读取指针。
- `BattleViewContext.OnDisabled`：清空保存的上下文。
- `ClientCommandGenerator.RequestCommand`：记录进入手动命令阶段后的等待字段。

代理目前以只读旁路方式挂钩 `CreateCmdManually`，只记录游戏自己提交的两个参数并原样转发，不会主动调用它。控制器卸载时先调用 `RaidChimeraAgentShutdown` 禁用全部钩子，再执行 `FreeLibrary`；该流程已在运行中的 Raid 上验证成功。

### 手动操作样本（PID 73664）

2026-08-09 在一场实际奇美拉手动战斗中确认：

- `ClientCommandGenerator.RequestCommand` 触发后，`IsWaitingForManualCommand = true`。
- 活动对象链为 `ClientBattleViewContext -> ClientBattleMode -> BattleProcessor / ClientCommandGenerator`。
- 界面技能入口实际是 `ClientBattleMode.SelectSkill(SkillData)`。
- 界面目标入口实际是 `ClientBattleViewContext.OnTargetActorSelectedInView(Int32 heroId)`。
- 最终手动命令签名是 `CreateCmdManually(Int32 targetId, Int32 skillId)`。
- 本次样本选择 `skillId=2`、`targetId=4`；用户事后确认这是一个**友方目标技能**，因此 `targetId=4` 在该场战斗中代表友方英雄实体，不是奇美拉 Boss。技能数据为 `level=3`、`cooldown=0`、`defaultCooldown=5`、`typeId=88903`、`heroTypeId=8896`。
- 界面技能、界面目标和最终命令三层记录一致；行动完成后下一次 `RequestCommand` 再次进入等待状态。
- 第一次手动样本是对奇美拉 Boss 施放，但当时尚未挂到正确的上层目标入口，因此没有记录到 Boss 的实体 ID；Boss 目标 ID 仍为未知。

`SkillData` 已确认的字段偏移：`Id=16`、`Level=20`、`Cooldown=24`、`IsPassive=28`、`IsSelected=29`、`TypeId=32`、`HeroTypeId=36`、`DefaultCooldown=64`。

名称解析不应维护一套易过期的外部 ID 表。`SkillData.Name`（偏移 40）和 `Description`（偏移 48）已经是游戏当前语言下的 IL2CPP 字符串，可以直接读取。英雄/目标的战斗实例 ID 则应先映射到 `BattleActorUIContext` 或 `BattleProcessor` 中的英雄类型 ID、阵营和 Boss 标志，再读取对应的本地化显示名。状态快照同时保留数值 ID 和名称：ID 用于稳定匹配，名称用于配置展示与人工核验。

### 首次受限执行测试结果

2026-08-09，测试账户 PID 73664 在相同战斗上下文、`AreaTypeId=13`、奇美拉标识开启、Boss 在场且 `IsWaitingForManualCommand=true` 时，尝试提交一次 `CreateCmdManually(targetId=4, skillId=2)`。

命令成功进入游戏原有路径，但没有通过战斗模型校验。事后确认控制器错误地把第二次友方技能样本中的 `targetId=4` 当成了 Boss；当前技能类型 82503 需要不同的合法目标。游戏日志给出的精确异常是：

`Selected target is not of available targets for current skill (82503)`

调用链为 `ClientCommandGenerator.OnCommandGenerated -> BaseClientBattleMode.ApplyCommand -> BattleProcessor.ApplyCommand -> BattleProcessor.EnqueueSkill`。游戏随后将战斗状态从 Started 改为 Finished 并返回主界面。代理和窗口钩子已安全卸载，Raid 进程保持响应。

结论：不能根据一次点击样本猜测目标阵营，也不能把运行时实体 ID 跨英雄、技能或战斗复用。执行器必须读取当前技能的 `ClientBattleMode.GetAcceptableTargets(SkillData)` 结果，并将候选实体映射为友方、敌方与奇美拉 Boss 后才允许调用最终命令。对于全体、随机、自身或无需显式目标的技能，也不能自行填入历史目标 ID。该事故后实际施法入口曾临时禁用；完成即时合法目标与 Boss 集合交叉验证后，才以失败关闭方式重新启用。

### 合法目标校验后的成功执行

同日重新进入奇美拉后，目标采集器确认：

- 界面“技能 2”使用零基内部编号 `skillId=1`。
- 当前 `heroTypeId=4716`，`skillTypeId=47102`，等级 5，冷却 0，基础冷却 4。
- `GetAcceptableTargets` 返回唯一目标 `[5]`。
- `_bossesUI` 的键为 `[5]`。后续确认普通 `_actorsUI` 的完整有效键为 `[0,1,2,3,4]`；早期读取器曾错误过滤合法键 `0`，因而漏掉第五名英雄。
- 因此 `targetId=5` 同时属于合法目标集合和 Boss 集合。

执行器在 Unity/窗口主线程再次即时读取并验证相同集合后，提交 `CreateCmdManually(targetId=5, skillId=1)`。命令完成后游戏进入下一名英雄的 `RequestCommand`，Raid 本地日志没有新增 `BattleErrorHandler`，进程保持响应。这个样本证明“当前 SkillData + 即时合法目标 + Boss 集合交集 + 手动等待状态”的受限命令路径可用。

`SkillData.Name` 当前返回 `Skill 47102 name` 这一类本地化键/占位文本，而不是最终界面译名。后续名称层需要继续调用游戏的本地化服务解析；策略匹配不依赖该显示文本。

## 最小状态模型

每次决策只需要以下数据：

1. 战斗身份：进程、游戏版本、是否为 Alliance Chimera、难度。
2. 时序：Boss 回合、玩家行动计数、当前形态、距下次变形的回合数。
3. 当前行动者：英雄实例 ID、英雄类型 ID、是否真的等待手动命令。
4. 技能：技能 ID、槽位、冷却、最大冷却、是否可选、合法目标。
5. 英雄：敌我阵营、实例 ID、类型 ID、血量、死亡状态、状态效果和计数器。
6. 试炼：挑战 ID、形态、难度、当前进度、目标进度、完成回合。

## 实现顺序

### 阶段 1：只读运行时探测

加载代理 DLL 后，仅调用 IL2CPP 导出接口，验证上述程序集、类型和方法是否能按名称找到。将结果经命名管道返回，不访问战斗对象实例。

### 阶段 2：捕获活动战斗上下文

对 `ClientBattleViewContext` 或 `ClientCommandGenerator` 的生命周期方法安装一个最小钩子，只保存活动实例的弱引用/GCHandle。代理退出或战斗结束时释放引用。

### 阶段 3：只读快照

在 Unity 主线程读取 `BattleProcessor`，生成 `chimera-state-contract.md` 中定义的快照。先记录未知 ID，不急于解释所有状态效果和试炼。

### 阶段 4：单条命令验证

仅在以下条件全部成立时调用 `CreateCmdManually`：

- 当前模式确认是 Alliance Chimera；
- 游戏正在等待手动命令；
- 当前英雄、技能和目标与最新快照一致；
- 技能冷却为 0 且目标仍存活；
- 目标存在于该技能即时返回的 `GetAcceptableTargets` 集合中；
- 本回合尚未发送过命令；
- 用户明确启用了执行模式。

第一次动作测试应使用 Free Regroup，不消耗钥匙，并限制为单回合单命令。

### 阶段 5：策略引擎

策略放在外部控制器中。规则根据形态、当前英雄、技能冷却、状态效果、Duel/Hunter's Gaze/Necrosis 和试炼进度选择动作。找不到匹配规则时暂停，不自动使用默认技能。

## 主要难点

- 找到活动的 `ClientCommandGenerator` 实例比找到类型本身更难，需要生命周期钩子或 ECS 单例路径。
- `CreateCmdManually` 必须从 Unity 主线程调用；从管道线程直接调用可能崩溃。
- 方法名相对稳定，但字段布局仍可能随版本更新，需要启动时验证类型、字段和参数数量。
- 当前机器有两个 Raid 进程，控制器必须按窗口句柄和 PID 显式绑定。
- 当前代理已能记录生命周期、手动等待状态、技能数据、目标实体和最终手动命令，并已接入结构化战斗快照、每回合决策快照和受限主线程动作队列。

## 运行时名称解析验证

2026-08-09 在 PID 73664 的新一场奇美拉手动战斗中完成只读验证：

- `ServiceLocator.Localizer` 能通过 `Localizer.Localize(key, LocalizationStorage)` 返回游戏当前语言的显示名称。
- `SkillData.Name`（例如 `Skill 47102 name`）只是战斗界面占位文本，不是有效的静态本地化键。
- 稳定名称路径为 `AppModel.Instance -> StaticData -> HeroData / SkillData`，再按类型 ID 获取 `HeroType.Name` 或 `SkillType.Name` 中的 `SharedLTextKey`。
- `heroTypeId=4716` 的名称键为 `l10n:hero-type/name?id=4710#static`，当前中文名为“死亡女妖莉迪亚”。
- `skillTypeId=47102` 的名称键为 `l10n:skill/name?id=47102#static`，当前中文名为“女妖哀嚎”。
- `heroTypeId=26866` 的名称键为 `l10n:hero-type/name?id=26860#static`，当前中文名为“奇美拉”。
- 本场技能 2 的 `GetAcceptableTargets` 为 `[5]`，Boss 集合也为 `[5]`；五名普通英雄实体为 `[0,1,2,3,4]`。

策略仍使用数值 ID 进行稳定匹配；名称键和显示名称只用于配置界面、日志与人工核验。

## 完整状态与策略链验证

2026-08-09 在 PID 73664 的同一奇美拉回合完成：

- `BattleContext.State` 在 DLL 中途载入时仍可恢复区域 13、地区 1302、战斗类型 8、当前回合、手动等待状态和活动英雄。
- 奇美拉 `CurrentFormIndex=0` 可按游戏枚举解析为 `Ultimate`；其实体为 `id=5/typeId=26866`。
- `AppliedEffect._type.KindId` 可直接解析效果类别。本次队伍的 `effectTypeId=370` 对应 `effectKindId=2004/Shield`，剩余 3 回合。
- 英雄自身技能优先读取 `_heroSkills`；未初始化时从 `Skills` 按 `IsHeroSkill` 过滤。五名英雄分别得到 4、6、4、5、5 个自身技能，不再混入装备、精通和祝福技能。
- 奇美拉 27 个挑战的 `Fixed` 目标值已按 `2^32` 还原，例如 5153960755200000 对应 1200000。
- `BattleHUDContext.PushActiveSkillData` 提供每回合可见 `SkillData`；代理对每个可用技能调用游戏原生 `GetAcceptableTargets`，生成 `decision_state`。
- 外部策略成功匹配“终极形态 + 英雄 4716 + 技能 47102 + Boss 目标”，得到“死亡女妖莉迪亚使用女妖哀嚎，目标奇美拉”。
- nonce 6001 的观察请求进入游戏主线程后，再次通过模型身份、活动英雄、技能冷却和目标 `[5]` 校验，并以 `command_probe_complete` 结束；没有调用施法。

命令安全条件现在允许合法的友方技能目标，但要求目标同时存在于游戏即时合法目标集合、当前 UI 实体集合和战斗模型中。这样不再把所有技能强制限定为 Boss，也不会复用旧战斗中的实体 ID。

### 第五名英雄与实体 ID 0

奇美拉允许五名英雄参战。当前样本的完整映射为：

- `0 / 8896 / 地窖守卫威克斯维尔`
- `1 / 4716 / 死亡女妖莉迪亚`
- `2 / 10436 / 蛛网占卜师玛瓦拉`
- `3 / 9906 / 黑羽缇塔斯`
- `4 / 8256 / 御影夫人`
- `5 / 26866 / 奇美拉`

.NET/IL2CPP 字典通过有效哈希槽和非空值区分“合法键 0”与空槽，因此实体 ID 不能用 `> 0` 判断。读取器、目标集合、模型查找、控制器和动作请求现在统一接受非负实体 ID，并继续拒绝负数。观察请求 nonce 8001 已验证行动英雄 `id=0/typeId=8896` 使用技能 `88901`、合法目标 `[5]` 时能够通过全部守卫并以 `command_probe_complete` 结束。

### 自动交接验证

2026-08-09 的限定自动交接测试完成了两条真实命令：

- nonce 9001：`地窖守卫威克斯维尔 / 8896` 使用 `死亡闪电 / 88901 / skillId 0`，目标 `奇美拉 / id 5`。
- nonce 2：控制器在下一次友方行动窗口识别 `御影夫人 / 8256`，按规则选择 `暗影猛击 / 82501 / skillId 0`，目标 `奇美拉 / id 5`。

两条命令都依次记录了 `command_guard`、`command_execute`、`create_manual_command` 和 `command_execute_returned`，随后出现新的 `RequestCommand`。控制器在第二条命令后按动作上限停止；战斗继续到 `蛛网占卜师玛瓦拉 / id 2` 的手动等待窗口，Raid 进程保持响应，奇美拉生命值从 99.9998% 降到 99.9977%。这证明了“外部规则选择 → 注入队列 → Unity 主线程即时守卫 → 原生手动命令 → 下一英雄再次决策”的完整闭环。

控制器随后改为等待 `command_execute_returned` 或 `command_probe_complete` 才计数，代理仅接受排队不再被视为成功。

### 五英雄连续闭环

同一场战斗随后从玛瓦拉的等待窗口执行了五条受限 A1 规则，顺序为：

1. `蛛网占卜师玛瓦拉 / 10436` → `104301`
2. `黑羽缇塔斯 / 9906` → `99001`
3. `死亡女妖莉迪亚 / 4716` → `47101`
4. `地窖守卫威克斯维尔 / 8896` → `88901`
5. `御影夫人 / 8256` → `82501`

五条命令的目标均为 `奇美拉 / id 5`，每条都有即时 `command_guard`、`command_execute`、`create_manual_command` 和 `command_execute_returned`。统计为 queued 5、guard 5、execute 5、returned 5、rejected 0、exception 0。控制器达到五条完成回执后退出；战斗继续停在下一次玛瓦拉手动等待窗口，回合字段为 `turn=10/playerTurnCount=8`，奇美拉生命值为 99.9802%，Raid 进程正常响应。
