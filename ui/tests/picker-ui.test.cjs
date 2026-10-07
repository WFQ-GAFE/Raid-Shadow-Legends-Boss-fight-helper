const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../node_modules/typescript');

// Run the real component event handlers with a small hook/host adapter. This
// deliberately avoids a browser or live/game API; rendering and visual QA are
// performed separately with the isolated UI fixture.
function harness(file, exportName, initialProps) {
  const hooks = [];
  let cursor = 0;
  let pendingEffects = [];
  let props = initialProps;
  let tree;
  let locale = props.language || 'en';
  let translate = makeTranslate(locale);
  const same = (left, right) => left && right && left.length === right.length && left.every((item, i) => Object.is(item, right[i]));
  const memo = (factory, deps) => {
    const index = cursor++;
    if (!hooks[index] || !same(hooks[index].deps, deps)) hooks[index] = { deps, value: factory() };
    return hooks[index].value;
  };
  const effect = (callback, deps) => {
    const index = cursor++;
    if (!hooks[index] || !same(hooks[index].deps, deps)) {
      const previous = hooks[index];
      hooks[index] = { deps, cleanup: previous?.cleanup };
      pendingEffects.push(() => { hooks[index].cleanup?.(); hooks[index].cleanup = callback(); });
    }
  };
  const react = {
    memo: value => value, useMemo: memo, useCallback: (callback, deps) => memo(() => callback, deps),
    useDeferredValue: value => value, useEffect: effect, useLayoutEffect: effect,
    useRef: value => memo(() => ({ current: value }), []), useId: () => memo(() => `picker-test-${cursor}`, []),
    useState(value) {
      const index = cursor++;
      if (!hooks[index]) hooks[index] = { value: typeof value === 'function' ? value() : value };
      return [hooks[index].value, next => { hooks[index].value = typeof next === 'function' ? next(hooks[index].value) : next; }];
    },
  };
  const modules = new Map();
  const jsx = (type, props, key) => ({ type, props: props || {}, key });
  const i18n = {
    useI18n: () => ({ t: translate, locale }), translate: (language, key, params) => makeTranslate(language)(key, params),
    activeLanguage: () => locale, backendText: value => String(value),
    hasMessage: key => Object.hasOwn(catalogs.en, key),
  };
  const icons = new Proxy({}, { get: (_, name) => `icon:${name}` });
  const dialog = new Proxy({}, { get: (_, name) => `dialog:${name}` });
  function load(name) {
    if (modules.has(name)) return modules.get(name);
    const source = fs.readFileSync(path.join(__dirname, '../src', name), 'utf8');
    const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    const exports = {};
    modules.set(name, exports);
    const dependency = id => {
      if (id === 'react') return react;
      if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
      if (id === 'react-dom') return { createPortal: child => child };
      if (id === '@radix-ui/react-dialog') return dialog;
      if (id === 'lucide-react') return icons;
      if (id === './i18n') return i18n;
      if (id === './HeroProfile' && name !== 'HeroProfile.tsx') return { HeroProfile: 'HeroProfile' };
      if (id === './EffectSearch' && name !== 'EffectSearch.tsx') return { EffectSearch: 'EffectSearch', EffectGlyph: 'EffectGlyph' };
      const relative = path.posix.normalize(path.posix.join(path.posix.dirname(name), id));
      if (fs.existsSync(path.join(__dirname, '../src', `${relative}.ts`))) return load(`${relative}.ts`);
      if (fs.existsSync(path.join(__dirname, '../src', `${relative}.tsx`))) return load(`${relative}.tsx`);
      return require(id);
    };
    new Function('require', 'exports', javascript)(dependency, exports);
    return exports;
  }
  const Component = load(file)[exportName];
  return {
    load,
    render(nextProps) {
      if (nextProps) props = { ...props, ...nextProps };
      if (props.language && props.language !== locale) { locale = props.language; translate = makeTranslate(locale); }
      cursor = 0;
      tree = Component(props);
      return tree;
    },
    flushEffects() { const work = pendingEffects; pendingEffects = []; work.forEach(callback => callback()); },
    find(predicate) { return flatten(tree).find(predicate); },
    findAll(predicate) { return flatten(tree).filter(predicate); },
    dispose() { hooks.forEach(hook => hook.cleanup?.()); },
  };
}
const catalogs = Object.fromEntries(['en', 'zh-CN', 'pt-BR'].map(locale => [locale,
  JSON.parse(fs.readFileSync(path.join(__dirname, `../src/i18n/messages/${locale}.json`), 'utf8'))]));
function makeTranslate(locale) {
  return (key, params = {}) => (catalogs[locale][key] || catalogs.en[key] || key).replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? `{${name}}`));
}
function flatten(node) {
  if (!node || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap(flatten);
  return [node, ...flatten(node.props?.children)];
}
const byClass = name => node => node.props.className?.split(' ').includes(name);
const event = (key, target) => ({ key, target, prevented: false, stopped: false,
  preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } });
function hostEnvironment() {
  const originalWindow = global.window;
  const originalDocument = global.document;
  const listeners = new Map();
  const documentListeners = new Map();
  const register = (map, name, callback) => { const set = map.get(name) || new Set(); set.add(callback); map.set(name, set); };
  global.window = { innerWidth: 800, innerHeight: 600,
    addEventListener: (name, callback) => register(listeners, name, callback),
    removeEventListener: (name, callback) => listeners.get(name)?.delete(callback) };
  global.document = { activeElement: null, body: {},
    addEventListener: (name, callback) => register(documentListeners, name, callback),
    removeEventListener: (name, callback) => documentListeners.get(name)?.delete(callback) };
  return {
    dispatch(name, value) { for (const callback of listeners.get(name) || []) { callback(value); if (value.stopped) break; } },
    restore() { global.window = originalWindow; global.document = originalDocument; },
  };
}
const heroes = [
  { id: 1, typeId: 101, grade: 6, rarity: 5, level: 60, vault: false, reserve: false },
  { id: 2, typeId: 201, grade: 5, rarity: 4, level: 50, vault: false, reserve: false },
  { id: 3, typeId: 301, grade: 4, rarity: 3, level: 40, vault: true, reserve: false },
  { id: 4, typeId: 401, grade: 6, rarity: 5, level: 60, vault: false, reserve: true },
  { id: 5, typeId: 102, grade: 6, rarity: 5, level: 60, vault: false, reserve: false },
];
const data = { heroes: {
  100: { faction: 'HighElves', forms: [{ element: 'Magic', role: 'Attack', skills: [1], stats: [] }], aura: [{ stat: 'Speed', value: 10 }] },
  200: { faction: 'HighElves', forms: [{ element: 'Force', role: 'Attack', skills: [2], stats: [] }] },
  300: { faction: 'SacredOrder', forms: [{ element: 'Spirit', role: 'Support', skills: [3], stats: [] }] },
  400: { faction: 'SacredOrder', forms: [{ element: 'Void', role: 'Support', skills: [4], stats: [] }] },
}, skills: { 1: { scaling: ['ATK'], special: ['Heal'], buffs: [[280, 2, 100, 'all', false]] }, 2: { scaling: ['ATK'] }, 3: { special: ['Heal'] }, 4: {} },
  statusEffects: { 280: { kind: 'Shield', name: 'Shield' } } };
const effects = [{ token: '280', icon: 'Shield', iconReady: true, label: '护盾', labelEn: 'Shield' }];
async function picker(initialIds = [1, 2, 3]) {
  const applied = [];
  const h = harness('TeamPicker.tsx', 'TeamPicker', { language: 'en', open: true, teamSize: 3, bossMode: 'chimera',
    initial: { heroInstanceIds: initialIds, heroTypeIds: initialIds.map(id => heroes.find(hero => hero.id === id)?.typeId) },
    loadRoster: async () => heroes, loadHeroData: async () => data, effects,
    heroName: typeId => `Champion ${typeId}`, heroAvatar: () => 'avatar', onClose() {}, onApply: value => applied.push(value) });
  h.render(); h.flushEffects(); await Promise.resolve(); h.render(); h.flushEffects(); h.render();
  h.applied = applied;
  return h;
}
const slotIds = h => h.findAll(byClass('picker-slot-profile')).map(node => Number(node.props['aria-label'].match(/Champion (\d+)/)[1]));
// Roster cards only: the team slots' avatars carry a hero too.
const candidates = h => h.findAll(node => typeof node.type === 'function' && node.props.hero && typeof node.props.replaceable === 'boolean');

test('removing the leader and dragging out a champion keep preview, aura and submitted team aligned', async () => {
  const h = await picker();
  const removeLeader = h.findAll(node => node.type === 'button' && node.props['aria-label']?.startsWith('Remove Champion'))[0];
  removeLeader.props.onClick(); h.render();
  assert.deepEqual(slotIds(h), [201, 301]);
  assert.equal(h.find(byClass('picker-aura')).props.children[1], makeTranslate('en')('picker.hasNoLeaderAura', { typeId: 'Champion 201' }));
  h.find(node => node.type === 'button' && node.props.className === 'button primary').props.onClick();
  assert.deepEqual(h.applied[0], { heroTypeIds: [201, 301], heroInstanceIds: [2, 3] });
  const firstSlot = h.findAll(byClass('picker-slot'))[0];
  firstSlot.props.onDragStart({ dataTransfer: { setData() {} }, nativeEvent: { defaultPrevented: true } });
  h.find(byClass('team-picker-list')).props.onDrop({ preventDefault() {} }); h.render();
  assert.deepEqual(slotIds(h), [301]);
  h.dispose();
});

test('initial missing heroes are compacted before displaying the leader', async () => {
  const h = await picker([999, 2, 3]);
  assert.deepEqual(slotIds(h), [201, 301]);
  h.dispose();
});

test('a full team keeps candidates readable and replaces a member by dragging; a click only says so', async () => {
  const h = await picker();
  h.find(node => node.type === 'button' && node.props.children === 'Reset all filters').props.onClick(); h.render();
  const dragOnto = (id, index) => {
    const candidate = candidates(h).find(node => node.props.hero.id === id);
    candidate.type(candidate.props).props.onDragStart({ dataTransfer: { setData() {} }, nativeEvent: { defaultPrevented: false } });
    h.findAll(byClass('picker-slot'))[index].props.onDrop({ preventDefault() {} }); h.render();
  };
  let candidate = candidates(h).find(node => node.props.hero.id === 4);
  const card = candidate.type(candidate.props);
  assert.ok(candidate.props.replaceable && !card.props.className.includes('blocked'));
  // No replace-a-slot controls: dragging is the way to replace.
  assert.ok(!flatten(card).some(node => node.type === 'select'));
  assert.ok(!h.findAll(byClass('picker-slot-replace')).length && !h.findAll(byClass('picker-replacement')).length);
  candidate.props.onClick(candidate.props.hero, { detail: 1 }); h.render();
  assert.deepEqual(slotIds(h), [101, 201, 301]);
  assert.equal(h.find(byClass('picker-hint')).props.children, makeTranslate('en')('picker.theTeamIsFullDrag'));
  dragOnto(4, 1);
  assert.deepEqual(slotIds(h), [101, 401, 301]);
  // Another copy of a champion in the team goes only into that champion's own slot.
  dragOnto(5, 2);
  assert.deepEqual(slotIds(h), [101, 401, 301]);
  dragOnto(5, 0);
  assert.deepEqual(slotIds(h), [102, 401, 301]);
  h.dispose();
});

test('switching English to Portuguese updates effect options and selected condition labels', async () => {
  const h = await picker();
  let search = h.find(node => node.type === 'EffectSearch');
  assert.equal(search.props.options.find(option => option.key === 'buff:Shield').label, 'Shield');
  search.props.onPick('buff:Shield'); h.render();
  h.render({ language: 'pt-BR' });
  search = h.find(node => node.type === 'EffectSearch');
  assert.equal(search.props.options.find(option => option.key === 'buff:Shield').label, catalogs['pt-BR']['effect.280']);
  const selected = h.findAll(byClass('picker-filter-group')).find(node => node.props.children[0].props.children === catalogs['pt-BR']['picker.skillConditions']);
  assert.ok(selected.props.children[2][0].props.children[0].includes(catalogs['pt-BR']['effect.280']));
  h.dispose();
});

test('reset includes all star ranks and locations; affinity/role groups use OR and skill conditions use AND', async () => {
  const h = await picker();
  assert.equal(candidates(h).length, 3);
  const reset = () => { h.find(node => node.type === 'button' && node.props.children === 'Reset all filters').props.onClick(); h.render(); };
  reset(); assert.equal(candidates(h).length, 5);
  const chip = label => h.find(node => node.type === 'button' && node.props.className?.startsWith('chip ') && node.props.children === label);
  chip(catalogs.en['hero.role.Attack']).props.onClick(); h.render();
  chip(catalogs.en['hero.role.Support']).props.onClick(); h.render();
  assert.equal(candidates(h).length, 5);
  // Affinity chips show a colour dot and the name (a fragment).
  const textOf = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(textOf).join('')
    : node && typeof node === 'object' ? textOf(node.props?.children) : '';
  const affinityChip = label => h.find(node => node.type === 'button' && node.props.className?.startsWith('chip ') && textOf(node.props.children) === label);
  affinityChip(catalogs.en['hero.element.Magic']).props.onClick(); h.render();
  affinityChip(catalogs.en['hero.element.Force']).props.onClick(); h.render();
  assert.deepEqual(candidates(h).map(node => node.props.hero.id), [1, 2, 5]);
  const search = h.find(node => node.type === 'EffectSearch');
  search.props.onPick('scaling:ATK'); h.render();
  h.find(node => node.type === 'EffectSearch').props.onPick('special:Heal'); h.render();
  assert.deepEqual(candidates(h).map(node => node.props.hero.id), [1, 5]);
  h.find(node => node.type === 'input').props.onChange({ target: { value: 'no such champion' } }); h.render();
  assert.ok(h.find(byClass('picker-empty-results')));
  reset(); assert.equal(candidates(h).length, 5);
  h.dispose();
});

test('effect search consumes one Escape, clamps empty/shrinking lists, and scrolls the active option into view', () => {
  const environment = hostEnvironment();
  const picked = [];
  const options = Array.from({ length: 70 }, (_, index) => ({ key: `${index}`, label: `Effect ${index}`, kind: 'Buff' }));
  const h = harness('EffectSearch.tsx', 'EffectSearch', { options: [], chosen: [], placeholder: 'Find effects', empty: 'No match', onPick: key => picked.push(key) });
  h.render();
  const inputTarget = {};
  h.find(byClass('effect-search')).props.ref.current = { contains: target => target === inputTarget };
  h.find(node => node.type === 'input').props.onFocus(); h.render();
  const scrolled = [];
  h.find(byClass('effect-search-menu')).props.ref.current = { querySelector: query => ({ scrollIntoView: () => scrolled.push(query) }) };
  h.flushEffects();
  h.find(node => node.type === 'input').props.onKeyDown(event('ArrowDown')); h.render();
  assert.equal(h.find(node => node.type === 'input').props['aria-activedescendant'], undefined);
  h.render({ options }); h.flushEffects();
  for (let i = 0; i < 80; i++) { h.find(node => node.type === 'input').props.onKeyDown(event('ArrowDown')); h.render(); h.flushEffects(); }
  assert.match(h.find(node => node.type === 'input').props['aria-activedescendant'], /option-59$/);
  assert.equal(scrolled.at(-1), '[data-option-index="59"]');
  h.render({ options: options.slice(0, 2) }); h.flushEffects(); h.render();
  assert.match(h.find(node => node.type === 'input').props['aria-activedescendant'], /option-1$/);
  h.find(node => node.type === 'input').props.onKeyDown(event('Enter'));
  assert.deepEqual(picked, ['1']);
  const escape = event('Escape', inputTarget); environment.dispatch('keydown', escape); h.render(); h.flushEffects();
  assert.equal(escape.prevented, true); assert.equal(escape.stopped, true);
  assert.equal(h.find(byClass('effect-search-menu')), undefined);
  const secondEscape = event('Escape', inputTarget); environment.dispatch('keydown', secondEscape);
  assert.equal(secondEscape.prevented, false);
  h.dispose(); environment.restore();
});

test('tooltip can be entered and scrolled; Escape closes it and nested scrolling repositions it', async () => {
  const environment = hostEnvironment();
  const h = harness('HoverCard.tsx', 'HoverCard', { content: 'Long detail', children: 'Anchor' });
  h.render(); h.flushEffects();
  let anchorBox = { left: 300, top: 100, right: 340, bottom: 130, width: 40, height: 30 };
  h.find(byClass('hover-anchor')).props.ref.current = { contains: () => false, getBoundingClientRect: () => anchorBox };
  h.find(byClass('hover-anchor')).props.onMouseEnter(); h.render();
  let scrolled = 0;
  const tooltip = h.find(byClass('hover-card'));
  tooltip.props.ref.current = { contains: () => false, getBoundingClientRect: () => ({ width: 320, height: 200 }), scrollHeight: 900, clientHeight: 200, scrollBy: value => { scrolled += value.top; } };
  h.flushEffects(); h.render();
  assert.equal(h.find(byClass('hover-card')).props.style.top, 138);
  anchorBox = { ...anchorBox, top: 180, bottom: 210 };
  environment.dispatch('scroll', {}); h.render();
  assert.equal(h.find(byClass('hover-card')).props.style.top, 218);
  h.find(byClass('hover-anchor')).props.onKeyDown(event('PageDown', { closest: () => null }));
  assert.equal(scrolled, 160);
  h.find(byClass('hover-anchor')).props.onMouseLeave();
  h.find(byClass('hover-card')).props.onMouseEnter();
  await new Promise(resolve => setTimeout(resolve, 200)); h.render();
  assert.ok(h.find(byClass('hover-card')));
  const escape = event('Escape', {}); environment.dispatch('keydown', escape); h.render(); h.flushEffects();
  assert.equal(escape.prevented, true); assert.equal(escape.stopped, true);
  assert.equal(h.find(byClass('hover-card')), undefined);
  h.dispose(); environment.restore();
});

test('pure slot normalization and tooltip geometry handle sparse teams and tight viewports', () => {
  const h = harness('EffectSearch.tsx', 'EffectSearch', { options: [], chosen: [], placeholder: '', empty: '', onPick() {} });
  const { compactSlots, clampActiveOption, hoverCardPosition } = h.load('pickerUi.ts');
  assert.deepEqual(compactSlots([null, 'second', null, 'fourth'], 5), ['second', 'fourth', null, null, null]);
  assert.equal(clampActiveOption(-1, 0), 0);
  const anchor = { left: 490, top: 360, right: 500, bottom: 390, width: 10, height: 30 };
  assert.deepEqual(hoverCardPosition(anchor, { width: 320, height: 200 }, { width: 500, height: 400 }), { left: 172, top: 152 });
  assert.deepEqual(hoverCardPosition(anchor, { width: 600, height: 600 }, { width: 200, height: 200 }), { left: 8, top: 8 });
});
