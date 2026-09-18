<template>
  <section class="development-panel" :aria-label="t('development.title')">
    <div class="development-toolbar">
      <label><el-switch :model-value="enabled" :disabled="busy" @change="toggle" /> {{ t('development.title') }}</label>
      <el-button v-if="enabled" :disabled="busy" @click="openLoader()">{{ t('development.load') }}</el-button>
      <el-button :disabled="busy" text @click="refresh">{{ t('common.refresh') }}</el-button>
      <slot name="help" />
    </div>
    <p v-if="enabled" class="development-hint">{{ t('development.hint') }}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="development-grid">
      <article v-for="record in records" :key="record.registration_id" class="development-card">
        <header><strong>{{ record.name || record.plugin_id }}</strong><el-tag>{{ t('development.badge') }}</el-tag></header>
        <p>{{ record.plugin_id }} · {{ record.version || '—' }} · {{ t(statusLabel(record.plugin_id)) }}</p>
        <p class="development-path">{{ record.source_dir }}</p>
        <details v-if="record.entry"><summary>{{ t('development.entry') }}</summary><code>{{ record.entry }}</code></details>
        <details class="development-entries" open>
          <summary>{{ t('plugins.entryPoint') }} ({{ entriesByPlugin.get(record.plugin_id)?.length || 0 }})</summary>
          <ul v-if="entriesByPlugin.get(record.plugin_id)?.length">
            <li v-for="entry in entriesByPlugin.get(record.plugin_id)" :key="entry.id">
              <code>{{ entry.id }}</code>
              <p>{{ entry.description || t('common.noData') }}</p>
            </li>
          </ul>
          <p v-else>{{ t('common.noData') }}</p>
        </details>
        <el-alert v-if="record.error" :title="record.error" type="error" :closable="false" />
        <div class="development-actions">
          <el-button v-if="host?.openPath" :disabled="busy" @click="openSource(record)">{{ t('development.openSource') }}</el-button>
          <el-button :disabled="busy || (!enabled && !isRunning(record))" @click="lifecycle(record, isRunning(record) ? 'stop' : 'start')">{{ t(isRunning(record) ? 'development.stop' : 'development.start') }}</el-button>
          <el-button :disabled="busy || !enabled" @click="lifecycle(record, 'reload')">{{ t('development.reload') }}</el-button>
          <el-button @click="router.push(`/logs/${encodeURIComponent(record.plugin_id)}`)">{{ t('development.logs') }}</el-button>
          <el-button :disabled="busy" @click="openLoader(record)">{{ t('development.rebind') }}</el-button>
          <el-button :disabled="busy" type="danger" plain @click="remove(record)">{{ t('development.remove') }}</el-button>
          <el-button class="development-build" type="primary" plain :disabled="busy || !enabled" @click="build(record)">{{ t('development.build') }}</el-button>
        </div>
      </article>
    </div>
    <el-dialog v-model="dialog" class="neko-development-dialog" :title="t(rebinding ? 'development.rebind' : 'development.load')" width="min(660px, 92vw)" align-center :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <template #header="{ titleId, titleClass }">
        <div class="source-dialog-heading">
          <span class="source-dialog-icon"><el-icon><FolderOpened /></el-icon></span>
          <div>
            <h2 :id="titleId" :class="titleClass">{{ t(rebinding ? 'development.rebind' : 'development.load') }}</h2>
            <p>{{ t('development.sourcePlaceholder') }}</p>
          </div>
        </div>
      </template>
      <div class="source-dialog-body">
        <label class="source-dialog-label" for="development-source-directory">{{ t('development.path') }}</label>
        <div class="source-dialog-picker">
          <el-input id="development-source-directory" v-model="sourceDir" size="large" :disabled="busy" :placeholder="t('development.sourcePlaceholder')" :aria-label="t('development.path')" @input="preview = null" />
          <el-button v-if="host?.pickDirectory" size="large" :icon="FolderOpened" :disabled="busy" @click="pick">{{ t('development.choose') }}</el-button>
        </div>
        <p class="source-dialog-hint"><el-icon><InfoFilled /></el-icon><span>{{ t('development.pathHint') }}</span></p>
        <el-alert v-if="dialogError" :title="dialogError" type="error" :closable="false" show-icon />
        <div v-if="preview" class="source-dialog-preview" role="status">
          <div class="source-dialog-preview-heading">
            <span><el-icon><CircleCheckFilled /></el-icon>{{ t('development.valid') }}</span>
            <el-tag size="small" type="info">v{{ preview.version }}</el-tag>
          </div>
          <strong class="source-dialog-plugin-name">{{ preview.name }}</strong>
          <dl>
            <dt>ID</dt><dd>{{ preview.plugin_id }}</dd>
            <dt>{{ t('development.entry') }}</dt><dd>{{ preview.entry }}</dd>
            <dt>{{ t('development.path') }}</dt><dd>{{ preview.source_dir }}</dd>
          </dl>
        </div>
        <div v-else class="source-dialog-empty"><el-icon><DocumentChecked /></el-icon><span>{{ t('development.previewHint') }}</span></div>
      </div>
      <template #footer>
        <div class="source-dialog-footer">
          <el-button class="source-dialog-cancel" :disabled="busy" @click="dialog = false">{{ t('common.cancel') }}</el-button>
          <el-button :disabled="busy || !sourceDir.trim()" @click="validate">{{ t('development.validate') }}</el-button>
          <el-button type="primary" :disabled="busy || !preview" @click="save">{{ t(rebinding ? 'development.rebind' : 'development.load') }}</el-button>
        </div>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { FolderOpened, InfoFilled, CircleCheckFilled, DocumentChecked } from '@element-plus/icons-vue'
import { usePluginStore } from '@/stores/plugin'
import { getDevelopment, setDevelopmentEnabled, registerDevelopment, rebindDevelopment, removeDevelopment, runDevelopmentAction, downloadDevelopmentPackage, type DevelopmentRegistration } from '@/api/development'
import { buildPluginCli } from '@/api/pluginCli'
import { formatHttpError } from '@/utils/request'

interface HostBridge {
  pickDirectory?: (options: { title: string; startPath?: string }) => Promise<{ cancelled?: boolean; selected_root?: string }>
  openPath?: (options: { path: string }) => Promise<{ ok: boolean; error?: string }>
}
const host = (window as unknown as { nekoHost?: HostBridge }).nekoHost
const emit = defineEmits<{ 'registrations-change': [count: number] }>()
const { t } = useI18n()
const router = useRouter()
const store = usePluginStore()
const enabled = ref(false)
const records = ref<DevelopmentRegistration[]>([])
const busy = ref(false)
const error = ref('')
const dialogError = ref('')
const dialog = ref(false)
const sourceDir = ref('')
const preview = ref<DevelopmentRegistration | null>(null)
const rebinding = ref<DevelopmentRegistration | null>(null)
const statuses = computed(() => new Map(store.pluginsWithStatus.map((plugin) => [plugin.id, plugin.status])))
const entriesByPlugin = computed(() => new Map(store.pluginsWithStatus.map((plugin) => [plugin.id, plugin.entries || []])))
const status = (id: string) => statuses.value.get(id) || 'stopped'
const statusKeys: Record<string, string> = { load_failed: 'status.loadFailed', source_missing: 'status.sourceMissing' }
const statusLabel = (id: string) => statusKeys[status(id)] || `status.${status(id)}`
const isRunning = (record: DevelopmentRegistration) => record.runtime_alive ?? status(record.plugin_id) === 'running'
const message = (err: unknown) => formatHttpError(err) || String(err)
async function refresh() {
  const wasBusy = busy.value
  busy.value = true
  try {
    const [state] = await Promise.all([getDevelopment(), store.fetchPlugins(true), store.fetchPluginStatus()])
    enabled.value = state.enabled
    records.value = state.registrations
    emit('registrations-change', records.value.length)
    error.value = ''
  } catch (err) { error.value = message(err) }
  finally { busy.value = wasBusy }
}
async function perform(action: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await action() } catch (err) { error.value = message(err) }
  finally {
    const actionError = error.value
    await refresh()
    if (actionError) error.value = actionError
    busy.value = false
  }
}
async function toggle(value: string | number | boolean) {
  await perform(() => setDevelopmentEnabled(Boolean(value)))
}
function openLoader(record?: DevelopmentRegistration) {
  rebinding.value = record ? { ...record } : null
  sourceDir.value = record?.source_dir || ''
  preview.value = null
  dialogError.value = ''
  dialog.value = true
}
async function pick() {
  try {
    const selected = await host?.pickDirectory?.({ title: t('development.choose'), startPath: sourceDir.value })
    if (!selected?.cancelled && selected?.selected_root) { sourceDir.value = selected.selected_root; preview.value = null }
  } catch (err) { dialogError.value = message(err) }
}
async function validate() {
  busy.value = true
  dialogError.value = ''
  preview.value = null
  try { preview.value = await registerDevelopment(sourceDir.value.trim(), true, rebinding.value || undefined) }
  catch (err) { dialogError.value = message(err) }
  finally { busy.value = false }
}
async function save() {
  if (!preview.value) return
  const path = preview.value.source_dir
  await perform(async () => {
    dialogError.value = ''
    try {
      const result = rebinding.value ? await rebindDevelopment(rebinding.value, path) : await registerDevelopment(path)
      dialog.value = false
      if (result.error) error.value = result.error
    } catch (err) {
      dialogError.value = message(err)
      throw err
    }
  })
}
async function lifecycle(record: DevelopmentRegistration, action: 'start' | 'stop' | 'reload') {
  await perform(async () => {
    const result = await runDevelopmentAction(record, action)
    if (!result.success) throw new Error(result.message || t('development.failed'))
  })
}
async function build(record: DevelopmentRegistration) {
  await perform(async () => {
    const result = await buildPluginCli({ mode: 'single', development_ref: { registration_id: record.registration_id, revision: record.revision } })
    if (!result.ok || !result.built.length) throw new Error(result.failed.map((item) => item.error).join('\n') || t('development.failed'))
    for (const item of result.built) await downloadDevelopmentPackage(item.package_path)
    ElMessage.success(t('development.built'))
  })
}
async function remove(record: DevelopmentRegistration) {
  try { await ElMessageBox.confirm(t('development.removeHint'), t('development.remove'), { type: 'warning' }) }
  catch { return }
  await perform(() => removeDevelopment(record))
}
async function openSource(record: DevelopmentRegistration) {
  try {
    const result = await host?.openPath?.({ path: record.source_dir })
    if (result && !result.ok) throw new Error(result.error || t('development.failed'))
  }
  catch (err) { error.value = message(err) }
}
onMounted(refresh)
</script>

<style scoped>
.development-panel { margin-bottom: 20px; }
.development-toolbar, .development-card header, .development-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.development-toolbar label { display: flex; align-items: center; gap: 8px; }
.development-hint { color: var(--el-text-color-secondary); }
.development-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr)); gap: 14px; margin-top: 12px; }
.development-card { border: 1px solid var(--el-border-color); border-radius: 10px; padding: 16px; min-width: 0; }
.development-card header { justify-content: space-between; }
.development-path { overflow-wrap: anywhere; font-family: monospace; }
.development-actions { margin-top: 14px; }
.development-actions .el-button { margin-left: 0; }
.development-actions .development-build { margin-left: auto; }
.development-entries { margin-top: 12px; overflow-wrap: anywhere; }
.development-entries summary { cursor: pointer; }
.development-entries ul { list-style: none; margin: 8px 0; padding: 0; max-height: 280px; overflow-y: auto; }
.development-entries li { padding: 8px; border-bottom: 1px solid var(--el-border-color-lighter); }
.development-entries p { margin: 6px 0; white-space: pre-wrap; color: var(--el-text-color-secondary); }
dd { margin: 6px 0 14px; }
:global(.neko-development-dialog) { padding: 0; border-radius: 20px; overflow: hidden; border: 1px solid var(--el-border-color-lighter); box-shadow: var(--el-box-shadow-dark); }
:global(.neko-development-dialog .el-dialog__header) { margin: 0; padding: 26px 52px 22px 28px; background: linear-gradient(120deg, color-mix(in srgb, var(--el-color-primary) 8%, var(--el-bg-color)), var(--el-bg-color)); border-bottom: 1px solid var(--el-border-color-lighter); }
:global(.neko-development-dialog .el-dialog__headerbtn) { top: 14px; right: 12px; }
:global(.neko-development-dialog .el-dialog__body) { padding: 0; }
:global(.neko-development-dialog .el-dialog__footer) { padding: 18px 28px; border-top: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-extra-light); }
.source-dialog-heading { display: flex; align-items: center; gap: 14px; }
.source-dialog-icon { display: flex; align-items: center; justify-content: center; flex: 0 0 46px; height: 46px; border-radius: 14px; color: var(--el-color-primary); background: var(--el-color-primary-light-9); font-size: 25px; }
.source-dialog-heading h2 { margin: 0; font-size: 19px; font-weight: 650; line-height: 1.4; }
.source-dialog-heading p { margin: 5px 0 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.5; }
.source-dialog-body { padding: 24px 28px; }
.source-dialog-label { display: block; margin-bottom: 10px; font-size: 14px; font-weight: 600; color: var(--el-text-color-primary); }
.source-dialog-picker { display: flex; gap: 10px; }
.source-dialog-picker .el-input { min-width: 0; flex: 1; }
.source-dialog-picker :deep(.el-input__wrapper), .source-dialog-picker .el-button { border-radius: 10px; }
.source-dialog-picker :deep(.el-input__inner) { font-family: var(--el-font-family); }
.source-dialog-hint { display: flex; align-items: flex-start; gap: 7px; margin: 12px 0 20px; font-size: 12px; line-height: 1.7; color: var(--el-text-color-secondary); }
.source-dialog-hint .el-icon { flex-shrink: 0; margin-top: 4px; }
.source-dialog-empty { display: flex; align-items: center; gap: 10px; padding: 18px; border: 1px dashed var(--el-border-color); border-radius: 12px; color: var(--el-text-color-secondary); background: var(--el-fill-color-extra-light); font-size: 13px; line-height: 1.6; }
.source-dialog-empty .el-icon { font-size: 22px; flex-shrink: 0; color: var(--el-color-primary); }
.source-dialog-preview { margin-top: 16px; padding: 18px; border: 1px solid var(--el-color-success-light-7); border-radius: 12px; background: color-mix(in srgb, var(--el-color-success) 4%, var(--el-bg-color)); }
.source-dialog-preview-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.source-dialog-preview-heading > span { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--el-color-success); }
.source-dialog-plugin-name { display: block; margin: 12px 0; font-size: 16px; color: var(--el-text-color-primary); overflow-wrap: anywhere; }
.source-dialog-preview dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 16px; margin: 0; font-size: 12px; line-height: 1.6; }
.source-dialog-preview dt { color: var(--el-text-color-secondary); }
.source-dialog-preview dd { margin: 0; overflow-wrap: anywhere; font-family: monospace; }
.source-dialog-footer { display: flex; gap: 10px; align-items: center; }
.source-dialog-footer .el-button { margin: 0; min-height: 36px; border-radius: 9px; }
.source-dialog-footer .source-dialog-cancel { margin-right: auto; }
@media (max-width: 520px) {
  :global(.neko-development-dialog .el-dialog__header) { padding: 22px 44px 18px 20px; }
  :global(.neko-development-dialog .el-dialog__footer) { padding: 16px 20px; }
  .source-dialog-body { padding: 20px; }
  .source-dialog-picker { flex-direction: column; }
  .source-dialog-footer { flex-wrap: wrap; justify-content: flex-end; }
  .source-dialog-heading { gap: 10px; }
}
</style>
