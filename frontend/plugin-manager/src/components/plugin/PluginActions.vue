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
      {{ uiActionLabel }}
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
import { resolveLocalizedText } from '@/utils/i18nLabel'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const pluginStore = usePluginStore()
const router = useRouter()
const { t, locale } = useI18n()

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
// label 可能是 string 或 locale-keyed dict（与 backend list_action contract
// 对齐，见 utils/i18nLabel.ts）。直接 `{{ uiAction.label }}` 在 dict 时会
// 渲染成 "[object Object]"，必须先按当前 locale 解析。
const uiActionLabel = computed(() =>
  resolveLocalizedText(uiAction.value?.label, locale.value, t('plugins.ui.open')),
)

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
  // open_in 缺省时统一默认 new_tab，与 usePluginListContextActions.ts:366
  // 的 list-action executor convention 对齐（`open_in === 'same_tab' ? '_self'
  // : '_blank'`）。否则同一个 plugin manager 里 Open UI 按钮和右键菜单
  // action 行为分叉，用户体验不一致。
  const openInNewTab = action.open_in !== 'same_tab'

  // 显式 target：用 browser navigation（window.open），不走 SPA router——
  // 与 list-action executor 的 ui/url 分支行为一致。SPA router（
  // src/router/index.ts）只认识 manager 内部路由（/plugins/:id 等），plugin
  // server 暴露的 path（如 /plugin/<id>/ui/）和外部 URL 都得走 browser
  // navigation 才能正确跳转，否则 same_tab + plugin-server path 会被当
  // unmatched SPA route，UI 不打开。
  if (target) {
    const windowTarget = openInNewTab ? '_blank' : '_self'
    window.open(
      target,
      windowTarget,
      openInNewTab ? 'noopener,noreferrer' : undefined,
    )
    return
  }

  // 无 target 时退回默认 plugin 详情页 ?tab=ui，这是 manager 内部路由，
  // 用 router.push 走 SPA 内导航更平顺；new_tab 时仍 window.open 新窗口。
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
