const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

// i18n.ts touches window/document only inside functions these tests do not call.
const source = fs.readFileSync(path.join(__dirname, '../src/i18n.ts'), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const i18n = {};
new Function('require', 'exports', js)(require, i18n);
const { gameText, translateToolText } = i18n;

test('game text inside a tool label is left as written and unmarked', () => {
  const label = `试炼激活：${gameText('使用基于敌人最大生命值造成伤害的技能')}`;
  const translated = translateToolText(label);
  assert.equal(translated, 'Active Trial: 使用基于敌人最大生命值造成伤害的技能');
  assert(!/[]/.test(translated));
});

test('short words translate only as the whole text', () => {
  assert.equal(translateToolText('移除'), 'Remove');
  assert.equal(translateToolText(' 本场 '), ' This battle ');
  assert.equal(translateToolText('无'), 'None');
  // Inside a longer (game) name the short word is untouched.
  assert.equal(translateToolText('无尽之刃'), '无尽之刃');
});

test('counted controller messages read naturally', () => {
  assert.equal(translateToolText('验证：奇美拉第 12 回合，形态 Ram，下一形态 Lion'),
    '验证: Chimera turn 12, form Ram, next form Lion');
  assert.equal(translateToolText('当前奇美拉队伍已选择 4/5 名英雄；将按当前队伍自动开始战斗。'),
    'Current Chimera team: selected 4/5 champions; the battle will start automatically with the current team.');
  assert(!/ {2}/.test(translateToolText('预期 甲，当前 乙')));
});

test('Hydra forecast conclusions read naturally in English with game-style damage', () => {
  // Records saved before 1.1.1 still say 亿/万.
  assert.equal(translateToolText('预计整场伤害 20.9 亿（最低要求 5,000 万）'), 'Predicted total damage 2.09B (minimum required 50.0M)');
  assert.equal(
    translateToolText('六头蛇开局推演结论：预计第 5 个标记（约第 577 回合）为“Titus”，违反条件 1；预计整场伤害 2.09B（最低要求 5.00B），未达到，执行免费重整。'),
    'Hydra opening forecast result: predicted mark #5 (around turn 577) is “Titus”, violating condition 1; predicted total damage 2.09B (minimum required 5.00B), not reached; performing a free regroup.');
  assert.equal(
    translateToolText('六头蛇开局推演触发重整条件 1：预计第 5 个标记（约第 577 回合）为“Titus”'),
    'Hydra opening forecast triggers regroup condition 1: predicted mark #5 (around turn 577) is “Titus”');
  assert.equal(
    translateToolText('六头蛇开局推演：约第 140 回合伤害接近规则中的伤害阈值，实战判断可能与推演不同；本场不据此重整，仍按实际吞噬标记判定。'),
    'Hydra opening forecast: around turn 140, the damage is too close to a damage threshold in the rules and the live battle may decide differently; no regroup based on it this battle; the actual devour marks are used.');
  assert.equal(translateToolText('六头蛇伤害未达目标：2.09B/5.00B；正在执行第 3 次免费重整并重新开战。'),
    'Hydra damage is below the goal: 2.09B/5.00B; starting free regroup attempt 3 and restarting the battle.');
  assert.equal(translateToolText('六头蛇开局推演触发重整：预计整场伤害 2.09B，低于最低伤害 5.00B。'),
    'Hydra opening forecast triggers a regroup: predicted total damage 2.09B, below the minimum damage 5.00B.');
  assert.equal(translateToolText('目标进度：必要试炼 1/3，伤害 12.3M/123.5M'), 'Goal progress: mandatory trials 1/3, damage 12.3M/123.5M');
});

test('never-devoured regroups read naturally in English', () => {
  assert.equal(
    translateToolText('六头蛇开局推演结论：预计第 7 个标记的“Titus”约第 694 回合被吞下，违反条件 1，执行免费重整。'),
    'Hydra opening forecast result: predicted mark #7: “Titus” is swallowed around turn 694, violating condition 1; performing a free regroup.');
  assert.equal(
    translateToolText('六头蛇吞噬顺序重整条件 1 已触发：第 3 个标记的“Titus”已被吞下，但以下英雄前 5 个标记内不能被吞下：Titus；当前已观察顺序：A → B → Titus。'),
    'Hydra devour-order regroup condition 1 triggered: mark #3: “Titus” has been swallowed, but these champions within the first 5 marks must never be swallowed: Titus; observed order so far: A → B → Titus.');
});
