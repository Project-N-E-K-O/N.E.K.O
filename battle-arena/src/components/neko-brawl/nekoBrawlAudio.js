// 当前 BGM / SFX 均为暂时占位实装用声音，不是最终结果。
// 如果后续替换为正式版音效或正式版 BGM，请在对应常量或场景旁明确注明“正式版音效”或“正式版 BGM”。
export const NEKO_BRAWL_AUDIO = {
  BGM_HOME: '/neko-brawl/audio/bgm_home_brightlands_night.mp3',
  BGM_HOME_PREVIOUS: '/neko-brawl/audio/bgm_home_loop.mp3',
  BGM_HOME_BRIGHTLANDS_NIGHT: '/neko-brawl/audio/bgm_home_brightlands_night.mp3',
  BGM_DECK_BUILDER: '/neko-brawl/audio/bgm_deck_builder_loop.mp3',
  BGM_DECK_LIBRARY: '/neko-brawl/audio/bgm_deck_library_loop.mp3',
  BGM_ANSWER_QUICKLY: '/neko-brawl/audio/bgm_answer_quickly.mp3',
  BGM_BATTLE: '/neko-brawl/audio/bgm_battle_loop.mp3',
  SFX_CARD_ATTACK: '/neko-brawl/audio/sfx_card_attack.mp3',
  SFX_CARD_HEAL: '/neko-brawl/audio/sfx_card_heal.mp3',
  SFX_CARD_SHIELD: '/neko-brawl/audio/sfx_card_shield.wav',
  SFX_CARD_DRAW: '/neko-brawl/audio/sfx_card_draw.wav',
  SFX_CARD_SUPPORT: '/neko-brawl/audio/sfx_card_support.wav',
  SFX_CARD_COMBO: '/neko-brawl/audio/sfx_card_combo.wav',
}

export const NEKO_BRAWL_BGM_OPTIONS = {
  homePrevious: {
    label: 'Home Previous',
    src: NEKO_BRAWL_AUDIO.BGM_HOME_PREVIOUS,
  },
  homeBrightlandsNight: {
    label: '1.16. ブライトランド地方 -夜-',
    src: NEKO_BRAWL_AUDIO.BGM_HOME_BRIGHTLANDS_NIGHT,
  },
  deckBuilderDefault: {
    label: 'Deck Builder Default',
    src: NEKO_BRAWL_AUDIO.BGM_DECK_BUILDER,
  },
  deckLibraryDefault: {
    label: 'Deck Library Default',
    src: NEKO_BRAWL_AUDIO.BGM_DECK_LIBRARY,
  },
  answerQuickly: {
    label: 'Answer Quickly',
    src: NEKO_BRAWL_AUDIO.BGM_ANSWER_QUICKLY,
  },
  battleDefault: {
    label: 'Battle Default',
    src: NEKO_BRAWL_AUDIO.BGM_BATTLE,
  },
}

export const NEKO_BRAWL_BGM_SCENES = {
  // 暂时占位 BGM 场景配置；替换正式音乐时请注明“正式版 BGM”。
  home: {
    src: NEKO_BRAWL_AUDIO.BGM_HOME,
    volume: 0.28,
  },
  deckBuilder: {
    src: NEKO_BRAWL_AUDIO.BGM_ANSWER_QUICKLY,
    volume: 0.34,
  },
  deckLibrary: {
    src: NEKO_BRAWL_AUDIO.BGM_ANSWER_QUICKLY,
    volume: 0.32,
  },
  battle: {
    src: NEKO_BRAWL_AUDIO.BGM_BATTLE,
    volume: 0.34,
  },
}

const audioCache = new Map()

let currentBgm = null
let currentBgmSrc = ''
let currentBgmVolume = 0.38
let pendingBgmRetry = null
let pendingBgmDelayTimer = 0
let unlockBgmHandler = null

const SFX_VOLUME = 0.72
// 暂时占位 SFX 映射；替换正式音效时请注明“正式版音效”。
const CARD_SFX_BY_TARGET = {
  boss: { src: NEKO_BRAWL_AUDIO.SFX_CARD_ATTACK, volume: 0.72 },
  heal: { src: NEKO_BRAWL_AUDIO.SFX_CARD_HEAL, volume: 0.68 },
  shield: { src: NEKO_BRAWL_AUDIO.SFX_CARD_SHIELD, volume: 0.64 },
  draw: { src: NEKO_BRAWL_AUDIO.SFX_CARD_DRAW, volume: 0.58 },
  support: { src: NEKO_BRAWL_AUDIO.SFX_CARD_SUPPORT, volume: 0.56 },
}
const BGM_FADE_STEP_MS = 40
const BGM_FADE_STEP = 0.04
const DEFAULT_BGM_VOLUME = 0.38
const BGM_UNLOCK_EVENTS = ['pointerdown', 'click', 'keydown', 'touchstart']

function getAudio(src) {
  if (typeof window === 'undefined') return null
  if (!audioCache.has(src)) {
    const audio = new Audio(src)
    audio.preload = 'none'
    audioCache.set(src, audio)
  }
  return audioCache.get(src)
}

function clearBgmStartDelay() {
  if (typeof window === 'undefined' || !pendingBgmDelayTimer) return
  window.clearTimeout(pendingBgmDelayTimer)
  pendingBgmDelayTimer = 0
}

function clearBgmUnlockRetry() {
  if (typeof window === 'undefined' || !unlockBgmHandler) return
  BGM_UNLOCK_EVENTS.forEach(eventName => {
    window.removeEventListener(eventName, unlockBgmHandler)
  })
  unlockBgmHandler = null
  pendingBgmRetry = null
}

function armBgmUnlockRetry(src, options) {
  if (typeof window === 'undefined') return
  pendingBgmRetry = { src, options }
  if (unlockBgmHandler) return

  unlockBgmHandler = () => {
    const retry = pendingBgmRetry
    clearBgmUnlockRetry()
    if (retry) playNekoBrawlBgm(retry.src, retry.options)
  }
  BGM_UNLOCK_EVENTS.forEach(eventName => {
    window.addEventListener(eventName, unlockBgmHandler, { once: true })
  })
}

function fadeIn(audio, targetVolume) {
  audio.volume = 0
  const timer = window.setInterval(() => {
    if (!audio || audio.paused) {
      window.clearInterval(timer)
      return
    }
    audio.volume = Math.min(targetVolume, audio.volume + BGM_FADE_STEP)
    if (audio.volume >= targetVolume) window.clearInterval(timer)
  }, BGM_FADE_STEP_MS)
}

export function playNekoBrawlBgm(src = NEKO_BRAWL_AUDIO.BGM_DECK_BUILDER, options = {}) {
  clearBgmStartDelay()
  if (options.delayMs > 0) {
    pendingBgmDelayTimer = window.setTimeout(() => {
      pendingBgmDelayTimer = 0
      playNekoBrawlBgm(src, { ...options, delayMs: 0 })
    }, options.delayMs)
    return
  }

  const audio = getAudio(src)
  if (!audio) return
  const targetVolume = options.volume ?? DEFAULT_BGM_VOLUME

  if (currentBgm && currentBgmSrc === src) {
    currentBgmVolume = targetVolume
    if (!currentBgm.paused) {
      currentBgm.volume = targetVolume
      return
    }
    currentBgm.play()
      .then(() => fadeIn(currentBgm, targetVolume))
      .catch(() => armBgmUnlockRetry(src, options))
    return
  }

  stopNekoBrawlBgm()

  currentBgm = audio
  currentBgmSrc = src
  currentBgmVolume = targetVolume
  currentBgm.loop = true
  currentBgm.currentTime = 0

  // BGM is flow-driven and has no user-facing controls. Browser autoplay may
  // block the initial page BGM; when that happens, the latest requested scene is
  // retried on the next pointer/key interaction.
  currentBgm.play()
    .then(() => fadeIn(audio, targetVolume))
    .catch(() => armBgmUnlockRetry(src, options))
}

export function playNekoBrawlSceneBgm(sceneName) {
  const scene = NEKO_BRAWL_BGM_SCENES[sceneName]
  if (!scene) return
  playNekoBrawlBgm(scene.src, { volume: scene.volume, delayMs: scene.delayMs || 0 })
}

export function stopNekoBrawlBgm() {
  clearBgmStartDelay()
  clearBgmUnlockRetry()
  if (!currentBgm) return
  currentBgm.pause()
  currentBgm.currentTime = 0
  currentBgm.volume = currentBgmVolume
  currentBgm = null
  currentBgmSrc = ''
  currentBgmVolume = DEFAULT_BGM_VOLUME
}

export function playNekoBrawlSfx(src, options = {}) {
  const baseAudio = getAudio(src)
  if (!baseAudio) return

  const audio = baseAudio.cloneNode()
  audio.volume = options.volume ?? SFX_VOLUME
  audio.playbackRate = options.playbackRate ?? 1
  audio.play().catch(() => {})
}

export function playNekoBrawlCardSfx(effectTarget, options = {}) {
  // 当前播放的是暂时占位实装用卡牌音效，不是最终结果；正式替换后请注明“正式版音效”。
  const sfx = CARD_SFX_BY_TARGET[effectTarget] || CARD_SFX_BY_TARGET.support
  playNekoBrawlSfx(sfx.src, {
    volume: options.volume ?? sfx.volume,
    playbackRate: options.playbackRate ?? 1,
  })

  if (options.comboActive && typeof window !== 'undefined') {
    window.setTimeout(() => {
      playNekoBrawlSfx(NEKO_BRAWL_AUDIO.SFX_CARD_COMBO, { volume: options.comboVolume ?? 0.54 })
    }, options.comboDelayMs ?? 90)
  }
}
