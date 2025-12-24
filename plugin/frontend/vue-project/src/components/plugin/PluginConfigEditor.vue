<template>
  <div class="plugin-config-editor">
    <div class="header">
      <div class="meta">
        <div v-if="configPath" class="meta-line">
          <span class="meta-label">{{ t('plugins.configPath') }}:</span>
          <span class="meta-value">{{ configPath }}</span>
        </div>
        <div v-if="lastModified" class="meta-line">
          <span class="meta-label">{{ t('plugins.lastModified') }}:</span>
          <span class="meta-value">{{ lastModified }}</span>
        </div>
      </div>

      <div class="actions">
        <el-segmented
          v-model="mode"
          :options="modeOptions"
          size="small"
          style="margin-right: 8px"
        />
        <el-button :icon="Refresh" size="small" @click="load" :loading="loading">
          {{ t('common.refresh') }}
        </el-button>
        <el-button type="primary" :icon="Check" size="small" @click="save" :loading="saving">
          {{ t('common.save') }}
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="error"
      :title="t('common.error')"
      :description="error"
      type="error"
      :closable="false"
      show-icon
      style="margin-bottom: 12px"
    />

    <el-skeleton v-if="loading" :rows="8" animated />

    <div v-else>
      <PluginConfigForm v-if="mode === 'form'" ref="formRef" :plugin-id="pluginId" />

      <el-input
        v-else
        v-model="rawText"
        type="textarea"
        :rows="18"
        :placeholder="t('plugins.configEditorPlaceholder')"
        spellcheck="false"
        input-style="font-family: Monaco, Menlo, Consolas, 'Ubuntu Mono', monospace; font-size: 13px;"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Check } from '@element-plus/icons-vue'

import { getPluginConfig, getPluginConfigToml, updatePluginConfig, updatePluginConfigToml } from '@/api/config'
import { usePluginStore } from '@/stores/plugin'
import PluginConfigForm from '@/components/plugin/PluginConfigForm.vue'

interface Props {
  pluginId: string
}

const props = defineProps<Props>()
const { t } = useI18n()
const pluginStore = usePluginStore()

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const mode = ref<'form' | 'source'>('form')
const modeOptions = computed(() => [
  { label: t('plugins.formMode'), value: 'form' },
  { label: t('plugins.sourceMode'), value: 'source' }
])

const formRef = ref<InstanceType<typeof PluginConfigForm> | null>(null)

const rawText = ref('')
const configPath = ref<string | undefined>(undefined)
const lastModified = ref<string | undefined>(undefined)

function sanitizeConfigForUpdate(cfg: Record<string, any>) {
  let next: Record<string, any>
  try {
    next = typeof structuredClone === 'function' ? structuredClone(cfg) : JSON.parse(JSON.stringify(cfg))
  } catch {
    next = { ...(cfg || {}) }
  }

  if (next && typeof next === 'object' && next.plugin && typeof next.plugin === 'object') {
    delete next.plugin.id
    delete next.plugin.entry
  }
  return next
}

async function load() {
  if (!props.pluginId) return

  loading.value = true
  error.value = null
  try {
    const res = await getPluginConfigToml(props.pluginId)
    configPath.value = res.config_path
    lastModified.value = res.last_modified

    rawText.value = res.toml || ''

    // 预热表单数据（不阻塞）
    if (formRef.value) {
      formRef.value.load()
    } else {
      // 当表单尚未挂载时，提前拉一次 config 确保后端可用
      void getPluginConfig(props.pluginId)
    }
  } catch (e: any) {
    error.value = e?.message || t('plugins.configLoadFailed')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!props.pluginId) return

  saving.value = true
  error.value = null
  try {
    const res =
      mode.value === 'form'
        ? await updatePluginConfig(
            props.pluginId,
            sanitizeConfigForUpdate((formRef.value?.getValue?.() || {}) as Record<string, any>)
          )
        : await updatePluginConfigToml(props.pluginId, rawText.value || '')

    ElMessage.success(res.message || t('common.success'))

    if (res.requires_reload) {
      try {
        await ElMessageBox.confirm(t('plugins.configReloadPrompt'), t('plugins.configReloadTitle'), {
          type: 'warning'
        })
        await pluginStore.reload(props.pluginId)
        ElMessage.success(t('messages.pluginReloaded'))
      } catch (e: any) {
        if (e !== 'cancel') {
          // ignore
        }
      }
    }

    await load()
  } catch (e: any) {
    error.value = e?.message || t('plugins.configSaveFailed')
  } finally {
    saving.value = false
  }
}

onMounted(load)

watch(
  () => props.pluginId,
  () => {
    load()
  }
)
</script>

<style scoped>
.plugin-config-editor {
  padding: 8px 0;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}

.meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.meta-line {
  display: flex;
  gap: 6px;
}

.meta-label {
  white-space: nowrap;
}

.meta-value {
  word-break: break-all;
}

.actions {
  display: flex;
  gap: 8px;
}
</style>
