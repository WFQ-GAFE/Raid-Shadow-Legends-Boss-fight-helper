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
  './effectNames': {}, './HoverCard': {}, './TeamPreview': {}, './i18n': i18n,
});
const team = [101, 102, 103, 104, 105, 106];
const capture = id => ({ id, teamHeroTypeIds: team });
const complete = strategyId => ({ strategyId, accountReadable: false, strategyTeam: team,
  author: { heroTypeIds: team, offlineReady: true, savedAt: '2026-10-02 10:00:00' } });

test('an imported strategy selects its packaged opening and author team with the game closed', () => {
  const inputs = simulationInputs('en', 'imported', [capture('local'), capture('strategy-package:imported')], complete('imported'), team);
  assert.equal(inputs.captureId, 'strategy-package:imported');
  assert.equal(inputs.teamSource, 'author');
  assert.equal(inputs.teamReady, true);
  assert.match(inputs.teamOptions.find(option => option.source === 'author').note, /saved team and account bonuses/);
});

test('team sources and packaged openings from the previous strategy are excluded after a switch', () => {
  const inputs = simulationInputs('en', 'second', [capture('strategy-package:first'), capture('local')], complete('first'), team,
    { captureId: 'strategy-package:first', teamSource: 'author' });
  assert.deepEqual(inputs.captures.map(item => item.id), ['local']);
  assert.equal(inputs.captureId, 'local');
  assert.equal(inputs.teamSource, 'battle');
  assert.equal(inputs.sources, undefined);
  assert.equal(inputs.teamOptions.some(option => option.source === 'author'), false);
});

test('a removed or unavailable author source falls back to a usable source', () => {
  const sources = { ...complete('a'), author: { heroTypeIds: team, offlineReady: false } };
  const inputs = simulationInputs('en', 'a', [capture('local')], sources, team,
    { captureId: 'local', teamSource: 'author' });
  assert.equal(inputs.teamSource, 'battle');
  assert.equal(inputs.teamReady, true);
  assert.equal(teamSourceOptions('en', { ...sources, author: { heroTypeIds: team, offlineReady: true, matches: false } }, team, team)
    .find(option => option.source === 'author').ready, false);
});

test('a valid explicit selection remains selected when automatic data becomes available', () => {
  const inputs = simulationInputs('en', 'a', [capture('local'), capture('strategy-package:a')], complete('a'), team,
    { captureId: 'local', teamSource: 'battle' });
  assert.equal(inputs.captureId, 'local');
  assert.equal(inputs.teamSource, 'battle');
});

test('save feedback distinguishes a complete package from saved rules with missing data', () => {
  assert.match(simulationSaveText('zh-CN', { status: 'complete', team: true, opening: true, accountBonuses: true }, 'saved'), /已自动打包/);
  assert.match(simulationSaveText('en', { status: 'partial', reason: 'No battle opening', team: true, opening: false, accountBonuses: true }, 'saved'), /incomplete: No battle opening/);
  assert.match(simulationSaveText('pt-BR', { status: 'unavailable', team: false, opening: false, accountBonuses: false }, 'saved'), /incompletos/);
  assert.equal(simulationSaveText('en', undefined, 'Legacy save message'), 'Legacy save message');
});
