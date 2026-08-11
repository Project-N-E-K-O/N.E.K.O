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
            <h2>{{ pluginDisplayText.name }}</h2>
          </div>
          <div data-yui-guide-id="plugin-detail-actions">
            <PluginActions :plugin-id="pluginId" />
          </div>
        </div>
      </template>

      <el-tabs v-model="activeTab" data-yui-guide-id="plugin-detail-tabs">
        <el-tab-pane v-if="displayedPanelSurfaces.length > 0" :label="$t('plugins.ui.panel')" name="panel">
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
            <el-tabs v-if="displayedPanelSurfaces.length > 1" v-model="activePanelSurfaceId" type="border-card">
              <el-tab-pane
                v-for="surface in displayedPanelSurfaces"
                :key="surface.id"
                :label="surface.title || surface.id"
                :name="surface.id"
              >
                <HostedSurfaceFrame :plugin-id="pluginId" :surface="surface" :height="hostedSurfaceFrameHeight" @open-logs="openLogsTab" @message="relayHostedSurfaceMessageToStaticUi" />
              </el-tab-pane>
            </el-tabs>
            <HostedSurfaceFrame v-else :plugin-id="pluginId" :surface="displayedPanelSurfaces[0]!" :height="hostedSurfaceFrameHeight" @open-logs="openLogsTab" @message="relayHostedSurfaceMessageToStaticUi" />
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
                <HostedSurfaceFrame :plugin-id="pluginId" :surface="surface" :height="hostedSurfaceFrameHeight" @open-logs="openLogsTab" @message="relayHostedSurfaceMessageToStaticUi" />
              </el-tab-pane>
            </el-tabs>
            <HostedSurfaceFrame v-else :plugin-id="pluginId" :surface="guideSurfaces[0]!" :height="hostedSurfaceFrameHeight" @open-logs="openLogsTab" @message="relayHostedSurfaceMessageToStaticUi" />
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="showLegacyStaticUi" :label="$t('plugins.ui.title')" name="ui">
          <PluginUIFrame ref="staticUiFrameRef" :plugin-id="pluginId" height="560px" @open-surface="openHostedSurfaceFromStaticUi" />
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.basicInfo')" name="info">
          <div class="info-section" data-yui-guide-id="plugin-detail-info">
            <el-descriptions :column="2" border>
              <el-descriptions-item :label="$t('plugins.id')">{{ plugin.id }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.version')">{{ plugin.version }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.description')" :span="2">{{ pluginDisplayText.description || $t('common.noData') }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.pluginType')">
                <el-tag size="small" :type="pluginTypeTagType">
                  {{ $t(pluginTypeText) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.sdkVersion')">{{ plugin.sdk_version || $t('common.nA') }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.autoStart')">
                <el-tag size="small" :type="plugin.autoStart ? 'success' : 'warning'">
                  {{ plugin.autoStart ? $t('plugins.autoStart') : $t('plugins.manualStart') }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.status')">
                <StatusIndicator :status="pluginStatus" />
              </el-descriptions-item>
            </el-descriptions>

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
      <div v-if="needsLegacyStaticUiRelay" class="static-ui-relay" aria-hidden="true">
        <PluginUIFrame ref="staticUiFrameRef" :plugin-id="pluginId" height="560px" @open-surface="openHostedSurfaceFromStaticUi" />
      </div>
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
import PluginUIFrame from '@/components/plugin/PluginUIFrame.vue'
import { getPluginUiSurfaceInfo } from '@/api/plugins'
import { get } from '@/api'
import { resolvePluginDisplayText, type PluginDisplayText } from '@/utils/pluginDisplay'
import { useI18n } from 'vue-i18n'
import type { PluginUiSurface, PluginUiWarning } from '@/types/api'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const { locale } = useI18n()

const pluginId = computed(() => route.params.id as string)
const activeTab = ref('info')
const loading = ref(true)
const surfaces = ref<PluginUiSurface[]>([])
const surfaceWarnings = ref<PluginUiWarning[]>([])
const activePanelSurfaceId = ref('')
const activeGuideSurfaceId = ref('')
const staticUiFrameRef = ref<InstanceType<typeof PluginUIFrame> | null>(null)
const hostedSurfaceFrameHeight = 'clamp(560px, calc(100vh - 220px), 1200px)'
const allowedTabs = new Set(['panel', 'guide', 'ui', 'info', 'entries', 'metrics', 'config', 'logs'])
let currentSurfaceLoadId = 0
// fetchStaticUI 也需要和 fetchSurfaces 一样的 stale-response guard：用户快速
// 切换 plugin detail 页时，旧 plugin 的 /ui-info 响应可能在新 plugin 加载后
// 才到达，覆盖 hasStaticUI 导致 UI tab 显示状态错位。
let currentStaticUiLoadId = 0
const hasStaticUI = ref(false)
// Keep a confirmed legacy UI relay mounted while this same plugin's surfaces
// are refreshed (for example after a locale change), but never reuse it for a
// different plugin while its /ui-info probe is still in flight.
const staticUiPluginId = ref('')

const plugin = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === pluginId.value)
})

const emptyPluginDisplayText: PluginDisplayText = {
  name: '',
  description: '',
  shortDescription: '',
}

const pluginDisplayText = computed(() => {
  return plugin.value ? resolvePluginDisplayText(plugin.value, locale.value) : emptyPluginDisplayText
})

const panelSurfaces = computed(() => surfaces.value.filter((surface) => surface.kind === 'panel'))
const guideSurfaces = computed(() => surfaces.value.filter((surface) => surface.kind === 'guide' || surface.kind === 'docs'))
const availablePanelSurfaces = computed(() => panelSurfaces.value.filter((surface) => surface.available !== false))
// `auto` is accepted by the manifest but does not have a renderer yet. Do not
// let its placeholder hide a working legacy static UI.
const renderablePanelSurfaces = computed(() => availablePanelSurfaces.value.filter((surface) => surface.mode !== 'auto'))
const availableDeclaredPanelSurfaces = computed(() => renderablePanelSurfaces.value.filter((surface) => !surface.legacy_static_compat))
const availableHostedPanelSurfaces = computed(() => availableDeclaredPanelSurfaces.value.filter((surface) => surface.mode === 'hosted-tsx'))
// Prefer usable hosted TSX panels without hiding other declared panels. Only
// fall back to a host-generated compatibility panel when the plugin did not
// declare any usable panel of its own.
const displayedPanelSurfaces = computed(() => {
  if (availableDeclaredPanelSurfaces.value.length > 0) {
    return [
      ...availableHostedPanelSurfaces.value,
      ...availableDeclaredPanelSurfaces.value.filter((surface) => surface.mode !== 'hosted-tsx'),
    ]
  }
  return renderablePanelSurfaces.value
})
const hasStaticCompatPanel = computed(() => panelSurfaces.value.some((surface) => surface.legacy_static_compat))
const hasDisplayablePanelSurface = computed(() => displayedPanelSurfaces.value.length > 0)
const hasCurrentStaticUI = computed(() => hasStaticUI.value && staticUiPluginId.value === pluginId.value)
// Preserve the legacy iframe only as a hidden message receiver when it is an
// automatically injected compatibility surface alongside a newer declared UI.
const needsLegacyStaticUiRelay = computed(() => hasCurrentStaticUI.value && hasStaticCompatPanel.value && availableDeclaredPanelSurfaces.value.length > 0)
const showLegacyStaticUi = computed(() => hasCurrentStaticUI.value && !hasDisplayablePanelSurface.value)

const isAdapter = computed(() => plugin.value?.type === 'adapter')

// 获取插件类型显示文本
const pluginTypeText = computed(() => {
  if (isAdapter.value) return 'plugins.typeAdapter'
  return 'plugins.pluginTypeNormal'
})

// 获取插件类型标签颜色
const pluginTypeTagType = computed(() => {
  if (isAdapter.value) return 'warning'
  return 'info'
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

function resolveActiveTab(value: unknown): string {
  return typeof value === 'string' && allowedTabs.has(value) ? value : 'info'
}

function resolveDefaultTab(value: unknown): string {
  const requested = resolveActiveTab(value)
  if (requested === 'panel' && !hasDisplayablePanelSurface.value) return 'info'
  if (requested === 'guide' && guideSurfaces.value.length === 0) return 'info'
  if (requested === 'ui' && hasDisplayablePanelSurface.value) return 'panel'
  if (requested === 'ui' && !showLegacyStaticUi.value) return 'info'
  return requested
}

function syncActiveTab(requestedTab: unknown) {
  const nextTab = resolveDefaultTab(requestedTab)
  activeTab.value = nextTab
  if (requestedTab === 'ui' && nextTab !== 'ui') {
    void router.replace({
      query: {
        ...route.query,
        tab: nextTab,
      },
    })
  }
}

function syncSurfaceTabs() {
  const requestedSurfaceId = typeof route.query.surface === 'string' ? route.query.surface : ''
  const requestedTab = resolveActiveTab(route.query.tab)
  if (requestedSurfaceId) {
    const panel = requestedTab !== 'guide'
      ? displayedPanelSurfaces.value.find((surface) => surface.id === requestedSurfaceId)
      : undefined
    if (panel) {
      activePanelSurfaceId.value = panel.id
    }
    const guide = requestedTab !== 'panel'
      ? guideSurfaces.value.find((surface) => surface.id === requestedSurfaceId)
      : undefined
    if (guide) {
      activeGuideSurfaceId.value = guide.id
    }
  }
  if (!activePanelSurfaceId.value && displayedPanelSurfaces.value[0]) {
    activePanelSurfaceId.value = displayedPanelSurfaces.value[0].id
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

function openHostedSurfaceFromStaticUi(payload: { pluginId?: string; surfaceId: string; kind?: string }) {
  if (payload.pluginId && payload.pluginId !== pluginId.value) return
  let activeSurfaceId = ''
  const preferPanel = payload.kind === 'panel'
  const preferGuide = payload.kind === 'guide' || payload.kind === 'docs'
  const panel = (preferPanel || !preferGuide)
    ? displayedPanelSurfaces.value.find((surface) => surface.id === payload.surfaceId)
    : undefined
  if (panel) {
    activePanelSurfaceId.value = panel.id
    activeSurfaceId = panel.id
    activeTab.value = 'panel'
  } else {
    const guide = (preferGuide || !preferPanel)
      ? guideSurfaces.value.find((surface) => surface.id === payload.surfaceId)
      : undefined
    if (!guide) return
    activeGuideSurfaceId.value = guide.id
    activeSurfaceId = guide.id
    activeTab.value = 'guide'
  }
  router.replace({
    query: {
      ...route.query,
      tab: activeTab.value,
      surface: activeSurfaceId,
    },
  })
}

function isLegacyOpenSurfaceMessage(data: unknown): data is {
  type: 'neko-study-open-surface'
  payload: { pluginId?: string; surfaceId: string; kind?: string }
} {
  if (!data || typeof data !== 'object') return false
  const message = data as { type?: unknown; payload?: unknown }
  if (message.type !== 'neko-study-open-surface' || !message.payload || typeof message.payload !== 'object') return false
  const payload = message.payload as { pluginId?: unknown; surfaceId?: unknown; kind?: unknown }
  return typeof payload.surfaceId === 'string'
    && (!payload.pluginId || typeof payload.pluginId === 'string')
    && (!payload.kind || typeof payload.kind === 'string')
}

function relayHostedSurfaceMessageToStaticUi(data: unknown) {
  if (isLegacyOpenSurfaceMessage(data)) {
    openHostedSurfaceFromStaticUi(data.payload)
    return
  }
  // Hosted surface messages have already been source/origin checked by the
  // frame. Forward them unchanged so legacy static UIs from any plugin can
  // opt into their own message contract without host-side plugin allowlists.
  staticUiFrameRef.value?.sendSurfaceMessage(data)
}

async function fetchSurfaces(): Promise<boolean> {
  const loadId = ++currentSurfaceLoadId
  const currentPluginId = pluginId.value
  try {
    const info = await getPluginUiSurfaceInfo(currentPluginId, locale.value)
    if (loadId !== currentSurfaceLoadId || currentPluginId !== pluginId.value) return false
    surfaces.value = info.surfaces
    surfaceWarnings.value = info.warnings
    if (hasDisplayablePanelSurface.value) {
      // Prefer the declared panel and invalidate a possible in-flight legacy
      // static-UI probe from the previous request. Keep an already-confirmed
      // relay mounted for this plugin until the replacement probe completes.
      currentStaticUiLoadId += 1
    }
  } catch (caught: any) {
    if (loadId !== currentSurfaceLoadId || currentPluginId !== pluginId.value) return false
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
  return true
}

async function fetchStaticUI(): Promise<boolean> {
  // The legacy /ui-info route serves static/index.html.  A modern panel may
  // intentionally point at that same file, so probing it would only create a
  // duplicate "界面" tab.
  if (hasDisplayablePanelSurface.value && !hasStaticCompatPanel.value) {
    hasStaticUI.value = false
    staticUiPluginId.value = pluginId.value
    return true
  }
  const loadId = ++currentStaticUiLoadId
  const currentPluginId = pluginId.value
  try {
    const info = await get<{ has_ui: boolean }>(`/plugin/${encodeURIComponent(currentPluginId)}/ui-info`)
    if (loadId !== currentStaticUiLoadId || currentPluginId !== pluginId.value) return false
    hasStaticUI.value = info?.has_ui ?? false
    staticUiPluginId.value = currentPluginId
    return true
  } catch {
    if (loadId !== currentStaticUiLoadId || currentPluginId !== pluginId.value) return false
    hasStaticUI.value = false
    staticUiPluginId.value = currentPluginId
    return true
  }
}

async function refreshPluginUi(): Promise<boolean> {
  const surfacesApplied = await fetchSurfaces()
  if (!surfacesApplied) return false
  return fetchStaticUI()
}

onMounted(async () => {
  try {
    await pluginStore.fetchPlugins()
    await pluginStore.fetchPluginStatus(pluginId.value)
    if (await refreshPluginUi()) syncActiveTab(route.query.tab)
    pluginStore.setSelectedPlugin(pluginId.value)
  } finally {
    loading.value = false
  }
})

watch(
  () => [route.query.tab, route.query.surface],
  ([tab]) => {
    syncSurfaceTabs()
    syncActiveTab(tab)
  },
)

watch(pluginId, async () => {
  loading.value = true
  try {
    await pluginStore.fetchPluginStatus(pluginId.value)
    if (await refreshPluginUi()) syncActiveTab(route.query.tab)
    pluginStore.setSelectedPlugin(pluginId.value)
  } finally {
    loading.value = false
  }
})

watch(locale, () => {
  if (!plugin.value) return
  void refreshPluginUi().then((refreshed) => {
    if (refreshed) syncActiveTab(route.query.tab)
  })
})
</script>

<style scoped>
.plugin-detail {
  padding: 0;
}

.static-ui-relay {
  display: none;
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

</style>
