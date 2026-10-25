import { ref, onMounted, type Ref } from 'vue'

const DARK_MODE_KEY = 'neko-dark-mode'
const isDark = ref(false)
let listenersRegistered = false
let darkModeInitEpoch = 0
// The host's mode is unknown while its bridge read is pending or after a bridge
// write failed; a choice made then is sent to the host even if unchanged here.
let hostModeUnconfirmed = false

// Light/dark is shared with the main N.E.K.O app; each mode's palette is a
// plugin-manager-only preference and never reaches the host bridge.
const PALETTE_OPTIONS = {
  light: ['neko', 'paper'],
  dark: ['neko', 'graphite'],
} as const
type PaletteSlot = keyof typeof PALETTE_OPTIONS
type Palette<S extends PaletteSlot> = (typeof PALETTE_OPTIONS)[S][number]
export type LightPalette = Palette<'light'>
export type DarkPalette = Palette<'dark'>
const PALETTE_SLOTS = Object.keys(PALETTE_OPTIONS) as PaletteSlot[]
const palettes: { [S in PaletteSlot]: Ref<Palette<S>> } = {
  light: ref<LightPalette>('neko'),
  dark: ref<DarkPalette>('neko'),
}

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
  } catch {}
}

function paletteKey(slot: PaletteSlot) {
  return `neko-plugin-manager-${slot}-palette`
}

function parsePalette<S extends PaletteSlot>(slot: S, value: string | null): Palette<S> {
  const options: readonly string[] = PALETTE_OPTIONS[slot]
  return value !== null && options.includes(value) ? (value as Palette<S>) : 'neko'
}

function readStoredPalette<S extends PaletteSlot>(slot: S): Palette<S> {
  try {
    return parsePalette(slot, localStorage.getItem(paletteKey(slot)))
  } catch {
    return 'neko'
  }
}

function applyDarkMode(dark: boolean | null | undefined, options: { persist?: boolean } = {}) {
  const resolvedDark = typeof dark === 'boolean' ? dark : getSystemPrefersDark()
  const html = document.documentElement
  if (resolvedDark) {
    html.classList.add('dark')
    html.setAttribute('data-theme', 'dark')
  } else {
    html.classList.remove('dark')
    html.removeAttribute('data-theme')
  }
  isDark.value = resolvedDark
  if (options.persist !== false) {
    writeStoredDarkMode(resolvedDark)
  }
}

// Both palette attributes stay set whichever mode is active, so a light/dark
// switch made by the main app or another window lands on the palette the user
// picked here for that mode.
function applyPalette<S extends PaletteSlot>(
  slot: S,
  palette: Palette<S>,
  options: { persist?: boolean } = {}
) {
  const html = document.documentElement
  const attribute = `data-${slot}-palette`
  if (palette === 'neko') {
    html.removeAttribute(attribute)
  } else {
    html.setAttribute(attribute, palette)
  }
  palettes[slot].value = palette
  if (options.persist !== false) {
    try {
      localStorage.setItem(paletteKey(slot), palette)
    } catch {}
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
    const slot = PALETTE_SLOTS.find((candidate) => event.key === paletteKey(candidate))
    if (slot) {
      applyPalette(slot, parsePalette(slot, event.newValue), { persist: false })
      return
    }
    if (event.key !== DARK_MODE_KEY) {
      return
    }
    if (event.newValue === null) {
      applyDarkMode(null, { persist: false })
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
  } catch {}
}

export function initDarkMode() {
  setupThemeSyncListeners()

  for (const slot of PALETTE_SLOTS) {
    applyPalette(slot, readStoredPalette(slot), { persist: false })
  }
  const saved = readStoredDarkMode()
  applyDarkMode(saved !== null ? saved : getSystemPrefersDark(), { persist: saved !== null })

  const bridge = getNekoDarkModeBridge()
  if (bridge && typeof bridge.get === 'function') {
    const initEpoch = ++darkModeInitEpoch
    hostModeUnconfirmed = true
    const settle = () => {
      if (initEpoch === darkModeInitEpoch) {
        hostModeUnconfirmed = false
      }
    }
    try {
      Promise.resolve(bridge.get())
        .then((dark) => {
          if (initEpoch === darkModeInitEpoch && typeof dark === 'boolean') {
            applyDarkMode(dark, { persist: false })
          }
          settle()
        })
        .catch(settle)
    } catch {
      settle()
    }
  }
}

function setDarkMode(dark: boolean) {
  // An explicit choice wins over a bridge read that is still pending, so it is
  // saved and sent to the host even when the page already shows that mode.
  darkModeInitEpoch += 1
  const hostUnconfirmed = hostModeUnconfirmed
  hostModeUnconfirmed = false
  const changed = dark !== isDark.value
  applyDarkMode(dark)
  if (!changed && !hostUnconfirmed) {
    return
  }

  const bridge = getNekoDarkModeBridge()
  if (bridge && typeof bridge.set === 'function') {
    // A failed write leaves the host on its old mode, so the next pick of this
    // mode retries it; a later switch to the other mode sends its own write.
    const markUnconfirmed = () => {
      if (isDark.value === dark) {
        hostModeUnconfirmed = true
      }
    }
    try {
      Promise.resolve(bridge.set(dark)).catch(markUnconfirmed)
    } catch {
      markUnconfirmed()
    }
  }

  window.dispatchEvent(new CustomEvent('neko-theme-changed', {
    detail: { darkMode: dark },
  }))
}

function setLightPalette(palette: LightPalette) {
  applyPalette('light', palette)
}

function setDarkPalette(palette: DarkPalette) {
  applyPalette('dark', palette)
}

export function useDarkMode() {
  onMounted(() => {
    const html = document.documentElement
    isDark.value = html.classList.contains('dark')
  })

  return {
    isDark,
    lightPalette: palettes.light,
    darkPalette: palettes.dark,
    setDarkMode,
    setLightPalette,
    setDarkPalette,
  }
}
