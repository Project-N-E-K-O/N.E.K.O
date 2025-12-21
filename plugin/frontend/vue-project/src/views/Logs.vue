<template>
  <div class="logs-page">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>{{ isServerLog ? $t('logs.serverLogs') : $t('logs.pluginLogs') + ': ' + pluginId }}</span>
          <el-button :icon="Refresh" @click="handleRefresh" :loading="loading">
            {{ $t('common.refresh') }}
          </el-button>
        </div>
      </template>

      <LogViewer :plugin-id="pluginId" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { useLogsStore } from '@/stores/logs'
import LogViewer from '@/components/logs/LogViewer.vue'

const route = useRoute()
const logsStore = useLogsStore()

const pluginId = computed(() => (route.params.id as string) || '')
const isServerLog = computed(() => pluginId.value === '_server')
const loading = computed(() => logsStore.loading)

async function handleRefresh() {
  if (pluginId.value) {
    await logsStore.fetchLogs(pluginId.value)
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
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>

