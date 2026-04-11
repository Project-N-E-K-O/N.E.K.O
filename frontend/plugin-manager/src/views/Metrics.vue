<template>
  <div class="metrics-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>{{ $t('metrics.title') }}</span>
          <el-button :icon="Refresh" @click="handleRefresh" :loading="loading">
            {{ $t('common.refresh') }}
          </el-button>
        </div>
      </template>

      <LoadingSpinner v-if="loading && metrics.length === 0" :loading="true" :text="$t('common.loading')" />
      <EmptyState v-else-if="metrics.length === 0" :description="$t('metrics.noMetrics')" />
      
      <div v-else class="metrics-grid">
        <MetricsCard
          v-for="metric in metrics"
          :key="metric.plugin_id"
          :plugin-id="metric.plugin_id"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { useMetricsStore } from '@/stores/metrics'
import MetricsCard from '@/components/metrics/MetricsCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { METRICS_REFRESH_INTERVAL } from '@/utils/constants'

const metricsStore = useMetricsStore()

const metrics = computed(() => metricsStore.allMetrics)
const loading = computed(() => metricsStore.loading)

let refreshTimer: number | null = null

async function handleRefresh() {
  await metricsStore.fetchAllMetrics()
}

function startAutoRefresh() {
  refreshTimer = window.setInterval(async () => {
    if (!loading.value) {
      await handleRefresh()
    }
  }, METRICS_REFRESH_INTERVAL)
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

onMounted(async () => {
  await handleRefresh()
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<style scoped>
.metrics-page {
  padding: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
  gap: 16px;
}
</style>

