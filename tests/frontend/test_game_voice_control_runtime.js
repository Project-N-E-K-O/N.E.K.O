const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  const listeners = new Map();
  const intervals = new Map();
  const clearedIntervals = [];
  const posted = [];
  let nextIntervalId = 1;
  let channelClosed = false;
  let failLocalStorageWrites = false;
  const localValues = new Map();

  const addEventListener = (type, handler) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(handler);
  };
  const removeEventListener = (type, handler) => listeners.get(type)?.delete(handler);

  const appState = {
    gameRouteActive: true,
    gameRouteGameType: 'soccer',
    gameRouteSessionId: 'soccer-runtime',
    gameRouteInstanceId: 'route-instance-b',
    isRecording: false,
    voiceStartPending: false,
    isMicMuted: false,
    gameVoiceTranscriptionMode: 'unavailable',
    gameVoiceTranscriptionProvider: '',
    gameVoiceTranscriptionReady: false,
    gameVoiceTranscriptionReason: 'voice_inactive',
    voiceInputRouteBlocked: false,
    voiceSessionStartEpoch: 0,
  };
  let holdMicStart = false;
  // Host-side start attribution, modelled on app-buttons.js /
  // app-audio-capture.js: the handler takes the offered receipt synchronously,
  // accepts it with its epoch, and the capture records its own commit.
  let receiptSlot = null;
  let liveCaptureToken = 0;
  let nextCaptureToken = 0;
  let lastClickReceipt = null;
  let clickBehavior = null;
  function commitReceipt(receipt) {
    nextCaptureToken += 1;
    receipt.captureToken = nextCaptureToken;
    receipt.committed = true;
    liveCaptureToken = nextCaptureToken;
    appState.isRecording = true;
  }
  let socketOpen = true;
  const sentWsMessages = [];
  const stopMicCaptureCalls = [];
  const micButton = {
    disabled: false,
    classList: { contains: () => false },
    clickCount: 0,
    click() {
      this.clickCount += 1;
      if (clickBehavior === 'no-listener') return;
      const receipt = windowMock.takeVoiceStartReceipt();
      lastClickReceipt = receipt;
      if (clickBehavior) {
        clickBehavior(receipt);
        return;
      }
      appState.voiceSessionStartEpoch += 1;
      if (receipt) {
        receipt.accepted = true;
        receipt.voiceStartEpoch = appState.voiceSessionStartEpoch;
      }
      if (holdMicStart) {
        appState.voiceStartPending = true;
      } else if (receipt) {
        commitReceipt(receipt);
        receipt.outcome = 'started';
      } else {
        appState.isRecording = true;
      }
    },
  };
  const documentMock = {
    getElementById(id) { return id === 'micButton' ? micButton : null; },
  };

  class BroadcastChannelMock {
    constructor(name) {
      this.name = name;
      this.onmessage = null;
      global.__gameVoiceChannel = this;
    }
    postMessage(message) {
      if (this.failPosts) throw new Error('channel unavailable');
      posted.push(message);
    }
    close() { channelClosed = true; }
  }

  const windowMock = {
    appState,
    appWebSocket: {
      isOpen() { return socketOpen; },
      send(payload) { sentWsMessages.push(payload); },
    },
    document: documentMock,
    console: { log() {}, warn() {} },
    isMicStarting: false,
    offerVoiceStartReceipt(receipt) { receiptSlot = receipt || null; },
    takeVoiceStartReceipt() {
      const receipt = receiptSlot;
      receiptSlot = null;
      return receipt;
    },
    micStartTokenOwnsLiveCapture(token) {
      return token > 0 && token === liveCaptureToken && appState.isRecording === true;
    },
    stopMicCapture: async () => {
      stopMicCaptureCalls.push(Date.now());
      appState.isRecording = false;
    },
    addEventListener,
    removeEventListener,
    dispatchEvent(event) {
      for (const handler of Array.from(listeners.get(event.type) || [])) handler(event);
    },
    CustomEvent: class CustomEventMock {
      constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
    },
  };

  global.window = windowMock;
  global.document = documentMock;
  global.BroadcastChannel = BroadcastChannelMock;
  global.localStorage = {
    getItem(key) { return localValues.has(key) ? localValues.get(key) : null; },
    setItem(key, value) {
      if (failLocalStorageWrites) throw new Error('storage unavailable');
      localValues.set(key, String(value));
    },
    removeItem(key) { localValues.delete(key); },
  };
  global.setInterval = (callback, delay) => {
    const id = nextIntervalId++;
    intervals.set(id, { callback, delay });
    return id;
  };
  global.clearInterval = (id) => {
    intervals.delete(id);
    clearedIntervals.push(id);
  };

  const sourcePath = path.resolve(__dirname, '../../static/app/app-game-voice-control.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });
  const channel = global.__gameVoiceChannel;
  assert(channel?.name === 'neko_game_voice_control_channel', 'host channel was not created');
  assert(intervals.size === 1, 'bounded state synchronization timer was not installed');
  assert(listeners.get('storage')?.size === 1, 'localStorage fallback listener was not installed');
  assert(listeners.get('neko-game-voice-control-message')?.size === 1,
    'same-document fallback listener was not installed');
  assert(listeners.get('neko-game-window-state-change')?.size === 1, 'route reconciliation listener was not installed');
  assert(listeners.get('neko-game-voice-transcription-state-change')?.size === 1,
    'transcription state listener was not installed');
  assert(listeners.get('neko:user-voice-content-received')?.size === 1,
    'final transcript relay listener was not installed');

  const routeWindowListener = listeners.get('neko-game-window-state-change')?.values().next().value;
  appState.gameRouteActive = false;
  appState.gameRouteGameType = '';
  appState.gameRouteSessionId = '';
  appState.gameRouteInstanceId = '';
  routeWindowListener({ detail: { action: 'opened', sessionId: 'missing-type' } });
  assert(appState.gameRouteGameType === '',
    'host voice bridge invented a soccer identity for a route without game_type');
  routeWindowListener({ detail: { action: 'closed', sessionId: 'missing-type' } });
  appState.gameRouteActive = true;
  appState.gameRouteGameType = 'soccer';
  appState.gameRouteSessionId = 'soccer-runtime';
  appState.gameRouteInstanceId = 'route-instance-a';
  routeWindowListener({ detail: {
    action: 'closed',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-a',
  } });
  assert(posted.some((message) => message.reason === 'route_closed'
    && message.route_active === false
    && message.game_type === 'soccer'
    && message.session_id === 'soccer-runtime'
    && message.sdk_route_instance_id === 'route-instance-a'),
  'route close cleared the identity before publishing the final inactive state');
  routeWindowListener({ detail: {
    action: 'opened',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-b',
  } });

  // The route generation is REQUIRED, not merely compared when present: it is
  // what keeps a non-SDK route (soccer/badminton never mint one) out of voice
  // control, so a request that omits it must be refused even though its
  // game_type and session_id match the live route exactly.
  for (const missingGeneration of ['', undefined]) {
    channel.onmessage({ data: {
      type: 'game_voice_control_request',
      sender_id: 'ungranted-window',
      request_id: `no-generation-${String(missingGeneration)}`,
      action: 'start',
      game_type: 'soccer',
      session_id: 'soccer-runtime',
      ...(missingGeneration === undefined
        ? {}
        : { sdk_route_instance_id: missingGeneration }),
    } });
    await flush();
    assert(micButton.clickCount === 0 && posted.some((message) => (
      message.request_id === `no-generation-${String(missingGeneration)}`
      && message.reason === 'route_mismatch'
    )), 'a request without a route generation controlled the microphone');
  }

  channel.onmessage({ data: {
    type: 'game_voice_control_request',
    sender_id: 'stale-soccer-window',
    request_id: 'stale-start',
    action: 'start',
    game_type: 'soccer',
    session_id: 'soccer-runtime',
    sdk_route_instance_id: 'route-instance-a',
  } });
  await flush();
  assert(micButton.clickCount === 0 && posted.some((message) => message.request_id === 'stale-start'
    && message.reason === 'route_mismatch'
    && message.sdk_route_instance_id === 'route-instance-a'),
  'stale same-session voice client controlled the replacement route');

  const duplicatedStartRequest = {
    type: 'game_voice_control_request',
    sender_id: 'soccer-window',
    request_id: 'start-1',
    message_id: 'duplicate-start-message',
    action: 'start',
    game_type: 'soccer',
    session_id: 'soccer-runtime',
    sdk_route_instance_id: 'route-instance-b',
  };
  channel.onmessage({ data: duplicatedStartRequest });
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
    detail: duplicatedStartRequest,
  }));
  await flush();
  assert(appState.isRecording && micButton.clickCount === 1,
    'dual voice transports executed the same start command more than once');
  assert(posted.some((message) => message.request_id === 'start-1' && message.reason === 'started' && message.active),
    'start request did not acknowledge the confirmed active state');
  assert(posted.some((message) => message.request_id === 'start-1'
    && message.capture_owner === 'host'
    && message.transcription_mode === 'backend_pending'
    && message.ready === false),
  'start response did not expose the host-owned pending transcription contract');
  assert(posted.filter((message) => message.request_id === 'start-1'
    && message.reason === 'started').length === 1,
  'dual voice transports emitted duplicate command completion states');
  assert(posted.some((message) => message.request_id === 'start-1'
    && message.reason === 'started'
    && message.start_attribution === 'command'
    && message.start_outcome === 'started'
    && message.start_committed === true
    && message.start_capture_live === true
    && message.host_capture_active === true),
  'a normal start did not report its own committed capture');
  assert(receiptSlot === null, 'the bridge left an offered start receipt behind after clicking');

  appState.gameVoiceTranscriptionMode = 'native_core';
  appState.gameVoiceTranscriptionProvider = 'free';
  appState.gameVoiceTranscriptionReady = true;
  appState.gameVoiceTranscriptionReason = 'native_ready';
  const transcriptionListener = listeners.get('neko-game-voice-transcription-state-change')?.values().next().value;
  transcriptionListener({ detail: {} });
  assert(posted.some((message) => message.active
    && message.capture_owner === 'host'
    && message.transcription_mode === 'native_core'
    && message.provider === 'free'
    && message.ready === true),
  'resolved native Core transcription state was not forwarded to the game');

  const transcriptListener = listeners.get('neko:user-voice-content-received')?.values().next().value;
  transcriptListener({ detail: {
    requestId: 'voice-final-1',
    text: '  hello game  ',
    source: 'voice',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-b',
  } });
  assert(posted.some((message) => message.type === 'game_voice_transcript'
    && message.game_type === 'soccer'
    && message.session_id === 'soccer-runtime'
    && message.request_id === 'voice-final-1'
    && message.sdk_route_instance_id === 'route-instance-b'
    && message.text === 'hello game'),
  'final normalized transcript was not relayed to the active game route');
  const transcriptCount = posted.filter((message) => message.type === 'game_voice_transcript').length;
  transcriptListener({ detail: {
    requestId: 'stale-voice',
    text: 'old route text',
    source: 'voice',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-a',
  } });
  assert(posted.filter((message) => message.type === 'game_voice_transcript').length === transcriptCount,
    'a stale route transcript was relabeled as the active route');
  appState.gameRouteActive = false;
  transcriptListener({ detail: { requestId: 'inactive-voice', text: 'must not escape' } });
  assert(posted.filter((message) => message.type === 'game_voice_transcript').length === transcriptCount,
    'transcript was relayed without an active game route');
  appState.gameRouteActive = true;

  channel.onmessage({ data: {
    type: 'game_voice_control_request',
    sender_id: 'soccer-window',
    request_id: 'stop-1',
    action: 'stop',
    game_type: 'soccer',
    session_id: 'soccer-runtime',
    sdk_route_instance_id: 'route-instance-b',
  } });
  await flush();
  assert(!appState.isRecording, 'stop request did not use the official microphone teardown');
  // Exactly one teardown per stop command. The microphone is process-global, so
  // a second issue after the command has yielded would land on whatever owns it
  // by then -- a replacement route, or the ordinary chat capture the host
  // resumes on route exit.
  assert(stopMicCaptureCalls.length === 1,
    `a stop command issued ${stopMicCaptureCalls.length} microphone teardowns`);
  assert(posted.some((message) => message.request_id === 'stop-1' && message.reason === 'stopped' && !message.active),
    'stop request did not acknowledge the confirmed inactive state');

  holdMicStart = true;
  channel.onmessage({ data: {
    type: 'game_voice_control_request',
    sender_id: 'soccer-window',
    request_id: 'superseded-start',
    action: 'start',
    game_type: 'soccer',
    session_id: 'soccer-runtime',
    sdk_route_instance_id: 'route-instance-b',
  } });
  await flush();
  routeWindowListener({ detail: {
    action: 'opened',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-c',
  } });
  appState.voiceStartPending = false;
  appState.isRecording = true;
  await new Promise((resolve) => setTimeout(resolve, 180));
  holdMicStart = false;
  // Route C took over while B's start was settling, and the microphone is
  // process-global. B must not reach in and tear it down: C (or the user) may be
  // mid-utterance on that capture, and killing it loses what they were saying
  // with no transcript and nothing logged. B only releases a microphone it
  // opened itself AND only when no route remains -- neither holds here.
  assert(appState.isRecording === true,
    `a superseded route tore down the replacement route's live microphone: ${JSON.stringify(posted.slice(-4))}`);
  assert(posted.some((message) => message.request_id === 'superseded-start'
    && message.reason === 'route_superseded'
    && message.sdk_route_instance_id === 'route-instance-b'),
  'a voice command crossing route generations did not return the frozen route identity');
  // The capture that is live belongs to route C, not to this command: the
  // report must not claim it.
  assert(posted.some((message) => message.request_id === 'superseded-start'
    && message.reason === 'route_superseded'
    && message.active === false
    && message.start_attribution === 'command'
    && message.start_committed === false
    && message.start_capture_live === false
    && message.host_capture_active === true),
  'a superseded command claimed a capture that something else committed');

  // Same rule when the route does not hand over but simply ENDS. Route exit
  // resumes ordinary chat capture (startMicCapture in app-websocket.js), so
  // "no route remains" does not mean "no owner" -- tearing the microphone down
  // here would kill the chat capture the host just resumed, losing whatever the
  // user says next with no transcript.
  // Let the previous command fully settle first, or this one is answered
  // `busy` and never reaches the code under test.
  await new Promise((resolve) => setTimeout(resolve, 120));
  await flush();
  routeWindowListener({ detail: {
    action: 'opened',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-d',
  } });
  appState.isRecording = false;
  appState.voiceStartPending = false;
  holdMicStart = true;
  channel.onmessage({ data: {
    type: 'game_voice_control_request',
    sender_id: 'soccer-window',
    request_id: 'ended-start',
    action: 'start',
    game_type: 'soccer',
    session_id: 'soccer-runtime',
    sdk_route_instance_id: 'route-instance-d',
  } });
  await flush();
  routeWindowListener({ detail: {
    action: 'closed',
    gameType: 'soccer',
    sessionId: 'soccer-runtime',
    routeInstanceId: 'route-instance-d',
  } });
  // The host resumes ordinary capture on route exit.
  appState.voiceStartPending = false;
  appState.isRecording = true;
  await new Promise((resolve) => setTimeout(resolve, 180));
  holdMicStart = false;
  // Let the held command actually finish its route check and cleanup before
  // asserting; otherwise this passes without ever reaching the code under test.
  await new Promise((resolve) => setTimeout(resolve, 120));
  await flush();
  assert(posted.some((message) => message.request_id === 'ended-start'
    && message.reason === 'route_superseded'),
  'the ended-route command never reached its post-await route check');
  assert(appState.isRecording === true,
    'a command whose route ended tore down the resumed ordinary chat microphone');
  assert(posted.some((message) => message.request_id === 'ended-start'
    && message.reason === 'route_superseded'
    && message.start_committed === false
    && message.start_capture_live === false
    && message.host_capture_active === true),
  'the resumed ordinary chat capture was reported as the ended command\'s own');

  // Wait until a command has produced its final (non-working) reply and its
  // cleanup broadcast, so the next command is not answered `busy`.
  async function finalReply(requestId) {
    for (let i = 0; i < 200; i += 1) {
      const reply = posted.find((message) => message.request_id === requestId
        && message.reason !== 'working');
      if (reply) {
        await flush();
        return reply;
      }
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    throw new Error(`no final reply for ${requestId}`);
  }
  function sendStart(requestId, routeInstanceId) {
    channel.onmessage({ data: {
      type: 'game_voice_control_request',
      sender_id: 'soccer-window',
      request_id: requestId,
      action: 'start',
      game_type: 'soccer',
      session_id: 'soccer-runtime',
      sdk_route_instance_id: routeInstanceId,
    } });
  }
  function openRoute(routeInstanceId) {
    routeWindowListener({ detail: {
      action: 'opened', gameType: 'soccer', sessionId: 'soccer-runtime', routeInstanceId,
    } });
  }
  function closeRoute(routeInstanceId) {
    routeWindowListener({ detail: {
      action: 'closed', gameType: 'soccer', sessionId: 'soccer-runtime', routeInstanceId,
    } });
  }
  function resetMicrophone() {
    appState.isRecording = false;
    appState.voiceStartPending = false;
    windowMock.isMicStarting = false;
    liveCaptureToken = 0;
    holdMicStart = false;
    clickBehavior = null;
  }

  // #3003 itself. The command's own start is parked (inside getUserMedia)
  // when its route ends; nothing on the exit path cancels it, so it commits
  // afterwards. The old route stays unavailable, but the reply must say that
  // THIS command's capture committed and is live -- not that it failed.
  resetMicrophone();
  openRoute('route-instance-e');
  holdMicStart = true;
  const clicksBeforeOwnCommit = micButton.clickCount;
  sendStart('own-commit-after-exit', 'route-instance-e');
  await flush();
  assert(micButton.clickCount === clicksBeforeOwnCommit + 1 && lastClickReceipt?.accepted === true,
    'the game-issued start did not hand its receipt to the host start');
  const ownReceipt = lastClickReceipt;
  closeRoute('route-instance-e');
  commitReceipt(ownReceipt);
  ownReceipt.outcome = 'started';
  appState.voiceStartPending = false;
  const ownCommitReply = await finalReply('own-commit-after-exit');
  assert(ownCommitReply.reason === 'route_superseded'
    && ownCommitReply.ok === false
    && ownCommitReply.available === false
    && ownCommitReply.active === false
    && ownCommitReply.starting === false,
  `the ended route was reported as usable: ${JSON.stringify(ownCommitReply)}`);
  assert(ownCommitReply.start_attribution === 'command'
    && ownCommitReply.start_outcome === 'started'
    && ownCommitReply.start_committed === true
    && ownCommitReply.start_capture_live === true
    && ownCommitReply.host_capture_active === true,
  `a command whose own capture committed after its route ended was reported as not started: ${JSON.stringify(ownCommitReply)}`);
  assert(appState.isRecording === true, 'the bridge tore down the capture it reports on');

  // Same window, but the route has not just ended -- the host's wait runs
  // out while the start is still in flight. That is unknown, not failed: the
  // host may still commit after the reply.
  resetMicrophone();
  openRoute('route-instance-f');
  holdMicStart = true;
  const realDateNow = Date.now;
  let skewMs = 0;
  Date.now = () => realDateNow() + skewMs;
  try {
    sendStart('deadline-start', 'route-instance-f');
    await flush();
    skewMs = 13000;
    const deadlineReply = await finalReply('deadline-start');
    assert(deadlineReply.reason === 'start_pending'
      && deadlineReply.ok === false
      && deadlineReply.start_outcome === 'pending'
      && deadlineReply.start_committed === false
      && deadlineReply.host_capture_starting === true
      && deadlineReply.starting === true,
    `a start still in flight at the deadline was reported as final: ${JSON.stringify(deadlineReply)}`);
  } finally {
    Date.now = realDateNow;
  }
  resetMicrophone();

  // Observed, not issued: the user's own press is already in flight. The
  // command must not click, and must not claim that start.
  openRoute('route-instance-g');
  appState.voiceStartPending = true;
  const clicksBeforeObserved = micButton.clickCount;
  sendStart('observed-start', 'route-instance-g');
  await flush();
  closeRoute('route-instance-g');
  appState.voiceStartPending = false;
  appState.isRecording = true;
  const observedReply = await finalReply('observed-start');
  assert(micButton.clickCount === clicksBeforeObserved, 'the bridge clicked over a start already in flight');
  assert(observedReply.reason === 'route_superseded'
    && observedReply.start_attribution === 'observed'
    && observedReply.start_outcome === 'not_applicable'
    && observedReply.start_committed === false
    && observedReply.start_capture_live === false
    && observedReply.host_capture_active === true,
  `an observed start was attributed to the command: ${JSON.stringify(observedReply)}`);

  // Real failures keep reporting as failures on a live route.
  resetMicrophone();
  openRoute('route-instance-h');
  clickBehavior = () => {};  // the handler took the receipt and refused the press
  sendStart('refused-start', 'route-instance-h');
  const refusedReply = await finalReply('refused-start');
  assert(refusedReply.reason === 'start_failed'
    && refusedReply.ok === false
    && refusedReply.start_attribution === 'none'
    && refusedReply.start_outcome === 'not_started'
    && refusedReply.start_committed === false,
  `a press the host refused was not reported as a failed start: ${JSON.stringify(refusedReply)}`);
  clickBehavior = (receipt) => {
    appState.voiceSessionStartEpoch += 1;
    receipt.accepted = true;
    receipt.voiceStartEpoch = appState.voiceSessionStartEpoch;
    receipt.outcome = 'failed';  // e.g. getUserMedia denied
  };
  sendStart('failed-start', 'route-instance-h');
  const failedReply = await finalReply('failed-start');
  assert(failedReply.reason === 'start_failed'
    && failedReply.ok === false
    && failedReply.active === false
    && failedReply.start_attribution === 'command'
    && failedReply.start_outcome === 'failed'
    && failedReply.start_committed === false
    && failedReply.host_capture_active === false,
  `a failed start was not reported as failed: ${JSON.stringify(failedReply)}`);
  // A start the handler accepted but then stood down from (text takeover,
  // blocked route, goodbye) settles nothing itself; once its lifecycle has
  // closed the bridge reports it as cancelled, not pending.
  clickBehavior = (receipt) => {
    appState.voiceSessionStartEpoch += 1;
    receipt.accepted = true;
    receipt.voiceStartEpoch = appState.voiceSessionStartEpoch;
  };
  sendStart('stood-down-start', 'route-instance-h');
  const stoodDownReply = await finalReply('stood-down-start');
  assert(stoodDownReply.reason === 'start_failed'
    && stoodDownReply.start_attribution === 'command'
    && stoodDownReply.start_outcome === 'cancelled',
  `a start that stood down was not reported as cancelled: ${JSON.stringify(stoodDownReply)}`);
  // A click nothing handles leaves the receipt untaken; it must not stay
  // offered for the next, unrelated press.
  clickBehavior = 'no-listener';
  sendStart('unhandled-click', 'route-instance-h');
  const unhandledReply = await finalReply('unhandled-click');
  assert(receiptSlot === null, 'an untaken start receipt stayed offered after the click');
  assert(unhandledReply.reason === 'start_failed' && unhandledReply.start_outcome === 'not_started',
    `an unhandled click was not reported as not started: ${JSON.stringify(unhandledReply)}`);
  resetMicrophone();
  openRoute('route-instance-c');

  channel.onmessage({ data: {
    type: 'game_voice_control_request',
    sender_id: 'other-game',
    request_id: 'wrong-route',
    action: 'start',
    game_type: 'badminton',
    session_id: 'badminton-runtime',
    sdk_route_instance_id: 'badminton-route',
  } });
  await flush();
  assert(posted.some((message) => message.request_id === 'wrong-route'
    && message.reason === 'route_mismatch'
    && message.game_type === 'badminton'
    && message.session_id === 'badminton-runtime'
    && message.sdk_route_instance_id === 'badminton-route'
    && message.route_active === false),
    'route mismatch was not rejected');

  const sameDocumentResponses = [];
  const sameDocumentObserver = (event) => {
    if (event?.detail?.type === 'game_voice_control_state') sameDocumentResponses.push(event.detail);
  };
  addEventListener('neko-game-voice-control-message', sameDocumentObserver);
  channel.failPosts = true;
  failLocalStorageWrites = true;
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
    detail: {
      type: 'game_voice_control_request',
      sender_id: 'soccer-window',
      request_id: 'same-document-query',
      action: 'query',
      game_type: 'soccer',
      session_id: 'soccer-runtime',
      sdk_route_instance_id: 'route-instance-c',
    },
  }));
  await flush();
  assert(sameDocumentResponses.some((message) => message.request_id === 'same-document-query'),
    'same-document fallback request was not answered after channel and storage failure');
  failLocalStorageWrites = false;
  removeEventListener('neko-game-voice-control-message', sameDocumentObserver);

  const dispose = listeners.get('pagehide')?.values().next().value;
  assert(typeof dispose === 'function', 'pagehide cleanup was not installed');
  dispose();
  assert(channelClosed, 'host voice control channel was not closed');
  assert(clearedIntervals.length === 1 && intervals.size === 0, 'state synchronization timer was not released');
  assert(!listeners.get('pagehide')?.size, 'pagehide cleanup listener was not released');
  assert(!listeners.get('beforeunload')?.size, 'beforeunload cleanup listener was not released');
  assert(!listeners.get('storage')?.size, 'localStorage fallback listener was not released');
  assert(!listeners.get('neko-game-voice-control-message')?.size,
    'same-document fallback listener was not released');
  assert(!listeners.get('neko-game-window-state-change')?.size, 'route reconciliation listener was not released');
  assert(!listeners.get('neko-game-voice-transcription-state-change')?.size,
    'transcription state listener was not released');
  assert(!listeners.get('neko:user-voice-content-received')?.size,
    'final transcript listener was not released');

  process.stdout.write('game voice control runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
