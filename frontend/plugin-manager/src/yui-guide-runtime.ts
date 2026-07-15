import leftCatEarUrl from '../../../static/assets/tutorial/highlight/left-cat-ear.png'
import rightCatEarUrl from '../../../static/assets/tutorial/highlight/right-cat-ear.png'
import catPawUrl from '../../../static/assets/tutorial/highlight/cat-paw.png'
import defaultGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/default-ghost-cursor.png'
import clickGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/click-ghost-cursor.png'
import { i18n, getLocale } from './i18n'
import router from './router'

const START_EVENT = 'neko:yui-guide:plugin-dashboard:start'
const READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready'
const DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done'
const TERMINATE_EVENT = 'neko:yui-guide:plugin-dashboard:terminate'
const NARRATION_FINISHED_EVENT = 'neko:yui-guide:plugin-dashboard:narration-finished'
const INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request'
const INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack'
const SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT = 'neko:yui-guide:plugin-dashboard:system-cursor-temporary-reveal'
const DESKTOP_INTERRUPT_ACK_EVENT = 'neko:yui-guide:desktop-interrupt-ack'
const DESKTOP_NARRATION_FINISHED_EVENT = 'neko:yui-guide:desktop-narration-finished'
const DESKTOP_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT = 'neko:yui-guide:desktop-system-cursor-temporary-reveal'
const SKIP_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:skip-request'
const HANDOFF_STORAGE_KEY = 'neko_yui_guide_handoff_token'
const HANDOFF_TOKEN_VERSION = 1
const PREACTIVATE_CLEANUP_MS = 8000
const GUIDE_AUDIO_BASE_URL = '/static/assets/tutorial/guide-audio/'
const DEFAULT_GUIDE_LOCALE = 'zh'
const DEFAULT_INTERRUPT_SHAKE_WINDOW_MS = 1100
const DEFAULT_INTERRUPT_SHAKE_MIN_DISTANCE = 50
const DEFAULT_INTERRUPT_SHAKE_MIN_SPAN_MS = 600
const DEFAULT_INTERRUPT_SHAKE_MIN_SUSTAINED_SPEED = 1100
const DEFAULT_INTERRUPT_SHAKE_REQUIRED_REVERSALS = 8
const DEFAULT_INTERRUPT_SHAKE_REVERSE_DOT_THRESHOLD = 0
const DEFAULT_INTERRUPT_THROTTLE_MS = 500
const DEFAULT_PASSIVE_RESISTANCE_DISTANCE = 10
const DEFAULT_PASSIVE_RESISTANCE_SPEED_THRESHOLD = 0.2
const DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS = 140
const DEFAULT_USER_CURSOR_REVEAL_DISTANCE = 14
const DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS = 160
const DEFAULT_USER_CURSOR_REVEAL_MOVES = 2
const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420
const NARRATION_RESUME_BACKTRACK_MS = 320
const NARRATION_RESUME_MIN_REMAINING_MS = 1400
const PLUGIN_DASHBOARD_MOVE_TO_MAIN_MS = 780
const PLUGIN_DASHBOARD_SCROLL_PHASE_MS = 2000
const PLUGIN_DASHBOARD_IDLE_ELLIPSE_MS = 1800
const PLUGIN_DASHBOARD_NARRATION_FINISH_FALLBACK_EXTRA_MS = 30000
// Negative values mean inward/inset padding for the plugin-main spotlight.
const PLUGIN_MAIN_SPOTLIGHT_INSET = -25
const PLUGIN_DASHBOARD_DEFAULT_TOTAL_MS = 9000
const MIN_SPOTLIGHT_RADIUS = 4

type InterruptShakeVector = {
  dx: number
  dy: number
  distance: number
}

type InterruptShakeMotion = {
  lastX: number | null
  lastY: number | null
  lastAt: number
  lastVector: InterruptShakeVector | null
  reversals: Array<{ at: number; distance: number }>
}

function createInterruptShakeMotion(): InterruptShakeMotion {
  return {
    lastX: null,
    lastY: null,
    lastAt: 0,
    lastVector: null,
    reversals: [],
  }
}

function isInterruptShakeReversal(previousVector: InterruptShakeVector | null, currentVector: InterruptShakeVector) {
  if (!previousVector) {
    return false
  }

  const denominator = previousVector.distance * currentVector.distance
  if (!Number.isFinite(denominator) || denominator <= 0) {
    return false
  }

  const dot = (
    previousVector.dx * currentVector.dx
    + previousVector.dy * currentVector.dy
  ) / denominator
  return Number.isFinite(dot) && dot <= DEFAULT_INTERRUPT_SHAKE_REVERSE_DOT_THRESHOLD
}

function isInterruptShakeReady(reversals: Array<{ at: number; distance: number }>) {
  if (reversals.length < DEFAULT_INTERRUPT_SHAKE_REQUIRED_REVERSALS) {
    return false
  }

  const first = reversals[0]
  const last = reversals[reversals.length - 1]
  if (!first || !last) {
    return false
  }
  const spanMs = last.at - first.at
  if (!Number.isFinite(spanMs) || spanMs < DEFAULT_INTERRUPT_SHAKE_MIN_SPAN_MS) {
    return false
  }

  const totalDistance = reversals.slice(1).reduce((sum, item) => sum + item.distance, 0)
  const sustainedSpeed = totalDistance / Math.max(0.001, spanMs / 1000)
  return sustainedSpeed >= DEFAULT_INTERRUPT_SHAKE_MIN_SUSTAINED_SPEED
}

const RESISTANCE_LINES = [
  '喵！现在是人家的教学时间，不可以乱动鼠标和键盘啦！乖乖看着人家，好不好嘛？',
  '真是的，又在乱动鼠标和键盘！再不听话的话，人家可真的要生气了喵！',
  '最后警告一次喵！你要是再乱动一下，人家就直接退出新手教程，不教你了！',
] as const
const RESISTANCE_VOICE_KEYS = [
  'interrupt_resist_light_1',
  'interrupt_resist_light_2',
  'interrupt_resist_light_3',
] as const
const ANGRY_EXIT_LINE = '人家已经忍你很久了！既然你就是不肯乖乖听话，那新手教程到此结束，接下来你自己慢慢研究吧，哼！'
const GUIDE_AUDIO_FILE_NAMES = {
  takeover_plugin_preview_dashboard: '有了它们，我不光能看.mp3',
  interrupt_resist_light_1: '喵！现在是人家的教学.mp3',
  interrupt_resist_light_2: '真是的，又在乱动鼠标.mp3',
  interrupt_resist_light_3: '最后警告一次喵！你要.mp3',
  interrupt_angry_exit: '人家已经忍你很久了！.mp3',
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
  interrupt_resist_light_2: {
    zh: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_2,
    en: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_2,
    ja: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_2,
    ko: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_2,
    ru: GUIDE_AUDIO_FILE_NAMES.interrupt_resist_light_2,
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

type StartPluginDashboardTutorialOptionsFactory = () => StartPluginDashboardTutorialOptions

type DesktopTutorialSkipPayload = {
  sessionId?: string
  reason?: string
  source?: string
  detail?: Record<string, unknown>
}

declare global {
  interface Window {
    nekoYuiGuideDesktopBridge?: {
      requestTutorialSkip?: (payload?: DesktopTutorialSkipPayload) => Promise<boolean> | boolean
      requestTutorialInterrupt?: (payload?: DesktopTutorialSkipPayload) => Promise<boolean> | boolean
    }
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

function isLoopbackOrigin(origin: string) {
  try {
    const url = new URL(origin)
    const hostname = url.hostname.toLowerCase()
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && (
        hostname === 'localhost'
        || hostname === '127.0.0.1'
        || hostname === '::1'
      )
    )
  } catch {
    return false
  }
}

const DEFAULT_OPENER_ORIGIN = normalizeOrigin(import.meta.env.VITE_YUI_TUTORIAL_OPENER_ORIGIN || '')
const OPENER_ORIGIN_QUERY_PARAM = 'yui_opener_origin'
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

function getQueryOpenerOrigin() {
  try {
    const params = new URLSearchParams(window.location.search || '')
    const origin = normalizeOrigin(params.get(OPENER_ORIGIN_QUERY_PARAM) || '')
    return origin && isLoopbackOrigin(origin) ? origin : ''
  } catch {
    return ''
  }
}

function getTrustedOpenerOrigin() {
  if (!window.opener || window.opener.closed) {
    return getQueryOpenerOrigin() || DEFAULT_OPENER_ORIGIN
  }

  try {
    const openerOrigin = window.opener.location.origin
    if (openerOrigin && (openerOrigin === window.location.origin || ALLOWED_OPENER_ORIGINS.has(openerOrigin))) {
      return openerOrigin
    }
  } catch {
    // Cross-origin opener access is expected here.
  }

  return getQueryOpenerOrigin() || DEFAULT_OPENER_ORIGIN
}

const ROOT_ID = 'yui-guide-plugin-dashboard-runtime'
const SVG_NS = 'http://www.w3.org/2000/svg'
const BACKDROP_MASK_ID = `${ROOT_ID}-mask`
const DEFAULT_SPOTLIGHT_PADDING = 6
const BACKDROP_CUTOUT_INSET = 4
const CONTROL_BANNER_TEXT_KEY = 'tutorial.yuiGuide.controlBanner'
const CONTROL_BANNER_FALLBACK_TEXT = 'The catgirl is controlling the mouse'
const CONTROL_BANNER_INTERRUPT_EMPHASIS_MS = 2000
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
  if (current.startsWith('es')) return 'es'
  if (current.startsWith('pt')) return 'pt'
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

function resolveControlBannerText() {
  try {
    const translated = i18n.global.t(CONTROL_BANNER_TEXT_KEY)
    if (
      typeof translated === 'string'
      && translated.trim()
      && translated !== CONTROL_BANNER_TEXT_KEY
    ) {
      return translated
    }
  } catch (_) {}

  return CONTROL_BANNER_FALLBACK_TEXT
}

function getAllowedOpenerOrigins() {
  const origins = new Set<string>(ALLOWED_OPENER_ORIGINS)
  const queryOpenerOrigin = getQueryOpenerOrigin()
  const trustedOrigin = getTrustedOpenerOrigin()
  if (queryOpenerOrigin) {
    origins.add(queryOpenerOrigin)
  }
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
  const audioLocale = ['zh', 'en', 'ja', 'ko', 'ru'].includes(locale) ? locale : 'en'
  const files = GUIDE_AUDIO_BY_KEY[voiceKey]
  const fileName = files[audioLocale as keyof typeof files] || files.en || ''
  const fileLocale = files[audioLocale as keyof typeof files] ? audioLocale : 'en'
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
  if (interruptCount >= 3) return 'tutorial.yuiGuide.lines.interruptResistLight3'
  if (interruptCount >= 2) return 'tutorial.yuiGuide.lines.interruptResistLight2'
  return 'tutorial.yuiGuide.lines.interruptResistLight1'
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
    html.yui-guide-plugin-dashboard-running [data-yui-cursor-hidden="true"],
    body.yui-guide-plugin-dashboard-running [data-yui-cursor-hidden="true"],
    html.yui-taking-over [data-yui-cursor-hidden="true"],
    body.yui-taking-over [data-yui-cursor-hidden="true"] {
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

    /* Double .yui-taking-over to out-specificity earlier cursor:auto !important rules. */
    html.yui-taking-over.yui-taking-over,
    html.yui-taking-over.yui-taking-over *,
    body.yui-taking-over.yui-taking-over,
    body.yui-taking-over.yui-taking-over *,
    html.yui-taking-over [data-yui-cursor-hidden="true"],
    body.yui-taking-over [data-yui-cursor-hidden="true"],
    html.yui-guide-plugin-dashboard-running.yui-taking-over button,
    html.yui-guide-plugin-dashboard-running.yui-taking-over a[href],
    html.yui-guide-plugin-dashboard-running.yui-taking-over input,
    html.yui-guide-plugin-dashboard-running.yui-taking-over select,
    html.yui-guide-plugin-dashboard-running.yui-taking-over textarea,
    html.yui-guide-plugin-dashboard-running.yui-taking-over summary,
    html.yui-guide-plugin-dashboard-running.yui-taking-over [role="button"],
    html.yui-guide-plugin-dashboard-running.yui-taking-over [role="link"],
    html.yui-guide-plugin-dashboard-running.yui-taking-over [tabindex]:not([tabindex="-1"]),
    body.yui-guide-plugin-dashboard-running.yui-taking-over button,
    body.yui-guide-plugin-dashboard-running.yui-taking-over a[href],
    body.yui-guide-plugin-dashboard-running.yui-taking-over input,
    body.yui-guide-plugin-dashboard-running.yui-taking-over select,
    body.yui-guide-plugin-dashboard-running.yui-taking-over textarea,
    body.yui-guide-plugin-dashboard-running.yui-taking-over summary,
    body.yui-guide-plugin-dashboard-running.yui-taking-over [role="button"],
    body.yui-guide-plugin-dashboard-running.yui-taking-over [role="link"],
    body.yui-guide-plugin-dashboard-running.yui-taking-over [tabindex]:not([tabindex="-1"]) {
      cursor: none !important;
    }

    html.yui-guide-plugin-dashboard-running.yui-taking-over.yui-user-cursor-revealed,
    html.yui-guide-plugin-dashboard-running.yui-taking-over.yui-user-cursor-revealed *,
    html.yui-guide-plugin-dashboard-running.yui-taking-over.yui-resistance-cursor-reveal,
    html.yui-guide-plugin-dashboard-running.yui-taking-over.yui-resistance-cursor-reveal *,
    body.yui-guide-plugin-dashboard-running.yui-taking-over.yui-user-cursor-revealed,
    body.yui-guide-plugin-dashboard-running.yui-taking-over.yui-user-cursor-revealed *,
    body.yui-guide-plugin-dashboard-running.yui-taking-over.yui-resistance-cursor-reveal,
    body.yui-guide-plugin-dashboard-running.yui-taking-over.yui-resistance-cursor-reveal * {
      cursor: auto !important;
    }

    #${ROOT_ID} {
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: 2147483646;
    }

    #${ROOT_ID} .yui-guide-plugin-control-banner {
      position: fixed;
      top: max(14px, env(safe-area-inset-top));
      left: 50%;
      z-index: 42;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      max-width: min(520px, calc(100vw - 180px));
      min-height: 34px;
      box-sizing: border-box;
      padding: 7px 14px;
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 8px;
      background: rgba(19, 28, 40, 0.9);
      box-shadow:
        0 12px 32px rgba(12, 18, 26, 0.22),
        inset 0 1px 0 rgba(255, 255, 255, 0.08);
      color: #fff8ef;
      font-size: 13px;
      font-weight: 700;
      line-height: 1.3;
      text-align: center;
      overflow-wrap: anywhere;
      pointer-events: none;
      opacity: 0;
      transform: translate(-50%, -6px);
      --yui-guide-plugin-control-banner-emphasis-ease: cubic-bezier(0.16, 1, 0.3, 1);
      transition:
        opacity 180ms ease,
        top 420ms var(--yui-guide-plugin-control-banner-emphasis-ease),
        max-width 420ms var(--yui-guide-plugin-control-banner-emphasis-ease),
        padding 420ms var(--yui-guide-plugin-control-banner-emphasis-ease),
        font-size 420ms var(--yui-guide-plugin-control-banner-emphasis-ease),
        transform 420ms var(--yui-guide-plugin-control-banner-emphasis-ease);
      backdrop-filter: blur(10px) saturate(1.08);
    }

    #${ROOT_ID} .yui-guide-plugin-control-banner.is-visible {
      opacity: 1;
      transform: translate(-50%, 0);
    }

    #${ROOT_ID} .yui-guide-plugin-control-banner.is-interrupt-emphasis {
      top: 50%;
      max-width: min(720px, calc(100vw - 40px));
      padding: 16px 26px;
      font-size: 17px;
      transform: translate(-50%, -50%) scale(3);
    }

    #${ROOT_ID} .yui-guide-plugin-control-banner[hidden] {
      display: none !important;
    }

    #${ROOT_ID} .yui-guide-plugin-control-banner::before {
      content: '';
      width: 8px;
      height: 8px;
      flex: 0 0 auto;
      border-radius: 999px;
      background: #38d6bb;
      box-shadow: 0 0 0 4px rgba(56, 214, 187, 0.18);
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

    #${ROOT_ID} .yui-guide-plugin-pointer {
      position: fixed;
      left: 0;
      top: 0;
      width: 46px;
      height: 46px;
      opacity: 0;
      pointer-events: none;
      z-index: 2147483647;
      transform: translate3d(-9999px, -9999px, 0);
      transform-origin: center center;
      transition:
        transform var(--yui-guide-plugin-pointer-duration, 0ms) cubic-bezier(0.22, 1, 0.36, 1),
        opacity 120ms ease,
        scale 120ms ease;
      will-change: transform;
      background-image: url('${defaultGhostCursorUrl}');
      background-position: center;
      background-repeat: no-repeat;
      background-size: contain;
      filter:
        drop-shadow(0 10px 18px rgba(10, 31, 68, 0.22))
        drop-shadow(0 2px 3px rgba(10, 31, 68, 0.18));
    }

    #${ROOT_ID} .yui-guide-plugin-pointer.is-visible {
      opacity: 1;
    }

    #${ROOT_ID} .yui-guide-plugin-pointer.is-pressed {
      background-image: url('${clickGhostCursorUrl}');
      scale: 1;
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

    @media (max-width: 768px) {
      #${ROOT_ID} .yui-guide-plugin-control-banner {
        top: max(56px, calc(env(safe-area-inset-top) + 56px));
        max-width: calc(100vw - 28px);
        font-size: 12px;
      }
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

    @media (prefers-reduced-motion: reduce) {
      #${ROOT_ID} .yui-guide-plugin-control-banner,
      #${ROOT_ID} .yui-guide-plugin-spotlight,
      #${ROOT_ID} .yui-guide-plugin-spotlight-sweep::before,
      #${ROOT_ID} .yui-guide-plugin-pointer {
        animation: none !important;
        transition: none !important;
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
  controlBanner: HTMLDivElement | null = null
  controlBannerEmphasisTimer: number | null = null
  controlBannerEmphasisActive = false
  renderedControlBannerText = ''
  renderedControlBannerVisible: boolean | null = null
  renderedControlBannerEmphasis: boolean | null = null
  spotlight: HTMLDivElement | null = null
  pointer: HTMLDivElement | null = null
  cursorPosition: { x: number; y: number } | null = null
  lastCursorTarget: { x: number; y: number } | null = null
  spotlightElement: Element | null = null
  activeSessionId = ''
  running = false
  interruptsEnabled = false
  scenePausedForResistance = false
  homeNarrationFinished = false
  homeNarrationOwnedByOpener = false
  angryExitTriggered = false
  interruptCount = 0
  interruptShakeMotion = createInterruptShakeMotion()
  lastInterruptAt = 0
  lastPassiveResistanceAt = 0
  lastPointerPoint: { x: number; y: number; t: number; speed: number } | null = null
  resistanceCursorTimer: number | null = null
  userCursorRevealMoveCount = 0
  userCursorRevealSuppressed = false
  lastUserCursorRevealMoveAt = 0
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
  desktopSkipButton: HTMLButtonElement | null = null
  desktopSkipButtonCleanup: (() => void) | null = null
  lastForwardedSkipAt = 0
  lastForwardedSkipScreenX = NaN
  lastForwardedSkipScreenY = NaN
  spotlightRefreshRaf: number | null = null
  cursorClickResetTimer: number | null = null
  boundPointerMoveHandler = (event: PointerEvent | MouseEvent) => {
    this.handleInterrupt(event)
  }
  boundPointerDownHandler = (event: PointerEvent | MouseEvent) => {
    if (this.isPluginDashboardSkipControlTarget(event)) {
      return
    }
    if (this.forwardHomeSkipClick(event)) {
      return
    }
    this.onPointerDown(event)
  }
  boundInteractionGuard = (event: Event) => {
    if (!this.running || !event) {
      return
    }

    if (this.isPluginDashboardSkipControlTarget(event)) {
      return
    }

    if (this.forwardHomeSkipClick(event)) {
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
  boundScheduleSpotlightRefresh = () => {
    if (this.spotlightRefreshRaf !== null) {
      return
    }
    this.spotlightRefreshRaf = window.requestAnimationFrame(() => {
      this.spotlightRefreshRaf = null
      this.boundRefreshSpotlight()
    })
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

  ensureRoot() {
    if (this.root && this.root.isConnected) {
      this.syncControlBanner()
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

    const pointer = document.createElement('div')
    pointer.className = 'yui-guide-plugin-pointer'
    pointer.setAttribute('aria-hidden', 'true')

    const interactionShield = document.createElement('div')
    interactionShield.className = 'yui-guide-plugin-interaction-shield'

    const controlBanner = document.createElement('div')
    controlBanner.className = 'yui-guide-plugin-control-banner'
    controlBanner.hidden = true
    controlBanner.setAttribute('role', 'status')
    controlBanner.setAttribute('aria-live', 'polite')
    controlBanner.setAttribute('data-yui-cursor-hidden', 'true')
    controlBanner.textContent = resolveControlBannerText()

    root.appendChild(backdrop)
    root.appendChild(interactionShield)
    root.appendChild(spotlight)
    root.appendChild(pointer)
    root.appendChild(controlBanner)
    document.body.appendChild(root)

    this.root = root
    this.backdrop = backdrop
    this.backdropBase = backdropBase
    this.backdropFill = backdropFill
    this.backdropCutout = backdropCutout
    this.interactionShield = interactionShield
    this.controlBanner = controlBanner
    this.spotlight = spotlight
    this.pointer = pointer
    this.syncControlBanner()
    this.syncBackdropViewport()
  }

  syncControlBanner(active?: boolean) {
    if (!this.controlBanner) {
      return
    }

    const isVisible = active === undefined
      ? (
          document.documentElement.classList.contains('yui-taking-over')
          || document.body.classList.contains('yui-taking-over')
        )
      : active === true
    const isEmphasized = isVisible && this.controlBannerEmphasisActive === true
    const text = resolveControlBannerText()

    if (
      this.renderedControlBannerText === text
      && this.renderedControlBannerVisible === isVisible
      && this.renderedControlBannerEmphasis === isEmphasized
      && this.controlBanner.hidden === !isVisible
      && this.controlBanner.classList.contains('is-visible') === isVisible
      && this.controlBanner.classList.contains('is-interrupt-emphasis') === isEmphasized
    ) {
      return
    }

    if (this.renderedControlBannerText !== text) {
      this.controlBanner.textContent = text
      this.renderedControlBannerText = text
    }
    this.controlBanner.hidden = !isVisible
    this.controlBanner.classList.toggle('is-visible', isVisible)
    this.controlBanner.classList.toggle('is-interrupt-emphasis', isEmphasized)
    this.renderedControlBannerVisible = isVisible
    this.renderedControlBannerEmphasis = isEmphasized
  }

  emphasizeControlBanner(durationMs = CONTROL_BANNER_INTERRUPT_EMPHASIS_MS) {
    if (
      !document.documentElement.classList.contains('yui-taking-over')
      && !document.body.classList.contains('yui-taking-over')
    ) {
      return
    }
    this.ensureRoot()
    if (this.controlBannerEmphasisTimer !== null) {
      window.clearTimeout(this.controlBannerEmphasisTimer)
      this.controlBannerEmphasisTimer = null
    }
    this.controlBannerEmphasisActive = true
    this.syncControlBanner(true)
    this.controlBannerEmphasisTimer = window.setTimeout(() => {
      this.controlBannerEmphasisTimer = null
      this.controlBannerEmphasisActive = false
      this.syncControlBanner()
    }, Math.max(0, Math.round(Number(durationMs) || CONTROL_BANNER_INTERRUPT_EMPHASIS_MS)))
  }

  hasDesktopTutorialSkipBridge() {
    return !!(
      window.nekoYuiGuideDesktopBridge
      && typeof window.nekoYuiGuideDesktopBridge.requestTutorialSkip === 'function'
    )
  }

  hasDesktopTutorialInterruptBridge() {
    return !!(
      window.nekoYuiGuideDesktopBridge
      && typeof window.nekoYuiGuideDesktopBridge.requestTutorialInterrupt === 'function'
    )
  }

  isPluginDashboardSkipControlTarget(event: Event) {
    const target = event.target
    return !!(
      target
      && target instanceof Element
      && target.closest('[data-yui-plugin-dashboard-skip-control="true"]')
    )
  }

  canRequestHomeInterruptPlayback() {
    if (this.hasDesktopTutorialInterruptBridge()) {
      return true
    }
    return !!(
      window.opener
      && !window.opener.closed
      && (openerMessageOrigin || getTrustedOpenerOrigin())
    )
  }

  requestPluginDashboardSkip(detail?: Record<string, unknown>) {
    const normalizedDetail = detail && typeof detail === 'object' ? detail : {}
    const detailReason = normalizedDetail.reason
    const normalizedReason = typeof detailReason === 'string' && detailReason.trim()
      ? detailReason.trim()
      : 'skip'
    const payload: DesktopTutorialSkipPayload = {
      sessionId: this.activeSessionId,
      reason: normalizedReason,
      source: 'plugin_dashboard',
      detail: normalizedDetail,
    }
    const bridge = window.nekoYuiGuideDesktopBridge
    if (bridge && typeof bridge.requestTutorialSkip === 'function') {
      try {
        const result = bridge.requestTutorialSkip(payload)
        if (result && typeof (result as Promise<boolean>).then === 'function') {
          void (result as Promise<boolean>).then((handled) => {
            if (!handled) {
              this.notify(SKIP_REQUEST_EVENT, this.activeSessionId, normalizedDetail)
            }
          }).catch(() => {
            this.notify(SKIP_REQUEST_EVENT, this.activeSessionId, normalizedDetail)
          })
          return true
        }
        if (result === true) {
          return true
        }
      } catch (_) {}
    }

    this.notify(SKIP_REQUEST_EVENT, this.activeSessionId, normalizedDetail)
    return true
  }

  ensureDesktopSkipButton() {
    if (!this.root || this.desktopSkipButton || !this.hasDesktopTutorialSkipBridge()) {
      return
    }

    const button = document.createElement('button')
    button.type = 'button'
    const locale = resolveGuideLocale()
    const labelByLocale: Record<string, string> = {
      zh: '跳过',
      en: 'Skip',
      ja: 'スキップ',
      ko: '건너뛰기',
      ru: 'Пропустить',
      es: 'Saltar',
      pt: 'Pular',
    }
    const label = labelByLocale[locale] || 'Skip'
    button.textContent = label
    button.setAttribute('aria-label', label)
    button.setAttribute('data-yui-plugin-dashboard-skip-control', 'true')
    button.style.position = 'fixed'
    button.style.top = '18px'
    button.style.right = '18px'
    button.style.zIndex = '2147483647'
    button.style.pointerEvents = 'auto'
    button.style.border = '0'
    button.style.borderRadius = '999px'
    button.style.padding = '8px 14px'
    button.style.background = 'rgba(8, 18, 44, 0.86)'
    button.style.color = '#eef7ff'
    button.style.boxShadow = '0 10px 28px rgba(8, 17, 40, 0.28)'
    button.style.fontSize = '13px'
    button.style.fontWeight = '600'
    button.style.cursor = 'pointer'

    let skipHandled = false
    const stopSkipEvent = (event: Event) => {
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation()
      }
      if (typeof event.stopPropagation === 'function') {
        event.stopPropagation()
      }
    }
    const handleSkip = (event: Event) => {
      if (typeof event.preventDefault === 'function') {
        event.preventDefault()
      }
      stopSkipEvent(event)
      if (skipHandled) {
        return
      }
      skipHandled = true
      button.setAttribute('aria-disabled', 'true')
      button.style.opacity = '0.72'
      this.requestPluginDashboardSkip({
        source: 'plugin_dashboard_button',
      })
    }

    button.addEventListener('pointerdown', stopSkipEvent)
    button.addEventListener('pointerup', stopSkipEvent)
    button.addEventListener('mousedown', stopSkipEvent)
    button.addEventListener('mouseup', stopSkipEvent)
    button.addEventListener('touchstart', stopSkipEvent)
    button.addEventListener('touchend', stopSkipEvent)
    button.addEventListener('click', handleSkip)
    this.root.appendChild(button)
    this.desktopSkipButton = button
    this.desktopSkipButtonCleanup = () => {
      button.removeEventListener('pointerdown', stopSkipEvent)
      button.removeEventListener('pointerup', stopSkipEvent)
      button.removeEventListener('mousedown', stopSkipEvent)
      button.removeEventListener('mouseup', stopSkipEvent)
      button.removeEventListener('touchstart', stopSkipEvent)
      button.removeEventListener('touchend', stopSkipEvent)
      button.removeEventListener('click', handleSkip)
    }
  }

  clearDesktopSkipButton() {
    if (this.desktopSkipButtonCleanup) {
      this.desktopSkipButtonCleanup()
    }
    this.desktopSkipButtonCleanup = null
    if (this.desktopSkipButton && this.desktopSkipButton.parentNode) {
      this.desktopSkipButton.parentNode.removeChild(this.desktopSkipButton)
    }
    this.desktopSkipButton = null
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

  handleInterruptAckData(data: unknown) {
    const payload = data && typeof data === 'object'
      ? data as { type?: unknown; requestId?: unknown }
      : null
    if (!payload || payload.type !== INTERRUPT_ACK_EVENT) {
      return
    }

    const pending = this.pendingInterruptAck
    const requestId = typeof payload.requestId === 'string'
      ? payload.requestId
      : ''
    if (!pending || !requestId || pending.requestId !== requestId) {
      return
    }

    this.clearPendingInterruptAck(true)
  }

  handleInterruptAckMessage(event: MessageEvent) {
    if (!isAllowedOpenerEvent(event)) {
      return
    }

    this.handleInterruptAckData(event.data)
  }

  handleDesktopInterruptAckEvent(event: Event) {
    const detail = (event as CustomEvent).detail
    this.handleInterruptAckData(detail)
  }

  handleDesktopNarrationFinishedEvent(event: Event) {
    const detail = (event as CustomEvent).detail
    const sessionId = detail && typeof detail.sessionId === 'string' ? detail.sessionId : ''
    if (sessionId) {
      this.markHomeNarrationFinished(sessionId)
    }
  }

  handleSystemCursorTemporaryRevealData(data: unknown) {
    const detail = data && typeof data === 'object' ? data as { sessionId?: unknown, durationMs?: unknown } : null
    if (!detail) {
      return
    }
    const sessionId = typeof detail?.sessionId === 'string' ? detail.sessionId : ''
    if (!sessionId || !this.isCurrentRun(sessionId)) {
      return
    }
    const durationMs = Number.isFinite(detail?.durationMs) ? Number(detail.durationMs) : undefined
    this.revealSystemCursorTemporarily(durationMs)
  }

  handleDesktopSystemCursorTemporaryRevealEvent(event: Event) {
    this.handleSystemCursorTemporaryRevealData((event as CustomEvent).detail)
  }

  async requestHomeInterruptPlayback(
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
    this.clearPendingInterruptAck(false)
    const requestId = `plugin-dashboard-interrupt-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const timeoutExtraMs = detail.kind === 'interrupt_angry_exit'
      ? PLUGIN_DASHBOARD_NARRATION_FINISH_FALLBACK_EXTRA_MS
      : 4000
    const timeoutMs = clamp(
      estimateSpeechDurationMs(detail.text) + timeoutExtraMs,
      4000,
      detail.kind === 'interrupt_angry_exit' ? 60000 : 12000,
    )

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

      const sendPostMessageFallback = () => {
        if (!this.pendingInterruptAck || this.pendingInterruptAck.requestId !== requestId) {
          return
        }

        if (!window.opener || window.opener.closed) {
          this.clearPendingInterruptAck(false)
          return
        }

        const targetOrigin = openerMessageOrigin || getTrustedOpenerOrigin()
        if (!targetOrigin) {
          this.clearPendingInterruptAck(false)
          return
        }

        try {
          this.notify(INTERRUPT_REQUEST_EVENT, this.activeSessionId, detail, requestId)
        } catch (_) {
          this.clearPendingInterruptAck(false)
        }
      }

      const bridge = window.nekoYuiGuideDesktopBridge
      if (bridge && typeof bridge.requestTutorialInterrupt === 'function') {
        try {
          const handled = bridge.requestTutorialInterrupt({
            sessionId: this.activeSessionId,
            reason: 'interrupt',
            source: 'plugin_dashboard',
            detail: {
              requestId,
              ...detail,
            },
          })
          void Promise.resolve(handled).then((result) => {
            if (result !== true) {
              sendPostMessageFallback()
            }
          }).catch(() => {
            sendPostMessageFallback()
          })
          return
        } catch (_) {}
      }

      sendPostMessageFallback()
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

  getEventScreenPoint(event: Event) {
    if (
      typeof window.MouseEvent !== 'undefined'
      && event instanceof window.MouseEvent
    ) {
      return {
        screenX: Number(event.screenX),
        screenY: Number(event.screenY),
      }
    }

    if (
      typeof window.TouchEvent !== 'undefined'
      && event instanceof window.TouchEvent
    ) {
      const touch = event.changedTouches[0] || event.touches[0]
      if (touch) {
        return {
          screenX: Number(touch.screenX),
          screenY: Number(touch.screenY),
        }
      }
    }

    return null
  }

  isHomeSkipForwardActivationEvent(event: Event) {
    return (
      event.type === 'pointerdown'
      || event.type === 'mousedown'
      || event.type === 'touchstart'
      || event.type === 'click'
    )
  }

  forwardHomeSkipClick(event: Event) {
    if (!this.running || !event || !this.activeSessionId) {
      return false
    }
    if (!this.isHomeSkipForwardActivationEvent(event)) {
      return false
    }

    const rect = this.homeSkipButtonScreenRect
    if (!rect) {
      return false
    }

    const point = this.getEventScreenPoint(event)
    const screenX = point && Number.isFinite(point.screenX) ? point.screenX : NaN
    const screenY = point && Number.isFinite(point.screenY) ? point.screenY : NaN
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
    this.requestPluginDashboardSkip({
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
    this.syncControlBanner(true)
  }

  showCursor(x: number, y: number) {
    this.activateOverlayShell()
    this.cursorPosition = { x, y }
    this.lastCursorTarget = { x, y }
    this.syncPointer()
  }

  getRenderedCursorPosition() {
    return this.cursorPosition
  }

  cancelCursorMotion() {
    this.cursorMotionToken += 1
    this.cursorTransitionActive = false
    this.setPointerTransitionDuration(0)
  }

  setPointerTransitionDuration(durationMs = 0) {
    if (!this.pointer) {
      return
    }

    this.pointer.style.setProperty(
      '--yui-guide-plugin-pointer-duration',
      `${Math.max(0, Math.round(durationMs))}ms`,
    )
  }

  syncPointer(durationMs = 0) {
    this.ensureRoot()
    if (!this.pointer) {
      return
    }

    this.setPointerTransitionDuration(durationMs)
    const position = this.cursorPosition
    if (!position || !Number.isFinite(position.x) || !Number.isFinite(position.y)) {
      this.pointer.classList.remove('is-visible')
      this.pointer.style.transform = 'translate3d(-9999px, -9999px, 0)'
      return
    }

    this.pointer.style.transform = `translate3d(${Math.round(position.x - 20)}px, ${Math.round(position.y - 18)}px, 0)`
    this.pointer.classList.add('is-visible')
  }

  moveCursor(
    x: number,
    y: number,
    durationMs = 480,
    isCurrent?: () => boolean,
    waitForSceneResume = true,
  ) {
    this.ensureRoot()
    if (!this.cursorPosition) {
      this.showCursor(x, y)
      return Promise.resolve(true)
    }

    const motionToken = ++this.cursorMotionToken
    this.cursorTransitionActive = true

    return new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (completed: boolean) => {
        if (settled) {
          return
        }
        settled = true
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
          const didComplete = completed && motionToken === this.cursorMotionToken
          resolve(didComplete)
        }
        void finalize()
      }

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
        this.cursorPosition = { x, y }
        this.lastCursorTarget = { x, y }
        this.syncPointer(durationMs)
      })
      window.setTimeout(() => finish(true), durationMs + 80)
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

  clickCursor(durationMs = DEFAULT_CURSOR_CLICK_VISIBLE_MS) {
    if (this.cursorClickResetTimer !== null) {
      window.clearTimeout(this.cursorClickResetTimer)
      this.cursorClickResetTimer = null
    }
    document.documentElement.classList.add('yui-taking-over')
    document.body.classList.add('yui-taking-over')
    this.pointer?.classList.add('is-pressed')
    this.cursorClickResetTimer = window.setTimeout(() => {
      this.cursorClickResetTimer = null
      this.resetCursorVisualState()
    }, Math.max(0, durationMs))
  }

  resetCursorVisualState() {
    if (this.cursorClickResetTimer !== null) {
      window.clearTimeout(this.cursorClickResetTimer)
      this.cursorClickResetTimer = null
    }
    this.pointer?.classList.remove('is-pressed')
    if (!this.userCursorRevealSuppressed) {
      document.documentElement.classList.remove('yui-user-cursor-revealed')
      document.body.classList.remove('yui-user-cursor-revealed')
    }
  }

  stopGhostCursorAnimation() {
    this.cancelCursorMotion()
    this.resetCursorVisualState()
    this.cursorTransitionActive = false
    this.cursorPosition = null
    this.lastCursorTarget = null
    this.syncPointer()
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
          this.cursorPosition = { x, y }
          this.lastCursorTarget = { x, y }
          this.syncPointer(0)

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
    this.resetInterruptShakeMotion()
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
    this.homeNarrationOwnedByOpener = false
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

    narration.resumeAudioOffsetMs = this.getSafeNarrationResumeAudioOffsetMs(currentGuideAudio)
    narration.interrupted = true
    this.clearNarrationResumeTimer()
    stopCurrentGuideSpeech()
    return true
  }

  getSafeNarrationResumeAudioOffsetMs(audio: HTMLAudioElement | null) {
    if (!audio || !Number.isFinite(audio.currentTime)) {
      return 0
    }

    const rawOffsetMs = Math.max(0, Math.round(audio.currentTime * 1000))
    const durationMs = Number.isFinite(audio.duration) && audio.duration > 0
      ? Math.round(audio.duration * 1000)
      : 0
    const maxResumeOffsetMs = durationMs > 0
      ? Math.max(0, durationMs - NARRATION_RESUME_MIN_REMAINING_MS)
      : rawOffsetMs
    return clamp(
      rawOffsetMs - NARRATION_RESUME_BACKTRACK_MS,
      0,
      maxResumeOffsetMs,
    )
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

  resetInterruptShakeMotion() {
    this.interruptShakeMotion = createInterruptShakeMotion()
  }

  getInterruptShakePoint(event: MouseEvent, now: number) {
    const screenX = Number.isFinite(event.screenX) ? event.screenX : null
    const screenY = Number.isFinite(event.screenY) ? event.screenY : null
    const hasScreenPoint = screenX !== null && screenY !== null
    return {
      x: hasScreenPoint ? screenX : event.clientX,
      y: hasScreenPoint ? screenY : event.clientY,
      at: now,
    }
  }

  trackInterruptShakeMotion(point: { x: number; y: number; at: number }) {
    const motion = this.interruptShakeMotion
    if (motion.lastX === null || motion.lastY === null) {
      motion.lastX = point.x
      motion.lastY = point.y
      motion.lastAt = point.at
      return false
    }

    const vector: InterruptShakeVector = {
      dx: point.x - motion.lastX,
      dy: point.y - motion.lastY,
      distance: 0,
    }
    vector.distance = Math.hypot(vector.dx, vector.dy)
    if (!Number.isFinite(vector.distance) || vector.distance < DEFAULT_INTERRUPT_SHAKE_MIN_DISTANCE) {
      return false
    }

    const elapsedMs = point.at - motion.lastAt
    let shakeReady = false
    if (elapsedMs > 0 && isInterruptShakeReversal(motion.lastVector, vector)) {
      const cutoff = point.at - DEFAULT_INTERRUPT_SHAKE_WINDOW_MS
      motion.reversals = motion.reversals.filter((item) => item.at >= cutoff)
      motion.reversals.push({
        at: point.at,
        distance: vector.distance,
      })
      shakeReady = isInterruptShakeReady(motion.reversals)
    }

    motion.lastX = point.x
    motion.lastY = point.y
    motion.lastAt = point.at
    motion.lastVector = vector
    return shakeReady
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
    this.resetInterruptShakeMotion()
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

    if (!this.canRequestHomeInterruptPlayback() && typeof document.hasFocus === 'function' && !document.hasFocus()) {
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
    const shakePoint = this.getInterruptShakePoint(event, now)
    const previousPoint = this.lastPointerPoint
    if (!previousPoint || !Number.isFinite(previousPoint.t)) {
      this.lastPointerPoint = { x, y, t: now, speed: 0 }
      this.resetInterruptShakeMotion()
      this.trackInterruptShakeMotion(shakePoint)
      return
    }

    const dx = x - previousPoint.x
    const dy = y - previousPoint.y
    const distance = Math.hypot(dx, dy)
    const dt = Math.max(1, now - previousPoint.t)
    const speed = distance / dt

    this.lastPointerPoint = { x, y, t: now, speed }
    this.noteUserCursorRevealSuppressionAttempt(distance, now)
    this.maybePlayPassiveResistance(x, y, distance, speed, now)

    if (
      this.homeNarrationOwnedByOpener
      && !this.homeNarrationFinished
      && !this.canRequestHomeInterruptPlayback()
    ) {
      this.resetInterruptShakeMotion()
      return
    }

    if (!this.trackInterruptShakeMotion(shakePoint)) {
      return
    }
    this.resetInterruptShakeMotion()

    if ((now - this.lastInterruptAt) < DEFAULT_INTERRUPT_THROTTLE_MS) {
      return
    }
    this.lastInterruptAt = now
    this.interruptCount += 1
    this.cancelCursorMotion()

    if (this.interruptCount >= 4) {
      void this.abortAsAngryExit()
      return
    }

    void this.playLightResistance(x, y)
  }

  noteUserCursorRevealSuppressionAttempt(distance: number, now: number) {
    if (
      this.userCursorRevealSuppressed
      || !Number.isFinite(distance)
      || distance < DEFAULT_USER_CURSOR_REVEAL_DISTANCE
      || !document.body.classList.contains('yui-taking-over')
    ) {
      return
    }

    if ((now - this.lastUserCursorRevealMoveAt) < DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS) {
      return
    }

    this.lastUserCursorRevealMoveAt = now
    this.userCursorRevealMoveCount += 1
    if (this.userCursorRevealMoveCount >= DEFAULT_USER_CURSOR_REVEAL_MOVES) {
      this.suppressUserCursorReveal()
    }
  }

  suppressUserCursorReveal() {
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
      this.resistanceCursorTimer = null
    }
    this.userCursorRevealSuppressed = true
    document.documentElement.classList.remove('yui-user-cursor-revealed')
    document.body.classList.remove('yui-user-cursor-revealed')
    document.documentElement.classList.remove('yui-resistance-cursor-reveal')
    document.body.classList.remove('yui-resistance-cursor-reveal')
  }

  clearUserCursorRevealSuppression() {
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
      this.resistanceCursorTimer = null
    }
    this.userCursorRevealSuppressed = false
    this.userCursorRevealMoveCount = 0
    this.lastUserCursorRevealMoveAt = 0
    document.documentElement.classList.remove('yui-user-cursor-revealed')
    document.documentElement.classList.remove('yui-resistance-cursor-reveal')
    document.body.classList.remove('yui-user-cursor-revealed')
    document.body.classList.remove('yui-resistance-cursor-reveal')
  }

  suppressResistanceCursorReveal() {
    if (this.userCursorRevealSuppressed) {
      this.suppressUserCursorReveal()
      return
    }
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
      this.resistanceCursorTimer = null
    }
    document.documentElement.classList.remove('yui-user-cursor-revealed')
    document.body.classList.remove('yui-user-cursor-revealed')
    document.documentElement.classList.remove('yui-resistance-cursor-reveal')
    document.body.classList.remove('yui-resistance-cursor-reveal')
  }

  revealSystemCursorTemporarily(durationMs = 2000) {
    const normalizedDurationMs = Math.min(10000, Math.max(0, Math.floor(Number(durationMs) || 0)))
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
      this.resistanceCursorTimer = null
    }
    document.documentElement.classList.add('yui-user-cursor-revealed', 'yui-resistance-cursor-reveal')
    document.body.classList.add('yui-user-cursor-revealed', 'yui-resistance-cursor-reveal')
    this.resistanceCursorTimer = window.setTimeout(() => {
      this.resistanceCursorTimer = null
      this.suppressResistanceCursorReveal()
    }, normalizedDurationMs)
  }

  async playLightResistance(x: number, y: number) {
    if (this.scenePausedForResistance || this.angryExitTriggered) {
      return
    }

    const sessionAtStart = this.activeSessionId
    const isSameSession = () => this.running && this.activeSessionId === sessionAtStart

    this.pauseCurrentSceneForResistance()
    this.interruptNarrationForResistance()
    this.suppressResistanceCursorReveal()
    this.emphasizeControlBanner()

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
    this.stopGhostCursorAnimation()
    this.resetInterruptShakeMotion()
    this.clearSpotlight()
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
    this.requestPluginDashboardSkip({
      source: 'plugin_dashboard_angry_exit',
      reason: 'angry_exit',
      interruptCount: this.interruptCount,
    })
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
    document.documentElement.classList.remove('yui-user-cursor-revealed')
    document.body.classList.remove('yui-guide-plugin-dashboard-running')
    document.body.classList.remove('yui-taking-over')
    document.body.classList.remove('yui-resistance-cursor-reveal')
    document.body.classList.remove('yui-user-cursor-revealed')
    this.syncControlBanner(false)
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
    this.userCursorRevealSuppressed = false
    this.userCursorRevealMoveCount = 0
    this.lastUserCursorRevealMoveAt = 0
    this.resetCursorVisualState()
    this.clearPendingInterruptAck(false)
    this.lastForwardedSkipAt = 0
    this.lastForwardedSkipScreenX = NaN
    this.lastForwardedSkipScreenY = NaN
    if (this.spotlightRefreshRaf !== null) {
      window.cancelAnimationFrame(this.spotlightRefreshRaf)
      this.spotlightRefreshRaf = null
    }
    window.removeEventListener('resize', this.boundScheduleSpotlightRefresh, true)
    window.removeEventListener('scroll', this.boundScheduleSpotlightRefresh, true)
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
    this.clearDesktopSkipButton()
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
    this.controlBanner = null
    if (this.controlBannerEmphasisTimer !== null) {
      window.clearTimeout(this.controlBannerEmphasisTimer)
      this.controlBannerEmphasisTimer = null
    }
    this.controlBannerEmphasisActive = false
    this.renderedControlBannerText = ''
    this.renderedControlBannerVisible = null
    this.renderedControlBannerEmphasis = null
    this.spotlight = null
    this.pointer = null
    this.cursorPosition = null
    this.spotlightElement = null
    this.lastCursorTarget = null
    this.running = false
    this.activeSessionId = ''
    this.desktopSkipButton = null
    this.desktopSkipButtonCleanup = null
    this.interruptsEnabled = false
    this.scenePausedForResistance = false
    this.homeNarrationFinished = false
    this.homeNarrationOwnedByOpener = false
    this.angryExitTriggered = false
    this.interruptCount = 0
    this.resetInterruptShakeMotion()
    this.lastInterruptAt = 0
    this.lastPassiveResistanceAt = 0
    this.lastPointerPoint = null
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
    this.homeNarrationOwnedByOpener = false
    const isCurrent = () => this.isCurrentRun(sessionId)
    this.activateOverlayShell()
    this.ensureDesktopSkipButton()
    window.addEventListener('resize', this.boundScheduleSpotlightRefresh, true)
    window.addEventListener('scroll', this.boundScheduleSpotlightRefresh, true)
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
    this.homeNarrationOwnedByOpener = true
    const isCurrentWithoutAngryExit = () => isCurrent() && !this.angryExitTriggered
    const homeNarrationFallbackActiveMs = clamp(
      budgetMs + PLUGIN_DASHBOARD_NARRATION_FINISH_FALLBACK_EXTRA_MS,
      PLUGIN_DASHBOARD_NARRATION_FINISH_FALLBACK_EXTRA_MS,
      120000,
    )
    let homeNarrationFallbackTimer: number | null = null
    let homeNarrationFallbackCancelled = false
    let homeNarrationFallbackRemainingMs = homeNarrationFallbackActiveMs
    let homeNarrationFallbackLastTickAt = Date.now()
    const clearHomeNarrationFallbackTimer = () => {
      homeNarrationFallbackCancelled = true
      if (homeNarrationFallbackTimer !== null) {
        window.clearTimeout(homeNarrationFallbackTimer)
        homeNarrationFallbackTimer = null
      }
    }
    const tickHomeNarrationFallback = () => {
      if (
        homeNarrationFallbackCancelled
        || this.homeNarrationFinished
        || this.angryExitTriggered
        || !isCurrent()
      ) {
        return
      }

      const now = Date.now()
      const pausedForInterrupt = this.scenePausedForResistance || !!this.pendingInterruptAck
      if (!pausedForInterrupt) {
        homeNarrationFallbackRemainingMs -= Math.max(0, now - homeNarrationFallbackLastTickAt)
      }
      homeNarrationFallbackLastTickAt = now
      if (homeNarrationFallbackRemainingMs <= 0) {
        homeNarrationFallbackTimer = null
        this.markHomeNarrationFinished(sessionId)
        return
      }

      homeNarrationFallbackTimer = window.setTimeout(tickHomeNarrationFallback, 250)
    }
    homeNarrationFallbackTimer = window.setTimeout(tickHomeNarrationFallback, 250)
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

    if (!isCurrentWithoutAngryExit()) {
      clearHomeNarrationFallbackTimer()
      return
    }
    this.setSpotlight(mainContainer)
    if (moveToMainDurationMs > 0) {
      await this.moveCursorToElementWithRecovery(mainContainer, moveToMainDurationMs, isCurrentWithoutAngryExit)
      if (!isCurrentWithoutAngryExit()) {
        clearHomeNarrationFallbackTimer()
        return
      }
    }
    if (scrollDownDurationMs > 0) {
      await this.animateScroll(mainContainer, 150, scrollDownDurationMs, isCurrentWithoutAngryExit)
      if (!isCurrentWithoutAngryExit()) {
        clearHomeNarrationFallbackTimer()
        return
      }
    }
    if (scrollUpDurationMs > 0) {
      await this.animateScroll(mainContainer, -150, scrollUpDurationMs, isCurrentWithoutAngryExit)
      if (!isCurrentWithoutAngryExit()) {
        clearHomeNarrationFallbackTimer()
        return
      }
    }
    if (ellipseDurationMs > 0) {
      await this.runEllipse(mainContainer, ellipseDurationMs, isCurrentWithoutAngryExit)
      if (!isCurrentWithoutAngryExit()) {
        clearHomeNarrationFallbackTimer()
        return
      }
    }

    while (isCurrentWithoutAngryExit() && !this.homeNarrationFinished) {
      this.setSpotlight(mainContainer)
      await this.runEllipse(mainContainer, PLUGIN_DASHBOARD_IDLE_ELLIPSE_MS, isCurrentWithoutAngryExit)
      if (!isCurrent()) {
        clearHomeNarrationFallbackTimer()
        return
      }
      if (this.angryExitTriggered) {
        clearHomeNarrationFallbackTimer()
        return
      }
      if (!this.homeNarrationFinished) {
        await wait(80)
      }
    }

    if (this.angryExitTriggered) {
      clearHomeNarrationFallbackTimer()
      return
    }

    try {
      if (!(await this.waitForHomeNarrationFinished(sessionId, isCurrent))) {
        return
      }
    } finally {
      clearHomeNarrationFallbackTimer()
    }
    if (!isCurrent()) {
      return
    }
    if (this.angryExitTriggered) {
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
    skipButton.setAttribute('data-yui-plugin-dashboard-skip-control', 'true')
    skipButton.style.border = '0'
    skipButton.style.borderRadius = '999px'
    skipButton.style.padding = '8px 14px'
    skipButton.style.background = 'rgba(107, 170, 255, 0.18)'
    skipButton.style.color = '#eef7ff'
    skipButton.style.fontSize = '13px'
    skipButton.style.fontWeight = '600'
    skipButton.style.cursor = 'pointer'
    const stopSkipEvent = (event: Event) => {
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation()
      }
      if (typeof event.stopPropagation === 'function') {
        event.stopPropagation()
      }
    }
    const handleSkip = (event: Event) => {
      if (typeof event.preventDefault === 'function') {
        event.preventDefault()
      }
      stopSkipEvent(event)
      this.requestCancel()
    }
    skipButton.addEventListener('pointerdown', stopSkipEvent)
    skipButton.addEventListener('pointerup', stopSkipEvent)
    skipButton.addEventListener('mousedown', stopSkipEvent)
    skipButton.addEventListener('mouseup', stopSkipEvent)
    skipButton.addEventListener('touchstart', stopSkipEvent)
    skipButton.addEventListener('touchend', stopSkipEvent)
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
let pluginDashboardRuntimeInitialized = false

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
  if (pluginDashboardRuntimeInitialized) {
    return
  }
  pluginDashboardRuntimeInitialized = true

  const runtime = new PluginDashboardGuideRuntime()
  let receivedStartMessage = false
  runtime.preactivatePendingOverlay()

  const handleDesktopInterruptAckEvent = (event: Event) => {
    runtime.handleDesktopInterruptAckEvent(event)
  }
  const handleDesktopNarrationFinishedEvent = (event: Event) => {
    runtime.handleDesktopNarrationFinishedEvent(event)
  }
  const handleDesktopSystemCursorTemporaryRevealEvent = (event: Event) => {
    runtime.handleDesktopSystemCursorTemporaryRevealEvent(event)
  }

  const handleRuntimeMessage = (event: MessageEvent) => {
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

    if (data.type === SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT && isAllowedOpenerEvent(event)) {
      runtime.handleSystemCursorTemporaryRevealData(data)
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
  }

  const cleanupRuntimeListeners = () => {
    window.removeEventListener(DESKTOP_INTERRUPT_ACK_EVENT, handleDesktopInterruptAckEvent, true)
    window.removeEventListener(DESKTOP_NARRATION_FINISHED_EVENT, handleDesktopNarrationFinishedEvent, true)
    window.removeEventListener(DESKTOP_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT, handleDesktopSystemCursorTemporaryRevealEvent, true)
    window.removeEventListener('message', handleRuntimeMessage)
    window.removeEventListener('pagehide', handleRuntimePageHide, true)
    pluginDashboardRuntimeInitialized = false
  }
  const handleRuntimePageHide = () => {
    cleanupRuntimeListeners()
  }

  window.addEventListener(DESKTOP_INTERRUPT_ACK_EVENT, handleDesktopInterruptAckEvent, true)
  window.addEventListener(DESKTOP_NARRATION_FINISHED_EVENT, handleDesktopNarrationFinishedEvent, true)
  window.addEventListener(DESKTOP_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT, handleDesktopSystemCursorTemporaryRevealEvent, true)
  window.addEventListener('message', handleRuntimeMessage)
  window.addEventListener('pagehide', handleRuntimePageHide, true)
}
