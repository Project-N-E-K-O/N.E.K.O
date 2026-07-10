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
const GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY = 'neko-goodbye-resource-suspended'

function isGoodbyeResourceSuspendingOrSuspended() {
  if (typeof window === 'undefined') return false
  try {
    const helper = (window as any).isNekoGoodbyeResourceSuspendingOrSuspended
    if (typeof helper === 'function' && helper()) return true
    if ((window as any).goodbyeResourceSuspended === true) return true
    if ((window as any).__nekoGoodbyeResourceSuspendPending === true) return true
    return window.localStorage.getItem(GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

async function handleRefresh() {
  await metricsStore.fetchAllMetrics()
}

function startAutoRefresh() {
  if (isGoodbyeResourceSuspendingOrSuspended()) return
  stopAutoRefresh()
  refreshTimer = window.setInterval(async () => {
    if (isGoodbyeResourceSuspendingOrSuspended()) {
      stopAutoRefresh()
      return
    }
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

function handleGoodbyeResourceState(event: Event) {
  const detail = (event as CustomEvent).detail || {}
  if (detail.suspended || detail.pending || isGoodbyeResourceSuspendingOrSuspended()) {
    stopAutoRefresh()
  } else {
    startAutoRefresh()
  }
}

function handleGoodbyeResourceStorage(event: StorageEvent) {
  if (event.key !== null && event.key !== GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY) return
  if (isGoodbyeResourceSuspendingOrSuspended()) {
    stopAutoRefresh()
  } else {
    startAutoRefresh()
  }
}

onMounted(async () => {
  await handleRefresh()
  window.addEventListener('neko:goodbye-resource-suspend-state', handleGoodbyeResourceState)
  window.addEventListener('storage', handleGoodbyeResourceStorage)
  startAutoRefresh()
})

onUnmounted(() => {
  window.removeEventListener('neko:goodbye-resource-suspend-state', handleGoodbyeResourceState)
  window.removeEventListener('storage', handleGoodbyeResourceStorage)
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
