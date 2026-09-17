<template>
  <div class="logs-page" data-yui-guide-id="logs-page">
    <el-card data-yui-guide-id="logs-card">
      <template #header>
        <div class="card-header" data-yui-guide-id="logs-card-header">
          <span data-yui-guide-id="logs-title">{{ isServerLog ? $t('logs.serverLogs') : $t('logs.pluginLogs') + ': ' + pluginId }}</span>
          <el-button :icon="Refresh" data-yui-guide-id="logs-refresh" @click="handleRefresh" :loading="loading">
            {{ $t('common.refresh') }}
          </el-button>
        </div>
      </template>

      <div data-yui-guide-id="logs-viewer-wrap">
        <LogViewer :plugin-id="pluginId" />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useLogsStore } from '@/stores/logs'
import { PANEL_HOST_MIN_HEIGHT } from '@/utils/constants'
import LogViewer from '@/components/logs/LogViewer.vue'

const route = useRoute()
const logsStore = useLogsStore()

const pluginId = computed(() => (route.params.id as string) || '')
const isServerLog = computed(() => pluginId.value === '_server')
const loading = computed(() => logsStore.loading)

async function handleRefresh() {
  if (pluginId.value) {
    try {
      await logsStore.fetchLogs(pluginId.value)
    } catch (error) {
      ElMessage.error(String((error as any)?.message || error || 'Failed to fetch logs'))
    }
  }
}

onMounted(async () => {
  if (pluginId.value) {
    await handleRefresh()
  }
})
</script>

<style scoped>
.logs-page {
  padding: 0;
  /* 面板用 height:100% 填满容器：这里提供确定高度（理由见 utils/constants.ts） */
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: v-bind('PANEL_HOST_MIN_HEIGHT');
}

.logs-page :deep(.el-card) {
  flex: 1 1 0;
  min-height: 0;
}

.logs-page :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.logs-page [data-yui-guide-id='logs-viewer-wrap'] {
  flex: 1 1 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>

