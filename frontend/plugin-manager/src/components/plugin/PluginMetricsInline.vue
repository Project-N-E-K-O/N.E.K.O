<template>
  <Transition name="fade" mode="out-in">
    <div v-if="metrics" key="data" class="plugin-metrics-inline">
      <el-descriptions :column="3" border size="small">
        <el-descriptions-item :label="t('metrics.cpu')">
          {{ metrics.cpu_percent.toFixed(1) }}%
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.memory')">
          {{ metrics.memory_mb.toFixed(2) }} MB
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.threads')">
          {{ metrics.num_threads }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.memoryPercent')">
          {{ metrics.memory_percent.toFixed(1) }}%
        </el-descriptions-item>
        <el-descriptions-item v-if="metrics.pending_requests !== undefined" :label="t('metrics.pendingRequests')">
          {{ metrics.pending_requests }}
        </el-descriptions-item>
        <el-descriptions-item v-if="metrics.total_executions !== undefined" :label="t('metrics.totalExecutions')">
          {{ metrics.total_executions }}
        </el-descriptions-item>
      </el-descriptions>
    </div>
    <div v-else key="empty" class="plugin-metrics-inline empty">
      <span class="empty-text">{{ t('metrics.noData') }}</span>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMetricsStore } from '@/stores/metrics'
import type { PluginMetrics } from '@/types/api'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const { t } = useI18n()
const metricsStore = useMetricsStore()

const metrics = computed<PluginMetrics | null>(() => {
  return metricsStore.getCurrentMetrics(props.pluginId)
})

// 如果当前没有该插件的指标数据，尝试单独获取
async function loadMetrics(id: string) {
  if (!id || metricsStore.getCurrentMetrics(id)) return
  try {
    await metricsStore.fetchPluginMetrics(id)
  } catch (e) {
    console.warn(`[PluginMetricsInline] failed to fetch metrics for ${id}`, e)
  }
}

onMounted(() => {
  console.log(`[PluginMetricsInline] Component mounted for plugin: ${props.pluginId}, has metrics: ${!!metrics.value}`)
  void loadMetrics(props.pluginId)
})

// 监听 pluginId 变化，重新获取数据
watch(() => props.pluginId, (newId) => {
  void loadMetrics(newId)
})
</script>

<style scoped>
.plugin-metrics-inline {
  padding: 0;
  background-color: transparent;
  border-radius: 0;
}

.plugin-metrics-inline.empty {
  padding: 12px;
  text-align: center;
  color: var(--el-text-color-placeholder);
}

.empty-text {
  font-size: 12px;
}

/* 淡入淡出动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

