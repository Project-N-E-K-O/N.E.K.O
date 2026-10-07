// Real soccer registration, provider, SDK and playback bridge; neutral HTTP
// and engine substitutes keep this test independent of user data and cloud TTS.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

async function main() {
  const { pathToFileURL } = require('node:url');
  const THREE = await import(pathToFileURL(path.join(root, 'static/libs/three.module.js')).href);
  const timers = new Map();
  const intervals = new Map();
  const listeners = new Map();
  const frames = new Map();
  const channels = new Set();
  const requests = [];
  let serial = 0;
  let lastSpeech;
  let mouth = 0;
  const add = (type, callback) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(callback);
  };
  const remove = (type, callback) => listeners.get(type)?.delete(callback);
  const emit = (type, detail) => { for (const fn of [...(listeners.get(type) || [])]) fn({ type, detail }); };
  const launch = {
    textContent: read('templates/soccer_demo.html').match(/id="neko-minigame-host-launch"[^>]*>([\s\S]*?)<\/script>/)[1],
    remove() {},
  };
  const container = { clientWidth: 200, clientHeight: 300 };
  const document = { hidden: false, visibilityState: 'visible', addEventListener: add, removeEventListener: remove,
    getElementById: id => id === 'neko-minigame-host-launch' ? launch : container,
    createElement: () => ({ remove() {} }) };
  const model = { width: 200, height: 300, scale: { x: 1, y: 1, set() {} },
    position: { set() {} }, anchor: { set() {} }, internalModel: {
      settings: { url: '/neko.model3.json' }, coreModel: {
        getParameterIndex: id => id === 'ParamMouthOpenY' ? 0 : -1,
        setParameterValueById(_id, value) { mouth = value; },
      },
    } };
  class Manager {
    constructor() {
      this.core = { init: async () => {
        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(30, 1, .01, 100);
        this.camera.position.z = 5;
        this.renderer = {};
      } };
      this.animation = { startLipSync: analyser => { this.analyser = analyser; },
        stopLipSync: () => { this.analyser = null; } };
    }
    startAnimateLoop() {}
    pauseRendering() {}
    resumeRendering() {}
    async playVRMAAnimation() {}
    dispose() { this.analyser = null; this.currentModel = null; }
  }
  const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
  const window = { THREE, document, console, AbortController, navigator: {},
    location: { origin: 'http://localhost', search: '' },
    lanlan_config: { lanlan_name: 'Neko' },
    addEventListener: add, removeEventListener: remove,
    setTimeout: fn => { const id = {}; timers.set(id, fn); return id; }, clearTimeout: id => timers.delete(id),
    setInterval: fn => { const id = {}; intervals.set(id, fn); return id; }, clearInterval: id => intervals.delete(id),
    requestAnimationFrame: fn => { const id = {}; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    BroadcastChannel: class { constructor(name) { this.name = name; channels.add(this); } postMessage() {} close() { channels.delete(this); } },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {}, length: 0 },
    nekoLocalMutationSecurity: { getMutationHeaders: async () => ({}), peekCachedToken: () => '' },
    VRMManager: Manager,
    live2dManager: { currentModel: model, async initPIXI() {}, async loadModel() { return model; },
      pauseRendering() {}, resumeRendering() {}, destroy() {} },
    loadTestVrmModule: async name => name === 'loader' ? { GLTFLoader: class {
      register() {} load(_path, resolve) { resolve({ userData: { vrm: {
        scene: new THREE.Mesh(new THREE.BoxGeometry(1, 2, .5)), meta: { metaVersion: '1' },
      } } }); }
    } } : { VRMLoaderPlugin: class {}, VRMUtils: { deepDispose() {} } },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : {};
      requests.push({ url, payload });
      if (url.includes('/character')) return response({ lanlan_name: 'Neko', model_type: 'vrm', vrm_path: '/neko.vrm' });
      if (url.endsWith('/route/start')) return response({ ok: true, state: { game_route_active: true,
        lanlan_name: 'Neko', session_id: payload.session_id, sdk_route_instance_id: payload.sdk_route_instance_id } });
      if (url.endsWith('/speak')) { lastSpeech = payload; return response({ ok: true, audio_sent: true, speech_id: `speech-${++serial}` }); }
      return response({ ok: true });
    },
  };
  const context = vm.createContext({ window, document, console, URL, URLSearchParams, AbortController,
    performance, setTimeout, clearTimeout, Uint8Array, Response });
  const run = name => vm.runInContext(read(name), context, { filename: name });
  document.head = { appendChild(script) {
    document.currentScript = script; run('static/game/sdk/neko-minigame-same-origin-host.js');
    document.currentScript = null; script.onload?.();
  } };
  run('static/game/sdk/neko-minigame-avatar-host.js');
  vm.runInContext(read('static/game/games/soccer/soccer-avatar-host.js')
    .replace("import('three/addons/loaders/GLTFLoader.js')", "window.loadTestVrmModule('loader')")
    .replace("import('@pixiv/three-vrm')", "window.loadTestVrmModule('vrm')"), context);
  run('static/game/games/soccer/soccer-neko-host-registration.js');
  run('static/game/sdk/neko-minigame-same-origin-bootstrap.js');
  await window.nekoMiniGameSameOriginHostReady;
  run('static/game/games/soccer/soccer-neko-adapter.js');
  run('static/game/sdk/neko-minigame-sdk.js');
  const host = await window.createSoccerNekoAdapter();
  const page = read('static/game/games/soccer/soccer-demo.js');
  assert(!page.includes('avatarHost:') && !page.includes('__SoccerAvatarHost'), 'page still constructs a legacy Avatar provider');
  const start = page.indexOf('const SOCCER_AVATAR_LAYOUT');
  const end = page.indexOf('\n    async function loadSoccerAvatars()', start);
  assert(start >= 0 && end > start);
  vm.runInContext(page.slice(start, end), context);
  window.__SoccerResolvedLanlanName = 'Neko';
  const game = await window.NekoMiniGame.connect({ id: 'soccer', version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'avatar-renderer', 'speech-output'] },
  { transport: host, windowImpl: window, documentImpl: document });
  const mountConfig = (slot, type, path) => context.soccerAvatarMountConfig(slot, { type, path });
  for (const type of ['vrm', 'mmd', 'live2d', 'pngtuber']) {
    assert.equal(mountConfig('ai', type, '/fixture').fit.mode,
      ['vrm', 'mmd'].includes(type) ? 'height' : 'contain');
  }
  const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
  const playback = (patch = {}) => emit('neko-speech-playback-state', { type: 'speech_playback_state',
    active: true, speechId: `speech-${serial}`, correlationId: lastSpeech.sdk_speech_correlation_id,
    remainingSeconds: 2, updatedAt: Date.now(), audioContextState: 'running',
    mouthFrame: { bins: Array(16).fill(100), rms: 0.2, sampleRate: 12000 }, ...patch });
  try {
    assert.equal((await game.runtime.bindCharacter()).name, 'Neko');
    assert.equal(game.runtime.session.characterName, 'Neko');
    let player = await game.avatar.mount(mountConfig('player', 'vrm', '/player.vrm'));
    const playerManager = window.vrmManager;
    game.runtime.configure({ pageExit: true, heartbeat: false, outputs: false });
    await game.runtime.start();
    await game.speech.speak({ text: 'Before AI loaded' }); playback(); await flush();
    assert(!playerManager.analyser, 'the sole player renderer accepted assistant speech');
    playback({ active: false, remainingSeconds: 0 }); await flush();
    let ai = await game.avatar.mount(mountConfig('ai', 'vrm', '/neko.vrm'));
    const aiManager = window.aiVrmManager;
    await game.speech.speak({ text: 'Neutral test' }); await flush();
    assert(!aiManager.analyser, 'HTTP acceptance started lip sync');
    playback(); await flush();
    assert(aiManager.analyser, 'actual SDK playback did not reach soccer aiVrmManager');
    assert(!playerManager.analyser, 'AI speech animated the player');
    ai.pause(); await flush(); assert(!aiManager.analyser);
    ai.resume(); await flush(); assert(aiManager.analyser);
    playback({ correlationId: 'another-window', speechId: 'other' }); await flush();
    assert(!aiManager.analyser, 'unowned speech animated the AI');
    playback(); await flush();
    await ai.setModel({ type: 'live2d', path: '/neko.model3.json' }); await flush();
    assert(!aiManager.analyser, 'model replacement retained retired VRM speech');
    playback(); await flush(); assert(mouth > 0 && frames.size === 1);
    playback({ active: false, remainingSeconds: 0 }); await flush(); assert.equal(mouth, 0); assert.equal(frames.size, 0);
    playback(); await flush();
    await game.runtime.end(); await flush(); assert.equal(mouth, 0); assert.equal(frames.size, 0);
    playback(); await flush(); assert.equal(mouth, 0, 'ended route accepted late playback');
    player.dispose(); ai.dispose();
    game.runtime.reset(); await game.runtime.bindCharacter('Neko');
    player = await game.avatar.mount(mountConfig('player', 'vrm', '/player.vrm'));
    ai = await game.avatar.mount(mountConfig('ai', 'live2d', '/neko.model3.json'));
    await game.runtime.start();
    playback(); await flush(); assert.equal(mouth, 0, 'new route accepted old speech');
    await game.speech.speak({ text: 'New generation' }); playback(); await flush(); assert(mouth > 0);
    emit('pagehide', {}); await flush();
    assert.equal(mouth, 0); assert.equal(frames.size, 0);
    assert(player.disposed && ai.disposed, 'page exit did not release controllers');
  } finally { game.dispose(); await flush(); }
  assert.equal(timers.size, 0, 'timers survived disposal');
  assert.equal(intervals.size, 0, 'intervals survived disposal');
  assert.equal(channels.size, 0, 'channels survived disposal');
  assert([...listeners.values()].every(set => set.size === 0), 'listeners survived disposal');
  console.log('soccer public Avatar automatic speech integration passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
