import { ref, onMounted } from 'vue'

const DARK_MODE_KEY = 'neko-dark-mode'
const isDark = ref(false)
let listenersRegistered = false
let darkModeInitEpoch = 0

type NekoDarkModeBridge = {
  get?: () => boolean | Promise<boolean>
  set?: (dark: boolean) => void | Promise<void>
}

function getNekoDarkModeBridge() {
  return (window as unknown as { nekoDarkMode?: NekoDarkModeBridge }).nekoDarkMode
}

function readStoredDarkMode() {
  try {
    const saved = localStorage.getItem(DARK_MODE_KEY)
    return saved === null ? null : saved === 'true'
  } catch {
    return null
  }
}

function writeStoredDarkMode(dark: boolean) {
  try {
    localStorage.setItem(DARK_MODE_KEY, dark ? 'true' : 'false')
  } catch (_) {}
}

function applyDarkMode(dark: boolean, options: { persist?: boolean } = {}) {
  const html = document.documentElement
  if (dark) {
    html.classList.add('dark')
    html.setAttribute('data-theme', 'dark')
  } else {
    html.classList.remove('dark')
    html.removeAttribute('data-theme')
  }
  isDark.value = dark
  if (options.persist !== false) {
    writeStoredDarkMode(dark)
  }
}

function getSystemPrefersDark() {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  } catch {
    return false
  }
}

function setupThemeSyncListeners() {
  if (listenersRegistered) {
    return
  }
  listenersRegistered = true

  window.addEventListener('storage', (event) => {
    if (event.key !== DARK_MODE_KEY || event.newValue === null) {
      return
    }
    applyDarkMode(event.newValue === 'true', { persist: false })
  })

  window.addEventListener('neko-theme-changed', (event: Event) => {
    const detail = (event as CustomEvent<{ darkMode?: boolean }>).detail
    if (detail && typeof detail.darkMode === 'boolean') {
      applyDarkMode(detail.darkMode, { persist: false })
    }
  })

  try {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    media.addEventListener('change', (event) => {
      if (readStoredDarkMode() === null) {
        applyDarkMode(event.matches, { persist: false })
      }
    })
  } catch (_) {}
}

export function initDarkMode() {
  setupThemeSyncListeners()

  const saved = readStoredDarkMode()
  applyDarkMode(saved !== null ? saved : getSystemPrefersDark(), { persist: saved !== null })

  const bridge = getNekoDarkModeBridge()
  if (bridge && typeof bridge.get === 'function') {
    try {
      const initEpoch = ++darkModeInitEpoch
      Promise.resolve(bridge.get())
        .then((dark) => {
          if (initEpoch === darkModeInitEpoch && typeof dark === 'boolean') {
            applyDarkMode(dark)
          }
        })
        .catch(() => {})
    } catch (_) {}
  }
}

function toggleDarkMode() {
  darkModeInitEpoch += 1
  const next = !isDark.value
  applyDarkMode(next)

  const bridge = getNekoDarkModeBridge()
  if (bridge && typeof bridge.set === 'function') {
    try {
      Promise.resolve(bridge.set(next)).catch(() => {})
    } catch (_) {}
  }

  window.dispatchEvent(new CustomEvent('neko-theme-changed', {
    detail: { darkMode: next },
  }))
}

export function useDarkMode() {
  onMounted(() => {
    const html = document.documentElement
    isDark.value = html.classList.contains('dark')
  })

  return {
    isDark,
    toggleDarkMode,
  }
}
