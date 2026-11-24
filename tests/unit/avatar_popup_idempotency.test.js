'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const POPUP_PATH = path.resolve(__dirname, '../../static/avatar/avatar-ui-popup.js');
const HOME_STEPS_PATH = path.resolve(__dirname, '../../static/tutorial/click-guide/home-steps.js');

function makeNode(tag, id = '') {
  const node = {
    tagName: tag.toUpperCase(),
    id,
    className: '',
    style: {
      display: 'none',
      _props: new Map(),
      setProperty(k, v, p = '') { this._props.set(k, [v, p]); this[k] = v; },
      getPropertyValue(k) { return (this._props.get(k) || [this[k] || ''])[0]; },
      getPropertyPriority(k) { return (this._props.get(k) || ['', ''])[1]; },
      removeProperty(k) { this._props.delete(k); delete this[k]; },
    },
    dataset: {},
    attributes: new Map(),
    children: [],
    parentNode: null,
    isConnected: false,
    classList: {
      _set: new Set(),
      add(...c) { c.forEach((x) => this._set.add(x)); },
      remove(...c) { c.forEach((x) => this._set.delete(x)); },
      contains(c) { return this._set.has(c); },
    },
    setAttribute(k, v) {
      this.attributes.set(k, String(v));
      if (k.startsWith('data-')) {
        const camel = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        this.dataset[camel] = String(v);
      }
    },
    getAttribute(k) { return this.attributes.has(k) ? this.attributes.get(k) : null; },
    removeAttribute(k) { this.attributes.delete(k); },
    appendChild(child) {
      child.parentNode = this;
      if (this.isConnected) markConnected(child, true);
      this.children.push(child);
      return child;
    },
    remove() {
      if (this.parentNode) {
        const idx = this.parentNode.children.indexOf(this);
        if (idx >= 0) this.parentNode.children.splice(idx, 1);
      }
      this.parentNode = null;
      markConnected(this, false);
    },
    addEventListener() {},
    matches() { return false; },
    querySelector() { return null; },
    querySelectorAll(selector) {
      const results = [];
      const ownerMatch = selector.match(/^\[data-neko-sidepanel-owner="([^"]+)"\]$/);
      const idPrefixMatch = selector.match(/^\[id\^="([^"]+)"\]$/);
      const walk = (root) => {
        for (const child of root.children) {
          if (ownerMatch && child.getAttribute('data-neko-sidepanel-owner') === ownerMatch[1]) {
            results.push(child);
          } else if (idPrefixMatch && child.id && child.id.startsWith(idPrefixMatch[1])) {
            results.push(child);
          }
          walk(child);
        }
      };
      walk(this);
      return results;
    },
  };
  return node;
}

function markConnected(node, connected) {
  node.isConnected = connected;
  for (const c of node.children) markConnected(c, connected);
}

function findById(root, id) {
  if (root.id === id) return root;
  for (const child of root.children) {
    const found = findById(child, id);
    if (found) return found;
  }
  return null;
}

test('F0377: createPopup is idempotent and home-steps cleanup preserves live popup and sidepanels', async () => {
  const head = makeNode('head');
  const body = makeNode('body');
  body.isConnected = true;
  const documentObj = {
    head,
    body,
    createElement: (tag) => makeNode(tag),
    getElementById: (id) => findById(body, id),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
    querySelector: (sel) => (sel.startsWith('#') ? findById(body, sel.slice(1)) : null),
  };

  class FakeManager {}
  FakeManager.prototype._createSettingsPopupContent = function (popup) {
    popup.dataset.initialized = 'true';
  };

  const context = {
    window: null,
    document: documentObj,
    AbortController,
    Live2DManager: FakeManager,
    AgentHUD: { _createAgentPopupContent() {} },
    console: { log() {}, warn() {}, error() {} },
    setTimeout,
    clearTimeout,
    Promise,
    Map,
    Set,
  };
  context.window = context;

  vm.createContext(context);
  vm.runInContext(fs.readFileSync(POPUP_PATH, 'utf8'), context, { filename: POPUP_PATH });
  vm.runInContext("AvatarPopupMixin.apply(Live2DManager.prototype, 'live2d');", context);

  const mgr = new FakeManager();
  context.window.live2dManager = mgr;
  const container = makeNode('div', 'live2d-floating-buttons');
  const btnWrapper = makeNode('div');
  const popup = mgr.createPopup('settings');
  btnWrapper.appendChild(popup);
  container.appendChild(btnWrapper);
  body.appendChild(container);

  // Also set up NekoClickGuide + universalTutorialManager for home-steps.js
  context.window.universalTutorialManager = { constructor: { detectModelPrefix: () => 'live2d' } };
  context.window.NekoClickGuide = {
    resolveTarget: (sel) => (sel.startsWith('#') ? findById(body, sel.slice(1)) : null),
    waitUntil: async (cond) => cond(),
  };
  vm.runInContext(fs.readFileSync(HOME_STEPS_PATH, 'utf8'), context, { filename: HOME_STEPS_PATH });

  const sidePanel = makeNode('div', 'live2d-sidepanel-1');
  sidePanel.setAttribute('data-neko-sidepanel-owner', popup.id);
  body.appendChild(sidePanel);

  // Calling createPopup again must return the exact same connected DOM node without removing side panels
  const second = mgr.createPopup('settings');
  assert.equal(second, popup);
  assert.equal(popup.isConnected, true);
  assert.equal(popup.parentNode, btnWrapper);
  assert.equal(sidePanel.isConnected, true);

  // Simulate open menu before prepareFloating runs and restores it
  popup.style.display = 'flex';
  const showCalls = [];
  mgr.closeAllPopups = () => { popup.style.display = 'none'; };
  mgr.showPopup = (id, target) => {
    showCalls.push({ id, target });
    if (target) target.style.display = 'flex';
  };

  const cleanup = await context.window.NekoClickGuide.prepareFloating();
  await cleanup();

  assert.equal(popup.isConnected, true);
  assert.equal(popup.parentNode, btnWrapper);
  assert.equal(sidePanel.isConnected, true);
  assert.equal(showCalls.length, 1);
  assert.equal(showCalls[0].id, 'settings');
  assert.equal(showCalls[0].target, popup);
  assert.equal(popup.style.display, 'flex');
});

test('F0377: home-steps cleanup restores open menu across real delayed closeAllPopups animation', async () => {
  const head = makeNode('head');
  const body = makeNode('body');
  body.isConnected = true;
  const documentObj = {
    head,
    body,
    createElement: (tag) => makeNode(tag),
    getElementById: (id) => findById(body, id),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
    querySelector: (sel) => (sel.startsWith('#') ? findById(body, sel.slice(1)) : null),
  };

  class FakeManager {}
  FakeManager.prototype._createSettingsPopupContent = function (popup) {
    popup.dataset.initialized = 'true';
  };

  const dispatchedEvents = [];
  const context = {
    window: null,
    document: documentObj,
    AbortController,
    Live2DManager: FakeManager,
    AgentHUD: { _createAgentPopupContent() {} },
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
      }
    },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    console: { log() {}, warn() {}, error() {} },
    setTimeout,
    clearTimeout,
    Promise,
    Map,
    Set,
  };
  context.dispatchEvent = (evt) => { dispatchedEvents.push(evt.type); };
  context.window = context;

  vm.createContext(context);
  vm.runInContext(fs.readFileSync(POPUP_PATH, 'utf8'), context, { filename: POPUP_PATH });
  vm.runInContext("AvatarPopupMixin.apply(Live2DManager.prototype, 'live2d');", context);

  const mgr = new FakeManager();
  mgr._animationDurationMs = 25;
  context.window.live2dManager = mgr;
  const container = makeNode('div', 'live2d-floating-buttons');
  const btnWrapper = makeNode('div');
  const agentPopup = mgr.createPopup('agent');
  btnWrapper.appendChild(agentPopup);
  container.appendChild(btnWrapper);
  body.appendChild(container);

  context.window.universalTutorialManager = { constructor: { detectModelPrefix: () => 'live2d' } };
  context.window.NekoClickGuide = {
    resolveTarget: (sel) => (sel.startsWith('#') ? findById(body, sel.slice(1)) : null),
    waitUntil: async (cond) => cond(),
  };
  vm.runInContext(fs.readFileSync(HOME_STEPS_PATH, 'utf8'), context, { filename: HOME_STEPS_PATH });

  // Simulate open agent menu before tutorial starts, using real AvatarPopupMixin.closeAllPopups & showPopup.
  agentPopup.style.display = 'flex';
  agentPopup.style.opacity = '1';

  const cleanup = await context.window.NekoClickGuide.prepareFloating();
  await cleanup();

  // Wait past _animationDurationMs + nested rAF frames so an uncancelled hide timer would fire.
  await new Promise((resolve) => setTimeout(resolve, 60));

  assert.equal(agentPopup._hideTimeoutId, null);
  assert.equal(agentPopup.isConnected, true);
  assert.equal(agentPopup.style.display, 'flex');
  assert.equal(agentPopup.style.opacity, '1');
  assert.ok(dispatchedEvents.includes('neko-popup-opening'));
});
