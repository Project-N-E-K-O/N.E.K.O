const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const timers = new Map();
  let nextTimer = 0;
  const windowImpl = {
    AbortController, console,
    setTimeout(fn) { const id = ++nextTimer; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  global.window = windowImpl;
  const root = path.resolve(__dirname, '../..');
  // Exercise the real player's publisher (not a copied capture function) in
  // a separate window, then route its emitted snapshot through the SDK below.
  const playerSource = fs.readFileSync(path.join(root, 'static/app/app-audio-playback.js'), 'utf8');
  const playerWindow = { appConst: {}, addEventListener() {}, dispatchEvent() {},
    appState: { globalAnalyser: { frequencyBinCount: 1024, context: { sampleRate: 48000 },
      getByteFrequencyData(target) { target.fill(100); },
      getByteTimeDomainData(target) { target.fill(160); } },
    audioPlayerContext: { currentTime: 1, state: 'running', sampleRate: 48000 },
    scheduledSources: [{}], audioBufferQueue: [], pendingAudioChunkMetaQueue: [], incomingAudioBlobQueue: [],
    nextChunkTime: 3, currentPlayingSpeechId: 'player', currentPlayingSpeechCorrelationId: 'sdk-test',
    assistantSpeechPlaybackStartAudioTime: 0.5 } };
  const stored = new Map();
  const messages = [];
  const playerSandbox = { window: playerWindow, console,
    document: { getElementById() { return null; }, addEventListener() {} }, navigator: {},
    localStorage: { setItem(key, value) { stored.set(key, value); } },
    BroadcastChannel: class { postMessage(message) { messages.push(message); } },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    setTimeout() { return 1; }, clearTimeout() {} };
  // Only expose the closure publisher to the harness; production exports stay unchanged.
  vm.runInNewContext(playerSource.replace('mod.clearAudioQueue = clearAudioQueue;',
    'window.testPublish = publishSpeechPlaybackState; mod.clearAudioQueue = clearAudioQueue;'), playerSandbox);
  const sampled = playerWindow.testPublish('test');
  assert.equal(sampled.mouthFrame.bins.length, 256);
  assert.equal(sampled.mouthFrame.sampleRate, 12000);
  assert.equal(sampled.mouthFrame.rms, 0.25);
  assert.equal(stored.size, 1, 'only the existing latest-state slot is used');
  assert.equal(messages.length, 1);
  playerWindow.appState.audioPlayerContext.state = 'suspended';
  assert.equal(playerWindow.testPublish('test').mouthFrame, null);
  playerWindow.appState.audioPlayerContext.state = 'running';
  playerWindow.appState.scheduledSources = [];
  assert.equal(playerWindow.testPublish('test').mouthFrame, null, 'queued audio is not audible');
  playerWindow.appState.scheduledSources = [{}];
  assert.equal(playerWindow.testPublish('test', { active: false }).mouthFrame, null);
  playerWindow.appState.currentPlayingSpeechCorrelationId = '';
  assert.equal(playerWindow.testPublish('test').mouthFrame, null, 'unrelated speech is not sampled');

  vm.runInThisContext(fs.readFileSync(path.join(root, 'static/game/sdk/neko-minigame-avatar-host.js'), 'utf8'));
  const analyser = windowImpl.NekoMiniGameAvatarHost.createSpeechAnalyser();
  analyser.update(sampled.mouthFrame);
  const bytes = new Uint8Array(256);
  analyser.getByteFrequencyData(bytes);
  assert.equal(bytes[0], 100);
  analyser.getByteTimeDomainData(bytes);
  assert.equal(bytes[0], 96);
  analyser.clear(); analyser.getByteFrequencyData(bytes);
  assert(bytes.every(value => value === 0));
  vm.runInThisContext(fs.readFileSync(path.join(root, 'static/game/sdk/neko-minigame-sdk.js'), 'utf8'));
  let bridge;
  let lastSpeech;
  let serial = 0;
  let blocker = null;
  let speechMode = 'success';
  let failManual = false;
  const calls = new Map();
  const manual = new Map();
  const transport = {
    logger: { log() {}, info() {}, warn() {}, error() {}, reset() {}, flush() {}, enable() {}, enableAfterRouteStart() {} },
    connectGame({ manifest }) {
      return { accepted: true, protocolVersion: '1', hostVersion: '1',
        registration: { mode: 'development', gameId: manifest.id, version: manifest.version },
        grantedCapabilities: manifest.requiredCapabilities };
    },
    getRuntimeState: () => ({ sessionId: 'session', characterName: 'Neko' }),
    resetRuntime: () => ({ sessionId: 'session', characterName: 'Neko' }),
    applyRuntimeState() {},
    start: async () => ({ ok: true, state: { game_route_active: true, session_id: 'session' } }),
    end: async () => ({ ok: true }),
    heartbeat: async () => ({ ok: true, active: true }),
    drain: async () => ({ ok: true, outputs: [] }),
    startSpeechOutputBridge(options) { bridge = options; return true; },
    stopSpeechOutputBridge() {},
    requestSpeechOutput(payload, options) {
      lastSpeech = payload;
      const speechId = `speech-${++serial}`;
      if (speechMode === 'pending') return new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
      });
      return Promise.resolve({ ok: true, speech_id: speechId, audio_sent: speechMode !== 'failed' });
    },
    preloadSpeechOutput: async () => ({ ok: true }),
    mirrorSpeechOutput: async () => ({ ok: true }),
    mountAvatar(config) {
      const frames = [];
      calls.set(config.slot, frames);
      return { dispose() { manual.set(config.slot, false); },
        pause() { manual.set(config.slot, false); }, resume() {},
        async setModel() { manual.set(config.slot, false); },
        setSpeaking(active) {
          if (failManual) throw new Error('manual rejected');
          manual.set(config.slot, active); return true;
        },
        setSpeechPlayback(frame) {
          frames.push(frame);
          return (blocker || Promise.resolve()).then(() => { manual.set(config.slot, false); });
        } };
    },
    dispose() {},
  };
  const game = await windowImpl.NekoMiniGame.connect({ id: 'example-game', version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'speech-output', 'avatar-renderer'] },
  { transport, windowImpl, documentImpl: {} });
  const mount = (slot, characterName) => game.avatar.mount({ slot, characterName,
    model: { type: 'vrm', path: '/model.vrm' }, viewport: { mode: 'fixed', width: 200, height: 300 },
    resize: { mode: 'fixed' } });
  const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
  const avatar = await mount('opponent', 'Neko');
  const other = await mount('other', 'Other');
  const emit = (patch = {}) => bridge.onState({ type: 'speech_playback_state', active: true,
    speechId: `speech-${serial}`, correlationId: lastSpeech.sdk_speech_correlation_id,
    remainingSeconds: 2, audioContextState: 'running', updatedAt: Date.now(),
    mouthFrame: sampled.mouthFrame, ...patch }, 'broadcast_channel');
  try {
    await game.runtime.start();
    await game.speech.speak({ text: 'Hello' });
    await flush();
    assert(!calls.get('opponent').some(frame => frame.active), 'HTTP acceptance must not open the mouth');
    emit(); await flush();
    assert(calls.get('opponent').at(-1)?.active, 'SDK speech must automatically drive the mounted character');
    assert.equal(calls.get('opponent').at(-1).mouthFrame.rms, 0.25);
    emit({ mouthFrame: { ...sampled.mouthFrame, bins: Array(257).fill(1) } }); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'oversized samples must not reach renderers');
    emit(); await flush();
    assert(!calls.get('other').some(frame => frame.active), 'another character must not speak');
    emit({ audioContextState: 'suspended' }); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false);
    emit(); await flush();
    avatar.pause(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false);
    avatar.resume(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, true);
    emit({ speechId: 'unrelated', correlationId: 'unrelated' }); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false);
    emit(); await flush();
    for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
    await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'missing stop must expire');
    let release;
    blocker = new Promise(resolve => { release = resolve; });
    const before = calls.get('opponent').length;
    emit(); await flush();
    for (let i = 0; i < 30; i++) emit();
    await flush();
    assert.equal(calls.get('opponent').length, before + 1, 'slow renderer must not accumulate updates');
    emit({ active: false, remainingSeconds: 0 });
    blocker = null; release(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'coalesced stop must win');
    await game.speech.speak({ text: 'Next' });
    emit(); await flush();
    await game.runtime.end(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false);
    emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'late exited speech must not restart');
    game.runtime.reset(); await game.runtime.start();
    emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'old generation must not restart');
    await game.speech.speak({ text: 'New generation' }); emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, true);
    speechMode = 'failed';
    await game.speech.speak({ text: 'Unavailable' }); emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'failed delivery must not animate');
    speechMode = 'pending';
    const cancel = new AbortController();
    const pending = game.speech.speak({ text: 'Pending' }, { signal: cancel.signal }).catch(() => null);
    await flush(); emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, true, 'playback before HTTP completion must animate');
    cancel.abort(); await pending; await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'request cancellation must stop');
    emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, false, 'cancelled owner must not reanimate');
    await avatar.setSpeaking(true);
    emit({ speechId: 'unrelated', correlationId: 'unrelated' }); await flush();
    assert.equal(manual.get('opponent'), true, 'automatic silence cancelled manual speech');
    other.dispose(); await flush();
    assert.equal(manual.get('opponent'), true, 'renderer disposal cancelled manual speech');
    avatar.pause(); avatar.resume(); await flush();
    await avatar.setModel({ type: 'vrm', path: '/replacement.vrm' }); await flush();
    assert.equal(manual.get('opponent'), true, 'model/pause transition lost manual intent');
    await avatar.setSpeaking(false); await flush();
    assert.equal(manual.get('opponent'), false);
    failManual = true;
    await assert.rejects(avatar.setSpeaking(true));
    failManual = false;
    speechMode = 'success';
    await game.speech.speak({ text: 'Automatic after manual failure' }); emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, true, 'manual failure retained ownership');
    let finishOldFrame;
    blocker = new Promise(resolve => { finishOldFrame = resolve; });
    emit({ active: false }); await flush();
    const manualPending = avatar.setSpeaking(true);
    await flush();
    await assert.rejects(avatar.setSpeaking(false), { code: 'busy' });
    for (let i = 0; i < 30; i++) emit({ speechId: 'unrelated', correlationId: 'unrelated' });
    blocker = null; finishOldFrame();
    await manualPending; await flush();
    assert.equal(manual.get('opponent'), true, 'older asynchronous silence overwrote manual speaking');
    await avatar.setSpeaking(false);
    await game.speech.speak({ text: 'Automatic ownership restored' }); emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, true, 'manual release did not restore automatic playback');
    await avatar.setSpeaking(true);
    await game.runtime.end(); await flush();
    assert.equal(manual.get('opponent'), false, 'route exit retained manual motion');
  } finally {
    avatar.dispose(); other.dispose(); game.dispose();
    await flush();
  }
  assert.equal(timers.size, 0, 'disposal must release the mouth watchdog');
  console.log('mini-game automatic Avatar speech runtime test passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
