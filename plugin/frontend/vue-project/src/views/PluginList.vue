<template>
  <div class="plugin-list">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>{{ $t('plugins.title') }}</span>
          <div class="header-actions">
            <el-button
              :type="showMetrics ? 'success' : 'default'"
              :icon="DataAnalysis"
              @click="toggleMetrics"
            >
              {{ showMetrics ? $t('plugins.hideMetrics') : $t('plugins.showMetrics') }}
            </el-button>
            <el-button
              type="warning"
              :icon="RefreshRight"
              :loading="reloadingAll"
              :disabled="runningPlugins.length === 0"
              @click="handleReloadAll"
            >
              {{ $t('plugins.reloadAll') }}
            </el-button>
            <el-button type="primary" :icon="Refresh" @click="handleRefresh" :loading="loading">
              {{ $t('common.refresh') }}
            </el-button>
          </div>
        </div>

        <div class="filter-bar" @mouseenter="showFilter" @mouseleave="scheduleHideFilter">
          <Transition name="filter-fade" mode="out-in">
            <div v-if="filterVisible" key="controls" class="filter-controls">
              <el-input
                v-model="filterText"
                clearable
                class="filter-input"
                :placeholder="$t('plugins.filterPlaceholder')"
              />
              <el-switch
                v-model="useRegex"
                class="filter-switch"
                active-text="Regex"
                inactive-text="Text"
              />
              <el-radio-group v-model="filterMode" size="small" class="filter-mode">
                <el-radio-button label="whitelist">{{ $t('plugins.filterWhitelist') }}</el-radio-button>
                <el-radio-button label="blacklist">{{ $t('plugins.filterBlacklist') }}</el-radio-button>
              </el-radio-group>
              <span v-if="regexError" class="filter-error">{{ $t('plugins.invalidRegex') }}</span>
            </div>
            <span v-else key="placeholder" class="filter-placeholder">{{ $t('plugins.hoverToShowFilter') }}</span>
          </Transition>
        </div>
      </template>

      <LoadingSpinner v-if="loading && rawPlugins.length === 0" :loading="true" :text="$t('common.loading')" />
      <EmptyState v-else-if="rawPlugins.length === 0" :description="$t('plugins.noPlugins')" />
      
      <template v-else>
        <!-- 普通插件 -->
        <div class="section-header">
          <span class="section-title">{{ $t('plugins.pluginsSection') }} ({{ filteredNormalPlugins.length }})</span>
        </div>
        <TransitionGroup name="list" tag="div" class="plugin-grid">
          <div
            v-for="plugin in filteredNormalPlugins"
            :key="plugin.id"
            class="plugin-item"
          >
            <PluginCard
              :plugin="plugin"
              :show-metrics="showMetrics"
              @click="handlePluginClick(plugin.id)"
            />
          </div>
        </TransitionGroup>

        <!-- 扩展插件 -->
        <template v-if="filteredExtensions.length > 0">
          <div class="section-header section-header--ext">
            <span class="section-title">{{ $t('plugins.extensionsSection') }} ({{ filteredExtensions.length }})</span>
          </div>
          <TransitionGroup name="list" tag="div" class="plugin-grid">
            <div
              v-for="ext in filteredExtensions"
              :key="ext.id"
              class="plugin-item"
            >
              <PluginCard
                :plugin="ext"
                :show-metrics="showMetrics"
                @click="handlePluginClick(ext.id)"
              />
            </div>
          </TransitionGroup>
        </template>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh, DataAnalysis, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import PluginCard from '@/components/plugin/PluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { reloadAllPlugins } from '@/api/plugins'
import { METRICS_REFRESH_INTERVAL } from '@/utils/constants'
import { useI18n } from 'vue-i18n'

const router = useRouter()
const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()
const { t } = useI18n()

const reloadingAll = ref(false)

const rawPlugins = computed(() => pluginStore.pluginsWithStatus)
const rawNormalPlugins = computed(() => pluginStore.normalPlugins)
const rawExtensions = computed(() => pluginStore.extensions)
const filterVisible = ref(false)
let hideTimer: number | null = null

function showFilter() {
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
  filterVisible.value = true
}

function scheduleHideFilter() {
  if (hideTimer) clearTimeout(hideTimer)
  hideTimer = window.setTimeout(() => {
    filterVisible.value = false
    hideTimer = null
  }, 1000)
}
const filterText = ref('')
const useRegex = ref(false)
const filterMode = ref<'whitelist' | 'blacklist'>('whitelist')
const regexError = ref(false)
function applyFilter<T extends { id: string; name: string; description: string }>(list: T[]): T[] {
  const text = filterText.value.trim()
  if (!text) {
    regexError.value = false
    return list
  }

  if (useRegex.value) {
    try {
      const re = new RegExp(text, 'i')
      regexError.value = false
      const matches = (p: T) => re.test(p.id || '') || re.test(p.name || '') || re.test(p.description || '')
      return filterMode.value === 'blacklist' ? list.filter(p => !matches(p)) : list.filter(p => matches(p))
    } catch {
      regexError.value = true
      return list
    }
  }

  regexError.value = false
  const lower = text.toLowerCase()
  const match = (p: T) => {
    return (p.id || '').toLowerCase().includes(lower) ||
           (p.name || '').toLowerCase().includes(lower) ||
           (p.description || '').toLowerCase().includes(lower)
  }
  return filterMode.value === 'blacklist' ? list.filter(p => !match(p)) : list.filter(p => match(p))
}

const filteredNormalPlugins = computed(() => applyFilter(rawNormalPlugins.value || []))
const filteredExtensions = computed(() => applyFilter(rawExtensions.value || []))
const loading = computed(() => pluginStore.loading)
const showMetrics = ref(false)
let metricsRefreshTimer: number | null = null

async function handleRefresh() {
  await pluginStore.fetchPlugins()
  await pluginStore.fetchPluginStatus()
  if (showMetrics.value) {
    try {
      await metricsStore.fetchAllMetrics()
    } catch (error) {
      console.warn('Failed to refresh metrics:', error)
    }
  }
}

async function toggleMetrics() {
  if (!showMetrics.value) {
    try {
      await metricsStore.fetchAllMetrics()
      showMetrics.value = true
      startMetricsAutoRefresh()
    } catch (error) {
      console.error('Failed to fetch metrics:', error)
      showMetrics.value = false
      stopMetricsAutoRefresh()
    }
  } else {
    showMetrics.value = false
    stopMetricsAutoRefresh()
  }
}

function startMetricsAutoRefresh() {
  stopMetricsAutoRefresh()
  metricsRefreshTimer = window.setInterval(() => {
    metricsStore.fetchAllMetrics().catch((error) => {
      console.warn('Auto-refresh metrics failed:', error)
    })
  }, METRICS_REFRESH_INTERVAL)
}

function stopMetricsAutoRefresh() {
  if (metricsRefreshTimer) {
    clearInterval(metricsRefreshTimer)
    metricsRefreshTimer = null
  }
}

function handlePluginClick(pluginId: string) {
  const safeId = encodeURIComponent(pluginId)
  router.push(`/plugins/${safeId}`)
}

// 获取运行中的普通插件列表（排除 extension）
const runningPlugins = computed(() => {
  return rawNormalPlugins.value.filter(p => p.status === 'running' && p.enabled !== false)
})

// 全局重载所有运行中的插件（使用后端批量 API）
async function handleReloadAll() {
  const plugins = runningPlugins.value
  if (plugins.length === 0) return

  try {
    await ElMessageBox.confirm(
      t('plugins.reloadAllConfirm', { count: plugins.length }),
      t('common.confirm'),
      {
        confirmButtonText: t('common.confirm'),
        cancelButtonText: t('common.cancel'),
        type: 'warning'
      }
    )
  } catch {
    return
  }

  reloadingAll.value = true

  try {
    // 使用后端批量 API，避免锁竞争导致超时
    const result = await reloadAllPlugins()
    
    const successCount = result.reloaded.length
    const failCount = result.failed.length

    // 记录失败的插件
    result.failed.forEach(item => {
      console.error(`Failed to reload plugin ${item.plugin_id}:`, item.error)
    })

    if (failCount === 0) {
      ElMessage.success(t('plugins.reloadAllSuccess', { count: successCount }))
    } else {
      ElMessage.warning(t('plugins.reloadAllPartial', { success: successCount, fail: failCount }))
    }
  } catch (error) {
    console.error('Failed to reload all plugins:', error)
    ElMessage.error(t('messages.reloadFailed'))
  } finally {
    reloadingAll.value = false
  }

  // 刷新列表
  await handleRefresh()
}

onMounted(async () => {
  await handleRefresh()
})

onUnmounted(() => {
  stopMetricsAutoRefresh()
  if (hideTimer) {
    clearTimeout(hideTimer)
    hideTimer = null
  }
})
</script>

<style scoped>
.filter-bar {
  margin-top: 16px;
  padding: 12px;
  background-color: var(--el-fill-color-light);
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  min-height: 56px;
  box-sizing: border-box;
}

.filter-input {
  flex: 1;
  min-width: 200px;
}

.filter-switch {
  flex-shrink: 0;
}

.filter-mode {
  flex-shrink: 0;
}

.filter-error {
  color: var(--el-color-danger);
  font-size: 12px;
  flex-shrink: 0;
}

.filter-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  width: 100%;
}

.filter-placeholder {
  color: var(--el-text-color-placeholder);
  font-style: italic;
  font-size: 14px;
  line-height: 32px;
}

.filter-fade-enter-active,
.filter-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.filter-fade-enter-from {
  opacity: 0;
  transform: translateY(-4px);
}

.filter-fade-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

.filter-fade-enter-to,
.filter-fade-leave-from {
  opacity: 1;
  transform: translateY(0);
}

.plugin-list {
  padding: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.section-header {
  margin-bottom: 12px;
}

.section-header--ext {
  margin-top: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.plugin-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  align-items: stretch;
}

.plugin-item {
  display: flex;
  flex-direction: column;
  height: 100%; /* 确保项目占满网格单元格高度 */
}

.plugin-item :deep(.plugin-card) {
  height: 100%; /* 让卡片占满容器高度 */
  display: flex;
  flex-direction: column;
}

.plugin-item :deep(.el-card__body) {
  flex: 1; /* 让卡片内容区域自动填充剩余空间 */
  display: flex;
  flex-direction: column;
}

.plugin-card-body {
  flex: 1; /* 让卡片主体内容区域自动填充 */
  display: flex;
  flex-direction: column;
}

/* 列表项过渡动画 */
.list-enter-active,
.list-leave-active {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.list-enter-from {
  opacity: 0;
  transform: scale(0.9) translateY(10px);
}

.list-leave-to {
  opacity: 0;
  transform: scale(0.9) translateY(-10px);
}

.list-move {
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

</style>

