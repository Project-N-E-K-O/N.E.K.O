// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp } from 'vue'
import { initDarkMode, useDarkMode } from './useDarkMode'

const DARK_MODE_KEY = 'neko-dark-mode'
const DARK_PALETTE_KEY = 'neko-plugin-manager-dark-palette'
const LIGHT_PALETTE_KEY = 'neko-plugin-manager-light-palette'
const html = document.documentElement
const unmounts: Array<() => void> = []

function mountDarkMode() {
  let state!: ReturnType<typeof useDarkMode>
  const app = createApp({
    setup() {
      state = useDarkMode()
      return () => null
    },
  })
  app.mount(document.createElement('div'))
  unmounts.push(() => app.unmount())
  return state
}

beforeEach(() => {
  localStorage.clear()
  html.className = ''
  html.removeAttribute('data-theme')
  html.removeAttribute('data-dark-palette')
  html.removeAttribute('data-light-palette')
})

afterEach(() => {
  while (unmounts.length) unmounts.pop()?.()
  delete (window as { nekoDarkMode?: unknown }).nekoDarkMode
})

describe('dark palette', () => {
  it('restores the stored graphite palette, and keeps it set in light mode', () => {
    localStorage.setItem(DARK_MODE_KEY, 'false')
    localStorage.setItem(DARK_PALETTE_KEY, 'graphite')
    initDarkMode()
    expect(html.classList.contains('dark')).toBe(false)
    expect(html.getAttribute('data-dark-palette')).toBe('graphite')
    expect(mountDarkMode().darkPalette.value).toBe('graphite')
  })

  it('falls back to the NEKO palette for missing or unknown values', () => {
    localStorage.setItem(DARK_PALETTE_KEY, 'solarized')
    initDarkMode()
    expect(html.hasAttribute('data-dark-palette')).toBe(false)
    expect(mountDarkMode().darkPalette.value).toBe('neko')
  })

  it('persists the choice and switches palettes without toggling dark mode', () => {
    localStorage.setItem(DARK_MODE_KEY, 'true')
    initDarkMode()
    const { isDark, darkPalette, setDarkPalette } = mountDarkMode()
    setDarkPalette('graphite')
    expect(isDark.value).toBe(true)
    expect(darkPalette.value).toBe('graphite')
    expect(html.getAttribute('data-dark-palette')).toBe('graphite')
    expect(localStorage.getItem(DARK_PALETTE_KEY)).toBe('graphite')
    setDarkPalette('neko')
    expect(html.hasAttribute('data-dark-palette')).toBe(false)
    expect(localStorage.getItem(DARK_PALETTE_KEY)).toBe('neko')
  })

  it('follows a palette change made in another plugin-manager window', () => {
    initDarkMode()
    const { darkPalette } = mountDarkMode()
    window.dispatchEvent(
      new StorageEvent('storage', { key: DARK_PALETTE_KEY, newValue: 'graphite' })
    )
    expect(darkPalette.value).toBe('graphite')
    expect(html.getAttribute('data-dark-palette')).toBe('graphite')
  })

  it('shares only light/dark with the host bridge, never the palette', () => {
    const bridge = { set: vi.fn() }
    ;(window as { nekoDarkMode?: unknown }).nekoDarkMode = bridge
    const themeEvents: unknown[] = []
    const onTheme = (event: Event) => themeEvents.push((event as CustomEvent).detail)
    window.addEventListener('neko-theme-changed', onTheme)
    localStorage.setItem(DARK_MODE_KEY, 'false')
    initDarkMode()
    const { setDarkMode, setDarkPalette } = mountDarkMode()
    setDarkPalette('graphite')
    expect(bridge.set).not.toHaveBeenCalled()
    setDarkMode(true)
    window.removeEventListener('neko-theme-changed', onTheme)
    expect(bridge.set).toHaveBeenCalledExactlyOnceWith(true)
    expect(themeEvents).toEqual([{ darkMode: true }])
  })
})

describe('choosing a palette of the mode already shown', () => {
  it('saves the mode even while the page follows the system', () => {
    initDarkMode()
    expect(localStorage.getItem(DARK_MODE_KEY)).toBeNull()
    const { isDark, setDarkMode, setLightPalette } = mountDarkMode()
    setLightPalette('paper')
    setDarkMode(false)
    expect(isDark.value).toBe(false)
    expect(localStorage.getItem(DARK_MODE_KEY)).toBe('false')
  })

  it('sends the mode to the host while its bridge read is pending', async () => {
    let resolveGet!: (dark: boolean) => void
    const bridge = {
      get: () => new Promise<boolean>((resolve) => (resolveGet = resolve)),
      set: vi.fn(),
    }
    ;(window as { nekoDarkMode?: unknown }).nekoDarkMode = bridge
    localStorage.setItem(DARK_MODE_KEY, 'false')
    initDarkMode()
    const { isDark, setDarkMode } = mountDarkMode()
    setDarkMode(false)
    expect(bridge.set).toHaveBeenCalledExactlyOnceWith(false)
    resolveGet(true)
    await Promise.resolve()
    expect(isDark.value).toBe(false)
  })

  it('does not resend the mode once the host has confirmed it', async () => {
    const bridge = { get: vi.fn().mockResolvedValue(false), set: vi.fn() }
    ;(window as { nekoDarkMode?: unknown }).nekoDarkMode = bridge
    localStorage.setItem(DARK_MODE_KEY, 'false')
    initDarkMode()
    await Promise.resolve()
    const { setDarkMode } = mountDarkMode()
    setDarkMode(false)
    expect(bridge.set).not.toHaveBeenCalled()
  })

  it('retries the same mode after the host rejected the write', async () => {
    const bridge = { set: vi.fn().mockRejectedValueOnce(new Error('offline')) }
    ;(window as { nekoDarkMode?: unknown }).nekoDarkMode = bridge
    localStorage.setItem(DARK_MODE_KEY, 'false')
    initDarkMode()
    const { setDarkMode } = mountDarkMode()
    setDarkMode(true)
    await Promise.resolve()
    await Promise.resolve()
    setDarkMode(true)
    expect(bridge.set).toHaveBeenNthCalledWith(2, true)
    await Promise.resolve()
    setDarkMode(true)
    expect(bridge.set).toHaveBeenCalledTimes(2)
  })

  it('retries the same mode after the host write threw', () => {
    const bridge = {
      set: vi.fn().mockImplementationOnce(() => {
        throw new Error('offline')
      }),
    }
    ;(window as { nekoDarkMode?: unknown }).nekoDarkMode = bridge
    localStorage.setItem(DARK_MODE_KEY, 'false')
    initDarkMode()
    const { setDarkMode } = mountDarkMode()
    setDarkMode(true)
    setDarkMode(true)
    expect(bridge.set).toHaveBeenCalledTimes(2)
  })
})

describe('light palette', () => {
  it('restores the stored paper palette next to an independent dark palette', () => {
    localStorage.setItem(DARK_MODE_KEY, 'true')
    localStorage.setItem(LIGHT_PALETTE_KEY, 'paper')
    localStorage.setItem(DARK_PALETTE_KEY, 'graphite')
    initDarkMode()
    expect(html.getAttribute('data-light-palette')).toBe('paper')
    expect(html.getAttribute('data-dark-palette')).toBe('graphite')
    const { lightPalette, darkPalette } = mountDarkMode()
    expect(lightPalette.value).toBe('paper')
    expect(darkPalette.value).toBe('graphite')
  })

  it('rejects palettes that belong to the other mode', () => {
    localStorage.setItem(LIGHT_PALETTE_KEY, 'graphite')
    localStorage.setItem(DARK_PALETTE_KEY, 'paper')
    initDarkMode()
    expect(html.hasAttribute('data-light-palette')).toBe(false)
    expect(html.hasAttribute('data-dark-palette')).toBe(false)
  })

  it('persists the choice without touching dark mode or the host bridge', () => {
    const bridge = { set: vi.fn() }
    ;(window as { nekoDarkMode?: unknown }).nekoDarkMode = bridge
    localStorage.setItem(DARK_MODE_KEY, 'false')
    initDarkMode()
    const { isDark, lightPalette, setLightPalette } = mountDarkMode()
    setLightPalette('paper')
    expect(isDark.value).toBe(false)
    expect(lightPalette.value).toBe('paper')
    expect(localStorage.getItem(LIGHT_PALETTE_KEY)).toBe('paper')
    expect(bridge.set).not.toHaveBeenCalled()
    setLightPalette('neko')
    expect(html.hasAttribute('data-light-palette')).toBe(false)
  })

  it('follows a light palette change made in another plugin-manager window', () => {
    initDarkMode()
    const { lightPalette } = mountDarkMode()
    window.dispatchEvent(new StorageEvent('storage', { key: LIGHT_PALETTE_KEY, newValue: 'paper' }))
    expect(lightPalette.value).toBe('paper')
    expect(html.getAttribute('data-light-palette')).toBe('paper')
  })
})
