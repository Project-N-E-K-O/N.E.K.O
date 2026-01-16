<template>
  <el-container class="app-layout">
    <el-aside width="240px" class="sidebar-container">
      <Sidebar />
    </el-aside>
    <el-container>
      <div v-if="connectionStore.disconnected" class="connection-banner">
        <el-alert
          :title="t('common.disconnected')"
          type="error"
          :closable="false"
          show-icon
        />
      </div>
      <el-header height="60px" class="header-container">
        <Header />
      </el-header>
      <el-main class="main-container">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>

  <el-dialog
    v-model="authDialogVisible"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :show-close="false"
    :title="t('auth.login')"
    width="420px"
  >
    <el-form @submit.prevent="submitAuth">
      <el-form-item :label="t('auth.login')" :error="authError">
        <el-input
          v-model="authCodeInput"
          :placeholder="t('auth.codePlaceholder')"
          :maxlength="4"
          :disabled="authLoading"
          @keyup.enter="submitAuth"
          @input="handleAuthInput"
          autofocus
        />
      </el-form-item>

      <el-alert
        v-if="connectionStore.lastAuthErrorMessage"
        type="warning"
        :closable="false"
        show-icon
        :title="connectionStore.lastAuthErrorMessage || t('auth.reAuthRequired')"
        class="auth-warning"
      />
    </el-form>

    <template #footer>
      <div class="auth-actions">
        <el-button :disabled="authLoading" @click="goToLogin">
          {{ t('auth.goToLogin') }}
        </el-button>
        <el-button :disabled="authLoading" @click="handlePasteFromClipboard">
          {{ t('auth.pasteFromClipboard') }}
        </el-button>
        <el-button type="primary" :loading="authLoading" :disabled="!isAuthCodeValid" @click="submitAuth">
          {{ t('auth.login') }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import Sidebar from './Sidebar.vue'
import Header from './Header.vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { useConnectionStore } from '@/stores/connection'
import { useAuthStore } from '@/stores/auth'
import { usePluginStore } from '@/stores/plugin'
import { get } from '@/api'

const { t } = useI18n()
const connectionStore = useConnectionStore()
const authStore = useAuthStore()
const pluginStore = usePluginStore()
const router = useRouter()

const authDialogVisible = ref(false)
const authCodeInput = ref('')
const authLoading = ref(false)
const authError = ref('')
const lastAutoAuthCode = ref('')
let autoAuthTimer: number | undefined

const isAuthCodeValid = computed(() => /^[A-Z]{4}$/.test(authCodeInput.value.trim().toUpperCase()))

watch(
  () => connectionStore.authRequired,
  (required) => {
    authDialogVisible.value = required
    if (required) {
      authCodeInput.value = ''
      authError.value = ''
      lastAutoAuthCode.value = ''
      if (autoAuthTimer) {
        clearTimeout(autoAuthTimer)
        autoAuthTimer = undefined
      }
    }
  },
  { immediate: true }
)

onUnmounted(() => {
  if (autoAuthTimer) {
    clearTimeout(autoAuthTimer)
    autoAuthTimer = undefined
  }
})

function handleAuthInput() {
  authCodeInput.value = authCodeInput.value.toUpperCase().slice(0, 4)
  authError.value = ''

  scheduleAutoSubmitAuth()
}

function scheduleAutoSubmitAuth() {
  if (!isAuthCodeValid.value) return
  if (authLoading.value) return

  const normalized = authCodeInput.value.trim().toUpperCase()
  if (normalized === lastAutoAuthCode.value) return

  if (autoAuthTimer) {
    clearTimeout(autoAuthTimer)
  }

  autoAuthTimer = window.setTimeout(async () => {
    if (!isAuthCodeValid.value) return
    if (authLoading.value) return

    const latest = authCodeInput.value.trim().toUpperCase()
    if (latest === lastAutoAuthCode.value) return

    const ok = await submitAuth()
    if (ok) {
      lastAutoAuthCode.value = latest
    }
  }, 150)
}

async function handlePasteFromClipboard() {
  try {
    if (!navigator.clipboard?.readText) {
      ElMessage.error(t('auth.clipboardUnsupported'))
      return
    }

    const text = await navigator.clipboard.readText()
    const match = (text || '').toUpperCase().match(/[A-Z]{4}/)
    if (!match) {
      ElMessage.warning(t('auth.clipboardNoCodeFound'))
      return
    }

    authCodeInput.value = match[0]
    authError.value = ''
    scheduleAutoSubmitAuth()
  } catch (error) {
    console.error('Failed to read clipboard:', error)
    ElMessage.error(t('auth.clipboardReadFailed'))
  }
}

async function submitAuth(): Promise<boolean> {
  if (!isAuthCodeValid.value) {
    authError.value = t('auth.codeError')
    return false
  }

  authLoading.value = true
  authError.value = ''
  try {
    const normalized = authCodeInput.value.trim().toUpperCase()
    const ok = authStore.setAuthCode(normalized)
    if (!ok) {
      authError.value = t('auth.codeError')
      return false
    }

    await get('/server/info')
    connectionStore.clearAuthRequired()
    authDialogVisible.value = false
    try {
      await pluginStore.fetchPlugins()
    } catch (e) {
      console.error('Failed to fetch plugins:', e)
      ElMessage.warning(t('plugins.fetchFailed'))
    }
    ElMessage.success(t('auth.loginSuccess'))
    return true
  } catch (e: any) {
    authStore.clearAuthCode()
    authError.value = t('auth.codeError')
    return false
  } finally {
    authLoading.value = false
  }
}

function goToLogin() {
  authStore.clearAuthCode()
  connectionStore.clearAuthRequired()
  authDialogVisible.value = false
  router.push('/login')
}

onMounted(() => {
  console.log('✅ AppLayout component mounted')
})
</script>

<style scoped>
.app-layout {
  height: 100vh;
  overflow: hidden;
}

.sidebar-container {
  background-color: var(--el-bg-color);
  border-right: 1px solid var(--el-border-color-light);
}

.header-container {
  background-color: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
  display: flex;
  align-items: center;
  padding: 0 20px;
}

.main-container {
  background-color: var(--el-bg-color-page);
  padding: 20px;
  overflow-y: auto;
}

.connection-banner {
  padding: 8px 20px 0 20px;
}

.auth-warning {
  margin-top: 8px;
}

.auth-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  width: 100%;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

