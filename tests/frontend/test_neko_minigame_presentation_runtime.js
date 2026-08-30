const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = String(tagName || '').toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.parentNode = null;
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.checked = false;
    this.disabled = false;
    this.type = '';
    this.max = 0;
    this.value = 0;
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  append(...children) {
    for (const child of children) this.appendChild(child);
  }

  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }

  setAttribute(name, value) {
    this.attributes.set(String(name), String(value));
  }

  getAttribute(name) {
    return this.attributes.has(String(name)) ? this.attributes.get(String(name)) : null;
  }

  removeAttribute(name) {
    this.attributes.delete(String(name));
  }

  addEventListener(type, handler) {
    let bucket = this.listeners.get(type);
    if (!bucket) {
      bucket = new Set();
      this.listeners.set(type, bucket);
    }
    bucket.add(handler);
  }

  removeEventListener(type, handler) {
    const bucket = this.listeners.get(type);
    if (!bucket) return;
    bucket.delete(handler);
    if (!bucket.size) this.listeners.delete(type);
  }

  dispatchEvent(event) {
    for (const handler of Array.from(this.listeners.get(event.type) || [])) handler(event);
  }
}

class FakeDocument {
  constructor() {
    this.documentElement = new FakeElement('html', this);
    this.head = new FakeElement('head', this);
    this.documentElement.appendChild(this.head);
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }
}

function createWindow(documentImpl) {
  let nextTimerId = 1;
  const timers = new Map();
  return {
    document: documentImpl,
    AbortController,
    TextEncoder,
    console: { error() {} },
    setTimeout(callback, delay) {
      const id = nextTimerId++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
    get timerCount() { return timers.size; },
    fireTimer(id) {
      const timer = timers.get(id);
      if (!timer) return;
      timers.delete(id);
      timer.callback();
    },
  };
}

function createTransport() {
  let runtimeState = { sessionId: 'presentation-session', characterName: 'Yui' };
  const consentCalls = [];
  return {
    consentCalls,
    logger: {
      log() {}, info() {}, warn() {}, error() {},
      async enable() { return { ok: true }; },
      async enableAfterRouteStart() { return { ok: true }; },
      async flush() { return { ok: true }; },
      reset() {},
    },
    connectGame(request) {
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'presentation-test-host',
        registration: {
          mode: 'registered',
          gameId: request.manifest.id,
          publisherId: 'presentation-tests',
          version: request.manifest.version,
        },
        grantedCapabilities: request.manifest.requiredCapabilities,
      };
    },
    getRuntimeState() { return runtimeState; },
    applyRuntimeState(state) { runtimeState = { ...runtimeState, ...state }; },
    resetRuntime({ newSession } = {}) {
      if (newSession) runtimeState = { ...runtimeState, sessionId: `${runtimeState.sessionId}-next` };
      return runtimeState;
    },
    async start() { return { ok: true, state: { game_route_active: true } }; },
    async end() { return { ok: true }; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async configureGameMemoryConsent(payload) {
      consentCalls.push(payload);
      return { ok: true, enabled: payload.enabled };
    },
    async submitGameMemory() { return { ok: true }; },
    dispose() {},
  };
}

async function main() {
  const documentImpl = new FakeDocument();
  const windowImpl = createWindow(documentImpl);
  global.window = windowImpl;
  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });

  const transport = createTransport();
  const game = await window.NekoMiniGame.connect({
    id: 'presentation-test',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'memory'],
  }, { transport, windowImpl, documentImpl });
  assert(windowImpl.timerCount === 0, 'the completed handshake timer was not released');

  const loadingSlot = documentImpl.createElement('div');
  const loading = game.presentation.loading.mount({
    container: loadingSlot,
    title: 'Preparing game',
    message: 'Loading assets',
  });
  assert(!loading.disposed && loadingSlot.children.length === 1,
    'loading presentation did not mount as an active controller');
  assert(loading.element.getAttribute('role') === 'status',
    'loading presentation did not expose accessible status semantics');
  loading.setStage('avatar');
  loading.setProgress(0.625);
  loading.setMessage('Loading character');
  loading.setError('Avatar failed');
  assert(loading.state.stage === 'avatar' && loading.state.progress === 0.625,
    'loading presentation did not retain bounded stage/progress state');
  assert(loading.state.error === 'Avatar failed' && loading.element.getAttribute('role') === 'alert',
    'loading error did not switch to alert semantics');
  loading.hide();
  assert(!loading.state.visible, 'loading presentation did not hide');
  loading.show();
  loading.setError('');
  assert(loading.state.visible && loading.element.getAttribute('role') === 'status',
    'loading presentation did not restore its normal status state');

  const bubbleSlot = documentImpl.createElement('div');
  const bubble = game.presentation.bubble.mount({ container: bubbleSlot });
  // Mounted means IN the container. Without this the mount built a root,
  // registered it, handed it back and never attached it -- so every assertion
  // below passes on a detached node that no user could ever see.
  assert(bubbleSlot.children.includes(bubble.element),
    'the bubble presentation was never appended to its container');
  bubble.show('Ready!', { durationMs: 4000 });
  assert(!bubble.disposed && !bubble.element.hidden && bubble.element.textContent === 'Ready!',
    'bubble presentation did not render text');
  assert(windowImpl.timerCount === 1, 'bubble auto-hide did not use one bounded timer');
  bubble.hide();
  assert(bubble.element.hidden && windowImpl.timerCount === 0,
    'bubble hide did not clear its timer and content');
  const boundedBubbles = Array.from({ length: 7 }, () => (
    game.presentation.bubble.mount({ container: documentImpl.createElement('div') })
  ));
  let bubbleLimitError = null;
  try {
    game.presentation.bubble.mount({ container: documentImpl.createElement('div') });
  } catch (error) { bubbleLimitError = error; }
  assert(bubbleLimitError?.code === 'busy', 'bubble presentation growth was not bounded');
  for (const item of boundedBubbles) item.dispose();

  const consentSlot = documentImpl.createElement('div');
  const consent = game.presentation.memoryConsent.mount({
    container: consentSlot,
    label: '本局对话进入记忆',
    hint: '仅在开局前设置',
  });
  assert(!consent.enabled && !consent.input.checked && !consent.input.disabled,
    'memory consent presentation was not default-off and editable before start');
  assert((consent.input.listeners.get('change') || new Set()).size === 1,
    'memory consent presentation did not own exactly one change listener');
  consent.input.checked = true;
  await consent.sync();
  assert(consent.enabled && game.memory.consent.enabled && transport.consentCalls.length === 1,
    'memory consent presentation did not use the official memory API');
  assert(consent.input.getAttribute('aria-invalid') === null,
    'successful memory consent remained marked invalid');
  const mirroredConsent = game.presentation.memoryConsent.mount({
    container: documentImpl.createElement('div'),
    label: 'Mirror consent state',
  });
  assert(mirroredConsent.enabled,
    'a newly mounted consent presentation did not reflect the accepted SDK state');
  await game.memory.configureConsent(false);
  assert(!consent.enabled && !mirroredConsent.enabled,
    'direct memory consent changes did not synchronize mounted presentations');
  consent.input.checked = true;
  await consent.sync();

  await game.runtime.start();
  assert(consent.input.disabled, 'memory consent remained editable after runtime start');
  await game.runtime.end({ reason: 'presentation-test-complete' });
  game.runtime.reset({ newSession: true });
  assert(!consent.input.checked && !consent.enabled && !consent.input.disabled,
    'new runtime session did not reset the consent presentation to default-off');

  const styleNodes = documentImpl.head.children.filter(
    (node) => node.getAttribute('data-neko-minigame-presentation') === 'v1',
  );
  assert(styleNodes.length === 1, 'presentation styles were injected more than once per document');

  loading.dispose();
  assert(loading.disposed && loadingSlot.children.length === 0
    && game.presentation.loading.activeCount === 0,
  'loading controller disposal did not release its DOM and registry entry');

  bubble.show('Disposed with client', { durationMs: 5000 });
  assert(windowImpl.timerCount === 1, 'client-disposal bubble timer was not installed');
  game.dispose();
  assert(bubble.disposed && consent.disposed,
    'client disposal did not mark presentation controllers disposed');
  assert(mirroredConsent.disposed,
    'client disposal did not release all memory consent presentations');
  assert(windowImpl.timerCount === 0 && bubbleSlot.children.length === 0 && consentSlot.children.length === 0,
    'client disposal did not release presentation DOM or timers');
  assert((consent.input.listeners.get('change') || new Set()).size === 0,
    'client disposal did not release the memory consent listener');
  assert(game.presentation.bubble.activeCount === 0
    && game.presentation.memoryConsent.activeCount === 0,
  'client disposal left presentation registry entries resident');

  process.stdout.write('mini-game presentation runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
