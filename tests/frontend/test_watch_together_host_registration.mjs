import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

// The same-origin adapter no longer accepts an injected avatarHost, so the page
// must reach avatar-renderer through its registered avatarHostFactory.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const template = read('templates/watch_together.html');
const hostModule = read('static/game/games/watch-together/host.mjs');
const REGISTRATION = '/static/game/games/watch-together/watch-together-neko-host-registration.js';

const launchStart = template.indexOf('id="neko-minigame-host-launch"');
const registrationAt = template.indexOf(`<script src="${REGISTRATION}?v={{ static_asset_version }}"></script>`);
const bootstrapAt = template.indexOf('neko-minigame-same-origin-bootstrap.js');
assert.ok(launchStart >= 0 && registrationAt > launchStart && bootstrapAt > registrationAt,
  'the provider registration must load after the launch node and before the bootstrap consumes it');

const factoryCall = hostModule.match(/factory\(\{([^}]*)\}\)/)?.[1];
assert.ok(factoryCall && !/\bavatarHost\b/.test(factoryCall), 'host.mjs still relies on the removed avatarHost injection');
const globalAt = hostModule.indexOf('window.createWatchTogetherAvatarHost');
assert.ok(globalAt >= 0 && globalAt < hostModule.indexOf('factory({'),
  'host.mjs must define its Avatar host before creating the adapter');
const requiredCapabilities = hostModule.match(/requiredCapabilities:\[([^\]]*)\]/)[1]
  .split(',').map(item => item.trim().replace(/'/g, ''));
assert.ok(requiredCapabilities.includes('avatar-renderer'));

const launchJson = template.match(/<script id="neko-minigame-host-launch" type="application\/json">([\s\S]*?)<\/script>/)[1]
  .replaceAll('{{ static_asset_version }}', 'test');
const classicScripts = [...template.matchAll(/<script src="([^"?]+)(?:\?[^"]*)?"><\/script>/g)]
  .map(match => match[1])
  .filter(src => ['/static/game/sdk/neko-minigame-avatar-host.js', REGISTRATION,
    '/static/game/sdk/neko-minigame-same-origin-bootstrap.js'].includes(src));

async function compose({withRegistration}) {
  const origin = 'http://127.0.0.1:48911';
  const runFile = src => {
    const file = path.join(root, src.replace(/^\//, ''));
    vm.runInThisContext(fs.readFileSync(file, 'utf8'), {filename: file});
  };
  const launchNode = {textContent: launchJson, remove() {}};
  const container = {style: {getPropertyValue: () => '', setProperty() {}, removeProperty() {}}};
  const windowMock = {
    AbortController, URL, setTimeout, clearTimeout, setInterval, clearInterval,
    console: {warn() {}, error() {}, log() {}},
    fetch: async () => new Response('{"ok":true}', {headers: {'content-type': 'application/json'}}),
    navigator: {sendBeacon: () => false, locks: {request: async (_name, _options, callback) => callback()}},
    location: {origin, search: ''},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}, key: () => null, length: 0},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    crypto: {getRandomValues: values => values.fill(7)},
  };
  windowMock.document = {
    currentScript: null,
    getElementById: id => (id === 'neko-minigame-host-launch' ? launchNode : null),
    createElement: () => ({remove() {}}),
    head: {appendChild(script) {
      windowMock.document.currentScript = script;
      try { runFile(new URL(script.src || '/static/game/sdk/neko-minigame-same-origin-host.js', origin).pathname); }
      finally { windowMock.document.currentScript = null; }
      script.onload?.();
    }},
  };
  globalThis.window = windowMock;
  for (const src of classicScripts) {
    if (withRegistration || src !== REGISTRATION) runFile(src);
  }

  // Mirrors host.mjs: define the page-owned Avatar host, then create the adapter.
  let avatarHostsCreated = 0;
  windowMock.createWatchTogetherAvatarHost = () => {
    avatarHostsCreated += 1;
    return windowMock.NekoMiniGameAvatarHost.create({slots: {companion: {container, createController: () => ({dispose() {}})}}});
  };
  const factory = await windowMock.nekoMiniGameSameOriginHostReady;
  const transport = factory({gameType: 'watch-together', gameVersion: '1.0.0', mediaHost: {mount() {}}});
  const handshake = transport.connectGame({
    protocolVersions: ['1'],
    manifest: {id: 'watch-together', version: '1.0.0', requiredCapabilities, optionalCapabilities: []},
  });
  transport.dispose();
  return {granted: handshake.grantedCapabilities, avatarHostsCreated};
}

assert.deepEqual(classicScripts, [
  '/static/game/sdk/neko-minigame-avatar-host.js',
  REGISTRATION,
  '/static/game/sdk/neko-minigame-same-origin-bootstrap.js',
]);
const registered = await compose({withRegistration: true});
assert.deepEqual(registered.granted, requiredCapabilities);
assert.equal(registered.avatarHostsCreated, 1);

const unregistered = await compose({withRegistration: false});
assert.equal(unregistered.granted.includes('avatar-renderer'), false,
  'the negative control unexpectedly granted avatar-renderer without a registered factory');
assert.equal(unregistered.avatarHostsCreated, 0);
console.log('watch-together host registration: avatar-renderer granted through the registered factory');
