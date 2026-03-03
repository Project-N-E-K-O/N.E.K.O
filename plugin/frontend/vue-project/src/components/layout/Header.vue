<template>
  <div class="header">
    <div class="header-left">
      <h1 class="header-title">{{ currentTitle }}</h1>
    </div>
    <div class="header-right">
      <LanguageSwitcher />
      <el-button
        :icon="isDark ? Sunny : Moon"
        circle
        @click="toggleDarkMode"
        :title="isDark ? $t('common.lightMode') : $t('common.darkMode')"
      />
      <el-button
        :icon="Refresh"
        circle
        @click="handleRefresh"
        :loading="refreshing"
      />
      <el-button
        :icon="SwitchButton"
        circle
        @click="handleLogout"
        :title="$t('auth.logout')"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, Sunny, Moon, SwitchButton } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { usePluginStore } from '@/stores/plugin'
import { useAuthStore } from '@/stores/auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import LanguageSwitcher from '@/components/common/LanguageSwitcher.vue'
import { useDarkMode } from '@/composables/useDarkMode'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const authStore = useAuthStore()
const { t } = useI18n()
const refreshing = ref(false)
const { isDark, toggleDarkMode } = useDarkMode()

const currentTitle = computed(() => {
  if (route.meta.titleKey) {
    return t(route.meta.titleKey as string)
  }
  return t('app.titleSuffix')
})

async function handleRefresh() {
  refreshing.value = true
  try {
    await Promise.all([
      pluginStore.fetchPlugins(),
      pluginStore.fetchPluginStatus()
    ])
    ElMessage.success(t('messages.operationSuccess'))
  } catch (error) {
    ElMessage.error(t('messages.operationFailed'))
  } finally {
    refreshing.value = false
  }
}

async function handleLogout() {
  try {
    await ElMessageBox.confirm(
      t('auth.logoutConfirm'),
      t('common.logoutConfirmTitle'),
      {
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
        type: 'warning'
      }
    )
    authStore.clearAuthCode()
    ElMessage.success(t('auth.logoutSuccess'))
    router.push('/login')
  } catch {
    // 用户取消
  }
}
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.header-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.header-right {
  display: flex;
  gap: 12px;
}
</style>

