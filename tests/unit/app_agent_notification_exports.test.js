'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_AGENT_PATH = path.resolve(__dirname, '../../static/app/app-agent.js');

function loadAppAgent() {
  const statusEl = {
    textContent: '',
    attributes: new Map([['data-i18n', 'agent.idle']]),
    style: { color: '' },
    removeAttribute(name) {
      this.attributes.delete(name);
    },
  };
  const alerts = [];
  const listeners = new Map();

  const windowObj = {
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener() {},
    showAlert(msg, title) {
      alerts.push({ msg, title });
      return Promise.resolve();
    },
    t(key) {
      return `t:${key}`;
    },
  };

  const documentObj = {
    getElementById(id) {
      return id === 'live2d-agent-status' ? statusEl : null;
    },
    addEventListener() {},
    removeEventListener() {},
  };

  const timers = [];
  const context = {
    window: windowObj,
    document: documentObj,
    console: { log() {}, warn() {}, error() {} },
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    setTimeout(fn, delay) {
      const id = timers.length + 1;
      timers.push({ id, fn, delay });
      return id;
    },
    clearTimeout() {},
    setInterval() { return 1; },
    clearInterval() {},
    Date,
    Promise,
    Map,
    Set,
    Array,
    String,
    Object,
  };
  windowObj.window = windowObj;
  windowObj.document = documentObj;

  const source = fs.readFileSync(APP_AGENT_PATH, 'utf8');
  vm.runInNewContext(source, context, { filename: APP_AGENT_PATH });
  return { windowObj, statusEl, alerts };
}

test('F0587: exposes setFloatingAgentStatus and maybeShowContentFilterModal on window', async () => {
  const { windowObj, statusEl, alerts } = loadAppAgent();

  assert.equal(typeof windowObj.setFloatingAgentStatus, 'function');
  assert.equal(typeof windowObj.maybeShowContentFilterModal, 'function');
  assert.equal(windowObj.appAgent.setFloatingAgentStatus, windowObj.setFloatingAgentStatus);
  assert.equal(windowObj.appAgent.maybeShowContentFilterModal, windowObj.maybeShowContentFilterModal);

  windowObj.setFloatingAgentStatus('Agent failed', 'failed');
  assert.equal(statusEl.textContent, 'Agent failed');
  assert.equal(statusEl.attributes.has('data-i18n'), false);
  assert.equal(statusEl.style.color, '#ff4d4f');

  windowObj.maybeShowContentFilterModal('data_inspection_failed');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(alerts.length, 1);
  assert.equal(alerts[0].title, 't:common.alert');
  assert.equal(alerts[0].msg, 't:agent.contentFilterError');
});
