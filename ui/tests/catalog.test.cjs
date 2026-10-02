const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

// The message catalogs (src/i18n/messages/<locale>.json), the formatter and the locale registry.
function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '../src/i18n', file), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  new Function('require', 'exports', js)(require, exports);
  return exports;
}
const { formatMessage, messageArguments } = load('format.ts');
const { LOCALES, fallbackChain, matchLocale } = load('locales.ts');
const catalogs = Object.fromEntries(LOCALES.map(({ code }) => [code,
  JSON.parse(fs.readFileSync(path.join(__dirname, '../src/i18n/messages', `${code}.json`), 'utf8'))]));
const english = catalogs.en;
const CJK = /[一-鿿]/;

test('every offered language has a catalog file and no others exist', () => {
  const files = fs.readdirSync(path.join(__dirname, '../src/i18n/messages')).map((name) => name.replace(/\.json$/, '')).sort();
  assert.deepEqual(files, LOCALES.map(({ code }) => code).sort());
});

test('every language has every message, and nothing English lacks', () => {
  for (const [code, catalog] of Object.entries(catalogs)) {
    assert.deepEqual(Object.keys(english).filter((key) => !(key in catalog)), [], `${code} is missing messages`);
    assert.deepEqual(Object.keys(catalog).filter((key) => !(key in english)), [], `${code} has messages English lacks`);
  }
});

test('a message uses the same arguments in every language', () => {
  for (const [code, catalog] of Object.entries(catalogs)) {
    for (const [key, pattern] of Object.entries(catalog)) {
      assert.deepEqual(messageArguments(pattern), messageArguments(english[key]), `${code} ${key}`);
    }
  }
});

test('only the Chinese catalog holds Chinese text', () => {
  for (const [code, catalog] of Object.entries(catalogs)) {
    if (code === 'zh-CN') continue;
    assert.deepEqual(Object.entries(catalog).filter(([, text]) => CJK.test(text)).map(([key]) => key), [], code);
  }
});

test('groups built from data are complete', () => {
  for (const key of ['input', 'team', 'counter', 'provoke', 'activate', 'effect', 'passive', 'other']) {
    assert.ok(english[`chain.${key}`] && english[`chain.${key}.hint`], key);
  }
  for (const key of [0, 1, 2, 3]) assert.ok(english[`chimera.form.${key}`]);
  for (const key of [0, 1, 2, 3, 4, 5]) assert.ok(english[`boss.difficulty.${key}`]);
});

test('arguments, numbers, plural and select', () => {
  assert.equal(formatMessage('en', 'Cooldown {cooldown}', { cooldown: 3 }), 'Cooldown 3');
  assert.equal(formatMessage('pt-BR', 'Poder {power, number}', { power: 92000 }), 'Poder 92.000');
  const runs = '{count, plural, =0 {nenhuma luta} one {# luta} other {# lutas}}';
  assert.equal(formatMessage('pt-BR', runs, { count: 0 }), 'nenhuma luta');
  assert.equal(formatMessage('pt-BR', runs, { count: 1 }), '1 luta');
  assert.equal(formatMessage('pt-BR', runs, { count: 1500 }), '1.500 lutas');
  assert.equal(formatMessage('en', '{turns, plural, one {# turn} other {# turns}}', { turns: 2 }), '2 turns');
  const boss = 'No effect against the {boss, select, hydra {Hydra} other {Chimera}}';
  assert.equal(formatMessage('en', boss, { boss: 'hydra' }), 'No effect against the Hydra');
  assert.equal(formatMessage('en', boss, { boss: 'chimera' }), 'No effect against the Chimera');
  // A branch can hold arguments; an empty branch leaves nothing.
  const run = 'Run {index}{exact, select, true { ({original})} other {}} stopped';
  assert.equal(formatMessage('en', run, { index: 1, exact: true, original: 'Original' }), 'Run 1 (Original) stopped');
  assert.equal(formatMessage('en', run, { index: 2, exact: false, original: 'Original' }), 'Run 2 stopped');
  assert.equal(formatMessage('en', "The author's team", {}), "The author's team");
  assert.deepEqual(messageArguments(run), ['exact', 'index', 'original']);
});

// The same cases tools/test_ui_text.py runs against the backend's port of the formatter.
test('the shared formatter cases', () => {
  for (const [locale, pattern, params, expected] of JSON.parse(fs.readFileSync(path.join(__dirname, 'format-cases.json'), 'utf8')))
    assert.equal(formatMessage(locale, pattern, params), expected, `${locale} ${pattern}`);
});

test('names the backend gives and its decision labels read in each language; user names stay', () => {
  const { backendDataValue } = load('backendData.ts');
  const t = (locale, key, params) => formatMessage(locale, catalogs[locale][key], params);
  const label = '马里斯 · 默认技能顺序 · Ram形态首回合技能';
  assert.equal(backendDataValue(label, 'en', t), '马里斯 · Default skill order · First-turn skill in Ram form');
  assert.equal(backendDataValue(label, 'pt-BR', t), '马里斯 · Ordem padrão de habilidades · Habilidade do primeiro turno na forma Carneiro');
  assert.equal(backendDataValue('莉迪亚：维持必要效果', 'pt-BR', t), '莉迪亚: manter os efeitos necessários');
  assert.equal(backendDataValue('规则 3', 'pt-BR', t), 'Regra 3');
  assert.equal(backendDataValue('默认策略', 'en', t), 'Default Strategy');
  assert.equal(backendDataValue('速攻队', 'pt-BR', t), null);
});

test('rule names the editor generated in Chinese show their target in each language', () => {
  const { backendDataValue } = load('backendData.ts');
  const t = (locale, key, params) => formatMessage(locale, catalogs[locale][key], params);
  assert.equal(backendDataValue('黑羽缇塔斯 · 久经沙场 → 被斩首的蛇头', 'pt-BR', t), '黑羽缇塔斯 · 久经沙场 → Cabeça decapitada');
  // Names saved before the wording followed the game's (暴露蛇颈 → 被斩首的蛇头).
  assert.equal(backendDataValue('黑羽缇塔斯 · 久经沙场 → 暴露蛇颈', 'pt-BR', t), '黑羽缇塔斯 · 久经沙场 → Cabeça decapitada');
  assert.equal(backendDataValue('地窖守卫威克斯维尔 · 典籍防御 → 5 号位队友', 'en', t), '地窖守卫威克斯维尔 · 典籍防御 → Ally in slot 5');
  // Head names joined by arrows, and a decision label's detail after the target.
  assert.equal(backendDataValue('疯帽客 · 肮脏混合剂 → 按类型优先：苦痛之头 → 枯萎之头 → 愤怒之头…', 'pt-BR', t),
    '疯帽客 · 肮脏混合剂 → Por prioridade de tipo: 苦痛之头 → 枯萎之头 → 愤怒之头…');
  assert.equal(backendDataValue('胜利者佩洛普斯 · 格罗戈雅之祸 → 生命最低蛇头 · 首回合技能', 'pt-BR', t),
    '胜利者佩洛普斯 · 格罗戈雅之祸 → Cabeça com menos vida · Habilidade do primeiro turno');
  assert.equal(backendDataValue('马里斯 · 切换至变形形态 · 蜕变 → 蛇头类型优先 · 生命最低兜底', 'en', t),
    `马里斯 · ${catalogs.en['app.switchToAlternateForm']} · 蜕变 → ${catalogs.en['app.headTypePriorityLowestHp']}`);
  // A target the editor did not generate (a champion's name) stays as it is.
  assert.equal(backendDataValue('疯帽客 · 肮脏混合剂 → 黑羽缇塔斯', 'pt-BR', t), null);
});

test('every catalog message parses', () => {
  for (const [code, catalog] of Object.entries(catalogs)) {
    for (const [key, pattern] of Object.entries(catalog)) {
      assert.doesNotThrow(() => formatMessage(code, pattern, {}), `${code} ${key}`);
    }
  }
});

test('the system language picks the closest offered one; missing text falls back to English', () => {
  assert.equal(matchLocale(['pt-BR']), 'pt-BR');
  assert.equal(matchLocale(['pt-PT', 'en']), 'pt-BR');
  assert.equal(matchLocale(['zh-TW']), 'zh-CN');
  assert.equal(matchLocale(['fr-FR', 'de']), 'en');
  assert.deepEqual(fallbackChain('pt-BR'), ['pt-BR', 'en']);
  assert.deepEqual(fallbackChain('en'), ['en']);
});

// UI code keeps no tool text in Chinese. The exceptions are data values that
// arrive in Chinese (effect groups, the game's "蛇头 N" head names, the stored
// default strategy name), the language menu's own names, the recognizers of
// backend data (i18n/backendData.ts) and the 1.1.1 phrase
// table for old records.
test('UI code holds no Chinese tool text', () => {
  const allowed = [/\/\^蛇头\\s\*/, /增益: 'effect\.group\.buff'/, /group(\)?)? === '(增益|减益)'/, /group: '特殊'/,
    /name === '默认策略'/, /backendRuleName = .*`规则 /, /DATA_VALUES|^\s*\[\/\^/, /name: '简体中文'/, /aria-label="Language \/ 语言 \/ Idioma"/];
  const root = path.join(__dirname, '../src');
  const files = fs.readdirSync(root, { recursive: true }).map(String)
    .filter((file) => /\.tsx?$/.test(file) && !/(legacy|backendData)\.ts$/.test(file));
  assert.ok(files.length > 20);
  for (const file of files) {
    const source = fs.readFileSync(path.join(root, file), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const lines = source.split(/\r?\n/).map((line) => line.replace(/(^|\s)\/\/.*$/, ''))
      .filter((line) => CJK.test(line) && !allowed.some((pattern) => pattern.test(line)));
    assert.deepEqual(lines, [], file);
  }
});
