<template>
  <div class="login-container">
    <el-card class="login-card">
      <template #header>
        <div class="login-header">
          <h2>🔐 {{ t('auth.loginTitle') }}</h2>
          <p class="subtitle">{{ t('auth.loginSubtitle') }}</p>
        </div>
      </template>

      <el-form @submit.prevent="handleLogin">
        <el-form-item :label="t('auth.login')" :error="errorMessage">
          <el-input
            v-model="code"
            :placeholder="t('auth.codePlaceholder')"
            :maxlength="4"
            :disabled="loading"
            @keyup.enter="handleLogin"
            @input="handleInput"
            class="code-input"
            size="large"
            autofocus
          >
            <template #prefix>
              <el-icon><Lock /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item>
          <div class="login-actions">
            <el-button
              type="primary"
              :loading="loading"
              :disabled="!isCodeValid"
              @click="handleLogin"
              size="large"
              class="login-button"
            >
              {{ loading ? t('auth.loggingIn') : t('auth.login') }}
            </el-button>
            <el-button
              :disabled="loading"
              @click="handlePasteFromClipboard"
              size="large"
              class="paste-button"
            >
              {{ t('auth.pasteFromClipboard') }}
            </el-button>
          </div>
        </el-form-item>
      </el-form>

      <div class="login-hint">
        <el-alert
          type="info"
          :closable="false"
          show-icon
        >
          <template #title>
            <div class="hint-content">
              <p>{{ t('auth.codeHint') }}</p>
              <p class="hint-small">{{ t('auth.codeFormat') }}</p>
            </div>
          </template>
        </el-alert>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { get } from '@/api'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const { t } = useI18n()

const code = ref('')
const loading = ref(false)
const errorMessage = ref('')
const lastAutoLoginCode = ref('')
let autoLoginTimer: number | undefined

const isCodeValid = computed(() => {
  const normalized = code.value.trim().toUpperCase()
  return /^[A-Z]{4}$/.test(normalized)
})

function isValidRedirect(url: string): boolean {
  if (!url) return false
  return url.startsWith('/') && !url.startsWith('//')
}

function handleInput() {
  // 自动转换为大写
  code.value = code.value.toUpperCase().slice(0, 4)
  errorMessage.value = ''

  scheduleAutoLogin()
}

function scheduleAutoLogin() {
  if (!isCodeValid.value) return
  if (loading.value) return

  const normalized = code.value.trim().toUpperCase()
  if (normalized === lastAutoLoginCode.value) return

  if (autoLoginTimer) {
    clearTimeout(autoLoginTimer)
  }

  autoLoginTimer = window.setTimeout(async () => {
    if (!isCodeValid.value) return
    if (loading.value) return

    const latest = code.value.trim().toUpperCase()
    if (latest === lastAutoLoginCode.value) return

    const ok = await handleLogin()
    if (ok) {
      lastAutoLoginCode.value = latest
    }
  }, 150)
}

onUnmounted(() => {
  if (autoLoginTimer) {
    clearTimeout(autoLoginTimer)
    autoLoginTimer = undefined
  }
})

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

    code.value = match[0]
    errorMessage.value = ''
    scheduleAutoLogin()
  } catch (error) {
    console.error('Failed to read clipboard:', error)
    ElMessage.error(t('auth.clipboardReadFailed'))
  }
}

async function handleLogin(): Promise<boolean> {
  if (!isCodeValid.value) {
    errorMessage.value = t('auth.codePlaceholder')
    return false
  }

  loading.value = true
  errorMessage.value = ''

  try {
    // 先设置验证码
    const normalizedCode = code.value.trim().toUpperCase()
    authStore.setAuthCode(normalizedCode)

    // 尝试访问一个需要认证的端点来验证
    try {
      await get('/server/info')
      // 验证成功，跳转到目标页面或首页
      ElMessage.success(t('auth.loginSuccess'))
      const redirect = route.query.redirect as string
      router.push(isValidRedirect(redirect) ? redirect : '/')
      return true
    } catch (error: any) {
      // 如果返回 401 或 403，说明验证码错误
      if (error.response?.status === 401 || error.response?.status === 403) {
        authStore.clearAuthCode()
        errorMessage.value = t('auth.codeError')
        ElMessage.error(t('auth.codeError'))
        return false
      } else {
        // 其他错误（500、网络问题等），不保存验证码
        authStore.clearAuthCode()
        errorMessage.value = t('auth.loginFailed')
        ElMessage.error(t('auth.networkError'))
        return false
      }
    }
  } catch (error) {
    console.error('Login error:', error)
    errorMessage.value = t('auth.loginFailed')
    ElMessage.error(t('auth.loginFailed'))
    return false
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.2);
}

.login-header {
  text-align: center;
}

.login-header h2 {
  margin: 0 0 8px 0;
  color: #303133;
  font-size: 24px;
}

.subtitle {
  margin: 0;
  color: #909399;
  font-size: 14px;
}

.code-input {
  font-size: 18px;
  letter-spacing: 8px;
  text-align: center;
  font-weight: bold;
}

.code-input :deep(.el-input__inner) {
  text-align: center;
  letter-spacing: 8px;
  font-size: 18px;
  font-weight: bold;
}

.login-button {
  margin-top: 20px;
  width: 100%;
}

.login-actions {
  display: flex;
  gap: 12px;
  width: 100%;
  margin-top: 20px;
}

.login-actions .login-button {
  margin-top: 0;
  flex: 1;
}

.paste-button {
  flex: 1;
}

.login-hint {
  margin-top: 24px;
}

.hint-content {
  font-size: 13px;
  line-height: 1.6;
}

.hint-content p {
  margin: 4px 0;
}

.hint-small {
  color: #909399;
  font-size: 12px;
}
</style>

