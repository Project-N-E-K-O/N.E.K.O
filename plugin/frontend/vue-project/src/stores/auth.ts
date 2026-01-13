/**
 * 认证状态管理
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const AUTH_CODE_KEY = 'neko_admin_code'

export const useAuthStore = defineStore('auth', () => {
  // 状态
  let storedCode: string | null = null
  try {
    storedCode = localStorage.getItem(AUTH_CODE_KEY)
  } catch (err) {
    console.warn('Failed to load auth code from storage:', err)
  }
  const authCode = ref<string | null>(storedCode)
  // 计算属性
  const isAuthenticated = computed(() => authCode.value !== null && /^[A-Z]{4}$/.test(authCode.value))

  /**
   * Validate an authentication code and persist it to localStorage when valid.
   *
   * The input is trimmed and converted to uppercase before validation; a valid code
   * consists of exactly four letters A–Z. When valid, the normalized code is saved
   * under `AUTH_CODE_KEY`.
   *
   * @param code - The candidate authentication code (will be trimmed and uppercased)
   * @returns `true` if the code is valid and was stored, `false` otherwise.
   */
  function setAuthCode(code: string) {
    // 验证码应该是4个大写字母
    const normalizedCode = code.trim().toUpperCase()
    if (normalizedCode.length === 4 && /^[A-Z]{4}$/.test(normalizedCode)) {
      authCode.value = normalizedCode
      try {
        localStorage.setItem(AUTH_CODE_KEY, normalizedCode)
      } catch (err) {
        console.warn('Failed to save auth code to storage:', err)
      }
      return true
    }
    return false
  }

  /**
   * Clears the stored authentication code from memory and persistent storage.
   *
   * Sets the in-memory `authCode` to null and attempts to remove the `AUTH_CODE_KEY`
   * entry from localStorage. If storage access fails, a warning is logged.
   */
  function clearAuthCode() {
    authCode.value = null
    try {
      localStorage.removeItem(AUTH_CODE_KEY)
    } catch (err) {
      console.warn('Failed to clear auth code from storage:', err)
    }
  }

  /**
   * Builds an HTTP Authorization header value from the current auth code.
   *
   * @returns The header value in the form `Bearer <code>` if an auth code exists, `null` otherwise.
   */
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
