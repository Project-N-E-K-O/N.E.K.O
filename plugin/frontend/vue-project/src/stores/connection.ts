import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useConnectionStore = defineStore('connection', () => {
  const disconnected = ref(false)
  const authRequired = ref(false)
  const lastAuthErrorMessage = ref<string | null>(null)

  function markDisconnected() {
    disconnected.value = true
  }

  function markConnected() {
    disconnected.value = false
  }

  function requireAuth(message?: string) {
    authRequired.value = true
    if (typeof message === 'string' && message.trim()) {
      lastAuthErrorMessage.value = message
    }
  }

  function clearAuthRequired() {
    authRequired.value = false
    lastAuthErrorMessage.value = null
  }

  return {
    disconnected,
    authRequired,
    lastAuthErrorMessage,
    markDisconnected,
    markConnected,
    requireAuth,
    clearAuthRequired
  }
})
