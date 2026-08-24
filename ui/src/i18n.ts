export type UiLanguage = 'en' | 'zh-CN'

const STORAGE_KEY = 'raid-boss-tool-language'
const trackedText = new WeakMap<Text, { source: string; rendered: string }>()
const trackedAttributes = new WeakMap<Element, Map<string, { source: string; rendered: string }>>()
const translatedAttributes = ['placeholder', 'title', 'aria-label'] as const

// The game supplies champion names and some battle content in its own client
// language. This table translates the tool chrome and the controller messages
// while leaving unknown game-owned names untouched.
const ENGLISH_PHRASES: Record<string, string> = {
  '设置行动规则': 'Configure action rule',
  '默认技能释放顺序与目标': 'Default Skill Order and Targets',
  '为每个奇美拉形态分别设置': 'Configure Each Chimera Form Separately',
  '按奇美拉形态分别配置': 'Configured Separately by Chimera Form',
  '套形态技能组': 'form skill sets',
  '效果与技能冷却条件': 'Effects and Skill Cooldown Conditions',
  '技能冷却': 'Skill Cooldown',
  '英雄首回合技能': 'Champion First-Turn Skill',
  '本形态英雄首回合技能': 'Champion First Turn in This Form',
  '每次进入该形态时优先于严格规则执行': 'Runs before strict rules each time this form begins',
  '优先于严格规则执行': 'Executes before strict rules',
  '不设定': 'Not configured',
  '首回合': 'First Turn',
  '各技能自动选择合法目标': 'Automatic legal target for each skill',
  '每个技能独立目标': 'Independent target for each skill',
  '个已设置': 'configured',
  '蛇头顺序': 'Head Order',
  '蛇头优先级': 'Head Priority',
  '自动兜底': 'Automatic Fallback',
  '尚未完成试炼': 'No trials completed yet',
  '的队伍目标': ' team target',
  '复活技能属于特殊情况：只要死亡队友是合法目标，就始终先复活；没有死亡队友时再按这里的优先方式处理。': 'Revive skills are special: if a dead ally is a legal target, revive them first; otherwise use the priority below.',
  '从上到下寻找第一个已就绪技能；默认禁用的技能不会由该规则释放。复活技能会先选择已经死亡且可复活的队友。': 'Use the first ready skill from top to bottom. Disabled skills are skipped, and revive skills target a dead ally first.',
  '优先目标对当前技能不合法或不存在时，自动改用游戏允许的目标': 'If the preferred target is missing or illegal for the skill, automatically use a legal target',
  '优先类型都不在场时，选择当前技能可攻击且生命最低的蛇头；绝不会按数组顺序或场上位置猜目标。': 'If no preferred type is present, choose the lowest-HP head the skill can attack. Field position is never guessed.',
  '每次行动都会重新识别在场蛇头；优先目标未出现、死亡或不可攻击时自动跳过并使用生命最低的合法蛇头。': 'Heads are identified again every turn. Missing, dead, or illegal priorities are skipped for a legal fallback.',
  '必做试炼已不可能完成时免费重整；全部目标达成后停在结算页，不自动保留结果。': 'Use a free regroup when a required trial becomes impossible. Stop at results when all goals are met.',
  '优先解救被吞噬英雄并利用暴露蛇颈；达到最低伤害后停在结算页，不自动保留结果。': 'Prioritize freeing devoured champions and exposed necks. Stop at results after the damage goal is met.',
  '进入六头蛇准备界面或手动战斗后，工具会读取区域、四个蛇头和六人队伍；读取成功前不会执行任何操作。': 'Enter Hydra team setup or a manual battle to read the area, four heads, and six champions. No action is taken before detection succeeds.',
  '严格规则始终先执行；全部严格规则都不可执行时，才使用英雄的默认技能顺序。': 'Strict rules always run first. Default skill order is used only when no strict rule can execute.',
  '复制当前规则、战斗目标和准备队伍，之后可以独立修改。': 'Copy the current rules, battle goals, and prepared team, then edit them independently.',
  '点击保存时记录当前准备队伍': 'Records the current prepared team when you click Save',
  '只修改策略组名称，不会改变其中的规则。': 'Change only the group name; its rules stay unchanged.',
  '英雄与行动条件必须满足；各条件组按所选“并且/或者”计算。': 'Champion and action conditions must match; each condition group uses the selected AND/OR mode.',
  '默认规则只在没有严格规则可执行时接管。': 'The default rule runs only when no strict rule can execute.',
  '目标、已有或缺少、剩余回合统一在一条条件中设置': 'Set target, present/missing state, and remaining turns in one condition',
  '队伍位置来自当前准备界面的': 'Team positions come from the current setup screen:',
  '读取准备队伍中具体英雄的技能剩余冷却回合': 'Read remaining cooldowns for a specific champion in the prepared team',
  '来自游戏静态目录，不受本轮四个在场蛇头限制；点击加入优先级。': 'Loaded from the full game catalog, not only the four current heads. Click to add priority.',
  '按顺序尝试；不在场、已死亡或当前技能不可选择时自动跳过。': 'Try in order; skip heads that are absent, dead, or illegal for the current skill.',
  '“禁止默认释放”只约束默认规则；如果你另外建立了明确的严格规则，严格规则仍可调用该技能。': 'Disable only affects the default rule. An explicit strict rule may still use the skill.',
  '完整技能说明会在游戏读取后自动缓存': 'The full skill description is cached after it is read from the game',
  '每项都显示当前轮换奖励，可按奖励名称搜索后决定是否必做。': 'Each trial shows its current rotation reward. Search rewards before marking trials as required.',
  '只显示当前 Boss 模式的记录；另一模式的日志会独立保留。': 'Only the current boss mode is shown. The other mode keeps a separate log.',
  '每种 Boss 模式分别保留当前会话最近 800 条记录。': 'Each boss mode keeps its latest 800 entries for this session.',
  '当前规则不会读取其他英雄的技能冷却；需要联动时添加一条即可。': 'This rule does not inspect another champion\'s cooldown unless a condition is added.',
  '当前规则不会检查任何增益或减益；需要时添加一条即可。': 'This rule does not inspect buffs or debuffs unless a condition is added.',
  '仅当试炼当前激活': 'Only While This Trial Is Active',
  '已完成、尚未解锁或不在对应形态时忽略此规则': 'Ignore this rule when the trial is completed, locked, or outside its matching form',
  '不限制试炼状态': 'No Trial-State Restriction',
  '试炼激活': 'Active Trial',
  '蛇头按固定类型身份判断，不使用会变化的场上位置': 'Heads use stable type identity, never changing field position',
  '以下位置只属于准备队伍，不是蛇头站位。': 'These positions belong only to the prepared team, not Hydra heads.',
  '只有这里或游戏内暂停会中断接管': 'Only this control or in-game Pause stops takeover',
  '用英雄、形态、具体技能和目标描述一次行动。': 'Define one action using a champion, form, exact skill, and target.',
  '英雄库已按头像身份去重；最多显示前 48 个搜索结果，共': 'Champion catalog is deduplicated by portrait. Showing up to 48 results; total:',
  '优先目标未出现、死亡或不可攻击时自动跳过': 'Skip when the preferred target is absent, dead, or cannot be attacked',
  '不存在、已死亡或不是合法目标时自动跳过': 'Skip if absent, dead, or not a legal target',
  '四个当前蛇头必须全部满足': 'All four current heads must match',
  '任意一个当前蛇头满足即可': 'Any current head may match',
  '按本规则技能目标优先级选中的蛇头': 'Head selected by this rule\'s target priority',
  '技能冷却条件的最小回合不能大于最大回合': 'Minimum cooldown cannot exceed maximum cooldown',
  '每条技能冷却条件至少填写一个剩余冷却范围': 'Each cooldown condition needs at least one cooldown bound',
  '请为技能冷却条件选择该英雄的具体技能': 'Choose an exact skill for this cooldown condition',
  '技能冷却条件必须从当前准备队伍中选择一名具体英雄': 'Choose a specific champion from the prepared team for the cooldown condition',
  '英雄效果条件必须从当前准备队伍中选择一名具体英雄': 'Choose a specific champion from the prepared team for the effect condition',
  '请为每一条效果条件选择具体效果，或删除未完成的条件': 'Choose an effect for every condition or remove the unfinished condition',
  '技能剩余冷却必须是非负整数': 'Remaining cooldown must be a non-negative integer',
  '效果剩余回合必须是非负整数': 'Remaining effect turns must be a non-negative integer',
  '常用触发条件必须是非负数': 'Common trigger conditions must be non-negative',
  '至少选择一个奇美拉形态': 'Select at least one Chimera form',
  '高级条件必须是一个对象': 'Advanced conditions must be an object',
  '正在建立联盟 Boss 资源缓存': 'Building Alliance Boss Resource Cache',
  '读取游戏内账户、英雄、技能与模式资料…': 'Reading game account, champions, skills, and boss modes…',
  '联盟 Boss 策略中心': 'Alliance Boss Strategy Studio',
  '选择必做试炼': 'Select Required Trials',
  '搜索试炼内容或奖励': 'Search trial text or rewards',
  '搜索效果、类别或编号': 'Search effect, category, or ID',
  '当前轮换奖励': 'Current Rotation Reward',
  '当前奖励尚未读取': 'Current reward has not been read',
  '应用选择': 'Apply Selection',
  '编辑策略规则': 'Edit Strategy Rule',
  '添加策略规则': 'Add Strategy Rule',
  '创建策略组副本': 'Create Strategy Group Copy',
  '重命名策略组': 'Rename Strategy Group',
  '删除策略组': 'Delete Strategy Group',
  '切换策略组': 'Switch Strategy Group',
  '当前策略组': 'Current Strategy Group',
  '策略组名称': 'Strategy Group Name',
  '已保存队伍': 'Saved Team',
  '新副本会立即成为当前策略组。': 'The new copy becomes the active strategy group immediately.',
  '名称会立即保存。': 'The name is saved immediately.',
  '例如：高速队、稳定队': 'e.g. Speed Team, Safe Team',
  '创建副本': 'Create Copy',
  '导入策略': 'Import Strategy',
  '导出策略': 'Export Strategy',
  '策略已导入为新的策略组': 'Strategy imported as a new strategy group',
  '策略已导出': 'Strategy exported',
  '策略文件不能超过 2 MB': 'Strategy file cannot exceed 2 MB',
  '策略文件不是有效的 JSON': 'The strategy file is not valid JSON',
  '导入文件的根节点必须是对象': 'The imported file root must be an object',
  '不支持此策略导出版本': 'This strategy export version is not supported',
  '不支持此策略文件格式': 'This strategy file format is not supported',
  '导入文件中没有有效策略': 'The imported file does not contain a valid strategy',
  '策略所属 Boss 与当前界面不一致，请先切换模式': 'This strategy belongs to another boss; switch modes before importing it',
  '保存名称': 'Save Name',
  '正在保存…': 'Saving…',
  '等待保存': 'Not Saved',
  '规则名称': 'Rule Name',
  '留空会自动生成': 'Leave blank to generate automatically',
  '行动英雄': 'Acting Champion',
  '搜索英雄名称': 'Search champion name',
  '取消任意英雄': 'Cancel Any Champion',
  '没有找到英雄': 'No champion found',
  '规则类型': 'Rule Type',
  '严格执行规则': 'Strict Execution Rule',
  '只有全部条件满足时才释放指定技能': 'Use the specified skill only when every condition matches',
  '默认技能规则': 'Default Skill Rule',
  '没有严格规则可执行时，按设定顺序选择技能': 'Use skills in this order when no strict rule can execute',
  '奇美拉形态': 'Chimera Form',
  '英雄形态与技能组': 'Champion Form and Skill Set',
  '同时查看两套': 'Show Both Sets',
  '原始形态': 'Base Form',
  '变形形态': 'Alternate Form',
  '终极形态': 'Ultimate Form',
  '公羊形态': 'Ram Form',
  '狮子形态': 'Lion Form',
  '毒蛇形态': 'Viper Form',
  '默认技能释放顺序': 'Default Skill Order',
  '严格规则始终优先，且其技能会自动保留': 'Strict rules take priority and automatically reserve their skills',
  '严格规则保留': 'Reserved by Strict Rule',
  '提高技能优先级': 'Move Skill Up',
  '降低技能优先级': 'Move Skill Down',
  '恢复默认使用': 'Enable in Default Rule',
  '禁止默认释放': 'Disable in Default Rule',
  '默认技能优先目标': 'Default Skill Target Priority',
  '优先方式': 'Priority Method',
  '自动合法目标': 'Automatic Legal Target',
  '准备队伍指定位置': 'Prepared Team Position',
  '队伍位置': 'Team Position',
  '蛇头类型优先级': 'Hydra Head Type Priority',
  '未设置时自动选择': 'Automatic when unset',
  '目标无效时继续尝试下一项': 'Try the next item if this target is invalid',
  '从英雄的实际技能图标选择': 'Choose from the champion\'s actual skill icons',
  '试炼自动决策': 'Automatic Trial Decisions',
  '按当前可完成试炼选择行动': 'Choose actions for currently achievable trials',
  '维持效果': 'Maintain Effects',
  '兼容已有高级规则': 'Compatibility for existing advanced rules',
  '选择实际技能': 'Select Exact Skill',
  '变形也占用技能、受冷却限制': 'Transformation uses a skill and respects cooldown',
  '技能释放目标': 'Skill Target',
  '按蛇头类型优先': 'Prioritize Head Types',
  '正在吞噬的蛇头': 'Devouring Head',
  '暴露蛇颈': 'Exposed Neck',
  '生命最低蛇头': 'Lowest-HP Head',
  '生命最低队友': 'Lowest-HP Ally',
  '完整蛇头类型库': 'Complete Hydra Head Catalog',
  '尚未指定，直接使用安全兜底': 'No priority set; use safe fallback',
  '加入优先目标': 'Add Priority Target',
  '提高优先级': 'Move Up',
  '降低优先级': 'Move Down',
  '宽松安全兜底': 'Safe Flexible Fallback',
  '队友目标': 'Ally Targets',
  '形态切换技能': 'Form Switch Skill',
  '形态切换': 'Form Switch',
  '切换形态': 'Switch Form',
  '尚未学习到该技能的效果记录': 'No learned effect record for this skill yet',
  '常用触发条件': 'Common Trigger Conditions',
  '距切换形态': 'Turns Until Form Change',
  '下一形态': 'Next Form',
  '当前伤害': 'Current Damage',
  '技能冷却条件': 'Skill Cooldown Conditions',
  '任意一条冷却条件满足即可。': 'Any cooldown condition may match.',
  '所有冷却条件都必须同时满足。': 'All cooldown conditions must match.',
  '全部满足（并且）': 'Match All (AND)',
  '任意满足（或者）': 'Match Any (OR)',
  '添加冷却条件': 'Add Cooldown Condition',
  '具体英雄': 'Specific Champion',
  '具体技能': 'Specific Skill',
  '没有技能冷却限制': 'No Skill Cooldown Limit',
  '效果条件': 'Effect Conditions',
  '下面任意一条效果条件满足即可。': 'Any effect condition may match.',
  '下面所有效果条件都必须同时满足。': 'All effect conditions must match.',
  '添加效果条件': 'Add Effect Condition',
  '规则优先目标蛇头': 'Rule Priority Head',
  '任一在场蛇头': 'Any Current Head',
  '全部在场蛇头': 'All Current Heads',
  '必须已有': 'Must Be Present',
  '必须缺少': 'Must Be Missing',
  '没有效果限制': 'No Effect Restrictions',
  '完整条件数据': 'Full Condition Data',
  '试炼进度、队友状态与效果剩余回合等高级条件': 'Advanced trial, ally-state, and effect-duration conditions',
  '保存规则': 'Save Rule',
  '正在准备代理…': 'Preparing agent…',
  '策略接管已启动': 'Strategy takeover started',
  '策略组已保存': 'Strategy group saved',
  '新策略组已创建': 'New strategy group created',
  '策略组已重命名': 'Strategy group renamed',
  '已切换策略组': 'Strategy group switched',
  '策略组已删除': 'Strategy group deleted',
  '已请求暂停接管': 'Takeover pause requested',
  '没有找到 Raid 账户': 'No Raid account found',
  '请选择一个已识别的游戏内账户': 'Select a detected game account',
  '刷新账户': 'Refresh Accounts',
  '等待状态': 'Waiting for Status',
  '联盟 Boss 模式': 'Alliance Boss Mode',
  '形态轮换 · 试炼与奖励': 'Form Rotation · Trials and Rewards',
  '四头在场 · 吞噬与斩首': 'Four Heads · Devour and Decapitation',
  '待实战标定': 'Needs Live Calibration',
  '账户读取失败': 'Account Read Failed',
  '当前队伍': 'Current Team',
  '英雄库已就绪': 'Champion Catalog Ready',
  '战斗目标': 'Battle Goals',
  '自动追踪': 'Automatic Tracking',
  'Boss 难度': 'Boss Difficulty',
  '最多免费重整': 'Maximum Free Regroups',
  '最低伤害（M）': 'Minimum Damage (M)',
  '必做试炼': 'Required Trials',
  '点击选择试炼': 'Click to Select Trials',
  '按当前难度读取完整试炼内容': 'Read all trials for the current difficulty',
  '等待首次实战标定': 'Waiting for First Live Calibration',
  '接管状态': 'Takeover Status',
  '暂停': 'Pause',
  '开始执行': 'Start',
  'Boss 回合': 'Boss Turn',
  '已完成试炼': 'Completed Trials',
  '当前目标': 'Current Targets',
  '本次已完成试炼与奖励': 'Completed Trials and Rewards This Run',
  '等待战斗进度': 'Waiting for Battle Progress',
  '战斗中完成试炼后，这里会立即显示试炼内容、当前轮换奖励和奖励图标。': 'Completed trials and their current rewards appear here during battle.',
  '六头蛇战斗重点': 'Hydra Battle Focus',
  '状态已连接': 'State Connected',
  '等待六头蛇状态': 'Waiting for Hydra State',
  '已死亡': 'Dead',
  '正在吞噬': 'Devouring',
  '可作为目标': 'Targetable',
  '按蛇头身份选择，不按站位': 'Target by Head Identity, Not Position',
  '策略树': 'Strategy Tree',
  '行动规则': 'Action Rules',
  '添加规则': 'Add Rule',
  '还没有策略规则': 'No Strategy Rules Yet',
  '添加第一条规则后才能开始执行。': 'Add the first rule before starting.',
  '任意行动英雄': 'Any Acting Champion',
  '未指定英雄': 'Unspecified Champion',
  '六头蛇全程': 'Entire Hydra Battle',
  '默认技能顺序': 'Default Skill Order',
  '按当前试炼自动决策': 'Decide from Current Trial',
  '自动维持增益 / 减益': 'Automatically Maintain Buffs / Debuffs',
  '切回原始形态': 'Return to Base Form',
  '切换至变形形态': 'Switch to Alternate Form',
  '切换神话形态': 'Switch Mythical Form',
  '按技能合法目标自动选择': 'Automatic Legal Skill Target',
  '生命最低的队友': 'Lowest-HP Ally',
  '指定队友': 'Specified Ally',
  '生命最低蛇头（旧位置规则已安全迁移）': 'Lowest-HP Head (legacy position migrated)',
  '蛇头类型优先 · 生命最低兜底': 'Head Type Priority · Lowest-HP Fallback',
  '全部蛇头': 'All Heads',
  '任一蛇头': 'Any Head',
  '优先目标蛇头': 'Priority Head',
  '指定英雄': 'Specified Champion',
  '指定技能': 'Specified Skill',
  '行动窗口满足时': 'When the Action Window Matches',
  '运行记录': 'Run Log',
  '放大运行记录': 'Expand Run Log',
  '尚无运行记录': 'No Run Log Yet',
  '完整运行记录': 'Full Run Log',
  '关闭完整运行记录': 'Close Full Run Log',
  '清空当前模式': 'Clear Current Mode',
  '收起运行记录': 'Collapse Run Log',
  '当前模式': 'Current Mode',
  '保存': 'Save',
  '编辑': 'Edit',
  '删除': 'Delete',
  '上移': 'Move Up',
  '下移': 'Move Down',
  '关闭': 'Close',
  '清空': 'Clear',
  '已清空': 'Cleared',
  '取消': 'Cancel',
  '不限': 'Any',
  '不判断此项': 'Ignore this condition',
  '按需添加': 'Add as Needed',
  '组合': 'Logic',
  '不适用': 'N/A',
  '未读取': 'Not Read',
  '待读取': 'Pending',
  '等待读取效果': 'Waiting for Effect Data',
  '当前形态技能冷却': 'Current-form Skill Cooldown',
  '只有就绪时才会执行': 'Executes only when ready',
  '回合': 'turns',
  '冷却': 'Cooldown',
  '技能': 'Skill',
  '个技能': 'skills',
  '禁用': 'Disabled',
  '行动': 'Action',
  '目标': 'Target',
  '效果': 'Effect',
  '拥有': 'has ',
  '缺少': 'is missing ',
  '简单': 'Easy',
  '普通': 'Normal',
  '困难': 'Hard',
  '苦难': 'Hard',
  '残暴': 'Brutal',
  '噩梦': 'Nightmare',
  '终极噩梦': 'Ultra-Nightmare',
  '增益': 'Buff',
  '减益': 'Debuff',
  '特殊': 'Special',
  '奇美拉': 'Chimera',
  '六头蛇': 'Hydra',
  '蛇头': 'Hydra Head',
  '英雄': 'Champion',
  '试炼': 'Trial',
  '规则': 'Rule',
  '已停止': 'Stopped',
  '正在运行': 'Running',
  '正在准备': 'Preparing',
  '正在暂停接管': 'Pausing Takeover',
  '已请求暂停': 'Pause Requested',
  '已暂停': 'Paused',
  '已关闭': 'Closed',
  '启动失败': 'Startup Failed',
  '控制器已启动': 'Controller Started',
  '控制器已退出': 'Controller Exited',
  '准备执行': 'Preparing Action',
  '未执行': 'Not Executed',
  '已提交': 'Submitted',
  '未行动': 'No Action',
  '命中规则': 'matched rule',
  '当前英雄': 'active champion',
  '合法目标': 'legal target',
  '技能未就绪': 'skill not ready',
  '条件不满足': 'conditions not met',
  '接管保持运行': 'takeover remains active',
  '等待下一回合': 'waiting for the next turn',
  '等待行动': 'Waiting for Action',
  '战斗进行中': 'Battle in Progress',
  '战绩结算画面': 'Battle Results',
  '等待你的决定': 'Waiting for Your Decision',
  '队伍准备界面': 'Team Setup',
  '尚无已验证的': 'No verified ',
  '状态': ' state',
  '已连接': 'Connected',
  '未知英雄': 'Unknown Champion',
  '未知形态': 'Unknown Form',
  '在场目标': 'current targets',
  '名英雄': 'champions',
  '个头像': 'portraits',
  '个当前队伍技能图标': 'current-team skill icons',
  '个奖励图标已缓存': 'reward icons cached',
  '个难度': 'difficulties',
  '条记录': 'entries',
  '项条件': 'conditions',
  '个可用技能': 'available skills',
  '个主动技能': 'active skills',
  '个蛇头': 'heads',
  '号位队友': ' position ally',
  '号位': ' position',
  '优先': 'Prefer ',
  '按类型优先': 'Type Priority',
  '无效时自动选择': '; automatic if invalid',
  '另有': 'plus ',
  '例如': 'e.g.',
  '删除技能冷却条件': 'Delete cooldown condition',
  '删除效果条件': 'Delete effect condition',
  '效果条件 的目标': 'effect condition target',
  '效果条件 的状态': 'effect condition state',
  '的目标': ' target',
  '的状态': ' state',
  '个槽位': 'slots',
  ' 或 ': ' OR ',
  '剩余': 'Remaining',
  '当前 Boss': 'Current Boss',
  '奇美拉 Boss': 'Chimera Boss',
  '自己': 'Self',
  '请求失败': 'Request Failed',
  '请输入策略组名称': 'Enter a strategy group name',
  '策略组名称不能超过 60 个字符': 'Strategy group names cannot exceed 60 characters',
  '策略组不存在': 'Strategy group does not exist',
  '策略组 ID 不能为空': 'Strategy group ID cannot be empty',
  '缺少策略组 ID': 'Strategy group ID is missing',
  '每种 Boss 模式至少保留一个策略组': 'Keep at least one strategy group for each Boss mode',
  '确定删除策略组': 'Delete strategy group',
  '吗？此操作无法撤销。': '? This cannot be undone.',
  '未知的策略组操作': 'Unknown strategy group operation',
  '未命名策略': 'Unnamed Strategy',
  '默认策略': 'Default Strategy',
  '稀有遗物锻造材料': 'Rare Relic Forge Material',
  '史诗遗物锻造材料': 'Epic Relic Forge Material',
  '传说遗物锻造材料': 'Legendary Relic Forge Material',
  '神话遗物锻造材料': 'Mythical Relic Forge Material',
  '遗物石宝箱': 'Relic Stone Chest',
  '奇美拉奖励宝箱': 'Chimera Reward Chest',
  '随机遗物石': 'Random Relic Stone',
  '奇美拉奖励': 'Chimera Reward',
  '施放技能': 'Cast Skill',
  'Boss 有': 'Boss has',
  '神话 · 双形态': 'Mythical · Two Forms',
  '无冷却': 'No Cooldown',
  '暂未读取到技能说明': 'Skill description not read yet',
  '尚未学习到': 'Not learned yet:',
  '已排列': 'Prioritized:',
  '优先级': 'Priority',
  '已添加': 'Added:',
  '已选择': 'Selected:',
  '已完成': 'Completed',
  '尚未指定': 'Not specified',
  '当前 Boss 目标': 'Current Boss Target',
  '准备队伍英雄': 'Prepared Team Champion',
  '已保存英雄': 'Saved Champion',
  '当前不等待手动指令': 'Not waiting for a manual command',
  '没有匹配且可安全执行的规则': 'has no matching rule that can execute safely',
  '没有为当前英雄配置规则': 'No rule is configured for the active champion',
  '优先列表中没有已就绪且目标合法的技能': 'no ready skill in the priority list has a legal target',
  '当前6人队伍': 'The current six-champion team',
  '当前5人队伍': 'The current five-champion team',
  '与策略组保存队伍不一致（具体英雄副本）': 'does not match the strategy group (specific champion copies)',
  '与策略组保存队伍不一致（英雄身份或顺序）': 'does not match the strategy group (champion identity or order)',
  '实战英雄身份与策略组保存的五人队伍不一致': 'The live champion identities do not match the five-champion strategy group',
  '免费重整后英雄身份或顺序发生变化，已停止在队伍界面': 'Champion identity or order changed after regrouping; stopped on team setup',
  '六头蛇战斗已结束，但结算伤害仍不可用': 'The Hydra battle ended, but result damage is still unavailable',
  '为避免错误重试，已停留在结算画面并停止接管': 'stopped at results to avoid an incorrect retry',
  '六头蛇伤害目标已达成': 'Hydra damage goal reached',
  '已停留在结算画面并暂停接管': 'stopped takeover at the result screen',
  '不会自动保存结果': 'the result will not be saved automatically',
  '六头蛇战斗结束时伤害未达目标': 'Hydra damage was below the goal when the battle ended',
  '当前设置不执行自动重整，已停留在结算画面': 'automatic regroup is disabled; stopped at results',
  '且已达到自动重整上限': 'and the automatic regroup limit was reached',
  '六头蛇伤害未达目标': 'Hydra damage is below the goal',
  '正在执行第': 'Starting free regroup attempt',
  '次免费重整并重新开战': 'and restarting the battle',
  '六头蛇已用当前队伍重新进入手动战斗': 'Hydra restarted in manual mode with the current team',
  '六头蛇自动重整': 'Hydra automatic regroup',
  '六头蛇结算实例已经变化，未执行自动重整': 'The Hydra result context changed; automatic regroup was not performed',
  '指定技能未就绪或没有合法目标': 'the specified skill is not ready or has no legal target',
  '触发条件不满足': 'trigger conditions do not match',
  '策略树没有产生可执行动作': 'The strategy tree produced no executable action',
  '正在为游戏内账户': 'Preparing takeover for game account',
  '游戏内账户': 'game account',
  '控制器仍保持运行': 'the controller is still running',
  '正在等待控制器清理接管会话': 'waiting for the controller to clean up the takeover session',
  '代理尚未载入，正在载入所选账户': 'Agent is not loaded; loading it for the selected account',
  '正在只更新所选游戏账户的代理版本': 'Updating the agent only for the selected game account',
  '账户变化会立即停止接管': 'an account change stops takeover immediately',
  '点击游戏内暂停或主工具的“暂停接管”即可停止': 'click in-game Pause or Pause Takeover in this tool to stop',
  '正在监听测试账户': 'Monitoring test account',
  '已绑定': 'Bound to',
  '已用当前选定的': 'Started the first battle with the selected',
  '开始首场': 'for the first',
  '战斗': 'battle',
  '接管': 'takeover',
  '代理': 'agent',
  '控制器': 'controller',
  '账户': 'account',
  '已检查': 'Checked',
  '使用': 'uses',
  '但游戏状态已由手动操作或回合推进改变': 'but the game state changed due to manual input or turn progression',
  '旧请求已被安全拒绝': 'the stale request was safely rejected',
  '正在等待': 'Waiting for',
  '当前不': 'Currently not ',
  '未知': 'Unknown',
  '格式无效': 'has an invalid format',
  '必须是非负数': 'must be non-negative',
  '必须是一个对象': 'must be an object',
  '策略规则必须是一个数组': 'Strategy rules must be an array',
  '策略必须是一个对象': 'Strategy must be an object',
  ' 项': ' items',
  ' 条': ' items',
  '条规则': 'rules',
  '副本': 'Copy',
  ' 种': ' types',
  ' 共': ' total ',
}

const orderedPhrases = Object.entries(ENGLISH_PHRASES).sort(
  ([left], [right]) => right.length - left.length,
)

export function getInitialLanguage(): UiLanguage {
  let saved: string | null = null
  try {
    saved = window.localStorage.getItem(STORAGE_KEY)
  } catch {
    // Local storage can be unavailable in hardened embedded-browser sessions.
  }
  const language: UiLanguage = saved === 'zh-CN' ? 'zh-CN' : 'en'
  document.documentElement.lang = language
  return language
}

export function saveLanguage(language: UiLanguage) {
  document.documentElement.lang = language
  try {
    window.localStorage.setItem(STORAGE_KEY, language)
  } catch {
    // The selected language still applies for this session.
  }
}

export function translateToolText(value: string): string {
  let translated = value
  for (const [source, target] of orderedPhrases) {
    if (translated.includes(source)) translated = translated.split(source).join(target)
  }
  return translated
    .replaceAll('（', ' (')
    .replaceAll('）', ')')
    .replaceAll('；', '; ')
    .replaceAll('，', ', ')
    .replaceAll('：', ': ')
    .replaceAll('。', '.')
    .replaceAll('、', ', ')
}

function isSkipped(node: Node): boolean {
  const element = node.nodeType === Node.ELEMENT_NODE
    ? node as Element
    : node.parentElement
  return Boolean(element?.closest('[data-i18n-skip]'))
}

function localizeTextNode(node: Text, language: UiLanguage) {
  if (isSkipped(node)) return
  const current = node.nodeValue ?? ''
  let tracked = trackedText.get(node)
  if (!tracked || current !== tracked.rendered) {
    tracked = { source: current, rendered: current }
  }
  const rendered = language === 'en' ? translateToolText(tracked.source) : tracked.source
  tracked.rendered = rendered
  trackedText.set(node, tracked)
  if (current !== rendered) node.nodeValue = rendered
}

function localizeElementAttributes(element: Element, language: UiLanguage) {
  if (isSkipped(element)) return
  const tracked = trackedAttributes.get(element) ?? new Map()
  for (const attribute of translatedAttributes) {
    const current = element.getAttribute(attribute)
    if (current === null) continue
    let value = tracked.get(attribute)
    if (!value || current !== value.rendered) {
      value = { source: current, rendered: current }
    }
    const rendered = language === 'en' ? translateToolText(value.source) : value.source
    value.rendered = rendered
    tracked.set(attribute, value)
    if (current !== rendered) element.setAttribute(attribute, rendered)
  }
  trackedAttributes.set(element, tracked)
}

function localizeSubtree(root: Node, language: UiLanguage) {
  if (root.nodeType === Node.TEXT_NODE) localizeTextNode(root as Text, language)
  if (root.nodeType === Node.ELEMENT_NODE) localizeElementAttributes(root as Element, language)
  const walker = document.createTreeWalker(
    root,
    NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
  )
  let node = walker.nextNode()
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) localizeTextNode(node as Text, language)
    else localizeElementAttributes(node as Element, language)
    node = walker.nextNode()
  }
}

export function installDocumentLocalization(root: HTMLElement, language: UiLanguage) {
  document.documentElement.lang = language
  localizeSubtree(root, language)
  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') {
        localizeTextNode(mutation.target as Text, language)
        continue
      }
      if (mutation.type === 'attributes') {
        localizeElementAttributes(mutation.target as Element, language)
        continue
      }
      for (const node of mutation.addedNodes) localizeSubtree(node, language)
    }
  })
  observer.observe(root, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: [...translatedAttributes],
  })
  return () => observer.disconnect()
}
