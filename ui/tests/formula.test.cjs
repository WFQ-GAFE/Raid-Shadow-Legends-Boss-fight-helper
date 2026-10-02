const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

function load(name) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name + '.ts'), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  new Function('require', 'exports', js)(require, exports);
  return exports;
}
const { describeFormula, FORMULA_MESSAGE_KEYS } = load('formula');
const { formatMessage } = load('i18n/format');
const catalogs = Object.fromEntries(['en', 'zh-CN', 'pt-BR'].map((code) => [code,
  JSON.parse(fs.readFileSync(path.join(__dirname, '../src/i18n/messages', `${code}.json`), 'utf8'))]));
const context = (locale, names) => ({ locale, kindName: (kind) => names[kind] ?? kind,
  t: (key, params) => formatMessage(locale, catalogs[locale][key], params) });
const zh = (formula) => describeFormula(formula, context('zh-CN', { ContinuousDamage: '中毒', Mark: '妖术' }));
const en = (formula) => describeFormula(formula, context('en', { ContinuousDamage: 'Poison', Mark: 'Hex' }));
const pt = (formula) => describeFormula(formula, context('pt-BR', { ContinuousDamage: 'Veneno', Mark: 'Maldição' }));

test('every word a formula can use is in every catalog', () => {
  for (const [code, catalog] of Object.entries(catalogs))
    assert.deepEqual(FORMULA_MESSAGE_KEYS.filter((key) => !(key in catalog)), [], code);
});

test('Portuguese reads the same formulas with its own words and decimal comma', () => {
  assert.equal(pt('3.9*DEF+0.035*TRG_HP'), '3,9×DEF + 0,035×VIDA máx. do alvo');
  const formula = '((!targetIsBoss&&!relationTargetIsBoss&&!targetIsMinion&&!relationTargetIsMinion)*0.3*REL_TRG_HP)'
    + '+((targetIsBoss||relationTargetIsBoss||targetIsMinion||relationTargetIsMinion)*0.1*TRG_HP)';
  assert.equal(pt(formula), 'contra alvos que não sejam chefes e lacaios: 0,3×VIDA máx. do alvo; contra chefes ou lacaios: 0,1×VIDA máx. do alvo');
});

test('target-type cases read as plain words', () => {
  const formula = '((!targetIsBoss&&!relationTargetIsBoss&&!targetIsMinion&&!relationTargetIsMinion)*0.3*REL_TRG_HP)'
    + '+((targetIsBoss||relationTargetIsBoss||targetIsMinion||relationTargetIsMinion)*0.1*TRG_HP)';
  assert.equal(zh(formula), '对首领和仆从以外的目标：0.3×目标最大生命值；对首领或仆从：0.1×目标最大生命值');
  assert.equal(en(formula), 'vs targets other than bosses and minions: 0.3×target max HP; vs bosses or minions: 0.1×target max HP');
});

test('simple formulas keep their numbers with stat names', () => {
  assert.equal(zh('1.3*ATK'), '1.3×攻击');
  assert.equal(zh('ATK*3'), '3×攻击');
  assert.equal(zh('3.9*DEF+0.035*TRG_HP'), '3.9×防御 + 0.035×目标最大生命值');
  assert.equal(en('3.9*DEF+0.035*TRG_HP'), '3.9×DEF + 0.035×target max HP');
  assert.equal(zh('4*(ATK+ACC)'), '4×(攻击 + 精准)');
});

test('bonuses that grow with a count', () => {
  assert.equal(zh('4.2*ATK*(1+0.1*TRG_DEBUFF_COUNT)'), '4.2×攻击，目标每有 1 个减益 +10%');
  assert.equal(en('4.2*ATK+((4.2*ATK)*0.1*TRG_DEBUFF_COUNT)'), '4.2×ATK, +10% per debuff on the target');
  assert.equal(zh('ATK*(2.5+3*(1-HP_PERC))'), '2.5×攻击，自身每损失 1% 生命再加 0.03×攻击');
  assert.equal(en('(4+deadAlliesCount)*DEF'), '4×DEF, plus 1×DEF per dead ally');
  assert.equal(zh('ATK*(1+SPD/100)'), '攻击，每点速度 +1%');
  assert.equal(zh('0.2*HP+(0.2*HP*0.1*TRG_BUFF_COUNT)+(0.2*HP*0.1*BUFF_COUNT)'),
    '0.2×最大生命值，目标每有 1 个增益 +10%，自身每有 1 个增益 +10%');
  assert.equal(zh('(3.1+(3.1*0.15*EffectsAppliedOnTargetCountOfKind(ContinuousDamage_KindId)))*ATK'),
    '3.1×攻击，目标每有 1 个【中毒】 +15%');
  assert.equal(en('0.01*REL_TRG_HP*REL_TRG_DEBUFF_COUNT'), '0.01×target max HP per debuff on the target');
});

test('variables read as the skill descriptions say', () => {
  // 轮回烈焰: damage grows with the buffs the skill removed ("unapplied" effects).
  const formula = 'targetIsBoss*(0.1*TRG_HP+0.005*TRG_HP*unappliedStatusEffectsCountByCurrentSkill)'
    + '+!targetIsBoss*(0.1*TRG_HP+0.03*TRG_HP*unappliedStatusEffectsCountByCurrentSkill)';
  assert.equal(zh(formula), '对首领：0.1×目标最大生命值，本技能每移除 1 个效果再加 0.005×目标最大生命值；'
    + '对首领以外的目标：0.1×目标最大生命值，本技能每移除 1 个效果再加 0.03×目标最大生命值');
  assert.match(en(formula), /plus 0\.005×target max HP per effect removed by this skill/);
  assert.equal(zh('0.3*CALCULATED_DMG'), '0.3×前一次命中的伤害');
  assert.equal(zh('0.99*CUR_HP'), '0.99×自身当前生命值');
  assert.equal(zh('EXCESSIVE_HEAL*0.5'), '0.5×过量治疗');
});

test('conditions on counts, roles and own stats', () => {
  assert.equal(zh('((TRG_BUFF_COUNT>0)*(4.6*ATK))+((TRG_BUFF_COUNT==0)*(9.2*ATK))'), '目标有增益时：4.6×攻击；目标没有增益时：9.2×攻击');
  assert.equal(en('(!targetHasControlDebuff*3*ATK)+(targetHasControlDebuff*6*ATK)'),
    'if the target has no control debuff: 3×ATK; if the target has a control debuff: 6×ATK');
  assert.match(zh('(targetIsBoss*(7.5*ATK))+((TRG_ATK>=ATK)*(!targetIsBoss*7.5*(ATK+TRG_ATK)))'), /目标攻击 ≥ 自身攻击时/);
  const roles = '(!targetIsBoss*(((targetRole==Attack_Role)*(5*TRG_ATK))+((targetRole==Health_Role)*(0.35*TRG_HP))'
    + '+((targetRole==Support_Role)*(0.35*TRG_HP))))+((targetIsBoss||targetIsChest)*(0.1*TRG_HP))';
  assert.equal(zh(roles), '对首领以外的目标，目标定位为攻击时：5×目标攻击；对首领以外的目标，目标定位为生命值或辅助时：0.35×目标最大生命值；对首领或宝箱：0.1×目标最大生命值');
});

test('formulas beyond the patterns stay readable and never throw', () => {
  assert.equal(zh('DMG_MUL/4/(aliveAlliesCount-!producerIsDead)'), '受到的伤害/4，由除自身外的存活队友平分');
  assert.equal(zh('5.5*DEF*(1+SHIELDS_SUM_VALUE/HP)'), '5.5×防御×(1 + 护盾总值/最大生命值)');
  // Unparsable text keeps its form with the known names.
  assert.equal(zh('3*ATK+)'), '3×攻击+)');
  assert.equal(en('UNKNOWN_VAR*2'), '2×UNKNOWN_VAR');
});
