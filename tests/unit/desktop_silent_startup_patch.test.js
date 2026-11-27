'use strict';

// Exercise the proposed patch's actual case body without requiring private
// desktop modules. This is not a production saveConfig or Electron integration.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const sourcePath = 'src/main/desktop-settings-actions.js';
const patch = fs.readFileSync(path.resolve(__dirname,
  '../../docs/design/desktop-silent-startup/electron-silent-startup.patch'), 'utf8');
const sectionStart = patch.indexOf('diff --git a/' + sourcePath + ' b/' + sourcePath + '\n');
assert.notEqual(sectionStart, -1, 'the settings-actions patch is present');
const sectionEnd = patch.indexOf('\ndiff --git ', sectionStart + 1);
const section = patch.slice(sectionStart, sectionEnd === -1 ? undefined : sectionEnd);
const after = section.split('\n')
  .filter(line => line.startsWith(' ') || (line.startsWith('+') && !line.startsWith('+++')))
  .map(line => line.slice(1)).join('\n');
const matches = [...after.matchAll(/^      case 'silentStartup': \{\n[\s\S]*?^      \}/gm)];
assert.equal(matches.length, 1, 'exactly one proposed silentStartup case is exercised');
const createApplyPatch = new Function('ensureAppConfig', 'saveConfig', 'notifyChanged',
  "return function applyPatch(key, value) { switch (key) {\n" +
  matches[0][0] + "\n default: return false; } };");

function harness(config, saveConfig, onNotify = () => {}) {
  let writes = 0;
  let notifications = 0;
  const applyPatch = createApplyPatch(() => config,
    (next, options) => { writes++; return saveConfig(next, options); },
    () => { notifications++; onNotify(); });
  return { applyPatch, counts: () => ({ writes, notifications }) };
}

const nonSuccess = [
  ['false', () => false],
  ['undefined', () => undefined],
  ['null', () => null],
  ['zero', () => 0],
  ['one', () => 1],
  ['true string', () => 'true'],
  ['false string', () => 'false'],
  ['object', () => ({ ok: true })],
  ['array', () => []],
  ['boxed true', () => new Boolean(true)],
  ['Promise of true', () => Promise.resolve(true)],
  ['Promise of false', () => Promise.resolve(false)],
];

for (const [label, result] of nonSuccess) {
  test('patch does not publish a write acknowledged with ' + label, () => {
    for (const previous of [false, true]) {
      const config = { silentStartup: previous, autoLaunch: true };
      const h = harness(config, result);
      assert.equal(h.applyPatch('silentStartup', !previous), false);
      assert.equal(config.silentStartup, previous);
      assert.equal(config.autoLaunch, true);
      assert.deepEqual(h.counts(), { writes: 1, notifications: 0 });
    }
  });
}

test('patch publishes only after synchronous boolean true and preserves other fields', () => {
  for (const previous of [false, true]) {
    const config = { silentStartup: previous, autoLaunch: true };
    let acknowledged = false;
    const h = harness(config, (next, options) => {
      assert.notEqual(next, config, 'persistence receives a copy');
      assert.deepEqual(next, { silentStartup: !previous, autoLaunch: true });
      assert.deepEqual(options, { logDetails: false });
      assert.equal(config.silentStartup, previous, 'live preference is not published early');
      acknowledged = true;
      return true;
    }, () => {
      assert.equal(acknowledged, true);
      assert.equal(config.silentStartup, !previous);
    });
    assert.equal(h.applyPatch('silentStartup', !previous), true);
    assert.deepEqual(h.counts(), { writes: 1, notifications: 1 });
  }
});

test('patch propagates a thrown write without publishing or notifying', () => {
  const config = { silentStartup: true };
  const failure = new Error('fixture ENOSPC');
  const h = harness(config, () => { throw failure; });
  assert.throws(() => h.applyPatch('silentStartup', false), error => error === failure);
  assert.equal(config.silentStartup, true);
  assert.deepEqual(h.counts(), { writes: 1, notifications: 0 });
});

test('patch rejects non-boolean IPC values before persistence', () => {
  const config = { silentStartup: false };
  const h = harness(config, () => true);
  for (const value of [undefined, null, 0, 1, 'false', 'true', {}, []]) {
    assert.equal(h.applyPatch('silentStartup', value), false);
  }
  assert.equal(config.silentStartup, false);
  assert.deepEqual(h.counts(), { writes: 0, notifications: 0 });
});

test('patch rejects a missing app configuration without writing', () => {
  const h = harness(null, () => true);
  assert.equal(h.applyPatch('silentStartup', true), false);
  assert.deepEqual(h.counts(), { writes: 0, notifications: 0 });
});
