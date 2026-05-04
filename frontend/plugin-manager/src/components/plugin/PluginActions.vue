<template>
  <div class="plugin-actions">
    <el-button
      v-if="uiAction"
      type="primary"
      plain
      :icon="Monitor"
      @click="handleOpenUi"
      :disabled="uiDisabled"
    >
      {{ uiAction.label || t('plugins.ui.open') }}
    </el-button>
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
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { VideoPlay, VideoPause, Refresh, SwitchButton, Monitor } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usePluginStore } from '@/stores/plugin'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const pluginStore = usePluginStore()
const router = useRouter()
const { t } = useI18n()

const loading = ref(false)

const currentPlugin = computed(() => {
  return pluginStore.pluginsWithStatus.find(p => p.id === props.pluginId)
})

const status = computed(() => currentPlugin.value?.status || 'stopped')
const isExtension = computed(() => currentPlugin.value?.type === 'extension')
const isDisabled = computed(() => status.value === 'disabled')
const uiAction = computed(() => {
  return currentPlugin.value?.list_actions?.find((action) => action.kind === 'ui') || null
})
const uiDisabled = computed(() => {
  if (!uiAction.value) return true
  if (uiAction.value.disabled) return true
  if (uiAction.value.requires_running && status.value !== 'running') return true
  return false
})

async function handleOpenUi() {
  if (!uiAction.value || uiDisabled.value) {
    return
  }

  // 尊重 backend 的 list_action 契约（plugin/server/application/plugins/ui_query_service.py
  // _normalize_list_action 会显式 normalize `target` 和 `open_in`）。
  // 若 plugin 声明了外部 URL / 自定义路由 / 新 tab 打开，UI 必须按字段路由，
  // 而不是无条件回退到默认的 `/plugins/{id}?tab=ui` 静态详情页。
  const action = uiAction.value
  const target = action.target?.trim() || ''
  const openInNewTab = action.open_in === 'new_tab'

  // 1) 显式外部 URL：按 open_in 在新 tab 或当前页跳转
  if (target && /^https?:\/\//i.test(target)) {
    if (openInNewTab) {
      window.open(target, '_blank', 'noopener,noreferrer')
    } else {
      window.location.href = target
    }
    return
  }

  // 2) 内部路由：target 作为 vue-router path
  if (target) {
    if (openInNewTab) {
      const resolved = router.resolve(target)
      window.open(resolved.href, '_blank', 'noopener,noreferrer')
    } else {
      await router.push(target)
    }
    return
  }

  // 3) 无 target 时退回默认 plugin 详情页 ?tab=ui
  const fallback = {
    path: `/plugins/${encodeURIComponent(props.pluginId)}`,
    query: { tab: 'ui' },
  }
  if (openInNewTab) {
    const resolved = router.resolve(fallback)
    window.open(resolved.href, '_blank', 'noopener,noreferrer')
  } else {
    await router.push(fallback)
  }
}

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
  flex-wrap: wrap;
}
</style>
