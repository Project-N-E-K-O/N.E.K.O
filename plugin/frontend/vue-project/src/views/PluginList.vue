<template>
  <div class="plugin-list">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>{{ $t('plugins.title') }}</span>
          <el-button type="primary" :icon="Refresh" @click="handleRefresh" :loading="loading">
            {{ $t('common.refresh') }}
          </el-button>
        </div>
      </template>

      <LoadingSpinner v-if="loading && plugins.length === 0" :loading="true" :text="$t('common.loading')" />
      <EmptyState v-else-if="plugins.length === 0" :description="$t('plugins.noPlugins')" />
      
      <div v-else class="plugin-grid">
        <PluginCard
          v-for="plugin in plugins"
          :key="plugin.id"
          :plugin="plugin"
          @click="handlePluginClick(plugin.id)"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import PluginCard from '@/components/plugin/PluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const router = useRouter()
const pluginStore = usePluginStore()

const plugins = computed(() => pluginStore.pluginsWithStatus)
const loading = computed(() => pluginStore.loading)

async function handleRefresh() {
  await pluginStore.fetchPlugins()
  await pluginStore.fetchPluginStatus()
}

function handlePluginClick(pluginId: string) {
  router.push(`/plugins/${pluginId}`)
}

onMounted(async () => {
  await handleRefresh()
})
</script>

<style scoped>
.plugin-list {
  padding: 0;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.plugin-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
</style>

