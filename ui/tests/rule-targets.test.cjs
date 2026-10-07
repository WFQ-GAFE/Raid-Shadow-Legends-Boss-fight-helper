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
const { ruleTargetMismatches, skillReach, targetFits, TARGET_LABEL_KEYS } = load('ruleTargets.ts');
const catalog = JSON.parse(fs.readFileSync(path.join(__dirname, '../src/i18n/messages/en.json'), 'utf8'));

// Skills as tools/hero_data.py keeps them: the game's SkillTargets name.
const data = { skills: {
  1: { targets: 'AliveEnemies' }, 2: { targets: 'AliveAllies' }, 3: { targets: 'Producer' },
  4: { targets: 'AliveAlliesExceptProducer' }, 5: { targets: 'DeadAllies' }, 6: { targets: 'AliveHeroes' }, 7: {},
} };

test('each SkillTargets value reaches the kinds the controller reports', () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7, 8].map(id => skillReach(data, id)),
    ['enemy', 'ally', 'self', 'otherAlly', 'deadAlly', 'any', undefined, undefined]);
  for (const key of Object.values(TARGET_LABEL_KEYS)) assert.ok(key in catalog, key);
  for (const reach of ['enemy', 'ally', 'otherAlly', 'self', 'deadAlly', 'any', 'none', 'absent']) assert.ok(`target.reach.${reach}` in catalog, reach);
});

test('a target fits only when the skill can ever reach it', () => {
  const fits = (reach, targets, atCaster) => targets.filter(target => targetFits(reach, target, atCaster));
  const all = ['auto', 'boss', 'exposedNeck', 'self', 'lowestHpAlly', 'allyPosition', 'allyHeroTypeId', 'somethingNew'];
  assert.deepEqual(fits('enemy', all), ['auto', 'boss', 'exposedNeck', 'somethingNew']);
  assert.deepEqual(fits('ally', all), ['auto', 'self', 'lowestHpAlly', 'allyPosition', 'allyHeroTypeId', 'somethingNew']);
  assert.deepEqual(fits('deadAlly', all), ['auto', 'somethingNew']);
  assert.deepEqual(fits('any', all), all);
  // Report-only kinds (nothing there then) say nothing about the target's kind.
  assert.deepEqual(fits('absent', all), all);
  assert.deepEqual(fits('none', all), all);
  // Itself only: lowest-HP ally picks the caster; a named ally only when it is the caster (unknown is not judged).
  assert.deepEqual(fits('self', all, false), ['auto', 'self', 'lowestHpAlly', 'somethingNew']);
  assert.deepEqual(fits('self', all), ['auto', 'self', 'lowestHpAlly', 'allyPosition', 'allyHeroTypeId', 'somethingNew']);
  // Other allies: never the caster.
  assert.deepEqual(fits('otherAlly', all, true), ['auto', 'lowestHpAlly', 'somethingNew']);
  assert.deepEqual(fits('otherAlly', all, false), ['auto', 'lowestHpAlly', 'allyPosition', 'allyHeroTypeId', 'somethingNew']);
});

test('a rule lists the targets its skills can never take', () => {
  const team = [501, 502, 503];
  const isCaster = typeId => typeId === 502;
  const cast = (skillTypeId, target) => ({ action: { type: 'cast', skillTypeId, target } });
  assert.deepEqual(ruleTargetMismatches(data, cast(1, { type: 'self' }), team, isCaster, 502),
    [{ skillTypeId: 1, reach: 'enemy', target: 'self', heroTypeId: 502 }]);
  assert.deepEqual(ruleTargetMismatches(data, cast(1, 'boss'), team, isCaster), []);
  // A slot holding the caster, for a skill on other allies only; another slot is fine.
  assert.equal(ruleTargetMismatches(data, cast(4, { type: 'allyPosition', position: 2 }), team, isCaster)[0].position, 2);
  assert.deepEqual(ruleTargetMismatches(data, cast(4, { type: 'allyPosition', position: 1 }), team, isCaster), []);
  assert.equal(ruleTargetMismatches(data, cast(4, { type: 'allyHeroTypeId', heroTypeId: 502 }), team, isCaster)[0].targetHeroTypeId, 502);
  // Without hero data, or for a skill it does not know, nothing is judged.
  assert.deepEqual(ruleTargetMismatches(null, cast(1, 'self'), team, isCaster), []);
  assert.deepEqual(ruleTargetMismatches(data, cast(7, 'self'), team, isCaster), []);
  // Default skill orders: each form's skills and its first-turn skill, once per skill and target; form switches are skipped.
  const order = { action: { type: 'defaultSkillPriority', formPolicies: {
    Ram: { prioritySkills: [{ skillTypeId: 1, target: { type: 'lowestHpAlly' } }, { skillTypeId: 2, target: { type: 'auto' } }],
           firstTurnSkill: { skillTypeId: 3, target: { type: 'boss' } } },
    Lion: { prioritySkills: [{ skillTypeId: 1, target: { type: 'lowestHpAlly' } }, { skillTypeId: 1, isTransform: true, target: { type: 'self' } }] },
  } } };
  assert.deepEqual(ruleTargetMismatches(data, order, team, isCaster).map(item => `${item.skillTypeId}:${item.target}`),
    ['1:lowestHpAlly', '3:boss']);
});
