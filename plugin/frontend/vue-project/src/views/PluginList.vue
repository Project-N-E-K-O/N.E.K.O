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
            <el-button type="primary" :icon="Refresh" @click="handleRefresh" :loading="loading">
              {{ $t('common.refresh') }}
            </el-button>
          </div>
        </div>

        <div class="filter-bar" @mouseenter="filterVisible = true">
          <template v-if="filterVisible">
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
          </template>
          <template v-else>
            <span class="filter-placeholder">{{ $t('plugins.hoverToShowFilter') }}</span>
          </template>
        </div>
      </template>

      <LoadingSpinner v-if="loading && rawPlugins.length === 0" :loading="true" :text="$t('common.loading')" />
      <EmptyState v-else-if="rawPlugins.length === 0" :description="$t('plugins.noPlugins')" />
      
      <TransitionGroup v-else name="list" tag="div" class="plugin-grid">
        <div
          v-for="plugin in filteredPlugins"
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
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh, DataAnalysis } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import PluginCard from '@/components/plugin/PluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { METRICS_REFRESH_INTERVAL } from '@/utils/constants'

const router = useRouter()
const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()

const rawPlugins = computed(() => pluginStore.pluginsWithStatus)
const filterVisible = ref(false)
const filterText = ref('')
const useRegex = ref(false)
const filterMode = ref<'whitelist' | 'blacklist'>('whitelist')
const regexError = ref(false)
const filteredPlugins = computed(() => {
  const list = rawPlugins.value || []
  const text = filterText.value.trim()
  if (!text) {
    regexError.value = false
    return list
  }

  if (useRegex.value) {
    try {
      const re = new RegExp(text, 'i')
      regexError.value = false
      const matches = (p: typeof list[number]) => {
        const id = p.id || ''
        const name = p.name || ''
        const desc = p.description || ''
        return re.test(id) || re.test(name) || re.test(desc)
      }
      
      if (filterMode.value === 'blacklist') {
        return list.filter((p) => !matches(p))
      }
      return list.filter((p) => matches(p))
    } catch {
      regexError.value = true
      return list
    }
  }

  regexError.value = false
  const lower = text.toLowerCase()
  const match = (p: typeof list[number]) => {
    const id = (p.id || '').toLowerCase()
    const name = (p.name || '').toLowerCase()
    const desc = (p.description || '').toLowerCase()
    return id.includes(lower) || name.includes(lower) || desc.includes(lower)
  }

  if (filterMode.value === 'blacklist') {
    return list.filter((p) => !match(p))
  }
  return list.filter((p) => match(p))
})
const loading = computed(() => pluginStore.loading)
const showMetrics = ref(false)
let metricsRefreshTimer: number | null = null

async function handleRefresh() {
  await pluginStore.fetchPlugins()
  await pluginStore.fetchPluginStatus()
  if (showMetrics.value) {
    await metricsStore.fetchAllMetrics()
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
  router.push(`/plugins/${pluginId}`)
}

onMounted(async () => {
  await handleRefresh()
})

onUnmounted(() => {
  stopMetricsAutoRefresh()
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

.filter-placeholder {
  color: var(--el-text-color-placeholder);
  font-style: italic;
  font-size: 14px;
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

.plugin-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  align-items: stretch; /* 让所有项目等高 */
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

