/**
 * 深色模式切换 Composable
 */
import { ref, onMounted } from 'vue'

const DARK_MODE_KEY = 'neko-dark-mode'
const isDark = ref(false)

/**
 * Apply or remove the application's dark mode state.
 *
 * Updates the document root's 'dark' CSS class, synchronizes the `isDark` reactive ref, and persists the choice in localStorage under `DARK_MODE_KEY`.
 *
 * @param dark - When `true`, enable dark mode; when `false`, disable it
 */
function applyDarkMode(dark: boolean) {
  const html = document.documentElement
  if (dark) {
    html.classList.add('dark')
  } else {
    html.classList.remove('dark')
  }
  isDark.value = dark
  localStorage.setItem(DARK_MODE_KEY, dark ? 'true' : 'false')
}

/**
 * Initialize dark mode state and apply the corresponding HTML class for the app.
 *
 * Reads the saved preference from localStorage (key "neko-dark-mode") and applies it; if no saved value exists, uses the system color-scheme preference. Call this during application startup (for example in main.ts) before mounting to prevent visual flash.
 */
export function initDarkMode() {
  const saved = localStorage.getItem(DARK_MODE_KEY)
  if (saved !== null) {
    const dark = saved === 'true'
    applyDarkMode(dark)
  } else {
    // 如果没有保存的设置，检查系统偏好
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    applyDarkMode(prefersDark)
  }
}

/**
 * Toggle the active dark mode state.
 */
function toggleDarkMode() {
  applyDarkMode(!isDark.value)
}

/**
 * Provides a Vue composable that exposes the reactive dark mode state and a toggle function.
 *
 * On component mount, synchronizes `isDark` with whether the document root currently has the `dark` class.
 *
 * @returns An object containing:
 * - `isDark` — a `Ref<boolean>` that is `true` when dark mode is active.
 * - `toggleDarkMode` — a function that switches the dark mode state.
 */
export function useDarkMode() {
  // 在组件挂载时同步状态（作为备用，主要初始化在模块加载时完成）
  onMounted(() => {
    const html = document.documentElement
    isDark.value = html.classList.contains('dark')
  })

  return {
    isDark,
    toggleDarkMode
  }
}

// 注意：initDarkMode 现在在 main.ts 中被调用（在应用挂载前）
// 这样可以避免页面闪烁，并确保状态在应用启动时就正确初始化
