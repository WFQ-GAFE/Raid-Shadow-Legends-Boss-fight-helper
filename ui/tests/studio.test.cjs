const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

function compile(name, overrides = {}, timerApi = {}) {
  const source = fs.readFileSync(path.join(__dirname, '../src', name + '.ts'), 'utf8');
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  new Function('require', 'exports', 'setTimeout', 'clearTimeout', js)(
    id => overrides[id] ?? require(id), exports, timerApi.setTimeout ?? setTimeout, timerApi.clearTimeout ?? clearTimeout);
  return exports;
}
const { DraftStore } = compile('draftStore');
const { RequestScope, mergeLogDelta } = compile('requestScope');

test('drafts survive Boss and profile changes, including returning to the first profile', () => {
  const drafts = new DraftStore();
  drafts.open('chimera:a', { rules: [] }, 'v1');
  drafts.edit('chimera:a', { rules: ['new rule'] });
  drafts.open('hydra:a', { rules: ['hydra'] }, 'h1');
  drafts.open('chimera:b', { rules: ['other'] }, 'v2');
  assert.deepEqual(drafts.open('chimera:a', { rules: [] }, 'v1').value.rules, ['new rule']);
});

test('a delayed save preserves edits made while the save was in flight', () => {
  const drafts = new DraftStore();
  drafts.open('hydra:a', { rules: [] }, 'v1');
  drafts.edit('hydra:a', { rules: ['first'] });
  const submittedGeneration = drafts.get('hydra:a').generation;
  drafts.edit('hydra:a', { rules: ['first', 'second'] });
  drafts.saved('hydra:a', submittedGeneration, { rules: ['first'] }, 'v2');
  assert.deepEqual(drafts.get('hydra:a').value.rules, ['first', 'second']);
  assert.equal(drafts.get('hydra:a').revision, 'v2');
  assert(drafts.dirty('hydra:a'));
});

test('saved responses update their own draft without selecting a different Boss', () => {
  const drafts = new DraftStore();
  drafts.open('chimera:a', { name: 'A' }, 'v1');
  drafts.edit('chimera:a', { name: 'A2' });
  const generation = drafts.get('chimera:a').generation;
  drafts.open('hydra:b', { name: 'B' }, 'v1');
  drafts.saved('chimera:a', generation, { name: 'A2' }, 'v2');
  assert.deepEqual(drafts.get('hydra:b').value, { name: 'B' });
  assert(!drafts.dirty('chimera:a'));
});

test('draft reload retains the original revision for conflict detection', () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const first = new DraftStore(storage);
  first.open('hydra:a', { rules: [] }, 'v1');
  first.edit('hydra:a', { rules: ['draft'] });
  const reloaded = new DraftStore(storage);
  const draft = reloaded.open('hydra:a', { rules: ['other window'] }, 'v2');
  assert.equal(draft.revision, 'v1');
  assert.deepEqual(draft.value.rules, ['draft']);
});

test('old requests remain obsolete even if their transport ignores cancellation', () => {
  const scope = new RequestScope();
  const old = scope.begin();
  scope.invalidate();
  const current = scope.begin();
  assert(old.signal.aborted);
  assert(!old.current());
  assert(current.current());
  old.finish(); current.finish();
});

test('incremental logs append once and support clear/reset', () => {
  const previous = { logs: ['one'], logCursor: 's:1' };
  const delta = { logs: ['two'], logCursor: 's:2', logsReset: false };
  const merged = mergeLogDelta(previous, delta);
  assert.deepEqual(merged.logs, ['one', 'two']);
  assert.deepEqual(mergeLogDelta(merged, delta).logs, ['one', 'two']);
  assert.deepEqual(mergeLogDelta(merged, { logs: [], logCursor: 'next:0', logsReset: true }).logs, []);
});

test('actual polling hook serializes requests and discards a disposed response', async () => {
  const timers = new Map();
  let timerId = 0, cleanup, resolve, requests = 0;
  const timerApi = { setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id) };
  const { RequestScope: Scope } = compile('requestScope', {}, timerApi);
  const { useSerialPoll } = compile('useSerialPoll', { react: { useEffect: effect => { cleanup = effect(); } }, './requestScope': { RequestScope: Scope } }, timerApi);
  const received = [];
  const scope = new Scope();
  useSerialPoll(true, 'chimera:1', scope, () => {
    requests++;
    return new Promise(done => { resolve = done; });
  }, next => received.push(next), error => { throw error; });
  const [id, tick] = [...timers][0]; timers.delete(id);
  const pending = tick();
  assert.equal(requests, 1);
  assert.equal(timers.size, 1); // Only the request timeout; no overlapping poll.
  cleanup();
  resolve({ bossMode: 'chimera' });
  await pending;
  assert.deepEqual(received, []);
  assert.equal(timers.size, 0);
});
