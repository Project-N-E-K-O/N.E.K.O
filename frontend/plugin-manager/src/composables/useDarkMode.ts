/**
 * 深色模式切换 Composable
 */
import { ref, onMounted } from 'vue'

const DARK_MODE_KEY = 'neko-dark-mode'
const isDark = ref(false)

/**
 * 应用深色模式
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
 * 初始化深色模式
 * 导出以便在应用启动时调用（在 main.ts 中）
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
 * 切换深色模式
 */
function toggleDarkMode() {
  applyDarkMode(!isDark.value)
}

/**
 * 使用深色模式的 Composable
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

