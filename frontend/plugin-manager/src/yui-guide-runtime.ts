import defaultGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/default-ghost-cursor.png'
import clickGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/click-ghost-cursor.png'
import leftCatEarUrl from '../../../static/assets/tutorial/highlight/left-cat-ear.png'
import rightCatEarUrl from '../../../static/assets/tutorial/highlight/right-cat-ear.png'
import catPawUrl from '../../../static/assets/tutorial/highlight/cat-paw.png'
import { getLocale } from './i18n'
import router from './router'

const START_EVENT = 'neko:yui-guide:plugin-dashboard:start'
const READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready'
const DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done'
const INTERRUPT_REQUEST_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-request'
const INTERRUPT_ACK_EVENT = 'neko:yui-guide:plugin-dashboard:interrupt-ack'
const LOCAL_TUTORIAL_START_EVENT = 'neko:plugin-dashboard-tutorial:start'
const LOCAL_TUTORIAL_ACTION_EVENT = 'neko:plugin-tutorial:action'
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
const LOCAL_TUTORIAL_ADVANCE_COOLDOWN_MS = 500
const SCRIPTED_MOTION_INTERRUPT_STREAK = 2
const DEFAULT_PASSIVE_RESISTANCE_DISTANCE = 10
const DEFAULT_PASSIVE_RESISTANCE_SPEED_THRESHOLD = 0.2
const DEFAULT_PASSIVE_RESISTANCE_INTERVAL_MS = 140
const DEFAULT_RESISTANCE_CURSOR_REVEAL_MS = 3000
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
const GUIDE_AUDIO_BY_KEY = {
  takeover_plugin_preview_dashboard: {
    zh: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
    en: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
    ja: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
    ko: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
    ru: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
  },
  interrupt_resist_light_1: {
    zh: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
    en: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
    ja: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
    ko: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
    ru: '喂！不要拽我啦，还没轮到你的回合呢！.mp3',
  },
  interrupt_resist_light_3: {
    zh: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
    en: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
    ja: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
    ko: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
    ru: '等一下啦！还没结束呢，不要随便打断我啦！.mp3',
  },
  interrupt_angry_exit: {
    zh: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
    en: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
    ja: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
    ko: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
    ru: '人类~~~~！你真的很没礼貌喵！既然你这么想自己操作，那你就自己对着冰冷的屏幕玩去吧！哼！.mp3',
  },
} as const

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
}

export type PluginDashboardLocalTutorialStep = {
  targetId?: string
  title: string
  body: string
  route?: string
  action?: string
  waitMs?: number
  allowMissing?: boolean
  motion?: 'point' | 'ellipse' | 'click'
  durationMs?: number
}

export type PluginDashboardLocalTutorialPayload = {
  steps: PluginDashboardLocalTutorialStep[]
  labels?: {
    skip?: string
    keyboardHint?: string
  }
}

type SpotlightRect = {
  left: number
  top: number
  width: number
  height: number
  radius: number
  padding: number
}

type ActiveNarration = {
  text: string
  voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
  audioUrl?: string
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

function resolveSpeechLang() {
  const locale = resolveGuideLocale()
  if (locale === 'ja') return 'ja-JP'
  if (locale === 'en') return 'en-US'
  if (locale === 'ko') return 'ko-KR'
  if (locale === 'ru') return 'ru-RU'
  return 'zh-CN'
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

function playGuideAudioWithPromise(audioSrc: string, minimumDurationMs: number) {
  const normalizedAudioSrc = typeof audioSrc === 'string' ? audioSrc.trim() : ''
  if (!normalizedAudioSrc) {
    return Promise.reject(new Error('missing_audio_src'))
  }

  return new Promise<void>((resolve, reject) => {
    let settled = false
    const audio = new Audio(normalizedAudioSrc)
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

    audio.preload = 'auto'
    audio.onloadedmetadata = () => {
      const durationMs = Number.isFinite(audio.duration) && audio.duration > 0
        ? Math.round(audio.duration * 1000)
        : 0
      cacheGuideAudioDuration(normalizedAudioSrc, audio.duration)
      finishMetadataDuration(durationMs)
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
      const playback = audio.play()
      if (playback && typeof playback.then === 'function') {
        playback.catch((error: unknown) => finish(false, error))
      }
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

  if (!spotlight.querySelector('.yui-guide-plugin-spotlight-chrome')) {
    const chrome = document.createElement('div')
    chrome.className = 'yui-guide-plugin-spotlight-chrome'
    spotlight.appendChild(chrome)
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
  },
): Promise<void> {
  const content = typeof text === 'string' ? text.trim() : ''
  if (!content) {
    return Promise.resolve()
  }

  const minDurationMs = estimateSpeechDurationMs(content)
  const localAudioSrc = resolveGuideAudioSrc(options?.voiceKey, options?.audioUrl)
  if (localAudioSrc) {
    return playGuideAudioWithPromise(localAudioSrc, minDurationMs).catch(() => {
      return speakTextWithPromise(content)
    })
  }

  if (typeof window.speechSynthesis === 'undefined' || typeof window.SpeechSynthesisUtterance === 'undefined') {
    return wait(minDurationMs)
  }

  return new Promise<void>((resolve) => {
    let settled = false
    const utterance = new SpeechSynthesisUtterance(content)
    utterance.lang = resolveSpeechLang()
    utterance.rate = 1
    utterance.pitch = 1.1

    const finish = () => {
      if (settled) {
        return
      }
      settled = true
      window.clearTimeout(timerId)
      if (currentGuideSpeechStop === stop) {
        currentGuideSpeechStop = null
      }
      resolve()
    }

    utterance.onend = finish
    utterance.onerror = finish

    const timerId = window.setTimeout(finish, minDurationMs + 1200)
    const stop = () => {
      try {
        window.speechSynthesis.cancel()
      } catch (_) {}
      finish()
    }
    currentGuideSpeechStop = stop

    try {
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(utterance)
    } catch (_) {
      finish()
    }
  })
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

function injectStyle() {
  if (document.getElementById(`${ROOT_ID}-style`)) {
    return
  }

  const style = document.createElement('style')
  style.id = `${ROOT_ID}-style`
  style.textContent = `
    html.yui-guide-plugin-dashboard-running,
    html.yui-guide-plugin-dashboard-running *,
    body.yui-guide-plugin-dashboard-running,
    body.yui-guide-plugin-dashboard-running * {
      cursor: none !important;
    }

    html.yui-taking-over,
    html.yui-taking-over *,
    body.yui-taking-over,
    body.yui-taking-over * {
      cursor: none !important;
    }

    html.yui-taking-over.yui-resistance-cursor-reveal,
    html.yui-taking-over.yui-resistance-cursor-reveal *,
    body.yui-taking-over.yui-resistance-cursor-reveal,
    body.yui-taking-over.yui-resistance-cursor-reveal * {
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
      opacity: 1;
      transition: opacity 180ms ease;
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
      background: linear-gradient(180deg, rgba(84, 133, 255, 0.09), rgba(89, 211, 255, 0.03));
      box-shadow:
        0 0 0 1px rgba(214, 243, 255, 0.72),
        0 0 18px rgba(104, 194, 255, 0.56),
        0 0 34px rgba(87, 136, 255, 0.26),
        inset 0 0 16px rgba(131, 214, 255, 0.16);
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
      animation: yui-guide-plugin-click 240ms ease;
    }

    #${ROOT_ID} .yui-guide-plugin-card {
      position: fixed;
      right: 32px;
      bottom: 32px;
      z-index: 3;
      width: min(360px, calc(100vw - 40px));
      padding: 18px;
      border: 1px solid rgba(147, 214, 255, 0.42);
      border-radius: 18px;
      background: color-mix(in srgb, var(--el-bg-color, #ffffff) 92%, transparent);
      box-shadow: 0 18px 48px rgba(15, 23, 42, 0.28);
      color: var(--el-text-color-primary, #1f2937);
      pointer-events: auto;
      backdrop-filter: blur(18px) saturate(1.25);
      -webkit-backdrop-filter: blur(18px) saturate(1.25);
    }

    #${ROOT_ID} .yui-guide-plugin-card[hidden] {
      display: none;
    }

    #${ROOT_ID} .yui-guide-plugin-card-title {
      margin: 0 0 8px;
      font-size: 16px;
      font-weight: 700;
      line-height: 1.4;
    }

    #${ROOT_ID} .yui-guide-plugin-card-body {
      margin: 0;
      color: var(--el-text-color-regular, #4b5563);
      font-size: 13px;
      line-height: 1.7;
    }

    #${ROOT_ID} .yui-guide-plugin-card-hint {
      margin: 10px 0 0;
      color: var(--el-text-color-secondary, #6b7280);
      font-size: 12px;
      line-height: 1.5;
    }

    #${ROOT_ID} .yui-guide-plugin-card-actions {
      display: flex;
      justify-content: flex-end;
      margin-top: 14px;
    }

    #${ROOT_ID} .yui-guide-plugin-skip {
      border: 1px solid color-mix(in srgb, var(--el-border-color, #dcdfe6) 70%, transparent);
      border-radius: 999px;
      padding: 6px 12px;
      background: transparent;
      color: var(--el-text-color-regular, #4b5563);
      cursor: pointer;
      font: inherit;
      font-size: 12px;
    }

    #${ROOT_ID} .yui-guide-plugin-skip:hover {
      border-color: var(--el-color-primary, #409eff);
      color: var(--el-color-primary, #409eff);
    }

    @keyframes yui-guide-plugin-pulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.02); }
    }

    @keyframes yui-guide-plugin-click {
      0% { transform: scale(1); }
      45% { transform: scale(0.82); }
      100% { transform: scale(1); }
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
  guideCard: HTMLElement | null = null
  guideCardTitle: HTMLHeadingElement | null = null
  guideCardBody: HTMLParagraphElement | null = null
  guideCardHint: HTMLParagraphElement | null = null
  guideCardSkip: HTMLButtonElement | null = null
  cursorPosition: { x: number; y: number } | null = null
  lastCursorTarget: { x: number; y: number } | null = null
  spotlightElement: Element | null = null
  activeSessionId = ''
  running = false
  interruptsEnabled = false
  scenePausedForResistance = false
  angryExitTriggered = false
  interruptCount = 0
  interruptAccelerationStreak = 0
  lastInterruptAt = 0
  lastPassiveResistanceAt = 0
  lastPointerPoint: { x: number; y: number; t: number; speed: number } | null = null
  resistanceCursorTimer: number | null = null
  narrationResumeTimer: number | null = null
  scenePauseResolvers: Array<() => void> = []
  cursorMotionToken = 0
  cursorReactionInFlight = false
  cursorTransitionActive = false
  activeNarration: ActiveNarration | null = null
  pendingInterruptAck: PendingInterruptAck | null = null
  localTutorialStepAdvanceReadyAt = 0
  localTutorialStepAdvance: (() => void) | null = null
  preactivationTimeoutId: number | null = null
  boundPointerMoveHandler = (event: PointerEvent | MouseEvent) => {
    this.handleInterrupt(event)
  }
  boundPointerDownHandler = (event: PointerEvent | MouseEvent) => {
    this.onPointerDown(event)
  }
  boundInteractionGuard = (event: Event) => {
    if (!this.running || !event || (event as { isTrusted?: boolean }).isTrusted === false) {
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
  boundLocalTutorialKeydown = (event: KeyboardEvent) => {
    this.handleLocalTutorialKeydown(event)
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
      return
    }

    injectStyle()

    const root = document.createElement('div')
    root.id = ROOT_ID

    const backdrop = createSvgElement('svg', 'yui-guide-plugin-backdrop')
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
    backdropFill.setAttribute('fill', 'rgba(3, 7, 18, 0.76)')
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

    const guideCard = document.createElement('section')
    guideCard.className = 'yui-guide-plugin-card'
    guideCard.hidden = true
    guideCard.setAttribute('role', 'dialog')
    guideCard.setAttribute('aria-live', 'polite')
    const guideCardTitle = document.createElement('h3')
    guideCardTitle.className = 'yui-guide-plugin-card-title'
    const guideCardBody = document.createElement('p')
    guideCardBody.className = 'yui-guide-plugin-card-body'
    const guideCardHint = document.createElement('p')
    guideCardHint.className = 'yui-guide-plugin-card-hint'
    const guideCardActions = document.createElement('div')
    guideCardActions.className = 'yui-guide-plugin-card-actions'
    const guideCardSkip = document.createElement('button')
    guideCardSkip.type = 'button'
    guideCardSkip.className = 'yui-guide-plugin-skip'
    guideCardActions.appendChild(guideCardSkip)
    guideCard.appendChild(guideCardTitle)
    guideCard.appendChild(guideCardBody)
    guideCard.appendChild(guideCardHint)
    guideCard.appendChild(guideCardActions)

    root.appendChild(backdrop)
    root.appendChild(interactionShield)
    root.appendChild(spotlight)
    root.appendChild(guideCard)
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
    this.guideCard = guideCard
    this.guideCardTitle = guideCardTitle
    this.guideCardBody = guideCardBody
    this.guideCardHint = guideCardHint
    this.guideCardSkip = guideCardSkip
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

    this.cursorShell.classList.add('is-visible')
    this.cursorShell.style.transitionDuration = '0ms'
    this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
    this.cursorPosition = { x, y }
    this.lastCursorTarget = { x, y }
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

    return new Promise<boolean>((resolve) => {
      let settled = false
      const finish = (completed: boolean) => {
        if (settled) {
          return
        }
        settled = true
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

      this.cursorShell?.addEventListener('transitionend', handleEnd)
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

  clickCursor() {
    if (!this.cursorInner) {
      return
    }

    this.cursorInner.classList.remove('is-clicking')
    void this.cursorInner.offsetWidth
    this.cursorInner.classList.add('is-clicking')
    window.setTimeout(() => {
      this.cursorInner?.classList.remove('is-clicking')
    }, 260)
  }

  resetCursorVisualState() {
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
    const startedAt = performance.now()
    let pausedAt: number | null = null
    let pausedDurationMs = 0
    this.cursorTransitionActive = true

    try {
      await new Promise<void>((resolve) => {
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
          const angle = progress * Math.PI * 2
          const x = centerX + Math.cos(angle) * radiusX
          const y = centerY + Math.sin(angle) * radiusY
          if (this.cursorShell) {
            this.cursorShell.style.transitionDuration = '80ms'
            this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
            this.cursorPosition = { x, y }
            this.lastCursorTarget = { x, y }
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
      this.cursorTransitionActive = false
    }
  }

  async speakLine(
    text: string,
    options?: {
      voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
      audioUrl?: string
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
        interrupted: false,
        cancelled: false,
        playVersion: 0,
        resolve,
      }
      this.activeNarration = narration
      this.playNarration(narration)
    })
  }

  requestLocalTutorialStepAdvance() {
    if (!this.running || Date.now() < this.localTutorialStepAdvanceReadyAt) {
      return false
    }
    const advance = this.localTutorialStepAdvance
    if (!advance) {
      return false
    }
    this.localTutorialStepAdvance = null
    this.cancelActiveNarration()
    this.cancelCursorMotion()
    advance()
    return true
  }

  handleLocalTutorialKeydown(event: KeyboardEvent) {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }

    event.preventDefault()
    event.stopPropagation()
    this.requestLocalTutorialStepAdvance()
  }

  showLocalGuideCard(step: PluginDashboardLocalTutorialStep, labels?: PluginDashboardLocalTutorialPayload['labels']) {
    this.ensureRoot()
    if (!this.guideCard || !this.guideCardTitle || !this.guideCardBody || !this.guideCardHint || !this.guideCardSkip) {
      return
    }

    this.guideCardTitle.textContent = step.title
    this.guideCardBody.textContent = step.body
    this.guideCardHint.textContent = labels?.keyboardHint || ''
    this.guideCardSkip.textContent = labels?.skip || 'Skip'
    this.guideCardSkip.onclick = () => {
      this.cleanup()
    }
    this.guideCard.hidden = false
  }

  hideLocalGuideCard() {
    if (this.guideCard) {
      this.guideCard.hidden = true
    }
    if (this.guideCardSkip) {
      this.guideCardSkip.onclick = null
    }
  }

  resolveLocalGuideTarget(step: PluginDashboardLocalTutorialStep) {
    const targetId = String(step.targetId || '').trim()
    if (!targetId) {
      return null
    }
    return document.querySelector(`[data-yui-guide-id="${CSS.escape(targetId)}"]`) as HTMLElement | null
  }

  async prepareLocalTutorialStep(step: PluginDashboardLocalTutorialStep) {
    const route = String(step.route || '').trim()
    if (route && router.currentRoute.value.fullPath !== route) {
      try {
        await router.push(route)
      } catch (error) {
        console.error(`[YuiGuide] Failed to navigate to route: ${route}`, error)
        throw error
      }
      await wait(220)
      if (router.currentRoute.value.fullPath !== route) {
        throw new Error(`Yui guide route mismatch: expected ${route}, got ${router.currentRoute.value.fullPath}`)
      }
    }

    const action = String(step.action || '').trim()
    if (action) {
      window.dispatchEvent(new CustomEvent(LOCAL_TUTORIAL_ACTION_EVENT, {
        detail: { action },
      }))
      await wait(step.waitMs ?? 280)
    } else if (step.waitMs && step.waitMs > 0) {
      await wait(step.waitMs)
    }
  }

  interruptNarrationForResistance() {
    const narration = this.activeNarration
    if (!narration || narration.cancelled) {
      return false
    }
    if (narration.interrupted) {
      return true
    }

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
    if (!event || event.isTrusted === false) {
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
  }

  handleInterrupt(event: MouseEvent) {
    if (
      !this.running
      || this.angryExitTriggered
      || this.scenePausedForResistance
      || !this.interruptsEnabled
      || !event
      || event.isTrusted === false
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

    if (distance < DEFAULT_INTERRUPT_DISTANCE) {
      this.interruptAccelerationStreak = 0
      return
    }
    if (speed < DEFAULT_INTERRUPT_SPEED_THRESHOLD) {
      this.interruptAccelerationStreak = 0
      return
    }
    const isScriptedMotionInterrupt = this.cursorTransitionActive
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
    this.clearSpotlight()
    this.resetCursorVisualState()
    this.setAngryVisual(true)
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
    }
    if (!isSameSession()) {
      return
    }
    this.notify(DONE_EVENT, this.activeSessionId)
    this.cleanup()
  }

  async runLocalTutorial(sessionId: string, payload: PluginDashboardLocalTutorialPayload) {
    const steps = Array.isArray(payload.steps)
      ? payload.steps.filter((step) => step && (step.targetId || step.route || step.action) && (step.title || step.body))
      : []
    if (steps.length === 0) {
      return
    }

    if (this.running) {
      this.cleanup()
    }

    this.running = true
    this.activeSessionId = sessionId
    this.localTutorialStepAdvanceReadyAt = Date.now() + LOCAL_TUTORIAL_ADVANCE_COOLDOWN_MS
    const isCurrent = () => this.isCurrentRun(sessionId)
    this.ensureRoot()
    window.addEventListener('resize', this.boundRefreshSpotlight, true)
    window.addEventListener('scroll', this.boundRefreshSpotlight, true)
    document.addEventListener('keydown', this.boundLocalTutorialKeydown, true)

    try {
      this.showCursor(window.innerWidth / 2, Math.max(72, window.innerHeight / 2))

      for (const step of steps) {
        if (!isCurrent()) {
          return
        }

        await this.prepareLocalTutorialStep(step)
        if (!isCurrent()) {
          return
        }

        const target = await this.waitForElement(() => this.resolveLocalGuideTarget(step), 2500)
        if (!isCurrent()) {
          return
        }
        if (!target) {
          this.clearSpotlight()
          this.hideLocalGuideCard()
          if (!step.allowMissing) {
            throw new Error(`Missing tutorial target: ${step.targetId || step.route || step.action || '(unknown)'}`)
          }
          continue
        }

        target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' })
        if (!(await this.waitForSceneDelay(240, isCurrent))) {
          return
        }

        this.setSpotlight(target)
        this.showLocalGuideCard(step, payload.labels)
        this.localTutorialStepAdvanceReadyAt = Date.now() + LOCAL_TUTORIAL_ADVANCE_COOLDOWN_MS
        let stepAdvanced = false
        const isCurrentStep = () => isCurrent() && !stepAdvanced
        const stepAdvancePromise = new Promise<void>((resolve) => {
          this.localTutorialStepAdvance = () => {
            stepAdvanced = true
            resolve()
          }
        })

        await Promise.race([
          this.moveCursorToElementWithRecovery(target, 620, isCurrentStep),
          stepAdvancePromise,
        ])
        if (!isCurrent()) {
          return
        }
        if (stepAdvanced) {
          this.localTutorialStepAdvance = null
          continue
        }

        if (step.motion === 'click') {
          this.clickCursor()
        } else if (step.motion === 'ellipse') {
          await Promise.race([
            this.runEllipse(target, Math.max(1200, step.durationMs || 2200), isCurrentStep),
            stepAdvancePromise,
          ])
        }

        if (!isCurrent()) {
          return
        }
        if (stepAdvanced) {
          this.localTutorialStepAdvance = null
          continue
        }

        await Promise.race([
          Promise.all([
            this.startNarration(step.body),
            this.waitForSceneDelay(Math.max(1400, step.durationMs || 2200), isCurrentStep),
          ]),
          stepAdvancePromise,
        ])
        this.localTutorialStepAdvance = null
      }
    } finally {
      if (isCurrent()) {
        this.cleanup()
      }
    }
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
    try {
      window.speechSynthesis?.cancel()
    } catch (_) {}
    this.cancelActiveNarration()
    if (this.resistanceCursorTimer !== null) {
      window.clearTimeout(this.resistanceCursorTimer)
      this.resistanceCursorTimer = null
    }
    this.clearPendingInterruptAck(false)
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
    document.removeEventListener('keydown', this.boundLocalTutorialKeydown, true)
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
    this.guideCard = null
    this.guideCardTitle = null
    this.guideCardBody = null
    this.guideCardHint = null
    this.guideCardSkip = null
    this.cursorPosition = null
    this.spotlightElement = null
    this.lastCursorTarget = null
    this.running = false
    this.activeSessionId = ''
    this.interruptsEnabled = false
    this.scenePausedForResistance = false
    this.angryExitTriggered = false
    this.interruptCount = 0
    this.interruptAccelerationStreak = 0
    this.lastInterruptAt = 0
    this.lastPassiveResistanceAt = 0
    this.lastPointerPoint = null
    this.narrationResumeTimer = null
    this.cursorMotionToken = 0
    this.cursorReactionInFlight = false
    this.cursorTransitionActive = false
    this.activeNarration = null
    this.pendingInterruptAck = null
    this.localTutorialStepAdvanceReadyAt = 0
    this.localTutorialStepAdvance = null
    this.clearPreactivationTimeout()
    this.scenePauseResolvers = []
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
    this.showCursor(startX, startY)
    this.setSpotlight(pluginButton)
    await this.moveCursorToElementWithRecovery(pluginButton, 700, isCurrent)
    if (!isCurrent()) {
      return
    }
    this.clickCursor()
    if (!isCurrent()) {
      return
    }
    pluginButton.click()
    if (!(await this.waitForSceneDelay(280, isCurrent))) {
      return
    }

    const speechPromise = this.startNarration(payload.line || '', {
      voiceKey: payload.voiceKey,
      audioUrl: payload.audioUrl,
    })
    const totalNarrationDurationMs = await resolveNarrationDurationMs(payload)
    const elapsedBeforeMotionMs = Number.isFinite(payload.narrationStartedAtMs)
      ? Math.max(0, Date.now() - Math.round(payload.narrationStartedAtMs as number))
      : 0
    const budgetMs = Math.max(0, totalNarrationDurationMs - elapsedBeforeMotionMs)
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
    await speechPromise
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

export function startPluginDashboardTutorial(payload: PluginDashboardLocalTutorialPayload) {
  window.dispatchEvent(new CustomEvent<PluginDashboardLocalTutorialPayload>(LOCAL_TUTORIAL_START_EVENT, {
    detail: payload,
  }))
}

export function initPluginDashboardYuiGuideRuntime() {
  const runtime = new PluginDashboardGuideRuntime()
  let receivedStartMessage = false
  runtime.preactivatePendingOverlay()

  window.addEventListener(LOCAL_TUTORIAL_START_EVENT, (event: Event) => {
    const payload = (event as CustomEvent<PluginDashboardLocalTutorialPayload>).detail
    const sessionId = `local-${Date.now()}`
    runtime.runLocalTutorial(sessionId, payload).catch(() => {
      if (!runtime.isCurrentRun(sessionId)) {
        return
      }
      runtime.cleanup()
    })
  })

  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data
    if (!data || typeof data !== 'object') {
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
