import defaultGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/default-ghost-cursor.png'
import clickGhostCursorUrl from '../../../static/assets/tutorial/ghost-cursor/click-ghost-cursor.png'
import { getLocale } from './i18n'
import { getTrustedOpenerOrigin } from './composables/useYuiTutorialBridge'

const START_EVENT = 'neko:yui-guide:plugin-dashboard:start'
const READY_EVENT = 'neko:yui-guide:plugin-dashboard:ready'
const DONE_EVENT = 'neko:yui-guide:plugin-dashboard:done'
const GUIDE_AUDIO_BASE_URL = '/static/assets/tutorial/guide-audio/'
const DEFAULT_GUIDE_LOCALE = 'zh'
const GUIDE_AUDIO_BY_KEY = {
  takeover_plugin_preview_dashboard: {
    zh: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
    ja: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼～.mp3',
  },
} as const

const ROOT_ID = 'yui-guide-plugin-dashboard-runtime'
const SVG_NS = 'http://www.w3.org/2000/svg'
const BACKDROP_MASK_ID = `${ROOT_ID}-mask`
let currentGuideAudio: HTMLAudioElement | null = null
let currentGuideAudioTimer: number | null = null

type StartPayload = {
  line?: string
  voiceKey?: keyof typeof GUIDE_AUDIO_BY_KEY
  audioUrl?: string
  closeOnDone?: boolean
}

type SpotlightRect = {
  left: number
  top: number
  width: number
  height: number
  radius: number
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
  const trustedOrigin = getTrustedOpenerOrigin()
  return trustedOrigin ? new Set([trustedOrigin]) : new Set<string>()
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

function playGuideAudioWithPromise(audioSrc: string, minimumDurationMs: number) {
  const normalizedAudioSrc = typeof audioSrc === 'string' ? audioSrc.trim() : ''
  if (!normalizedAudioSrc) {
    return Promise.reject(new Error('missing_audio_src'))
  }

  return new Promise<void>((resolve, reject) => {
    let settled = false
    const audio = new Audio(normalizedAudioSrc)
    const maxWaitMs = Math.max(3000, minimumDurationMs) + 12000
    currentGuideAudio = audio

    const finish = (success: boolean, error?: unknown) => {
      if (settled) {
        return
      }
      settled = true
      window.clearTimeout(timerId)
      if (currentGuideAudioTimer === timerId) {
        currentGuideAudioTimer = null
      }
      if (currentGuideAudio === audio) {
        currentGuideAudio = null
      }
      audio.onended = null
      audio.onerror = null
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
    audio.onended = () => finish(true)
    audio.onerror = () => finish(false, new Error('guide_audio_error'))

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
      resolve()
    }

    utterance.onend = finish
    utterance.onerror = finish

    const timerId = window.setTimeout(finish, minDurationMs + 1200)

    try {
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(utterance)
    } catch (_) {
      finish()
    }
  })
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
      border: 3px solid #93d6ff;
      border-radius: 18px;
      box-shadow:
        0 0 0 7px #a1e4ff,
        0 0 0 12px #aff3ff,
        0 20px 38px rgba(147, 214, 255, 0.26);
      opacity: 0;
      transition:
        opacity 180ms ease,
        left 220ms ease,
        top 220ms ease,
        width 220ms ease,
        height 220ms ease;
    }

    #${ROOT_ID} .yui-guide-plugin-spotlight.is-visible {
      opacity: 1;
      animation: yui-guide-plugin-pulse 1.5s ease-in-out infinite;
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

    #${ROOT_ID} .yui-guide-plugin-cursor::after {
      content: none;
    }

    #${ROOT_ID} .yui-guide-plugin-cursor.is-clicking {
      background-image: url('${clickGhostCursorUrl}');
      animation: yui-guide-plugin-click 240ms ease;
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
  spotlight: HTMLDivElement | null = null
  cursorShell: HTMLDivElement | null = null
  cursorInner: HTMLDivElement | null = null
  cursorPosition: { x: number; y: number } | null = null
  spotlightElement: Element | null = null
  activeSessionId = ''
  running = false
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

    const backdropFill = createSvgElement('rect')
    backdropFill.setAttribute('fill', 'rgba(3, 7, 18, 0.76)')
    backdropFill.setAttribute('mask', `url(#${BACKDROP_MASK_ID})`)

    mask.appendChild(backdropBase)
    mask.appendChild(backdropCutout)
    defs.appendChild(mask)
    backdrop.appendChild(defs)
    backdrop.appendChild(backdropFill)

    const spotlight = document.createElement('div')
    spotlight.className = 'yui-guide-plugin-spotlight'

    const cursorShell = document.createElement('div')
    cursorShell.className = 'yui-guide-plugin-cursor-shell'

    const cursorInner = document.createElement('div')
    cursorInner.className = 'yui-guide-plugin-cursor'
    cursorShell.appendChild(cursorInner)

    root.appendChild(backdrop)
    root.appendChild(spotlight)
    root.appendChild(cursorShell)
    document.body.appendChild(root)

    this.root = root
    this.backdrop = backdrop
    this.backdropBase = backdropBase
    this.backdropFill = backdropFill
    this.backdropCutout = backdropCutout
    this.spotlight = spotlight
    this.cursorShell = cursorShell
    this.cursorInner = cursorInner
    this.syncBackdropViewport()
  }

  notify(type: string, sessionId: string) {
    try {
      const targetOrigin = getTrustedOpenerOrigin()
      if (!targetOrigin) {
        return
      }
      window.opener?.postMessage({
        type,
        sessionId,
      }, targetOrigin)
    } catch (_) {}
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

    const padding = 12
    const left = Math.max(0, Math.floor(rect.left - padding))
    const top = Math.max(0, Math.floor(rect.top - padding))
    const right = Math.min(window.innerWidth, Math.ceil(rect.right + padding))
    const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom + padding))
    const width = Math.max(0, right - left)
    const height = Math.max(0, bottom - top)

    let radius = 18
    try {
      const computed = window.getComputedStyle(htmlElement)
      const parsedRadius = Number.parseFloat(computed.borderTopLeftRadius || computed.borderRadius || '')
      if (Number.isFinite(parsedRadius) && parsedRadius > 0) {
        radius = parsedRadius + 12
      }
    } catch (_) {}

    return {
      left,
      top,
      width,
      height,
      radius,
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
      this.backdropCutout.setAttribute('visibility', 'hidden')
      this.backdropCutout.setAttribute('x', '0')
      this.backdropCutout.setAttribute('y', '0')
      this.backdropCutout.setAttribute('width', '0')
      this.backdropCutout.setAttribute('height', '0')
      this.backdropCutout.setAttribute('rx', '0')
      this.backdropCutout.setAttribute('ry', '0')
      return
    }

    this.backdropCutout.setAttribute('visibility', 'visible')
    this.backdropCutout.setAttribute('x', String(spotlightRect.left))
    this.backdropCutout.setAttribute('y', String(spotlightRect.top))
    this.backdropCutout.setAttribute('width', String(spotlightRect.width))
    this.backdropCutout.setAttribute('height', String(spotlightRect.height))
    this.backdropCutout.setAttribute('rx', String(spotlightRect.radius))
    this.backdropCutout.setAttribute('ry', String(spotlightRect.radius))
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
      this.spotlight.classList.remove('is-visible')
      this.updateBackdropCutout(null)
      return
    }

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
      this.spotlight.classList.remove('is-visible')
    }
    this.updateBackdropCutout(null)
  }

  showCursor(x: number, y: number) {
    this.ensureRoot()
    if (!this.cursorShell) {
      return
    }

    document.documentElement.classList.add('yui-guide-plugin-dashboard-running')
    document.body.classList.add('yui-guide-plugin-dashboard-running')
    this.cursorShell.classList.add('is-visible')
    this.cursorShell.style.transitionDuration = '0ms'
    this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
    this.cursorPosition = { x, y }
  }

  moveCursor(x: number, y: number, durationMs = 480, isCurrent?: () => boolean) {
    this.ensureRoot()
    if (!this.cursorShell) {
      return Promise.resolve()
    }

    if (!this.cursorPosition) {
      this.showCursor(x, y)
      return Promise.resolve()
    }

    this.cursorShell.classList.add('is-visible')
    this.cursorShell.style.transitionDuration = `${Math.max(0, durationMs)}ms`

    return new Promise<void>((resolve) => {
      let settled = false
      const finish = () => {
        if (settled) {
          return
        }
        settled = true
        this.cursorShell?.removeEventListener('transitionend', handleEnd)
        resolve()
      }
      const handleEnd = (event: Event) => {
        if (event.target === this.cursorShell) {
          finish()
        }
      }

      this.cursorShell?.addEventListener('transitionend', handleEnd)
      window.requestAnimationFrame(() => {
        if (isCurrent && !isCurrent()) {
          finish()
          return
        }
        if (this.cursorShell) {
          this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
        }
      })
      window.setTimeout(finish, durationMs + 80)
      this.cursorPosition = { x, y }
    })
  }

  async moveCursorToElement(element: Element | null, durationMs = 480, isCurrent?: () => boolean) {
    const rect = this.getRect(element)
    if (!rect) {
      return false
    }

    await this.moveCursor(rect.left + rect.width / 2, rect.top + rect.height / 2, durationMs, isCurrent)
    return true
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

  async animateScroll(container: HTMLElement, deltaY: number, durationMs: number, isCurrent?: () => boolean) {
    const startedAt = performance.now()
    const initialTop = container.scrollTop
    const targetTop = initialTop + deltaY

    return new Promise<void>((resolve) => {
      const tick = (now: number) => {
        if (isCurrent && !isCurrent()) {
          resolve()
          return
        }
        const progress = clamp((now - startedAt) / durationMs, 0, 1)
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

    await new Promise<void>((resolve) => {
      const tick = (now: number) => {
        if (isCurrent && !isCurrent()) {
          resolve()
          return
        }
        const progress = clamp((now - startedAt) / durationMs, 0, 1)
        const angle = progress * Math.PI * 2
        const x = centerX + Math.cos(angle) * radiusX
        const y = centerY + Math.sin(angle) * radiusY
        if (this.cursorShell) {
          this.cursorShell.style.transitionDuration = '80ms'
          this.cursorShell.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`
          this.cursorPosition = { x, y }
        }

        if (progress >= 1) {
          resolve()
          return
        }
        window.requestAnimationFrame(tick)
      }

      window.requestAnimationFrame(tick)
    })
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

  cleanup() {
    document.documentElement.classList.remove('yui-guide-plugin-dashboard-running')
    document.body.classList.remove('yui-guide-plugin-dashboard-running')
    if (currentGuideAudioTimer !== null) {
      window.clearTimeout(currentGuideAudioTimer)
      currentGuideAudioTimer = null
    }
    if (currentGuideAudio) {
      currentGuideAudio.onended = null
      currentGuideAudio.onerror = null
      currentGuideAudio.pause()
      currentGuideAudio.currentTime = 0
      currentGuideAudio = null
    }
    try {
      window.speechSynthesis?.cancel()
    } catch (_) {}
    window.removeEventListener('resize', this.boundRefreshSpotlight, true)
    window.removeEventListener('scroll', this.boundRefreshSpotlight, true)
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
    this.spotlight = null
    this.cursorShell = null
    this.cursorInner = null
    this.cursorPosition = null
    this.spotlightElement = null
    this.running = false
    this.activeSessionId = ''
  }

  async run(sessionId: string, payload: StartPayload) {
    if (this.running && this.activeSessionId === sessionId) {
      return
    }

    this.cleanup()
    this.running = true
    this.activeSessionId = sessionId
    const isCurrent = () => this.isCurrentRun(sessionId)
    this.ensureRoot()
    window.addEventListener('resize', this.boundRefreshSpotlight, true)
    window.addEventListener('scroll', this.boundRefreshSpotlight, true)
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

    if (!isCurrent()) {
      return
    }
    this.notify(READY_EVENT, sessionId)

    const pluginRect = this.getRect(pluginButton)
    const startX = pluginRect ? pluginRect.left + pluginRect.width / 2 - 56 : window.innerWidth / 2
    const startY = pluginRect ? pluginRect.top + pluginRect.height / 2 - 24 : window.innerHeight / 2
    if (!isCurrent()) {
      return
    }
    this.showCursor(startX, startY)
    this.setSpotlight(pluginButton)
    await this.moveCursorToElement(pluginButton, 700, isCurrent)
    if (!isCurrent()) {
      return
    }
    this.clickCursor()
    if (!isCurrent()) {
      return
    }
    pluginButton.click()
    await wait(280)
    if (!isCurrent()) {
      return
    }

    const speechPromise = this.speakLine(payload.line || '', {
      voiceKey: payload.voiceKey,
      audioUrl: payload.audioUrl,
    })

    if (!isCurrent()) {
      return
    }
    this.setSpotlight(mainContainer)
    await this.moveCursorToElement(mainContainer, 780, isCurrent)
    if (!isCurrent()) {
      return
    }
    await this.animateScroll(mainContainer, 150, 1000, isCurrent)
    if (!isCurrent()) {
      return
    }
    await this.animateScroll(mainContainer, -150, 1000, isCurrent)
    if (!isCurrent()) {
      return
    }
    await this.runEllipse(mainContainer, 7000, isCurrent)
    if (!isCurrent()) {
      return
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
      await wait(120)
      if (!isCurrent()) {
        return
      }
      window.close()
    }

    if (!isCurrent()) {
      return
    }
    this.cleanup()
  }
}

export function initPluginDashboardYuiGuideRuntime() {
  const runtime = new PluginDashboardGuideRuntime()

  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data
    if (!data || typeof data !== 'object' || data.type !== START_EVENT) {
      return
    }

    const allowedOrigins = getAllowedOpenerOrigins()
    if (!allowedOrigins.has(event.origin)) {
      return
    }

    const sessionId = typeof data.sessionId === 'string' ? data.sessionId : ''
    if (!sessionId) {
      return
    }

    runtime.run(sessionId, (data.payload || {}) as StartPayload).catch(() => {
      if (!runtime.isCurrentRun(sessionId)) {
        return
      }
      runtime.notify(DONE_EVENT, sessionId)
      runtime.cleanup()
    })
  })
}
