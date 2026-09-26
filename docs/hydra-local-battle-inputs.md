# 六头蛇开局数据的离线取得与转换

> 2026-09-25 清理说明：本文是研究阶段的历史记录。文中提到的研究期工具（历史重放 `replay-observed`、首回合投影、选择器探针、动作脚本校验等）和 `out/` 下的研究证据已删除；离线引擎现在只保留 `json-convert` 与 `forecast` 两种模式，验证基线与现行实现见 [1.0.6 开局吞噬顺序推演](1.0.6-hydra-devour-forecast.md)。

本说明最初基于本机已安装版本（Unity 6000.3.18f1、原版 `GameAssembly.dll`）的类型元数据及离线方法体检查，讨论自然缓存的输入来源。2026-09-25 已另通过安全版代理的有界 JSON 采集取得一场真实六头蛇战斗的 `BattleSetup` 与 `BattleSettings`，并在隔离原版引擎中完成短程启动。取得开局输入不代表千回合预测已经验证；当前离线运行器尚未复现工具的实战策略动作。

## BattleSetup 的自然缓存

正常的战斗创建流程会调用 `BattleSetupsCache.CacheOnDevice`。写入函数依次执行：

1. `JsonMain.ToJsonStr(List<BattleSetup>, ignoreNames: false)`；
2. UTF-8 编码、GZip 压缩、Base64 编码；
3. `Application.DeprecatedSettings.Local.SetString(key, value)`，本机的 `LocalSettings` 最终调用 Unity `PlayerPrefs.SetString`。

读取函数执行完全相反的 Base64 → GZip → UTF-8 → `JsonMain.FromJsonStr<List<BattleSetup>>`。`BattleSetupsCache.GetKey` 使用 `BattleSetupCache_<AppModel.UserId>`；`LocalSettings.GetKey` 在配置了环境值时再加 `<Environment>_` 前缀。实际查找必须同时核对账号 ID 和环境前缀，不能把其他账号的缓存误认作当前战斗。正常完成或取消六头蛇后，游戏会清理这个缓存；应在战斗仍进行且缓存存在时从游戏外只读复制，原始值连同读取时间和注册表路径一起保留。

缓存是一个 `BattleSetup` **列表**，不能直接把其中第一个成员当作目标场次。每个候选至少核对 JSON 字段 `z`（Setup GUID）、`r`（随机种子）、`k`（战斗种类，六头蛇为 5）、`i`（关卡 ID）、`f`（玩家队伍）、`s`（敌方队伍）。玩家队伍中的 `h` 是 `HeroSlotSetup` 列表；每个英雄的 `h` 是库存英雄 ID，`i` 是类型 ID，`t` 是站位。还要检查六位英雄及其技能、装备、精通、祝福等实例数据，队伍与同一场次的观测记录一致。缺少场次 GUID 或无法排除多个候选时，结果为 `unknown`。

离线解压器应有压缩输入长度、解压字节数和 JSON 深度上限，并拒绝非 GZip、无效 UTF-8、非列表和重复 Setup GUID。它只用于检视和选出候选，不应自行把短键 JSON 手工改写成 MessagePack；这些字段有枚举、可空值、`Fixed`、`Guid`、`DateTime` 及游戏特有序列化规则。

## 转为原版处理器输入

在现有受限、无网络的独立 AppContainer 中，使用**同版本原版序列化器**完成转换：

1. 初始化 `Client.RaidApp.MessagePack.Init`；
2. 用 `Plarium.Common.Serialization.JsonMain.FromJsonStr<List<BattleSetup>>` 解析上述已解压 JSON，或用接收 `System.Type` 的重载；
3. 在所得列表中按 Setup GUID、种子、关卡和六个库存英雄 ID 选出唯一的 `BattleSetup`；
4. 对该对象调用原版 `MessagePackExtensions.ToPackedMessagePack<BattleSetup>`，将有界字节数组另存为 `battle-setup.msgpack`；
5. 用 `FromPackedMessagePack<BattleSetup>` 回读，再核对上述身份字段、队伍和关键实例集合；记录原始缓存字符串与输出文件的 SHA-256、游戏引擎版本及时间。

当前 `src/offline_runtime/static_data_probe.hpp` 已有原版 MessagePack 初始化、打包回读和有界字节复制函数，可供这个新增离线模式复用。转换时须把 JSON 列表及选出的对象保持为本地 GC 根，所有原版方法只在隔离进程调用。`BattleSetup` 不足以独立确定战斗轨迹，不能仅凭这个文件运行并宣称预测。

## BattleSettings 的自然缓存缺口

`BattleSettings` 是 `SignInResult.GameParameters`（登录响应 JSON 键 `f`）中的 `GameParameters.BattleSettings`（键 `b`）。原版 `SettingsManager.Update(SignInResult)` 把它放入 `FeatureSettings` 和 `SharedModelManager.GameParameters`；已检查的方法体没有显示把完整 `GameParameters` 另存为本地 `BattleSetupCache`。当前静态数据缓存也不含此动态设置，已检查的本机持久化数据中没有可用的完整 `BattleSettings` 副本。因此目前**没有已验证的纯本地来源**可与自然缓存的 Setup 配成完整真实回放输入。

尤其要取得同一登录配置中的 `ActiveEngineVersion`、`WarmupBattleRandomCount`、`MaxTurnsInBattle` 和按区域的回合限制。`StartBattle` 会先依 `WarmupBattleRandomCount` 消耗随机流，使用构造器默认值或从另一时间的登录配置推测值，会使后续吞噬顺序失去可靠性。自然缓存没有提供这份动态设置；当前实测样本改用已验证的安全版代理在开局读取原版 JSON。未来每场仍须重新核对其来源与场次绑定。未取得并绑定本局适用的 `BattleSettings` 时，离线结果必须标记 `unknown`，不能启动自动重试判断。

## 2026-09-25 实战样本的进展与限制

gafee 这场战斗已保存同一场次的 Setup GUID `0f67c419094c894e8ed47e97773dd2e9`、种子 `330805491`、六个库存英雄和 `BattleSettings`；原版离线转换得到 `battle-setup.msgpack` 与 `battle-settings.msgpack`。当前 11.75.0 游戏磁盘二进制与隔离副本的哈希一致。旧隔离包的静态数据哈希 `9CB6D708…4D9D5` 与当前游戏缓存 `9DEB7FCB…17422` 不同，故旧包结果不能用作这场战斗的预测校验。匹配当前缓存的新隔离包在无权限 AppContainer 中成功启动真实 Setup/Settings，并得到与实战相同的第一枚标记；游戏当次登录所使用的静态数据哈希尚缺直接证明。

采集器在原版第 98 回合停止，控制器日志继续记录到第 305 回合。后段日志有策略快照和已提交技能，但缺完整原生 RNG 与每个动作后的状态。离线运行器目前对玩家使用 `GetDefaultSkill`，首个玩家技能即与实战不同；因此其后续标记序列不是这场战斗的预测。必须先完成原版决策状态到工具策略的适配，再与已记录的动作、随机状态和标记逐步核对。

## 实际预测的验收

取得完整输入后，先用记录下来的真实技能与目标逐动作回放，并比对每次动作前后 RNG、首次标记与吞噬事件。任何首次分歧都应停止并报告所在回合、动作和字段；真实轨迹未一致前不做千回合外推。再接入已验证的离线策略决策，并在原版处理器中运行到结算或原版第 1000 回合上限。只有跨样本、含不同种子及策略路径的标记序列一致后，才可把“前 N 次不得标记指定英雄”作为开局筛选依据。

证据：`out/hydra-research-20260924-local-input/dump` 中的 `BattleSetupsCache`、`CompressStringExtensions`、`LocalSettings`、`SettingsManager` 方法体；`out/hydra-research-20260921/metadata-cs` 中的 `BattleSetup`、`TeamSetup`、`HeroSlotSetup`、`GameParameters`、`BattleSettings`、`SignInResult` 类型。原始资料留在被忽略的 `out` 目录中。
