// Propagate the entry module's cache-busting query to every dependency.
// Without this, browsers can combine a fresh game.js with stale child modules.
const assetVersion = new URL(import.meta.url).search;
const [i18nModule, physicsModule, avatarModule, sdkModule, sharedCourtModule, timersModule] = await Promise.all([
  import(`./i18n.js${assetVersion}`),
  import(`./physics.js${assetVersion}`),
  import(`./avatar.js${assetVersion}`),
  import(`./sdk-bootstrap.js${assetVersion}`),
  import(`./shared-court.js${assetVersion}`),
  import(`./match-timers.js${assetVersion}`)
]);
const { applyTranslations, t, voiceLine, voiceLines } = i18nModule;
const { ShotLane, clamp } = physicsModule;
const { SharedCourt } = sharedCourtModule;
const { MatchTimers } = timersModule;
const matchTimers = new MatchTimers();
const { initNekoAvatar, reactNeko, setAvatarUnavailableLabel } = avatarModule;
const {
  airBasketballSdkReady,
  configureGameRuntime,
  disposeGameSdk,
  endGameRuntime,
  playGameTone,
  preloadNekoSpeech,
  speakNekoSpeech,
  startGameRuntime,
  unlockGameAudio,
  waitForNekoSpeechPlayback
} = sdkModule;
// A failed bootstrap (missing character, host or capability) must not stop the
// rest of this module from wiring the page; the start card reports it instead.
// A bootstrap step that never settles (host, handshake, character lookup, audio)
// is treated the same way, so the page never sits behind a dead start button.
const SDK_BOOTSTRAP_TIMEOUT_MS = 20000;
let sdkContext = null;
let sdkBootstrapTimer = 0;
try {
  sdkContext = await Promise.race([
    airBasketballSdkReady,
    new Promise((_, reject) => {
      sdkBootstrapTimer = setTimeout(
        () => reject(new Error(`SDK bootstrap timed out after ${SDK_BOOTSTRAP_TIMEOUT_MS} ms`)),
        SDK_BOOTSTRAP_TIMEOUT_MS
      );
    })
  ]);
} catch (error) {
  console.error('[air_basketball] SDK bootstrap failed', error);
  // A late connection would hold a runtime this page already gave up on.
  void airBasketballSdkReady.then(() => disposeGameSdk(), () => undefined);
} finally {
  clearTimeout(sdkBootstrapTimer);
}
const sdkGame = sdkContext?.game || null;
const sdkIdentity = sdkContext?.identity || null;

applyTranslations();

const ROUND_SECONDS = 60;
const RESULT_SPEECH_END_MAX_WAIT_MS = 12000;
const STAGE_THRESHOLDS = [0, 12, 30, 54];
const FEVER_HITS = 5;
const FEVER_SECONDS = 7;
const COURT_GRAVITY = 720;
const MAX_PHYSICS_STEP_SECONDS = .033;
const MAX_PHYSICS_STEPS_PER_FRAME = 8;
const MAX_PHYSICS_FRAME_DELTA_SECONDS = .25;
const NEKO_ACTION = Object.freeze({ IDLE:'idle', HOOP:'hoop', PLAYER:'player', MOUSE:'mouse' });
const ACTION_BALANCE = Object.freeze({
  MAX_FOCUS:100,
  FOCUS_REGEN:14,
  FOCUS_REGEN_DELAY:.6,
  SHOT_COST:18,
  JAM_DRAG_SCALE:.38,
  AVATAR_HIT_COST:24,
  PRANK_COST:36,
  MOUSE_COST:60,
  PLAYER_HIT_COOLDOWN:1.5,
  STAGGER:.45,
  HIT_GRACE:1.5,
  DISRUPTION_GRACE:3,
  MOUSE_MAX_PER_ROUND:2
});
// Eight shared balls start split between the collectors. The AI's own active
// shot budget is independent of the number that roll into its collector.
const INITIAL_BALLS_PER_SIDE = 4;
const NEKO_BALL_POOL = Object.freeze({ TOTAL:INITIAL_BALLS_PER_SIDE, READY:INITIAL_BALLS_PER_SIDE,
  MAX_ACTIVE:INITIAL_BALLS_PER_SIDE, MAX_AIRBORNE:INITIAL_BALLS_PER_SIDE });
const NEKO_SHOT_DELAY = Object.freeze({ MIN:1.9, MAX:2.5, BUSY_BONUS:.45, PRANK_RECOVERY:1.4, RETURN_RETRY:.32 });
const NEKO_STRENGTH = Object.freeze({
  BASE_ACCURACY:.76,
  MISS_RECOVERY:.04,
  MAX_ACCURACY:.88,
  OFFENSE_DROUGHT:3.2,
  SHOT_RESERVE:ACTION_BALANCE.SHOT_COST
});
const MOUSE_STEAL_DURATION = Object.freeze({ MIN:1800, MAX:3200 });
const MOUSE_STEAL_COOLDOWN = Object.freeze({ MIN:15, MAX:23 });
const NEKO_ATTENTION = Object.freeze({ AIM_SECONDS:.9, CROSS_WINDOW:3.2, REVENGE_WINDOW:3.5, COMBO_THREAT:3 });
const randomBetween = (min, max) => min + Math.random() * (max - min);
const makeActor = (time = ROUND_SECONDS) => ({
  score:0, combo:0, time, stage:1, fever:0, feverCharge:0,
  focus:ACTION_BALANCE.MAX_FOCUS, focusRecoveryDelay:0,
  stagger:0, hitGrace:0, action:NEKO_ACTION.IDLE,
  attempts:0, makes:0, shotMissStreak:0
});
const makeNekoAttention = () => ({
  aimSeconds:0, crossThreat:0, revenge:0,
  pendingMouse:false, pendingPrank:false
});
const state = {
  running:false,
  paused:false,
  mode:'timed',
  remaining:ROUND_SECONDS,
  elapsed:0,
  player:makeActor(),
  neko:makeActor(),
  nextNekoDecision:1.1,
  nextNekoInterference:4.2,
  nextMouseSteal:9.5,
  disruptionGrace:0,
  interferenceCount:0,
  nekoHitCount:0,
  nekoBallHitCount:0,
  crossCount:0,
  ballClashCount:0,
  mouseStealCount:0,
  mouseStealEscapes:0,
  mouseStealActive:false,
  mouseStealDurationMs:0,
  nekoOffenseIdle:0,
  nekoCounterPending:false,
  nekoAction:NEKO_ACTION.IDLE,
  nekoAttention:makeNekoAttention(),
  sound:true
};

const byId = id => document.getElementById(id);
const overlay = byId('start-overlay');
const startButton = byId('start-button');
const startCopy = byId('start-copy');
const resultBoard = byId('result-board');
const matchClock = byId('match-clock');
const clockLabel = byId('clock-label');
const clockUnit = byId('clock-unit');
const guide = byId('player-guide');
const shotHint = byId('shot-hint');
const soundToggle = byId('sound-toggle');
const stopMatchButton = byId('stop-match');
const pauseMatchButton = byId('pause-match');
const pauseOverlay = byId('pause-overlay');
const focusFills = {
  player:byId('player-focus-fill'),
  neko:byId('neko-focus-fill')
};
const nekoAvatar = byId('air-neko-avatar');
const nekoSpeech = byId('neko-speech');
const crossBall = byId('cross-ball');
const mouseStealLayer = byId('mouse-steal-layer');
const mouseStealTitle = byId('mouse-steal-title');
const pageParams = new URLSearchParams(window.location.search);
let opponentName = String(sdkIdentity?.name || pageParams.get('lanlan_name') || window.lanlan_config?.lanlan_name || 'N.E.K.O').trim() || 'N.E.K.O';
let lastFrame = 0;
let timerAccumulator = 0;
let counterTimer = 0;
let speechTimer = 0;
let voiceSequence = 0;
let voiceGuardUntil = 0;
let latestSpeechPromise = Promise.resolve();
let skipResultSpeechWait = null;
let prewarmedVoiceName = '';
let mouseStealTimer = 0;
let mouseStealVisualTimer = 0;
let crossAnimationFrame = 0;
let crossHideFrame = 0;
let trackedGuestBall = null;
let trackedGuestLane = null;
let trackedGuestSuspended = false;
let trackedCrossTransit = null;
let nekoAiFrozen = false;
let matchSequence = 0;
const auxiliaryTransitBalls = new Set();
const outsideBallOverlays = new Map();
const pointerMemory = { x:window.innerWidth * .5, y:window.innerHeight * .55, seen:false };
const mouseSteal = { active:false, x:0, y:0, targetX:0, targetY:0, lastX:null, lastY:null, struggle:0, startedAt:0, duration:0, escapeThreshold:500 };
const comboTimers = { player:0, neko:0 };

function beginNekoAction(action) {
  if (state.paused || state.nekoAction !== NEKO_ACTION.IDLE) return false;
  state.nekoAction = action;
  state.neko.action = action;
  return true;
}

function endNekoAction(action) {
  if (state.nekoAction !== action) return false;
  state.nekoAction = NEKO_ACTION.IDLE;
  state.neko.action = NEKO_ACTION.IDLE;
  return true;
}

function beginPlayerAction(action) {
  if (state.paused || state.player.action !== NEKO_ACTION.IDLE || state.player.stagger > 0) return false;
  state.player.action = action;
  return true;
}

function endPlayerAction(action) {
  if (state.player.action !== action) return false;
  state.player.action = NEKO_ACTION.IDLE;
  return true;
}

function syncFocus(side) {
  const actor = state[side];
  const fill = focusFills[side];
  if (!fill) return;
  fill.style.width = `${actor.focus}%`;
  fill.closest('.interference-meter')?.classList.toggle('is-low', actor.focus < ACTION_BALANCE.SHOT_COST);
}

function hasFocus(side, cost) {
  return state[side].focus + .001 >= cost;
}

function spendFocus(side, cost) {
  if (!hasFocus(side, cost)) return false;
  state[side].focus = clamp(state[side].focus - cost, 0, ACTION_BALANCE.MAX_FOCUS);
  state[side].focusRecoveryDelay = ACTION_BALANCE.FOCUS_REGEN_DELAY;
  syncFocus(side);
  return true;
}

function refundFocus(side, cost) {
  state[side].focus = clamp(state[side].focus + cost, 0, ACTION_BALANCE.MAX_FOCUS);
  syncFocus(side);
}

function recoverFocus(side, dt) {
  const actor = state[side];
  actor.focusRecoveryDelay = Math.max(0, actor.focusRecoveryDelay - dt);
  actor.stagger = Math.max(0, actor.stagger - dt);
  actor.hitGrace = Math.max(0, actor.hitGrace - dt);
  if (actor.focusRecoveryDelay > 0) return;
  actor.focus = clamp(actor.focus + ACTION_BALANCE.FOCUS_REGEN * dt, 0, ACTION_BALANCE.MAX_FOCUS);
  frameSync.focus.add(side);
}

// Physics steps only change numbers; the HUD and focus bars they touch are
// written once per frame, after the steps.
const frameSync = { hud:new Set(), focus:new Set() };
function flushFrameSync() {
  frameSync.hud.forEach(side => syncHud(side));
  frameSync.focus.forEach(side => syncFocus(side));
  frameSync.hud.clear();
  frameSync.focus.clear();
}

// The travelling ball must sit above both cabinets instead of inheriting the
// centre divider's stacking context. It remains below the mouse-steal layer.
document.body.appendChild(crossBall);

function sound(frequency, duration=.08, type='sine') {
  if (!state.sound) return;
  void playGameTone(frequency, duration, type).catch(error => {
    console.warn('[air_basketball] SDK sound unavailable', error);
  });
}

function applyOpponentName(name) {
  const clean = String(name || '').trim();
  if (!clean) return;
  opponentName = clean;
  ['opponent-name','start-opponent-name'].forEach(id => {
    const node = byId(id);
    if (node) node.textContent = clean;
  });
  const loading = byId('neko-avatar-loading');
  if (loading && !loading.classList.contains('is-hidden')) loading.textContent = clean;
  if (!state.nekoCounterPending) setNekoStatus(state.neko.fever > 0 ? 'opponentFever' : 'opponentReady');
}

function showNekoSpeech(line) {
  if (!nekoSpeech || !line) return;
  clearTimeout(speechTimer);
  nekoSpeech.textContent = line;
  nekoSpeech.classList.remove('is-visible');
  void nekoSpeech.offsetWidth;
  nekoSpeech.classList.add('is-visible');
  speechTimer = setTimeout(() => nekoSpeech.classList.remove('is-visible'), 2600);
}

function prewarmNekoVoice(name = opponentName) {
  const lanlanName = String(name || '').trim();
  if (!lanlanName || lanlanName === 'N.E.K.O' || lanlanName === prewarmedVoiceName) return;
  prewarmedVoiceName = lanlanName;
  const keys = ['voiceOpening', 'voiceScore', 'voiceMouseSteal', 'voiceHit', 'voiceChaos', 'voiceWin', 'voiceLose'];
  const lines = keys.flatMap(key => voiceLines(key, { name:lanlanName }));
  void preloadNekoSpeech(lines).then(result => {
    if (!result?.ok) {
      prewarmedVoiceName = '';
      console.warn('[air_basketball] SDK speech preload unavailable', result?.status);
      return;
    }
    console.debug('[air_basketball] SDK speech preload completed', { lineCount:lines.length });
  }).catch(error => {
    prewarmedVoiceName = '';
    console.warn('[air_basketball] SDK speech preload failed', error);
  });
}

function speakNeko(lineKey, { kind='game-event', interrupt=false } = {}) {
  const line = voiceLine(lineKey, { name:opponentName });
  if (!line) return false;
  showNekoSpeech(line);
  if (!state.sound) return false;
  const lanlanName = String(opponentName || '').trim();
  if (!lanlanName || lanlanName === 'N.E.K.O') return false;
  const now = Date.now();
  // An interrupting line (result, cursor steal, hit) replaces the current one.
  if (!interrupt && now < voiceGuardUntil) return false;
  const holdMs = clamp(1200 + line.length * 85, 1500, 4200);
  voiceGuardUntil = now + holdMs;
  const requestId = `air-basketball-${++voiceSequence}-${now}`;
  latestSpeechPromise = speakNekoSpeech({
    text:line,
    requestId,
    source:'game-event',
    eventKey:`${kind}:${voiceSequence}`,
    interruptExisting:interrupt,
    reuseSynthesizedAudio:true,
    mirrorText:false,
    emitTurnEnd:false,
    language:document.documentElement.lang || navigator.language,
    event:{ kind, mode:state.mode, score:{ player:state.player.score, neko:state.neko.score } }
  }).then(result => {
    if (!result?.ok) {
      voiceGuardUntil = 0;
      console.warn('[air_basketball] SDK speech was not delivered', result?.status);
    } else {
      console.debug('[air_basketball] SDK speech completed', result.data?.speech_id || requestId);
    }
  }).catch(error => {
    voiceGuardUntil = 0;
    console.warn('[air_basketball] SDK speech unavailable', error);
  });
  return requestId;
}

function formatElapsed(seconds) {
  const whole = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(whole / 60)).padStart(2,'0')}:${String(whole % 60).padStart(2,'0')}`;
}

function selectedMode() {
  return document.querySelector('input[name="match-mode"]:checked')?.value === 'endless' ? 'endless' : 'timed';
}

function opponentStatus(kind) {
  return t(kind, { name:opponentName });
}

// These elements are rewritten at runtime, so they carry no data-i18n (a
// language switch would reset them to their static text). Remember what each
// one shows and re-render it here instead.
let nekoStatusKey = 'opponentReady';
let runtimeStartFailed = false;
function setNekoStatus(key) {
  nekoStatusKey = key;
  byId('neko-status-text').textContent = opponentStatus(key);
}

function renderClockLabels(mode) {
  clockLabel.textContent = t(mode === 'timed' ? 'timeLeft' : 'elapsed');
  clockUnit.textContent = mode === 'timed' ? t('seconds') : '';
}

function renderLocalizedState() {
  setNekoStatus(nekoStatusKey);
  renderClockLabels(state.mode);
  startButton.textContent = t(resultBoard.hidden ? 'start' : 'again');
  pauseMatchButton.textContent = t(state.paused ? 'resume' : 'pause');
  renderStartCopy();
}

function renderStartCopy() {
  if (!sdkContext) startCopy.textContent = t('sdkUnavailable');
  else if (runtimeStartFailed) startCopy.textContent = t('runtimeStartFailed');
  else startCopy.textContent = selectedMode() === 'endless' ? t('endlessIntro') : t('intro');
}

function callout(side, text) {
  const node = byId(`${side}-callout`);
  node.textContent = text;
  node.classList.remove('show');
  void node.offsetWidth;
  node.classList.add('show');
}

function showCombo(side, combo, fever = false) {
  const node = byId(`${side}-combo-burst`);
  const screen = node.closest('.machine-screen');
  clearTimeout(comboTimers[side]);
  node.querySelector('strong').textContent = combo;
  node.classList.remove('is-hit','is-fever');
  void node.offsetWidth;
  node.classList.add('is-visible','is-hit');
  node.classList.toggle('is-fever', fever);
  screen.classList.remove('combo-impact');
  void screen.offsetWidth;
  screen.classList.add('combo-impact');
  comboTimers[side] = setTimeout(() => {
    node.classList.remove('is-visible','is-hit','is-fever');
    screen.classList.remove('combo-impact');
  }, 1050);
}

function clearCombo(side) {
  clearTimeout(comboTimers[side]);
  byId(`${side}-combo-burst`).classList.remove('is-visible','is-hit','is-fever');
}

function stageForScore(score) {
  let stage = 1;
  STAGE_THRESHOLDS.forEach((threshold, index) => { if (score >= threshold) stage = index + 1; });
  return stage;
}

function nextStageTarget(stage) {
  return STAGE_THRESHOLDS[stage] ?? 'MAX';
}

function syncHud(side) {
  const actor = state[side];
  byId(`${side}-score`).textContent = actor.score;
  byId(`${side}-combo`).textContent = actor.combo;
  byId(`${side}-time`).textContent = actor.time;
  byId(`${side}-stage`).textContent = actor.stage;
  byId(`${side}-target`).textContent = nextStageTarget(actor.stage);
  const feverPercent = actor.fever > 0 ? actor.fever / FEVER_SECONDS * 100 : actor.feverCharge / FEVER_HITS * 100;
  byId(`${side}-fever-fill`).style.width = `${clamp(feverPercent, 0, 100)}%`;
  byId(`${side}-fever-fill`).closest('.fever-meter').classList.toggle('is-active', actor.fever > 0);
}

const playerLane = new ShotLane({ canvas:byId('player-court'), side:'player', onScore:(data, ball) => nativeScored('player', data, ball), onMiss:data => nativeMissed('player', data) });
const nekoLane = new ShotLane({ canvas:byId('neko-court'), side:'neko', onScore:data => nativeScored('neko', data), onMiss:data => nativeMissed('neko', data) });
const sharedCourt = new SharedCourt(playerLane, nekoLane);
sharedCourt.onBoardImpact = () => sound(122, .07, 'triangle');
playerLane.enableBallPool(INITIAL_BALLS_PER_SIDE);
nekoLane.enableBallPool(INITIAL_BALLS_PER_SIDE);
sharedCourt.onExit = (ball, lane) => {
  state.crossCount += 1;
  state.nekoAttention.crossThreat = NEKO_ATTENTION.CROSS_WINDOW;
};
const laneFor = side => side === 'player' ? playerLane : nekoLane;

function clearTrackedGuestBall() {
  if (trackedGuestBall) trackedGuestBall.pageOverlay = false;
  trackedCrossTransit = null;
  trackedGuestBall = null;
  trackedGuestLane = null;
  trackedGuestSuspended = false;
  crossBall.classList.remove('is-crossing','is-interactive');
}

function trackedGuestIsInteractive() {
  return !trackedGuestSuspended
    && Boolean(trackedGuestBall?.flying)
    && !trackedGuestBall?.expired
    && !trackedGuestBall?.inTransit;
}

function syncTrackedGuestInteraction() {
  crossBall.classList.toggle('is-interactive', trackedGuestIsInteractive());
}

function trackGuestBall(ball, lane) {
  if (trackedGuestBall && trackedGuestBall !== ball) trackedGuestBall.pageOverlay = false;
  trackedGuestBall = ball;
  trackedGuestLane = lane;
  trackedGuestSuspended = false;
  ball.pageOverlay = true;
  crossBall.classList.add('is-crossing');
  syncTrackedGuestInteraction();
  return true;
}

// Runs every physics step, so it must not read layout. Returns whether a ball
// is left for the page overlay to follow.
function settleTrackedGuestBall() {
  const ball = trackedGuestBall;
  const lane = trackedGuestLane;
  syncTrackedGuestInteraction();
  if (trackedGuestSuspended) return false;
  // Native shots replace `playerLane.ball` when they score, miss or the court
  // resizes. Rebind immediately instead of leaving the page overlay attached
  // to the discarded object, which looked like the ball had disappeared.
  if (lane === playerLane && ball !== playerLane.ball) {
    trackGuestBall(playerLane.ball, playerLane);
    return settleTrackedGuestBall();
  }
  if (ball?.expired) {
    if (lane === nekoLane && ball.owner === 'player' && !ball.scored) missed('player');
    clearTrackedGuestBall();
    if (state.running) {
      playerLane.resetBall();
      trackGuestBall(playerLane.ball, playerLane);
    }
    return false;
  }
  if (!ball || !lane) {
    clearTrackedGuestBall();
    return false;
  }
  return true;
}

// Once per frame (and on resize): move the page overlay onto the tracked ball.
function syncTrackedGuestBall() {
  if (!settleTrackedGuestBall()) return;
  if (trackedGuestLane.sharedCourt) {
    const { left, right } = sharedCourt.bounds(trackedGuestLane);
    const ball = trackedGuestBall;
    const outside = ball.x - ball.r < left || ball.x + ball.r > right;
    // Keep a resting/replaced ball in the actual court renderer. A separate
    // fixed-position sprite is needed only when it travels outside the scene.
    ball.pageOverlay = outside;
    if (!outside) {
      crossBall.classList.remove('is-crossing');
      return;
    }
  }
  positionPageBall(crossBall, trackedGuestBall, trackedGuestLane);
}

function positionPageBall(visual, ball, lane) {
  const rect = lane.canvas.getBoundingClientRect();
  const scaleX = rect.width / lane.width;
  const scaleY = rect.height / lane.height;
  const spriteSize = lane.ballSpriteSize(ball.r) * scaleX;
  const size = lane.sharedCourt ? spriteSize : clamp(spriteSize, 38, 64);
  const x = rect.left + ball.x * scaleX;
  const y = rect.top + ball.y * scaleY;
  visual.style.setProperty('--cross-size', `${size}px`);
  visual.style.transform = `translate3d(${x - size / 2}px,${y - size / 2}px,0) rotate(${ball.rotation || 0}rad)`;
  visual.classList.add('is-crossing');
}

function syncOutsideBallOverlays() {
  const active = new Set();
  for (const lane of sharedCourt.lanes) for (const ball of [lane.ball, ...lane.guests]) {
    if (!state.running || ball === trackedGuestBall || !ball.allowOuterExit
        || (!ball.flying && ball.poolState !== 'returning') || ball.inTransit || ball.expired) continue;
    const bounds = sharedCourt.bounds(lane);
    if (ball.x - ball.r >= bounds.left && ball.x + ball.r <= bounds.right) continue;
    active.add(ball);
    let visual = outsideBallOverlays.get(ball);
    if (!visual) {
      visual = crossBall.cloneNode(false);
      visual.removeAttribute('id');
      visual.removeAttribute('style');
      visual.classList.add('auxiliary-cross-ball', 'is-interactive');
      visual.addEventListener('pointerdown', event => beginCourtInterference(lane, event, true));
      visual.addEventListener('pointermove', event => { if (interferenceLane) moveCourtInterference(event); });
      visual.addEventListener('pointerup', endInterference);
      visual.addEventListener('pointercancel', endInterference);
      document.body.appendChild(visual);
      outsideBallOverlays.set(ball, visual);
    }
    ball.pageOverlay = true;
    positionPageBall(visual, ball, lane);
  }
  for (const [ball, visual] of outsideBallOverlays) if (!active.has(ball)) {
    visual.remove();
    if (ball !== trackedGuestBall) ball.pageOverlay = false;
    outsideBallOverlays.delete(ball);
  }
}

function animateCross(direction, sourceLane, startY, targetLane, endY, duration, radius, motion, onArrive, elapsedSeconds = 0, onProgress = null) {
  const sourceRect = sourceLane.canvas.getBoundingClientRect();
  const targetRect = targetLane.canvas.getBoundingClientRect();
  const sourceClientY = sourceRect.top + sourceRect.height * clamp(startY / sourceLane.height, 0, 1);
  const targetClientY = targetRect.top + targetRect.height * clamp(endY / targetLane.height, 0, 1);
  const startClientX = direction === 'right' ? sourceRect.right : sourceRect.left;
  const endClientX = direction === 'right' ? targetRect.left : targetRect.right;
  const visualScale = sourceRect.width / sourceLane.width;
  const size = clamp(radius * 2.46 * visualScale, 38, 64);
  const totalSeconds = Math.max(duration / 1000, .001);
  const initialVy = (motion?.vy || 0) * sourceRect.height / sourceLane.height;
  const verticalAcceleration = 2 * (targetClientY - sourceClientY - initialVy * totalSeconds)
    / (totalSeconds * totalSeconds);
  const initialRotation = motion?.rotation || 0;
  const rotationDelta = direction === 'right' ? Math.PI : -Math.PI;
  cancelAnimationFrame(crossAnimationFrame);
  cancelAnimationFrame(crossHideFrame);
  crossBall.style.setProperty('--cross-size', `${size}px`);
  crossBall.style.transform = `translate3d(${startClientX - size / 2}px,${sourceClientY - size / 2}px,0) rotate(${initialRotation}rad)`;
  crossBall.classList.add('is-crossing');
  const startedAt = performance.now() - Math.min(elapsedSeconds, totalSeconds) * 1000;
  const step = now => {
    const elapsed = Math.min((now - startedAt) / 1000, totalSeconds);
    const progress = elapsed / totalSeconds;
    onProgress?.(progress);
    const x = startClientX + (endClientX - startClientX) * progress;
    const y = sourceClientY + initialVy * elapsed + .5 * verticalAcceleration * elapsed * elapsed;
    const rotation = initialRotation + rotationDelta * progress;
    crossBall.style.transform = `translate3d(${x - size / 2}px,${y - size / 2}px,0) rotate(${rotation}rad)`;
    if (progress < 1) {
      crossAnimationFrame = requestAnimationFrame(step);
      return;
    }
    crossAnimationFrame = 0;
    const keepOnTop = onArrive?.() === true;
    if (keepOnTop) return;
    // Keep the overlay for one more paint. The destination canvas draws the
    // guest ball on the next game frame, then this overlay can disappear with
    // no empty frame or flash at the hand-off point.
    crossHideFrame = requestAnimationFrame(() => {
      crossBall.classList.remove('is-crossing');
      crossHideFrame = 0;
    });
  };
  crossAnimationFrame = requestAnimationFrame(step);
}

function animateAuxiliaryCross(direction, sourceLane, startY, targetLane, endY, duration, radius, owner, onArrive, elapsedSeconds = 0) {
  const sourceRect = sourceLane.canvas.getBoundingClientRect();
  const targetRect = targetLane.canvas.getBoundingClientRect();
  const sourceClientY = sourceRect.top + sourceRect.height * clamp(startY / sourceLane.height, 0, 1);
  const targetClientY = targetRect.top + targetRect.height * clamp(endY / targetLane.height, 0, 1);
  const startClientX = direction === 'right' ? sourceRect.right : sourceRect.left;
  const endClientX = direction === 'right' ? targetRect.left : targetRect.right;
  const size = clamp(radius * 2.46 * sourceRect.width / sourceLane.width, 38, 64);
  const visual = crossBall.cloneNode(false);
  visual.removeAttribute('id');
  visual.removeAttribute('style');
  visual.classList.add('is-crossing','auxiliary-cross-ball');
  visual.style.setProperty('--cross-size', `${size}px`);
  document.body.appendChild(visual);
  const startTransform = `translate3d(${startClientX - size / 2}px,${sourceClientY - size / 2}px,0) rotate(0deg)`;
  const endTransform = `translate3d(${endClientX - size / 2}px,${targetClientY - size / 2}px,0) rotate(${direction === 'right' ? 180 : -180}deg)`;
  const animation = visual.animate(
    [{ transform:startTransform }, { transform:endTransform }],
    { duration:Math.max(1, duration), easing:'linear', fill:'forwards' }
  );
  animation.currentTime = Math.min(elapsedSeconds * 1000, Math.max(1, duration));
  const transit = { visual, animation, owner };
  auxiliaryTransitBalls.add(transit);
  const finish = () => {
    if (!auxiliaryTransitBalls.delete(transit)) return;
    onArrive?.();
    visual.remove();
  };
  animation.finished.then(finish).catch(finish);
}

function clearTransits() {
  cancelAnimationFrame(crossAnimationFrame);
  cancelAnimationFrame(crossHideFrame);
  crossAnimationFrame = 0;
  crossHideFrame = 0;
  auxiliaryTransitBalls.forEach(({ visual, animation }) => {
    animation.cancel();
    visual.remove();
  });
  auxiliaryTransitBalls.clear();
  for (const [ball, visual] of outsideBallOverlays) {
    ball.pageOverlay = false;
    visual.remove();
  }
  outsideBallOverlays.clear();
  clearTrackedGuestBall();
}

function crossTransitState(data, sourceLane, targetLane, sourceRect, targetRect, entersFromLeft, radius) {
  const sourceScaleX = sourceRect.width / sourceLane.width;
  const sourceScaleY = sourceRect.height / sourceLane.height;
  const targetScaleX = targetRect.width / targetLane.width;
  const targetScaleY = targetRect.height / targetLane.height;
  const gap = entersFromLeft ? targetRect.left - sourceRect.right : sourceRect.left - targetRect.right;
  const horizontalScreenSpeed = Math.max(Math.abs(data.vx) * sourceScaleX, 1);
  const duration = Math.max(1, Math.abs(gap) / horizontalScreenSpeed * 1000);
  const seconds = duration / 1000;
  const sourceClientY = sourceRect.top + data.y * sourceScaleY;
  const targetClientY = sourceClientY
    + data.vy * sourceScaleY * seconds
    + .5 * COURT_GRAVITY * sourceScaleY * seconds * seconds;
  return {
    duration,
    y:clamp((targetClientY - targetRect.top) / targetScaleY, -radius, targetLane.height + radius),
    vx:data.vx * sourceScaleX / targetScaleX,
    vy:(data.vy + COURT_GRAVITY * seconds) * sourceScaleY / targetScaleY,
    rotation:(data.rotation || 0) + data.vx * seconds / Math.max(10, radius) * .55
  };
}

function transferBall(data, sourceLane, targetLane) {
  const entersFromLeft = data.edge === 'right';
  let radius = clamp(data.r, 15, 26);
  const sourceRect = sourceLane.canvas.getBoundingClientRect();
  const targetRect = targetLane.canvas.getBoundingClientRect();
  let transit = crossTransitState(data, sourceLane, targetLane, sourceRect, targetRect, entersFromLeft, radius);
  const playerTransfer = data.owner === 'player' && sourceLane === playerLane && targetLane === nekoLane;
  if (playerTransfer) state.nekoAttention.crossThreat = NEKO_ATTENTION.CROSS_WINDOW;
  state.crossCount += 1;
  sound(310, .06, 'triangle');
  const arrive = () => {
    if (playerTransfer) trackedCrossTransit = null;
    if (!state.running) return false;
    const guest = targetLane.receiveGuestBall({
      x:entersFromLeft ? 0 : targetLane.width,
      y:transit.y,
      r:radius,
      vx:transit.vx,
      vy:transit.vy,
      owner:data.owner,
      fever:data.fever,
      rotation:transit.rotation,
      allowOuterExit:playerTransfer
    });
    if (playerTransfer) return trackGuestBall(guest, targetLane);
    sourceLane.resetBall();
    return false;
  };
  if (playerTransfer) {
    const trackedTransit = {
      progress:clamp((data.stepRemainder || 0) / Math.max(transit.duration / 1000, .001), 0, 1),
      restart:null
    };
    const rememberProgress = progress => {
      if (trackedCrossTransit === trackedTransit) trackedTransit.progress = progress;
    };
    trackedTransit.restart = () => {
      const sourceBall = sourceLane.ball;
      const resizedData = {
        ...data,
        x:entersFromLeft ? sourceLane.width : 0,
        y:sourceBall.y,
        r:sourceBall.r,
        vx:sourceBall.vx,
        vy:sourceBall.vy,
        rotation:sourceBall.rotation || 0,
        sourceWidth:sourceLane.width,
        sourceHeight:sourceLane.height,
        stepRemainder:0
      };
      radius = clamp(resizedData.r, 15, 26);
      transit = crossTransitState(
        resizedData,
        sourceLane,
        targetLane,
        sourceLane.canvas.getBoundingClientRect(),
        targetLane.canvas.getBoundingClientRect(),
        entersFromLeft,
        radius
      );
      animateCross(
        'right', sourceLane, resizedData.y, targetLane, transit.y, transit.duration, radius,
        { vy:resizedData.vy, rotation:resizedData.rotation },
        arrive,
        transit.duration / 1000 * trackedTransit.progress,
        rememberProgress
      );
    };
    trackedCrossTransit = trackedTransit;
    trackedGuestSuspended = true;
    syncTrackedGuestInteraction();
    animateCross(
      'right', sourceLane, data.y, targetLane, transit.y, transit.duration, radius,
      { vy:data.vy, rotation:data.rotation || 0 },
      arrive,
      data.stepRemainder || 0,
      rememberProgress
    );
  } else {
    // A native ball whose possession flipped leaves through the auxiliary clone.
    // If the page overlay was following it, park the overlay until the source
    // lane hands out its next ball; otherwise the ball is drawn twice.
    const releasesTrackedBall = trackedGuestBall === sourceLane.ball;
    if (releasesTrackedBall) {
      trackedGuestSuspended = true;
      crossBall.classList.remove('is-crossing');
      syncTrackedGuestInteraction();
    }
    const arriveAuxiliary = () => {
      const result = arrive();
      if (releasesTrackedBall && trackedGuestSuspended && trackedGuestBall !== sourceLane.ball) {
        trackGuestBall(sourceLane.ball, sourceLane);
      }
      return result;
    };
    animateAuxiliaryCross(
      entersFromLeft ? 'right' : 'left', sourceLane, data.y, targetLane,
      transit.y, transit.duration, radius, data.owner, arriveAuxiliary, data.stepRemainder || 0
    );
  }
  return true;
}

function crossScored(owner, targetSide, data) {
  if (!state[owner]) return;
  scored(owner, { clean:data.clean, crossCourt:true });
  callout(targetSide, t('crossScore'));
}

function nativeScored(laneSide, data, ball) {
  if (data.owner === 'neko' && ball?.nativeShot) scored('neko', data);
  else if (data.owner && data.owner !== laneSide) crossScored(data.owner, laneSide, data);
  else scored(laneSide, data);
}

function nativeMissed(laneSide, data) {
  const owner = data?.owner === 'player' || data?.owner === 'neko' ? data.owner : laneSide;
  missed(owner);
}

function ballClashed(targetSide, data) {
  state.ballClashCount += 1;
  callout(targetSide, t(data.ownershipChanged ? 'stolenBall' : 'ballClash'));
  sound(330, .08, 'square');
}

playerLane.onCross = data => transferBall(data, playerLane, nekoLane);
nekoLane.onCross = data => transferBall(data, nekoLane, playerLane);
playerLane.onGuestScore = data => nativeScored('player', data);
nekoLane.onGuestScore = data => nativeScored('neko', data);
playerLane.onBallClash = data => ballClashed('player', data);
nekoLane.onBallClash = data => ballClashed('neko', data);

function scored(side, { clean, crossCourt = false }) {
  const actor = state[side];
  if (side === 'neko') actor.shotMissStreak = 0;
  actor.combo += 1;
  actor.makes += 1;
  actor.feverCharge += 1;
  let feverStarted = false;
  if (actor.fever <= 0 && actor.feverCharge >= FEVER_HITS) {
    actor.fever = FEVER_SECONDS;
    actor.feverCharge = 0;
    feverStarted = true;
    laneFor(side).setFever(true);
  }
  const basePoints = (clean ? 3 : 2) + (crossCourt ? 1 : 0);
  // Fever and the final-ten-second clutch phase share one visible bonus slot.
  // This keeps both sides on the same curve and caps every possible basket at
  // five instead of letting bonuses multiply into a match-deciding spike.
  const clutch = state.mode === 'timed' && state.remaining <= 10;
  const points = Math.min(5, basePoints + (actor.fever > 0 || clutch ? 1 : 0));
  actor.score += points;
  if (actor.combo >= 2) showCombo(side, actor.combo, feverStarted || actor.fever > 0);
  else clearCombo(side);
  const newStage = stageForScore(actor.score);
  const stageAdvanced = newStage > actor.stage;
  if (stageAdvanced) {
    actor.stage = newStage;
    laneFor(side).setStage(newStage);
  }
  if (stageAdvanced && !sharedCourt.fixedHoop) {
    callout(side, `${t('stageUp')} ${newStage}`);
    sound(920, .24, 'triangle');
  } else {
    if (feverStarted) callout(side, t('feverOn'));
    else if (actor.combo === 1) callout(side, t(clean ? 'swish' : 'basket'));
    sound(feverStarted ? 980 : clean ? 760 : 540, feverStarted ? .24 : .14);
  }
  syncHud(side);
  if (side === 'neko') {
    reactNeko(feverStarted ? 'shoot' : 'score');
    if (feverStarted || actor.combo % 3 === 0) speakNeko('voiceScore', { kind:'neko-score' });
  }
}

function missed(side) {
  const actor = state[side];
  if (side === 'neko') actor.shotMissStreak = Math.min(3, actor.shotMissStreak + 1);
  actor.combo = 0;
  if (actor.fever <= 0) actor.feverCharge = 0;
  clearCombo(side);
  syncHud(side);
  callout(side, t('miss'));
  sound(150, .05, 'triangle');
}

function canvasPoint(event, lane=playerLane) {
  const rect = lane.canvas.getBoundingClientRect();
  return {
    x:(event.clientX - rect.left) * lane.width / Math.max(1, rect.width),
    y:(event.clientY - rect.top) * lane.height / Math.max(1, rect.height)
  };
}

let interferencePointer = null;
let interferenceLane = null;
let interferencePointerId = null;
let interferenceBall = null;
let interferenceFromReady = false;

function ballNearPointer(lane, point) {
  return [lane.ball, ...lane.guests].filter(ball => !ball.expired && !ball.inTransit
    && !lane.isHeldBall(ball) && (ball.flying || lane.isReadyBall(ball)))
    .map(ball => ({ lane, ball, distance:Math.hypot(point.x - ball.x, point.y - ball.y) / ball.r }))
    .filter(candidate => candidate.distance < (candidate.ball.flying ? 4.5 : 2.5));
}

function beginCourtInterference(lane, event, nearBallOnly = false) {
  if (!state.running || state.mouseStealActive
      || interferenceLane || playerAimLane) return false;
  // Either actor's balls can now occupy either half of the same court.
  const nearby = sharedCourt.lanes.flatMap(candidate => ballNearPointer(candidate, canvasPoint(event, candidate)))
    .sort((a, b) => a.distance - b.distance)[0];
  if (nearby) lane = nearby.lane;
  else if (nearBallOnly) return false;
  const point = canvasPoint(event, lane);
  if (nearby && lane.isReadyBall(nearby.ball)) return beginReadyThrow(lane, event);
  if (!hasFocus('player', 8)) return false;
  if (!beginPlayerAction(NEKO_ACTION.PLAYER)) return false;
  interferencePointer = point;
  interferenceLane = lane;
  interferencePointerId = event.pointerId;
  interferenceBall = nearby?.ball || null;
  interferenceFromReady = interferenceBall ? lane.isReadyBall(interferenceBall) : false;
  if (interferenceFromReady) {
    interferenceBall.dragged = true;
    lane.selectReadyBall();
  }
  event.currentTarget?.setPointerCapture?.(event.pointerId);
  lane.canvas.classList.add('is-interfering');
  return true;
}

function moveCourtInterference(event) {
  if (event.pointerId !== interferencePointerId) return;
  if (!interferencePointer || !interferenceLane || !state.running || state.player.focus <= 0) return;
  const lane = interferenceLane;
  if (interferenceBall && (!lane.ballPool.includes(interferenceBall)
      || (interferenceFromReady ? !interferenceBall.dragged || !lane.isReadyBall(interferenceBall) : !interferenceBall.flying))) {
    endInterference();
    return;
  }
  const point = canvasPoint(event, lane);
  const dx = point.x - interferencePointer.x;
  const dy = point.y - interferencePointer.y;
  const requestedCost = Math.hypot(dx, dy) * ACTION_BALANCE.JAM_DRAG_SCALE;
  // Keep sub-threshold movement pending instead of dropping every tiny event.
  // Otherwise a slow mouse drag never gets far enough to move the ball.
  if (requestedCost <= 1) return;
  const cost = Math.min(requestedCost, state.player.focus);
  if (cost > 1 && spendFocus('player', cost)) {
    const appliedRatio = cost / requestedCost;
    const appliedDx = dx * appliedRatio;
    const appliedDy = dy * appliedRatio;
    const appliedPoint = {
      x:interferencePointer.x + appliedDx,
      y:interferencePointer.y + appliedDy
    };
    lane.interfere(appliedDx, appliedDy, appliedPoint, interferenceBall);
    state.interferenceCount += 1;
    byId('neko-interference-hint').style.opacity = '.18';
    lane.canvas.closest('.machine-screen').classList.add('is-disrupted');
    setTimeout(() => lane.canvas.closest('.machine-screen').classList.remove('is-disrupted'), 250);
  }
  interferencePointer = point;
}

function endInterference(event) {
  if (event && interferencePointerId !== null && event.pointerId !== interferencePointerId) return;
  if (interferenceBall) interferenceBall.dragged = false;
  interferenceLane?.canvas.classList.remove('is-interfering');
  interferencePointer = null;
  interferenceLane = null;
  interferencePointerId = null;
  interferenceBall = null;
  interferenceFromReady = false;
  endPlayerAction(NEKO_ACTION.PLAYER);
}

let playerAimPointer = null;
let playerAimLane = null;
let hasThrown = false;

function syncShotHint(aimAllowed) {
  const lane = playerAimLane;
  const ball = lane?.heldBall;
  if (!ball || (aimAllowed && hasThrown)) {
    shotHint.hidden = true;
    return;
  }
  const copy = t(aimAllowed ? 'shotFirstHint' : 'shotFocusLow');
  if (shotHint.textContent !== copy) shotHint.textContent = copy;
  const rect = lane.canvas.getBoundingClientRect();
  const x = rect.left + ball.x * rect.width / lane.width;
  const y = rect.top + (ball.y - ball.r - 24) * rect.height / lane.height;
  shotHint.style.setProperty('--shot-hint-x', `${x}px`);
  shotHint.style.top = `${y}px`;
  shotHint.hidden = false;
}

function beginReadyThrow(lane, event) {
  if (!state.running || state.mouseStealActive || playerAimLane
      || !beginPlayerAction(NEKO_ACTION.HOOP)) return false;
  if (!lane.beginAim(canvasPoint(event, lane), event.timeStamp)) {
    endPlayerAction(NEKO_ACTION.HOOP);
    return false;
  }
  playerAimLane = lane;
  playerAimPointer = event.pointerId;
  event.currentTarget?.setPointerCapture?.(event.pointerId);
  lane.canvas.classList.add('aiming');
  guide.style.opacity = '0';
  return true;
}

function moveReadyAim(event) {
  const lane = playerAimLane;
  if (!lane?.heldBall) return;
  // A quick bend can be inside a browser-coalesced pointer move. Preserve
  // those real samples before applying the final visible pointer position.
  for (const sample of event.getCoalescedEvents?.() || []) {
    if (sample.timeStamp >= (lane.aimMotion?.samples.at(-1)?.time ?? -Infinity)) {
      lane.moveAim(canvasPoint(sample, lane), sample.timeStamp);
    }
  }
  lane.moveAim(canvasPoint(event, lane), event.timeStamp);
}

playerLane.canvas.addEventListener('pointerdown', event => {
  if (!state.running || state.mouseStealActive || playerAimLane) return;
  const point = canvasPoint(event, playerLane);
  const pickingSupply = playerLane.ballPool.some(ball => playerLane.isReadyBall(ball)
    && Math.hypot(point.x - ball.x, point.y - ball.y) <= ball.r * 2.5);
  if (!pickingSupply && beginCourtInterference(playerLane, event, true)) return;
  beginReadyThrow(playerLane, event);
});
playerLane.canvas.addEventListener('pointermove', event => {
  if (state.mouseStealActive) return;
  if (interferenceLane) {
    moveCourtInterference(event);
    return;
  }
  if (event.pointerId !== playerAimPointer) return;
  moveReadyAim(event);
});
function shootPlayer(vx, vy) {
  if (!state.running || !hasFocus('player', ACTION_BALANCE.SHOT_COST)
      || !beginPlayerAction(NEKO_ACTION.HOOP)) return false;
  if (!playerLane.shoot(vx, vy)) {
    endPlayerAction(NEKO_ACTION.HOOP);
    return false;
  }
  spendFocus('player', ACTION_BALANCE.SHOT_COST);
  state.player.attempts += 1;
  endPlayerAction(NEKO_ACTION.HOOP);
  return true;
}

function releasePlayerShot() {
  if (!state.running) {
    cancelPlayerAction();
    return;
  }
  let shot = false;
  const lane = playerAimLane || playerLane;
  if (hasFocus('player', ACTION_BALANCE.SHOT_COST)) shot = lane.releaseAim('player');
  else lane.cancelAim();
  playerAimPointer = null;
  playerAimLane = null;
  if (shot) {
    hasThrown = true;
    spendFocus('player', ACTION_BALANCE.SHOT_COST);
    state.player.attempts += 1;
    sound(210, .04, 'triangle');
  } else if (!hasFocus('player', ACTION_BALANCE.SHOT_COST)) {
    sound(125, .05, 'triangle');
  }
  lane.canvas.classList.remove('aiming');
  endPlayerAction(NEKO_ACTION.HOOP);
}

function cancelPlayerAction() {
  for (const lane of sharedCourt.lanes) {
    lane.cancelAim();
    lane.canvas.classList.remove('aiming');
  }
  playerAimPointer = null;
  playerAimLane = null;
  endInterference();
  avatarPointer = null;
  state.player.action = NEKO_ACTION.IDLE;
}
function releasePlayerPointer(event) {
  if (playerAimPointer !== null && event.pointerId !== playerAimPointer) return;
  if (interferenceLane) {
    if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) moveCourtInterference(event);
    endInterference(event);
  } else {
    // Fast releases may reach a new position before another pointermove is
    // delivered. Use the release position rather than the previous frame.
    if (playerAimLane?.heldBall && Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
      moveReadyAim(event);
    }
    releasePlayerShot();
  }
}
playerLane.canvas.addEventListener('pointerup', releasePlayerPointer);
playerLane.canvas.addEventListener('pointercancel', event => {
  if (interferenceLane) {
    endInterference(event);
    return;
  }
  if (playerAimPointer !== null && event.pointerId !== playerAimPointer) return;
  cancelPlayerAction();
});
playerLane.canvas.addEventListener('lostpointercapture', event => {
  if (event.pointerId === playerAimPointer) cancelPlayerAction();
});
// Release outside a canvas and interrupted capture must both unlock the next
// grab. Ordinary bubbled pointerup events are harmless after local cleanup.
document.addEventListener('lostpointercapture', event => {
  if (event.pointerId === playerAimPointer) cancelPlayerAction();
  else if (event.pointerId === interferencePointerId) endInterference(event);
}, true);
document.addEventListener('pointerup', event => {
  if (event.pointerId === playerAimPointer) releasePlayerPointer(event);
  else if (event.pointerId === interferencePointerId) endInterference(event);
});
document.addEventListener('pointercancel', event => {
  if (event.pointerId === playerAimPointer) cancelPlayerAction();
  else if (event.pointerId === interferencePointerId) endInterference(event);
});
window.addEventListener('blur', cancelPlayerAction);

function syncMouseStealVisual() {
  if (!mouseSteal.active || state.paused) return;
  const dx = mouseSteal.x - mouseSteal.targetX;
  const dy = mouseSteal.y - mouseSteal.targetY;
  const length = Math.hypot(dx, dy);
  mouseStealLayer.style.setProperty('--cursor-x', `${mouseSteal.x}px`);
  mouseStealLayer.style.setProperty('--cursor-y', `${mouseSteal.y}px`);
  mouseStealLayer.style.setProperty('--tether-x', `${mouseSteal.targetX}px`);
  mouseStealLayer.style.setProperty('--tether-y', `${mouseSteal.targetY}px`);
  mouseStealLayer.style.setProperty('--tether-length', `${length}px`);
  mouseStealLayer.style.setProperty('--tether-angle', `${Math.atan2(dy, dx)}rad`);
}

function endMouseSteal(escaped = false) {
  if (!mouseSteal.active) {
    endNekoAction(NEKO_ACTION.MOUSE);
    return false;
  }
  matchTimers.clear(mouseStealTimer);
  mouseSteal.active = false;
  state.mouseStealActive = false;
  endNekoAction(NEKO_ACTION.MOUSE);
  state.nextMouseSteal = randomBetween(MOUSE_STEAL_COOLDOWN.MIN, MOUSE_STEAL_COOLDOWN.MAX);
  state.disruptionGrace = Math.max(state.disruptionGrace, ACTION_BALANCE.DISRUPTION_GRACE);
  state.player.hitGrace = Math.max(state.player.hitGrace, ACTION_BALANCE.DISRUPTION_GRACE);
  document.body.classList.remove('is-mouse-stolen');
  mouseStealLayer.classList.toggle('is-escaped', escaped);
  mouseStealTitle.textContent = t(escaped ? 'mouseStealEscaped' : 'mouseStealReleased', { name:opponentName });
  if (escaped) {
    state.mouseStealEscapes += 1;
    sound(680, .1, 'triangle');
  } else {
    sound(210, .08, 'square');
    if (playerLane.ball.flying) {
      const ball = playerLane.ball;
      playerLane.interfere(-18, 10, { x:ball.x, y:ball.y });
    }
  }
  mouseStealVisualTimer = setTimeout(() => {
    if (mouseSteal.active) return;
    mouseStealLayer.classList.remove('is-active','is-escaped');
    mouseStealLayer.setAttribute('aria-hidden', 'true');
  }, 420);
  setTimeout(() => {
    if (state.running && !state.nekoCounterPending && !mouseSteal.active) {
      setNekoStatus(state.neko.fever > 0 ? 'opponentFever' : 'opponentReady');
    }
  }, 520);
  return true;
}

function beginMouseSteal() {
  if (!state.running || mouseSteal.active || state.nekoCounterPending
      || state.mouseStealCount >= ACTION_BALANCE.MOUSE_MAX_PER_ROUND
      || state.disruptionGrace > 0 || !hasFocus('neko', ACTION_BALANCE.MOUSE_COST)
      || state.neko.stagger > 0
      || !beginNekoAction(NEKO_ACTION.MOUSE)) return false;
  spendFocus('neko', ACTION_BALANCE.MOUSE_COST);
  clearTimeout(mouseStealVisualTimer);
  cancelPlayerAction();
  const avatarRect = nekoAvatar.getBoundingClientRect();
  const playerRect = playerLane.canvas.getBoundingClientRect();
  mouseSteal.targetX = avatarRect.left + avatarRect.width * .38;
  mouseSteal.targetY = avatarRect.top + avatarRect.height * .38;
  mouseSteal.x = pointerMemory.seen ? pointerMemory.x : playerRect.left + playerRect.width * .52;
  mouseSteal.y = pointerMemory.seen ? pointerMemory.y : playerRect.top + playerRect.height * .72;
  mouseSteal.lastX = pointerMemory.x;
  mouseSteal.lastY = pointerMemory.y;
  mouseSteal.struggle = 0;
  mouseSteal.startedAt = performance.now();
  mouseSteal.duration = randomBetween(MOUSE_STEAL_DURATION.MIN, MOUSE_STEAL_DURATION.MAX);
  mouseSteal.escapeThreshold = 500 - (mouseSteal.duration - MOUSE_STEAL_DURATION.MIN)
    / (MOUSE_STEAL_DURATION.MAX - MOUSE_STEAL_DURATION.MIN) * 120;
  mouseSteal.active = true;
  state.mouseStealActive = true;
  state.mouseStealDurationMs = Math.round(mouseSteal.duration);
  state.mouseStealCount += 1;
  mouseStealTitle.textContent = t('mouseStealCaught', { name:opponentName });
  setNekoStatus('mouseStealStatus');
  mouseStealLayer.classList.remove('is-escaped');
  mouseStealLayer.classList.add('is-active');
  mouseStealLayer.setAttribute('aria-hidden', 'false');
  document.body.classList.add('is-mouse-stolen');
  reactNeko('steal');
  speakNeko('voiceMouseSteal', { kind:'mouse-steal', interrupt:true });
  sound(440, .08, 'triangle');
  syncMouseStealVisual();
  mouseStealTimer = matchTimers.set(() => endMouseSteal(false), mouseSteal.duration);
  return true;
}

function mouseEscapeTarget() {
  const progress = clamp((performance.now() - mouseSteal.startedAt) / Math.max(1, mouseSteal.duration), 0, 1);
  return Math.max(280, mouseSteal.escapeThreshold - progress * 100);
}

document.addEventListener('pointermove', event => {
  pointerMemory.x = event.clientX;
  pointerMemory.y = event.clientY;
  pointerMemory.seen = true;
  if (!mouseSteal.active || state.paused) return;
  const distance = Math.hypot(event.clientX - mouseSteal.lastX, event.clientY - mouseSteal.lastY);
  mouseSteal.struggle += Math.min(distance, 75);
  mouseSteal.lastX = event.clientX;
  mouseSteal.lastY = event.clientY;
  mouseSteal.x = event.clientX * .38 + mouseSteal.targetX * .62;
  mouseSteal.y = event.clientY * .38 + mouseSteal.targetY * .62;
  syncMouseStealVisual();
  if (mouseSteal.struggle >= mouseEscapeTarget()) endMouseSteal(true);
}, { capture:true, passive:true });

document.addEventListener('pointerdown', () => {
  if (!mouseSteal.active || state.paused) return;
  mouseSteal.struggle += 95;
  if (mouseSteal.struggle >= mouseEscapeTarget()) endMouseSteal(true);
}, { capture:true, passive:true });

nekoLane.canvas.addEventListener('pointerdown', event => {
  beginCourtInterference(nekoLane, event);
});
nekoLane.canvas.addEventListener('pointermove', event => {
  if (interferenceLane) moveCourtInterference(event);
  else if (event.pointerId === playerAimPointer) {
    moveReadyAim(event);
  }
});
nekoLane.canvas.addEventListener('pointerup', event => {
  if (event.pointerId === playerAimPointer) releasePlayerPointer(event);
  else if (interferenceLane) endInterference(event);
});
nekoLane.canvas.addEventListener('pointercancel', event => {
  if (event.pointerId === playerAimPointer) cancelPlayerAction();
  else if (interferenceLane) endInterference(event);
});

crossBall.addEventListener('pointerdown', event => {
  if (!crossBall.classList.contains('is-interactive')) return;
  if (!beginCourtInterference(trackedGuestLane || nekoLane, event, true)) return;
  event.preventDefault();
  event.stopPropagation();
});
crossBall.addEventListener('pointermove', event => {
  if (interferenceLane) moveCourtInterference(event);
});
crossBall.addEventListener('pointerup', event => {
  if (interferenceLane) endInterference(event);
});
crossBall.addEventListener('pointercancel', event => {
  if (interferenceLane) endInterference(event);
});

let avatarPointer = null;
let lastAvatarHit = 0;

function hitNeko(direction = 1, source = 'pointer') {
  const now = performance.now();
  const ballImpact = source === 'ball';
  const cost = ballImpact ? 0 : ACTION_BALANCE.AVATAR_HIT_COST;
  if (!state.running || !hasFocus('player', cost)
      || state.neko.hitGrace > 0
      || (!ballImpact && now - lastAvatarHit < ACTION_BALANCE.PLAYER_HIT_COOLDOWN * 1000)) return false;
  lastAvatarHit = now;
  if (!ballImpact) spendFocus('player', cost);
  if (state.nekoCounterPending) {
    matchTimers.clear(counterTimer);
    state.nekoCounterPending = false;
    playerLane.canvas.closest('.machine-screen').classList.remove('incoming-warning');
    refundFocus('neko', ACTION_BALANCE.PRANK_COST);
    endNekoAction(NEKO_ACTION.PLAYER);
  }
  if (ballImpact && mouseSteal.active) endMouseSteal(true);
  state.neko.stagger = ACTION_BALANCE.STAGGER;
  state.neko.hitGrace = ACTION_BALANCE.HIT_GRACE;
  state.interferenceCount += 1;
  state.nekoHitCount += 1;
  if (ballImpact) {
    state.nekoBallHitCount += 1;
    state.nekoAttention.revenge = NEKO_ATTENTION.REVENGE_WINDOW;
  }
  state.nextNekoDecision = Math.max(state.nextNekoDecision, .45);
  const activeNekoBall = nekoLane.ballPool.find(ball => ball.flying);
  if (activeNekoBall) {
    const ball = activeNekoBall;
    nekoLane.interfere(direction * 20, 7, { x:ball.x, y:ball.y });
  }
  reactNeko('hit', direction);
  callout('neko', t('nekoHit'));
  setNekoStatus('nekoHit');
  speakNeko('voiceHit', { kind:'avatar-hit', interrupt:true });
  sound(ballImpact ? 76 : 105, ballImpact ? .13 : .08, 'square');
  setTimeout(() => {
    if (state.running) setNekoStatus(state.neko.fever > 0 ? 'opponentFever' : 'opponentReady');
  }, 520);
  return true;
}

function circleTouchesEllipse(x, y, radius, rect) {
  const centerX = rect.left + rect.width / 2;
  const centerY = rect.top + rect.height / 2;
  const radiusX = rect.width / 2 + radius;
  const radiusY = rect.height / 2 + radius;
  return ((x - centerX) / radiusX) ** 2 + ((y - centerY) / radiusY) ** 2 <= 1;
}

// Physics runs up to MAX_PHYSICS_STEPS_PER_FRAME steps per frame. Steps read
// layout through this cache so each element is measured once per frame instead
// of forcing a reflow between the style writes of every step.
let stepLayoutCache = null;
function stepLayout(key, read) {
  if (!stepLayoutCache) return read();
  if (!stepLayoutCache.has(key)) stepLayoutCache.set(key, read());
  return stepLayoutCache.get(key);
}

function avatarCollisionZones() {
  return [...nekoAvatar.querySelectorAll('.avatar-hit-zone')]
    .map(zone => stepLayout(zone, () => zone.getBoundingClientRect()));
}

function avatarIsReady() {
  return nekoAvatar.classList.contains('is-ready')
    && nekoAvatar.dataset.avatarReady === 'true';
}

function containTrackedBallAtViewportEdge(ball, court, scaleX, radius) {
  if (!ball.allowOuterExit) return false;
  const viewportRight = stepLayout('viewportWidth', () => document.documentElement.clientWidth);
  const x = court.left + ball.x * scaleX;
  if (x + radius <= viewportRight) return false;
  ball.x = (viewportRight - radius - court.left) / scaleX;
  if (ball.vx > 0) ball.vx = -Math.abs(ball.vx) * .76;
  ball.hitRim = true;
  return true;
}

function checkPlayerBallNekoHit() {
  for (const lane of sharedCourt.lanes) for (const ball of [lane.ball, ...lane.guests]) {
    if (!ball.flying || ball.expired || ball.inTransit) continue;
    checkBallNekoHit(ball, lane);
  }
}

function checkBallNekoHit(ball, lane) {
  const court = stepLayout(lane.canvas, () => lane.canvas.getBoundingClientRect());
  const scaleX = court.width / lane.width;
  const scaleY = court.height / lane.height;
  const x = court.left + ball.x * scaleX;
  const y = court.top + ball.y * scaleY;
  const radius = ball.r * Math.max(scaleX, scaleY) * 1.08;
  const canHitAvatar = ball.owner === 'player'
    && avatarIsReady()
    && (ball.flight?.depth ?? 0) < .32
    && !ball.avatarHit
    && Math.hypot(ball.vx, ball.vy) >= 320;
  const hitbox = canHitAvatar
    ? avatarCollisionZones().find(zone => circleTouchesEllipse(x, y, radius, zone))
    : null;
  if (hitbox) {
    ball.avatarHit = true;
    ball.x = (hitbox.left - court.left) / scaleX - ball.r - 2;
    ball.vx = -Math.max(Math.abs(ball.vx) * .68, 360);
    ball.vy -= 150;
    ball.hitRim = true;
    lane.burst(ball.x, ball.y, 28);
    hitNeko(1, 'ball');
    return;
  }
  containTrackedBallAtViewportEdge(ball, court, scaleX, radius);
}

nekoAvatar.addEventListener('pointerdown', event => {
  if (!state.running || state.mouseStealActive || !avatarIsReady()) return;
  if (beginCourtInterference(nekoLane, event, true)) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
  if (!beginPlayerAction(NEKO_ACTION.PLAYER)) return;
  const rect = nekoAvatar.getBoundingClientRect();
  avatarPointer = { x:event.clientX, y:event.clientY };
  nekoAvatar.setPointerCapture?.(event.pointerId);
  event.preventDefault();
  event.stopPropagation();
  hitNeko(event.clientX < rect.left + rect.width / 2 ? 1 : -1);
});

nekoAvatar.addEventListener('pointermove', event => {
  if (event.pointerId === playerAimPointer) {
    moveReadyAim(event);
    return;
  }
  if (interferenceLane) {
    moveCourtInterference(event);
    return;
  }
  if (!avatarPointer || !state.running) return;
  const dx = event.clientX - avatarPointer.x;
  const dy = event.clientY - avatarPointer.y;
  if (Math.hypot(dx, dy) >= 18 && hitNeko(dx < 0 ? -1 : 1)) {
    avatarPointer = { x:event.clientX, y:event.clientY };
  }
});

function endAvatarHit() {
  avatarPointer = null;
  endPlayerAction(NEKO_ACTION.PLAYER);
}
function endAvatarPointer(event) {
  if (event.pointerId === playerAimPointer) releasePlayerPointer(event);
  else if (interferenceLane) endInterference(event);
  else endAvatarHit();
}
nekoAvatar.addEventListener('pointerup', endAvatarPointer);
nekoAvatar.addEventListener('pointercancel', endAvatarPointer);

function throwChaosBall(targetLane, owner, fromEdge = 'right', onArrive) {
  const sourceLane = targetLane === playerLane ? nekoLane : playerLane;
  const hoop = targetLane.getHoopPose();
  const radius = clamp(targetLane.width * .055, 16, 23);
  if (targetLane.sharedCourt) {
    const origin = sourceLane.readyForLaunch();
    if (!origin) return;
    const target = targetLane.ball;
    const flight = .68 + Math.random() * .1;
    const anticipation = target.flying ? flight * .36 : 0;
    const depth = target.flight ? clamp(target.flight.depth + target.flight.vDepth * anticipation, 0, 1) : 0;
    const x = target.x + sharedCourt.origin(targetLane) - sharedCourt.origin(sourceLane)
      + target.vx * anticipation + (Math.random() - .5) * 42 * flight;
    const y = target.y + target.vy * anticipation + (Math.random() - .5) * 28 * flight;
    const guest = sourceLane.launchPoolBall(0, 0, { owner, spatial:{ seconds:flight, target:{ x, y, depth } } });
    if (!guest) return;
    state.crossCount += 1;
    onArrive?.(guest);
    return;
  }
  const x = fromEdge === 'right' ? targetLane.width + radius + 2 : -radius - 2;
  const y = targetLane.height * (.68 + Math.random() * .1);
  const flight = .68 + Math.random() * .1;
  // Prefer the opponent's ready ball as well as an airborne one. Previously an
  // idle ball made the prank fall back to the hoop, so only the player could
  // reliably start a cross-court ball collision.
  const targetBall = targetLane.ball.inTransit ? null : targetLane.ball;
  const anticipation = targetBall?.flying ? flight * .36 : 0;
  const targetX = targetBall ? targetBall.x + targetBall.vx * anticipation : hoop.x;
  const targetY = targetBall ? targetBall.y + targetBall.vy * anticipation : hoop.rimY;
  const vx = (targetX - x) / flight + (Math.random() - .5) * 42;
  const vy = (targetY - y - .5 * 720 * flight * flight) / flight + (Math.random() - .5) * 28;
  const sourceRect = sourceLane.canvas.getBoundingClientRect();
  const targetRect = targetLane.canvas.getBoundingClientRect();
  const gap = fromEdge === 'right' ? sourceRect.left - targetRect.right : targetRect.left - sourceRect.right;
  const targetScaleX = targetRect.width / targetLane.width;
  const duration = Math.max(1, Math.abs(gap) / Math.max(Math.abs(vx) * targetScaleX, 1) * 1000);
  const transitSeconds = duration / 1000;
  const startYInTarget = y - vy * transitSeconds + .5 * 720 * transitSeconds * transitSeconds;
  const startY = startYInTarget / targetLane.height * sourceLane.height;
  state.crossCount += 1;
  animateAuxiliaryCross(
    fromEdge === 'right' ? 'left' : 'right',
    sourceLane,
    startY,
    targetLane,
    y,
    duration,
    radius,
    owner,
    () => {
      const guest = state.running
        ? targetLane.receiveGuestBall({ x, y, r:radius, vx, vy, owner, fever:state[owner].fever > 0 })
        : null;
      onArrive?.(guest);
    }
  );
}

function beginNekoPrank() {
  if (!state.running || state.nekoCounterPending || !nekoPrankInventoryReady()
      || nekoLane.ball.flying || nekoLane.ball.inTransit || mouseSteal.active
      || state.disruptionGrace > 0 || !hasFocus('neko', ACTION_BALANCE.PRANK_COST)
      || state.neko.stagger > 0
      || !beginNekoAction(NEKO_ACTION.PLAYER)) return false;
  spendFocus('neko', ACTION_BALANCE.PRANK_COST);
  const playerScreen = playerLane.canvas.closest('.machine-screen');
  matchTimers.clear(counterTimer);
  state.nekoCounterPending = true;
  setNekoStatus('nekoCountering');
  playerScreen.classList.add('incoming-warning');
  reactNeko('aim');
  sound(420, .05, 'triangle');
  counterTimer = matchTimers.set(() => {
    state.nekoCounterPending = false;
    playerScreen.classList.remove('incoming-warning');
    if (!state.running) return;
    // The player may knock another Neko-owned ball loose during the warning.
    // Recheck at release time in case a contact used the ready supply.
    if (!nekoPrankInventoryReady()) {
      refundFocus('neko', ACTION_BALANCE.PRANK_COST);
      endNekoAction(NEKO_ACTION.PLAYER);
      state.nextNekoInterference = NEKO_SHOT_DELAY.RETURN_RETRY;
      setNekoStatus('opponentReady');
      return;
    }
    throwChaosBall(playerLane, 'neko', 'right');
    endNekoAction(NEKO_ACTION.PLAYER);
    state.disruptionGrace = Math.max(state.disruptionGrace, ACTION_BALANCE.DISRUPTION_GRACE);
    // Reserve enough time for this aimed prank to reach and launch the
    // player's ready ball before another hoop shot is considered.
    state.nextNekoDecision = Math.max(state.nextNekoDecision, NEKO_SHOT_DELAY.PRANK_RECOVERY);
    callout('player', t('chaosBall', { name:opponentName }));
    speakNeko('voiceChaos', { kind:'chaos-ball' });
    reactNeko('score');
    sound(115, .07, 'square');
    setNekoStatus('nekoCountering');
  }, 540);
  return true;
}

function runtimeSnapshot() {
  return {
    mode:state.mode,
    running:state.running,
    paused:state.paused,
    elapsed:state.elapsed,
    remaining:state.remaining,
    score:{ player:state.player.score, ai:state.neko.score },
    attempts:{ player:state.player.attempts, neko:state.neko.attempts },
    makes:{ player:state.player.makes, neko:state.neko.makes }
  };
}

function runtimeEndPayload(reason = 'match-ended') {
  return {
    reason,
    mode:state.mode,
    finalScore:{ player:state.player.score, ai:state.neko.score },
    currentState:runtimeSnapshot()
  };
}

function resetMatch() {
  // A new match interrupts the previous result line anyway; end its route now.
  skipResultSpeechWait?.();
  runtimeStartFailed = false;
  const currentMatch = ++matchSequence;
  const mode = selectedMode();
  const initialClock = mode === 'timed' ? ROUND_SECONDS : formatElapsed(0);
  matchTimers.clearAll();
  matchTimers.resume();
  state.paused = false;
  pauseOverlay.hidden = true;
  pauseMatchButton.hidden = false;
  pauseMatchButton.textContent = t('pause');
  pauseMatchButton.setAttribute('aria-pressed', 'false');
  document.body.classList.remove('is-match-paused');
  matchTimers.clear(counterTimer);
  endMouseSteal(false);
  clearTransits();
  nekoAiFrozen = false;
  playerLane.canvas.closest('.machine-screen').classList.remove('incoming-warning');
  Object.assign(state, { running:true, mode, remaining:mode === 'timed' ? ROUND_SECONDS : null, elapsed:0, nextNekoDecision:.68, nextNekoInterference:4.2, nextMouseSteal:randomBetween(10, 14), disruptionGrace:0, interferenceCount:0, nekoHitCount:0, nekoBallHitCount:0, crossCount:0, ballClashCount:0, mouseStealCount:0, mouseStealEscapes:0, mouseStealActive:false, mouseStealDurationMs:0, nekoOffenseIdle:0, nekoCounterPending:false, nekoAction:NEKO_ACTION.IDLE, nekoAttention:makeNekoAttention() });
  Object.assign(state.player, makeActor(initialClock));
  Object.assign(state.neko, makeActor(initialClock));
  timerAccumulator = 0;
  sharedCourt.clock = 0;
  sharedCourt.assembly.reset();
  sharedCourt.resetBallPools();
  trackGuestBall(playerLane.ball, playerLane);
  playerLane.setStage(1);
  nekoLane.setStage(1);
  playerLane.setFever(false);
  nekoLane.setFever(false);
  playerLane.clearGuests();
  nekoLane.clearGuests();
  syncHud('player');
  syncHud('neko');
  clearCombo('player');
  clearCombo('neko');
  matchClock.textContent = initialClock;
  renderClockLabels(mode);
  stopMatchButton.hidden = mode !== 'endless';
  setNekoStatus('opponentReady');
  resultBoard.hidden = true;
  overlay.classList.add('hidden');
  overlay.hidden = true;
  overlay.style.display = 'none';
  guide.style.opacity = '1';
  void unlockGameAudio().catch(error => console.warn('[air_basketball] SDK audio unlock failed', error));
  syncFocus('player');
  syncFocus('neko');
  void startGameRuntime({
    mode,
    gameStarted:true,
    game_started:true,
    gameStartedElapsedMs:0,
    currentState:runtimeSnapshot()
  }).then(() => {
    if (!state.running || currentMatch !== matchSequence) return;
    if (opponentName !== 'N.E.K.O') speakNeko('voiceOpening', { kind:'opening-line' });
  }).catch(error => {
    console.warn('[air_basketball] SDK runtime start failed', error);
    abortMatchWithoutRuntime(currentMatch);
  });
}

// A match must not keep running without its backend route (no heartbeat,
// memory or postgame); return to the start card and say why.
function abortMatchWithoutRuntime(matchId) {
  if (matchId !== matchSequence || !state.running) return;
  stopMatchPlay();
  runtimeStartFailed = true;
  resultBoard.hidden = true;
  startButton.textContent = t('start');
  renderStartCopy();
  showStartCard();
}

// Stop play without settling a result; shared by finishMatch and an aborted match.
function stopMatchPlay() {
  state.running = false;
  state.paused = false;
  matchTimers.clearAll();
  matchTimers.resume();
  pauseOverlay.hidden = true;
  pauseMatchButton.hidden = true;
  document.body.classList.remove('is-match-paused');
  // Drop any held aim, drag or avatar pointer: a release after this point must
  // not shoot into the final score or leave interference locked for next match.
  cancelPlayerAction();
  endMouseSteal(false);
  matchTimers.clear(counterTimer);
  clearTransits();
  state.nekoCounterPending = false;
  state.nekoAction = NEKO_ACTION.IDLE;
  playerLane.canvas.closest('.machine-screen').classList.remove('incoming-warning');
  playerLane.setFever(false);
  nekoLane.setFever(false);
}

function showStartCard() {
  stopMatchButton.hidden = true;
  overlay.hidden = false;
  overlay.style.display = 'grid';
  overlay.classList.remove('hidden');
}

function finishMatch() {
  stopMatchPlay();
  byId('final-player').textContent = state.player.score;
  byId('final-neko').textContent = state.neko.score;
  byId('result-title').textContent = state.player.score === state.neko.score ? t('draw') : state.player.score > state.neko.score ? t('win') : opponentStatus('opponentWin');
  resultBoard.hidden = false;
  startButton.textContent = t('again');
  showStartCard();
  sound(state.player.score >= state.neko.score ? 640 : 260, .25, 'triangle');
  const resultSpeechRequestId = state.player.score !== state.neko.score
    && speakNeko(state.neko.score > state.player.score ? 'voiceWin' : 'voiceLose', { kind:'match-result', interrupt:true });
  // Ending the route cancels speech that is still playing, so let the result
  // line finish first. Bound the wait well below the SDK's 60 s speech timeout,
  // and stop waiting as soon as the player starts another match.
  let resultSpeechSettled = null;
  if (resultSpeechRequestId) {
    resultSpeechSettled = Promise.race([
      latestSpeechPromise.then(() => waitForNekoSpeechPlayback(resultSpeechRequestId, RESULT_SPEECH_END_MAX_WAIT_MS)),
      new Promise(resolve => {
        skipResultSpeechWait = resolve;
        setTimeout(resolve, RESULT_SPEECH_END_MAX_WAIT_MS);
      })
    ]).finally(() => { skipResultSpeechWait = null; });
  }
  void endGameRuntime(runtimeEndPayload(), { after:resultSpeechSettled }).catch(error => {
    console.warn('[air_basketball] SDK runtime end failed', error);
  });
}

function updateFever(side, dt) {
  const actor = state[side];
  if (actor.fever <= 0) return;
  actor.fever = Math.max(0, actor.fever - dt);
  if (actor.fever === 0) laneFor(side).setFever(false);
  frameSync.hud.add(side);
}

function activeAuxiliaryBallCount(owner = null) {
  return [...auxiliaryTransitBalls].filter(ball => owner === null || ball.owner === owner).length;
}

function nekoActiveBallCount() {
  const laneBalls = playerLane.countActiveBalls({ owner:'neko' })
    + nekoLane.countActiveBalls({ owner:'neko' })
  const transitBalls = activeAuxiliaryBallCount('neko');
  // A native ball crossing away from its cabinet remains marked inTransit
  // while an auxiliary top-layer visual represents that same physical ball.
  const representedTwice = [playerLane, nekoLane].filter(
    lane => lane.ball.inTransit && lane.ball.owner === 'neko'
  ).length;
  return laneBalls + transitBalls - Math.min(transitBalls, representedTwice);
}

function nekoBallInventoryReady() {
  return Boolean(nekoLane.readyForLaunch());
}

function nekoPrankInventoryReady() {
  return nekoBallInventoryReady();
}

function nekoShotSupply() {
  const airborne = sharedCourt.poolBalls.filter(ball => (ball.flying || ball.inTransit)
    && ball.owner === 'neko' && ball.nativeShot).length;
  if (airborne >= NEKO_BALL_POOL.MAX_AIRBORNE) return null;

  if (nekoLane.ballPool.some(ball => ball.poolState !== 'wedged')) {
    const ball = nekoLane.readyForLaunch();
    return ball && !nekoLane.ball.flying && !nekoLane.ball.inTransit ? { lane:nekoLane, ball } : null;
  }

  // An empty tray can stay empty indefinitely. Use a surplus ball in place,
  // leaving the player's usual starting supply and any held ball alone.
  const surplus = playerLane.ballPool.filter(ball => playerLane.isReadyBall(ball) && !playerLane.isHeldBall(ball));
  if (surplus.length <= playerLane.poolCapacity) return null;
  const ball = surplus.reduce((closest, candidate) => candidate.x > closest.x ? candidate : closest);
  return { lane:playerLane, ball };
}

function shootNekoAtHoop(difficulty = .72) {
  if (!state.running
      || !hasFocus('neko', ACTION_BALANCE.SHOT_COST)
      || state.neko.stagger > 0) return false;
  const supply = nekoShotSupply();
  if (!supply || !beginNekoAction(NEKO_ACTION.HOOP)) return false;
  const { lane, ball } = supply;
  const shot = lane.releaseAutoShot(difficulty, { ball, owner:'neko', fever:nekoLane.fever, stage:nekoLane.stage });
  if (shot) {
    spendFocus('neko', ACTION_BALANCE.SHOT_COST);
    state.neko.attempts += 1;
    state.nekoOffenseIdle = 0;
  }
  endNekoAction(NEKO_ACTION.HOOP);
  return Boolean(shot);
}

function nekoShotAccuracy() {
  // Correct only Neko's own cold streak. Score-gap rubber-banding would make
  // identical-looking releases behave differently based on the scoreboard.
  return Math.min(
    NEKO_STRENGTH.MAX_ACCURACY,
    NEKO_STRENGTH.BASE_ACCURACY + state.neko.shotMissStreak * NEKO_STRENGTH.MISS_RECOVERY
  );
}

function updateNekoAttention(dt) {
  const attention = state.nekoAttention;
  attention.aimSeconds = playerLane.aim && state.player.action === NEKO_ACTION.HOOP
    ? attention.aimSeconds + dt
    : 0;
  attention.crossThreat = Math.max(0, attention.crossThreat - dt);
  attention.revenge = Math.max(0, attention.revenge - dt);
}

function nekoAttentionContext() {
  const attention = state.nekoAttention;
  const clutch = state.mode === 'timed' && state.remaining <= 10;
  const serious = state.neko.score < state.player.score
    || (clutch && state.neko.score <= state.player.score);
  const playerBallActive = [playerLane.ball, ...playerLane.guests].some(ball => ball.flying && ball.owner === 'player');
  let prankReason = '';
  if (attention.revenge > 0) prankReason = 'revenge';
  else if (attention.crossThreat > 0) prankReason = 'cross';
  else if (state.player.fever > 0) prankReason = 'fever';
  else if (state.player.combo >= NEKO_ATTENTION.COMBO_THREAT) prankReason = 'combo';
  else if (!serious && playerBallActive) prankReason = 'active-shot';
  return {
    mouse:!serious && Boolean(playerLane.aim) && attention.aimSeconds >= NEKO_ATTENTION.AIM_SECONDS,
    prank:Boolean(prankReason),
    prankReason,
    serious
  };
}

function planNekoIntent({ roll, canMouse, canPrank, canShoot, context, pending }) {
  let pendingMouse = Boolean(pending?.mouse);
  let pendingPrank = Boolean(pending?.prank);
  const scheduled = canMouse && roll < .10 ? NEKO_ACTION.MOUSE
    : canPrank && roll < .30 ? NEKO_ACTION.PLAYER
      : NEKO_ACTION.HOOP;
  let action = null;
  let source = '';

  if (scheduled === NEKO_ACTION.MOUSE) {
    if (context.mouse) {
      action = NEKO_ACTION.MOUSE;
      source = 'scheduled';
    } else pendingMouse = true;
  } else if (scheduled === NEKO_ACTION.PLAYER) {
    if (context.prank) {
      action = NEKO_ACTION.PLAYER;
      source = 'scheduled';
    } else pendingPrank = true;
  } else if (pendingPrank && canPrank && context.prank) {
    action = NEKO_ACTION.PLAYER;
    source = 'pending';
    pendingPrank = false;
  } else if (pendingMouse && canMouse && context.mouse) {
    action = NEKO_ACTION.MOUSE;
    source = 'pending';
    pendingMouse = false;
  }

  if (!action && canShoot) {
    action = NEKO_ACTION.HOOP;
    source = 'fallback';
  } else if (!action && canPrank && context.prank) {
    action = NEKO_ACTION.PLAYER;
    source = 'fallback';
  } else if (!action && canMouse && context.mouse) {
    action = NEKO_ACTION.MOUSE;
    source = 'fallback';
  }
  return { action, source, pendingMouse, pendingPrank };
}

function performNekoIntent(action) {
  if (action === NEKO_ACTION.MOUSE) return beginMouseSteal();
  if (action === NEKO_ACTION.PLAYER) return beginNekoPrank();
  if (action !== NEKO_ACTION.HOOP) return false;
  setNekoStatus('opponentAiming');
  reactNeko('aim');
  const acted = shootNekoAtHoop(nekoShotAccuracy());
  if (acted) {
    reactNeko('shoot');
    sound(190, .04, 'triangle');
  }
  return acted;
}

function chooseNekoAction(forcedRoll = null) {
  if (state.nekoAction !== NEKO_ACTION.IDLE || state.nekoCounterPending
      || mouseSteal.active || state.neko.stagger > 0) return false;

  const canMouse = state.mouseStealCount < ACTION_BALANCE.MOUSE_MAX_PER_ROUND
    && state.nextMouseSteal <= 0
    && state.disruptionGrace <= 0
    && hasFocus('neko', ACTION_BALANCE.MOUSE_COST + NEKO_STRENGTH.SHOT_RESERVE);
  const canPrank = state.nextNekoInterference <= 0
    && state.disruptionGrace <= 0
    && hasFocus('neko', ACTION_BALANCE.PRANK_COST + NEKO_STRENGTH.SHOT_RESERVE)
    && nekoPrankInventoryReady()
    && !nekoLane.ball.flying
    && !nekoLane.ball.inTransit;
  const canShoot = Boolean(nekoShotSupply()) && hasFocus('neko', ACTION_BALANCE.SHOT_COST);

  const offenseOverdue = canShoot && state.nekoOffenseIdle >= NEKO_STRENGTH.OFFENSE_DROUGHT;
  let acted = false;
  if (offenseOverdue) {
    acted = performNekoIntent(NEKO_ACTION.HOOP);
  } else {
    // A special slot without a relevant player event becomes a normal shot and
    // leaves one bounded credit for a later, genuinely interactive moment.
    const context = nekoAttentionContext();
    const intent = planNekoIntent({
      roll:forcedRoll ?? Math.random(), canMouse, canPrank, canShoot, context,
      pending:{ mouse:state.nekoAttention.pendingMouse, prank:state.nekoAttention.pendingPrank }
    });
    state.nekoAttention.pendingMouse = intent.pendingMouse;
    state.nekoAttention.pendingPrank = intent.pendingPrank;
    acted = performNekoIntent(intent.action);
    if (!acted && intent.action === NEKO_ACTION.MOUSE) state.nekoAttention.pendingMouse = true;
    if (!acted && intent.action === NEKO_ACTION.PLAYER) state.nekoAttention.pendingPrank = true;
    if (!acted && intent.action !== NEKO_ACTION.HOOP && canShoot) acted = performNekoIntent(NEKO_ACTION.HOOP);
  }

  if (!acted && canMouse) state.nextMouseSteal = randomBetween(1.5, 3);
  if (!acted && canPrank) state.nextNekoInterference = randomBetween(1, 2.5);

  if (acted) {
    const busyBonus = nekoActiveBallCount() >= NEKO_BALL_POOL.MAX_ACTIVE ? NEKO_SHOT_DELAY.BUSY_BONUS : 0;
    state.nextNekoDecision = randomBetween(NEKO_SHOT_DELAY.MIN, NEKO_SHOT_DELAY.MAX) + busyBonus;
    if (state.nekoAction === NEKO_ACTION.IDLE) {
      setTimeout(() => {
        if (state.running && !state.nekoCounterPending && !mouseSteal.active) {
          setNekoStatus(state.neko.fever > 0 ? 'opponentFever' : 'opponentReady');
        }
      }, 450);
    }
  }
  return acted;
}

function update(dt) {
  const rightCourt = stepLayout(nekoLane.canvas, () => nekoLane.canvas.getBoundingClientRect());
  const avatar = stepLayout(nekoAvatar, () => nekoAvatar.getBoundingClientRect());
  sharedCourt.rightOpening = avatarIsReady() ? {
    top:Math.max(0, (avatar.top - rightCourt.top) * nekoLane.height / rightCourt.height),
    bottom:Math.min(nekoLane.height, (avatar.bottom - rightCourt.top) * nekoLane.height / rightCourt.height)
  } : null;
  sharedCourt.advance(dt, state.running);
  playerLane.update(dt, state.running);
  nekoLane.update(dt, state.running);
  if (state.running) sharedCourt.collideBalls();
  checkPlayerBallNekoHit();
  settleTrackedGuestBall();
  if (!state.running) return;
  updateFever('player', dt);
  updateFever('neko', dt);
  recoverFocus('player', dt);
  recoverFocus('neko', dt);
  state.nekoOffenseIdle += dt;
  updateNekoAttention(dt);
  state.nextNekoDecision = Math.max(0, state.nextNekoDecision - dt);
  state.nextNekoInterference = Math.max(0, state.nextNekoInterference - dt);
  state.nextMouseSteal = Math.max(0, state.nextMouseSteal - dt);
  state.disruptionGrace = Math.max(0, state.disruptionGrace - dt);
  if (!nekoAiFrozen && state.nextNekoDecision <= 0) {
    if (!chooseNekoAction()) state.nextNekoDecision = NEKO_SHOT_DELAY.RETURN_RETRY;
    if (state.nextNekoInterference <= 0 && state.nekoCounterPending) {
      state.nextNekoInterference = randomBetween(5.5, 8);
    }
  }
  if (mouseSteal.active) {
    const pull = Math.min(1, dt * 2.2);
    mouseSteal.x += (mouseSteal.targetX - mouseSteal.x) * pull;
    mouseSteal.y += (mouseSteal.targetY - mouseSteal.y) * pull;
    syncMouseStealVisual();
  }
}

function advanceMatchClock(frameSeconds) {
  if (!state.running || state.paused) return;
  const realSeconds = Number.isFinite(frameSeconds) ? Math.max(0, frameSeconds) : 0;
  timerAccumulator += realSeconds;
  if (timerAccumulator >= 1) {
    const elapsed = Math.floor(timerAccumulator);
    timerAccumulator -= elapsed;
    state.elapsed += elapsed;
    if (state.mode === 'timed') {
      state.remaining = Math.max(0, state.remaining - elapsed);
      state.player.time = state.neko.time = state.remaining;
      matchClock.textContent = state.remaining;
    } else {
      const elapsedText = formatElapsed(state.elapsed);
      state.player.time = state.neko.time = elapsedText;
      matchClock.textContent = elapsedText;
    }
    syncHud('player');
    syncHud('neko');
    if (state.mode === 'timed' && !state.remaining) finishMatch();
  }
}

function planPhysicsSteps(frameSeconds) {
  const safeFrameSeconds = Number.isFinite(frameSeconds) ? frameSeconds : 0;
  const simulatedSeconds = clamp(safeFrameSeconds, 0, MAX_PHYSICS_FRAME_DELTA_SECONDS);
  if (simulatedSeconds <= 0) return { steps:0, stepSeconds:0 };
  const steps = Math.min(
    MAX_PHYSICS_STEPS_PER_FRAME,
    Math.ceil(simulatedSeconds / MAX_PHYSICS_STEP_SECONDS)
  );
  return { steps, stepSeconds:simulatedSeconds / steps };
}

function playablePhysicsSeconds(frameSeconds) {
  const safeFrameSeconds = Number.isFinite(frameSeconds) ? Math.max(0, frameSeconds) : 0;
  if (!state.running || state.mode !== 'timed') return safeFrameSeconds;
  return Math.min(safeFrameSeconds, Math.max(0, state.remaining - timerAccumulator));
}

function frame(now) {
  const frameSeconds = lastFrame ? (now - lastFrame) / 1000 : 0;
  lastFrame = now;
  const plan = state.paused ? { steps:0, stepSeconds:0 } : planPhysicsSteps(playablePhysicsSeconds(frameSeconds));
  stepLayoutCache = new Map();
  try {
    for (let step = 0; step < plan.steps; step += 1) update(plan.stepSeconds);
  } finally {
    stepLayoutCache = null;
  }
  syncTrackedGuestBall();
  syncOutsideBallOverlays();
  const opening = sharedCourt.rightOpening;
  const rightScreen = nekoLane.canvas.closest('.machine-screen');
  rightScreen.style.setProperty('--shared-opening-top', `${opening ? opening.top / nekoLane.height * 100 : 100}%`);
  rightScreen.style.setProperty('--shared-opening-bottom', `${opening ? opening.bottom / nekoLane.height * 100 : 100}%`);
  flushFrameSync();
  advanceMatchClock(frameSeconds);
  const aimAllowed = hasFocus('player', ACTION_BALANCE.SHOT_COST);
  syncShotHint(aimAllowed);
  playerLane.draw(aimAllowed);
  nekoLane.draw(aimAllowed);
  requestAnimationFrame(frame);
}

// rAF stops while the page is hidden (background tab, minimized window). Drop
// that gap instead of feeding it to the wall-time match clock, which would
// otherwise settle a timed match the moment the page is shown again.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) lastFrame = 0;
});

function resize() {
  const trackingPlayerNative = trackedGuestBall === playerLane.ball;
  playerLane.resize();
  nekoLane.resize();
  sharedCourt.reprojectNonFlyingBalls();
  if (trackingPlayerNative && trackedGuestSuspended) trackedCrossTransit?.restart();
  else if (trackingPlayerNative) trackGuestBall(playerLane.ball, playerLane);
  syncTrackedGuestBall();
}
window.addEventListener('resize', resize);
startButton.addEventListener('click', resetMatch);
stopMatchButton.addEventListener('click', finishMatch);
function setMatchPaused(paused) {
  if (!state.running || state.paused === paused) return;
  cancelPlayerAction();
  state.paused = paused;
  document.body.classList.toggle('is-match-paused', paused);
  if (paused) {
    matchTimers.pause();
    mouseSteal.pausedAt = performance.now();
  } else {
    if (mouseSteal.active) mouseSteal.startedAt += performance.now() - mouseSteal.pausedAt;
    matchTimers.resume();
  }
  lastFrame = 0;
  pauseOverlay.hidden = !paused;
  pauseMatchButton.textContent = t(paused ? 'resume' : 'pause');
  pauseMatchButton.setAttribute('aria-pressed', String(paused));
}
pauseMatchButton.addEventListener('click', () => setMatchPaused(!state.paused));
byId('resume-match').addEventListener('click', () => setMatchPaused(false));
document.querySelectorAll('input[name="match-mode"]').forEach(input => {
  input.addEventListener('change', () => {
    renderStartCopy();
  });
});
soundToggle.addEventListener('click', () => {
  state.sound = !state.sound;
  soundToggle.textContent = state.sound ? '♪' : '×';
  updateSoundToggleLabel();
});

function updateSoundToggleLabel() {
  const key = state.sound ? 'soundOff' : 'soundOn';
  const translated = t(key);
  const fallback = state.sound ? soundToggle.dataset.soundOffFallback : soundToggle.dataset.soundOnFallback;
  soundToggle.setAttribute('aria-label', translated === key ? fallback : translated);
}

function showSdkUnavailable() {
  startButton.disabled = true;
  startCopy.textContent = t('sdkUnavailable');
  startCopy.setAttribute('role', 'alert');
  document.querySelectorAll('input[name="match-mode"]').forEach(input => { input.disabled = true; });
}

updateSoundToggleLabel();
applyOpponentName(opponentName);
renderLocalizedState();
setAvatarUnavailableLabel(t('avatarUnavailable'));
window.addEventListener('localechange', () => {
  renderLocalizedState();
  updateSoundToggleLabel();
  setAvatarUnavailableLabel(t('avatarUnavailable'));
});
window.addEventListener('pageshow', event => {
  if (event?.persisted) window.location.reload();
});
if (sdkContext) {
  prewarmNekoVoice(opponentName);
  void configureGameRuntime(
    () => ({ currentState:runtimeSnapshot() }),
    context => runtimeEndPayload(context?.type || 'page-exit')
  ).catch(error => console.warn('[air_basketball] SDK runtime configuration failed', error))
    // configure() registers the SDK's page-exit handler, which beacons the route
    // end before disposing. Register this cleanup after it (pagehide listeners run
    // in order), and whether or not configuration succeeded.
    .finally(() => window.addEventListener('pagehide', disposeGameSdk, { once:true }));
  void initNekoAvatar(sdkGame, sdkIdentity, identity => {
    applyOpponentName(identity?.name);
    prewarmNekoVoice(identity?.name);
    nekoLane.showAvatarPlaceholder = false;
  });
} else {
  showSdkUnavailable();
}

function prepareIsolatedCrossTest() {
  resetMatch();
  nekoAiFrozen = true;
  playerLane.clearGuests();
  nekoLane.clearGuests();
  for (const ball of nekoLane.ballPool) Object.assign(ball, { owner:null, flying:false, inTransit:true });
}

function setTrackedGuestForTest({ xRatio, yRatio, vx, vy, owner } = {}) {
  const ball = trackedGuestBall;
  if (!ball || trackedGuestLane !== nekoLane || trackedGuestSuspended) return false;
  if (Number.isFinite(xRatio)) ball.x = nekoLane.width * xRatio;
  if (Number.isFinite(yRatio)) ball.y = nekoLane.height * yRatio;
  if (Number.isFinite(vx)) ball.vx = vx;
  if (Number.isFinite(vy)) ball.vy = vy;
  if (owner === 'player' || owner === 'neko') ball.owner = owner;
  Object.assign(ball, { flying:true, expired:false, allowOuterExit:true });
  syncTrackedGuestBall();
  return true;
}

// Automation hooks drive Neko's AI and restart matches past the focus/cooldown
// rules, so they exist only on test_mode=1 pages.
if (pageParams.get('test_mode') === '1') window.AirBasketballMVP = Object.freeze({
  start:resetMatch,
  getState:() => ({
    ...JSON.parse(JSON.stringify(state)),
    ballPools:{ player:playerLane.poolSnapshot(), neko:nekoLane.poolSnapshot() },
    playerBallFlying:playerLane.ballPool.some(ball => ball.flying),
    playerBallMotion:playerLane.ball.flying && !playerLane.ball.inTransit ? {
      x:playerLane.ball.x,
      y:playerLane.ball.y,
      r:playerLane.ball.r,
      vx:playerLane.ball.vx,
      vy:playerLane.ball.vy,
      owner:playerLane.ball.owner
    } : null,
    playerReadyBallOwner:playerLane.ball.owner,
    nekoBallFlying:nekoLane.ball.flying || nekoLane.guests.some(ball => !ball.expired && ball.owner === 'neko'),
    nekoReadyBallFlying:nekoLane.ball.flying,
    playerBallInTransit:playerLane.ball.inTransit,
    nekoBallInTransit:nekoLane.ball.inTransit,
    playerGuestBalls:playerLane.guests.length,
    nekoGuestBalls:nekoLane.guests.length,
    nekoNativeShotBalls:nekoLane.countActiveGuestBalls({ nativeShot:true }),
    nekoActiveBalls:nekoActiveBallCount(),
    nekoActiveBreakdown:{
      playerLane:playerLane.countActiveBalls({ owner:'neko' }),
      nekoLane:nekoLane.countActiveBalls({ owner:'neko' }),
      transit:activeAuxiliaryBallCount('neko')
    },
    nekoBallPool:{ ...NEKO_BALL_POOL },
    actionBalance:{ ...ACTION_BALANCE },
    nekoAccuracy:nekoShotAccuracy(),
    sharedCourt:{
      fixedHoop:sharedCourt.fixedHoop,
      width:sharedCourt.width,
      playerHoop:playerLane.getHoopPose(),
      nekoHoop:nekoLane.getHoopPose(),
      rightOpening:sharedCourt.rightOpening
    },
    avatarReady:avatarIsReady(),
    trackedGuestSuspended,
    crossBallInteractive:crossBall.classList.contains('is-interactive'),
    playerGuestMotion:playerLane.getGuestMotion('neko') || playerLane.getGuestMotion(),
    nekoGuestMotion:nekoLane.getGuestMotion('player') || nekoLane.getGuestMotion()
  }),
  shootPlayer,
  shootNeko:() => shootNekoAtHoop(nekoShotAccuracy()),
  prankNeko:beginNekoPrank,
  stealMouse:beginMouseSteal,
  releaseMouse:() => endMouseSteal(true),
  test:Object.freeze({
    prepareIsolatedCrossTest,
    setTrackedGuestForTest,
    advanceMatchClock,
    planPhysicsSteps,
    playablePhysicsSeconds,
    runtimeSnapshot,
    planNekoIntent,
    nekoAttentionContext
  })
});

requestAnimationFrame(frame);
