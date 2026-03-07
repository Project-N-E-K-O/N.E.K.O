/**
 * 认证状态管理
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export const useAuthStore = defineStore('auth', () => {
  const authCode = ref<string | null>(null)
  const isAuthenticated = computed(() => true)

  function setAuthCode(code: string) {
    authCode.value = code || null
    return true
  }

  function clearAuthCode() {
    authCode.value = null
  }

  function getAuthHeader(): string | null {
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

