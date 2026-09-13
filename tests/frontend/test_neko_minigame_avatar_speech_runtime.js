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
  let rejectManual = false;
  let pauseFailure = '';
  let modelFailure = false;
  let manualBlocker = null;
  let modelBlocker = null;
  const calls = new Map();
  const manual = new Map();
  const manualCalls = [];
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
        pause() {
          if (pauseFailure === 'sync') throw new Error('pause rejected');
          if (pauseFailure === 'async') return Promise.reject(new Error('pause rejected'));
          manual.set(config.slot, false);
        }, resume() {},
        async setModel() {
          if (modelFailure) throw new Error('model rejected');
          if (modelBlocker) await modelBlocker;
          manual.set(config.slot, false); return 'model loaded';
        },
        setSpeaking(active) {
          manualCalls.push([config.slot, active]);
          if (failManual) throw new Error('manual rejected');
          if (rejectManual) return false;
          if (manualBlocker) return manualBlocker.then(() => {
            manual.set(config.slot, active); return active;
          });
          manual.set(config.slot, active); return true;
        },
        setSpeechPlayback: config.slot.startsWith('late-start-') ? undefined : function(frame) {
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
    for (const failure of ['sync', 'async']) {
      pauseFailure = failure;
      if (failure === 'sync') assert.throws(() => avatar.pause());
      else await assert.rejects(avatar.pause());
      pauseFailure = '';
      emit(); await flush();
      assert.equal(calls.get('opponent').at(-1).active, true, 'failed pause suppressed playback');
    }
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
    failManual = true;
    assert.equal(await avatar.setModel({ type: 'vrm', path: '/successful.vrm' }), 'model loaded',
      'optional speaking restoration replaced a successful model result');
    await flush();
    modelFailure = true;
    await assert.rejects(avatar.setModel({ type: 'vrm', path: '/failed.vrm' }),
      error => error.cause?.message === 'model rejected' || error.message.includes('model rejected'));
    modelFailure = false;
    failManual = false;
    await flush();
    await avatar.setSpeaking(false); await flush();
    assert.equal(manual.get('opponent'), false);
    failManual = true;
    await assert.rejects(avatar.setSpeaking(true));
    failManual = false;
    speechMode = 'success';
    await game.speech.speak({ text: 'Automatic after manual failure' }); emit(); await flush();
    assert.equal(calls.get('opponent').at(-1).active, true, 'manual failure retained ownership');
    rejectManual = true;
    assert.equal(await avatar.setSpeaking(true), false);
    const rejectedCount = calls.get('opponent').length;
    await game.speech.speak({ text: 'Automatic after declined manual activation' }); emit(); await flush();
    assert(calls.get('opponent').length > rejectedCount
      && calls.get('opponent').at(-1).active, 'declined manual activation retained automatic ownership');
    rejectManual = false;
    await avatar.setSpeaking(true);
    rejectManual = true;
    assert.equal(await avatar.setSpeaking(true), false);
    const ownedCount = calls.get('opponent').length;
    emit({ active: false }); await flush();
    assert.equal(calls.get('opponent').length, ownedCount, 'rejected update lost previous manual ownership');
    await avatar.setSpeaking(false); await flush();
    await avatar.pause();
    assert.equal(await avatar.setSpeaking(true), false);
    rejectManual = false;
    await avatar.resume(); await flush();
    assert.equal(manual.get('opponent'), true, 'paused manual intent did not resume');
    await avatar.setSpeaking(false); await flush();
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
    // An uncooperative automatic renderer cannot retain public manual callers.
    game.runtime.reset(); await game.runtime.start(); await flush();
    blocker = new Promise(() => {});
    const stuck = await mount('stuck', 'Neko'); await flush();
    const bounded = stuck.setSpeaking(true).catch(error => error);
    await flush();
    for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
    await flush();
    assert.equal((await Promise.race([bounded, Promise.resolve('unsettled')])).code, 'timeout');
    // Repeated timed-out callers use a removable single waiter, not .then()
    // handlers permanently accumulated on the never-settling automatic promise.
    for (let i = 0; i < 5; i++) {
      const retry = stuck.setSpeaking(false).catch(error => error);
      await flush();
      for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
      await flush(); assert.equal((await retry).code, 'timeout');
    }
    const disposedWait = stuck.setSpeaking(true);
    await flush(); stuck.dispose(); await flush();
    assert.equal(await Promise.race([disposedWait, Promise.resolve('unsettled')]), false);
    blocker = null;
    const restoring = await mount('restoring', 'Neko'); await flush();
    for (const action of ['timeout', 'pause', 'end']) {
      const slot = `late-start-${action}`;
      const lateStart = await mount(slot, 'Neko'); await flush();
      let finishStart;
      manualBlocker = new Promise(resolve => { finishStart = resolve; });
      const starting = lateStart.setSpeaking(true).catch(error => error); await flush();
      for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
      await flush(); assert.equal((await starting).code, 'timeout');
      await assert.rejects(lateStart.setSpeaking(false), {code:'busy'});
      if (action === 'pause') { await lateStart.pause(); await flush(); }
      if (action === 'end') { await game.runtime.end(); await flush(); }
      manualBlocker = null; finishStart(); await flush();
      assert.equal(manual.get(slot), false, 'late activation survived its timeout');
      assert.deepEqual(manualCalls.filter(([name]) => name === slot).map(([,active]) => active), [true,false]);
      lateStart.dispose(); await flush();
      if (action === 'end') { game.runtime.reset(); await game.runtime.start(); await flush(); }
    }
    await restoring.setSpeaking(true);
    for (const action of ['restore', 'pause', 'end', 'dispose']) {
      const lateStop = await mount(`late-stop-${action}`, 'Neko'); await flush();
      await lateStop.setSpeaking(true);
      let finishStop;
      manualBlocker = new Promise(resolve => { finishStop = resolve; });
      const stopping = lateStop.setSpeaking(false).catch(error => error); await flush();
      for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
      await flush(); assert.equal((await stopping).code, 'timeout');
      await assert.rejects(lateStop.setSpeaking(false), {code:'busy'});
      if (action === 'end') { await game.runtime.end(); await flush(); }
      if (action === 'dispose') { lateStop.dispose(); await flush(); }
      if (action === 'pause') { await lateStop.pause(); await flush(); }
      manualBlocker = null; finishStop(); await flush();
      assert.equal(manual.get(`late-stop-${action}`), action === 'restore',
        `${action}: late stop did not reconcile current manual ownership`);
      if (action === 'pause') {
        await lateStop.resume(); await flush();
        assert.equal(manual.get(`late-stop-${action}`), true, 'paused manual intent did not resume');
      }
      assert.deepEqual(manualCalls.filter(([slot]) => slot === `late-stop-${action}`).map(([,active]) => active),
        action === 'restore' || action === 'pause' ? [true, false, true] : [true, false],
        `${action}: late stop restarted manual motion more than once`);
      lateStop.dispose(); await flush();
      if (action === 'end') { game.runtime.reset(); await game.runtime.start(); await flush(); }
    }
    await restoring.setSpeaking(true);
    const replacing = await mount('late-stop-replacing', 'Neko'); await flush();
    await replacing.setSpeaking(true);
    let finishOldStop, finishReplacement;
    manualBlocker = new Promise(resolve => { finishOldStop = resolve; });
    const oldStop = replacing.setSpeaking(false).catch(error => error); await flush();
    modelBlocker = new Promise(resolve => { finishReplacement = resolve; });
    const replacement = replacing.setModel({type:'vrm',path:'/replacement.vrm'}); await flush();
    for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
    await flush(); assert.equal((await oldStop).code, 'timeout');
    manualBlocker = null; finishOldStop(); await flush();
    assert.deepEqual(manualCalls.filter(([slot]) => slot === 'late-stop-replacing').map(([,active]) => active),
      [true, false], 'manual motion restored before model replacement completed');
    modelBlocker = null; finishReplacement(); await replacement; await flush();
    assert.equal(manual.get('late-stop-replacing'), true);
    assert.deepEqual(manualCalls.filter(([slot]) => slot === 'late-stop-replacing').map(([,active]) => active),
      [true, false, true], 'new model did not restore manual motion exactly once');
    replacing.dispose(); await flush();
    let releaseManual;
    manualBlocker = new Promise(resolve => { releaseManual = resolve; });
    const changed = restoring.setModel({ type: 'vrm', path: '/new.vrm' }); await flush();
    assert.equal(await Promise.race([changed, Promise.resolve('unsettled')]), 'model loaded');
    for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
    await flush();
    await assert.rejects(restoring.setSpeaking(false), { code: 'busy' });
    manualBlocker = null; releaseManual(); await flush();
    await restoring.setSpeaking(false);
    await restoring.setSpeaking(true);
    restoring.pause();
    manualBlocker = new Promise(resolve => { releaseManual = resolve; });
    const resumed = Promise.resolve(restoring.resume()).then(() => 'resumed');
    await flush();
    assert.equal(await Promise.race([resumed, Promise.resolve('unsettled')]), 'resumed',
      'optional manual speech delayed renderer resumption');
    await assert.rejects(restoring.setSpeaking(false), {code:'busy'});
    manualBlocker = null; releaseManual(); await flush();
    await restoring.setSpeaking(false);
    blocker = new Promise(() => {});
    emit({ active: false }); await flush();
    const endedWait = restoring.setSpeaking(true).catch(error => error); await flush();
    await game.runtime.end(); await flush();
    assert.equal((await Promise.race([endedWait, Promise.resolve('unsettled')])).code, 'cancelled',
      'route exit retained the manual waiter');
    restoring.dispose(); blocker = null;
  } finally {
    avatar.dispose(); other.dispose(); game.dispose();
    await flush();
  }
  assert.equal(timers.size, 0, 'disposal must release the mouth watchdog');
  console.log('mini-game automatic Avatar speech runtime test passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
