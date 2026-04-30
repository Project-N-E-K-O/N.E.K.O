import defaultGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/default-ghost-cursor.png'
import clickGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/click-ghost-cursor.png'
import leftCatEarUrl from '../../../static/assets/tutorial/highlight/left-cat-ear.png'
import rightCatEarUrl from '../../../static/assets/tutorial/highlight/right-cat-ear.png'
import catPawUrl from '../../../static/assets/tutorial/highlight/cat-paw.png'
import sendIconUrl from '../../../static/icons/send_icon.png'
import pawUiUrl from '../../../static/icons/paw_ui.png'
import { getLocale } from './i18n'
import router from './router'

const START_EVENT = 'neko:yui-guide:plugin-dashboard:start'
const READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready'
const DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done'
const TERMINATE_EVENT = 'neko:yui-guide:plugin-dashboard:terminate'
const NARRATION_FINISHED_EVENT = 'neko:yui-guide:plugin-dashboard:narration-finished'
const INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request'
const INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack'
const SKIP_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:skip-request'
const HANDOFF_STORAGE_KEY = 'neko_yui_guide_handoff_token'
const HANDOFF_TOKEN_VERSION = 1
const PREACTIVATE_CLEANUP_MS = 8000
const GUIDE_AUDIO_BASE_URL = '/static/assets/tutorial/guide-audio/'
const DEFAULT_GUIDE_LOCALE = 'zh'
const DEFAULT_INTERRUPT_DISTANCE = 32
const DEFAULT_INTERRUPT_SPEED_THRESHOLD = 1.8
const DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD = 0.09
const DEFAULT_INTERRUPT_ACCELERATION_STREAK = 3
const DEFAULT_INTERRUPT_THROTTLE_MS = 500
const SCRIPTED_MOTION_INTERRUPT_STREAK = 2
const SCRIPTED_MOTION_INTERRUPT_WINDOW_MS = 220
const DEFAULT_PASSIVE_RESISTANCE_DISTANCE = 10
const DEFAULT_PASSIVE_RESISTANCE_SPEED_THRESHOLD = 0.2
const DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS = 140
const DEFAULT_RESISTANCE_CURSOR_REVEAL_MS = 3000
const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420
const CURSOR_CLICK_STAR_COUNT = 7
const CURSOR_CLICK_STAR_LIFETIME_MS = 760
const CURSOR_TRAIL_PARTICLE_LIFETIME_MS = 300
const CURSOR_TRAIL_MIN_DISTANCE = 3
const CURSOR_TRAIL_MIN_INTERVAL_MS = 8
const CURSOR_TRAIL_SEGMENT_SPACING = 9
const CURSOR_TRAIL_MAX_SEGMENTS_PER_FRAME = 6
const CURSOR_TRAIL_MAX_POINTS = 34
const CURSOR_TRAIL_MAX_PARTICLES = 12
const CURSOR_TRAIL_ICON_CHANCE = 0.045
const CURSOR_TRAIL_BODY_HEAD_WIDTH = 34
const CURSOR_TRAIL_BODY_TAIL_WIDTH = 3
const CURSOR_TRAIL_CORE_HEAD_WIDTH = 14
const CURSOR_TRAIL_CORE_TAIL_WIDTH = 1.5
const CURSOR_TRAIL_HEAD_RADIUS = 15
const CURSOR_TRAIL_ICON_URLS = [sendIconUrl, pawUiUrl] as const
const PLUGIN_DASHBOARD_MOVE_TO_MAIN_MS = 780
const PLUGIN_DASHBOARD_SCROLL_PHASE_MS = 2000
// Negative values mean inward/inset padding for the plugin-main spotlight.
const PLUGIN_MAIN_SPOTLIGHT_INSET = -25
const PLUGIN_DASHBOARD_DEFAULT_TOTAL_MS = 9000
const MIN_SPOTLIGHT_RADIUS = 4
const RESISTANCE_LINES = [
  '喂！不要拽我啦，还没轮到你的回合呢！',
  '等一下啦！还没结束呢，不要随便打断我啦！',
] as const
const RESISTANCE_VOICE_KEYS = [
  'interrupt_resist_light_1',
  'interrupt_resist_light_3',
] as const
const ANGRY_EXIT_LINE = '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！'
const GUIDE_AUDIO_FILE_NAMES = {
  takeover_plugin_preview_dashboard: '有了它们，我不光能看.mp3',
  interrupt_resist_light_1: '喂！不要拽我啦，还没.mp3',
  interrupt_resist_light_3: '等一下啦！还没结束呢.mp3',
  interrupt_angry_exit: '人类~~~~！你真的.mp3',
} as const
const GUIDE_AUDIO_BY_KEY = {
  takeover_plugin_preview_dashboard: {
    zh: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
    en: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
    ja: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
    ko: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
    ru: GUIDE_AUDIO_FILE_NAMES.takeover_plugin_preview_dashboard,
  },
  interrupt_resist_light_1: {
    zh: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
    en: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
    ja: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
    ko: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
    ru: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_1,
  },
  interrupt_resist_light_3: {
    zh: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
    en: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
    ja: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
    ko: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
    ru: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_3,
  },
  interrupt_angry_exit: {
    zh: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
    en: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
    ja: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
    ko: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
    ru: GUIDE_AUDIO_FILE_NAMES.interrupt_angry_exit,
  },
} as const

const LOCAL_TUTORIAL_ACTION_EVENT = 'neko:plugin-tutorial:action'

export type PluginDashboardLocalTutorialMotion = 'point' | 'click' | 'ellipse'

export type PluginDashboardLocalTutorialStep = {
  targetId: string
  title: string
  body: string
  route?: string
  motion?: PluginDashboardLocalTutorialMotion
  action?: string
  waitMs?: number
  allowMissing?: boolean
  durationMs?: number
}

type StartPluginDashboardTutorialOptions = {
  steps: PluginDashboardLocalTutorialStep[]
  labels?: {
    skip?: string
    keyboardHint?: string
  }
}

function normalizeOrigin(value: string) {
  const normalizedValue = String(value || '').trim()
  if (!normalizedValue) {
    return ''
  }

  try {
    return new URL(normalizedValue).origin
  } catch {
    return ''
  }
}

const DEFAULT_OPENER_ORIGIN = normalizeOrigin(import.meta.env.VITE_YUI_TUTORIAL_OPENER_ORIGIN || '')
const DEFAULT_LOCAL_OPENER_ORIGINS = [
  'http://127.0.0.1:48911',
  'http://localhost:48911',
  'https://127.0.0.1:48912',
  'https://localhost:48912',
] as const
const ALLOWED_OPENER_ORIGINS = new Set(
  [
    import.meta.env.VITE_YUI_TUTORIAL_ALLOWED_OPENER_ORIGINS || '',
    DEFAULT_OPENER_ORIGIN,
    ...DEFAULT_LOCAL_OPENER_ORIGINS,
  ]
    .flatMap((value) => String(value || '').split(','))
    .map((value) => normalizeOrigin(value))
    .filter(Boolean),
)

function getTrustedOpenerOrigin() {
  if (!window.opener || window.opener.closed) {
    return DEFAULT_OPENER_ORIGIN
  }

  try {
    const openerOrigin = window.opener.location.origin
    if (openerOrigin && (openerOrigin === window.location.origin || ALLOWED_OPENER_ORIGINS.has(openerOrigin))) {
      return openerOrigin
    }
  } catch {
    // Cross-origin opener access is expected here.
  }

  return DEFAULT_OPENER_ORIGIN
}

const ROOT_ID = 'yui-guide-plugin-dashboard-runtime'
const SVG_NS = 'http://www.w3.org/2000/svg'
const BACKDROP_MASK_ID = `${ROOT_ID}-mask`
const DEFAULT_SPOTLIGHT_PADDING = 6
const BACKDROP_CUTOUT_INSET = 4
let currentGuideAudio: HTMLAudioElement | null = null
let currentGuideAudioTimer: number | null = null
let currentGuideSpeechStop: (() => void) | null = null
let openerMessageOrigin = ''
const guideAudioDurationCache = new Map<string, number>()
const guideAudioDurationPromiseCache = new Map<string, Promise<number>>()

type StartPayload = {
  line?: string
  voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
  audioUrl?: string
  closeOnDone?: boolean
  interruptCount?: number
  narrationDurationMs?: number
  narrationStartedAtMs?: number
  skipButtonScreenRect?: ScreenRect | null
  platformCapabilities?: HomeTutorialPlatformCapabilities | null
}

type SpotlightRect = {
  left: number
  top: number
  width: number
  height: number
  radius: number
  padding: number
}

type ScreenRect = {
  left: number
  top: number
  right: number
  bottom: number
  coordinateSpace?: string
  platform?: 'windows' | 'macos' | 'linux' | 'web' | string
  devicePixelRatio?: number
  hitPadding?: number
  forwardingTolerance?: number
  pointerProfile?: string
}

type HomeTutorialPlatformCapabilities = {
  version?: number
  platform?: 'windows' | 'macos' | 'linux' | 'web' | string
  windowBoundsSource?: string
  supportsExternalChat?: boolean
  supportsSystemTrayHint?: boolean
  supportsPluginDashboardWindow?: boolean
  pointerProfile?: string
  preferredSkipHitPadding?: number
}

type ActiveNarration = {
  text: string
  voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
  audioUrl?: string
  resumeAudioOffsetMs: number
  interrupted: boolean
  cancelled: boolean
  playVersion: number
  resolve: () => void
}

type PendingInterruptAck = {
  requestId: string
  resolve: (success: boolean) => void
  timeoutId: number | null
}

function wait(ms: number) {
  return new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value))
}

function normalizeGuideLocale(locale?: string) {
  const current = String(locale || '').trim().toLowerCase()
  if (!current || current === 'auto') {
    return DEFAULT_GUIDE_LOCALE
  }

  if (current.startsWith('ja')) return 'ja'
  if (current.startsWith('en')) return 'en'
  if (current.startsWith('ko')) return 'ko'
  if (current.startsWith('ru')) return 'ru'
  if (current.startsWith('zh')) return 'zh'
  return DEFAULT_GUIDE_LOCALE
}

function resolveGuideLocale() {
  try {
    return normalizeGuideLocale(getLocale())
  } catch (_) {}

  const candidates = [
    window.localStorage?.getItem('locale'),
    document.documentElement.lang,
    navigator.language,
  ]

  for (const candidate of candidates) {
    const value = String(candidate || '').trim()
    if (!value || value.toLowerCase() === 'auto') {
      continue
    }
    return normalizeGuideLocale(value)
  }

  return DEFAULT_GUIDE_LOCALE
}

function getAllowedOpenerOrigins() {
  const origins = new Set<string>(ALLOWED_OPENER_ORIGINS)
  const trustedOrigin = getTrustedOpenerOrigin()
  if (trustedOrigin) {
    origins.add(trustedOrigin)
  }
  if (openerMessageOrigin) {
    origins.add(openerMessageOrigin)
  }
  return origins
}

function isAllowedOpenerEvent(event: MessageEvent) {
  if (!window.opener || window.opener.closed || event.source !== window.opener) {
    return false
  }

  const origin = typeof event.origin === 'string' ? event.origin : ''
  if (!origin) {
    return false
  }

  if (origin === window.location.origin) {
    openerMessageOrigin = origin
    return true
  }

  const allowedOrigins = getAllowedOpenerOrigins()
  if (!allowedOrigins.has(origin)) {
    return false
  }

  openerMessageOrigin = origin
  return true
}

function estimateSpeechDurationMs(text: string) {
  const content = typeof text === 'string' ? text.trim() : ''
  if (!content) {
    return 0
  }

  return clamp(Math.round(content.length * 280), 2400, 24000)
}

function resolveGuideAudioSrc(voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY, audioUrl?: string) {
  const normalizedAudioUrl = typeof audioUrl === 'string' ? audioUrl.trim() : ''
  if (normalizedAudioUrl) {
    return normalizedAudioUrl
  }

  if (!voiceKey) {
    return ''
  }

  const locale = resolveGuideLocale()
  const files = GUIDE_AUDIO_BY_KEY[voiceKey]
  const fileName = files[locale as keyof typeof files] || files.zh || ''
  const fileLocale = files[locale as keyof typeof files] ? locale : DEFAULT_GUIDE_LOCALE
  return fileName ? `${GUIDE_AUDIO_BASE_URL}${fileLocale}/${encodeURIComponent(fileName)}` : ''
}

function getGuideAudioDurationCacheKey(audioSrc: string) {
  const normalizedAudioSrc = typeof audioSrc === 'string' ? audioSrc.trim() : ''
  if (!normalizedAudioSrc) {
    return ''
  }

  try {
    return new URL(normalizedAudioSrc, window.location.href).href
  } catch (_) {
    return normalizedAudioSrc
  }
}

function cacheGuideAudioDuration(audioSrc: string, durationSeconds: number) {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    return
  }
  const cacheKey = getGuideAudioDurationCacheKey(audioSrc)
  if (cacheKey) {
    guideAudioDurationCache.set(cacheKey, Math.round(durationSeconds * 1000))
  }
}

function playGuideAudioWithPromise(audioSrc: string, minimumDurationMs: number, startAtMs = 0) {
  const normalizedAudioSrc = typeof audioSrc === 'string' ? audioSrc.trim() : ''
  if (!normalizedAudioSrc) {
    return Promise.reject(new Error('missing_audio_src'))
  }

  return new Promise<void>((resolve, reject) => {
    let settled = false
    let playbackStarted = false
    let seekFallbackTimer: number | null = null
    const audio = new Audio()
    const initialTimeSeconds = Math.max(0, startAtMs / 1000)
    const cacheKey = getGuideAudioDurationCacheKey(normalizedAudioSrc)
    let resolveMetadataDuration: ((durationMs: number) => void) | null = null
    let metadataTimerId: number | null = null
    const maxWaitMs = Math.max(3000, minimumDurationMs) + 12000
    currentGuideAudio = audio
    if (cacheKey) {
      let metadataPromise: Promise<number>
      metadataPromise = new Promise<number>((resolveMetadata) => {
        resolveMetadataDuration = resolveMetadata
        metadataTimerId = window.setTimeout(() => {
          metadataTimerId = null
          resolveMetadata(0)
        }, 2500)
      }).finally(() => {
        if (guideAudioDurationPromiseCache.get(cacheKey) === metadataPromise) {
          guideAudioDurationPromiseCache.delete(cacheKey)
        }
      })
      guideAudioDurationPromiseCache.set(cacheKey, metadataPromise)
    }

    const finishMetadataDuration = (durationMs: number) => {
      if (metadataTimerId !== null) {
        window.clearTimeout(metadataTimerId)
        metadataTimerId = null
      }
      if (resolveMetadataDuration) {
        resolveMetadataDuration(durationMs)
        resolveMetadataDuration = null
      }
    }

    const finish = (success: boolean, error?: unknown) => {
      if (settled) {
        return
      }
      settled = true
      finishMetadataDuration(0)
      if (seekFallbackTimer !== null) {
        window.clearTimeout(seekFallbackTimer)
        seekFallbackTimer = null
      }
      window.clearTimeout(timerId)
      if (currentGuideAudioTimer === timerId) {
        currentGuideAudioTimer = null
      }
      if (currentGuideAudio === audio) {
        currentGuideAudio = null
      }
      if (currentGuideSpeechStop === stop) {
        currentGuideSpeechStop = null
      }
      audio.onended = null
      audio.onerror = null
      audio.onloadedmetadata = null
      audio.onseeked = null
      if (success) {
        resolve()
        return
      }
      reject(error)
    }

    const timerId = window.setTimeout(() => {
      finish(true)
    }, maxWaitMs)
    currentGuideAudioTimer = timerId

    const beginPlayback = () => {
      if (settled || playbackStarted) {
        return
      }
      playbackStarted = true
      try {
        const playback = audio.play()
        if (playback && typeof playback.then === 'function') {
          playback.catch((error: unknown) => finish(false, error))
        }
      } catch (error) {
        finish(false, error)
      }
    }

    audio.preload = 'auto'
    audio.onloadedmetadata = () => {
      const durationMs = Number.isFinite(audio.duration) && audio.duration > 0
        ? Math.round(audio.duration * 1000)
        : 0
      cacheGuideAudioDuration(normalizedAudioSrc, audio.duration)
      finishMetadataDuration(durationMs)
      if (settled) {
        return
      }
      if (initialTimeSeconds > 0) {
        const maxSeek = Number.isFinite(audio.duration) && audio.duration > 0
          ? Math.max(0, audio.duration - 0.05)
          : initialTimeSeconds
        const targetTime = Math.min(initialTimeSeconds, maxSeek)

        if (targetTime > 0.01) {
          audio.onseeked = () => {
            audio.onseeked = null
            if (seekFallbackTimer !== null) {
              window.clearTimeout(seekFallbackTimer)
              seekFallbackTimer = null
            }
            beginPlayback()
          }
          seekFallbackTimer = window.setTimeout(() => {
            seekFallbackTimer = null
            audio.onseeked = null
            beginPlayback()
          }, 250)

          try {
            audio.currentTime = targetTime
          } catch (_) {
            if (seekFallbackTimer !== null) {
              window.clearTimeout(seekFallbackTimer)
              seekFallbackTimer = null
            }
            audio.onseeked = null
            beginPlayback()
            return
          }

          if (Math.abs(audio.currentTime - targetTime) <= 0.01) {
            if (seekFallbackTimer !== null) {
              window.clearTimeout(seekFallbackTimer)
              seekFallbackTimer = null
            }
            audio.onseeked = null
            beginPlayback()
          }
          return
        }

        try {
          audio.currentTime = 0
        } catch (_) {}
      }
      beginPlayback()
    }
    audio.onended = () => finish(true)
    audio.onerror = () => finish(false, new Error('guide_audio_error'))
    const stop = () => {
      try {
        audio.pause()
        audio.currentTime = 0
      } catch (_) {}
      finish(true)
    }
    currentGuideSpeechStop = stop

    try {
      audio.src = normalizedAudioSrc
      audio.load()
    } catch (error) {
      finish(false, error)
    }
  })
}

function loadGuideAudioDurationMs(audioSrc: string, fallbackDurationMs: number): Promise<number> {
  const normalizedAudioSrc = typeof audioSrc === 'string' ? audioSrc.trim() : ''
  if (!normalizedAudioSrc) {
    return Promise.resolve(fallbackDurationMs)
  }

  const cacheKey = getGuideAudioDurationCacheKey(normalizedAudioSrc)
  const cachedDurationMs = cacheKey ? guideAudioDurationCache.get(cacheKey) : null
  if (Number.isFinite(cachedDurationMs) && (cachedDurationMs as number) > 0) {
    return Promise.resolve(cachedDurationMs as number)
  }

  const pendingDurationPromise = cacheKey ? guideAudioDurationPromiseCache.get(cacheKey) : null
  if (pendingDurationPromise) {
    return pendingDurationPromise.then((durationMs): number | Promise<number> => {
      if (Number.isFinite(durationMs) && durationMs > 0) {
        return durationMs
      }
      return loadGuideAudioDurationMs(normalizedAudioSrc, fallbackDurationMs)
    })
  }

  const currentAudioCacheKey = currentGuideAudio
    ? getGuideAudioDurationCacheKey(currentGuideAudio.currentSrc || currentGuideAudio.src || '')
    : ''
  if (
    currentGuideAudio
    && cacheKey
    && currentAudioCacheKey === cacheKey
    && Number.isFinite(currentGuideAudio.duration)
    && currentGuideAudio.duration > 0
  ) {
    const durationMs = Math.round(currentGuideAudio.duration * 1000)
    guideAudioDurationCache.set(cacheKey, durationMs)
    return Promise.resolve(durationMs)
  }

  return new Promise<number>((resolve) => {
    let settled = false
    const audio = new Audio()
    const finish = (durationMs?: number) => {
      if (settled) {
        return
      }
      settled = true
      window.clearTimeout(timerId)
      audio.onloadedmetadata = null
      audio.onerror = null
      try {
        audio.pause()
        audio.removeAttribute('src')
        audio.load()
      } catch (_) {}
      resolve(Number.isFinite(durationMs) && (durationMs as number) > 0
        ? Math.round(durationMs as number)
        : fallbackDurationMs)
    }

    const timerId = window.setTimeout(() => finish(), 2500)
    audio.preload = 'metadata'
    audio.onloadedmetadata = () => {
      const durationMs = Number.isFinite(audio.duration) && audio.duration > 0
        ? audio.duration * 1000
        : 0
      if (cacheKey && durationMs > 0) {
        guideAudioDurationCache.set(cacheKey, Math.round(durationMs))
      }
      finish(durationMs)
    }
    audio.onerror = () => finish()

    try {
      audio.src = normalizedAudioSrc
      audio.load()
    } catch (_) {
      finish()
    }
  })
}

function createSvgElement<K extends keyof SVGElementTagNameMap>(
  tagName: K,
  className?: string,
) {
  const element = document.createElementNS(SVG_NS, tagName)
  if (className) {
    element.setAttribute('class', className)
  }
  return element
}

function readSpotlightNumberAttr(element: Element | null, attributeName: string) {
  if (!element || !attributeName || typeof element.getAttribute !== 'function') {
    return null
  }

  const rawValue = element.getAttribute(attributeName)
  const value = Number.parseFloat(rawValue || '')
  return Number.isFinite(value) ? value : null
}

function ensurePluginSpotlightDecorations(spotlight: HTMLDivElement | null) {
  if (!spotlight) {
    return
  }

  let chrome = spotlight.querySelector('.yui-guide-plugin-spotlight-chrome') as HTMLDivElement | null
  if (!chrome) {
    chrome = document.createElement('div')
    chrome.className = 'yui-guide-plugin-spotlight-chrome'
    spotlight.appendChild(chrome)
  } else if (!(chrome instanceof HTMLDivElement)) {
    chrome = null
  }

  if (!spotlight.querySelector('.yui-guide-plugin-spotlight-sweep')) {
    const sweep = document.createElement('span')
    sweep.className = 'yui-guide-plugin-spotlight-sweep'
    spotlight.appendChild(sweep)
  }

  if (!spotlight.querySelector('.yui-guide-plugin-spotlight-ear-left')) {
    const earLeft = document.createElement('div')
    earLeft.className = 'yui-guide-plugin-spotlight-decoration yui-guide-plugin-spotlight-ear-left'
    spotlight.appendChild(earLeft)
  }

  if (!spotlight.querySelector('.yui-guide-plugin-spotlight-ear-right')) {
    const earRight = document.createElement('div')
    earRight.className = 'yui-guide-plugin-spotlight-decoration yui-guide-plugin-spotlight-ear-right'
    spotlight.appendChild(earRight)
  }

  if (!spotlight.querySelector('.yui-guide-plugin-spotlight-paw')) {
    const paw = document.createElement('div')
    paw.className = 'yui-guide-plugin-spotlight-decoration yui-guide-plugin-spotlight-paw'
    spotlight.appendChild(paw)
  }
}

function speakTextWithPromise(
  text: string,
  options?: {
    voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
    audioUrl?: string
    startAtMs?: number
  },
): Promise<void> {
  const content = typeof text === 'string' ? text.trim() : ''
  if (!content) {
    return Promise.resolve()
  }

  const minDurationMs = estimateSpeechDurationMs(content)
  const localAudioSrc = resolveGuideAudioSrc(options?.voiceKey, options?.audioUrl)
  const startAtMs = Number.isFinite(options?.startAtMs) ? Math.max(0, Math.round(options?.startAtMs as number)) : 0
  if (localAudioSrc) {
    return playGuideAudioWithPromise(localAudioSrc, minDurationMs, startAtMs).catch(() => {
      return wait(minDurationMs)
    })
  }

  return wait(minDurationMs)
}

function stopCurrentGuideSpeech() {
  const stop = currentGuideSpeechStop
  currentGuideSpeechStop = null
  if (!stop) {
    return
  }
  try {
    stop()
  } catch (_) {}
}

async function resolveNarrationDurationMs(payload: StartPayload) {
  if (Number.isFinite(payload.narrationDurationMs)) {
    return Math.min(Math.max(0, Math.round(payload.narrationDurationMs as number)), 24000)
  }

  const fallbackDurationMs = PLUGIN_DASHBOARD_DEFAULT_TOTAL_MS
  const localAudioSrc = resolveGuideAudioSrc(payload.voiceKey, payload.audioUrl)
  if (!localAudioSrc) {
    return fallbackDurationMs
  }

  return loadGuideAudioDurationMs(localAudioSrc, fallbackDurationMs)
}

function resolveResistanceTextKey(interruptCount: number) {
  return interruptCount >= 2
    ? 'tutorial.yuiGuide.lines.interruptResistLight3'
    : 'tutorial.yuiGuide.lines.interruptResistLight1'
}

function shouldReduceMotion() {
  try {
    const query = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null
    return !!query?.matches
  } catch {
    return false
  }
}

function injectStyle() {
  if (document.getElementById(`${ROOT_ID}-style`)) {
    return
  }

  const style = document.createElement('style')
  style.id = `${ROOT_ID}-style`
  style.textContent = `
    #${ROOT_ID},
    #${ROOT_ID} .yui-guide-plugin-backdrop,
    #${ROOT_ID} .yui-guide-plugin-backdrop *,
    #${ROOT_ID} .yui-guide-plugin-interaction-shield,
    #${ROOT_ID} .yui-guide-plugin-spotlight,
    #${ROOT_ID} .yui-guide-plugin-cursor-shell,
    #${ROOT_ID} .yui-guide-plugin-cursor,
    html.yui-guide-plugin-dashboard-running [data-yui-cursor-hidden="true"],
    body.yui-guide-plugin-dashboard-running [data-yui-cursor-hidden="true"],
    html.yui-taking-over [data-yui-cursor-hidden="true"],
    body.yui-taking-over [data-yui-cursor-hidden="true"] {
      cursor: none !important;
    }

    html.yui-taking-over.yui-resistance-cursor-reveal,
    html.yui-taking-over.yui-resistance-cursor-reveal *,
    body.yui-taking-over.yui-resistance-cursor-reveal,
    body.yui-taking-over.yui-resistance-cursor-reveal * {
      cursor: auto !important;
    }

    html.yui-guide-plugin-dashboard-running button,
    html.yui-guide-plugin-dashboard-running a[href],
    html.yui-guide-plugin-dashboard-running input,
    html.yui-guide-plugin-dashboard-running select,
    html.yui-guide-plugin-dashboard-running textarea,
    html.yui-guide-plugin-dashboard-running summary,
    html.yui-guide-plugin-dashboard-running [role="button"],
    html.yui-guide-plugin-dashboard-running [role="link"],
    html.yui-guide-plugin-dashboard-running [tabindex]:not([tabindex="-1"]),
    body.yui-guide-plugin-dashboard-running button,
    body.yui-guide-plugin-dashboard-running a[href],
    body.yui-guide-plugin-dashboard-running input,
    body.yui-guide-plugin-dashboard-running select,
    body.yui-guide-plugin-dashboard-running textarea,
    body.yui-guide-plugin-dashboard-running summary,
    body.yui-guide-plugin-dashboard-running [role="button"],
    body.yui-guide-plugin-dashboard-running [role="link"],
    body.yui-guide-plugin-dashboard-running [tabindex]:not([tabindex="-1"]) {
      cursor: auto !important;
    }

    #${ROOT_ID} {
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 2147483646;
    }

    #${ROOT_ID} .yui-guide-plugin-backdrop {
      position: fixed;
      inset: 0;
      width: 100%;
      height: 100%;
      display: none !important;
      opacity: 0 !important;
      visibility: hidden !important;
      transition: none !important;
    }

    #${ROOT_ID} .yui-guide-plugin-interaction-shield {
      position: fixed;
      inset: 0;
      pointer-events: auto;
      background: transparent;
      cursor: none !important;
      touch-action: none;
      user-select: none;
      -webkit-user-select: none;
    }

    #${ROOT_ID} .yui-guide-plugin-backdrop-cutout {
      transition:
        x 220ms ease,
        y 220ms ease,
        width 220ms ease,
        height 220ms ease,
        rx 220ms ease,
        ry 220ms ease;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight {
      position: fixed;
      border-radius: 18px;
      opacity: 0;
      overflow: visible;
      isolation: isolate;
      transition:
        opacity 180ms ease,
        left 220ms ease,
        top 220ms ease,
        width 220ms ease,
        height 220ms ease;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-chrome {
      position: absolute;
      inset: 3px;
      border-radius: inherit;
      overflow: hidden;
      isolation: isolate;
      background: linear-gradient(180deg, rgba(84, 133, 255, 0.09), rgba(89, 211, 255, 0.03));
      box-shadow:
        0 0 0 1px rgba(214, 243, 255, 0.72),
        0 0 18px rgba(104, 194, 255, 0.56),
        0 0 34px rgba(87, 136, 255, 0.26),
        inset 0 0 16px rgba(131, 214, 255, 0.16);
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-sweep {
      position: absolute;
      inset: 8px;
      border-radius: inherit;
      overflow: hidden;
      pointer-events: none;
      z-index: 4;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-sweep::before {
      content: '';
      position: absolute;
      top: -22%;
      bottom: -22%;
      left: -48%;
      width: 34%;
      background:
        linear-gradient(108deg, transparent 0 10%, rgba(255, 255, 255, 0.58) 45%, rgba(125, 225, 255, 0.26) 58%, transparent 100%);
      filter: blur(0.2px);
      opacity: 0;
      transform: translateX(0) skewX(-12deg);
      animation: yui-guide-plugin-spotlight-sheen 2.4s ease-in-out infinite;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-chrome::before {
      content: '';
      position: absolute;
      inset: 0;
      padding: 2px;
      border-radius: inherit;
      --yui-guide-plugin-spotlight-corner-size: min(34%, 138px);
      --yui-guide-plugin-spotlight-border-gap: min(68%, 144px);
      background:
        linear-gradient(rgba(39, 89, 228, 0.98), rgba(39, 89, 228, 0.98)) top center / calc(100% - var(--yui-guide-plugin-spotlight-border-gap)) 2px no-repeat,
        linear-gradient(rgba(39, 89, 228, 0.98), rgba(39, 89, 228, 0.98)) bottom center / calc(100% - var(--yui-guide-plugin-spotlight-border-gap)) 2px no-repeat,
        linear-gradient(90deg, rgba(39, 89, 228, 0.98), rgba(39, 89, 228, 0.98)) left center / 2px calc(100% - var(--yui-guide-plugin-spotlight-border-gap)) no-repeat,
        linear-gradient(90deg, rgba(39, 89, 228, 0.98), rgba(39, 89, 228, 0.98)) right center / 2px calc(100% - var(--yui-guide-plugin-spotlight-border-gap)) no-repeat,
        radial-gradient(circle at top left,
          rgba(235, 249, 255, 0.98) 0,
          rgba(186, 231, 255, 0.98) 13%,
          rgba(76, 137, 255, 0.95) 52%,
          rgba(39, 89, 228, 0.98) 96%,
          transparent 100%
        ) top left / var(--yui-guide-plugin-spotlight-corner-size) var(--yui-guide-plugin-spotlight-corner-size) no-repeat,
        radial-gradient(circle at top right,
          rgba(235, 249, 255, 0.98) 0,
          rgba(186, 231, 255, 0.98) 13%,
          rgba(76, 137, 255, 0.95) 52%,
          rgba(39, 89, 228, 0.98) 96%,
          transparent 100%
        ) top right / var(--yui-guide-plugin-spotlight-corner-size) var(--yui-guide-plugin-spotlight-corner-size) no-repeat,
        radial-gradient(circle at bottom right,
          rgba(235, 249, 255, 0.98) 0,
          rgba(186, 231, 255, 0.98) 13%,
          rgba(76, 137, 255, 0.95) 52%,
          rgba(39, 89, 228, 0.98) 96%,
          transparent 100%
        ) bottom right / var(--yui-guide-plugin-spotlight-corner-size) var(--yui-guide-plugin-spotlight-corner-size) no-repeat,
        radial-gradient(circle at bottom left,
          rgba(235, 249, 255, 0.98) 0,
          rgba(186, 231, 255, 0.98) 13%,
          rgba(76, 137, 255, 0.95) 52%,
          rgba(39, 89, 228, 0.98) 96%,
          transparent 100%
        ) bottom left / var(--yui-guide-plugin-spotlight-corner-size) var(--yui-guide-plugin-spotlight-corner-size) no-repeat;
      pointer-events: none;
      z-index: 2;
      -webkit-mask:
        linear-gradient(#000 0 0) content-box,
        linear-gradient(#000 0 0);
      -webkit-mask-composite: xor;
      mask:
        linear-gradient(#000 0 0) content-box,
        linear-gradient(#000 0 0);
      mask-composite: exclude;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-decoration {
      position: absolute;
      pointer-events: none;
      background-position: center;
      background-repeat: no-repeat;
      background-size: contain;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-ear-left {
      top: -29px;
      left: 2px;
      width: 94.5px;
      height: 40.5px;
      background-image: url('${leftCatEarUrl}');
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-ear-right {
      top: -30px;
      right: 2px;
      width: 94.5px;
      height: 40.5px;
      background-image: url('${rightCatEarUrl}');
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight-paw {
      right: -18px;
      bottom: -11px;
      width: 51px;
      height: 51px;
      background-image: url('${catPawUrl}');
      filter: drop-shadow(0 0 8px rgba(119, 211, 255, 0.58));
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight.is-visible {
      opacity: 1;
      animation: yui-guide-plugin-pulse 1.5s ease-in-out infinite;
    }

    #${ROOT_ID}.is-angry .yui-guide-plugin-backdrop-fill {
      fill: rgba(58, 10, 10, 0.82);
    }

    #${ROOT_ID}.is-angry .yui-guide-plugin-spotlight {
      opacity: 0 !important;
      display: none !important;
      animation: none;
    }

    #${ROOT_ID}.is-angry .yui-guide-plugin-backdrop-cutout {
      visibility: hidden !important;
      display: none !important;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-shell {
      position: fixed;
      left: 0;
      top: 0;
      z-index: 3;
      width: 0;
      height: 0;
      transform: translate(0, 0);
      transition-property: transform;
      transition-timing-function: cubic-bezier(0.2, 0.9, 0.2, 1);
      transition-duration: 0ms;
      opacity: 0;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-shell.is-visible {
      opacity: 1;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor {
      position: absolute;
      width: 46px;
      height: 46px;
      margin-left: -20px;
      margin-top: -18px;
      background-image: url('${defaultGhostCursorUrl}');
      background-repeat: no-repeat;
      background-position: center;
      background-size: contain;
      filter: drop-shadow(0 10px 20px rgba(138, 78, 50, 0.24));
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail-layer {
      position: fixed;
      inset: 0;
      z-index: 2;
      width: 100vw;
      height: 100vh;
      pointer-events: none;
      opacity: 0;
      overflow: visible;
      transition: opacity 90ms ease-out;
      will-change: opacity, transform;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail-layer.is-visible {
      opacity: 1;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail-ribbon {
      opacity: 0.96;
      filter:
        blur(0.35px)
        drop-shadow(0 0 10px rgba(41, 191, 255, 0.36))
        drop-shadow(0 3px 16px rgba(42, 86, 224, 0.24));
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail-core {
      opacity: 0.58;
      filter: blur(0.18px);
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail-head {
      opacity: 0.95;
      filter:
        blur(0.2px)
        drop-shadow(0 0 12px rgba(58, 223, 255, 0.42));
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail-head-core {
      opacity: 0.58;
      filter: blur(0.4px);
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail {
      position: fixed;
      left: 0;
      top: 0;
      z-index: 2;
      width: var(--trail-width, 10px);
      height: var(--trail-height, 10px);
      pointer-events: none;
      opacity: 0;
      transform:
        translate(-50%, -50%)
        rotate(var(--trail-angle, 0deg))
        scale(0.92);
      animation: yui-guide-plugin-cursor-trail-fade 300ms ease-out both;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail.is-glow {
      display: none;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor-trail.is-icon {
      background-image: var(--trail-icon);
      background-repeat: no-repeat;
      background-position: center;
      background-size: contain;
      filter:
        brightness(var(--trail-brightness, 0.88))
        saturate(1.28)
        drop-shadow(0 0 5px rgba(87, 211, 255, 0.32));
      opacity: 0;
    }

    #${ROOT_ID} .yui-guide-plugin-click-star {
      position: absolute;
      left: 0;
      top: 0;
      z-index: 3;
      width: var(--star-size, 8px);
      height: var(--star-size, 8px);
      pointer-events: none;
      opacity: 0;
      background:
        radial-gradient(circle at 34% 30%, rgba(255, 255, 255, 0.96) 0 16%, transparent 17%),
        hsl(var(--star-hue, 46) 96% 62%);
      clip-path: polygon(50% 0, 61% 34%, 96% 34%, 68% 55%, 80% 92%, 50% 70%, 20% 92%, 32% 55%, 4% 34%, 39% 34%);
      filter:
        drop-shadow(0 0 5px rgba(255, 239, 150, 0.72))
        drop-shadow(0 2px 5px rgba(180, 92, 32, 0.24));
      transform: translate(-50%, -50%) rotate(var(--star-rotate, 0deg)) scale(0.24);
      animation: yui-guide-plugin-click-star-burst 760ms cubic-bezier(0.16, 1, 0.3, 1) var(--star-delay, 0ms) both;
    }

    #${ROOT_ID}.is-angry .yui-guide-plugin-cursor {
      background-color: transparent;
      filter:
        drop-shadow(0 14px 26px rgba(116, 33, 25, 0.34))
        saturate(1.08);
    }

    #${ROOT_ID}.is-angry .yui-guide-plugin-cursor.is-clicking {
      background-image: url('${defaultGhostCursorUrl}');
      animation: none;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor::after {
      content: none;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor.is-clicking {
      background-image: url('${clickGhostCursorUrl}');
      animation: yui-guide-plugin-click 420ms ease;
    }

    @keyframes yui-guide-plugin-pulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.02); }
    }

    @keyframes yui-guide-plugin-spotlight-sheen {
      0%,
      62% {
        opacity: 0;
        transform: translateX(0) skewX(-12deg);
      }
      78% {
        opacity: 0.42;
      }
      100% {
        opacity: 0;
        transform: translateX(420%) skewX(-12deg);
      }
    }

    @keyframes yui-guide-plugin-click {
      0% { transform: scale(1); }
      35%, 68% { transform: scale(0.82); }
      100% { transform: scale(1); }
    }

    @keyframes yui-guide-plugin-cursor-trail-fade {
      0% {
        opacity: var(--trail-opacity, 0.16);
        transform:
          translate(-50%, -50%)
          rotate(var(--trail-angle, 0deg))
          scale(0.84);
      }
      45% {
        opacity: calc(var(--trail-opacity, 0.16) * 0.62);
      }
      100% {
        opacity: 0;
        transform:
          translate(calc(-50% + var(--trail-drift-x, 0px)), calc(-50% + var(--trail-drift-y, 0px)))
          rotate(var(--trail-angle, 0deg))
          scale(0.48);
      }
    }

    @keyframes yui-guide-plugin-click-star-burst {
      0% {
        opacity: 0;
        transform: translate(-50%, -50%) rotate(var(--star-rotate, 0deg)) scale(0.18);
      }
      18% {
        opacity: 1;
      }
      62% {
        opacity: 0.94;
        transform:
          translate(calc(-50% + var(--star-mid-x, 0px)), calc(-50% + var(--star-mid-y, 0px)))
          rotate(calc(var(--star-rotate, 0deg) + 88deg))
          scale(1);
      }
      100% {
        opacity: 0;
        transform:
          translate(calc(-50% + var(--star-x, 0px)), calc(-50% + var(--star-y, 0px)))
          rotate(calc(var(--star-rotate, 0deg) + 170deg))
          scale(0.18);
      }
    }

    @media (prefers-reduced-motion: reduce) {
      #${ROOT_ID} .yui-guide-plugin-spotlight,
      #${ROOT_ID} .yui-guide-plugin-spotlight-sweep::before,
      #${ROOT_ID} .yui-guide-plugin-cursor-trail-layer,
      #${ROOT_ID} .yui-guide-plugin-cursor-trail,
      #${ROOT_ID} .yui-guide-plugin-click-star {
        animation: none !important;
      }

      #${ROOT_ID} .yui-guide-plugin-cursor-trail-layer,
      #${ROOT_ID} .yui-guide-plugin-cursor-trail,
      #${ROOT_ID} .yui-guide-plugin-click-star {
        display: none !important;
      }
    }
  `
  document.head.appendChild(style)
}

class PluginDashboardGuideRuntime {
  root: HTMLDivElement | null = null
  backdrop: SVGSVGElement | null = null
  backdropBase: SVGRectElement | null = null
  backdropFill: SVGRectElement | null = null
  backdropCutout: SVGRectElement | null = null
  interactionShield: HTMLDivElement | null = null
  spotlight: HTMLDivElement | null = null
  cursorShell: HTMLDivElement | null = null
  cursorInner: HTMLDivElement | null = null
  cursorPosition: { x: number; y: number } | null = null
  lastCursorTarget: { x: number; y: number } | null = null
  spotlightElement: Element | null = null
  activeSessionId = ''
  running = false
  interruptsEnabled = false
  scenePausedForResistance = false
  homeNarrationFinished = false
  angryExitTriggered = false
  interruptCount = 0
  interruptAccelerationStreak = 0
  lastInterruptAt = 0
  lastPassiveResistanceAt = 0
  lastPointerPoint: { x: number; y: number; t: number; speed: number } | null = null
  scriptedMotionInterruptDistance = 0
  scriptedMotionInterruptWindowStartedAt = 0
  resistanceCursorTimer: number | null = null
  cursorClickTimer: number | null = null
  activeClickStars: Set<{ element: HTMLSpanElement; timer: number }> = new Set()
  activeTrailParticles: Set<{ element: HTMLSpanElement; timer: number }> = new Set()
  cursorTrailLastPoint: { x: number; y: number; t?: number } | null = null
  cursorTrailLastAt = 0
  cursorTrailSvg: SVGSVGElement | null = null
  cursorTrailBody: SVGPathElement | null = null
  cursorTrailCore: SVGPathElement | null = null
  cursorTrailHead: SVGCircleElement | null = null
  cursorTrailHeadCore: SVGCircleElement | null = null
  cursorTrailGradient: SVGLinearGradientElement | null = null
  cursorTrailPoints: Array<{ x: number; y: number; t: number }> = []
  cursorTrailDecayFrame = 0
  narrationResumeTimer: number | null = null
  scenePauseResolvers: Array<() => void> = []
  homeNarrationResolvers: Array<() => void> = []
  cursorMotionToken = 0
  cursorReactionInFlight = false
  cursorTransitionActive = false
  activeNarration: ActiveNarration | null = null
  pendingInterruptAck: PendingInterruptAck | null = null
  preactivationTimeoutId: number | null = null
  homeSkipButtonScreenRect: ScreenRect | null = null
  lastForwardedSkipAt = 0
  lastForwardedSkipScreenX = NaN
  lastForwardedSkipScreenY = NaN
  boundPointerMoveHandler = (event: PointerEvent | MouseEvent) => {
    this.handleInterrupt(event)
  }
  boundPointerDownHandler = (event: PointerEvent | MouseEvent) => {
    if (this.forwardHomeSkipClick(event)) {
      return
    }
    this.onPointerDown(event)
  }
  boundInteractionGuard = (event: Event) => {
    if (!this.running || !event) {
      return
    }

    if (
      typeof window.MouseEvent !== 'undefined'
      && event instanceof window.MouseEvent
      && this.forwardHomeSkipClick(event)
    ) {
      return
    }

    if ((event as { isTrusted?: boolean }).isTrusted === false) {
      return
    }

    if (typeof event.preventDefault === 'function') {
      event.preventDefault()
    }
    if (typeof event.stopImmediatePropagation === 'function') {
      event.stopImmediatePropagation()
    }
    if (typeof event.stopPropagation === 'function') {
      event.stopPropagation()
    }
  }
  boundRefreshSpotlight = () => {
    if (!this.spotlightElement) {
      this.syncBackdropViewport()
      return
    }
    this.setSpotlight(this.spotlightElement)
  }

  isCurrentRun(sessionId: string) {
    return this.running && this.activeSessionId === sessionId
  }

  hasPendingPluginDashboardHandoff() {
    if (typeof window === 'undefined' || typeof window.localStorage === 'undefined') {
      return false
    }

    try {
      const raw = window.localStorage.getItem(HANDOFF_STORAGE_KEY)
      if (!raw) {
        return false
      }
      const token = JSON.parse(raw) as {
        token_version?: number
        flow_id?: string
        target_page?: string
        consumed?: boolean
        expires_at?: number
      } | null
      return !!(
        token
        && token.token_version === HANDOFF_TOKEN_VERSION
        && typeof token.flow_id === 'string'
        && token.flow_id.trim() !== ''
        && token.target_page === 'plugin_dashboard'
        && token.consumed !== true
        && Number.isFinite(token.expires_at)
        && Number(token.expires_at) > Date.now()
      )
    } catch {
      return false
    }
  }

  clearPreactivationTimeout() {
    if (this.preactivationTimeoutId !== null) {
      window.clearTimeout(this.preactivationTimeoutId)
      this.preactivationTimeoutId = null
    }
  }

  preactivatePendingOverlay() {
    if (!window.opener || window.opener.closed) {
      return false
    }
    if (!this.hasPendingPluginDashboardHandoff()) {
      return false
    }

    this.activateOverlayShell()
    this.clearPreactivationTimeout()
    this.preactivationTimeoutId = window.setTimeout(() => {
      this.preactivationTimeoutId = null
      if (this.running) {
        return
      }
      this.cleanup()
    }, PREACTIVATE_CLEANUP_MS)
    return true
  }

  createCursorTrailLayer() {
    const trailSvg = createSvgElement('svg', 'yui-guide-plugin-cursor-trail-layer') as SVGSVGElement
    trailSvg.setAttribute('aria-hidden', 'true')
    trailSvg.setAttribute('preserveAspectRatio', 'none')

    const defs = createSvgElement('defs')
    const gradient = createSvgElement('linearGradient') as SVGLinearGradientElement
    gradient.id = `${ROOT_ID}-cursor-trail-gradient`
    gradient.setAttribute('gradientUnits', 'userSpaceOnUse')

    ;([
      ['0%', '#3157e8', '0'],
      ['30%', '#396dff', '0.16'],
      ['68%', '#26bfff', '0.58'],
      ['100%', '#55efff', '0.92'],
    ] as const).forEach(([offset, color, opacity]) => {
      const stop = createSvgElement('stop')
      stop.setAttribute('offset', offset)
      stop.setAttribute('stop-color', color)
      stop.setAttribute('stop-opacity', opacity)
      gradient.appendChild(stop)
    })

    const headGradient = createSvgElement('radialGradient')
    headGradient.id = `${ROOT_ID}-cursor-trail-head-gradient`
    headGradient.setAttribute('cx', '50%')
    headGradient.setAttribute('cy', '50%')
    headGradient.setAttribute('r', '58%')
    ;([
      ['0%', '#7df7ff', '0.78'],
      ['48%', '#31c8ff', '0.36'],
      ['100%', '#2d5cff', '0'],
    ] as const).forEach(([offset, color, opacity]) => {
      const stop = createSvgElement('stop')
      stop.setAttribute('offset', offset)
      stop.setAttribute('stop-color', color)
      stop.setAttribute('stop-opacity', opacity)
      headGradient.appendChild(stop)
    })

    defs.appendChild(gradient)
    defs.appendChild(headGradient)

    const body = createSvgElement('path', 'yui-guide-plugin-cursor-trail-ribbon') as SVGPathElement
    body.setAttribute('fill', `url(#${ROOT_ID}-cursor-trail-gradient)`)

    const core = createSvgElement('path', 'yui-guide-plugin-cursor-trail-core') as SVGPathElement
    core.setAttribute('fill', `url(#${ROOT_ID}-cursor-trail-gradient)`)

    const head = createSvgElement('circle', 'yui-guide-plugin-cursor-trail-head') as SVGCircleElement
    head.setAttribute('fill', `url(#${ROOT_ID}-cursor-trail-head-gradient)`)

    const headCore = createSvgElement('circle', 'yui-guide-plugin-cursor-trail-head-core') as SVGCircleElement
    headCore.setAttribute('fill', '#66f3ff')

    trailSvg.appendChild(defs)
    trailSvg.appendChild(body)
    trailSvg.appendChild(core)
    trailSvg.appendChild(head)
    trailSvg.appendChild(headCore)

    this.cursorTrailSvg = trailSvg
    this.cursorTrailBody = body
    this.cursorTrailCore = core
    this.cursorTrailHead = head
    this.cursorTrailHeadCore = headCore
    this.cursorTrailGradient = gradient

    return trailSvg
  }

  ensureRoot() {
    if (this.root && this.root.isConnected) {
      return
    }

    injectStyle()

    const root = document.createElement('div')
    root.id = ROOT_ID

    const backdrop = createSvgElement('svg', 'yui-guide-plugin-backdrop')
    ;(backdrop as unknown as { hidden?: boolean }).hidden = true
    backdrop.style.display = 'none'
    const defs = createSvgElement('defs')
    const mask = createSvgElement('mask')
    mask.id = BACKDROP_MASK_ID
    mask.setAttribute('maskUnits', 'userSpaceOnUse')
    mask.setAttribute('maskContentUnits', 'userSpaceOnUse')

    const backdropBase = createSvgElement('rect')
    backdropBase.setAttribute('fill', 'white')

    const backdropCutout = createSvgElement('rect', 'yui-guide-plugin-backdrop-cutout')
    backdropCutout.setAttribute('fill', 'black')
    backdropCutout.setAttribute('visibility', 'hidden')
    ;(backdropCutout as unknown as { hidden?: boolean }).hidden = true
    backdropCutout.style.display = 'none'

    const backdropFill = createSvgElement('rect', 'yui-guide-plugin-backdrop-fill')
    backdropFill.setAttribute('fill', 'transparent')
    backdropFill.setAttribute('mask', `url(#${BACKDROP_MASK_ID})`)

    mask.appendChild(backdropBase)
    mask.appendChild(backdropCutout)
    defs.appendChild(mask)
    backdrop.appendChild(defs)
    backdrop.appendChild(backdropFill)

    const spotlight = document.createElement('div')
    spotlight.className = 'yui-guide-plugin-spotlight'
    spotlight.hidden = true
    ensurePluginSpotlightDecorations(spotlight)

    const interactionShield = document.createElement('div')
    interactionShield.className = 'yui-guide-plugin-interaction-shield'

    const cursorShell = document.createElement('div')
    cursorShell.className = 'yui-guide-plugin-cursor-shell'

    const cursorInner = document.createElement('div')
    cursorInner.className = 'yui-guide-plugin-cursor'
    cursorShell.appendChild(cursorInner)
    const cursorTrailSvg = this.createCursorTrailLayer()

    root.appendChild(backdrop)
    root.appendChild(interactionShield)
    root.appendChild(spotlight)
    root.appendChild(cursorTrailSvg)
    root.appendChild(cursorShell)
    document.body.appendChild(root)

    this.root = root
    this.backdrop = backdrop
    this.backdropBase = backdropBase
    this.backdropFill = backdropFill
    this.backdropCutout = backdropCutout
    this.interactionShield = interactionShield
    this.spotlight = spotlight
    this.cursorShell = cursorShell
    this.cursorInner = cursorInner
    this.syncBackdropViewport()
  }

  notify(type: string, sessionId: string, detail?: Record<string, unknown>, requestId?: string) {
    try {
      const targetOrigin = openerMessageOrigin || getTrustedOpenerOrigin()
      if (!targetOrigin) {
        return
      }
      window.opener?.postMessage({
        type,
        sessionId,
        requestId: requestId || undefined,
        detail: detail || undefined,
      }, targetOrigin)
    } catch (_) {}
  }

  clearPendingInterruptAck(success: boolean) {
    const pending = this.pendingInterruptAck
    if (!pending) {
      return
    }
    if (pending.timeoutId !== null) {
      window.clearTimeout(pending.timeoutId)
    }
    this.pendingInterruptAck = null
    try {
      pending.resolve(success)
    } catch (_) {}
  }

  handleInterruptAckMessage(event: MessageEvent) {
    if (!isAllowedOpenerEvent(event)) {
      return
    }

    const data = event.data
    if (!data || typeof data !== 'object' || data.type !== INTERRUPT_ACK_EVENT) {
      return
    }

    const pending = this.pendingInterruptAck
    const requestId = typeof data.requestId === 'string' ? data.requestId : ''
    if (!pending || !requestId || pending.requestId !== requestId) {
      return
    }

    this.clearPendingInterruptAck(true)
  }

  requestHomeInterruptPlayback(
    detail: {
      kind: 'interrupt_resist_light' | 'interrupt_angry_exit'
      text: string
      textKey: string
      voiceKey: keyof typeof GUIDE_AUDIO_BY_KEY
      interruptCount: number
      x?: number
      y?: number
    },
  ) {
    if (!window.opener || window.opener.closed) {
      return Promise.resolve(false)
    }

    const targetOrigin = openerMessageOrigin || getTrustedOpenerOrigin()
    if (!targetOrigin) {
      return Promise.resolve(false)
    }

    this.clearPendingInterruptAck(false)
    const requestId = `plugin-dashboard-interrupt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const timeoutMs = clamp(estimateSpeechDurationMs(detail.text) + 4000, 4000, 12000)

    return new Promise<boolean>((resolve) => {
      const timeoutId = window.setTimeout(() => {
        if (!this.pendingInterruptAck || this.pendingInterruptAck.requestId !== requestId) {
          return
        }
        this.clearPendingInterruptAck(false)
      }, timeoutMs)

      this.pendingInterruptAck = {
        requestId,
        resolve,
        timeoutId,
      }

      try {
        this.notify(INTERRUPT_REQUEST_EVENT, this.activeSessionId, detail, requestId)
      } catch (_) {
        this.clearPendingInterruptAck(false)
      }
    })
  }

  async waitForElement<T extends Element>(resolver: () => T | null, timeoutMs = 5000) {
    const startedAt = Date.now()
    while ((Date.now() - startedAt) < timeoutMs) {
      const element = resolver()
      if (element) {
        return element
      }
      await wait(80)
    }
    return null
  }

  getRect(element: Element | null) {
    if (!element || !(element instanceof HTMLElement)) {
      return null
    }
    const rect = element.getBoundingClientRect()
    if (!rect.width || !rect.height) {
      return null
    }
    return rect
  }

  getSpotlightRect(element: Element | null): SpotlightRect | null {
    const rect = this.getRect(element)
    if (!rect) {
      return null
    }

    const htmlElement = element instanceof HTMLElement ? element : null
    if (!htmlElement) {
      return null
    }

    const padding = readSpotlightNumberAttr(htmlElement, 'data-yui-guide-spotlight-padding')
      ?? DEFAULT_SPOTLIGHT_PADDING
    const left = Math.max(0, Math.floor(rect.left - padding))
    const top = Math.max(0, Math.floor(rect.top - padding))
    const right = Math.min(window.innerWidth, Math.ceil(rect.right + padding))
    const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom + padding))
    const width = Math.max(0, right - left)
    const height = Math.max(0, bottom - top)

    let radius = 18
    try {
      const explicitRadius = readSpotlightNumberAttr(htmlElement, 'data-yui-guide-spotlight-radius')
      const parsedRadius = Number.isFinite(explicitRadius) && Number(explicitRadius) > 0
        ? Number(explicitRadius)
        : Number.parseFloat(window.getComputedStyle(htmlElement).borderTopLeftRadius || window.getComputedStyle(htmlElement).borderRadius || '')
      if (Number.isFinite(parsedRadius) && parsedRadius > 0) {
        radius = Math.max(MIN_SPOTLIGHT_RADIUS, parsedRadius + padding)
      }
    } catch (_) {}

    return {
      left,
      top,
      width,
      height,
      radius,
      padding,
    }
  }

  getHomeSkipForwardingTolerance(rect: ScreenRect) {
    const explicitTolerance = Number(rect.forwardingTolerance)
    if (Number.isFinite(explicitTolerance) && explicitTolerance >= 0) {
      return explicitTolerance
    }

    const coordinateSpace = String(rect.coordinateSpace || '').toLowerCase()
    const rawPadding = Number(rect.hitPadding)
    const basePadding = Number.isFinite(rawPadding) ? Math.max(0, rawPadding) : 0
    if (coordinateSpace === 'electron-window-bounds') {
      const platform = String(rect.platform || '').toLowerCase()
      if (platform === 'linux') return Math.max(8, Math.round(basePadding * 0.35))
      if (platform === 'macos') return Math.max(6, Math.round(basePadding * 0.25))
      return Math.max(4, Math.round(basePadding * 0.2))
    }
    return 6
  }

  forwardHomeSkipClick(event: PointerEvent | MouseEvent) {
    if (!this.running || !event || !this.activeSessionId) {
      return false
    }

    const rect = this.homeSkipButtonScreenRect
    if (!rect) {
      return false
    }

    const screenX = Number.isFinite(event.screenX) ? Number(event.screenX) : NaN
    const screenY = Number.isFinite(event.screenY) ? Number(event.screenY) : NaN
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
      return false
    }

    const tolerance = this.getHomeSkipForwardingTolerance(rect)
    if (
      screenX < rect.left - tolerance
      || screenX > rect.right + tolerance
      || screenY < rect.top - tolerance
      || screenY > rect.bottom + tolerance
    ) {
      return false
    }

    const now = Date.now()
    if (
      (now - this.lastForwardedSkipAt) < 700
      && Math.abs(screenX - this.lastForwardedSkipScreenX) <= 2
      && Math.abs(screenY - this.lastForwardedSkipScreenY) <= 2
    ) {
      if (typeof event.preventDefault === 'function') {
        event.preventDefault()
      }
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation()
      }
      if (typeof event.stopPropagation === 'function') {
        event.stopPropagation()
      }
      return true
    }

    if (typeof event.preventDefault === 'function') {
      event.preventDefault()
    }
    if (typeof event.stopImmediatePropagation === 'function') {
      event.stopImmediatePropagation()
    }
    if (typeof event.stopPropagation === 'function') {
      event.stopPropagation()
    }

    this.lastForwardedSkipAt = now
    this.lastForwardedSkipScreenX = screenX
    this.lastForwardedSkipScreenY = screenY
    this.notify(SKIP_REQUEST_EVENT, this.activeSessionId, {
      source: 'plugin_dashboard',
      screenX,
      screenY,
      coordinateSpace: rect.coordinateSpace || '',
      platform: rect.platform || '',
    })
    return true
  }

  syncBackdropViewport() {
    const width = Math.max(1, Math.round(window.innerWidth || 0))
    const height = Math.max(1, Math.round(window.innerHeight || 0))

    this.backdrop?.setAttribute('viewBox', `0 0 ${width} ${height}`)
    for (const rect of [this.backdropBase, this.backdropFill]) {
      if (!rect) {
        continue
      }
      rect.setAttribute('x', '0')
      rect.setAttribute('y', '0')
      rect.setAttribute('width', String(width))
      rect.setAttribute('height', String(height))
    }
  }

  updateBackdropCutout(spotlightRect: SpotlightRect | null) {
    if (!this.backdropCutout) {
      if (this.backdrop) {
        ;(this.backdrop as unknown as { hidden?: boolean }).hidden = true
        this.backdrop.style.display = 'none'
      }
      return
    }

    if (!spotlightRect) {
      ;(this.backdropCutout as unknown as { hidden?: boolean }).hidden = true
      this.backdropCutout.setAttribute('visibility', 'hidden')
      this.backdropCutout.setAttribute('x', '0')
      this.backdropCutout.setAttribute('y', '0')
      this.backdropCutout.setAttribute('width', '0')
      this.backdropCutout.setAttribute('height', '0')
      this.backdropCutout.setAttribute('rx', '0')
      this.backdropCutout.setAttribute('ry', '0')
      this.backdropCutout.style.display = 'none'
      return
    }

    ;(this.backdropCutout as unknown as { hidden?: boolean }).hidden = false
    this.backdropCutout.setAttribute('visibility', 'visible')
    this.backdropCutout.style.removeProperty('display')
    const maxInset = Math.max(0, spotlightRect.padding)
    const inset = Math.max(0, Math.min(
      BACKDROP_CUTOUT_INSET,
      maxInset,
      Math.floor(spotlightRect.width / 2),
      Math.floor(spotlightRect.height / 2),
    ))
    const x = spotlightRect.left + inset
    const y = spotlightRect.top + inset
    const width = Math.max(0, spotlightRect.width - (inset * 2))
    const height = Math.max(0, spotlightRect.height - (inset * 2))
    const radius = Math.max(0, spotlightRect.radius - inset)
    this.backdropCutout.setAttribute('x', String(x))
    this.backdropCutout.setAttribute('y', String(y))
    this.backdropCutout.setAttribute('width', String(width))
    this.backdropCutout.setAttribute('height', String(height))
    this.backdropCutout.setAttribute('rx', String(radius))
    this.backdropCutout.setAttribute('ry', String(radius))
  }

  setSpotlight(element: Element | null) {
    this.ensureRoot()
    if (!this.spotlight) {
      return
    }

    this.spotlightElement = element
    this.syncBackdropViewport()

    const rect = this.getSpotlightRect(element)
    if (!rect) {
      this.spotlight.hidden = true
      this.spotlight.classList.remove('is-visible')
      this.updateBackdropCutout(null)
      return
    }

    this.spotlight.hidden = false
    this.spotlight.style.left = `${rect.left}px`
    this.spotlight.style.top = `${rect.top}px`
    this.spotlight.style.width = `${rect.width}px`
    this.spotlight.style.height = `${rect.height}px`
    this.spotlight.style.borderRadius = `${rect.radius}px`
    this.spotlight.classList.add('is-visible')
    this.updateBackdropCutout(rect)
  }

  clearSpotlight() {
    this.spotlightElement = null
    if (this.spotlight) {
      this.spotlight.hidden = true
      this.spotlight.classList.remove('is-visible')
      this.spotlight.style.left = '0px'
      this.spotlight.style.top = '0px'
      this.spotlight.style.width = '0px'
      this.spotlight.style.height = '0px'
      this.spotlight.style.borderRadius = '0px'
    }
    this.updateBackdropCutout(null)
  }

  activateOverlayShell() {
    this.ensureRoot()
    document.documentElement.classList.add('yui-guide-plugin-dashboard-running')
    document.documentElement.classList.add('yui-taking-over')
    document.body.classList.add('yui-guide-plugin-dashboard-running')
    document.body.classList.add('yui-taking-over')
  }

  showCursor(x: number, y: number) {
    this.activateOverlayShell()
    if (!this.cursorShell) {
      return
    }

    const previous = this.cursorPosition
    const shouldGlide = !!(
      previous
      && this.cursorShell.classList.contains('is-visible')
    )
    this.cursorShell.classList.add('is-visible')
    this.cursorShell.style.transitionDuration = shouldGlide ? '360ms' : '0ms'
    this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
    this.cursorPosition = { x, y }
    this.lastCursorTarget = { x, y }
    this.cursorTrailLastPoint = shouldGlide && previous ? { x: previous.x, y: previous.y } : null
    this.cursorTrailLastAt = 0
  }

  getRenderedCursorPosition() {
    if (!this.cursorShell) {
      return this.cursorPosition
    }

    try {
      const transform = window.getComputedStyle(this.cursorShell).transform
      if (!transform || transform === 'none') {
        return this.cursorPosition
      }
      const matrix = new DOMMatrixReadOnly(transform)
      return {
        x: matrix.m41,
        y: matrix.m42,
      }
    } catch (_) {
      return this.cursorPosition
    }
  }

  cancelCursorMotion() {
    if (!this.cursorShell) {
      return
    }

    this.cursorMotionToken += 1
    this.cursorTransitionActive = false
    const position = this.getRenderedCursorPosition()
    if (!position) {
      return
    }

    this.cursorShell.style.transitionDuration = '0ms'
    this.cursorShell.style.transform = `translate(${Math.round(position.x)}px, ${Math.round(position.y)}px)`
    this.cursorPosition = position
  }

  moveCursor(
    x: number,
    y: number,
    durationMs = 480,
    isCurrent?: () => boolean,
    waitForSceneResume = true,
  ) {
    this.ensureRoot()
    if (!this.cursorShell) {
      return Promise.resolve(false)
    }

    if (!this.cursorPosition) {
      this.showCursor(x, y)
      return Promise.resolve(true)
    }

    const motionToken = ++this.cursorMotionToken
    this.cursorTransitionActive = true
    this.cursorShell.classList.add('is-visible')
    this.cursorShell.style.transitionDuration = `${Math.max(0, durationMs)}ms`
    const startPosition = this.getRenderedCursorPosition() || this.cursorPosition
    this.cursorTrailLastPoint = startPosition ? { x: startPosition.x, y: startPosition.y } : null
    this.cursorTrailLastAt = 0

    return new Promise<boolean>((resolve) => {
      let settled = false
      let trailFrame = 0
      const finish = (completed: boolean) => {
        if (settled) {
          return
        }
        settled = true
        if (trailFrame) {
          window.cancelAnimationFrame(trailFrame)
          trailFrame = 0
        }
        this.cursorShell?.removeEventListener('transitionend', handleEnd)
        const finalize = async () => {
          if (motionToken === this.cursorMotionToken) {
            this.cursorTransitionActive = false
          }
          if (
            waitForSceneResume
            && this.scenePausedForResistance
            && (!isCurrent || isCurrent())
          ) {
            await this.waitUntilSceneResumed()
          }
          resolve(completed && motionToken === this.cursorMotionToken)
        }
        void finalize()
      }
      const handleEnd = (event: Event) => {
        if (event.target === this.cursorShell) {
          finish(true)
        }
      }
      const sampleTrail = (now: number) => {
        if (settled || motionToken !== this.cursorMotionToken || !this.cursorShell) {
          return
        }
        const position = this.getRenderedCursorPosition()
        const previous = this.cursorTrailLastPoint || startPosition
        if (position && previous) {
          this.maybeSpawnCursorTrail(position.x, position.y, previous.x, previous.y, now)
        }
        trailFrame = window.requestAnimationFrame(sampleTrail)
      }

      this.cursorShell?.addEventListener('transitionend', handleEnd)
      trailFrame = window.requestAnimationFrame(sampleTrail)
      window.requestAnimationFrame(() => {
        if (motionToken !== this.cursorMotionToken) {
          finish(false)
          return
        }
        if (isCurrent && !isCurrent()) {
          finish(false)
          return
        }
        if (waitForSceneResume && this.scenePausedForResistance) {
          finish(false)
          return
        }
        if (this.cursorShell) {
          this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
        }
      })
      window.setTimeout(() => finish(true), durationMs + 80)
      this.cursorPosition = { x, y }
      this.lastCursorTarget = { x, y }
    })
  }

  async moveCursorToElement(element: Element | null, durationMs = 480, isCurrent?: () => boolean) {
    const rect = this.getRect(element)
    if (!rect) {
      return false
    }

    return this.moveCursor(rect.left + rect.width / 2, rect.top + rect.height / 2, durationMs, isCurrent)
  }

  async moveCursorToElementWithRecovery(element: Element | null, durationMs = 480, isCurrent?: () => boolean) {
    while (!isCurrent || isCurrent()) {
      const moved = await this.moveCursorToElement(element, durationMs, isCurrent)
      if (moved) {
        return true
      }
      if (this.scenePausedForResistance) {
        await this.waitUntilSceneResumed()
        continue
      }
      return false
    }

    return false
  }

  removeCursorTrailEntry(entry: { element: HTMLSpanElement; timer: number }) {
    window.clearTimeout(entry.timer)
    if (entry.element.parentNode) {
      entry.element.parentNode.removeChild(entry.element)
    }
    this.activeTrailParticles.delete(entry)
  }

  trimCursorTrailParticles() {
    while (this.activeTrailParticles.size > CURSOR_TRAIL_MAX_PARTICLES) {
      const first = this.activeTrailParticles.values().next().value
      if (!first) {
        return
      }
      this.removeCursorTrailEntry(first)
    }
  }

  clearCursorTrailParticles() {
    if (this.cursorTrailDecayFrame) {
      window.cancelAnimationFrame(this.cursorTrailDecayFrame)
      this.cursorTrailDecayFrame = 0
    }

    if (this.activeTrailParticles.size) {
      Array.from(this.activeTrailParticles).forEach((entry) => {
        this.removeCursorTrailEntry(entry)
      })
    }

    this.cursorTrailPoints = []
    this.cursorTrailLastPoint = null
    this.cursorTrailLastAt = 0
    this.cursorTrailSvg?.classList.remove('is-visible')
    this.cursorTrailBody?.setAttribute('d', '')
    this.cursorTrailCore?.setAttribute('d', '')
  }

  spawnCursorTrailParticle(x: number, y: number, angle: number) {
    if (!this.root || shouldReduceMotion()) {
      return
    }

    const particle = document.createElement('span')
    const width = 7 + Math.random() * 5
    const opacity = 0.1 + Math.random() * 0.12
    const drift = 10 + Math.random() * 16
    const sideJitter = (Math.random() - 0.5) * 20
    const backOffset = 22 + Math.random() * 20
    const cos = Math.cos(angle)
    const sin = Math.sin(angle)
    const baseX = x - (cos * backOffset) - (sin * sideJitter)
    const baseY = y - (sin * backOffset) + (cos * sideJitter)

    particle.className = 'yui-guide-plugin-cursor-trail is-icon'
    particle.setAttribute('aria-hidden', 'true')
    particle.style.left = `${baseX.toFixed(2)}px`
    particle.style.top = `${baseY.toFixed(2)}px`
    particle.style.setProperty('--trail-width', `${width.toFixed(2)}px`)
    particle.style.setProperty('--trail-height', `${width.toFixed(2)}px`)
    particle.style.setProperty('--trail-angle', `${((angle * 180) / Math.PI).toFixed(2)}deg`)
    particle.style.setProperty('--trail-drift-x', `${(-cos * drift).toFixed(2)}px`)
    particle.style.setProperty('--trail-drift-y', `${(-sin * drift).toFixed(2)}px`)
    particle.style.setProperty('--trail-opacity', opacity.toFixed(2))
    particle.style.setProperty('--trail-brightness', (0.78 + Math.random() * 0.2).toFixed(2))

    const iconUrl = CURSOR_TRAIL_ICON_URLS[Math.floor(Math.random() * CURSOR_TRAIL_ICON_URLS.length)]
    particle.style.setProperty('--trail-icon', `url("${iconUrl}")`)

    const entry: { element: HTMLSpanElement; timer: number } = {
      element: particle,
      timer: 0,
    }
    entry.timer = window.setTimeout(() => {
      this.removeCursorTrailEntry(entry)
    }, CURSOR_TRAIL_PARTICLE_LIFETIME_MS + 120)

    this.activeTrailParticles.add(entry)
    this.root.appendChild(particle)
    this.trimCursorTrailParticles()
  }

  getCursorTrailNow(now?: number) {
    if (Number.isFinite(now)) {
      return Number(now)
    }
    if (window.performance && typeof window.performance.now === 'function') {
      return window.performance.now()
    }
    return Date.now()
  }

  syncCursorTrailViewport() {
    if (!this.cursorTrailSvg) {
      return
    }
    const width = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1)
    const height = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1)
    this.cursorTrailSvg.setAttribute('viewBox', `0 0 ${width} ${height}`)
  }

  trimCursorTrailPoints(now: number) {
    const cutoff = now - CURSOR_TRAIL_PARTICLE_LIFETIME_MS
    this.cursorTrailPoints = this.cursorTrailPoints
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y) && point.t >= cutoff)

    if (this.cursorTrailPoints.length > CURSOR_TRAIL_MAX_POINTS) {
      this.cursorTrailPoints = this.cursorTrailPoints.slice(this.cursorTrailPoints.length - CURSOR_TRAIL_MAX_POINTS)
    }
  }

  formatCursorTrailPoint(point: { x: number; y: number }) {
    return `${point.x.toFixed(1)} ${point.y.toFixed(1)}`
  }

  appendSmoothCursorTrailPath(points: Array<{ x: number; y: number }>, useMove: boolean) {
    if (!points.length) {
      return ''
    }

    let path = `${useMove ? 'M' : 'L'} ${this.formatCursorTrailPoint(points[0]!)}`
    if (points.length === 1) {
      return path
    }

    for (let index = 1; index < points.length - 1; index += 1) {
      const current = points[index]!
      const next = points[index + 1]!
      const mid = {
        x: (current.x + next.x) / 2,
        y: (current.y + next.y) / 2,
      }
      path += ` Q ${this.formatCursorTrailPoint(current)} ${this.formatCursorTrailPoint(mid)}`
    }

    path += ` L ${this.formatCursorTrailPoint(points[points.length - 1]!)}`
    return path
  }

  buildCursorTrailRibbonPath(points: Array<{ x: number; y: number }>, headWidth: number, tailWidth: number) {
    if (points.length < 2) {
      return ''
    }

    const left: Array<{ x: number; y: number }> = []
    const right: Array<{ x: number; y: number }> = []
    const count = points.length

    for (let index = 0; index < count; index += 1) {
      const point = points[index]!
      const previous = points[Math.max(0, index - 1)]!
      const next = points[Math.min(count - 1, index + 1)]!
      let dx = next.x - previous.x
      let dy = next.y - previous.y
      let length = Math.hypot(dx, dy)

      if (length < 0.001 && index > 0) {
        dx = point.x - points[index - 1]!.x
        dy = point.y - points[index - 1]!.y
        length = Math.hypot(dx, dy)
      }
      if (length < 0.001) {
        dx = 1
        dy = 0
        length = 1
      }

      const progress = count <= 1 ? 1 : index / (count - 1)
      const eased = progress * progress * (3 - (2 * progress))
      const width = tailWidth + ((headWidth - tailWidth) * eased)
      const normalX = -dy / length
      const normalY = dx / length
      const halfWidth = width / 2

      left.push({
        x: point.x + (normalX * halfWidth),
        y: point.y + (normalY * halfWidth),
      })
      right.push({
        x: point.x - (normalX * halfWidth),
        y: point.y - (normalY * halfWidth),
      })
    }

    return `${this.appendSmoothCursorTrailPath(left, true)} ${this.appendSmoothCursorTrailPath(right.slice().reverse(), false)} Z`
  }

  updateCursorTrail(now?: number) {
    if (!this.root || shouldReduceMotion()) {
      this.clearCursorTrailParticles()
      return
    }

    if (!this.cursorTrailSvg || !this.cursorTrailBody || !this.cursorTrailCore) {
      const layer = this.createCursorTrailLayer()
      if (this.cursorShell) {
        this.root.insertBefore(layer, this.cursorShell)
      } else {
        this.root.appendChild(layer)
      }
    }

    const currentNow = this.getCursorTrailNow(now)
    this.syncCursorTrailViewport()
    this.trimCursorTrailPoints(currentNow)

    if (this.cursorTrailPoints.length < 2) {
      this.cursorTrailSvg?.classList.remove('is-visible')
      this.cursorTrailBody?.setAttribute('d', '')
      this.cursorTrailCore?.setAttribute('d', '')
      return
    }

    const points = this.cursorTrailPoints
    const tail = points[0]!
    const head = points[points.length - 1]!
    const bodyPath = this.buildCursorTrailRibbonPath(
      points,
      CURSOR_TRAIL_BODY_HEAD_WIDTH,
      CURSOR_TRAIL_BODY_TAIL_WIDTH,
    )
    const corePath = this.buildCursorTrailRibbonPath(
      points,
      CURSOR_TRAIL_CORE_HEAD_WIDTH,
      CURSOR_TRAIL_CORE_TAIL_WIDTH,
    )

    this.cursorTrailGradient?.setAttribute('x1', tail.x.toFixed(1))
    this.cursorTrailGradient?.setAttribute('y1', tail.y.toFixed(1))
    this.cursorTrailGradient?.setAttribute('x2', head.x.toFixed(1))
    this.cursorTrailGradient?.setAttribute('y2', head.y.toFixed(1))
    this.cursorTrailBody?.setAttribute('d', bodyPath)
    this.cursorTrailCore?.setAttribute('d', corePath)
    this.cursorTrailHead?.setAttribute('cx', head.x.toFixed(1))
    this.cursorTrailHead?.setAttribute('cy', head.y.toFixed(1))
    this.cursorTrailHead?.setAttribute('r', String(CURSOR_TRAIL_HEAD_RADIUS))
    this.cursorTrailHeadCore?.setAttribute('cx', head.x.toFixed(1))
    this.cursorTrailHeadCore?.setAttribute('cy', head.y.toFixed(1))
    this.cursorTrailHeadCore?.setAttribute('r', '3.8')
    this.cursorTrailSvg?.classList.add('is-visible')
  }

  scheduleCursorTrailDecay() {
    if (this.cursorTrailDecayFrame || shouldReduceMotion()) {
      return
    }

    const tick = (now: number) => {
      this.cursorTrailDecayFrame = 0
      this.updateCursorTrail(now)
      if (this.cursorTrailPoints.length) {
        this.cursorTrailDecayFrame = window.requestAnimationFrame(tick)
      }
    }

    this.cursorTrailDecayFrame = window.requestAnimationFrame(tick)
  }

  maybeSpawnCursorTrail(x: number, y: number, previousX: number, previousY: number, now: number) {
    if (shouldReduceMotion()) {
      return
    }

    const dx = x - previousX
    const dy = y - previousY
    if (Math.hypot(dx, dy) < 0.6) {
      return
    }

    const currentNow = this.getCursorTrailNow(now)
    const lastPoint = this.cursorTrailLastPoint
    const elapsedMs = Number.isFinite(currentNow) && Number.isFinite(this.cursorTrailLastAt)
      ? currentNow - this.cursorTrailLastAt
      : CURSOR_TRAIL_MIN_INTERVAL_MS
    const distanceFromLast = lastPoint
      ? Math.hypot(x - lastPoint.x, y - lastPoint.y)
      : CURSOR_TRAIL_MIN_DISTANCE

    if (distanceFromLast < CURSOR_TRAIL_MIN_DISTANCE && elapsedMs < CURSOR_TRAIL_MIN_INTERVAL_MS) {
      return
    }

    const startPoint = lastPoint
      ? {
          x: lastPoint.x,
          y: lastPoint.y,
          t: Number.isFinite(lastPoint.t) ? Number(lastPoint.t) : Math.max(0, currentNow - 16),
        }
      : {
          x: previousX,
          y: previousY,
          t: Math.max(0, currentNow - 16),
        }
    if (!lastPoint || this.cursorTrailPoints.length === 0) {
      this.cursorTrailPoints.push(startPoint)
    }

    const distance = Math.hypot(x - startPoint.x, y - startPoint.y)
    const segmentCount = Math.max(
      1,
      Math.min(CURSOR_TRAIL_MAX_SEGMENTS_PER_FRAME, Math.ceil(distance / CURSOR_TRAIL_SEGMENT_SPACING)),
    )
    const startTime = Number.isFinite(startPoint.t) ? Number(startPoint.t) : currentNow - 16

    for (let index = 1; index <= segmentCount; index += 1) {
      const ratio = index / segmentCount
      this.cursorTrailPoints.push({
        x: startPoint.x + ((x - startPoint.x) * ratio),
        y: startPoint.y + ((y - startPoint.y) * ratio),
        t: startTime + ((currentNow - startTime) * ratio),
      })
    }

    this.cursorTrailLastPoint = { x, y, t: currentNow }
    this.cursorTrailLastAt = currentNow
    this.updateCursorTrail(currentNow)
    this.scheduleCursorTrailDecay()

    if (Math.random() < CURSOR_TRAIL_ICON_CHANCE && distance > 16) {
      this.spawnCursorTrailParticle(x, y, Math.atan2(dy, dx))
    }
  }

  clickCursor(durationMs = DEFAULT_CURSOR_CLICK_VISIBLE_MS) {
    if (!this.cursorInner) {
      return
    }

    const visibleMs = Number.isFinite(durationMs)
      ? Math.max(DEFAULT_CURSOR_CLICK_VISIBLE_MS, Math.round(durationMs))
      : DEFAULT_CURSOR_CLICK_VISIBLE_MS
    if (this.cursorClickTimer !== null) {
      window.clearTimeout(this.cursorClickTimer)
      this.cursorClickTimer = null
    }
    if (this.cursorShell) {
      this.cursorShell.classList.add('is-visible')
    }
    this.cursorInner.classList.remove('is-clicking')
    void this.cursorInner.offsetWidth
    this.cursorInner.classList.add('is-clicking')
    this.spawnCursorClickStars()
    this.cursorClickTimer = window.setTimeout(() => {
      this.cursorClickTimer = null
      this.cursorInner?.classList.remove('is-clicking')
    }, visibleMs)
  }

  clearCursorClickStars() {
    if (!this.activeClickStars.size) {
      return
    }

    this.activeClickStars.forEach((entry) => {
      window.clearTimeout(entry.timer)
      if (entry.element.parentNode) {
        entry.element.parentNode.removeChild(entry.element)
      }
    })
    this.activeClickStars.clear()
  }

  spawnCursorClickStars() {
    if (!this.cursorShell || shouldReduceMotion()) {
      return
    }

    const fragment = document.createDocumentFragment()
    for (let index = 0; index < CURSOR_CLICK_STAR_COUNT; index += 1) {
      const angle = ((Math.PI * 2) * (index / CURSOR_CLICK_STAR_COUNT)) + ((Math.random() - 0.5) * 0.92)
      const distance = 24 + Math.random() * 30
      const size = 5 + Math.random() * 5
      const x = Math.cos(angle) * distance
      const y = Math.sin(angle) * distance
      const star = document.createElement('span')
      star.className = 'yui-guide-plugin-click-star'
      star.setAttribute('aria-hidden', 'true')
      star.style.setProperty('--star-x', `${x.toFixed(2)}px`)
      star.style.setProperty('--star-y', `${y.toFixed(2)}px`)
      star.style.setProperty('--star-mid-x', `${(x * 0.76).toFixed(2)}px`)
      star.style.setProperty('--star-mid-y', `${(y * 0.76).toFixed(2)}px`)
      star.style.setProperty('--star-size', `${size.toFixed(2)}px`)
      star.style.setProperty('--star-rotate', `${Math.round(Math.random() * 180)}deg`)
      star.style.setProperty('--star-delay', `${Math.round(Math.random() * 60)}ms`)
      star.style.setProperty('--star-hue', String(Math.round(36 + Math.random() * 28)))
      fragment.appendChild(star)

      const entry = {
        element: star,
        timer: window.setTimeout(() => {
          if (star.parentNode) {
            star.parentNode.removeChild(star)
          }
          this.activeClickStars.delete(entry)
        }, CURSOR_CLICK_STAR_LIFETIME_MS + 120),
      }
      this.activeClickStars.add(entry)
    }

    this.cursorShell.appendChild(fragment)
  }

  resetCursorVisualState() {
    if (this.cursorClickTimer !== null) {
      window.clearTimeout(this.cursorClickTimer)
      this.cursorClickTimer = null
    }
    this.clearCursorClickStars()
    this.clearCursorTrailParticles()
    if (!this.cursorInner) {
      return
    }
    this.cursorInner.classList.remove('is-clicking')
  }

  async animateScroll(container: HTMLElement, deltaY: number, durationMs: number, isCurrent?: () => boolean) {
    const startedAt = performance.now()
    const initialTop = container.scrollTop
    const targetTop = initialTop + deltaY
    let pausedAt: number | null = null
    let pausedDurationMs = 0

    return new Promise<void>((resolve) => {
      const tick = (now: number) => {
        if (isCurrent && !isCurrent()) {
          resolve()
          return
        }
        if (this.scenePausedForResistance) {
          if (pausedAt === null) {
            pausedAt = now
          }
          window.requestAnimationFrame(tick)
          return
        }
        if (pausedAt !== null) {
          pausedDurationMs += now - pausedAt
          pausedAt = null
        }
        const progress = clamp((now - startedAt - pausedDurationMs) / durationMs, 0, 1)
        container.scrollTop = initialTop + ((targetTop - initialTop) * progress)
        if (progress >= 1) {
          resolve()
          return
        }
        window.requestAnimationFrame(tick)
      }

      window.requestAnimationFrame(tick)
    })
  }

  async runEllipse(container: HTMLElement, durationMs: number, isCurrent?: () => boolean) {
    const rect = this.getRect(container)
    if (!rect) {
      return
    }

    const centerX = rect.left + rect.width * 0.55
    const centerY = rect.top + rect.height * 0.42
    const radiusX = Math.min(440, rect.width * 0.72)
    const radiusY = Math.min(224, rect.height * 0.4)
    const startX = centerX + radiusX
    const startY = centerY
    let ellipseMotionDurationMs = durationMs
    if (this.cursorPosition && Math.hypot(startX - this.cursorPosition.x, startY - this.cursorPosition.y) > 2) {
      const prepareMoveDurationMs = Math.min(
        Math.max(0, durationMs - 360),
        Math.min(1400, Math.max(700, Math.round(durationMs * 0.3))),
      )
      const prepared = await this.moveCursor(
        startX,
        startY,
        prepareMoveDurationMs,
        isCurrent,
      )
      if (!prepared || (isCurrent && !isCurrent())) {
        return
      }
      ellipseMotionDurationMs = Math.max(0, durationMs - prepareMoveDurationMs)
    } else if (!this.cursorPosition) {
      this.showCursor(startX, startY)
    }
    if (ellipseMotionDurationMs <= 0) {
      return
    }

    const startedAt = performance.now()
    let pausedAt: number | null = null
    let pausedDurationMs = 0
    const motionToken = ++this.cursorMotionToken
    this.cursorTransitionActive = true
    this.cursorTrailLastPoint = this.cursorPosition
      ? { x: this.cursorPosition.x, y: this.cursorPosition.y }
      : null
    this.cursorTrailLastAt = 0

    try {
      await new Promise<void>((resolve) => {
        const tick = (now: number) => {
          if (motionToken !== this.cursorMotionToken || (isCurrent && !isCurrent())) {
            resolve()
            return
          }
          if (this.scenePausedForResistance) {
            if (pausedAt === null) {
              pausedAt = now
            }
            window.requestAnimationFrame(tick)
            return
          }
          if (pausedAt !== null) {
            pausedDurationMs += now - pausedAt
            pausedAt = null
          }
          const progress = clamp((now - startedAt - pausedDurationMs) / ellipseMotionDurationMs, 0, 1)
          const angle = progress * Math.PI * 2
          const x = centerX + Math.cos(angle) * radiusX
          const y = centerY + Math.sin(angle) * radiusY
          const previousX = this.cursorPosition ? this.cursorPosition.x : x
          const previousY = this.cursorPosition ? this.cursorPosition.y : y
          if (this.cursorShell) {
            this.cursorShell.style.transitionDuration = '80ms'
            this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
            this.cursorPosition = { x, y }
            this.lastCursorTarget = { x, y }
            this.maybeSpawnCursorTrail(x, y, previousX, previousY, now)
          }

          if (progress >= 1) {
            resolve()
            return
          }
          window.requestAnimationFrame(tick)
        }

        window.requestAnimationFrame(tick)
      })
    } finally {
      if (motionToken === this.cursorMotionToken) {
        this.cursorTransitionActive = false
      }
    }
  }

  async speakLine(
    text: string,
    options?: {
      voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
      audioUrl?: string
      startAtMs?: number
    },
  ) {
    await speakTextWithPromise(text, options)
  }

  pauseCurrentSceneForResistance() {
    if (this.scenePausedForResistance) {
      return
    }
    this.scenePausedForResistance = true
    this.cancelCursorMotion()
    this.scriptedMotionInterruptDistance = 0
    this.scriptedMotionInterruptWindowStartedAt = 0
  }

  resumeCurrentSceneAfterResistance() {
    if (!this.scenePausedForResistance) {
      return
    }
    this.scenePausedForResistance = false
    const resolvers = this.scenePauseResolvers.slice()
    this.scenePauseResolvers = []
    resolvers.forEach((resolve) => {
      try {
        resolve()
      } catch (_) {}
    })
  }

  waitUntilSceneResumed() {
    if (!this.scenePausedForResistance) {
      return Promise.resolve()
    }
    return new Promise<void>((resolve) => {
      this.scenePauseResolvers.push(resolve)
    })
  }

  markHomeNarrationFinished(sessionId: string) {
    if (!this.isCurrentRun(sessionId) || this.homeNarrationFinished) {
      return
    }

    this.homeNarrationFinished = true
    const resolvers = this.homeNarrationResolvers.slice()
    this.homeNarrationResolvers = []
    resolvers.forEach((resolve) => {
      try {
        resolve()
      } catch (_) {}
    })
  }

  waitForHomeNarrationFinished(sessionId: string, isCurrent?: () => boolean) {
    if (this.homeNarrationFinished) {
      return Promise.resolve(true)
    }

    return new Promise<boolean>((resolve) => {
      this.homeNarrationResolvers.push(() => {
        resolve(!isCurrent || isCurrent())
      })
    })
  }

  clearNarrationResumeTimer() {
    if (this.narrationResumeTimer !== null) {
      window.clearTimeout(this.narrationResumeTimer)
      this.narrationResumeTimer = null
    }
  }

  cancelActiveNarration() {
    this.clearNarrationResumeTimer()
    const narration = this.activeNarration
    if (!narration) {
      stopCurrentGuideSpeech()
      return
    }

    narration.cancelled = true
    narration.interrupted = false
    this.activeNarration = null
    stopCurrentGuideSpeech()
    try {
      narration.resolve()
    } catch (_) {}
  }

  playNarration(narration: ActiveNarration) {
    const playVersion = narration.playVersion + 1
    narration.playVersion = playVersion

    void this.speakLine(narration.text, {
      voiceKey: narration.voiceKey,
      audioUrl: narration.audioUrl,
      startAtMs: narration.resumeAudioOffsetMs,
    }).then(() => {
      if (
        this.activeNarration !== narration
        || narration.cancelled
        || narration.playVersion !== playVersion
      ) {
        return
      }
      if (narration.interrupted) {
        return
      }

      narration.resumeAudioOffsetMs = 0
      this.activeNarration = null
      try {
        narration.resolve()
      } catch (_) {}
    }).catch(() => {
      if (this.activeNarration !== narration || narration.cancelled) {
        return
      }

      this.activeNarration = null
      try {
        narration.resolve()
      } catch (_) {}
    })
  }

  startNarration(
    text: string,
    options?: {
      voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
      audioUrl?: string
    },
  ) {
    const content = typeof text === 'string' ? text.trim() : ''
    if (!content) {
      return Promise.resolve()
    }

    this.cancelActiveNarration()
    return new Promise<void>((resolve) => {
      const narration: ActiveNarration = {
        text: content,
        voiceKey: options?.voiceKey,
        audioUrl: options?.audioUrl,
        resumeAudioOffsetMs: 0,
        interrupted: false,
        cancelled: false,
        playVersion: 0,
        resolve,
      }
      this.activeNarration = narration
      this.playNarration(narration)
    })
  }

  interruptNarrationForResistance() {
    const narration = this.activeNarration
    if (!narration || narration.cancelled) {
      if (!currentGuideAudio && !currentGuideSpeechStop) {
        return false
      }

      this.clearNarrationResumeTimer()
      stopCurrentGuideSpeech()
      return true
    }
    if (narration.interrupted) {
      return true
    }

    narration.resumeAudioOffsetMs = currentGuideAudio && Number.isFinite(currentGuideAudio.currentTime)
      ? Math.max(0, Math.round(currentGuideAudio.currentTime * 1000))
      : 0
    narration.interrupted = true
    this.clearNarrationResumeTimer()
    stopCurrentGuideSpeech()
    return true
  }

  scheduleNarrationResume() {
    this.clearNarrationResumeTimer()

    const attemptResume = () => {
      const narration = this.activeNarration
      if (
        !narration
        || narration.cancelled
        || !narration.interrupted
        || !this.running
        || this.angryExitTriggered
      ) {
        return
      }

      const lastMotionAt = this.lastPointerPoint && Number.isFinite(this.lastPointerPoint.t)
        ? this.lastPointerPoint.t
        : 0
      if ((Date.now() - lastMotionAt) < 720) {
        this.narrationResumeTimer = window.setTimeout(attemptResume, 240)
        return
      }

      narration.interrupted = false
      this.playNarration(narration)
    }

    this.narrationResumeTimer = window.setTimeout(attemptResume, 720)
  }

  async waitForSceneDelay(delayMs: number, isCurrent?: () => boolean) {
    const totalMs = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 0
    if (totalMs <= 0) {
      return true
    }

    let remainingMs = totalMs
    let lastTickAt = Date.now()
    while (remainingMs > 0) {
      if (isCurrent && !isCurrent()) {
        return false
      }
      if (this.scenePausedForResistance) {
        await this.waitUntilSceneResumed()
        lastTickAt = Date.now()
        continue
      }

      const sliceMs = Math.min(remainingMs, 80)
      await wait(sliceMs)
      const now = Date.now()
      remainingMs = Math.max(0, remainingMs - (now - lastTickAt))
      lastTickAt = now
    }

    return !isCurrent || isCurrent()
  }

  setAngryVisual(isAngry: boolean) {
    this.root?.classList.toggle('is-angry', isAngry)
  }

  maybePlayPassiveResistance(x: number, y: number, distance: number, speed: number, now: number) {
    if (this.cursorReactionInFlight || this.cursorTransitionActive) {
      return
    }
    if (distance < DEFAULT_PASSIVE_RESISTANCE_DISTANCE) {
      return
    }
    if (speed < DEFAULT_PASSIVE_RESISTANCE_SPEED_THRESHOLD) {
      return
    }
    if ((now - this.lastPassiveResistanceAt) < DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS) {
      return
    }
    this.lastPassiveResistanceAt = now
    void this.reactAwayFromUser(x, y)
  }

  async reactAwayFromUser(userX: number, userY: number) {
    if (this.cursorReactionInFlight) {
      return
    }
    const current = this.cursorPosition
    if (!current) {
      return
    }
    this.cursorReactionInFlight = true
    const dx = userX - current.x
    const dy = userY - current.y
    const distance = Math.max(1, Math.hypot(dx, dy))
    const reactionDistance = clamp(distance * 0.12, 6, 18)
    const targetX = current.x - ((dx / distance) * reactionDistance)
    const targetY = current.y - ((dy / distance) * reactionDistance)
    const returnTarget = this.lastCursorTarget || current

    try {
      await this.moveCursor(targetX, targetY, 80, undefined, false)
      if (!this.running || this.angryExitTriggered) {
        return
      }
      await this.moveCursor(returnTarget.x, returnTarget.y, 180, undefined, false)
    } finally {
      this.cursorReactionInFlight = false
    }
  }

  async resistTo(userX: number, userY: number) {
    const current = this.cursorPosition
    if (!current) {
      return
    }
    const dx = userX - current.x
    const dy = userY - current.y
    const distance = Math.max(1, Math.hypot(dx, dy))
    const pullDistance = clamp(distance * 0.22, 12, 36)
    const pullX = current.x + ((dx / distance) * pullDistance)
    const pullY = current.y + ((dy / distance) * pullDistance)
    const returnTarget = this.lastCursorTarget || current

    await this.moveCursor(pullX, pullY, 120, undefined, false)
    this.clickCursor()
    if (!this.running || this.angryExitTriggered) {
      return
    }
    await this.moveCursor(returnTarget.x, returnTarget.y, 260, undefined, false)
  }

  onPointerDown(event: MouseEvent) {
    if (!event) {
      return
    }
    const x = Number.isFinite(event.clientX) ? event.clientX : null
    const y = Number.isFinite(event.clientY) ? event.clientY : null
    if (x === null || y === null) {
      return
    }
    this.lastPointerPoint = {
      x,
      y,
      t: Date.now(),
      speed: 0,
    }
    this.interruptAccelerationStreak = 0
    this.scriptedMotionInterruptDistance = 0
    this.scriptedMotionInterruptWindowStartedAt = 0
  }

  handleInterrupt(event: MouseEvent) {
    if (
      !this.running
      || this.angryExitTriggered
      || this.scenePausedForResistance
      || !this.interruptsEnabled
      || !event
    ) {
      return
    }

    const x = Number.isFinite(event.clientX) ? event.clientX : null
    const y = Number.isFinite(event.clientY) ? event.clientY : null
    if (x === null || y === null) {
      return
    }

    if (!document.body.classList.contains('yui-taking-over')) {
      return
    }

    if (typeof document.hasFocus === 'function' && !document.hasFocus()) {
      return
    }

    if (event.type === 'mousemove') {
      const movementX = Number.isFinite(event.movementX) ? event.movementX : null
      const movementY = Number.isFinite(event.movementY) ? event.movementY : null
      if (movementX !== null && movementY !== null && Math.hypot(movementX, movementY) <= 0) {
        return
      }
    }

    const now = Date.now()
    const previousPoint = this.lastPointerPoint
    if (!previousPoint || !Number.isFinite(previousPoint.t)) {
      this.lastPointerPoint = { x, y, t: now, speed: 0 }
      this.interruptAccelerationStreak = 0
      return
    }

    const dx = x - previousPoint.x
    const dy = y - previousPoint.y
    const distance = Math.hypot(dx, dy)
    const dt = Math.max(1, now - previousPoint.t)
    const speed = distance / dt
    const previousSpeed = Number.isFinite(previousPoint.speed) ? previousPoint.speed : 0
    const acceleration = (speed - previousSpeed) / dt

    this.lastPointerPoint = { x, y, t: now, speed }
    this.maybePlayPassiveResistance(x, y, distance, speed, now)

    const isScriptedMotionInterrupt = this.cursorTransitionActive
    let effectiveDistance = distance
    if (isScriptedMotionInterrupt && distance < DEFAULT_INTERRUPT_DISTANCE) {
      if (
        this.scriptedMotionInterruptWindowStartedAt <= 0
        || (now - this.scriptedMotionInterruptWindowStartedAt) > SCRIPTED_MOTION_INTERRUPT_WINDOW_MS
      ) {
        this.scriptedMotionInterruptWindowStartedAt = now
        this.scriptedMotionInterruptDistance = 0
      }
      this.scriptedMotionInterruptDistance += distance
      effectiveDistance = this.scriptedMotionInterruptDistance
    }

    if (effectiveDistance < DEFAULT_INTERRUPT_DISTANCE) {
      this.interruptAccelerationStreak = 0
      return
    }
    this.scriptedMotionInterruptDistance = 0
    this.scriptedMotionInterruptWindowStartedAt = 0

    if (speed < DEFAULT_INTERRUPT_SPEED_THRESHOLD) {
      this.interruptAccelerationStreak = 0
      return
    }
    if (!isScriptedMotionInterrupt && acceleration < DEFAULT_INTERRUPT_ACCELERATION_THRESHOLD) {
      this.interruptAccelerationStreak = 0
      return
    }

    this.interruptAccelerationStreak += 1
    const requiredStreak = isScriptedMotionInterrupt
      ? SCRIPTED_MOTION_INTERRUPT_STREAK
      : DEFAULT_INTERRUPT_ACCELERATION_STREAK
    if (this.interruptAccelerationStreak < requiredStreak) {
      return
    }
    this.interruptAccelerationStreak = 0

    if ((now - this.lastInterruptAt) < DEFAULT_INTERRUPT_THROTTLE_MS) {
      return
    }
    this.lastInterruptAt = now
    this.interruptCount += 1
    this.cancelCursorMotion()

    if (this.interruptCount >= 3) {
      void this.abortAsAngryExit()
      return
    }

    void this.playLightResistance(x, y)
  }

  revealRealCursorTemporarily() {
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
    }
    document.documentElement.classList.add('yui-resistance-cursor-reveal')
    document.body.classList.add('yui-resistance-cursor-reveal')
    this.resistanceCursorTimer = window.setTimeout(() => {
      this.resistanceCursorTimer = null
      document.documentElement.classList.remove('yui-resistance-cursor-reveal')
      document.body.classList.remove('yui-resistance-cursor-reveal')
    }, DEFAULT_RESISTANCE_CURSOR_REVEAL_MS)
  }

  async playLightResistance(x: number, y: number) {
    if (this.scenePausedForResistance || this.angryExitTriggered) {
      return
    }

    const sessionAtStart = this.activeSessionId
    const isSameSession = () => this.running && this.activeSessionId === sessionAtStart

    this.pauseCurrentSceneForResistance()
    this.interruptNarrationForResistance()
    this.revealRealCursorTemporarily()

    const voiceIndex = Math.min(RESISTANCE_VOICE_KEYS.length - 1, Math.max(0, this.interruptCount - 1))
    const line = RESISTANCE_LINES[voiceIndex] || RESISTANCE_LINES[0]
    const voiceKey = RESISTANCE_VOICE_KEYS[voiceIndex] || RESISTANCE_VOICE_KEYS[0]
    const textKey = resolveResistanceTextKey(this.interruptCount)
    const resistanceMotionPromise = this.resistTo(x, y)
    const handledByHome = await this.requestHomeInterruptPlayback({
      kind: 'interrupt_resist_light',
      text: line,
      textKey,
      voiceKey,
      interruptCount: this.interruptCount,
      x,
      y,
    })
    if (!isSameSession()) {
      return
    }
    if (!handledByHome) {
      await this.speakLine(line, { voiceKey })
      if (!isSameSession()) {
        return
      }
    }
    await resistanceMotionPromise.catch(() => {})
    if (!isSameSession()) {
      return
    }
    this.resumeCurrentSceneAfterResistance()
    if (this.activeNarration?.interrupted) {
      this.scheduleNarrationResume()
    }
  }

  async abortAsAngryExit() {
    if (this.angryExitTriggered || !this.running) {
      return
    }

    const sessionAtStart = this.activeSessionId
    const isSameSession = () => this.running && this.activeSessionId === sessionAtStart

    this.angryExitTriggered = true
    this.interruptsEnabled = false
    this.cancelActiveNarration()
    this.cancelCursorMotion()
    this.scriptedMotionInterruptDistance = 0
    this.scriptedMotionInterruptWindowStartedAt = 0
    this.clearSpotlight()
    this.resetCursorVisualState()
    this.setAngryVisual(true)
    this.homeNarrationFinished = false
    const handledByHome = await this.requestHomeInterruptPlayback({
      kind: 'interrupt_angry_exit',
      text: ANGRY_EXIT_LINE,
      textKey: 'tutorial.yuiGuide.lines.interruptAngryExit',
      voiceKey: 'interrupt_angry_exit',
      interruptCount: this.interruptCount,
    })
    if (!isSameSession()) {
      return
    }
    if (!handledByHome) {
      await this.speakLine(ANGRY_EXIT_LINE, {
        voiceKey: 'interrupt_angry_exit',
      })
      if (!isSameSession()) {
        return
      }
    } else {
      const angryExitTimeoutMs = clamp(estimateSpeechDurationMs(ANGRY_EXIT_LINE) + 2000, 4000, 12000)
      const homeNarrationCompleted = await Promise.race([
        this.waitForHomeNarrationFinished(sessionAtStart, isSameSession),
        wait(angryExitTimeoutMs).then(() => isSameSession()),
      ])
      if (!homeNarrationCompleted || !isSameSession()) {
        return
      }
    }
    if (!isSameSession()) {
      return
    }
    this.notify(DONE_EVENT, this.activeSessionId)
    this.cleanup()
  }

  cleanup() {
    const pauseResolvers = this.scenePauseResolvers.slice()
    this.scenePauseResolvers = []
    this.scenePausedForResistance = false
    pauseResolvers.forEach((resolve) => {
      try {
        resolve()
      } catch (_) {}
    })
    const narrationResolvers = this.homeNarrationResolvers.slice()
    this.homeNarrationResolvers = []
    narrationResolvers.forEach((resolve) => {
      try {
        resolve()
      } catch (_) {}
    })
    document.documentElement.classList.remove('yui-guide-plugin-dashboard-running')
    document.documentElement.removeAttribute('data-yui-guide-spotlight-padding')
    document.documentElement.classList.remove('yui-taking-over')
    document.documentElement.classList.remove('yui-resistance-cursor-reveal')
    document.body.classList.remove('yui-guide-plugin-dashboard-running')
    document.body.classList.remove('yui-taking-over')
    document.body.classList.remove('yui-resistance-cursor-reveal')
    document
      .querySelector('[data-yui-guide-id="plugin-main"]')
      ?.removeAttribute('data-yui-guide-spotlight-padding')
    if (currentGuideAudioTimer !== null) {
      window.clearTimeout(currentGuideAudioTimer)
      currentGuideAudioTimer = null
    }
    if (currentGuideAudio) {
      try {
        currentGuideAudio.onended = null
        currentGuideAudio.onerror = null
        currentGuideAudio.pause()
        currentGuideAudio.currentTime = 0
      } catch (_) {}
      currentGuideAudio = null
    }
    this.cancelActiveNarration()
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
      this.resistanceCursorTimer = null
    }
    this.resetCursorVisualState()
    this.clearPendingInterruptAck(false)
    this.lastForwardedSkipAt = 0
    this.lastForwardedSkipScreenX = NaN
    this.lastForwardedSkipScreenY = NaN
    window.removeEventListener('resize', this.boundRefreshSpotlight, true)
    window.removeEventListener('scroll', this.boundRefreshSpotlight, true)
    window.removeEventListener('pointermove', this.boundPointerMoveHandler, true)
    window.removeEventListener('pointerdown', this.boundPointerDownHandler, true)
    document.removeEventListener('pointerdown', this.boundInteractionGuard, true)
    document.removeEventListener('pointerup', this.boundInteractionGuard, true)
    document.removeEventListener('mousedown', this.boundInteractionGuard, true)
    document.removeEventListener('mouseup', this.boundInteractionGuard, true)
    document.removeEventListener('touchstart', this.boundInteractionGuard, true)
    document.removeEventListener('touchend', this.boundInteractionGuard, true)
    document.removeEventListener('touchmove', this.boundInteractionGuard, true)
    document.removeEventListener('wheel', this.boundInteractionGuard, true)
    document.removeEventListener('click', this.boundInteractionGuard, true)
    document.removeEventListener('dblclick', this.boundInteractionGuard, true)
    document.removeEventListener('contextmenu', this.boundInteractionGuard, true)
    this.clearSpotlight()
    if (this.root && this.root.parentNode) {
      this.root.parentNode.removeChild(this.root)
    }
    const runtimeStyle = document.getElementById(`${ROOT_ID}-style`)
    if (runtimeStyle && runtimeStyle.parentNode) {
      runtimeStyle.parentNode.removeChild(runtimeStyle)
    }
    this.root = null
    this.backdrop = null
    this.backdropBase = null
    this.backdropFill = null
    this.backdropCutout = null
    this.interactionShield = null
    this.spotlight = null
    this.cursorShell = null
    this.cursorInner = null
    this.cursorPosition = null
    this.cursorTrailSvg = null
    this.cursorTrailBody = null
    this.cursorTrailCore = null
    this.cursorTrailHead = null
    this.cursorTrailHeadCore = null
    this.cursorTrailGradient = null
    this.cursorTrailPoints = []
    this.cursorTrailLastPoint = null
    this.cursorTrailLastAt = 0
    this.spotlightElement = null
    this.lastCursorTarget = null
    this.running = false
    this.activeSessionId = ''
    this.interruptsEnabled = false
    this.scenePausedForResistance = false
    this.homeNarrationFinished = false
    this.angryExitTriggered = false
    this.interruptCount = 0
    this.interruptAccelerationStreak = 0
    this.lastInterruptAt = 0
    this.lastPassiveResistanceAt = 0
    this.lastPointerPoint = null
    this.scriptedMotionInterruptDistance = 0
    this.scriptedMotionInterruptWindowStartedAt = 0
    this.narrationResumeTimer = null
    this.cursorMotionToken = 0
    this.cursorReactionInFlight = false
    this.cursorTransitionActive = false
    this.activeNarration = null
    this.pendingInterruptAck = null
    this.clearPreactivationTimeout()
    this.homeSkipButtonScreenRect = null
    this.scenePauseResolvers = []
    this.homeNarrationResolvers = []
  }

  async run(sessionId: string, payload: StartPayload) {
    if (this.running && this.activeSessionId === sessionId) {
      return
    }

    this.clearPreactivationTimeout()
    this.cleanup()
    this.running = true
    this.activeSessionId = sessionId
    this.interruptCount = Number.isFinite(payload.interruptCount)
      ? Math.max(0, Math.floor(payload.interruptCount as number))
      : 0
    this.homeSkipButtonScreenRect = payload.skipButtonScreenRect
      && Number.isFinite(payload.skipButtonScreenRect.left)
      && Number.isFinite(payload.skipButtonScreenRect.top)
      && Number.isFinite(payload.skipButtonScreenRect.right)
      && Number.isFinite(payload.skipButtonScreenRect.bottom)
      ? {
          left: Math.round(payload.skipButtonScreenRect.left),
          top: Math.round(payload.skipButtonScreenRect.top),
          right: Math.round(payload.skipButtonScreenRect.right),
          bottom: Math.round(payload.skipButtonScreenRect.bottom),
          coordinateSpace: payload.skipButtonScreenRect.coordinateSpace,
          platform: payload.skipButtonScreenRect.platform,
          devicePixelRatio: payload.skipButtonScreenRect.devicePixelRatio,
          hitPadding: payload.skipButtonScreenRect.hitPadding,
          forwardingTolerance: payload.skipButtonScreenRect.forwardingTolerance,
          pointerProfile: payload.skipButtonScreenRect.pointerProfile || payload.platformCapabilities?.pointerProfile,
        }
      : null
    this.homeNarrationFinished = false
    const isCurrent = () => this.isCurrentRun(sessionId)
    this.activateOverlayShell()
    window.addEventListener('resize', this.boundRefreshSpotlight, true)
    window.addEventListener('scroll', this.boundRefreshSpotlight, true)
    // 用 pointer 事件而非 mouse 事件采样：interactionGuard 把 touchstart/move/end 都拦掉了，
    // 单挂 mousemove/mousedown 会让触屏设备永远攒不到 interruptCount，被脚本接管到结束。
    // pointer 事件统一覆盖鼠标和触屏，capture 阶段先于 document 上的 interactionGuard 执行。
    window.addEventListener('pointermove', this.boundPointerMoveHandler, true)
    window.addEventListener('pointerdown', this.boundPointerDownHandler, true)
    document.addEventListener('pointerdown', this.boundInteractionGuard, true)
    document.addEventListener('pointerup', this.boundInteractionGuard, true)
    document.addEventListener('mousedown', this.boundInteractionGuard, true)
    document.addEventListener('mouseup', this.boundInteractionGuard, true)
    document.addEventListener('touchstart', this.boundInteractionGuard, true)
    document.addEventListener('touchend', this.boundInteractionGuard, true)
    document.addEventListener('touchmove', this.boundInteractionGuard, true)
    document.addEventListener('wheel', this.boundInteractionGuard, true)
    document.addEventListener('click', this.boundInteractionGuard, true)
    document.addEventListener('dblclick', this.boundInteractionGuard, true)
    document.addEventListener('contextmenu', this.boundInteractionGuard, true)
    if (!isCurrent()) {
      return
    }
    this.showCursor(window.innerWidth / 2, Math.max(56, window.innerHeight / 2))

    const pluginButton = await this.waitForElement(
      () => document.querySelector('[data-yui-guide-id="sidebar-plugins"]') as HTMLElement | null,
      5000,
    )
    const mainContainer = await this.waitForElement(
      () => document.querySelector('[data-yui-guide-id="plugin-main"]') as HTMLElement | null,
      5000,
    )

    if (!isCurrent()) {
      return
    }

    if (!pluginButton || !mainContainer) {
      if (isCurrent()) {
        this.notify(DONE_EVENT, sessionId)
        this.cleanup()
      }
      return
    }

    mainContainer.setAttribute('data-yui-guide-spotlight-padding', String(PLUGIN_MAIN_SPOTLIGHT_INSET))

    if (!isCurrent()) {
      return
    }
    this.notify(READY_EVENT, sessionId)
    this.interruptsEnabled = true

    const pluginRect = this.getRect(pluginButton)
    const startX = pluginRect ? pluginRect.left + pluginRect.width / 2 - 56 : window.innerWidth / 2
    const startY = pluginRect ? pluginRect.top + pluginRect.height / 2 - 24 : window.innerHeight / 2
    if (!isCurrent()) {
      return
    }
    await this.moveCursor(startX, startY, 420, isCurrent)
    if (!isCurrent()) {
      return
    }
    this.setSpotlight(pluginButton)
    await this.moveCursorToElementWithRecovery(pluginButton, 700, isCurrent)
    if (!isCurrent()) {
      return
    }
    this.clickCursor(DEFAULT_CURSOR_CLICK_VISIBLE_MS)
    if (!(await this.waitForSceneDelay(DEFAULT_CURSOR_CLICK_VISIBLE_MS, isCurrent))) {
      return
    }
    pluginButton.click()
    if (!(await this.waitForSceneDelay(280, isCurrent))) {
      return
    }

    const totalNarrationDurationMs = await resolveNarrationDurationMs(payload)
    const elapsedBeforeMotionMs = Number.isFinite(payload.narrationStartedAtMs)
      ? Math.max(0, Date.now() - Math.round(payload.narrationStartedAtMs as number))
      : 0
    const budgetMs = Math.max(0, totalNarrationDurationMs - elapsedBeforeMotionMs)
    const homeNarrationAlreadyRunning = Number.isFinite(payload.narrationStartedAtMs)
    const speechPromise = homeNarrationAlreadyRunning
      ? wait(budgetMs)
      : this.startNarration(payload.line || '', {
        voiceKey: payload.voiceKey,
        audioUrl: payload.audioUrl,
      })
    if (!homeNarrationAlreadyRunning) {
      void speechPromise.finally(() => {
        this.markHomeNarrationFinished(sessionId)
      })
    }
    const baseMoveToMainDurationMs = PLUGIN_DASHBOARD_MOVE_TO_MAIN_MS
    const baseScrollDownDurationMs = Math.round(PLUGIN_DASHBOARD_SCROLL_PHASE_MS / 2)
    const baseScrollUpDurationMs = PLUGIN_DASHBOARD_SCROLL_PHASE_MS - baseScrollDownDurationMs
    const fixedPartsDurationMs = baseMoveToMainDurationMs + baseScrollDownDurationMs + baseScrollUpDurationMs
    let moveToMainDurationMs = baseMoveToMainDurationMs
    let scrollDownDurationMs = baseScrollDownDurationMs
    let scrollUpDurationMs = baseScrollUpDurationMs
    let ellipseDurationMs = Math.max(0, budgetMs - fixedPartsDurationMs)
    if (budgetMs < fixedPartsDurationMs && fixedPartsDurationMs > 0) {
      const scale = budgetMs / fixedPartsDurationMs
      moveToMainDurationMs = Math.floor(baseMoveToMainDurationMs * scale)
      scrollDownDurationMs = Math.floor(baseScrollDownDurationMs * scale)
      scrollUpDurationMs = Math.max(0, Math.round(budgetMs) - moveToMainDurationMs - scrollDownDurationMs)
      ellipseDurationMs = 0
    }

    if (!isCurrent()) {
      return
    }
    this.setSpotlight(mainContainer)
    if (moveToMainDurationMs > 0) {
      await this.moveCursorToElementWithRecovery(mainContainer, moveToMainDurationMs, isCurrent)
      if (!isCurrent()) {
        return
      }
    }
    if (scrollDownDurationMs > 0) {
      await this.animateScroll(mainContainer, 150, scrollDownDurationMs, isCurrent)
      if (!isCurrent()) {
        return
      }
    }
    if (scrollUpDurationMs > 0) {
      await this.animateScroll(mainContainer, -150, scrollUpDurationMs, isCurrent)
      if (!isCurrent()) {
        return
      }
    }
    if (ellipseDurationMs > 0) {
      await this.runEllipse(mainContainer, ellipseDurationMs, isCurrent)
      if (!isCurrent()) {
        return
      }
    }
    if (!(await this.waitForHomeNarrationFinished(sessionId, isCurrent))) {
      return
    }
    if (!isCurrent()) {
      return
    }

    this.notify(DONE_EVENT, sessionId)
    if (!isCurrent()) {
      return
    }

    if (payload.closeOnDone !== false) {
      window.close()
    }

    if (!isCurrent()) {
      return
    }
    this.cleanup()
  }
}

class PluginDashboardLocalTutorialRunner {
  runtime = new PluginDashboardGuideRuntime()
  tooltip: HTMLDivElement | null = null
  titleEl: HTMLDivElement | null = null
  bodyEl: HTMLDivElement | null = null
  hintEl: HTMLDivElement | null = null
  skipButton: HTMLButtonElement | null = null
  cancelled = false
  shieldClickHandler: ((event: Event) => void) | null = null
  keydownHandler: ((event: KeyboardEvent) => void) | null = null
  advanceResolver: (() => void) | null = null
  cancelResolvers: Array<() => void> = []
  advanceEnabled = false

  async start(options: StartPluginDashboardTutorialOptions) {
    const steps = Array.isArray(options.steps) ? options.steps.filter(Boolean) : []
    if (!steps.length) {
      return
    }
    const firstStep = steps[0]
    if (!firstStep) {
      return
    }

    this.runtime.activateOverlayShell()
    this.runtime.ensureRoot()
    this.ensureTooltip(options.labels)
    this.bindAdvanceHandlers()

    try {
      const initialTarget = await this.waitForStepTarget(firstStep, 1200)
      if (initialTarget) {
        const rect = this.runtime.getRect(initialTarget)
        const x = rect ? rect.left + rect.width / 2 : window.innerWidth / 2
        const y = rect ? rect.top + rect.height / 2 : window.innerHeight / 2
        this.runtime.showCursor(x, y)
      } else {
        this.runtime.showCursor(window.innerWidth / 2, window.innerHeight / 2)
      }

      for (const step of steps) {
        if (this.cancelled) {
          return
        }

        await this.runStep(step)
      }
    } catch (error) {
      this.requestCancel()
      console.warn('[PluginDashboardLocalTutorialRunner] 教程步骤执行失败:', error)
    } finally {
      this.cleanup()
    }
  }

  ensureTooltip(labels?: StartPluginDashboardTutorialOptions['labels']) {
    if (!this.runtime.root || this.tooltip) {
      return
    }

    const tooltip = document.createElement('div')
    tooltip.style.position = 'fixed'
    tooltip.style.right = '24px'
    tooltip.style.bottom = '24px'
    tooltip.style.width = 'min(360px, calc(100vw - 32px))'
    tooltip.style.padding = '16px 16px 14px'
    tooltip.style.borderRadius = '18px'
    tooltip.style.background = 'rgba(8, 18, 44, 0.92)'
    tooltip.style.border = '1px solid rgba(160, 214, 255, 0.35)'
    tooltip.style.boxShadow = '0 24px 80px rgba(8, 17, 40, 0.45)'
    tooltip.style.backdropFilter = 'blur(14px)'
    tooltip.style.color = '#eef7ff'
    tooltip.style.pointerEvents = 'auto'
    tooltip.style.zIndex = '2147483647'
    tooltip.style.fontFamily = 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'

    const titleEl = document.createElement('div')
    titleEl.style.fontSize = '16px'
    titleEl.style.fontWeight = '700'
    titleEl.style.lineHeight = '1.35'
    titleEl.style.marginBottom = '8px'

    const bodyEl = document.createElement('div')
    bodyEl.style.fontSize = '14px'
    bodyEl.style.lineHeight = '1.6'
    bodyEl.style.color = 'rgba(238, 247, 255, 0.92)'

    const footer = document.createElement('div')
    footer.style.display = 'flex'
    footer.style.alignItems = 'center'
    footer.style.justifyContent = 'space-between'
    footer.style.gap = '12px'
    footer.style.marginTop = '14px'

    const hintEl = document.createElement('div')
    hintEl.textContent = labels?.keyboardHint || ''
    hintEl.style.fontSize = '12px'
    hintEl.style.lineHeight = '1.45'
    hintEl.style.color = 'rgba(194, 219, 255, 0.78)'
    hintEl.style.flex = '1'

    const skipButton = document.createElement('button')
    skipButton.type = 'button'
    skipButton.textContent = labels?.skip || 'Skip'
    skipButton.style.border = '0'
    skipButton.style.borderRadius = '999px'
    skipButton.style.padding = '8px 14px'
    skipButton.style.background = 'rgba(107, 170, 255, 0.18)'
    skipButton.style.color = '#eef7ff'
    skipButton.style.fontSize = '13px'
    skipButton.style.fontWeight = '600'
    skipButton.style.cursor = 'pointer'
    const handleSkip = (event: Event) => {
      if (typeof event.preventDefault === 'function') {
        event.preventDefault()
      }
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation()
      }
      if (typeof event.stopPropagation === 'function') {
        event.stopPropagation()
      }
      this.requestCancel()
    }
    skipButton.addEventListener('pointerdown', handleSkip)
    skipButton.addEventListener('click', handleSkip)

    footer.appendChild(hintEl)
    footer.appendChild(skipButton)
    tooltip.appendChild(titleEl)
    tooltip.appendChild(bodyEl)
    tooltip.appendChild(footer)
    this.runtime.root.appendChild(tooltip)

    this.tooltip = tooltip
    this.titleEl = titleEl
    this.bodyEl = bodyEl
    this.hintEl = hintEl
    this.skipButton = skipButton
  }

  bindAdvanceHandlers() {
    const shield = this.runtime.interactionShield
    if (shield && !this.shieldClickHandler) {
      this.shieldClickHandler = () => {
        if (!this.advanceEnabled) {
          return
        }
        this.resolveAdvance()
      }
      shield.addEventListener('click', this.shieldClickHandler)
    }

    if (!this.keydownHandler) {
      this.keydownHandler = (event: KeyboardEvent) => {
        if (!this.advanceEnabled) {
          return
        }
        if (event.key !== 'Enter' && event.key !== ' ') {
          return
        }
        event.preventDefault()
        this.resolveAdvance()
      }
      window.addEventListener('keydown', this.keydownHandler, true)
    }
  }

  resolveAdvance() {
    const resolver = this.advanceResolver
    this.advanceResolver = null
    if (resolver) {
      resolver()
    }
  }

  requestCancel() {
    this.cancelled = true
    this.advanceEnabled = false
    this.runtime.cancelCursorMotion()
    this.resolveAdvance()
    const resolvers = this.cancelResolvers.slice()
    this.cancelResolvers = []
    resolvers.forEach((resolve) => {
      try {
        resolve()
      } catch (_) {}
    })
  }

  waitForCancelOrTimeout(delayMs: number) {
    if (this.cancelled) {
      return Promise.resolve(false)
    }

    return new Promise<boolean>((resolve) => {
      let settled = false
      let timeoutId: number | null = null
      const finish = (completed: boolean) => {
        if (settled) {
          return
        }
        settled = true
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId)
          timeoutId = null
        }
        this.cancelResolvers = this.cancelResolvers.filter((resolver) => resolver !== cancel)
        resolve(completed && !this.cancelled)
      }
      const cancel = () => finish(false)
      this.cancelResolvers.push(cancel)
      timeoutId = window.setTimeout(() => finish(true), Math.max(0, Math.round(delayMs)))
    })
  }

  async navigate(route?: string) {
    const targetRoute = String(route || '').trim()
    if (!targetRoute) {
      return
    }

    const currentFullPath = router.currentRoute.value.fullPath
    if (currentFullPath === targetRoute) {
      return
    }

    try {
      await router.push(targetRoute)
    } catch (_) {}
    await wait(80)
  }

  async dispatchAction(action?: string) {
    const value = String(action || '').trim()
    if (!value) {
      return
    }

    window.dispatchEvent(new CustomEvent(LOCAL_TUTORIAL_ACTION_EVENT, {
      detail: {
        action: value,
      },
    }))
  }

  async waitForStepTarget(step: PluginDashboardLocalTutorialStep, timeoutMs = 3600) {
    const targetId = String(step.targetId || '').trim()
    if (!targetId) {
      return null
    }

    const startedAt = Date.now()
    while (!this.cancelled && (Date.now() - startedAt) < timeoutMs) {
      const element = document.querySelector(`[data-yui-guide-id="${targetId}"]`) as HTMLElement | null
      if (element) {
        return element
      }
      if (!(await this.waitForCancelOrTimeout(80))) {
        return null
      }
    }

    return null
  }

  positionTooltip(target: HTMLElement | null) {
    if (!this.tooltip) {
      return
    }

    const rect = target ? this.runtime.getRect(target) : null
    const tooltipWidth = Math.min(360, Math.max(280, Math.round(window.innerWidth * 0.28)))
    this.tooltip.style.width = `${Math.min(tooltipWidth, window.innerWidth - 32)}px`

    if (!rect) {
      this.tooltip.style.left = ''
      this.tooltip.style.top = ''
      this.tooltip.style.right = '24px'
      this.tooltip.style.bottom = '24px'
      return
    }

    const margin = 16
    const tooltipRect = this.tooltip.getBoundingClientRect()
    const preferredTop = rect.bottom + 16
    const placeBelow = preferredTop + tooltipRect.height <= window.innerHeight - margin
    const left = clamp(rect.left + (rect.width / 2) - (tooltipRect.width / 2), margin, window.innerWidth - tooltipRect.width - margin)
    const top = placeBelow
      ? preferredTop
      : Math.max(margin, rect.top - tooltipRect.height - 16)

    this.tooltip.style.left = `${Math.round(left)}px`
    this.tooltip.style.top = `${Math.round(top)}px`
    this.tooltip.style.right = 'auto'
    this.tooltip.style.bottom = 'auto'
  }

  async runStep(step: PluginDashboardLocalTutorialStep) {
    await this.navigate(step.route)
    if (this.cancelled) {
      return
    }

    await this.dispatchAction(step.action)
    if (step.waitMs && step.waitMs > 0) {
      await this.waitForCancelOrTimeout(step.waitMs)
    } else if (step.action) {
      await this.waitForCancelOrTimeout(120)
    }
    if (this.cancelled) {
      return
    }

    const target = await this.waitForStepTarget(step)
    if (!target) {
      if (step.allowMissing) {
        return
      }
      throw new Error(`[PluginDashboardLocalTutorialRunner] Missing target for step: ${step.targetId || '(unknown)'}`)
    }

    if (this.titleEl) {
      this.titleEl.textContent = step.title || ''
    }
    if (this.bodyEl) {
      this.bodyEl.textContent = step.body || ''
    }

    this.runtime.setSpotlight(target)
    this.positionTooltip(target)

    const motion = step.motion || 'point'
    const durationMs = Math.max(600, Math.round(step.durationMs || 1800))
    if (motion === 'ellipse') {
      await this.runtime.moveCursorToElementWithRecovery(target, 460, () => !this.cancelled)
      if (!this.cancelled) {
        await this.runtime.runEllipse(target, durationMs, () => !this.cancelled)
      }
    } else {
      await this.runtime.moveCursorToElementWithRecovery(target, Math.min(700, durationMs), () => !this.cancelled)
      if (!this.cancelled && motion === 'click') {
        this.runtime.clickCursor()
      }
    }

    if (this.cancelled) {
      return
    }

    this.advanceEnabled = false
    window.setTimeout(() => {
      this.advanceEnabled = true
    }, 500)

    await new Promise<void>((resolve) => {
      this.advanceResolver = resolve
      window.setTimeout(() => {
        if (this.advanceResolver === resolve) {
          this.resolveAdvance()
        }
      }, Math.max(1200, durationMs))
    })
    this.advanceEnabled = false
  }

  cleanup() {
    this.requestCancel()

    if (this.runtime.interactionShield && this.shieldClickHandler) {
      this.runtime.interactionShield.removeEventListener('click', this.shieldClickHandler)
    }
    if (this.keydownHandler) {
      window.removeEventListener('keydown', this.keydownHandler, true)
    }

    this.shieldClickHandler = null
    this.keydownHandler = null
    this.tooltip = null
    this.titleEl = null
    this.bodyEl = null
    this.hintEl = null
    this.skipButton = null
    this.runtime.cleanup()
  }
}

let activeLocalTutorialRunner: PluginDashboardLocalTutorialRunner | null = null

export function startPluginDashboardTutorial(options: StartPluginDashboardTutorialOptions) {
  activeLocalTutorialRunner?.cleanup()
  const runner = new PluginDashboardLocalTutorialRunner()
  activeLocalTutorialRunner = runner
  void runner.start(options).finally(() => {
    if (activeLocalTutorialRunner === runner) {
      activeLocalTutorialRunner = null
    }
  })
}

export function initPluginDashboardYuiGuideRuntime() {
  const runtime = new PluginDashboardGuideRuntime()
  let receivedStartMessage = false
  runtime.preactivatePendingOverlay()

  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data
    if (!data || typeof data !== 'object') {
      return
    }

    if (data.type === TERMINATE_EVENT && isAllowedOpenerEvent(event)) {
      const sessionId = typeof data.sessionId === 'string' ? data.sessionId : ''
      if (sessionId && runtime.activeSessionId && sessionId !== runtime.activeSessionId) {
        return
      }

      runtime.cleanup()
      if (data.closeWindow !== false) {
        try {
          window.close()
        } catch (_) {}
      }
      return
    }

    if (data.type === NARRATION_FINISHED_EVENT && isAllowedOpenerEvent(event)) {
      const sessionId = typeof data.sessionId === 'string' ? data.sessionId : ''
      if (sessionId) {
        runtime.markHomeNarrationFinished(sessionId)
      }
      return
    }

    if (data.type === INTERRUPT_ACK_EVENT) {
      runtime.handleInterruptAckMessage(event)
      return
    }

    if (data.type !== START_EVENT || !isAllowedOpenerEvent(event)) {
      return
    }

    if (receivedStartMessage) {
      return
    }

    const sessionId = typeof data.sessionId === 'string' ? data.sessionId : ''
    if (!sessionId) {
      return
    }

    const startPayload = (data.payload || {}) as StartPayload

    activeLocalTutorialRunner?.cleanup()
    receivedStartMessage = true
    runtime.run(sessionId, startPayload).catch(() => {
      if (!runtime.isCurrentRun(sessionId)) {
        return
      }
      runtime.notify(DONE_EVENT, sessionId)
      runtime.cleanup()
    }).finally(() => {
      receivedStartMessage = false
    })
  })
}
