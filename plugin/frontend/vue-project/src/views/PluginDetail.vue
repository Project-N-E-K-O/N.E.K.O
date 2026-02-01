<template>
  <div class="plugin-detail">
    <el-card v-if="plugin">
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
              <el-descriptions-item :label="$t('plugins.sdkVersion')">{{ plugin.sdk_version || $t('common.nA') }}</el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.enabled')">
                <el-tag size="small" :type="plugin.enabled ? 'success' : 'info'">
                  {{ plugin.enabled ? $t('plugins.enabled') : $t('plugins.disabled') }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item :label="$t('plugins.autoStart')">
                <el-tag size="small" :type="plugin.autoStart ? 'success' : 'warning'" :class="{ 'is-disabled': !plugin.enabled }">
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

    <EmptyState v-else :description="$t('plugins.pluginNotFound')" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft } from '@element-plus/icons-vue'
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

const plugin = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === pluginId.value)
})

// 确保 status 始终是字符串类型
const pluginStatus = computed(() => {
  if (!plugin.value) return 'stopped'
  const status = plugin.value.status
  // 如果 status 是对象，尝试提取字符串值
  if (typeof status === 'object' && status !== null) {
    return (status as any).status || 'stopped'
  }
  // 确保返回字符串
  return typeof status === 'string' ? status : 'stopped'
})

function goBack() {
  router.push('/plugins')
}

onMounted(async () => {
  await pluginStore.fetchPlugins()
  await pluginStore.fetchPluginStatus(pluginId.value)
  pluginStore.setSelectedPlugin(pluginId.value)
})
</script>

<style scoped>
.plugin-detail {
  padding: 0;
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
</style>

