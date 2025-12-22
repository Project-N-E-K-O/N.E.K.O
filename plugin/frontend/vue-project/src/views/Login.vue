<template>
  <div class="login-container">
    <el-card class="login-card">
      <template #header>
        <div class="login-header">
          <h2>🔐 N.E.K.O 插件管理</h2>
          <p class="subtitle">请输入管理员验证码</p>
        </div>
      </template>

      <el-form @submit.prevent="handleLogin">
        <el-form-item label="验证码" :error="errorMessage">
          <el-input
            v-model="code"
            placeholder="请输入4位字母验证码"
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
          <el-button
            type="primary"
            :loading="loading"
            :disabled="!isCodeValid"
            @click="handleLogin"
            size="large"
            class="login-button"
            block
          >
            {{ loading ? '验证中...' : '登录' }}
          </el-button>
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
              <p>验证码在服务器启动时显示在终端中</p>
              <p class="hint-small">格式：4个大写字母（如：ABCD）</p>
            </div>
          </template>
        </el-alert>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { get } from '@/api'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const code = ref('')
const loading = ref(false)
const errorMessage = ref('')

const isCodeValid = computed(() => {
  const normalized = code.value.trim().toUpperCase()
  return /^[A-Z]{0,4}$/.test(normalized) && normalized.length === 4
})

function handleInput() {
  // 自动转换为大写
  code.value = code.value.toUpperCase()
  errorMessage.value = ''
}

async function handleLogin() {
  if (!isCodeValid.value) {
    errorMessage.value = '请输入4位字母验证码'
    return
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
      ElMessage.success('登录成功')
      const redirect = (route.query.redirect as string) || '/'
      router.push(redirect)
    } catch (error: any) {
      // 如果返回 401 或 403，说明验证码错误
      if (error.response?.status === 401 || error.response?.status === 403) {
        authStore.clearAuthCode()
        errorMessage.value = '验证码错误，请重新输入'
        ElMessage.error('验证码错误')
      } else {
        // 其他错误，可能是网络问题，但验证码可能正确
        // 先保存验证码，让用户继续使用
        ElMessage.warning('网络错误，但验证码已保存')
        const redirect = (route.query.redirect as string) || '/'
        router.push(redirect)
      }
    }
  } catch (error) {
    console.error('Login error:', error)
    errorMessage.value = '登录失败，请重试'
    ElMessage.error('登录失败')
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

