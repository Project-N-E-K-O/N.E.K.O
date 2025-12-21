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
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, Sunny, Moon } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
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
  return 'N.E.K.O 插件管理'
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

