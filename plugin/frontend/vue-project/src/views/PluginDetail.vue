<template>
  <div class="plugin-detail">
    <!-- Loading 状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>{{ $t('common.loading') }}</span>
    </div>

    <el-card v-else-if="plugin">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <el-button :icon="ArrowLeft" @click="goBack">{{ $t('common.back') }}</el-button>
            <h2>{{ plugin.name }}</h2>
          </div>
          <PluginActions :plugin-id="pluginId" />
        </div>
      </template>

      <el-tabs v-model="activeTab">
        <el-tab-pane :label="$t('plugins.basicInfo')" name="info">
          <div class="info-section">
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
          <EntryList :entries="plugin.entries || []" :plugin-id="pluginId" :plugin-status="pluginStatus" />
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.performance')" name="metrics">
          <MetricsCard :plugin-id="pluginId" />
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.config')" name="config">
          <PluginConfigEditor :plugin-id="pluginId" />
        </el-tab-pane>

        <el-tab-pane :label="$t('plugins.logs')" name="logs">
          <LogViewer :plugin-id="pluginId" />
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <EmptyState v-else-if="!loading" :description="$t('plugins.pluginNotFound')" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
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

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()

const pluginId = computed(() => route.params.id as string)
const activeTab = ref('info')
const loading = ref(true)

const plugin = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === pluginId.value)
})

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

onMounted(async () => {
  try {
    await pluginStore.fetchPlugins()
    await pluginStore.fetchPluginStatus(pluginId.value)
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

