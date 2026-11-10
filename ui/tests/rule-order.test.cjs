const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

// ruleOrder.ts has no imports.
const source = fs.readFileSync(path.join(__dirname, '../src/ruleOrder.ts'), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const loaded = {};
new Function('exports', js)(loaded);
const { groupRules, normalizeRuleOrder, moveInGroup, ruleChampion, ruleKind } = loaded;

// Rules as the editor saves them: the acting champion's runtime ids, and the action type.
const rule = (name, ids, type = 'cast') => ({ name, when: { activeHeroTypeId: ids }, action: { type } });
const team = [7376, 9806, 8736];  // Ava (A6), Lydia-like 9800 (A6), 8730 (A6)
// Interleaved as older strategies keep them: defaults first, strict rules mixed, a trial rule in between.
const rules = [
  rule('9800 default', [9800, 9806], 'defaultSkillPriority'),
  rule('7370 default', [7370, 7376], 'defaultSkillPriority'),
  rule('9800 a', [9806]),
  rule('7370 a', [7376]),
  rule('8730 trial', [8736], 'executeTrialRecipe'),
  rule('9800 b', [9806]),
  rule('4710 outside', [4716]),
  rule('7370 b', [7376]),
];

test('a rule names one champion by its base id; kinds follow the controller\'s three passes', () => {
  assert.equal(ruleChampion(rules[0]), 9800);
  assert.equal(ruleChampion(rule('any', [7376, 9806])), null);
  assert.equal(ruleChampion({ when: {} }), null);
  assert.deepEqual(rules.slice(0, 5).map(ruleKind), ['default', 'default', 'strict', 'strict', 'trial']);
});

test('rules are grouped by the team\'s champions in team order, each as its rules are checked', () => {
  const groups = groupRules(rules, team);
  assert.deepEqual(groups.map((group) => [group.champion, group.inTeam]), [[7370, true], [9800, true], [8730, true], [4710, false]]);
  assert.deepEqual(groups.map((group) => group.entries.map((entry) => entry.rule.name)), [
    ['7370 a', '7370 b', '7370 default'],
    ['9800 a', '9800 b', '9800 default'],
    ['8730 trial'],
    ['4710 outside'],
  ]);
  // A champion of the team without rules still has its (empty) section.
  assert.deepEqual(groupRules([], [7376]).map((group) => [group.champion, group.entries.length]), [[7370, 0]]);
});

test('saving keeps each champion\'s checking order and only moves champions apart', () => {
  const ordered = normalizeRuleOrder(rules, team);
  assert.deepEqual(ordered.map((item) => item.name),
    ['7370 a', '7370 b', '7370 default', '9800 a', '9800 b', '9800 default', '8730 trial', '4710 outside']);
  // For every champion, its strict rules keep their order (what the controller checks first).
  for (const champion of [7370, 9800]) {
    const strict = (list) => list.filter((item) => ruleChampion(item) === champion && ruleKind(item) === 'strict').map((item) => item.name);
    assert.deepEqual(strict(ordered), strict(rules));
  }
  assert.equal(normalizeRuleOrder(ordered, team), ordered);  // already grouped: the same array
  // A rule for any acting champion is checked by its place among the others: nothing moves.
  const shared = [...rules, rule('any', [7376, 9806])];
  assert.equal(normalizeRuleOrder(shared, team), shared);
});

test('a rule moves only among the same champion\'s rules of the same kind', () => {
  const moved = moveInGroup(rules, 7, -1, team);  // '7370 b' up: swaps with '7370 a' (index 3)
  assert.deepEqual([moved[3].name, moved[7].name], ['7370 b', '7370 a']);
  assert.equal(moveInGroup(rules, 3, -1, team), null);  // first strict rule of its champion
  assert.equal(moveInGroup(rules, 7, 1, team), null);   // last strict rule: the default is another kind
  assert.equal(moveInGroup(rules, 1, -1, team), null);  // the only default of its champion
  assert.equal(moveInGroup(rules, 99, 1, team), null);
});
