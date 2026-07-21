const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadScript(relativePath, context) {
  const source = fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
  vm.runInContext(source, context, { filename: relativePath });
}

function createRuntime(initialCatMindState) {
  const listeners = new Map();
  const timers = [];
  const publishedReasons = [];
  let timerSeq = 0;
  const catMindState = Object.assign({
    active: true,
    tier: 'cat1',
    enteredAt: 1000,
  }, initialCatMindState || {});
  let compactChatState = 'default';
  const hostParts = {
    getCurrentChatSurfaceMode: () => 'compact',
    setCompactChatState(next) {
      compactChatState = next;
    },
    renderWindow() {},
  };
  const window = {
    location: { pathname: '/' },
    __appReactChatWindowParts: hostParts,
    nekoCatMind: { getState: () => ({ ...catMindState }) },
    postGoodbyeChatComposerHiddenState: (_hidden, reason) => {
      publishedReasons.push(reason);
    },
    addEventListener(name, listener) {
      const bucket = listeners.get(name) || [];
      bucket.push(listener);
      listeners.set(name, bucket);
    },
    dispatchEvent(event) {
      (listeners.get(event.type) || []).forEach(listener => listener(event));
    },
    setTimeout(callback) {
      timerSeq += 1;
      timers.push({ id: timerSeq, callback, cancelled: false });
      return timerSeq;
    },
    clearTimeout(id) {
      const timer = timers.find(candidate => candidate.id === id);
      if (timer) timer.cancelled = true;
    },
  };
  class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }
  const context = vm.createContext({
    window,
    CustomEvent,
    console,
    Date,
    Math,
    Number,
    Object,
    Array,
  });
  loadScript('app/app-react-chat-window/cat-local-chat-lexicon.js', context);
  loadScript('app/app-react-chat-window/cat-local-chat.js', context);

  return {
    window,
    catMindState,
    publishedReasons,
    runNextTimer() {
      const timer = timers.shift();
      if (timer && !timer.cancelled) timer.callback();
    },
    pendingTimerCount() {
      return timers.filter(timer => !timer.cancelled).length;
    },
    get compactChatState() {
      return compactChatState;
    },
  };
}

test('cat replies are composed from separate vocabulary atom groups', () => {
  const runtime = createRuntime();
  const lexicon = runtime.window.nekoCatLocalChatLexicon;
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.deepEqual(Array.from(lexicon.meows), ['喵']);
  Object.values(lexicon.punctuation).flat().forEach(token => assert.equal(token.includes('喵'), false));
  Object.values(lexicon.kaomoji).flat().forEach(token => assert.equal(token.includes('喵'), false));

  const cat1Reply = manager.composeReply('cat1', () => 0);
  const cat3Reply = manager.composeReply('cat3', () => 0.999);
  assert.match(cat1Reply, /喵/);
  assert.match(cat3Reply, /喵/);
  assert.equal(cat1Reply.includes('听见'), false);
  assert.equal(cat3Reply.includes('知道'), false);
});

test('canonical cat chat deduplicates requests and replies in submission order', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'r1', text: '第一条', enteredAt: 1000 }), true);
  assert.equal(manager.submit({ requestId: 'r1', text: '重复', enteredAt: 1000 }), true);
  assert.equal(manager.submit({ requestId: 'r2', text: '第二条', enteredAt: 1000 }), true);
  assert.equal(manager.getSnapshot().items.length, 2);

  while (runtime.pendingTimerCount()) runtime.runNextTimer();

  const items = manager.getSnapshot().items;
  assert.deepEqual(Array.from(items, item => item.role), ['user', 'user', 'assistant', 'assistant']);
  assert.deepEqual(Array.from(items.filter(item => item.role === 'user'), item => item.text), ['第一条', '第二条']);
  assert.equal(items.filter(item => item.requestId === 'r1' && item.role === 'assistant').length, 1);
  assert.equal(items.filter(item => item.requestId === 'r2' && item.role === 'assistant').length, 1);
  assert.equal(items.filter(item => item.role === 'assistant').every(item => item.text.includes('喵')), true);
  assert.equal(runtime.publishedReasons.includes('cat-local-chat-user'), true);
  assert.equal(runtime.publishedReasons.includes('cat-local-chat-reply'), true);
});

test('cycle exit invalidates pending replies and standalone pages cannot become authoritative', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'r3', text: '等回复', enteredAt: 1000 }), true);
  runtime.catMindState.active = false;
  runtime.catMindState.tier = 'none';
  runtime.catMindState.enteredAt = 0;
  runtime.runNextTimer();
  assert.deepEqual(Array.from(manager.getSnapshot().items), []);
  assert.equal(manager.getSnapshot().active, false);

  runtime.window.location.pathname = '/chat';
  runtime.catMindState.active = true;
  runtime.catMindState.tier = 'cat1';
  runtime.catMindState.enteredAt = 2000;
  assert.equal(manager.submit({ requestId: 'standalone', text: '不能本地处理', enteredAt: 2000 }), false);
});

test('an older cross-window snapshot cannot revive an ended cat cycle', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.applySnapshot({
    active: false,
    tier: 'none',
    enteredAt: 0,
    items: [],
    updatedAt: 3000,
  }), true);
  assert.equal(manager.applySnapshot({
    active: true,
    tier: 'cat1',
    enteredAt: 1000,
    items: [{ id: 'old', role: 'assistant', text: '喵', sequence: 1 }],
    updatedAt: 2000,
  }), false);
  assert.equal(manager.getSnapshot().active, false);
  assert.deepEqual(Array.from(manager.getSnapshot().items), []);
});

test('the real cat appearance event opens compact input and closes the same cycle', () => {
  const runtime = createRuntime({ active: false, tier: 'none', enteredAt: 0 });
  const manager = runtime.window.nekoCatLocalChatManager;
  assert.equal(manager.getSnapshot().active, false);

  runtime.catMindState.active = true;
  runtime.catMindState.tier = 'cat1';
  runtime.catMindState.enteredAt = 4000;
  runtime.window.dispatchEvent({ type: 'neko:cat-local-active-change' });

  assert.equal(manager.getSnapshot().active, true);
  assert.equal(manager.getSnapshot().enteredAt, 4000);
  assert.equal(runtime.compactChatState, 'input');
  assert.equal(runtime.publishedReasons.includes('cat-local-active-change'), true);

  runtime.catMindState.active = false;
  runtime.catMindState.tier = 'none';
  runtime.catMindState.enteredAt = 0;
  runtime.window.dispatchEvent({ type: 'neko:cat-local-active-change' });
  assert.equal(manager.getSnapshot().active, false);
  assert.deepEqual(Array.from(manager.getSnapshot().items), []);
});

test('the existing goodbye clear event also closes temporary cat chat', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;
  assert.equal(manager.submit({ requestId: 'before-switch', text: '切角色前', enteredAt: 1000 }), true);

  runtime.catMindState.active = false;
  runtime.catMindState.tier = 'none';
  runtime.catMindState.enteredAt = 0;
  runtime.window.dispatchEvent({ type: 'neko:goodbye-state-cleared' });

  assert.equal(manager.getSnapshot().active, false);
  assert.deepEqual(Array.from(manager.getSnapshot().items), []);
  assert.equal(runtime.pendingTimerCount(), 0);
  assert.equal(runtime.publishedReasons.includes('goodbye-state-cleared'), true);
});
