<template>
  <div class="header__actions">
    <LanguageSwitcher />
    <el-dropdown trigger="click" :persistent="false" @command="handleThemeCommand">
      <button type="button" class="header-btn" :title="$t('common.chooseTheme')" :aria-label="$t('common.chooseTheme')" data-testid="theme-switcher">
        <el-icon :size="16"><Moon v-if="isDark" /><Sunny v-else /></el-icon>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item
            v-for="choice in THEME_CHOICES"
            :key="choice.id"
            :command="choice.id"
            :divided="choice.divided"
            :disabled="currentTheme === choice.id"
          >{{ $t(choice.label) }}</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
    <button class="header-btn" :disabled="refreshing" :aria-label="$t('common.refresh')" @click="handleRefresh">
      <el-icon :size="16" :class="{ 'spin': refreshing }"><Refresh /></el-icon>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Refresh, Sunny, Moon } from '@element-plus/icons-vue'
import { PluginDataRefreshError, usePluginStore } from '@/stores/plugin'
import { ElMessage } from 'element-plus'
import LanguageSwitcher from '@/components/common/LanguageSwitcher.vue'
import { useDarkMode, type DarkPalette, type LightPalette } from '@/composables/useDarkMode'

type ThemeChoice = `light-${LightPalette}` | `dark-${DarkPalette}`

const THEME_CHOICES: { id: ThemeChoice; label: string; divided?: boolean }[] = [
  { id: 'light-neko', label: 'common.lightModeNeko' },
  { id: 'light-paper', label: 'common.lightModePaper' },
  { id: 'dark-neko', label: 'common.darkModeNeko', divided: true },
  { id: 'dark-graphite', label: 'common.darkModeGraphite' },
]

const pluginStore = usePluginStore()
const { t } = useI18n()
const refreshing = ref(false)
const { isDark, lightPalette, darkPalette, setDarkMode, setLightPalette, setDarkPalette } =
  useDarkMode()
const currentTheme = computed<ThemeChoice>(() =>
  isDark.value ? `dark-${darkPalette.value}` : `light-${lightPalette.value}`
)

function handleThemeCommand(command: ThemeChoice) {
  if (command.startsWith('dark-')) {
    setDarkPalette(command.slice('dark-'.length) as DarkPalette)
    setDarkMode(true)
  } else {
    setLightPalette(command.slice('light-'.length) as LightPalette)
    setDarkMode(false)
  }
}

async function handleRefresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    const [data, status] = await Promise.allSettled([
      pluginStore.refreshLoadedPluginData(),
      pluginStore.fetchPluginStatus(),
    ])
    const dataUpdated = data.status === 'fulfilled'
    const dataPartiallyUpdated = data.status === 'rejected'
      && data.reason instanceof PluginDataRefreshError
      && data.reason.successfulRequests > 0
    const statusUpdated = status.status === 'fulfilled' && status.value === 'applied'
    const statusSuperseded = status.status === 'fulfilled' && status.value === 'superseded'
    // `superseded` confirms a newer query has already applied successfully.
    if (dataUpdated && statusSuperseded) return
    if (dataUpdated && statusUpdated) ElMessage.success(t('messages.operationSuccess'))
    else if (dataUpdated || dataPartiallyUpdated || statusUpdated || statusSuperseded) ElMessage.warning(t('messages.refreshPartialFailure'))
    else ElMessage.error(t('messages.operationFailed'))
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
