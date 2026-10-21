<template>
  <div class="header__actions">
    <LanguageSwitcher />
    <button class="header-btn" :title="isDark ? $t('common.lightMode') : $t('common.darkMode')" :aria-label="isDark ? $t('common.lightMode') : $t('common.darkMode')" @click="toggleDarkMode">
      <el-icon :size="16"><Sunny v-if="isDark" /><Moon v-else /></el-icon>
    </button>
    <button class="header-btn" :disabled="refreshing" :aria-label="$t('common.refresh')" @click="handleRefresh">
      <el-icon :size="16" :class="{ 'spin': refreshing }"><Refresh /></el-icon>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Refresh, Sunny, Moon } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { ElMessage } from 'element-plus'
import LanguageSwitcher from '@/components/common/LanguageSwitcher.vue'
import { useDarkMode } from '@/composables/useDarkMode'

const pluginStore = usePluginStore()
const { t } = useI18n()
const refreshing = ref(false)
const { isDark, toggleDarkMode } = useDarkMode()

async function handleRefresh() {
  refreshing.value = true
  try {
    await Promise.all([pluginStore.refreshLoadedPluginData(), pluginStore.fetchPluginStatus()])
    ElMessage.success(t('messages.operationSuccess'))
  } catch {
    ElMessage.error(t('messages.operationFailed'))
  } finally {
    refreshing.value = false
  }
}
</script>

<style scoped>
.header__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  -webkit-app-region: no-drag;
}

.header-btn,
.header__actions :deep(.el-button) {
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    background-color var(--motion-duration-normal) var(--motion-ease-standard),
    color var(--motion-duration-normal) var(--motion-ease-standard);
}

.header-btn:hover,
.header__actions :deep(.el-button:hover) {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
  color: var(--el-color-primary);
}

.header-btn:active {
  background: color-mix(in srgb, var(--el-color-primary) 14%, transparent);
}

.header-btn:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
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
@media (prefers-reduced-motion: reduce) {
  .header-btn, .header__actions :deep(.el-button) { transition: none; }
  .spin { animation: none; }
}
</style>
