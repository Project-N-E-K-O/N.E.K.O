const assetVersion = new URL(import.meta.url).search;
const { createAirBasketballAvatarHost } = await import(`./avatar-host.js${assetVersion}`);

// Trusted same-origin adapter. The transport and host helpers stay private to
// this module; game.js receives only public SDK clients/controllers.
const GAME_ID = 'air-basketball';
const GAME_VERSION = '1.0.0';
const pageParams = new URLSearchParams(window.location.search);
const toneObjectUrls = new Set();
let sdkContext = null;

function toneKey(frequency, duration, type) {
  return `${Math.round(frequency)}-${Math.round(duration * 1000)}-${type}`;
}

function toneBlobUrl(frequency, duration, type) {
  const sampleRate = 8000;
  const sampleCount = Math.max(1, Math.round(sampleRate * duration));
  const bytes = new Uint8Array(44 + sampleCount * 2);
  const view = new DataView(bytes.buffer);
  const write = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, 'RIFF');
  view.setUint32(4, 36 + sampleCount * 2, true);
  write(8, 'WAVE');
  write(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, sampleCount * 2, true);
  for (let index = 0; index < sampleCount; index += 1) {
    const phase = index / sampleRate * frequency;
    const wave = type === 'square'
      ? (phase % 1 < .5 ? 1 : -1)
      : type === 'triangle'
        ? 1 - 4 * Math.abs(Math.round(phase) - phase)
        : Math.sin(phase * Math.PI * 2);
    const envelope = Math.max(0, 1 - index / sampleCount);
    view.setInt16(44 + index * 2, Math.round(wave * envelope * 2600), true);
  }
  const url = URL.createObjectURL(new Blob([bytes], { type:'audio/wav' }));
  toneObjectUrls.add(url);
  return url;
}

const toneSpecs = [
  [310,.06,'triangle'], [330,.08,'square'], [920,.24,'triangle'],
  [980,.24,'sine'], [760,.14,'sine'], [540,.14,'sine'],
  [150,.05,'triangle'], [210,.04,'triangle'], [125,.05,'triangle'],
  [680,.1,'triangle'], [210,.08,'square'], [440,.08,'triangle'],
  [76,.13,'square'], [105,.08,'square'], [420,.05,'triangle'],
  [115,.07,'square'], [640,.25,'triangle'], [260,.25,'triangle'],
  [190,.04,'triangle']
];

// avatar-host.js only implements these renderers.
const SUPPORTED_AVATAR_TYPES = new Set(['live2d', 'vrm']);

function resolveIdentity(character) {
  const name = String(character?.name || pageParams.get('lanlan_name') || 'N.E.K.O').trim() || 'N.E.K.O';
  const primary = character?.rendererAvailable ? character.model : null;
  const model = [primary, ...(character?.fallbackModels || [])]
    .find(candidate => SUPPORTED_AVATAR_TYPES.has(candidate?.type) && candidate.path);
  if (model) {
    return { name, renderer:model.type, modelType:model.type, model:{ type:model.type, path:String(model.path) } };
  }
  return { name, renderer:'unavailable', modelType:character?.model?.type || 'unavailable', model:null };
}

async function bootstrap() {
  if (!window.NekoMiniGame?.connect) throw new Error('NekoMiniGame SDK is unavailable');
  if (!window.NekoMiniGameAudioHost?.create) throw new Error('NekoMiniGame audio host is unavailable');
  const createHost = await window.nekoMiniGameSameOriginHostReady;
  // Consumed by air-basketball-neko-host-registration.js when the host is created.
  window.createAirBasketballAvatarHost = createAirBasketballAvatarHost;
  const audioHost = window.NekoMiniGameAudioHost.create({
    AudioSystem:window.NekoGameSystem?.GameAudioSystem,
    maxControllers:1
  });
  const transport = createHost({
    gameType:GAME_ID,
    gameVersion:GAME_VERSION,
    sessionId:String(pageParams.get('session_id') || '').trim(),
    source:'air_basketball',
    displayName:'Air Basketball',
    audioHost
  });
  const game = await window.NekoMiniGame.connect({
    id:GAME_ID,
    version:GAME_VERSION,
    protocolVersion:'1',
    requiredCapabilities:['runtime', 'logging', 'avatar-renderer', 'audio', 'speech-output']
  }, { transport });
  const requestedName = String(pageParams.get('lanlan_name') || '').trim();
  const character = await game.runtime.bindCharacter(requestedName || undefined);
  if (!character) throw new Error(`Character is unavailable (${requestedName || 'current'})`);
  const identity = resolveIdentity(character);
  const sfx = Object.fromEntries(toneSpecs.map(spec => {
    const [frequency, duration, type] = spec;
    return [toneKey(frequency, duration, type), [toneBlobUrl(frequency, duration, type)]];
  }));
  const audio = await game.audio.mount({
    slot:'main',
    resources:{ sfx },
    settings:{ maxConcurrent:12, maxPreloadEntries:32 }
  });
  Object.keys(sfx).forEach(key => audio.preloadSfx(key));
  sdkContext = Object.freeze({ game, identity, audio, transport });
  return sdkContext;
}

export const airBasketballSdkReady = bootstrap();
// game.js reports the failure on the start card; keep it out of unhandled rejections.
airBasketballSdkReady.catch(() => undefined);

export async function playGameTone(frequency, duration = .08, type = 'sine') {
  const { audio } = await airBasketballSdkReady;
  return audio.playSfx(toneKey(frequency, duration, type));
}

export async function unlockGameAudio() {
  const { audio } = await airBasketballSdkReady;
  return audio.unlock();
}

export async function preloadNekoSpeech(lines) {
  const { game } = await airBasketballSdkReady;
  return game.speech.preload(lines, { language:document.documentElement.lang || navigator.language });
}

export async function speakNekoSpeech(request) {
  const { game } = await airBasketballSdkReady;
  return game.speech.speak(request);
}

let lifecycleTail = Promise.resolve();
function enqueueLifecycle(operation) {
  lifecycleTail = lifecycleTail.catch(() => undefined).then(async () => operation(await airBasketballSdkReady));
  return lifecycleTail;
}

export async function configureGameRuntime(payload, pageExitPayload) {
  const { game } = await airBasketballSdkReady;
  game.runtime.configure({
    payload,
    heartbeat:{ intervalMs:2500, timeoutMs:4500 },
    outputs:{ intervalMs:700, timeoutMs:8000, limit:50 },
    pageExit:{ payload:pageExitPayload }
  });
}

export function startGameRuntime(payload) {
  return enqueueLifecycle(async ({ game, identity, transport }) => {
    if (['ended', 'inactive'].includes(game.runtime.state)) {
      game.runtime.reset({ newSession:true });
      // reset() clears the host's bound character. Public bindCharacter() would
      // require unmounting the Avatar first, so the trusted adapter restores the
      // already-validated identity on its private transport instead.
      transport.bindRuntimeCharacter(identity.name);
    }
    const result = await game.runtime.start(payload);
    await game.logger.enableAfterRuntimeStart();
    return result;
  });
}

export function endGameRuntime(payload, options = {}) {
  return enqueueLifecycle(async ({ game }) => {
    await Promise.resolve(options.after).catch(() => undefined);
    if (!['running', 'degraded', 'starting'].includes(game.runtime.state)) return undefined;
    return game.runtime.end(payload);
  });
}

export function disposeGameSdk() {
  if (!sdkContext) return;
  const { game } = sdkContext;
  sdkContext = null;
  game.dispose();
  toneObjectUrls.forEach(url => URL.revokeObjectURL(url));
  toneObjectUrls.clear();
}
