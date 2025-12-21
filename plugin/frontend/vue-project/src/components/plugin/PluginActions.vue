<template>
  <div class="plugin-actions">
    <el-button-group>
      <el-button
        v-if="status !== 'running'"
        type="success"
        :icon="VideoPlay"
        @click="handleStart"
        :loading="loading"
      >
        {{ t('plugins.start') }}
      </el-button>
      <el-button
        v-if="status === 'running'"
        type="warning"
        :icon="VideoPause"
        @click="handleStop"
        :loading="loading"
      >
        {{ t('plugins.stop') }}
      </el-button>
      <el-button
        type="primary"
        :icon="Refresh"
        @click="handleReload"
        :loading="loading"
      >
        {{ t('plugins.reload') }}
      </el-button>
    </el-button-group>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { VideoPlay, VideoPause, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const pluginStore = usePluginStore()
const { t } = useI18n()

const loading = ref(false)

const status = computed(() => {
  const plugin = pluginStore.pluginsWithStatus.find(p => p.id === props.pluginId)
  return plugin?.status || 'stopped'
})

async function handleStart() {
  try {
    loading.value = true
    await pluginStore.start(props.pluginId)
    ElMessage.success(t('messages.pluginStarted'))
  } catch (error: any) {
    ElMessage.error(error.message || t('messages.startFailed'))
  } finally {
    loading.value = false
  }
}

async function handleStop() {
  try {
    await ElMessageBox.confirm(t('messages.confirmStop'), t('common.confirm'), {
      type: 'warning'
    })
    loading.value = true
    await pluginStore.stop(props.pluginId)
    ElMessage.success(t('messages.pluginStopped'))
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || t('messages.stopFailed'))
    }
  } finally {
    loading.value = false
  }
}

async function handleReload() {
  try {
    await ElMessageBox.confirm(t('messages.confirmReload'), t('common.confirm'), {
      type: 'warning'
    })
    loading.value = true
    await pluginStore.reload(props.pluginId)
    ElMessage.success(t('messages.pluginReloaded'))
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || t('messages.reloadFailed'))
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.plugin-actions {
  display: flex;
  gap: 8px;
}
</style>

