const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');
const { runJsPartsInNewContext } = require('./app-part-test-utils.cjs');

function createClassList() {
  return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
}

function createElement(overrides = {}) {
  const attributes = new Map();
  return Object.assign({
    hidden: false,
    classList: createClassList(),
    style: { setProperty() {}, removeProperty() {} },
    dataset: {},
    children: [],
    parentElement: null,
    offsetParent: {},
    clientWidth: 430,
    clientHeight: 64,
    scrollWidth: 430,
    scrollHeight: 64,
    setAttribute(key, value) { attributes.set(key, String(value)); },
    getAttribute(key) { return attributes.has(key) ? attributes.get(key) : null; },
    hasAttribute(key) { return attributes.has(key); },
    removeAttribute(key) { attributes.delete(key); },
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 430, height: 64, right: 430, bottom: 64, x: 0, y: 0 };
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    contains() { return false; },
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
    remove() {},
    focus() {},
    blur() {},
  }, overrides);
}

function loadReactChatHost() {
  let renderProps = null;
  const elements = {
    'react-chat-window-overlay': createElement({ hidden: false }),
    'react-chat-window-root': createElement(),
    'react-chat-window-shell': createElement(),
  };
  const document = {
    readyState: 'loading',
    body: createElement(),
    documentElement: createElement(),
    addEventListener() {},
    removeEventListener() {},
    getElementById(id) { return elements[id] || null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return createElement(); },
  };
  const quietConsole = { debug() {}, error() {}, log() {}, warn() {} };
  const localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  const window = {
    document,
    innerWidth: 1200,
    innerHeight: 800,
    screenX: 0,
    screenY: 0,
    devicePixelRatio: 1,
    NekoChatWindow: { mount(_root, nextProps) { renderProps = nextProps; } },
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {},
    setTimeout() { return 1; },
    clearTimeout() {},
    setInterval() { return 1; },
    clearInterval() {},
    requestAnimationFrame() { return 1; },
    cancelAnimationFrame() {},
    matchMedia() { return { matches: false, addEventListener() {}, removeEventListener() {} }; },
    localStorage,
    console: quietConsole,
    getComputedStyle() { return { display: 'block', visibility: 'visible' }; },
  };
  const context = {
    window,
    document,
    navigator: { userAgent: '' },
    localStorage,
    console: quietConsole,
    CustomEvent: class CustomEvent {
      constructor(type, options) { this.type = type; this.detail = options && options.detail; }
    },
    Date,
    Math,
    Promise,
    URL,
    URLSearchParams,
    AbortController,
    fetch: async () => ({ ok: false, json: async () => ({}) }),
    getComputedStyle: window.getComputedStyle,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    requestAnimationFrame: window.requestAnimationFrame,
    cancelAnimationFrame: window.cancelAnimationFrame,
  };
  runJsPartsInNewContext(path.join(__dirname, 'app/app-react-chat-window'), context);
  window.reactChatWindowHost.setViewProps({});
  assert.equal(typeof renderProps.onAvatarInteraction, 'function');
  return { host: window.reactChatWindowHost, onAvatarInteraction: renderProps.onAvatarInteraction };
}

test('React chat queues committed avatar interactions until the authoritative host callback binds', () => {
  const { host, onAvatarInteraction } = loadReactChatHost();
  const delivered = [];

  onAvatarInteraction({ interactionId: 'early-1', toolId: 'fist' });
  onAvatarInteraction({ interactionId: 'early-1', toolId: 'fist' });
  onAvatarInteraction({ interactionId: 'early-2', toolId: 'hammer' });
  host.setOnAvatarInteraction(payload => delivered.push(payload));

  assert.deepEqual(delivered.map(item => item.interactionId), ['early-1', 'early-2']);
  onAvatarInteraction({ interactionId: 'live-1', toolId: 'lollipop' });
  assert.deepEqual(delivered.map(item => item.interactionId), ['early-1', 'early-2', 'live-1']);
});

test('a bound host callback failure is reported once and never becomes an unreachable retry', () => {
  const { host, onAvatarInteraction } = loadReactChatHost();
  let attempts = 0;
  const delivered = [];

  host.setOnAvatarInteraction(() => {
    attempts += 1;
    throw new Error('host failure');
  });
  onAvatarInteraction({ interactionId: 'failed-live', toolId: 'hammer' });
  assert.equal(attempts, 1);

  host.setOnAvatarInteraction(payload => delivered.push(payload));
  assert.deepEqual(delivered, []);
});
