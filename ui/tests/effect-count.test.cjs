const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

// Exercise the actual editor's load/save functions, including the recursive
// mixed tree; a model of the serializer would miss disappearing conditions.
const source = fs.readFileSync(path.join(__dirname, '../src/App.tsx'), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const names = new Set(['createEffectCondition', 'createSkillCooldownCondition', 'createConditionEffect',
  'createConditionCooldown', 'createConditionHeroState', 'createConditionEffectCount',
  'createConditionGroup', 'hydrateConditionTree', 'serializeConditionNode', 'effectPickerSelection']);
const parts = [];
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name && names.has(node.name.text)) parts.push(node.getText(ast));
  ts.forEachChild(node, visit);
}
visit(ast);
assert.equal(parts.length, names.size);
const prelude = `let effectConditionSequence=0, skillCooldownConditionSequence=0,
  heroStateConditionSequence=0, conditionGroupSequence=0, effectCountSequence=0;
  const team=[4716,4716,8736],teamSize=3;`;
const js = ts.transpileModule(prelude + parts.join('\n') + '\nreturn {hydrateConditionTree,serializeConditionNode,createConditionEffectCount,effectPickerSelection};',
  {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText;
const ui = new Function(js)();

test('each count scope and target survives save and reopen inside mixed AND/OR/NOT groups', () => {
  for (const polarity of ['all','buff','debuff']) {
    for (const target of ['boss','bossAll','bossAny','bossPriority','ally']) {
      const leaf = {type:'effectCount',target,polarity,countAtLeast:10,countAtMost:10,negate:true,
        ...(target === 'ally' ? {heroTypeId:4716,teamPosition:2} : {})};
      const tree = {type:'group',operator:'all',children:[leaf,{type:'group',operator:'any',children:[
        {type:'effect',target:'boss',presence:'missing',effect:{effectTypeId:290}},
        {type:'effectCount',target:'boss',polarity:'buff',countAtMost:0}]}]};
      assert.deepEqual(ui.serializeConditionNode(ui.hydrateConditionTree(tree)),tree);
    }
  }
});

test('blank, reversed, negative, fractional and wrong-team inputs cannot silently change the rule', () => {
  for (const changes of [{countAtLeast:'',countAtMost:''}, {countAtLeast:'11',countAtMost:'10'},
    {countAtLeast:'-1'}, {countAtLeast:'1.5'}, {target:'ally',heroTypeId:'999',teamPosition:'1'},
    {target:'ally',heroTypeId:'4716',teamPosition:'3'}]) {
    assert.throws(() => ui.serializeConditionNode(ui.createConditionEffectCount(changes)));
  }
  assert.throws(() => ui.serializeConditionNode({type:'effect',token:''}));
});

test('saved kind predicates and unknown effects are never displayed as an empty selection', () => {
  const effects = [{token:'130',icon:'StatusReduceAttack',label:'降低攻击 25%',labelEn:'Decrease Attack 25%',group:'减益'},
    {token:'470',icon:'AoEContinuousDamage',label:'生命值燃烧',labelEn:'HP Burn',group:'减益'}];
  const kind = ui.effectPickerSelection(effects,'StatusReduceAttack');
  assert.equal(kind.token,'StatusReduceAttack');
  assert.equal(kind.label,'降低攻击（按效果种类）');
  assert(!kind.labelEn.includes('25%'));
  assert.equal(ui.effectPickerSelection(effects,'AoEContinuousDamage').label,'生命值燃烧（按效果种类）');
  assert.equal(ui.effectPickerSelection(effects,'130'),effects[0]);
  assert.equal(ui.effectPickerSelection(effects,'unknown-saved-kind').token,'unknown-saved-kind');
  assert.equal(ui.effectPickerSelection(effects,''),undefined);
});
