<template>
  <div class="plugin-actions">
    <!-- Extension 操作按钮 -->
    <el-button-group v-if="isExtension">
      <el-button
        v-if="status !== 'disabled'"
        type="warning"
        :icon="SwitchButton"
        @click="handleDisableExt"
        :loading="loading"
      >
        {{ t('plugins.disableExtension') }}
      </el-button>
      <el-button
        v-else
        type="success"
        :icon="SwitchButton"
        @click="handleEnableExt"
        :loading="loading"
      >
        {{ t('plugins.enableExtension') }}
      </el-button>
    </el-button-group>
    <!-- 普通插件操作按钮 -->
    <el-button-group v-else>
      <el-button
        v-if="status !== 'running' && status !== 'disabled'"
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
        :disabled="status === 'disabled'"
      >
        {{ t('plugins.reload') }}
      </el-button>
    </el-button-group>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { VideoPlay, VideoPause, Refresh, SwitchButton } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const pluginStore = usePluginStore()
const { t } = useI18n()

const loading = ref(false)

const currentPlugin = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === props.pluginId)
})

const status = computed(() => currentPlugin.value?.status || 'stopped')
const isExtension = computed(() => currentPlugin.value?.type === 'extension')
const isDisabled = computed(() => status.value === 'disabled')

async function handleStart() {
  if (isDisabled.value) {
    ElMessage.warning(t('messages.pluginDisabled'))
    return
  }
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
  if (isDisabled.value) {
    ElMessage.warning(t('messages.pluginDisabled'))
    return
  }
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
  if (isDisabled.value) {
    ElMessage.warning(t('messages.pluginDisabled'))
    return
  }
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

async function handleDisableExt() {
  try {
    await ElMessageBox.confirm(t('messages.confirmDisableExt'), t('common.confirm'), {
      type: 'warning'
    })
    loading.value = true
    await pluginStore.disableExt(props.pluginId)
    ElMessage.success(t('messages.extensionDisabled'))
  } catch (error: any) {
    if (error !== 'cancel') {
      ElMessage.error(error.message || t('messages.disableExtFailed'))
    }
  } finally {
    loading.value = false
  }
}

async function handleEnableExt() {
  try {
    loading.value = true
    await pluginStore.enableExt(props.pluginId)
    ElMessage.success(t('messages.extensionEnabled'))
  } catch (error: any) {
    ElMessage.error(error.message || t('messages.enableExtFailed'))
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

