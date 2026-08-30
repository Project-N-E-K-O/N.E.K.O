const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

class FakeAudio {
  constructor(src = '') {
    this.initialSrc = String(src || '');
    this.src = this.initialSrc;
    this.currentSrc = this.src;
    this.currentTime = 0;
    this.volume = 1;
    this.loop = false;
    this.preload = '';
    this.networkState = 1;
    this.readyState = 4;
    this.error = null;
    this.paused = true;
    this.listeners = new Map();
    this.disposed = false;
    FakeAudio.instances.push(this);
  }

  addEventListener(type, handler, options = {}) {
    let entries = this.listeners.get(type);
    if (!entries) {
      entries = new Set();
      this.listeners.set(type, entries);
    }
    entries.add({ handler, once: options.once === true });
  }

  dispatch(type, event = { type }) {
    for (const entry of Array.from(this.listeners.get(type) || [])) {
      entry.handler(event);
      if (entry.once) this.listeners.get(type)?.delete(entry);
    }
  }

  play() {
    this.paused = false;
    return Promise.resolve();
  }

  pause() { this.paused = true; }
  load() { if (!this.src) this.disposed = true; }
  cloneNode() { return new FakeAudio(this.initialSrc); }
}
FakeAudio.instances = [];

function logger() {
  return {
    log() {}, info() {}, warn() {}, error() {},
    enable() {}, enableAfterRouteStart() {}, flush() {}, reset() {},
  };
}

async function main() {
  const storage = new Map();
  const windowMock = {
    console: { error() {}, warn() {} },
    localStorage: {
      getItem(key) { return storage.has(key) ? storage.get(key) : null; },
      setItem(key, value) { storage.set(key, String(value)); },
    },
    setInterval,
    clearInterval,
    setTimeout,
    clearTimeout,
  };
  global.window = windowMock;
  global.Audio = FakeAudio;

  for (const relativePath of [
    '../../static/game/system/game-audio-system.js',
    '../../static/game/sdk/neko-minigame-sdk.js',
    '../../static/game/sdk/neko-minigame-audio-host.js',
  ]) {
    const sourcePath = path.resolve(__dirname, relativePath);
    vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });
  }

  const host = window.NekoMiniGameAudioHost.create({
    AudioSystem: window.NekoGameSystem.GameAudioSystem,
    audioFactory: (src) => new FakeAudio(src),
    maxControllers: 4,
  });
  const transport = {
    logger: logger(),
    connectGame(request) {
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'audio-test-host',
        registration: {
          mode: 'registered',
          gameId: request.manifest.id,
          version: request.manifest.version,
        },
        grantedCapabilities: [
          ...request.manifest.requiredCapabilities,
          ...request.manifest.optionalCapabilities,
        ],
      };
    },
    configureLogger() {},
    mountAudio(config) { return host.mount(config); },
    dispose() { host.dispose(); },
  };
  const game = await window.NekoMiniGame.connect({
    id: 'audio-test',
    version: '1.0.0',
    requiredCapabilities: ['logging', 'audio'],
  }, { transport, windowImpl: windowMock, documentImpl: {} });

  assert(game.capabilities.has('audio'), 'audio capability was not granted');
  const audio = await game.audio.mount({
    slot: 'main',
    resources: {
      bgm: { menu: ['/menu-a.mp3', '/menu-b.mp3'] },
      loopedBgm: {
        match: { intro: '/intro.mp3', loop: '/loop.mp3', outro: '/outro.mp3' },
      },
      sfx: { kick: '/kick.mp3' },
    },
    settings: {
      fadeMs: 0,
      persistVolume: false,
      maxConcurrent: 2,
      maxPreloadEntries: 2,
      maxPlaylistHistory: 2,
      maxEndWaiters: 2,
    },
  });
  assert(game.audio.activeCount === 1 && host.activeCount === 1,
    'mounted audio controller count is invalid');
  assert(audio.system === undefined, 'public audio controller exposed the trusted audio system');

  audio.preloadBgm(['/preload-a.mp3', '/preload-b.mp3', '/preload-c.mp3']);
  const firstPreload = FakeAudio.instances.find((item) => item.initialSrc === '/preload-a.mp3');
  assert(firstPreload?.disposed, 'BGM preload cache did not evict and dispose its oldest entry');

  assert(audio.setBgmVolume(0.6) === 0.6, 'BGM volume was not applied');
  assert(audio.setSfxVolume(0.4) === 0.4, 'SFX volume was not applied');
  assert(await audio.playBgm('menu', { id: 'menu', repeat: false }), 'BGM did not start');
  assert(audio.isCurrentBgm('menu'), 'current BGM identity was not exposed');
  assert(audio.getState().bgmVolume === 0.6, 'audio state lost BGM volume');

  const waiterOne = audio.waitForBgmEnd();
  const waiterTwo = audio.waitForBgmEnd();
  const waiterThree = audio.waitForBgmEnd();
  assert(await waiterOne === false, 'BGM waiter limit did not release its oldest waiter');
  audio.stopBgm();
  assert(await waiterTwo === false && await waiterThree === false,
    'BGM stop did not release remaining completion waiters');

  await audio.playSfx('kick');
  await audio.playSfx('kick');
  await audio.playSfx('kick');
  const kickInstances = FakeAudio.instances.filter((item) => item.initialSrc === '/kick.mp3');
  assert(kickInstances.some((item) => item.disposed),
    'SFX concurrency limit did not release the oldest playback instance');

  const errors = [];
  const unsubscribeError = audio.onError((payload) => errors.push(payload));
  await audio.playBgm({ src: '/broken.mp3' }, { repeat: false });
  const broken = [...FakeAudio.instances].reverse().find((item) => item.initialSrc === '/broken.mp3');
  broken.error = { code: 4, message: 'decode failed' };
  broken.dispatch('error', { type: 'error' });
  assert(errors.length === 1 && errors[0].channel === 'bgm' && errors[0].src.includes('broken.mp3'),
    'audio errors were not normalized through the public controller');
  unsubscribeError();

  for (const slot of ['secondary', 'third', 'fourth']) {
    await game.audio.mount({ slot, resources: {}, settings: { persistVolume: false } });
  }
  let limitError = null;
  try {
    await game.audio.mount({ slot: 'fifth', resources: {} });
  } catch (error) { limitError = error; }
  assert(limitError?.code === 'busy', 'SDK audio controller limit was not enforced');

  let oversizedError = null;
  const separateHost = window.NekoMiniGameAudioHost.create({
    AudioSystem: window.NekoGameSystem.GameAudioSystem,
    audioFactory: (src) => new FakeAudio(src),
  });
  const separateGame = await window.NekoMiniGame.connect({
    id: 'audio-size-test',
    version: '1.0.0',
    requiredCapabilities: ['logging', 'audio'],
  }, {
    transport: {
      logger: logger(),
      connectGame: transport.connectGame,
      mountAudio(config) { return separateHost.mount(config); },
      dispose() { separateHost.dispose(); },
    },
    windowImpl: windowMock,
    documentImpl: {},
  });
  try {
    await separateGame.audio.mount({
      slot: 'main',
      resources: { sfx: { excessive: Array.from({ length: 257 }, (_, index) => `/sfx-${index}.mp3`) } },
    });
  } catch (error) { oversizedError = error; }
  assert(oversizedError?.code === 'invalid_request', 'oversized audio resources were not rejected');
  separateGame.dispose();

  game.dispose();
  assert(game.audio.activeCount === 0 && host.activeCount === 0,
    'client disposal did not release all audio controllers');
  assert(audio.disposed, 'public audio controller did not enter disposed state');

  process.stdout.write('mini-game audio host runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
