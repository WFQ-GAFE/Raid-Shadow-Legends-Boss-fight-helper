const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const React = require('react');
const { renderToString } = require('react-dom/server');
function load(name) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name + '.tsx'), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const exports = {};
  new Function('require', 'exports', js)(require, exports);
  return exports;
}
const { DecisionRows } = load('DecisionRows');
const legacyReservation = { rule: '第一总督阿莫科 · 遗产守护者 → 自己', matched: false,
  reason: 'skill_reserved_for_trial_rule', skillTypeId: 98903,
  reservedByRules: ['第一总督阿莫科 · 遗产守护者 → 自己'] };

test('the logged reservation row reproduces the old crash and renders with the fix', () => {
  assert.throws(() => legacyReservation.conditions.map(check => check.key), TypeError);
  const html = renderToString(React.createElement(DecisionRows, { rows: [legacyReservation] }));
  assert.match(html, /为试炼专用规则保留技能/);
  assert.match(html, /遗产守护者/);
});

test('diagnostics tolerate absent/malformed rows and still render valid conditions', () => {
  for (const rows of [undefined, null, {}, [null, 'old', {}, { conditions: {} }]]) {
    assert.doesNotThrow(() => renderToString(React.createElement(DecisionRows, { rows })));
  }
  const html = renderToString(React.createElement(DecisionRows, { rows: [legacyReservation,
    { index: 2, name: 'ready-rule', outcome: 'selected', conditions: [null, { key: 'form', passed: true }] }] }));
  assert.match(html, /已选中/);
  assert.match(html, /✓ form/);
});

test('the root error fallback remains visible and offers recovery and pause controls', () => {
  const { UiErrorBoundary } = load('UiErrorBoundary');
  const boundary = new UiErrorBoundary({ children: null });
  boundary.state = { failed: true, status: '' };
  const html = renderToString(boundary.render());
  assert.match(html, /界面显示出现异常/);
  assert.match(html, /恢复界面/);
  assert.match(html, /暂停接管/);
});
