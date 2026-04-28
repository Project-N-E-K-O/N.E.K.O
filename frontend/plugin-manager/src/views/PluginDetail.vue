<template>
  <div class="plugin-detail" data-yui-guide-id="plugin-detail-page">
    <!-- Loading 状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>{{ $t('common.loading') }}</span>
    </div>

    <el-card v-else-if="plugin" data-yui-guide-id="plugin-detail-card">
      <template #header>
        <div class="card-header" data-yui-guide-id="plugin-detail-header">
          <div class="header-left" data-yui-guide-id="plugin-detail-title">
            <el-button :icon="ArrowLeft" data-yui-guide-id="plugin-detail-back" @click="goBack">{{ $t('common.back') }}</el-button>
            <h2>{{ plugin.name }}</h2>
          </div>
          <div data-yui-guide-id="plugin-detail-actions">
            <PluginActions :plugin-id="pluginId" />
          </div>
        </div>
      </template>

      <el-tabs v-model="activeTab" data-yui-guide-id="plugin-detail-tabs">
        <el-tab-pane v-if="panelSurfaces.length > 0" :label="$t('plugins.ui.panel')" name="panel">
          <div class="surface-section" data-yui-guide-id="plugin-detail-panel">
            <el-alert
              v-if="surfaceWarnings.length > 0"
              class="surface-warning"
              type="warning"
              show-icon
              :closable="false"
            >
              <template #title>{{ $t('plugins.ui.surfaceWarnings') }}</template>
              <ul class="surface-warning__list">
                <li v-for="warning in surfaceWarnings" :key="`${warning.path}:${warning.code}:${warning.message}`">
                  <code>{{ warning.path }}</code>
                  <span>{{ warning.message }}</span>
                </li>
              </ul>
            </el-alert>
            <el-tabs v-if="panelSurfaces.length > 1" v-model="activePanelSurfaceId" type="border-card">
              <el-tab-pane
                v-for="surface in panelSurfaces"
                :key="surface.id"
                :label="surface.title || surface.id"
                :name="surface.id"
              >
                <HostedSurfaceFrame :plugin-id="pluginId" :surface="surface" height="560px" @open-logs="openLogsTab" />
              </el-tab-pane>
            </el-tabs>
            <HostedSurfaceFrame v-else :plugin-id="pluginId" :surface="panelSurfaces[0]!" height="560px" @open-logs="openLogsTab" />
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="guideSurfaces.length > 0" :label="$t('plugins.ui.guide')" name="guide">
          <div class="surface-section" data-yui-guide-id="plugin-detail-guide">
            <el-alert
              v-if="surfaceWarnings.length > 0"
              class="surface-warning"
              type="warning"
              show-icon
              :closable="false"
            >
              <template #title>{{ $t('plugins.ui.surfaceWarnings') }}</template>
              <ul class="surface-warning__list">
                <li v-for="warning in surfaceWarnings" :key="`${warning.path}:${warning.code}:${warning.message}`">
                  <code>{{ warning.path }}</code>
                  <span>{{ warning.message }}</span>
                </li>
              </ul>
            </el-alert>
            <el-tabs v-if="guideSurfaces.length > 1" v-model="activeGuideSurfaceId" type="border-card">
              <el-tab-pane
                v-for="surface in guideSurfaces"
                :key="surface.id"
                :label="surface.title || surface.id"
                :name="surface.id"
              >
                <HostedSurfaceFrame :plugin-id="pluginId" :surface="surface" height="560px" @open-logs="openLogsTab" />
              </el-tab-pane>
            </el-tabs>
            <HostedSurfaceFrame v-else :plugin-id="pluginId" :surface="guideSurfaces[0]!" height="560px" @open-logs="openLogsTab" />
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.basicInfo')" name="info">
          <div class="info-section" data-yui-guide-id="plugin-detail-info">
            <el-descriptions :column="2" border>
              <el-descriptions-item :label="$t('plugins.id')">{{ plugin.id }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.version')">{{ plugin.version }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.description')" :span="2">{{ plugin.description || $t('common.noData') }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.pluginType')">
                <el-tag size="small" :type="pluginTypeTagType">
                  {{ $t(pluginTypeText) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.sdkVersion')">{{ plugin.sdk_version || $t('common.nA') }}</el-descriptions-item>
              <el-descriptions-item v-if="isExtension" :label="$t('plugins.hostPlugin')">
                <el-link type="primary" @click="goToPlugin(plugin.host_plugin_id!)">
                  {{ plugin.host_plugin_id }}
                </el-link>
              </el-descriptions-item>
              <el-descriptions-item v-if="!isExtension" :label="$t('plugins.enabled')">
                <el-tag size="small" :type="plugin.enabled ? 'success' : 'info'">
                  {{ plugin.enabled ? $t('plugins.enabled') : $t('plugins.disabled') }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item v-if="!isExtension" :label="$t('plugins.autoStart')">
                <el-tag size="small" :type="plugin.autoStart ? 'success' : 'warning'" :class="{ 'is-disabled': !plugin.enabled }">
                  {{ plugin.autoStart ? $t('plugins.autoStart') : $t('plugins.manualStart') }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.status')">
                <StatusIndicator :status="pluginStatus" />
              </el-descriptions-item>
            </el-descriptions>

            <!-- 普通插件：显示绑定的 Extension 列表 -->
            <div v-if="!isExtension && boundExtensions.length > 0" class="bound-extensions">
              <h4 class="bound-extensions-title">{{ $t('plugins.boundExtensions') }} ({{ boundExtensions.length }})</h4>
              <div class="bound-extensions-list">
                <el-card
                  v-for="ext in boundExtensions"
                  :key="ext.id"
                  shadow="hover"
                  class="bound-ext-card"
                  @click="goToPlugin(ext.id)"
                >
                  <div class="bound-ext-info">
                    <span class="bound-ext-name">{{ ext.name }}</span>
                    <StatusIndicator :status="ext.status || 'pending'" />
                  </div>
                  <p class="bound-ext-desc">{{ ext.description || $t('common.noData') }}</p>
                </el-card>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.entries')" name="entries">
          <div data-yui-guide-id="plugin-detail-entries">
            <EntryList :entries="plugin.entries || []" :plugin-id="pluginId" :plugin-status="pluginStatus" />
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.performance')" name="metrics">
          <div data-yui-guide-id="plugin-detail-metrics">
            <MetricsCard :plugin-id="pluginId" />
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.config')" name="config">
          <div data-yui-guide-id="plugin-detail-config">
            <PluginConfigEditor :plugin-id="pluginId" />
          </div>
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.logs')" name="logs">
          <div data-yui-guide-id="plugin-detail-logs">
            <LogViewer :plugin-id="pluginId" />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <EmptyState v-else-if="!loading" :description="$t('plugins.pluginNotFound')" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Loading } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import PluginActions from '@/components/plugin/PluginActions.vue'
import EntryList from '@/components/plugin/EntryList.vue'
import MetricsCard from '@/components/metrics/MetricsCard.vue'
import PluginConfigEditor from '@/components/plugin/PluginConfigEditor.vue'
import LogViewer from '@/components/logs/LogViewer.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import HostedSurfaceFrame from '@/components/plugin/HostedSurfaceFrame.vue'
import { getPluginUiSurfaceInfo } from '@/api/plugins'
import type { PluginUiSurface, PluginUiWarning } from '@/types/api'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()

const pluginId = computed(() => route.params.id as string)
const activeTab = ref('info')
const loading = ref(true)
const surfaces = ref<PluginUiSurface[]>([])
const surfaceWarnings = ref<PluginUiWarning[]>([])
const activePanelSurfaceId = ref('')
const activeGuideSurfaceId = ref('')
const allowedTabs = new Set(['panel', 'guide', 'info', 'entries', 'metrics', 'config', 'logs'])
let currentSurfaceLoadId = 0

const plugin = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === pluginId.value)
})

const panelSurfaces = computed(() => surfaces.value.filter((surface) => surface.kind === 'panel'))
const guideSurfaces = computed(() => surfaces.value.filter((surface) => surface.kind === 'guide' || surface.kind === 'docs'))

const isExtension = computed(() => plugin.value?.type === 'extension')
const isAdapter = computed(() => plugin.value?.type === 'adapter')

// 获取插件类型显示文本
const pluginTypeText = computed(() => {
  if (isExtension.value) return 'plugins.extension'
  if (isAdapter.value) return 'plugins.typeAdapter'
  return 'plugins.pluginTypeNormal'
})

// 获取插件类型标签颜色
const pluginTypeTagType = computed(() => {
  if (isExtension.value) return 'primary'
  if (isAdapter.value) return 'warning'
  return 'info'
})

const boundExtensions = computed(() => {
  if (!plugin.value || isExtension.value) return []
  return pluginStore.getExtensionsForHost(pluginId.value)
})

// 确保 status 始终是字符串类型
const pluginStatus = computed(() => {
  if (!plugin.value) return 'stopped'
  const status = plugin.value.status
  if (typeof status === 'object' && status !== null) {
    return (status as any).status || 'stopped'
  }
  return typeof status === 'string' ? status : 'stopped'
})

function goBack() {
  router.push('/plugins')
}

function goToPlugin(pid: string) {
  router.push(`/plugins/${encodeURIComponent(pid)}`)
}

function resolveActiveTab(value: unknown): string {
  return typeof value === 'string' && allowedTabs.has(value) ? value : 'info'
}

function resolveDefaultTab(value: unknown): string {
  const requested = resolveActiveTab(value)
  if (requested === 'panel' && panelSurfaces.value.length === 0) return 'info'
  if (requested === 'guide' && guideSurfaces.value.length === 0) return 'info'
  return requested
}

function syncSurfaceTabs() {
  if (!activePanelSurfaceId.value && panelSurfaces.value[0]) {
    activePanelSurfaceId.value = panelSurfaces.value[0].id
  }
  if (!activeGuideSurfaceId.value && guideSurfaces.value[0]) {
    activeGuideSurfaceId.value = guideSurfaces.value[0].id
  }
}

function openLogsTab() {
  activeTab.value = 'logs'
  router.replace({
    query: {
      ...route.query,
      tab: 'logs',
    },
  })
}

async function fetchSurfaces() {
  const loadId = ++currentSurfaceLoadId
  const currentPluginId = pluginId.value
  try {
    const info = await getPluginUiSurfaceInfo(currentPluginId)
    if (loadId !== currentSurfaceLoadId || currentPluginId !== pluginId.value) return
    surfaces.value = info.surfaces
    surfaceWarnings.value = info.warnings
  } catch (caught: any) {
    if (loadId !== currentSurfaceLoadId || currentPluginId !== pluginId.value) return
    surfaces.value = []
    surfaceWarnings.value = [{
      path: 'plugin.ui',
      code: 'surface_query_failed',
      message: caught?.response?.data?.detail || caught?.message || String(caught),
    }]
  }
  activePanelSurfaceId.value = ''
  activeGuideSurfaceId.value = ''
  syncSurfaceTabs()
}

onMounted(async () => {
  try {
    await pluginStore.fetchPlugins()
    await pluginStore.fetchPluginStatus(pluginId.value)
    await fetchSurfaces()
    activeTab.value = resolveDefaultTab(route.query.tab)
    pluginStore.setSelectedPlugin(pluginId.value)
  } finally {
    loading.value = false
  }
})

watch(
  () => route.query.tab,
  (tab) => {
    activeTab.value = resolveDefaultTab(tab)
  },
)

watch(pluginId, async () => {
  loading.value = true
  try {
    await pluginStore.fetchPluginStatus(pluginId.value)
    await fetchSurfaces()
    activeTab.value = resolveDefaultTab(route.query.tab)
    pluginStore.setSelectedPlugin(pluginId.value)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.plugin-detail {
  padding: 0;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  gap: 12px;
  color: var(--el-text-color-secondary);
}

.loading-container .el-icon {
  color: var(--el-color-primary);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.header-left h2 {
  margin: 0;
  font-size: 20px;
}

.info-section {
  padding: 20px 0;
}

.surface-section {
  padding: 16px 0;
}

.surface-warning {
  margin-bottom: 14px;
}

.surface-warning__list {
  margin: 6px 0 0;
  padding-left: 18px;
}

.surface-warning__list li {
  line-height: 1.7;
}

.surface-warning__list code {
  margin-right: 8px;
  color: var(--el-color-warning);
}

.bound-extensions {
  margin-top: 24px;
}

.bound-extensions-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 12px 0;
  color: var(--el-text-color-primary);
}

.bound-extensions-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.bound-ext-card {
  cursor: pointer;
  transition: all 0.2s;
}

.bound-ext-card:hover {
  border-color: var(--el-color-primary);
}

.bound-ext-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.bound-ext-name {
  font-weight: 600;
  font-size: 14px;
}

.bound-ext-desc {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
