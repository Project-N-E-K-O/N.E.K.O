<template>
  <div class="header">
    <h1 class="header__title">{{ currentTitle }}</h1>

    <div class="header__actions">
      <LanguageSwitcher />
      <button class="header-btn" :title="isDark ? $t('common.lightMode') : $t('common.darkMode')" @click="toggleDarkMode">
        <el-icon :size="16"><Sunny v-if="isDark" /><Moon v-else /></el-icon>
      </button>
      <button class="header-btn" :disabled="refreshing" @click="handleRefresh">
        <el-icon :size="16" :class="{ 'spin': refreshing }"><Refresh /></el-icon>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Refresh, Sunny, Moon } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { ElMessage } from 'element-plus'
import LanguageSwitcher from '@/components/common/LanguageSwitcher.vue'
import { useDarkMode } from '@/composables/useDarkMode'

const route = useRoute()
const pluginStore = usePluginStore()
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
    await Promise.all([pluginStore.fetchPlugins(), pluginStore.fetchPluginStatus()])
    ElMessage.success(t('messages.operationSuccess'))
  } catch {
    ElMessage.error(t('messages.operationFailed'))
  } finally {
    refreshing.value = false
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

.header__title {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-btn {
  width: 34px;
  height: 34px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  border-radius: 10px;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    transform 0.18s ease;
}

.header-btn:hover {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  color: var(--el-color-primary);
  transform: translateY(-1px);
}

.header-btn:active {
  transform: translateY(0) scale(0.95);
}

.header-btn:disabled {
  opacity: 0.4;
  pointer-events: none;
}

.spin {
  display: inline-block;
  animation: spin-rotate 0.8s linear infinite;
}

@keyframes spin-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
