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
      </template>

      <LoadingSpinner v-if="loading && plugins.length === 0" :loading="true" :text="$t('common.loading')" />
      <EmptyState v-else-if="plugins.length === 0" :description="$t('plugins.noPlugins')" />
      
      <TransitionGroup v-else name="list" tag="div" class="plugin-grid">
        <div
          v-for="plugin in plugins"
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

const plugins = computed(() => pluginStore.pluginsWithStatus)
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
    // 显示性能指标时，先获取数据，再切换显示状态
    try {
      await metricsStore.fetchAllMetrics()
      showMetrics.value = true
      startMetricsAutoRefresh()
    } catch (error) {
      console.error('Failed to fetch metrics:', error)
      // 即使失败也显示，让用户看到错误状态
      showMetrics.value = true
    }
  } else {
    // 隐藏时停止自动刷新
    showMetrics.value = false
    stopMetricsAutoRefresh()
  }
}

function startMetricsAutoRefresh() {
  stopMetricsAutoRefresh()
  metricsRefreshTimer = window.setInterval(() => {
    metricsStore.fetchAllMetrics()
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

