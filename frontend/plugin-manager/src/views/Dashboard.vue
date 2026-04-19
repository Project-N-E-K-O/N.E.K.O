<template>
  <div class="dashboard">
    <!-- ── Stats row ── -->
    <div class="stats-row">
      <div
        v-for="(stat, i) in statCards"
        :key="stat.key"
        v-motion
        :initial="{ opacity: 0, scale: 0.92, y: 18, filter: 'blur(6px)' }"
        :enter="{ opacity: 1, scale: 1, y: 0, filter: 'blur(0px)', transition: { delay: i * 60, duration: 420, type: 'spring', stiffness: 260, damping: 24 } }"
        class="stat-card"
        :class="`stat-card--${stat.key}`"
      >
        <div class="stat-card__icon">
          <el-icon :size="24"><component :is="stat.icon" /></el-icon>
        </div>
        <div class="stat-card__body">
          <span class="stat-card__value">{{ stat.value }}</span>
          <span class="stat-card__label">{{ stat.label }}</span>
        </div>
      </div>
    </div>

    <!-- ── Main grid ── -->
    <div class="main-grid">
      <!-- Global metrics -->
      <div
        v-motion
        :initial="{ opacity: 0, y: 24, filter: 'blur(6px)' }"
        :enter="{ opacity: 1, y: 0, filter: 'blur(0px)', transition: { delay: 280, duration: 460, type: 'spring', stiffness: 220, damping: 22 } }"
        class="panel panel--metrics"
      >
        <div class="panel__header">
          <span class="panel__title">{{ $t('dashboard.globalMetrics') }}</span>
          <button class="panel__action" :disabled="metricsLoading" @click="handleRefreshMetrics">
            <el-icon :class="{ 'spin': metricsLoading }"><Refresh /></el-icon>
          </button>
        </div>

        <div v-if="globalMetrics" class="metrics-grid">
          <div class="gauge-card">
            <div class="gauge">
              <svg viewBox="0 0 80 80" class="gauge__svg">
                <circle cx="40" cy="40" r="34" class="gauge__track" />
                <circle
                  cx="40" cy="40" r="34"
                  class="gauge__fill gauge__fill--cpu"
                  :style="{ strokeDashoffset: cpuOffset }"
                />
              </svg>
              <span class="gauge__value">{{ cpuDisplay }}</span>
            </div>
            <span class="gauge-card__label">{{ $t('dashboard.totalCpuUsage') }}</span>
          </div>

          <div class="gauge-card">
            <div class="gauge">
              <svg viewBox="0 0 80 80" class="gauge__svg">
                <circle cx="40" cy="40" r="34" class="gauge__track" />
                <circle
                  cx="40" cy="40" r="34"
                  class="gauge__fill gauge__fill--mem"
                  :style="{ strokeDashoffset: memOffset }"
                />
              </svg>
              <span class="gauge__value">{{ memDisplay }}</span>
            </div>
            <span class="gauge-card__label">{{ $t('dashboard.totalMemoryUsage') }}</span>
          </div>

          <div class="metric-mini">
            <el-icon :size="20" class="metric-mini__icon"><Connection /></el-icon>
            <span class="metric-mini__value">{{ globalMetrics.total_threads }}</span>
            <span class="metric-mini__label">{{ $t('dashboard.totalThreads') }}</span>
          </div>

          <div class="metric-mini">
            <el-icon :size="20" class="metric-mini__icon"><Lightning /></el-icon>
            <span class="metric-mini__value">{{ globalMetrics.active_plugins }}</span>
            <span class="metric-mini__label">{{ $t('dashboard.activePlugins') }}</span>
          </div>
        </div>

        <div v-else class="panel__empty">
          <span>{{ $t('dashboard.noMetricsData') }}</span>
        </div>
      </div>

      <!-- Server info -->
      <div
        v-motion
        :initial="{ opacity: 0, y: 24, filter: 'blur(6px)' }"
        :enter="{ opacity: 1, y: 0, filter: 'blur(0px)', transition: { delay: 380, duration: 460, type: 'spring', stiffness: 220, damping: 22 } }"
        class="panel panel--server"
      >
        <div class="panel__header">
          <span class="panel__title">{{ $t('dashboard.serverInfo') }}</span>
        </div>

        <div v-if="serverInfoLoading" class="panel__loading">
          <el-icon class="spin"><Refresh /></el-icon>
          <span>{{ $t('common.loading') }}</span>
        </div>

        <div v-else-if="serverInfoError" class="panel__error">
          {{ $t('dashboard.failedToLoadServerInfo') }}
        </div>

        <div v-else-if="serverInfo" class="server-info">
          <div class="server-info__item">
            <span class="server-info__label">{{ $t('dashboard.sdkVersion') }}</span>
            <span class="server-info__badge">{{ serverInfo.sdk_version }}</span>
          </div>
          <div class="server-info__item">
            <span class="server-info__label">{{ $t('dashboard.totalPlugins') }}</span>
            <span class="server-info__value">{{ totalPlugins }}</span>
          </div>
          <div class="server-info__item">
            <span class="server-info__label">{{ $t('dashboard.updateTime') }}</span>
            <span class="server-info__value">{{ serverInfo.time ? formatTime(serverInfo.time) : '—' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import { getServerInfo } from '@/api/plugins'
import { PluginStatus, METRICS_REFRESH_INTERVAL } from '@/utils/constants'
import type { ServerInfo, GlobalMetrics } from '@/types/api'
import { Box, VideoPlay, CloseBold, WarningFilled, Connection, Lightning, Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'

const { t } = useI18n()
const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()

const serverInfo = ref<ServerInfo | null>(null)
const serverInfoLoading = ref(false)
const serverInfoError = ref(false)
const metricsLoading = ref(false)
const globalMetrics = ref<GlobalMetrics | null>(null)
let metricsTimer: number | null = null

// ── Computed stats ────────────────────────────────────────────────────

const totalPlugins = computed(() => pluginStore.plugins.length)
const runningCount = computed(() => pluginStore.pluginsWithStatus.filter((p) => p.status === PluginStatus.RUNNING).length)
const stoppedCount = computed(() => pluginStore.pluginsWithStatus.filter((p) => p.status === PluginStatus.STOPPED).length)
const crashedCount = computed(() => pluginStore.pluginsWithStatus.filter((p) => p.status === PluginStatus.CRASHED).length)

const statCards = computed(() => [
  { key: 'total', icon: Box, value: totalPlugins.value, label: t('dashboard.totalPlugins') },
  { key: 'running', icon: VideoPlay, value: runningCount.value, label: t('dashboard.running') },
  { key: 'stopped', icon: CloseBold, value: stoppedCount.value, label: t('dashboard.stopped') },
  { key: 'crashed', icon: WarningFilled, value: crashedCount.value, label: t('dashboard.crashed') },
])

// ── Gauge math ────────────────────────────────────────────────────────

const CIRCUMFERENCE = 2 * Math.PI * 34 // r=34

const cpuPercent = computed(() => Math.min(globalMetrics.value?.total_cpu_percent ?? 0, 100))
const memPercent = computed(() => Math.min(globalMetrics.value?.total_memory_percent ?? 0, 100))

const cpuOffset = computed(() => CIRCUMFERENCE - (cpuPercent.value / 100) * CIRCUMFERENCE)
const memOffset = computed(() => CIRCUMFERENCE - (memPercent.value / 100) * CIRCUMFERENCE)

const cpuDisplay = computed(() => cpuPercent.value.toFixed(1) + '%')
const memDisplay = computed(() => {
  const mb = globalMetrics.value?.total_memory_mb ?? 0
  if (mb >= 1024) return (mb / 1024).toFixed(1) + 'G'
  return mb.toFixed(0) + 'M'
})

// ── Helpers ───────────────────────────────────────────────────────────

function formatTime(time: string): string {
  return dayjs(time).format('YYYY-MM-DD HH:mm:ss')
}

// ── Data fetching ─────────────────────────────────────────────────────

async function fetchServerInfo() {
  serverInfoLoading.value = true
  serverInfoError.value = false
  try {
    const info = await getServerInfo()
    if (info && typeof info === 'object') {
      serverInfo.value = {
        sdk_version: info.sdk_version || 'Unknown',
        plugins_count: info.plugins_count ?? 0,
        time: info.time || new Date().toISOString(),
      }
    } else {
      throw new Error('Invalid response')
    }
  } catch {
    serverInfoError.value = true
  } finally {
    serverInfoLoading.value = false
  }
}

async function fetchGlobalMetrics() {
  metricsLoading.value = true
  try {
    const response = await metricsStore.fetchAllMetrics()
    if (response?.global) {
      globalMetrics.value = response.global
    }
  } catch {
    // silent
  } finally {
    metricsLoading.value = false
  }
}

async function handleRefreshMetrics() {
  await fetchGlobalMetrics()
}

function startAutoRefresh() {
  stopAutoRefresh()
  metricsTimer = window.setInterval(() => {
    fetchGlobalMetrics()
  }, METRICS_REFRESH_INTERVAL)
}

function stopAutoRefresh() {
  if (metricsTimer) {
    clearInterval(metricsTimer)
    metricsTimer = null
  }
}

onMounted(async () => {
  await Promise.all([
    pluginStore.fetchPlugins(),
    pluginStore.fetchPluginStatus(),
    fetchServerInfo(),
    fetchGlobalMetrics(),
  ])
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Stat cards row ── */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--el-bg-color) 82%, transparent);
  backdrop-filter: blur(16px) saturate(1.4);
  -webkit-backdrop-filter: blur(16px) saturate(1.4);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 24%, transparent),
    0 4px 16px color-mix(in srgb, var(--el-text-color-primary) 4%, transparent);
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 24%, transparent),
    0 8px 28px color-mix(in srgb, var(--el-text-color-primary) 8%, transparent);
}

.stat-card__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 12px;
  flex-shrink: 0;
  color: var(--el-color-primary);
}

.stat-card--running .stat-card__icon { color: var(--el-color-success); }
.stat-card--stopped .stat-card__icon { color: var(--el-color-info); }
.stat-card--crashed .stat-card__icon { color: var(--el-color-danger); }

.stat-card__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.stat-card__value {
  font-size: 26px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
  line-height: 1.1;
}

.stat-card__label {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
}

/* Color accents per stat */
.stat-card--running { border-left: 3px solid var(--el-color-success); }
.stat-card--stopped { border-left: 3px solid var(--el-color-info); }
.stat-card--crashed { border-left: 3px solid var(--el-color-danger); }
.stat-card--total { border-left: 3px solid var(--el-color-primary); }

/* ── Main grid ── */
.main-grid {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
  align-items: start;
}

/* ── Panel (shared) ── */
.panel {
  padding: 20px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--el-bg-color) 82%, transparent);
  backdrop-filter: blur(16px) saturate(1.4);
  -webkit-backdrop-filter: blur(16px) saturate(1.4);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 40%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 24%, transparent),
    0 4px 16px color-mix(in srgb, var(--el-text-color-primary) 4%, transparent);
}

.panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}

.panel__title {
  font-size: 15px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.panel__action {
  width: 30px;
  height: 30px;
  border: 1px solid color-mix(in srgb, var(--el-border-color) 50%, transparent);
  border-radius: 10px;
  background: transparent;
  color: var(--el-text-color-regular);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    color 0.2s ease,
    transform 0.18s ease;
}

.panel__action:hover {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
  border-color: color-mix(in srgb, var(--el-color-primary) 30%, var(--el-border-color));
  color: var(--el-color-primary);
}

.panel__action:disabled {
  opacity: 0.4;
  pointer-events: none;
}

.panel__empty {
  padding: 32px 0;
  text-align: center;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}

.panel__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.panel__error {
  padding: 20px;
  text-align: center;
  color: var(--el-color-warning);
  font-size: 13px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--el-color-warning) 6%, transparent);
}

/* ── Metrics grid ── */
.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

/* ── Gauge (ring chart) ── */
.gauge-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 16px 12px;
  border-radius: 14px;
  background: color-mix(in srgb, var(--el-fill-color-light) 50%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 25%, transparent);
}

.gauge-card__label {
  font-size: 12px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.gauge {
  position: relative;
  width: 80px;
  height: 80px;
}

.gauge__svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.gauge__track {
  fill: none;
  stroke: color-mix(in srgb, var(--el-border-color) 30%, transparent);
  stroke-width: 6;
}

.gauge__fill {
  fill: none;
  stroke-width: 6;
  stroke-linecap: round;
  stroke-dasharray: 213.63; /* 2 * PI * 34 */
  transition: stroke-dashoffset 0.8s cubic-bezier(0.22, 1, 0.36, 1);
}

.gauge__fill--cpu {
  stroke: var(--el-color-primary);
}

.gauge__fill--mem {
  stroke: var(--el-color-success);
}

.gauge__value {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

/* ── Metric mini cards ── */
.metric-mini {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 16px 12px;
  border-radius: 14px;
  background: color-mix(in srgb, var(--el-fill-color-light) 50%, transparent);
  border: 1px solid color-mix(in srgb, var(--el-border-color) 25%, transparent);
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease;
}

.metric-mini:hover {
  background: color-mix(in srgb, var(--el-color-primary) 5%, transparent);
  border-color: color-mix(in srgb, var(--el-color-primary) 16%, var(--el-border-color));
}

.metric-mini__icon {
  color: var(--el-text-color-secondary);
}

.metric-mini__value {
  font-size: 22px;
  font-weight: 750;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
  line-height: 1.1;
}

.metric-mini__label {
  font-size: 11px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
  text-align: center;
}

/* ── Server info ── */
.server-info {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.server-info__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 0;
  border-bottom: 1px solid color-mix(in srgb, var(--el-border-color) 25%, transparent);
}

.server-info__item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.server-info__item:first-child {
  padding-top: 0;
}

.server-info__label {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
}

.server-info__value {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-primary);
}

.server-info__badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 700;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}

/* ── Spin animation ── */
.spin {
  display: inline-block;
  animation: spin-rotate 0.8s linear infinite;
}

@keyframes spin-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* ── Responsive ── */
@media (max-width: 1100px) {
  .main-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }

  .metrics-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 480px) {
  .stats-row {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .gauge__fill {
    transition: none;
  }

  .spin {
    animation: none;
  }
}
</style>
