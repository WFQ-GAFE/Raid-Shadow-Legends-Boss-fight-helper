const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

// trialHeroMatch.ts with heroData's two id helpers (its only imports at run time).
const ascensionOf = (typeId) => { const level = typeId % 10; return level >= 1 && level <= 6 ? level : 0; };
const championId = (typeId) => typeId - ascensionOf(typeId);
function load(name) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  new Function('require', 'exports', js)((id) => id === './heroData' ? { ascensionOf, championId } : require(id), exports);
  return exports;
}
const { trialRecipe, championSkills, trialHeroReference } = load('trialHeroMatch.ts');

// Recipes as data/chimera-trial-recipes.json writes them (Ultra-Nightmare ids).
const recipes = [
  { trialId: 8000512, actionGoal: 'absorbDamage', requirements: [{ scope: 'activeHero', anyOf: [{ effectTypeId: 60 }, { effectTypeId: 320 }] }] },
  { trialId: 8000514, actionGoal: 'damageBoss', requirements: [{ scope: 'boss', anyOf: [{ effectTypeId: 170 }, { effectTypeId: 171 }] }] },
  { trialId: 8000521, actionGoal: 'applyDistinctEffects', requirements: [], minimumBossDebuffs: 9,
    difficultyOverrides: { 6: { minimumBossDebuffs: 10 } }, forbiddenBossEffectTypeIds: [290] },
];
// Champions as tools/hero_data.py keeps them: A3 of 9800 gives Unkillable from its 6th ascension only.
const data = {
  heroes: {
    9800: { forms: [{ skills: [98001, 98002] }], catalogForms: { 0: [[98001, 98002]], 6: [[98001, 98002, 98003]] } },
    7600: { forms: [{ skills: [76001] }] },
    4700: { forms: [{ skills: [47001] }] },
  },
  skills: {
    98001: { debuffs: [[170, 2, 50, 'target', false]] },
    98003: { cd: 5, buffs: [[320, 2, null, 'all', false]] },
    76001: { passive: true, buffs: [[60, 1, 30, 'self', true]] },
    47001: { debuffs: [[171, 2, 75, 'self', false]] },
  },
  statusEffects: {},
};
const roster = [
  { id: 3, typeId: 7600, grade: 6, level: 60, vault: true, reserve: false },
  { id: 1, typeId: 9806, grade: 6, level: 60, vault: false, reserve: false },
  { id: 2, typeId: 4700, grade: 5, level: 50, vault: false, reserve: false },
  { id: 4, typeId: 9800, grade: 3, level: 1, vault: false, reserve: false },
];

test('a trial on another difficulty uses the shared recipe and that difficulty\'s thresholds', () => {
  assert.equal(trialRecipe(recipes, 8000612).recipe.trialId, 8000512);
  assert.deepEqual(trialRecipe(recipes, 8000621).thresholds, { minimumBossDebuffs: 10 });
  assert.deepEqual(trialRecipe(recipes, 8000521).thresholds, { minimumBossDebuffs: 9 });
  assert.equal(trialRecipe(recipes, 8000610), null);
});

test('a champion\'s skills are those of its ascension level', () => {
  assert.deepEqual(championSkills(data, 9806), [98001, 98002, 98003]);
  assert.deepEqual(championSkills(data, 9800), [98001, 98002]);
  assert.deepEqual(championSkills(data, 7600), [76001]);
});

test('champions whose skills apply a needed effect are listed, team first, vaults last', () => {
  const [lionHard, lionTail, snakeHard, unknown] = trialHeroReference([8000612, 8000614, 8000621, 8000610], recipes, data, roster,
    { heroIds: [1], typeIds: [] });
  assert.deepEqual(lionHard.needs.map((need) => need.on), ['actor']);
  assert.deepEqual(lionHard.needs[0].providers.map((provider) => [provider.hero.id, provider.inTeam]), [[1, true], [3, false]]);
  assert.deepEqual(lionHard.needs[0].providers[0].skills, [
    { skillTypeId: 98003, effectTypeId: 320, turns: 2, chance: null, scope: 'all', conditional: false, cooldown: 5, passive: false }]);
  // A debuff only on its caster does not reach the Chimera; the unascended copy still has A1.
  assert.deepEqual(lionTail.needs[0].providers.map((provider) => provider.hero.id), [1, 4]);
  assert.deepEqual([snakeHard.needs, snakeHard.forbidden, snakeHard.thresholds], [[], [290], { minimumBossDebuffs: 10 }]);
  assert.equal(unknown.recipeFound, false);
});

test('a trial naming no effect lists the champions of any of its sources, hidden parts under their skill', () => {
  const sourced = [
    { trialId: 8000515, actionGoal: 'manual', requirements: [], sources: [{ buff: [50] }, { special: 'Counterattack' }, { special: 'TeamAttack' }] },
    { trialId: 8000502, actionGoal: 'periodicDamage', requirements: [], sources: [{ scaling: 'TRG_HP' }, { debuff: [80, 81] }] },
  ];
  const kit = {
    heroes: {
      4710: { forms: [{ skills: [47101, 47106] }] },
      5100: { forms: [{ skills: [51001, 51002] }] },
      6200: { forms: [{ skills: [62001] }] },
    },
    skills: {
      // Lydia: the passive counterattack is a hidden part; A1 says it uses itself.
      47101: { name: '压制', desc: '攻击1名敌人。【被动效果】每当敌人施放【恐惧】减益时，使用此技能攻击敌人。' },
      47106: { name: 'Skill 47106 name', hidden: true, passive: true, special: ['Counterattack'] },
      51001: { name: 'A1', scaling: ['ATK', 'TRG_HP'], debuffs: [[81, 2, 50, 'target', false]] },
      51002: { name: 'A2', buffs: [[50, 2, null, 'self', false]], special: ['TeamAttack'] },
      62001: { name: 'A1', debuffs: [[80, 2, 100, 'self', false]] },
    },
    statusEffects: { 50: { name: '反击' } },
  };
  const team = [
    { id: 1, typeId: 4710, grade: 6, level: 60, vault: false, reserve: false },
    { id: 2, typeId: 5100, grade: 6, level: 60, vault: false, reserve: false },
    { id: 3, typeId: 6200, grade: 6, level: 60, vault: false, reserve: false },
  ];
  const [counter, damage] = trialHeroReference([8000615, 8000602], sourced, kit, team, { heroIds: [], typeIds: [] });
  assert.deepEqual(counter.needs, []);
  assert.deepEqual(counter.sourceList, sourced[0].sources);
  assert.deepEqual(counter.sources.providers.map((provider) => provider.hero.id), [1, 2]);
  // Lydia's hidden counterattack is shown under A1; Nephilim's A2 gives the buff and an ally attack.
  assert.deepEqual(counter.sources.providers[0].skills.map((skill) => [skill.skillTypeId, skill.trait]), [[47101, 'special:Counterattack']]);
  assert.deepEqual(counter.sources.providers[1].skills.map((skill) => [skill.skillTypeId, skill.effectTypeId, skill.trait ?? '']),
    [[51002, 50, ''], [51002, 0, 'special:TeamAttack']]);
  // Enemy max HP damage and Poison on the enemy count; Poison only on the caster does not.
  assert.deepEqual(damage.sources.providers.map((provider) => provider.hero.id), [2]);
  assert.deepEqual(damage.sources.providers[0].skills.map((skill) => [skill.effectTypeId, skill.trait ?? '']), [[81, ''], [0, 'scaling:TRG_HP']]);
});

test('without saved hero ids the team is matched by champion', () => {
  const [reference] = trialHeroReference([8000612], recipes, data, roster, { heroIds: [], typeIds: [9800] });
  assert.deepEqual(reference.needs[0].providers.map((provider) => [provider.hero.id, provider.inTeam]), [[1, true], [3, false]]);
});
