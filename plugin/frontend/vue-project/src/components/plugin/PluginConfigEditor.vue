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
        <el-button size="small" @click="resetDraft" :disabled="!hasChanges" :loading="saving">
          {{ t('common.reset') }}
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
      <PluginConfigForm
        v-if="mode === 'form'"
        :model-value="draftConfig"
        :baseline-value="baselineConfig"
        @update:model-value="updateDraftConfig"
      />

      <div v-else>
        <el-input
          v-model="draftToml"
          type="textarea"
          :rows="18"
          :placeholder="t('plugins.configEditorPlaceholder')"
          spellcheck="false"
          input-style="font-family: Monaco, Menlo, Consolas, 'Ubuntu Mono', monospace; font-size: 13px;"
        />

        <el-divider style="margin: 12px 0" />
        <div class="diff">
          <div class="diff-title">{{ t('plugins.diffPreview') }}</div>
          <pre class="diff-body">
<template v-for="(l, idx) in diffLines" :key="idx"><span :class="l.type">{{ l.prefix }} {{ l.text }}\n</span></template>
          </pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch, computed, toRaw } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Check } from '@element-plus/icons-vue'

import {
  getPluginConfig,
  getPluginConfigToml,
  parsePluginConfigToml,
  renderPluginConfigToml,
  updatePluginConfig,
  updatePluginConfigToml
} from '@/api/config'
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

const baselineConfig = ref<Record<string, any> | null>(null)
const draftConfig = ref<Record<string, any> | null>(null)

const baselineToml = ref('')
const draftToml = ref('')
const configPath = ref<string | undefined>(undefined)
const lastModified = ref<string | undefined>(undefined)

function deepClone<T>(v: T): T {
  try {
    return JSON.parse(JSON.stringify(toRaw(v))) as T
  } catch {
    return JSON.parse(JSON.stringify(v)) as T
  }
}

function sanitizeConfigForUpdate(cfg: Record<string, any>) {
  let next: Record<string, any>
  try {
    next = deepClone(cfg)
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
    const [tomlRes, cfgRes] = await Promise.all([
      getPluginConfigToml(props.pluginId),
      getPluginConfig(props.pluginId)
    ])
    configPath.value = tomlRes.config_path
    lastModified.value = tomlRes.last_modified

    baselineToml.value = tomlRes.toml || ''
    draftToml.value = baselineToml.value

    baselineConfig.value = (cfgRes.config || {}) as Record<string, any>
    draftConfig.value = deepClone(baselineConfig.value)
  } catch (e: any) {
    error.value = e?.message || t('plugins.configLoadFailed')
  } finally {
    loading.value = false
  }
}

function updateDraftConfig(v: Record<string, any> | null) {
  draftConfig.value = v
}

const hasChanges = computed(() => {
  const cfgChanged = JSON.stringify(baselineConfig.value || {}) !== JSON.stringify(draftConfig.value || {})
  const tomlChanged = (baselineToml.value || '') !== (draftToml.value || '')
  return cfgChanged || tomlChanged
})

async function syncToFormDraft() {
  const res = await parsePluginConfigToml(props.pluginId, draftToml.value || '')
  baselineConfig.value = baselineConfig.value || {}
  draftConfig.value = (res.config || {}) as Record<string, any>
}

async function syncToSourceDraft() {
  const res = await renderPluginConfigToml(props.pluginId, draftConfig.value || {})
  draftToml.value = res.toml || ''
}

async function resetDraft() {
  saving.value = true
  error.value = null
  try {
    draftToml.value = baselineToml.value
    draftConfig.value = deepClone(baselineConfig.value || {})
  } finally {
    saving.value = false
  }
}

async function save() {
  if (!props.pluginId) return

  saving.value = true
  error.value = null
  try {
    const res =
      mode.value === 'form'
        ? await updatePluginConfig(props.pluginId, sanitizeConfigForUpdate((draftConfig.value || {}) as Record<string, any>))
        : await updatePluginConfigToml(props.pluginId, draftToml.value || '')

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

watch(
  () => mode.value,
  async (m) => {
    if (!props.pluginId) return
    if (loading.value) return
    try {
      if (m === 'form') {
        await syncToFormDraft()
      } else {
        await syncToSourceDraft()
      }
    } catch (e: any) {
      error.value = e?.message || t('common.error')
    }
  }
)

type DiffLine = { type: 'add' | 'del' | 'ctx'; prefix: string; text: string }

function computeDiffLines(aText: string, bText: string): DiffLine[] {
  const a = (aText || '').split(/\r?\n/)
  const b = (bText || '').split(/\r?\n/)
  const n = a.length
  const m = b.length

  const dp: number[][] = Array.from({ length: n + 1 }, () => Array(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      const ai = a[i] ?? ''
      const bj = b[j] ?? ''
      const downRight = (dp[i + 1]?.[j + 1] ?? 0) + 1
      const down = dp[i + 1]?.[j] ?? 0
      const right = dp[i]?.[j + 1] ?? 0
      const row = dp[i] as number[]
      row[j] = ai === bj ? downRight : Math.max(down, right)
    }
  }

  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    const ai = a[i] ?? ''
    const bj = b[j] ?? ''
    if (ai === bj) {
      out.push({ type: 'ctx', prefix: ' ', text: ai })
      i++
      j++
    } else if ((dp[i + 1]?.[j] ?? 0) >= (dp[i]?.[j + 1] ?? 0)) {
      out.push({ type: 'del', prefix: '-', text: ai })
      i++
    } else {
      out.push({ type: 'add', prefix: '+', text: bj })
      j++
    }
  }
  while (i < n) {
    out.push({ type: 'del', prefix: '-', text: a[i] ?? '' })
    i++
  }
  while (j < m) {
    out.push({ type: 'add', prefix: '+', text: b[j] ?? '' })
    j++
  }
  return out
}

const diffLines = computed(() => computeDiffLines(baselineToml.value || '', draftToml.value || ''))
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

.diff-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}

.diff-body {
  font-family: Monaco, Menlo, Consolas, 'Ubuntu Mono', monospace;
  font-size: 12px;
  line-height: 1.5;
  background: var(--el-fill-color-lighter);
  border: 1px solid var(--el-border-color-lighter);
  padding: 8px;
  border-radius: 6px;
  max-height: 320px;
  overflow: auto;
  white-space: pre;
}

.diff-body .add {
  background: rgba(46, 160, 67, 0.14);
}

.diff-body .del {
  background: rgba(248, 81, 73, 0.12);
}
</style>
