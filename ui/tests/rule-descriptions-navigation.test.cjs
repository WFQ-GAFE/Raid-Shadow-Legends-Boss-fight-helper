const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

function load(name) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  new Function('require', 'exports', js)(require, exports);
  return exports;
}
const { describeConditionTree, describeRuleConditions } = load('ruleDescriptions.ts');
const { canMoveRule, revealRuleAfterRender, panelPreference, logDrawerPreference } = load('ruleNavigation.ts');
const strings = JSON.parse(fs.readFileSync(path.join(__dirname, '../src/i18n/messages/en.json'), 'utf8'));
const context = {
  text: (key, params = {}) => {
    assert.ok(key in strings, `missing descriptor phrase ${key}`);
    return strings[key].replace(/\{(\w+)\}/g, (_, name) => String(params[name]));
  },
  // 511 is another rank of champion 501: the same champion, the same name.
  heroName: id => ({ 501: 'Alpha', 511: 'Alpha', 502: 'Beta' })[id],
  skillName: (heroId, id) => heroId === 501 && id === 91 ? 'Rescue' : undefined,
  effectName: token => ({ 12: 'Shield', 13: 'Block Debuffs', Poison: 'Poison' })[token],
  trialName: id => id === 4 ? 'Rescue trial' : undefined,
  formName: form => form,
  allForms: ['Ultimate', 'Ram', 'Lion', 'Snake'],
};

test('a nested tree preserves every leaf, object, AND/OR group and negation', () => {
  const tree = { type: 'group', operator: 'all', negate: true, children: [
    { type: 'heroState', heroTypeId: 501, teamPosition: 2, state: 'dead' },
    { type: 'group', operator: 'any', children: [
      { type: 'effect', target: 'ally', heroTypeId: 502, presence: 'missing', effect: { kind: 'Poison' } },
      { type: 'effectCount', target: 'bossAll', polarity: 'debuff', countAtLeast: 0, countAtMost: 10, negate: true },
    ] },
    { type: 'skillCooldown', heroTypeId: 501, skillTypeId: 91, turnsAtLeast: 0, turnsAtMost: 0 },
    { type: 'heroState', heroTypeId: 502, teamPosition: 3, state: 'alive' },
  ] };
  // Brackets only where the logic needs them: the inner OR group and each NOT.
  assert.equal(describeConditionTree(tree, context), 'not (Alpha in team slot 2 is dead and (Beta lacks Poison or not (Every head on the field: debuff slots ≥ 0 and ≤ 10)) and Alpha · Rescue: cooldown turns = 0 and Beta in team slot 3 is alive)');
});

test('groups flatten, one subject\'s effects merge, and a negated single subject reads as its opposite', () => {
  const nested = { type: 'group', operator: 'any', children: [
    { type: 'effect', target: 'boss', presence: 'has', effect: { effectTypeId: 12 } },
    { type: 'group', operator: 'any', children: [{ type: 'effect', target: 'boss', presence: 'has', effect: { kind: 'Poison' } }] },
  ] };
  assert.equal(describeConditionTree(nested, context), 'Boss has Shield or Poison');
  const inside = { type: 'group', operator: 'all', children: [{ type: 'heroState', heroTypeId: 501, state: 'alive' }, nested] };
  assert.equal(describeConditionTree(inside, context), 'Alpha is alive and (Boss has Shield or Poison)');
  assert.equal(describeConditionTree({ type: 'effect', target: 'boss', presence: 'has', negate: true, effect: { effectTypeId: 13 } }, context), 'Boss lacks Block Debuffs');
  assert.equal(describeConditionTree({ type: 'heroState', heroTypeId: 502, state: 'dead', negate: true }, context), 'Beta is alive');
  // "Not (any head has it)" is not "any head lacks it": the NOT stays.
  assert.equal(describeConditionTree({ type: 'effect', target: 'bossAny', presence: 'has', negate: true, effect: { effectTypeId: 12 } }, context), 'not (Any head on the field has Shield)');
  assert.equal(describeConditionTree({ type: 'group', operator: 'all', negate: true, children: [{ type: 'heroState', heroTypeId: 501, state: 'dead' }] }, context), 'Alpha is alive');
});

test('effect, count and cooldown leaves retain selectors and inclusive zero bounds', () => {
  assert.equal(describeConditionTree({ type: 'effect', target: 'bossPriority', presence: 'has', effect: { effectTypeId: 12, turnsAtLeast: 0, turnsAtMost: 2 } }, context), 'The rule’s priority head has Shield (remaining turns ≥ 0 and ≤ 2)');
  assert.equal(describeConditionTree({ type: 'effectCount', target: 'bossAny', polarity: 'buff', countAtLeast: 0 }, context), 'Any head on the field: buff slots ≥ 0');
  assert.equal(describeConditionTree({ type: 'skillCooldown', heroTypeId: 909, skillTypeId: 999, turnsAtMost: 0 }, context), 'Champion #909 · Skill #999: cooldown turns ≤ 0');
});

test('unknown state and condition identities remain visible without claiming alive or a known subject', () => {
  const state = { type: 'heroState', heroTypeId: 909, state: 'futureState' };
  const text = describeConditionTree(state, context);
  assert.match(text, /futureState/);
  assert.doesNotMatch(text, /is alive|Alpha|Beta/);
  assert.match(describeConditionTree({ type: 'effect', target: 'futureTarget', presence: 'has', effect: { kind: 'futureEffect' } }, context), /futureTarget.*futureEffect/);
  assert.match(describeConditionTree({ type: 'heroState', heroTypeId: [501, 502], state: 'dead' }, context), /\[501,502\].*is dead/);
  assert.match(describeConditionTree({ type: 'futurePredicate', value: 17 }, context), /futurePredicate.*17/);
});

test('saved top-level conditions keep their array logic, maxima, trial ids and advanced predicates', () => {
  const when = { activeHeroTypeId: [501], form: ['Ram', 'Lion'], chimeraTurnAtLeast: 0, chimeraTurnAtMost: 17,
    effectConditionsMode: 'any', effectConditions: [
      { target: 'ally', heroTypeId: 502, presence: 'has', effect: { effectTypeId: 12, turnsAtLeast: 1 } },
      { target: 'bossAll', presence: 'missing', effect: { kind: 'Poison' } },
    ], eligibleTrialsAny: [4, 99], futurePredicate: { id: 77 } };
  const description = describeRuleConditions(when, context);
  assert.equal(description.full, 'Boss turn ≥ 0, and Boss turn ≤ 17, and Beta has Shield (remaining turns ≥ 1) or Every head on the field lacks Poison, and The active trial is Rescue trial or Trial #99, and Saved condition “futurePredicate”: {"id":77}');
  assert.equal(description.summary, 'Boss turn ≥ 0, and Boss turn ≤ 17, +3 more');
  assert.doesNotMatch(description.full, /Acting champion is|Boss form is/);
  const preview = describeRuleConditions(when, context, true).full;
  assert.match(preview, /^Acting champion is Alpha, and Boss form is Ram or Lion, and /);
});

test('rank variants name a champion once; every form listed or a top-level AND reads without brackets', () => {
  const when = { activeHeroTypeId: [501, 511, 502], form: ['Ultimate', 'Ram', 'Lion', 'Snake'], conditionTree: { type: 'group', operator: 'all', children: [
    { type: 'group', operator: 'any', children: [{ type: 'heroState', heroTypeId: 501, state: 'dead' }, { type: 'heroState', heroTypeId: 502, state: 'dead' }] },
    { type: 'effect', target: 'boss', presence: 'has', effect: { effectTypeId: 12 } },
    { type: 'effect', target: 'boss', presence: 'has', effect: { effectTypeId: 13 } },
  ] } };
  assert.equal(describeRuleConditions(when, context, true).full, 'Acting champion is Alpha or Beta, and Alpha is dead or Beta is dead, and Boss has Shield and Block Debuffs');
  assert.equal(describeRuleConditions({ bossHasEffects: [12, 13], bossMissingEffects: [12] }, context).full, 'Boss has Shield and Block Debuffs, and Boss lacks Shield');
});

test('every trial-set predicate of the controller reads as words, all lists with "and", any lists with "or"', () => {
  const describe = (when) => describeRuleConditions(when, context).full;
  assert.equal(describe({ completedTrialsAll: [4, 99] }), 'Rescue trial and Trial #99: completed');
  assert.equal(describe({ completedTrialsAny: [4, 99] }), 'Rescue trial or Trial #99: completed');
  assert.equal(describe({ activeTrialsAny: [4] }), 'Rescue trial: in progress');
  assert.equal(describe({ eligibleTrialsAny: [4] }), 'The active trial is Rescue trial');
  for (const key of ['incompleteTrialsAll', 'startedTrialsAll', 'startedTrialsAny', 'activeTrialsAll', 'eligibleTrialsAll', 'lockedTrialsAny', 'possibleTrialsAll', 'impossibleTrialsAny']) {
    assert.doesNotMatch(describe({ [key]: [4] }), /Saved condition|#/, key);
  }
});

test('invalid modes and empty groups expose exact saved data', () => {
  assert.match(describeConditionTree({ type: 'group', operator: 'futureOp', children: [] }, context), /futureOp.*children/);
  const text = describeRuleConditions({ effectConditions: [], effectConditionsMode: 'futureOp' }, context).full;
  assert.match(text, /effectConditions.*\[\].*effectConditionsMode.*futureOp/);
});

test('filtering prevents any move into an invisible neighbour; normal boundary checks remain', () => {
  for (const direction of [-1, 1]) assert.equal(canMoveRule(2, direction, 7, 'Alpha'), false);
  assert.equal(canMoveRule(0, -1, 7, ''), false);
  assert.equal(canMoveRule(6, 1, 7, ''), false);
  assert.equal(canMoveRule(2, -1, 7, '   '), true);
  assert.equal(canMoveRule(-1, 1, 7, ''), false);
});

test('rule navigation waits for the committed DOM then scrolls and focuses its exact index', () => {
  let row;
  let nextId = 0;
  const frames = new Map();
  const calls = [];
  const schedule = fn => { frames.set(++nextId, fn); return nextId; };
  const runFrame = () => { const [id, fn] = frames.entries().next().value; frames.delete(id); fn(); };
  const dispose = revealRuleAfterRender(4, id => { calls.push(['find', id]); return row || null; }, schedule, id => frames.delete(id));
  runFrame();
  assert.equal(frames.size, 1);
  row = { scrollIntoView: options => calls.push(['scroll', options]), focus: options => calls.push(['focus', options]) };
  runFrame();
  assert.deepEqual(calls, [['find', 'rule-row-4'], ['find', 'rule-row-4'], ['scroll', { block: 'center', behavior: 'smooth' }], ['focus', { preventScroll: true }]]);
  assert.equal(frames.size, 0);
  dispose();
});

test('cancelling a jump prevents stale navigation; retries are bounded', () => {
  let pending;
  let searches = 0;
  let cancelled;
  const dispose = revealRuleAfterRender(1, () => { searches++; return null; }, fn => { pending = fn; return 22; }, id => { cancelled = id; });
  dispose(); pending();
  assert.equal(searches, 0);
  assert.equal(cancelled, 22);
  revealRuleAfterRender(1, () => { searches++; return null; }, fn => { pending = fn; return 23; }, () => {});
  for (let i = 0; i < 20; i++) pending();
  assert.equal(searches, 20);
});

test('new defaults preserve saved panel choices and log drawer preferences by boss', () => {
  const data = { 'studio:panel:chimera:profiles': 'open', 'studio:panel:hydra:rules': 'closed', 'studio:logs:hydra': 'open' };
  const storage = { getItem: key => data[key] ?? null };
  assert.equal(panelPreference(storage, 'chimera:profiles', false), true);
  assert.equal(panelPreference(storage, 'hydra:rules', true), false);
  assert.equal(panelPreference(storage, 'chimera:decision', false), false);
  assert.equal(panelPreference(storage, 'chimera:team', true), true);
  assert.equal(logDrawerPreference(storage, 'hydra'), true);
  assert.equal(logDrawerPreference(storage, 'chimera'), false);
  assert.equal(panelPreference({ getItem: () => { throw Error('storage unavailable'); } }, 'chimera:rules', true), true);
});
