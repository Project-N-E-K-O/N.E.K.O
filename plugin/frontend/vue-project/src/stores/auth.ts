/**
 * 认证状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const AUTH_CODE_KEY = 'neko_admin_code'

export const useAuthStore = defineStore('auth', () => {
  // 状态
  const authCode = ref<string | null>(localStorage.getItem(AUTH_CODE_KEY))

  // 计算属性
  const isAuthenticated = computed(() => authCode.value !== null && authCode.value.length === 4)

  // 方法
  function setAuthCode(code: string) {
    // 验证码应该是4个大写字母
    const normalizedCode = code.trim().toUpperCase()
    if (normalizedCode.length === 4 && /^[A-Z]{4}$/.test(normalizedCode)) {
      authCode.value = normalizedCode
      localStorage.setItem(AUTH_CODE_KEY, normalizedCode)
      return true
    }
    return false
  }

  function clearAuthCode() {
    authCode.value = null
    localStorage.removeItem(AUTH_CODE_KEY)
  }

  function getAuthHeader(): string | null {
    if (authCode.value) {
      return `Bearer ${authCode.value}`
    }
    return null
  }

  return {
    authCode,
    isAuthenticated,
    setAuthCode,
    clearAuthCode,
    getAuthHeader
  }
})

