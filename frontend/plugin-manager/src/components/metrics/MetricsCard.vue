<template>
  <el-card class="metrics-card">
    <template #header>
      <div class="card-header">
        <span>{{ pluginId }}</span>
      </div>
    </template>

    <div v-if="metrics" class="metrics-content">
      <el-descriptions :column="2" border size="small">
        <el-descriptions-item :label="t('metrics.cpu')">
          {{ metrics.cpu_percent }}%
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.memoryMb')">
          {{ metrics.memory_mb.toFixed(2) }} MB
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.memoryPercent')">
          {{ metrics.memory_percent.toFixed(2) }}%
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.threads')">
          {{ metrics.num_threads }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.pendingRequests')">
          {{ metrics.pending_requests || 0 }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('metrics.updatedAt')">
          {{ formatTime(metrics.timestamp) }}
        </el-descriptions-item>
      </el-descriptions>
    </div>
    <EmptyState v-else :description="t('metrics.noData')" />
  </el-card>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { useMetricsStore } from '@/stores/metrics'
import EmptyState from '@/components/common/EmptyState.vue'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const { t } = useI18n()
const metricsStore = useMetricsStore()

const metrics = computed(() => {
  return metricsStore.getCurrentMetrics(props.pluginId)
})

function formatTime(timestamp: string) {
  return dayjs(timestamp).format('YYYY-MM-DD HH:mm:ss')
}

onMounted(async () => {
  console.log(`[MetricsCard] Component mounted for plugin: ${props.pluginId}`)
  if (props.pluginId) {
    await metricsStore.fetchPluginMetrics(props.pluginId)
  } else {
    console.warn('[MetricsCard] Component mounted with empty pluginId')
  }
})
</script>

<style scoped>
.metrics-card {
  height: 100%;
}

.card-header {
  font-weight: 600;
}

.metrics-content {
  padding: 10px 0;
}
</style>

