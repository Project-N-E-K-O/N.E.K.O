<template>
  <div
    ref="container"
    class="plugin-config-editor"
    :class="{ 'page-scroll': pageScroll }"
    :id="editorId"
    :aria-busy="loading || saving || applying"
  >
    <div ref="toolbar" class="config-toolbar">
      <el-input
        v-model="search"
        :prefix-icon="Search"
        :placeholder="t('plugins.configUi.search')"
        :aria-label="t('plugins.configUi.search')"
        clearable
      />
      <el-dropdown trigger="click" @command="(value: ConfigFilter) => (filter = value)">
        <el-button
          text
          :class="{ 'filter-active': filter !== 'all' }"
          :aria-label="t('plugins.configUi.filterFields')"
        >
          {{ filterOptions.find((option) => option.value === filter)?.label
          }}<el-icon class="trailing-icon"><ArrowDown /></el-icon>
        </el-button>
        <template #dropdown
          ><el-dropdown-menu>
            <el-dropdown-item
              v-for="option in filterOptions"
              :key="option.value"
              :command="option.value"
            >
              {{ option.label
              }}<span v-if="option.count" class="filter-count">{{ option.count }}</span>
            </el-dropdown-item>
          </el-dropdown-menu></template
        >
      </el-dropdown>
      <div class="toolbar-spacer" />
      <el-button class="profile-entry" text :icon="Setting" @click="profilesOpen = true">
        {{ t('plugins.profiles') }}<span class="profile-name">{{ selected }}</span
        ><el-icon class="trailing-icon"><ArrowDown /></el-icon>
      </el-button>
      <el-dropdown trigger="click" @command="toolbarCommand">
        <el-button
          text
          :icon="MoreFilled"
          :aria-label="t('plugins.configUi.configData') + ' / ' + t('common.refresh')"
        />
        <template #dropdown
          ><el-dropdown-menu>
            <el-dropdown-item command="data" :icon="Document">{{
              t('plugins.configUi.configData')
            }}</el-dropdown-item>
            <el-dropdown-item
              command="refresh"
              :icon="Refresh"
              :disabled="loading || saving || applying"
              >{{ t('common.refresh') }}</el-dropdown-item
            >
          </el-dropdown-menu></template
        >
      </el-dropdown>
    </div>
    <el-dialog
      v-model="profilesOpen"
      :title="t('plugins.profiles')"
      class="profile-manager-dialog"
      width="min(520px, 94vw)"
      append-to-body
    >
      <div class="profile-manager-body">
        <label class="profile-label" for="config-profile-picker">{{
          t('plugins.configUi.editProfile')
        }}</label>
        <div class="profile-picker-row">
          <el-select
            id="config-profile-picker"
            data-testid="profile-select"
            :model-value="selected"
            :aria-label="t('plugins.configUi.editProfile')"
            :disabled="loading || saving || applying"
            @change="selectProfile"
          >
            <el-option
              v-for="name in names"
              :key="name"
              :value="name"
              :label="
                name +
                (dirtyCount(name)
                  ? ' · ' + t('plugins.configUi.unsavedCount', { count: dirtyCount(name) })
                  : '')
              "
            />
          </el-select>
          <el-button
            :icon="Plus"
            :disabled="!profiles || loading || saving || applying"
            @click="addProfile"
            >{{ t('plugins.configUi.newProfile') }}</el-button
          >
        </div>
        <div v-if="selected && !virtualDefault(selected)" class="profile-current-row">
          <span class="active-profile">{{
            active ? t('plugins.configUi.activeProfile', { name: active }) : ''
          }}</span>
          <el-button
            text
            type="danger"
            :icon="Delete"
            :disabled="loading || saving || applying"
            @click="removeProfile"
            >{{ t('plugins.configUi.deleteProfile') }}</el-button
          >
        </div>
        <p class="profile-help">{{ t('plugins.configUi.inheritHint') }}</p>
      </div>
      <template #footer>
        <div class="profile-dialog-actions">
          <el-button @click="profilesOpen = false">{{ t('common.close') }}</el-button>
          <el-button
            v-if="virtualDefault(selected || '')"
            type="primary"
            :disabled="!saveEnabled || applying"
            :loading="saving"
            @click="saveOnly"
            >{{ t('plugins.configUi.saveAsProfile') }}</el-button
          >
          <el-button
            v-else-if="selected && selected !== active"
            type="primary"
            :disabled="!canSave || dirty || applying"
            @click="activate"
            >{{ t('plugins.configUi.activateProfile') }}</el-button
          >
        </div>
      </template>
    </el-dialog>

    <div ref="workspace" class="config-workspace">
      <nav
        v-if="current?.loaded && sectionNames.length"
        ref="navigation"
        class="config-nav"
        :aria-label="t('plugins.configUi.jumpSection')"
      >
        <div class="config-nav-title">{{ t('plugins.configUi.jumpSection') }}</div>
        <button
          v-for="name in sectionNames"
          :key="name"
          type="button"
          :class="{ 'is-active': activeSection === name }"
          :aria-current="activeSection === name ? 'location' : undefined"
          :disabled="!visibleSections.includes(name)"
          @click="jumpSection(name)"
        >
          <span>{{ name }}</span>
          <small v-if="sectionChanges(name)">{{ sectionChanges(name) }}</small>
        </button>
      </nav>
      <div
        ref="contentScroll"
        class="config-content"
        tabindex="0"
        :aria-label="t('plugins.configUi.configData')"
        @scroll="updateActiveSection"
      >
        <el-alert
          v-if="visibleError"
          :title="visibleError"
          type="error"
          show-icon
          :closable="false"
          class="config-error"
        />
        <el-skeleton v-if="loading || current?.loading" :rows="6" animated />
        <template v-else-if="current?.loaded">
          <div v-if="search || filter !== 'all'" class="search-scope">
            {{ t('plugins.configUi.searchScope') }}
            <el-button text size="small" @click="clearFilters">{{
              t('plugins.configUi.clearFilters')
            }}</el-button>
          </div>
          <el-empty
            v-if="!hasVisibleFields"
            :description="t('plugins.configUi.emptySearch')"
            :image-size="65"
          >
            <el-button @click="clearFilters">{{ t('plugins.configUi.clearFilters') }}</el-button>
          </el-empty>
          <PluginConfigForm
            v-show="hasVisibleFields"
            :key="pluginId + ':' + selected"
            :model-value="current.draft"
            :baseline-value="base"
            :search="search"
            :filter="filter"
            :changes="changes"
            @update:model-value="updateDraft"
            @undo="undoField"
          />
        </template>
      </div>
    </div>

    <el-dialog
      v-model="reviewOpen"
      :title="t('plugins.configUi.unsaved') + ' · ' + changes.length"
      width="min(900px, 94vw)"
      append-to-body
    >
      <section class="change-review" :aria-label="t('plugins.configUi.reviewChanges')">
        <p class="data-hint">{{ t('plugins.configUi.reviewHint') }}</p>
        <p v-if="!changes.length" class="no-changes">{{ t('plugins.configUi.noChanges') }}</p>
        <div v-for="change in changes" :key="JSON.stringify(change.path)" class="change-row">
          <code>{{ change.path.join('.') }}</code>
          <div>
            <small>{{ t('plugins.configUi.beforeSave') }}</small>
            <pre>{{ configValueText(configValueAt(originalPreview, change.path)) }}</pre>
          </div>
          <div>
            <small>{{ t('plugins.configUi.draftValue') }}</small>
            <pre>{{ configValueText(configValueAt(preview, change.path)) }}</pre>
            <span class="change-intent">{{
              t(
                change.afterPresent
                  ? 'plugins.configUi.explicitValue'
                  : 'plugins.configUi.restoreInheritance'
              )
            }}</span>
          </div>
        </div>
      </section>
    </el-dialog>

    <footer v-if="current?.loaded" ref="footer" class="config-footer">
      <div class="footer-status" role="status" aria-live="polite">
        <strong
          ><span class="status-dot" :class="{ dirty }" />{{
            dirty
              ? t('plugins.configUi.unsavedCount', { count: changes.length })
              : virtualDefault(selected || '')
                ? t('plugins.configUi.usingBase')
                : t('plugins.configUi.noChanges')
          }}</strong
        ><small v-if="dirty || otherDirtyCount"
          >{{ t('plugins.configUi.saveScope', { name: selected })
          }}<template v-if="otherDirtyCount">
            · {{ t('plugins.configUi.otherDrafts', { count: otherDirtyCount }) }}</template
          ></small
        >
        <p
          v-if="!dirty && (applicationNotice || pendingApplication)"
          class="apply-status"
          :class="{ pending: pendingApplication }"
        >
          {{ applicationNotice || t('plugins.configUi.pendingApply') }}
        </p>
      </div>
      <div class="footer-actions">
        <el-button v-if="dirty || reviewOpen" text @click="reviewOpen = !reviewOpen">{{
          t('plugins.configUi.reviewChanges')
        }}</el-button>
        <el-button v-if="dirty" text :disabled="saving || applying" @click="undoAll">{{
          t('plugins.configUi.discardChanges')
        }}</el-button>
        <template v-if="pendingApplication && !dirty">
          <el-button
            v-if="pendingForActive"
            :disabled="loading || saving || applying"
            @click="hotUpdate"
            >{{ t('plugins.hotUpdate') }}</el-button
          >
          <el-button
            type="primary"
            :loading="applying"
            :disabled="loading || saving || !active"
            @click="reloadSaved"
            >{{ t('plugins.reloadPlugin') }}</el-button
          >
        </template>
        <el-button
          v-if="selected === active && dirty"
          :disabled="!saveEnabled || applying"
          :loading="applying"
          @click="saveAndReload"
          >{{ t('plugins.configUi.saveReload') }}</el-button
        >
        <el-button
          v-if="dirty || !pendingForActive"
          type="primary"
          :icon="Check"
          :loading="saving"
          :disabled="!saveEnabled || !dirty || applying"
          @click="saveOnly"
          >{{ t('plugins.configUi.saveProfile') }}</el-button
        >
      </div>
    </footer>

    <el-dialog
      v-model="dataOpen"
      :title="t('plugins.configUi.configData')"
      width="min(900px, 94vw)"
      append-to-body
    >
      <div class="config-meta">
        <p v-if="configPath">
          <strong>{{ t('plugins.configPath') }}</strong
          ><code>{{ configPath }}</code>
        </p>
        <p v-if="lastModified">{{ t('plugins.lastModified') }}: {{ lastModified }}</p>
      </div>
      <p class="data-hint">{{ t('plugins.configUi.dataHint') }}</p>
      <el-tabs v-model="dataTab"
        ><el-tab-pane :label="t('plugins.configUi.resolvedConfig')" name="effective" /><el-tab-pane
          :label="t('plugins.configUi.draftOverlay')"
          name="draft" /><el-tab-pane :label="t('plugins.configUi.previewConfig')" name="preview"
      /></el-tabs>
      <pre class="config-json">{{ dataJson }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, toRef, useId, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useEventListener, useResizeObserver } from '@vueuse/core'
import {
  ArrowDown,
  Check,
  Delete,
  Document,
  MoreFilled,
  Plus,
  Refresh,
  Search,
  Setting,
} from '@element-plus/icons-vue'
import { getPluginConfig, hotUpdatePluginConfig } from '@/api/config'
import { usePluginStore } from '@/stores/plugin'
import { useConfigEditorLayout } from '@/composables/useConfigEditorLayout'
import { usePluginConfigDrafts } from '@/composables/usePluginConfigDrafts'
import {
  applyProfileOverlay,
  configuredFieldCount,
  configNodeMatches,
  configValueAt,
  configValueText,
  deepClone,
  type ConfigFilter,
} from '@/utils/configEditor'
import { isRequestTimeout } from '@/utils/request'
import { isAxiosError } from 'axios'
import PluginConfigForm from './PluginConfigForm.vue'

const props = defineProps<{ pluginId: string }>()
const emit = defineEmits<{ (event: 'layout-mode-change', pageScroll: boolean): void }>()
const { t } = useI18n()
const pluginStore = usePluginStore()
const drafts = usePluginConfigDrafts(toRef(props, 'pluginId'))
const {
  base,
  effective,
  profiles,
  selected,
  active,
  current,
  names,
  loading,
  saving,
  error,
  configPath,
  lastModified,
  changes,
  dirty,
  anyDirty,
  canSave,
  pendingApplication,
  setPendingApplication,
  virtualDefault,
  dirtyCount,
  selectProfile,
  updateDraft,
  undoAll,
  undoField,
} = drafts
const search = ref('')
const filter = ref<ConfigFilter>('all')
const reviewOpen = ref(false)
const profilesOpen = ref(false)
const editorId = 'plugin-config-' + useId().replace(/[^a-zA-Z0-9_-]/g, '-')
function toolbarCommand(command: string) {
  if (command === 'data') dataOpen.value = true
  else if (command === 'refresh') void refresh()
}
const dataOpen = ref(false)
const dataTab = ref('effective')
const applying = ref(false)
const operationError = ref<string | null>(null)
const applicationNotice = ref('')
let alive = true
const container = ref<HTMLElement | null>(null)
const contentScroll = ref<HTMLElement | null>(null)
const activeSection = ref('')
const toolbar = ref<HTMLElement | null>(null)
const workspace = ref<HTMLElement | null>(null)
const navigation = ref<HTMLElement | null>(null)
const footer = ref<HTMLElement | null>(null)
const { pageScroll, scrollContainer, scrollToElement, resetScroll } = useConfigEditorLayout({
  editor: container,
  content: contentScroll,
  toolbar,
  workspace,
  navigation,
  footer,
})
watch(pageScroll, (value) => emit('layout-mode-change', value), { flush: 'sync' })
function sectionRows() {
  return [
    ...(contentScroll.value?.querySelectorAll<HTMLElement>('.cve.is-root > .obj > .row') || []),
  ].filter((row) => row.getClientRects().length > 0)
}
function updateActiveSection() {
  const pane = scrollContainer()
  if (!pane) return
  const rows = sectionRows()
  const top = pane.getBoundingClientRect().top
  const current =
    rows.filter((row) => row.getBoundingClientRect().top <= top + 24).at(-1) || rows[0]
  const atBottom =
    pane.scrollHeight > pane.clientHeight &&
    pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2
  activeSection.value = (atBottom ? rows.at(-1) : current)?.dataset.configPath || ''
}
useResizeObserver(contentScroll, updateActiveSection)
useEventListener(
  document,
  'scroll',
  () => {
    if (pageScroll.value) updateActiveSection()
  },
  { capture: true, passive: true }
)
const configuredCount = computed(() =>
  current.value && Object.keys(current.value.draft).length
    ? configuredFieldCount(current.value.draft, true)
    : 0
)
const sectionNames = computed(() => {
  const names = new Set([...Object.keys(base.value), ...Object.keys(current.value?.draft || {})])
  names.delete('plugin')
  // Match the editor order, including the runtime section pinned at the end.
  if (names.delete('plugin_runtime')) names.add('plugin_runtime')
  return [...names]
})
const sectionChanges = (name: string) =>
  changes.value.filter((change) => change.path[0] === name).length
const visibleSections = computed(() =>
  sectionNames.value.filter((name) =>
    configNodeMatches(
      current.value?.draft[name],
      base.value[name],
      [name],
      search.value,
      filter.value,
      changes.value
    )
  )
)
async function jumpSection(name: string) {
  await nextTick()
  const target = sectionRows().find((row) => row.dataset.configPath === name)
  if (target) scrollToElement(target, true)
  updateActiveSection()
}
watch([visibleSections, selected, () => props.pluginId, loading], async () => {
  await nextTick()
  updateActiveSection()
})
watch([selected, () => props.pluginId], resetScroll)
const filterOptions = computed(() => [
  { value: 'all' as const, label: t('plugins.configUi.all') },
  { value: 'dirty' as const, label: t('plugins.configUi.unsaved'), count: changes.value.length },
  {
    value: 'configured' as const,
    label: t('plugins.configUi.configured'),
    count: configuredCount.value,
  },
])
const hasVisibleFields = computed(() =>
  configNodeMatches(
    current.value?.draft || {},
    base.value,
    [],
    search.value,
    filter.value,
    changes.value
  )
)
const visibleError = computed(() => error.value || current.value?.error || operationError.value)
const saveEnabled = computed(
  () => canSave.value && (dirty.value || virtualDefault(selected.value || ''))
)
const otherDirtyCount = computed(
  () => names.value.filter((n) => n !== selected.value && dirtyCount(n)).length
)
const pendingForActive = computed(
  () => pendingApplication.value && !!selected.value && selected.value === active.value
)
const preview = computed(() => applyProfileOverlay(base.value, current.value?.draft || {}))
const originalPreview = computed(() =>
  applyProfileOverlay(base.value, current.value?.original || {})
)
const dataJson = computed(() =>
  JSON.stringify(
    dataTab.value === 'effective'
      ? effective.value
      : dataTab.value === 'draft'
        ? current.value?.draft || {}
        : preview.value,
    null,
    2
  )
)
const clearFilters = () => {
  search.value = ''
  filter.value = 'all'
}
const errorText = (err: unknown) => (err instanceof Error ? err.message : t('common.error'))

async function confirmDiscard(): Promise<boolean> {
  if (!anyDirty.value) return true
  try {
    await ElMessageBox.confirm(t('plugins.configUi.discardPrompt'), t('common.warning'), {
      type: 'warning',
    })
    return true
  } catch {
    return false
  }
}
async function refresh() {
  if (!(await confirmDiscard())) return
  operationError.value = null
  applicationNotice.value = ''
  await drafts.loadAll(true)
}
async function addProfile() {
  const id = props.pluginId
  try {
    const { value } = await ElMessageBox.prompt(
      t('plugin.addProfile.prompt'),
      t('plugin.addProfile.title'),
      {
        inputPattern: /^(?!\s*$).+/u,
        inputErrorMessage: t('plugin.addProfile.inputError'),
      }
    )
    const name = String(value || '').trim()
    if (!alive || id !== props.pluginId || !name) return
    if (names.value.includes(name) && !virtualDefault(name)) {
      ElMessage.error(t('plugin.addProfile.inputError'))
      return
    }
    // A virtual default stops being listed when the first real profile is
    // created. Do not orphan its unsaved draft in an invisible cache entry.
    const discardVirtual =
      name !== 'default' && virtualDefault('default') && dirtyCount('default') > 0
    if (discardVirtual && !(await confirmDiscard())) return
    // A virtual default is materialized by persisting its current draft, which
    // would otherwise be replaced by the empty profile written below.
    if (name === 'default' && virtualDefault('default')) {
      const saved = await drafts.saveProfile()
      if (alive && id === props.pluginId && saved)
        ElMessage.success(t('plugins.configUi.configStored', { name: saved }))
      return
    }
    if (!alive || id !== props.pluginId) return
    await drafts.createProfile(name)
    if (alive && id === props.pluginId && discardVirtual && names.value.includes(name))
      drafts.records.delete('default')
  } catch (err) {
    if (alive && id === props.pluginId && err !== 'cancel' && err !== 'close')
      operationError.value = errorText(err)
  }
}
async function removeProfile() {
  const id = props.pluginId,
    name = selected.value
  if (!name || virtualDefault(name)) return
  try {
    await ElMessageBox.confirm(
      t('plugin.removeProfile.confirm', { name }) +
        (dirtyCount(name) ? '\n' + t('plugins.configUi.discardPrompt') : ''),
      t('plugin.removeProfile.title'),
      { type: 'warning' }
    )
    if (!alive || id !== props.pluginId || name !== selected.value) return
    await drafts.deleteProfile(name)
    if (!alive || id !== props.pluginId || name !== selected.value) return
    ElMessage.success(t('common.success'))
  } catch (err) {
    if (alive && id === props.pluginId && err !== 'cancel' && err !== 'close')
      operationError.value = errorText(err)
  }
}
async function activate() {
  if (!selected.value || dirty.value) return
  const name = selected.value,
    id = props.pluginId
  operationError.value = null
  try {
    await drafts.activateProfile(name)
    if (!alive || id !== props.pluginId) return
    if (active.value !== name)
      ElMessage.warning(t('plugins.configUi.activationUnchanged', { name: active.value || '—' }))
  } catch (err) {
    if (alive && id === props.pluginId) operationError.value = errorText(err)
  }
}
async function saveOnly() {
  operationError.value = null
  applicationNotice.value = ''
  const name = await drafts.saveProfile()
  if (name) ElMessage.success(t('plugins.configUi.configStored', { name }))
  return name
}
async function saveAndReload() {
  const id = props.pluginId
  const name = await saveOnly()
  if (alive && id === props.pluginId && name && name === active.value && name === selected.value)
    await reloadSaved()
}
async function reloadSaved() {
  // Reloading applies the persisted active profile, whatever is being viewed.
  const id = props.pluginId,
    name = active.value
  if (!name || applying.value) return
  applying.value = true
  operationError.value = null
  try {
    await pluginStore.reload(id)
    if (!alive || id !== props.pluginId) return
    setPendingApplication(false)
    applicationNotice.value = t('plugins.configUi.reloadComplete')
    await drafts.loadAll()
  } catch (err) {
    if (alive && id === props.pluginId)
      operationError.value =
        isAxiosError(err) && isRequestTimeout(err)
          ? t('plugins.configUi.applyUnconfirmed')
          : errorText(err)
  } finally {
    if (alive && id === props.pluginId) applying.value = false
  }
}
async function hotUpdate() {
  const id = props.pluginId,
    name = selected.value
  if (!name || name !== active.value || !current.value?.loaded || dirty.value || applying.value)
    return
  applying.value = true
  operationError.value = null
  try {
    // The profile is already persisted. Permanent hot updates write the shared
    // base, so apply the server-resolved saved config only to the running plugin.
    const saved = await getPluginConfig(id)
    if (!alive || id !== props.pluginId || name !== selected.value || name !== active.value) return
    const config = deepClone(saved.config)
    delete config.plugin
    const result = await hotUpdatePluginConfig(id, config, 'temporary', name)
    if (!alive || id !== props.pluginId) return
    if (!result.success) {
      operationError.value = result.message || t('plugins.hotUpdateFailed')
      return
    }
    applicationNotice.value = result.hot_reloaded
      ? t('plugins.configUi.hotRequested')
      : t('plugins.hotUpdatePartial')
    // Keep the pending marker: the host merges this payload into its live
    // configuration, so removed or replaced tables stay live until a reload.
    // The backend also reports success when the plugin never acknowledged.
    await drafts.loadAll()
  } catch (err) {
    if (alive && id === props.pluginId) operationError.value = errorText(err)
  } finally {
    if (alive && id === props.pluginId) applying.value = false
  }
}

watch([() => props.pluginId, selected], () => {
  clearFilters()
  reviewOpen.value = false
  operationError.value = null
  applicationNotice.value = ''
})
watch(
  () => props.pluginId,
  () => {
    applying.value = false
  }
)
onBeforeRouteLeave(() => confirmDiscard())
onBeforeRouteUpdate((to, from) => (to.params.id === from.params.id ? true : confirmDiscard()))
function beforeUnload(event: BeforeUnloadEvent) {
  if (anyDirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => {
  alive = false
  window.removeEventListener('beforeunload', beforeUnload)
})
</script>

<style scoped>
.plugin-config-editor {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 4px 0 0;
  min-width: 0;
  container-type: inline-size;
  container-name: config-editor;
  color: var(--el-text-color-primary);
}
.config-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0 12px;
  flex-shrink: 0;
}
.config-toolbar > .el-input {
  width: 300px;
  max-width: 45%;
}
.config-toolbar :deep(.el-input__wrapper) {
  min-height: 36px;
  border-radius: 8px;
}
.toolbar-spacer {
  flex: 1;
}
.trailing-icon {
  margin-left: 8px;
  font-size: 11px;
}
.profile-name {
  margin-left: 9px;
  font-weight: 600;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.filter-active {
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.filter-count {
  margin-left: 12px;
  color: var(--el-text-color-secondary);
}
:global(.profile-manager-dialog) {
  padding: 24px;
  border-radius: 12px;
}
:global(.profile-manager-dialog .el-dialog__header) {
  padding: 0 28px 22px 0;
}
:global(.profile-manager-dialog .el-dialog__title) {
  font-size: 18px;
  font-weight: 600;
  line-height: 26px;
}
:global(.profile-manager-dialog .el-dialog__headerbtn) {
  top: 16px;
  right: 16px;
}
:global(.profile-manager-dialog .el-dialog__footer) {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.profile-label {
  display: block;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}
.profile-picker-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
}
.profile-picker-row :deep(.el-select__wrapper) {
  min-height: 40px;
  border-radius: 6px;
}
.profile-picker-row > .el-button,
.profile-dialog-actions > .el-button {
  height: 40px;
  margin: 0;
  border-radius: 6px;
}
.profile-current-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
.active-profile {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.profile-current-row > .el-button {
  flex-shrink: 0;
  padding-right: 0;
}
.profile-help {
  margin: 16px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
.profile-dialog-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}
@media (max-width: 420px) {
  .profile-picker-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
.config-workspace {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 20px;
  overflow: hidden;
}
.config-nav {
  flex: 0 0 180px;
  max-width: 100%;
  min-width: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  border-right: 1px solid var(--el-border-color-lighter);
  padding: 8px 12px 12px 0;
}
.config-nav-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 4px 10px 12px;
}
.config-nav button {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  text-align: left;
  border: 0;
  border-radius: 6px;
  padding: 10px;
  background: transparent;
  color: var(--el-text-color-regular);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.config-nav button span {
  min-width: 0;
  overflow-wrap: anywhere;
  flex: 1;
}
.config-nav button small {
  flex-shrink: 0;
}
.config-nav button:hover:not(:disabled),
.config-nav button.is-active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.config-nav button:disabled {
  opacity: 0.45;
  cursor: default;
}
.config-nav button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: -2px;
}
.config-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  padding: 4px 8px 16px 0;
  container-type: inline-size;
  container-name: config-fields;
}
@container config-editor (max-width: 760px) {
  .config-workspace {
    flex-direction: column;
    gap: 8px;
  }
  .config-nav {
    flex: 0 0 auto;
    display: flex;
    max-height: 88px;
    padding: 0 0 8px;
    border-right: 0;
    border-bottom: 1px solid var(--el-border-color-lighter);
    overflow: auto;
  }
  .config-nav-title {
    display: none;
  }
  .config-nav button {
    width: auto;
    flex-shrink: 0;
    max-width: 180px;
  }
}

.config-error {
  margin-bottom: 20px;
}
.search-scope {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.config-footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 0;
  flex-shrink: 0;

  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color-lighter);
}
.footer-status {
  min-width: 0;
}
.footer-status strong {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 400;
  font-size: 13px;
}
.footer-status small {
  display: block;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--el-color-info);
}
.status-dot.dirty {
  background: var(--el-color-primary);
}
.apply-status {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 6px 0 0;
  max-width: 480px;
}
.apply-status.pending {
  color: var(--el-color-warning);
}
.footer-actions {
  min-width: 0;
  max-width: 100%;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.footer-actions .el-button {
  max-width: 100%;
  height: auto;
  min-height: 32px;
  white-space: normal;
  margin-left: 0;
  border-radius: 7px;
}
.change-review {
  max-height: 55vh;
  overflow: auto;
}
.change-review header {
  position: sticky;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: var(--el-bg-color);
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.change-review h3 {
  margin: 0;
  font-size: 14px;
}
.change-review h3 span {
  color: var(--el-color-primary);
  margin-left: 5px;
}
.change-review header p,
.no-changes {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin: 6px 0;
}
.change-row {
  display: grid;
  grid-template-columns: minmax(130px, 1fr) minmax(160px, 1fr) minmax(160px, 1fr);
  gap: 14px;
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}
.change-row code {
  font-size: 12px;
  overflow-wrap: anywhere;
}
.change-row small {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.change-row pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 180px;
  overflow: auto;
  font: 12px/1.6 monospace;
  margin: 5px 0;
}
.change-intent {
  font-size: 11px;
  color: var(--el-color-primary);
}
.config-meta {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.config-meta strong {
  margin-right: 10px;
}
.config-meta code {
  overflow-wrap: anywhere;
}
.data-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.config-json {
  background: var(--el-fill-color-extra-light);
  padding: 14px;
  border-radius: 6px;
  max-height: 55vh;
  overflow: auto;
  font: 12px/1.6 monospace;
  white-space: pre;
}
@container config-editor (max-width: 660px) {
  .config-toolbar {
    flex-wrap: wrap;
    gap: 6px;
  }
  .config-toolbar > .el-input {
    max-width: none;
    width: calc(100% - 100px);
    flex: 1;
  }
  .toolbar-spacer {
    flex-basis: 100%;
  }
  .profile-entry {
    max-width: calc(100% - 40px);
    margin-left: -12px;
  }
  .config-footer {
    gap: 10px;
  }
  .footer-actions {
    flex: 1 1 100%;
    justify-content: flex-end;
  }
}
@media (max-width: 680px) {
  .change-row {
    grid-template-columns: 1fr 1fr;
  }
  .change-row code {
    grid-column: 1 / -1;
  }
}
.plugin-config-editor.page-scroll {
  height: auto;
  overflow: visible;
}
.page-scroll .config-workspace {
  flex: none;
  overflow: visible;
}
.page-scroll .config-content {
  overflow: visible;
}
.page-scroll .config-nav {
  max-height: 200px;
  max-width: 100%;
}
.page-scroll .config-footer {
  overflow: visible;
}
</style>
