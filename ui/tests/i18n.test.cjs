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
