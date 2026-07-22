const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadScript(relativePath, context) {
  const source = fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
  vm.runInContext(source, context, { filename: relativePath });
}

function createRuntime(initialCatMindState, options = {}) {
  const listeners = new Map();
  const timers = [];
  const publishedReasons = [];
  const observations = [];
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
    nekoCatMind: {
      getState: () => ({ ...catMindState }),
      observe(payload) {
        observations.push(payload);
        return payload;
      },
    },
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
  const runtimeMath = Object.create(Math);
  runtimeMath.random = typeof options.random === 'function' ? options.random : Math.random;
  const stretchRequests = [];
  if (Object.prototype.hasOwnProperty.call(options, 'stretchStarted')) {
    window.NekoCatIdlePresentation = {
      requestCat1Stretch() {
        stretchRequests.push({ tier: catMindState.tier, enteredAt: catMindState.enteredAt });
        return options.stretchStarted === true;
      },
    };
  }
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
    Math: runtimeMath,
    Number,
    Object,
    Array,
  });
  loadScript('app/app-react-chat-window/cat-local-chat-lexicon.js', context);
  loadScript('app/app-react-chat-window/cat-local-chat.js', context);

  return {
    window,
    catMindState,
    observations,
    stretchRequests,
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
  assert.deepEqual(Array.from(lexicon.tiers.cat1.meows), [
    '喵', '喵', '喵', '喵', '喵', '喵', '喵', '喵呜',
  ]);
  assert.equal(lexicon.tiers.cat2.meows, undefined);
  assert.equal(lexicon.tiers.cat3.meows, undefined);
  assert.deepEqual(Array.from(lexicon.tiers.cat2.meowCounts), [1, 1, 2]);
  assert.deepEqual(Array.from(lexicon.tiers.cat3.meowCounts), [1]);
  assert.deepEqual(Array.from(lexicon.punctuation.drowsy), ['。', '～', '……']);
  assert.deepEqual(Array.from(lexicon.punctuation.sleeping), ['。', '……', '……。']);
  Object.values(lexicon.punctuation).flat().forEach(token => assert.equal(token.includes('喵'), false));
  Object.values(lexicon.kaomoji).flat().forEach(token => assert.equal(token.includes('喵'), false));
  assert.equal(lexicon.punctuation.gentle, undefined);
  assert.equal(lexicon.punctuation.sleepy, undefined);
  assert.equal(lexicon.kaomoji.gentle, undefined);
  assert.equal(lexicon.kaomoji.sleepy, undefined);
  assert.equal(lexicon.tiers.cat2.kaomojiGroup, 'drowsy');
  assert.equal(lexicon.tiers.cat3.kaomojiGroup, 'sleeping');
  ['(＾▽＾)', '(・_・?)', '(⊙_⊙)', '(¬‿¬)', '(〃ω〃)', '(¬_¬)']
    .forEach(face => assert.ok(lexicon.kaomoji.lively.includes(face)));
  assert.ok(lexicon.kaomoji.drowsy.includes('(´ぅω・｀)'));
  assert.ok(lexicon.kaomoji.sleeping.includes('(-_-)zzz'));
  assert.deepEqual(Array.from(lexicon.easterEggs.hissStretch.voices), ['哈', '嘶....哈']);
  assert.deepEqual(Array.from(lexicon.easterEggs.hissStretch.punctuation), ['～', '！！！']);
  assert.deepEqual(Array.from(lexicon.easterEggs.hissStretch.kaomoji), [
    'ฅ(`ꈊ´ฅ)',
    '(ฅ`ω´ฅ)',
  ]);

  const cat1Reply = manager.composeReply('cat1', () => 0);
  const cat3Reply = manager.composeReply('cat3', () => 0.999);
  const hissReply = manager.composeHissStretchReply(() => 0.999);
  assert.match(cat1Reply, /喵/);
  assert.match(cat3Reply, /喵/);
  assert.equal(hissReply, '嘶....哈！！！(ฅ`ω´ฅ)');
  assert.equal(cat1Reply.includes('听见'), false);
  assert.equal(cat3Reply.includes('知道'), false);
});

test('cat replies mix voice atoms with optional internal punctuation', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;
  const values = [
    0.999, // four voice atoms
    0, 0.999, 0, // first 喵 + infix ～
    0, 0,        // second 喵 + no infix
    0, 0,        // third 喵 + no infix
    0,           // fourth 喵
    0.7,         // terminal ！！！
    0,           // no leading pause
    0,           // no kaomoji
  ];
  let index = 0;
  const reply = manager.composeReply('cat1', () => values[index++] ?? 0);

  assert.equal(reply, '喵～喵喵喵！！！');
});

test('CAT1 hiss easter egg starts stretch before using its dedicated reply', () => {
  const runtime = createRuntime({}, { random: () => 0, stretchStarted: true });
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'hiss', text: '摸一下', enteredAt: 1000 }), true);
  assert.equal(manager.submit({ requestId: 'hiss', text: '重复摸一下', enteredAt: 1000 }), true);
  runtime.runNextTimer();

  const items = manager.getSnapshot().items;
  assert.equal(runtime.stretchRequests.length, 1);
  assert.equal(items.at(-1).text, '哈～ฅ(`ꈊ´ฅ)');
  assert.equal(runtime.observations.filter(item => item.type === 'cat_local_text_received').length, 1);
});

test('CAT1 hiss easter egg uses an exact three-percent boundary', () => {
  const randomValues = [0, 0.03];
  const runtime = createRuntime({}, {
    random: () => randomValues.shift() ?? 0,
    stretchStarted: true,
  });
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'outside-hiss-rate', text: '刚好边界', enteredAt: 1000 }), true);
  runtime.runNextTimer();

  assert.equal(runtime.stretchRequests.length, 0);
  assert.match(manager.getSnapshot().items.at(-1).text, /喵/);
});

test('hiss easter egg falls back to meow when stretch cannot start', () => {
  const runtime = createRuntime({}, { random: () => 0, stretchStarted: false });
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'blocked-hiss', text: '贴边也回应', enteredAt: 1000 }), true);
  runtime.runNextTimer();

  const reply = manager.getSnapshot().items.at(-1).text;
  assert.equal(runtime.stretchRequests.length, 1);
  assert.match(reply, /喵/);
  assert.equal(reply.includes('ฅ(`ꈊ´ฅ)'), false);
  assert.equal(reply.includes('(ฅ`ω´ฅ)'), false);
});

test('hiss easter egg is not requested outside CAT1', () => {
  const runtime = createRuntime({ tier: 'cat2' }, { random: () => 0, stretchStarted: true });
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'cat2-no-hiss', text: '继续打盹', enteredAt: 1000 }), true);
  runtime.runNextTimer();

  assert.equal(runtime.stretchRequests.length, 0);
  assert.match(manager.getSnapshot().items.at(-1).text, /喵/);
});

test('canonical cat chat deduplicates requests and replies in submission order', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'r1', text: '第一条', enteredAt: 1000 }), true);
  assert.equal(manager.submit({ requestId: 'r1', text: '重复', enteredAt: 1000 }), true);
  assert.equal(manager.submit({ requestId: 'r2', text: '第二条', enteredAt: 1000 }), true);
  assert.equal(manager.getSnapshot().items.length, 2);
  assert.deepEqual(Array.from(runtime.observations, item => item.type), [
    'cat_local_text_received',
    'cat_local_text_received',
  ]);
  assert.deepEqual(Array.from(runtime.observations, item => item.detail.requestId), ['r1', 'r2']);
  assert.equal(runtime.observations.some(item => JSON.stringify(item).includes('第一条')), false);
  assert.equal(runtime.observations.some(item => JSON.stringify(item).includes('第二条')), false);

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

test('Cat Mind observation failure does not break the accepted local reply', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;
  runtime.window.nekoCatMind.observe = () => { throw new Error('Cat Mind unavailable'); };

  assert.equal(manager.submit({ requestId: 'observe-failure', text: '仍然回复', enteredAt: 1000 }), true);
  while (runtime.pendingTimerCount()) runtime.runNextTimer();

  const items = manager.getSnapshot().items;
  assert.deepEqual(Array.from(items, item => item.role), ['user', 'assistant']);
  assert.match(items[1].text, /喵/);
});

test('missing lexicon invalidates a pending reply instead of appending an empty bubble', () => {
  const runtime = createRuntime();
  const manager = runtime.window.nekoCatLocalChatManager;

  assert.equal(manager.submit({ requestId: 'missing-lexicon', text: '还能看到吗', enteredAt: 1000 }), true);
  delete runtime.window.nekoCatLocalChatLexicon;
  runtime.runNextTimer();

  const items = manager.getSnapshot().items;
  assert.deepEqual(Array.from(items, item => item.role), ['user']);
  assert.equal(items.some(item => item.text === ''), false);
  assert.equal(runtime.pendingTimerCount(), 0);
  assert.equal(runtime.publishedReasons.includes('cat-local-chat-reply-invalidated'), true);
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
