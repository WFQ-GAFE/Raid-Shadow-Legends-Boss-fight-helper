const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../src/logReading.ts'), 'utf8');
const exportsObject = {};
new Function('exports', ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText)(exportsObject);
const {LogReadingState, addedLogEntries} = exportsObject;

test('rolling repeated messages use cursor counts while preserving the reading snapshot', () => {
  const reader = new LogReadingState();
  reader.update(['same', 'same'], 'run:hydra:0:2');
  reader.follow(false);
  reader.scrollTop = 75;
  reader.update(['same', 'same'], 'run:hydra:0:7');
  assert.deepEqual(reader.visible, ['same', 'same']);
  assert.equal(reader.unread, 5);
  assert.equal(reader.scrollTop, 75);
  reader.update(['same', 'same'], 'run:hydra:0:7');
  assert.equal(reader.unread, 5);
});
test('resume adopts the latest buffer, while a new epoch does not move a paused reader', () => {
  const reader = new LogReadingState();
  reader.update(['old'], 'first:0:1'); reader.follow(false);
  reader.update(['new', 'latest'], 'second:0:2');
  assert.deepEqual(reader.visible, ['old']); assert.equal(reader.unread, 2);
  reader.follow(true);
  assert.deepEqual(reader.visible, ['new', 'latest']); assert.equal(reader.unread, 0);
});
test('clearing logs resets unread state and restores initial following', () => {
  const reader = new LogReadingState();
  reader.update(['old'], 'run:0:1'); reader.follow(false);
  reader.update(['old', 'new'], 'run:0:2');
  reader.update([], 'run:1:0');
  assert.equal(reader.following, true); assert.equal(reader.unread, 0); assert.deepEqual(reader.visible, []);
});
test('legacy buffers use suffix overlap for appended and trimmed entries', () => {
  assert.equal(addedLogEntries(['a', 'b', 'c'], ['b', 'c', 'd']), 1);
  assert.equal(addedLogEntries(['a', 'b'], ['a', 'b']), 0);
  assert.equal(addedLogEntries(['a'], ['different']), 1);
});
