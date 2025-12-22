<template>
  <div class="dashboard">
    <el-row :gutter="20">
      <!-- 插件概览 -->
      <el-col :span="24">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ $t('dashboard.pluginOverview') }}</span>
            </div>
          </template>
          <div class="stats">
            <el-statistic :title="$t('dashboard.totalPlugins')" :value="totalPlugins" />
            <el-statistic :title="$t('dashboard.running')" :value="runningPlugins" />
            <el-statistic :title="$t('dashboard.stopped')" :value="stoppedPlugins" />
            <el-statistic :title="$t('dashboard.crashed')" :value="crashedPlugins" />
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" style="margin-top: 20px">
      <!-- 全局性能监控 -->
      <el-col :span="16">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ $t('dashboard.globalMetrics') }}</span>
              <el-button :icon="Refresh" circle size="small" @click="handleRefreshMetrics" :loading="metricsLoading" />
            </div>
          </template>
          <div v-if="globalMetrics" class="global-metrics">
            <div class="metric-item">
              <div class="metric-label">{{ $t('dashboard.totalCpuUsage') }}</div>
              <div class="metric-value">
                <el-progress
                  :percentage="Math.min(globalMetrics?.total_cpu_percent || 0, 100)"
                  :color="getCpuColor(globalMetrics?.total_cpu_percent || 0)"
                  :format="() => `${(globalMetrics?.total_cpu_percent || 0).toFixed(1)}%`"
                />
              </div>
            </div>
            <div class="metric-item">
              <div class="metric-label">{{ $t('dashboard.totalMemoryUsage') }}</div>
              <div class="metric-value">
                <el-progress
                  :percentage="Math.min(globalMetrics?.total_memory_percent || 0, 100)"
                  :color="getMemoryColor(globalMetrics?.total_memory_percent || 0)"
                  :format="() => `${(globalMetrics?.total_memory_mb || 0).toFixed(1)} MB (${(globalMetrics?.total_memory_percent || 0).toFixed(1)}%)`"
                />
              </div>
            </div>
            <div class="metric-row">
              <div class="metric-item">
                <div class="metric-label">{{ $t('dashboard.totalThreads') }}</div>
                <div class="metric-value">
                  <el-statistic :value="globalMetrics?.total_threads || 0" />
                </div>
              </div>
              <div class="metric-item">
                <div class="metric-label">{{ $t('dashboard.activePlugins') }}</div>
                <div class="metric-value">
                  <el-statistic :value="globalMetrics?.active_plugins || 0" />
                </div>
              </div>
            </div>
          </div>
          <EmptyState v-else :description="$t('dashboard.noMetricsData')" />
        </el-card>
      </el-col>

      <!-- 服务器信息 -->
      <el-col :span="8">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ $t('dashboard.serverInfo') }}</span>
            </div>
          </template>
          <div v-if="serverInfoLoading" class="server-info-loading">
            <LoadingSpinner :loading="true" :text="$t('common.loading')" />
          </div>
          <div v-else-if="serverInfoError" class="server-info-error">
            <el-alert type="warning" :closable="false">
              <template #title>
                <span>{{ $t('dashboard.failedToLoadServerInfo') }}</span>
              </template>
            </el-alert>
          </div>
          <div v-else-if="serverInfo" class="server-info">
            <div class="info-item">
              <span class="info-label">{{ $t('dashboard.sdkVersion') }}</span>
              <el-tag v-if="serverInfo.sdk_version" type="info" size="large">
                {{ serverInfo.sdk_version }}
              </el-tag>
              <span v-else class="info-value">-</span>
            </div>
            <div class="info-item">
              <span class="info-label">{{ $t('dashboard.updateTime') }}</span>
              <span class="info-value">{{ serverInfo.time ? formatTime(serverInfo.time) : '-' }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { useMetricsStore } from '@/stores/metrics'
import { useAuthStore } from '@/stores/auth'
import { getServerInfo } from '@/api/plugins'
import { PluginStatus } from '@/utils/constants'
import type { ServerInfo, GlobalMetrics } from '@/types/api'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import dayjs from 'dayjs'

const pluginStore = usePluginStore()
const metricsStore = useMetricsStore()
const authStore = useAuthStore()

const serverInfo = ref<ServerInfo | null>(null)
const serverInfoLoading = ref(false)
const serverInfoError = ref(false)
const metricsLoading = ref(false)
const globalMetrics = ref<GlobalMetrics | null>(null)

const totalPlugins = computed(() => pluginStore.plugins.length)

const runningPlugins = computed(() => {
  return pluginStore.pluginsWithStatus.filter(
    p => p.status === PluginStatus.RUNNING
  ).length
})

const stoppedPlugins = computed(() => {
  return pluginStore.pluginsWithStatus.filter(
    p => p.status === PluginStatus.STOPPED
  ).length
})

const crashedPlugins = computed(() => {
  return pluginStore.pluginsWithStatus.filter(
    p => p.status === PluginStatus.CRASHED
  ).length
})

function getUsageColor(percent: number): string {
  if (percent < 50) return '#67c23a'
  if (percent < 80) return '#e6a23c'
  return '#f56c6c'
}

// 为兼容性保留别名
const getCpuColor = getUsageColor
const getMemoryColor = getUsageColor

function formatTime(time: string): string {
  return dayjs(time).format('YYYY-MM-DD HH:mm:ss')
}

async function fetchServerInfo() {
  // 如果未认证，不发送请求
  if (!authStore.isAuthenticated) {
    return
  }
  
  serverInfoLoading.value = true
  serverInfoError.value = false
  try {
    const info = await getServerInfo()
    console.log('Server info received:', info)
    if (info && typeof info === 'object') {
      serverInfo.value = {
        sdk_version: info.sdk_version || 'Unknown',
        plugins_count: info.plugins_count ?? 0,
        time: info.time || new Date().toISOString()
      }
      console.log('Server info set:', serverInfo.value)
    } else {
      console.warn('Invalid server info format:', info)
      throw new Error('Invalid server info response')
    }
  } catch (err: any) {
    // 如果是认证错误，不显示错误提示（会自动跳转登录）
    if (err.response?.status === 401 || err.response?.status === 403) {
      return
    }
    console.error('Failed to fetch server info:', err)
    serverInfoError.value = true
    // 保持 serverInfo 为 null，让模板显示错误提示
  } finally {
    serverInfoLoading.value = false
  }
}

async function fetchGlobalMetrics() {
  // 如果未认证，不发送请求
  if (!authStore.isAuthenticated) {
    return
  }
  
  metricsLoading.value = true
  try {
    const response = await metricsStore.fetchAllMetrics()
    // 从响应中提取全局指标（使用类型安全的访问）
    if (response?.global) {
      globalMetrics.value = response.global
    }
  } catch (err: any) {
    // 如果是认证错误，不显示错误提示（会自动跳转登录）
    if (err.response?.status === 401 || err.response?.status === 403) {
      return
    }
    console.error('Failed to fetch global metrics:', err)
  } finally {
    metricsLoading.value = false
  }
}

async function handleRefreshMetrics() {
  await fetchGlobalMetrics()
}

onMounted(async () => {
  await Promise.all([
    pluginStore.fetchPlugins(),
    pluginStore.fetchPluginStatus(),
    fetchServerInfo(),
    fetchGlobalMetrics()
  ])
})
</script>

<style scoped>
.dashboard {
  padding: 0;
}

.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.global-metrics {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.metric-row {
  display: flex;
  flex-direction: row;
  gap: 40px;
}

.metric-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
}

.metric-label {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  font-weight: 500;
}

.metric-value {
  flex: 1;
}

.server-info {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.info-item:last-child {
  border-bottom: none;
}

.info-label {
  font-size: 14px;
  color: var(--el-text-color-secondary);
  font-weight: 500;
}

.info-value {
  font-size: 14px;
  color: var(--el-text-color-primary);
}
</style>

