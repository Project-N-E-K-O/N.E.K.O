<template>
  <div class="adapter-ui">
    <!-- Loading 状态 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading" :size="32"><Loading /></el-icon>
      <span>{{ $t('common.loading') }}</span>
    </div>

    <!-- Error 状态 -->
    <el-alert v-else-if="loadError" type="error" :title="loadError" show-icon :closable="false" />

    <!-- 正常内容 -->
    <el-card v-else-if="adapter">
      <template #header>
        <div class="card-header">
          <div class="header-left">
            <el-button :icon="ArrowLeft" @click="goBack">{{ $t('common.back') }}</el-button>
            <h2>{{ adapter.name }}</h2>
            <el-tag type="warning" size="small">{{ $t('plugins.typeAdapter') }}</el-tag>
          </div>
          <div class="header-right">
            <StatusIndicator :status="adapter.status || 'stopped'" />
          </div>
        </div>
      </template>

      <div class="adapter-ui-container">
        <PluginUIFrame :plugin-id="adapterId" height="calc(100vh - 200px)" />
      </div>
    </el-card>

    <EmptyState v-else :description="$t('plugins.adapterNotFound')" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Loading } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import PluginUIFrame from '@/components/plugin/PluginUIFrame.vue'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import EmptyState from '@/components/common/EmptyState.vue'

const route = useRoute()
const router = useRouter()
const pluginStore = usePluginStore()
const { t } = useI18n()

const loading = ref(false)
const loadError = ref<string | null>(null)

const adapterId = computed(() => route.params.id as string)

const adapter = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === adapterId.value)
})

function goBack() {
  router.push('/plugins')
}

onMounted(async () => {
  if (pluginStore.pluginsWithStatus.length === 0) {
    loading.value = true
    loadError.value = null
    try {
      await pluginStore.fetchPlugins()
    } catch (e: any) {
      loadError.value = e?.message || t('plugins.loadFailed')
    } finally {
      loading.value = false
    }
  }
})
</script>

<style scoped>
.adapter-ui {
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

.header-left h2 {
  margin: 0;
  font-size: 20px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.adapter-ui-container {
  min-height: 500px;
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
</style>
