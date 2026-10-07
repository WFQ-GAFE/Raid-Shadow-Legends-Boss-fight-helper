const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

function load(name, extension = '.tsx', modules = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name + extension), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  new Function('require', 'exports', js)(id => modules[id] ?? require(id), exports);
  return exports;
}
const { formatMessage } = load('i18n/format', '.ts');
const catalogs = Object.fromEntries(['en', 'zh-CN', 'pt-BR'].map(locale => [locale,
  JSON.parse(fs.readFileSync(path.join(__dirname, '../src/i18n/messages', locale + '.json'), 'utf8'))]));
const i18n = {
  translate: (lang, key, params) => formatMessage(lang, catalogs[lang][key], params),
  backendText: text => String(text ?? ''),
  hasMessage: key => key in catalogs.en,
};
const { simulationInputs, simulationSaveText, teamSourceOptions } = load('SimulationShared', '.tsx', {
  './effectNames': {}, './HoverCard': {}, './TeamPreview': {}, './EffectSearch': {}, './TargetNote': {}, './i18n': i18n,
});
const team = [101, 102, 103, 104, 105, 106];
const capture = id => ({ id, teamHeroTypeIds: team });
const complete = strategyId => ({ strategyId, accountReadable: false, strategyTeam: team,
  author: { heroTypeIds: team, offlineReady: true, savedAt: '2026-10-02 10:00:00' } });

test('a simulation builds on the newest saved battle, else on the one saved with the strategy', () => {
  // Captures arrive newest first; the strategy group's packaged battle is listed first by the server.
  const inputs = simulationInputs('en', 'imported', [capture('strategy-package:imported'), capture('newest'), capture('older')],
    complete('imported'), team);
  assert.equal(inputs.captureId, 'newest');
  assert.equal(inputs.teamSource, 'author');
  assert.equal(inputs.teamReady, true);
  assert.match(inputs.teamOptions.find(option => option.source === 'author').note, /saved team and account bonuses/);
  // A new computer with only the imported strategy: its own saved battle.
  assert.equal(simulationInputs('en', 'imported', [capture('strategy-package:imported')], complete('imported'), team).captureId,
    'strategy-package:imported');
});

test('the team always comes from the strategy group, never from the saved battle', () => {
  const options = teamSourceOptions('en', complete('a'), team);
  assert.deepEqual(options.map(option => option.source), ['current', 'strategy', 'author']);
});

test('team sources and saved battles of the previous strategy are excluded after a switch', () => {
  const inputs = simulationInputs('en', 'second', [capture('strategy-package:first')], complete('first'), team,
    { teamSource: 'author' });
  assert.equal(inputs.capture, undefined);
  assert.equal(inputs.sources, undefined);
  assert.equal(inputs.teamSource, 'current');
  assert.equal(inputs.teamReady, false);
  assert.equal(inputs.teamOptions.some(option => option.source === 'author'), false);
});

test('the saved team simulates with the game closed when its account bonuses were saved too', () => {
  const saved = { heroTypeIds: team, savedAt: '2026-10-07 02:00:00', matches: true };
  const closed = { ...complete('a'), author: { heroTypeIds: team, offlineReady: false }, strategy: saved };
  const inputs = simulationInputs('en', 'a', [capture('local')], { ...closed, strategy: { ...saved, offlineReady: true } }, team,
    { teamSource: 'author' });
  assert.equal(inputs.teamSource, 'strategy');
  assert.equal(inputs.teamReady, true);
  assert.match(inputs.teamOptions.find(option => option.source === 'strategy').note, /Saved team and account bonuses/);
  // Without the saved bonuses it needs the game.
  assert.equal(teamSourceOptions('en', closed, team).find(option => option.source === 'strategy').ready, false);
  assert.equal(teamSourceOptions('en', { ...closed, author: { heroTypeIds: team, offlineReady: true, matches: false } }, team)
    .find(option => option.source === 'author').ready, false);
});

test('a valid explicit selection remains selected when automatic data becomes available', () => {
  const open = { ...complete('a'), accountReadable: true, strategy: { heroTypeIds: team, matches: true } };
  const inputs = simulationInputs('en', 'a', [capture('local'), capture('strategy-package:a')], open, team, { teamSource: 'strategy' });
  assert.equal(inputs.captureId, 'local');
  assert.equal(inputs.teamSource, 'strategy');
});

test('save feedback distinguishes a complete package from saved rules with missing data', () => {
  assert.match(simulationSaveText('zh-CN', { status: 'complete', team: true, opening: true, accountBonuses: true }, 'saved'), /已自动打包/);
  assert.match(simulationSaveText('en', { status: 'partial', reason: 'No battle opening', team: true, opening: false, accountBonuses: true }, 'saved'), /incomplete: No battle opening/);
  assert.match(simulationSaveText('pt-BR', { status: 'unavailable', team: false, opening: false, accountBonuses: false }, 'saved'), /incompletos/);
  assert.equal(simulationSaveText('en', undefined, 'Legacy save message'), 'Legacy save message');
});

test('a picked trial keeps the turns of its form until it was completed', () => {
  const { trialTurnScope } = load('SimulationShared', '.tsx', {
    './effectNames': {}, './HoverCard': {}, './TeamPreview': {}, './EffectSearch': {}, './TargetNote': {}, './i18n': i18n,
  });
  // 8000609: n = 9 → Ram (form 1), trial 3, hard; 8000618: n = 18 → Lion (form 2).
  // The run's forms: Ultimate (0) between the others, as the Chimera rotates.
  const rows = [[0, 5], [1, 6], [1, 10], [0, 11], [0, 15], [2, 16], [0, 21], [0, 35], [0, 35], [1, 36], [0, 41]]
    .map(([form, bossTurns]) => ({ form, bossTurns }));
  const turns = scope => rows.filter(scope).map(row => `${row.form}:${row.bossTurns}`);
  // Ram's turns and the Ultimate turn right before each Ram window (its trials already progress there).
  assert.deepEqual(turns(trialTurnScope(rows, undefined, 8000609, undefined)), ['0:5', '1:6', '1:10', '0:35', '0:35', '1:36']);
  // Until it was completed.
  assert.deepEqual(turns(trialTurnScope(rows, undefined, 8000609, 10)), ['0:5', '1:6', '1:10']);
  assert.deepEqual(turns(trialTurnScope(rows, undefined, 8000618, undefined)), ['0:15', '2:16']);
  // The game's own form for a trial wins over the one its id implies; an unknown form keeps every turn.
  assert.deepEqual(turns(trialTurnScope(rows, { id: 8000609, form: 'Lion' }, 8000609, undefined)), ['0:15', '2:16']);
  assert.equal(trialTurnScope(rows, undefined, 9999999, undefined), null);
});

test('a picked champion also sees the boss actions that reached it', () => {
  const { enemyReached } = load('SimulationShared', '.tsx', {
    './effectNames': {}, './HoverCard': {}, './TeamPreview': {}, './EffectSearch': {}, './TargetNote': {}, './i18n': i18n,
  });
  // 1, 2: the team; 10: a boss head.
  const byId = new Map([[1, { actorId: 1, heroTypeId: 101, player: true }], [2, { actorId: 2, heroTypeId: 102, player: true }],
    [10, { actorId: 10, heroTypeId: 900, player: false }]]);
  const picked = new Set([1]);
  const use = (actorId, targetId, trigger, hits) => ({ actorId, skillTypeId: 1, targetId, trigger, damage: 0, hits });
  const row = (source, actorId, targetId, uses) => ({ turn: 1, actorId, actorTypeId: 0, source, skillTypeId: 1, targetId, damage: 0, deaths: [], uses });
  // The command named someone else, an area skill hit both.
  assert.equal(enemyReached(row('enemy', 10, 2, [use(10, 2, 'input', [2, 1])]), picked, byId), true);
  // A skill that chose its own target: what it hit counts, not the command.
  assert.equal(enemyReached(row('enemy', 10, 1, [use(10, 1, 'input', [2])]), picked, byId), false);
  // No damage to go by: the command's target.
  assert.equal(enemyReached(row('enemy', 10, 1, [use(10, 1, 'input')]), picked, byId), true);
  assert.equal(enemyReached(row('enemy', 10, 1, undefined), picked, byId), true);
  // A counterattack on another champion's turn; the team's own skills never count.
  assert.equal(enemyReached(row('policy', 2, 10, [use(2, 10, 'input', [10]), use(10, 1, 'counter', [1])]), picked, byId), true);
  assert.equal(enemyReached(row('policy', 2, 1, [use(2, 1, 'input')]), picked, byId), false);
  assert.equal(enemyReached(row('policy', 2, 1, undefined), picked, byId), false);
  assert.equal(enemyReached(row('enemy', 10, 1, undefined), new Set(), byId), false);
});

test('the action log groups strength variants of an effect and looks for buffs on the actor, debuffs on the target', () => {
  const { effectGroup, defaultScope } = load('SimulationShared', '.tsx', {
    './effectNames': { effectName: option => option.label }, './HoverCard': {}, './TeamPreview': {}, './EffectSearch': {}, './TargetNote': {}, './i18n': i18n,
  });
  const effects = new Map([
    ['120', { token: '120', icon: 'StatusIncreaseAttack', label: '增加攻击 25%', group: '增益' }],
    ['121', { token: '121', icon: 'StatusIncreaseAttack2', label: '增加攻击 50%', group: '增益' }],
    ['310', { token: '310', icon: 'ShareDamage', label: '队友保护（50%）', group: '增益' }],
    ['81', { token: '81', icon: 'ContinuousDamage2', label: 'Veneno (2,5%)', group: '减益' }],
    ['480', { token: '480', icon: 'Invisible', label: '隐身', group: '增益' }],
    ['481', { token: '481', icon: 'Invisible2', label: '完美隐身', group: '增益' }],
  ]);
  assert.equal(effectGroup(effects, 'zh-CN', 120), effectGroup(effects, 'zh-CN', 121));
  assert.equal(effectGroup(effects, 'zh-CN', 120), '增加攻击');
  assert.equal(effectGroup(effects, 'zh-CN', 310), '队友保护');
  assert.equal(effectGroup(effects, 'pt-BR', 81), 'Veneno');
  // Veil and Perfect Veil are different effects, not strengths of one.
  assert.notEqual(effectGroup(effects, 'zh-CN', 480), effectGroup(effects, 'zh-CN', 481));
  assert.equal(effectGroup(effects, 'zh-CN', 999), '#999');
  assert.equal(defaultScope(effects, 120), 'actor');
  assert.equal(defaultScope(effects, 81), 'target');
});
