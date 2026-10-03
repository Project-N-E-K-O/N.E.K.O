const container = document.getElementById('air-neko-avatar');
const loading = document.getElementById('neko-avatar-loading');
const fallback = document.getElementById('air-neko-avatar-fallback');
const fallbackName = document.getElementById('air-neko-avatar-fallback-name');
const fallbackType = document.getElementById('air-neko-avatar-fallback-type');

let reactionTimer = 0;
let avatarController = null;
let avatarMountPromise = Promise.resolve();
// The fallback line is `<MODEL> · <label>`; keep both parts so a language switch
// re-renders it instead of prefixing the already-composed text again.
let fallbackModelLabel = '';
let fallbackUnavailableLabel = fallbackType?.textContent || '';

function hideRenderers() {
  for (const element of document.querySelectorAll('#air-neko-avatar .avatar-renderer')) element.hidden = true;
  if (fallback) fallback.hidden = true;
}

function markReady() {
  container?.classList.add('is-ready');
  if (container) container.dataset.avatarReady = 'true';
  loading?.classList.add('is-hidden');
}

function showCurrentCharacterFallback(identity) {
  hideRenderers();
  if (container) container.dataset.renderer = 'placeholder';
  if (fallback) fallback.hidden = false;
  if (fallbackName) fallbackName.textContent = identity?.name || 'N.E.K.O';
  const type = identity?.live3dSubType || identity?.modelType || 'avatar';
  fallbackModelLabel = String(type).toUpperCase();
  renderFallbackType();
  markReady();
}

function renderFallbackType() {
  if (fallbackType && fallbackModelLabel) {
    fallbackType.textContent = `${fallbackModelLabel} · ${fallbackUnavailableLabel}`;
  }
}

export function setAvatarUnavailableLabel(label) {
  if (label) fallbackUnavailableLabel = label;
  renderFallbackType();
}

// Settles (never rejects) once the opponent Avatar mount has finished either way.
export function avatarMountSettled() {
  return avatarMountPromise.then(() => undefined, () => undefined);
}

export async function initNekoAvatar(game, identity, onReady) {
  if (!container) return false;
  onReady?.(identity);
  if (!identity?.model) {
    showCurrentCharacterFallback(identity);
    return false;
  }
  try {
    avatarMountPromise = game.avatar.mount({
      slot:'opponent',
      characterName:identity.name,
      model:identity.model,
      viewport:{ mode:'container' },
      fit:{ mode:'contain', align:'bottom-center', padding:4, scaleMultiplier:1 },
      resize:{ mode:'container' }
    });
    avatarController = await avatarMountPromise;
    markReady();
    return true;
  } catch (error) {
    console.warn(`[air_basketball] ${identity.name} ${identity.renderer} failed to load`, error);
    showCurrentCharacterFallback(identity);
    return false;
  }
}

// Expressions are cosmetic. A busy queue, a disposed controller or a renderer
// error must not surface as an unhandled rejection on every shot.
function setAvatarEmotion(name) {
  try {
    void Promise.resolve(avatarController?.setEmotion(name)).catch(() => undefined);
  } catch (_) { /* synchronous controller failure is equally cosmetic */ }
}

function reactionEmotion(type, enabled) {
  if (!enabled) return 'relaxed';
  if (type === 'score') return 'happy';
  if (type === 'hit') return 'surprised';
  return 'relaxed';
}

export function reactNeko(type, direction = 1) {
  if (!container?.classList.contains('is-ready')) return;
  clearTimeout(reactionTimer);
  container.classList.remove('is-aiming', 'is-shooting', 'is-celebrating', 'is-hit', 'is-stealing');
  void container.offsetWidth;
  const className = type === 'shoot' ? 'is-shooting'
    : type === 'score' ? 'is-celebrating'
      : type === 'hit' ? 'is-hit'
        : type === 'steal' ? 'is-stealing' : 'is-aiming';
  if (type === 'hit') {
    const sign = direction < 0 ? -1 : 1;
    container.style.setProperty('--hit-shift', `${sign * 13}%`);
    container.style.setProperty('--hit-return', `${sign * -5}%`);
    container.style.setProperty('--hit-settle', `${sign * 2}%`);
    container.style.setProperty('--hit-angle', `${sign * 6}deg`);
    container.style.setProperty('--hit-angle-back', `${sign * -3}deg`);
    container.style.setProperty('--hit-angle-settle', `${sign}deg`);
  }
  container.classList.add(className);
  setAvatarEmotion(reactionEmotion(type, true));
  reactionTimer = setTimeout(() => {
    container.classList.remove(className);
    setAvatarEmotion(reactionEmotion(type, false));
  }, type === 'aim' ? 420 : type === 'hit' ? 520 : type === 'steal' ? 760 : 680);
}
