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
        <div class="source-editor">
          <div ref="gutterScrollRef" class="gutter" aria-hidden="true">
            <div
              v-for="n in draftLineNumbers"
              :key="n"
              :class="gutterLineClass(n)"
            >
              {{ n }}
            </div>
          </div>
          <textarea
            ref="sourceTextareaRef"
            v-model="draftToml"
            class="source-textarea"
            :placeholder="t('plugins.configEditorPlaceholder')"
            spellcheck="false"
            rows="18"
            @scroll="onSourceScroll"
          />
        </div>

        <el-alert
          v-if="mode === 'source' && tomlCheckStatus !== 'idle'"
          :type="tomlCheckStatus === 'ok' ? 'success' : tomlCheckStatus === 'error' ? 'error' : 'info'"
          :title="tomlCheckTitle"
          :description="tomlCheckDesc"
          :closable="false"
          show-icon
          style="margin-top: 10px"
        />

        <el-divider style="margin: 12px 0" />
        <div class="diff">
          <div class="diff-title">{{ t('plugins.diffPreview') }}</div>
          <pre class="diff-body">
            <template v-for="(l, idx) in diffLines" :key="idx">
              <span :class="l.type">{{ l.prefix }} {{ l.text }}\n</span>
            </template>
          </pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch, computed, toRaw, nextTick } from 'vue'
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

const sourceDirty = ref(false)
const suppressSourceDirty = ref(false)

type TomlCheckStatus = 'idle' | 'checking' | 'ok' | 'error'
const tomlCheckStatus = ref<TomlCheckStatus>('idle')
const tomlCheckMessage = ref<string>('')
const tomlErrorLine = ref<number | null>(null)
const tomlCheckTimer = ref<number | null>(null)

const sourceTextareaRef = ref<HTMLTextAreaElement | null>(null)
const gutterScrollRef = ref<HTMLDivElement | null>(null)

function deepClone<T>(v: T): T {
  const raw = toRaw(v) as any
  const sc = (globalThis as any).structuredClone
  if (typeof sc === 'function') return sc(raw) as T
  return JSON.parse(JSON.stringify(raw)) as T
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
    suppressSourceDirty.value = true
    draftToml.value = baselineToml.value
    sourceDirty.value = false
    await syncGutterToTextarea()
    suppressSourceDirty.value = false

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
  draftConfig.value = (res.config || {}) as Record<string, any>
}

async function syncToSourceDraft() {
  const res = await renderPluginConfigToml(props.pluginId, draftConfig.value || {})
  suppressSourceDirty.value = true
  draftToml.value = res.toml || ''
  sourceDirty.value = false
  await syncGutterToTextarea()
  suppressSourceDirty.value = false
}

watch(
  () => draftToml.value,
  () => {
    if (mode.value === 'source' && !suppressSourceDirty.value) sourceDirty.value = true
    if (mode.value === 'source' && !suppressSourceDirty.value) scheduleTomlCheck()
  }
)

const tomlCheckTitle = computed(() => {
  if (tomlCheckStatus.value === 'ok') return t('common.success')
  if (tomlCheckStatus.value === 'error') return t('common.error')
  if (tomlCheckStatus.value === 'checking') return t('common.loading')
  return ''
})

const tomlCheckDesc = computed(() => {
  if (tomlCheckStatus.value === 'ok') return t('common.success')
  return tomlCheckMessage.value || ''
})

function parseErrorLine(msg: string): number | null {
  const m = String(msg || '').match(/\bline\s+(\d+)\b/i)
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) && n > 0 ? n : null
}

function scheduleTomlCheck() {
  if (!props.pluginId) return
  if (tomlCheckTimer.value) window.clearTimeout(tomlCheckTimer.value)

  tomlCheckStatus.value = 'checking'
  tomlCheckMessage.value = ''
  tomlErrorLine.value = null

  tomlCheckTimer.value = window.setTimeout(() => {
    void runTomlCheck()
  }, 350)
}

async function runTomlCheck() {
  if (!props.pluginId) return
  if (mode.value !== 'source') return

  try {
    await parsePluginConfigToml(props.pluginId, draftToml.value || '')
    tomlCheckStatus.value = 'ok'
    tomlCheckMessage.value = t('common.success')
    tomlErrorLine.value = null
  } catch (e: any) {
    tomlCheckStatus.value = 'error'
    const msg = e?.message || String(e)
    tomlCheckMessage.value = msg
    tomlErrorLine.value = parseErrorLine(msg)
  }
}

function gutterLineClass(n: number) {
  const cls: string[] = []
  const mk = lineMarks.value[n - 1]
  if (mk) cls.push(`mark-${mk}`)
  if (tomlCheckStatus.value === 'error' && tomlErrorLine.value === n) cls.push('mark-err')
  return cls.join(' ')
}

async function resetDraft() {
  error.value = null
  suppressSourceDirty.value = true
  try {
    draftToml.value = baselineToml.value
    draftConfig.value = deepClone(baselineConfig.value || {})
    sourceDirty.value = false
    await syncGutterToTextarea()
  } finally {
    suppressSourceDirty.value = false
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
        if (e !== 'cancel' && e !== 'close') {
          ElMessage.error(e?.message || t('messages.reloadFailed'))
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
  async (m, prev) => {
    if (!props.pluginId) return
    if (loading.value) return
    try {
      if (m === 'form') {
        if (prev === 'form') return
        if (sourceDirty.value) {
          await syncToFormDraft()
          sourceDirty.value = false
        }
      } else {
        if (sourceDirty.value) {
          await syncGutterToTextarea()
          return
        }

        const cfgUnchanged =
          JSON.stringify(baselineConfig.value || {}) === JSON.stringify(draftConfig.value || {})
        if (cfgUnchanged) {
          suppressSourceDirty.value = true
          draftToml.value = baselineToml.value
          sourceDirty.value = false
          await syncGutterToTextarea()
          suppressSourceDirty.value = false
          tomlCheckStatus.value = 'idle'
          tomlCheckMessage.value = ''
          tomlErrorLine.value = null
        } else {
          await syncToSourceDraft()
        }
      }
    } catch (e: any) {
      error.value = e?.message || t('common.error')
    }
  }
)

type DiffLine = { type: 'add' | 'del' | 'ctx'; prefix: string; text: string }

function splitLinesForDiff(text: string): string[] {
  const t = (text || '').replace(/\r\n/g, '\n')
  const lines = t.split(/\n/)
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

function computeDiffLines(aText: string, bText: string): DiffLine[] {
  const a = splitLinesForDiff(aText)
  const b = splitLinesForDiff(bText)
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

type LineMark = 'add' | 'mod' | 'del' | null

const draftLineCount = computed(() => {
  const lines = splitLinesForDiff(draftToml.value || '')
  return Math.max(1, lines.length)
})

const draftLineNumbers = computed(() => Array.from({ length: draftLineCount.value }, (_, i) => i + 1))

const lineMarks = computed<LineMark[]>(() => {
  const marks: LineMark[] = Array.from({ length: draftLineCount.value }, () => null)
  const delsBefore: number[] = Array.from({ length: draftLineCount.value + 2 }, () => 0)

  let draftLn = 0
  let prevWasDel = false
  for (const l of diffLines.value) {
    if (l.type === 'del') {
      if (draftLn <= draftLineCount.value) delsBefore[draftLn] = (delsBefore[draftLn] || 0) + 1
      prevWasDel = true
      continue
    }

    draftLn++
    if (draftLn > draftLineCount.value) break

    if (l.type === 'add') {
      marks[draftLn - 1] = prevWasDel ? 'mod' : 'add'
    }
    prevWasDel = false
  }

  for (let i = 1; i <= draftLineCount.value; i++) {
    if ((delsBefore[i] || 0) > 0 && marks[i - 1] == null) marks[i - 1] = 'del'
  }
  if ((delsBefore[0] || 0) > 0 && marks[0] == null) marks[0] = 'del'

  return marks
})

function onSourceScroll(e: Event) {
  const ta = e.target as HTMLTextAreaElement
  if (gutterScrollRef.value) gutterScrollRef.value.scrollTop = ta.scrollTop
}

async function syncGutterToTextarea() {
  await nextTick()
  if (sourceTextareaRef.value && gutterScrollRef.value) {
    gutterScrollRef.value.scrollTop = sourceTextareaRef.value.scrollTop
  }
}
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

.source-editor {
  display: flex;
  width: 100%;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
  background: var(--el-fill-color-lighter);
}

.gutter {
  flex: 0 0 56px;
  padding: 10px 0;
  border-right: 1px solid var(--el-border-color-lighter);
  background: rgba(0, 0, 0, 0.02);
  overflow: hidden;
}

.gutter-line {
  height: 20px;
  line-height: 20px;
  padding: 0 10px;
  font-family: Monaco, Menlo, Consolas, 'Ubuntu Mono', monospace;
  font-size: 12px;
  text-align: right;
  color: var(--el-text-color-secondary);
  user-select: none;
  white-space: nowrap;
}

.gutter-line.mark-add {
  background: rgba(46, 160, 67, 0.14);
}

.gutter-line.mark-mod {
  background: rgba(210, 153, 34, 0.16);
}

.gutter-line.mark-del {
  background: rgba(248, 81, 73, 0.12);
}

.gutter-line.mark-err {
  background: rgba(248, 81, 73, 0.22);
  box-shadow: inset 0 0 0 1px rgba(248, 81, 73, 0.35);
}

.source-textarea {
  flex: 1 1 auto;
  border: none;
  outline: none;
  resize: vertical;
  padding: 10px 12px;
  margin: 0;
  font-family: Monaco, Menlo, Consolas, 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 20px;
  background: transparent;
  color: var(--el-text-color-primary);
  width: 100%;
  min-height: 280px;
  overflow: auto;
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
