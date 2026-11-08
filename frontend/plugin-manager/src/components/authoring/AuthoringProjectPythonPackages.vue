<template>
  <div class="python-packages" data-testid="project-dependencies">
    <el-alert v-if="error" :title="errorText" type="error" :closable="false" show-icon>
      <details v-if="diagnostic" class="authoring-diagnostic">
        <summary>{{ t('pythonPackages.technical') }}</summary>
        <pre>{{ diagnostic }}</pre>
      </details>
    </el-alert>
    <el-skeleton v-if="!snapshot && loading" :rows="5" animated />
    <template v-if="snapshot">
      <details class="python-packages-environment">
        <summary>
          {{ t('pythonPackages.environment') }} · Python {{ snapshot.environment.python_version }} ·
          {{ snapshot.environment.platform }} {{ snapshot.environment.architecture }}
        </summary>
        <p>{{ t('pythonPackages.installer') }}: {{ snapshot.environment.installer || '—' }}</p>
        <p>
          {{ t('pythonPackages.vendor') }}: <code>{{ snapshot.vendor_path }}</code>
        </p>
      </details>
      <el-alert
        v-if="!snapshot.environment.can_sync"
        :title="t('pythonPackages.unavailable')"
        type="warning"
        :closable="false"
        show-icon
      >
        <details v-if="snapshot.environment.reason">
          <summary>{{ t('pythonPackages.technical') }}</summary>
          {{ snapshot.environment.reason }}
        </details>
      </el-alert>
      <el-alert
        v-if="snapshot.environment.setup_required"
        :title="t('pythonPackages.setupRequired')"
        type="info"
        :closable="false"
        show-icon
      />
      <el-alert
        v-if="snapshot.running"
        :title="t('pythonPackages.running')"
        type="info"
        :closable="false"
        show-icon
      >
        <el-button v-if="project.plugin_id" :loading="stopping" :disabled="busy" @click="stop">{{
          t('pythonPackages.stop')
        }}</el-button>
      </el-alert>
      <el-alert
        v-if="snapshot.operation && !active"
        :title="t('pythonPackages.busy')"
        type="info"
        :closable="false"
        show-icon
      />
      <div class="python-packages-actions">
        <el-button :icon="Plus" :disabled="busy" @click="openEditor()">{{
          t('pythonPackages.add')
        }}</el-button>
        <el-button
          type="primary"
          :disabled="!canSync || dirty"
          :loading="active && task?.operation === 'sync'"
          @click="run('sync')"
          >{{ t('pythonPackages.sync') }}</el-button
        >
        <el-button :disabled="busy || dirty" @click="run('check')">{{
          t('pythonPackages.check')
        }}</el-button>
        <el-button :icon="Refresh" :loading="loading" @click="reload()">{{
          t('common.refresh')
        }}</el-button>
      </div>
      <section class="authoring-details-panel">
        <h3>{{ t('pythonPackages.requirements') }} ({{ draft.length }})</h3>
        <p v-if="!draft.length" class="authoring-muted">{{ t('pythonPackages.noRequirements') }}</p>
        <ul v-else class="python-package-requirements">
          <li v-for="(requirement, index) in draft" :key="`${index}:${requirement}`">
            <div class="python-package-declaration">
              <code>{{ requirement }}</code>
              <span v-if="savedRequirement(requirement)" class="authoring-small">
                <el-tag size="small" :type="statusType(savedRequirement(requirement)!.status)">{{
                  statusLabel(savedRequirement(requirement)!.status)
                }}</el-tag>
                {{ savedRequirement(requirement)!.installed_version || '' }}
              </span>
            </div>
            <div class="python-packages-actions">
              <el-button
                text
                :disabled="busy || !editable(requirement)"
                @click="openEditor(index)"
                >{{ t('common.edit') }}</el-button
              >
              <el-button v-if="canPin(requirement)" text :disabled="busy" @click="pin(index)">{{
                t('pythonPackages.pin')
              }}</el-button>
              <el-button text type="danger" :disabled="busy" @click="remove(index)">{{
                t('pythonPackages.remove')
              }}</el-button>
            </div>
          </li>
        </ul>
        <p class="authoring-small authoring-muted">{{ t('pythonPackages.removeHint') }}</p>
        <p
          v-if="snapshot.requirements.some((item) => !item.editable)"
          class="authoring-small authoring-muted"
        >
          {{ t('pythonPackages.directSource') }}
        </p>
        <el-alert v-if="dirty" :title="t('pythonPackages.unsaved')" type="info" :closable="false" />
        <div v-if="dirty" class="python-packages-actions">
          <el-button :loading="saving" :disabled="busy" @click="save()">{{
            t('pythonPackages.save')
          }}</el-button>
          <el-button type="primary" :disabled="!canSync" @click="save(true)">{{
            t('pythonPackages.saveSync')
          }}</el-button>
          <el-button :disabled="busy" @click="discard">{{ t('pythonPackages.reload') }}</el-button>
        </div>
      </section>
      <section class="authoring-details-panel">
        <h3>{{ t('pythonPackages.installed') }} ({{ snapshot.installed.length }})</h3>
        <div class="python-packages-filters">
          <el-input
            v-model="search"
            :placeholder="t('pythonPackages.search')"
            :aria-label="t('pythonPackages.search')"
            clearable
          />
          <el-select v-model="filter" :aria-label="t('pythonPackages.installed')">
            <el-option
              v-for="kind in ['all', 'direct', 'other'] as const"
              :key="kind"
              :value="kind"
              :label="t(`pythonPackages.${kind}`)"
            />
          </el-select>
        </div>
        <p class="authoring-small authoring-muted">{{ t('pythonPackages.otherHint') }}</p>
        <p v-if="!filteredPackages.length" class="authoring-muted">
          {{ t('pythonPackages.empty') }}
        </p>
        <details v-for="pkg in filteredPackages" :key="pkg.name" class="python-package-details">
          <summary>
            <strong>{{ pkg.name }}</strong> <code>{{ pkg.version }}</code> ·
            {{ t(pkg.direct ? 'pythonPackages.direct' : 'pythonPackages.other') }}
          </summary>
          <p v-if="pkg.summary">{{ pkg.summary }}</p>
          <p>
            {{ t('pythonPackages.requiresPython') }}: <code>{{ pkg.requires_python || '—' }}</code>
          </p>
          <h4>{{ t('pythonPackages.requiresDist') }}</h4>
          <ul>
            <li v-for="req in pkg.requires_dist" :key="req">
              <code>{{ req }}</code>
            </li>
          </ul>
        </details>
      </section>
      <section v-if="task" class="authoring-details-panel" aria-live="polite">
        <h3>
          {{ t('pythonPackages.task') }} ·
          {{ t(`pythonPackages.${task.status === 'running' ? 'runningTask' : task.status}`) }}
        </h3>
        <div class="python-packages-actions">
          <span v-if="active"
            >{{ phaseLabel }} · {{ t('pythonPackages.elapsed', { seconds: elapsed }) }}</span
          >
          <el-button v-if="active" :loading="saving" @click="cancel">{{
            t('pythonPackages.cancel')
          }}</el-button>
        </div>
        <details v-if="task.message">
          <summary>{{ t('pythonPackages.technical') }}</summary>
          <pre>{{ task.message }}</pre>
        </details>
        <details :open="active || task.status === 'failed'">
          <summary>{{ t('pythonPackages.log') }}</summary>
          <pre class="python-package-log">{{ task.log.join('\n') || '—' }}</pre>
        </details>
        <div v-if="task.result?.changes?.length">
          <h4>{{ t('pythonPackages.changes') }}</h4>
          <ul>
            <li v-for="change in task.result.changes" :key="change.name">
              <strong>{{ change.name }}</strong> ·
              {{
                t(
                  !change.before
                    ? 'pythonPackages.added'
                    : !change.after
                      ? 'pythonPackages.removed'
                      : 'pythonPackages.updated'
                )
              }}: {{ change.before || '—' }} → {{ change.after || '—' }}
            </li>
          </ul>
        </div>
      </section>
      <section class="authoring-details-panel">
        <h3>{{ t('pythonPackages.checks') }}</h3>
        <ul v-if="inspection.issues.length">
          <li v-for="(issue, index) in inspection.issues" :key="index">
            {{ issueLabel(issue) }} <code>{{ issue.package }} {{ issue.requirement }}</code>
            <details v-if="issue.message">
              <summary>{{ t('pythonPackages.technical') }}</summary>
              <pre>{{ issue.message }}</pre>
            </details>
          </li>
        </ul>
        <p v-else>{{ t(checked ? 'pythonPackages.noFindings' : 'pythonPackages.notChecked') }}</p>
      </section>
    </template>
    <el-dialog
      v-model="editorVisible"
      :title="t(editorIndex === null ? 'pythonPackages.add' : 'pythonPackages.edit')"
      width="min(560px, 94vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="!saving"
      :show-close="!saving"
    >
      <form class="authoring-form" @submit.prevent="applyEdit(true)">
        <el-alert
          v-if="editorError || error"
          :title="editorError ? t(`pythonPackages.${editorError}`) : errorText"
          type="error"
          :closable="false"
          show-icon
        />
        <template v-if="!fullDeclaration">
          <label for="python-package-name"
            >{{ t('pythonPackages.packageName')
            }}<el-input
              id="python-package-name"
              v-model="packageName"
              :placeholder="t('pythonPackages.nameExample')"
              :disabled="saving"
              @paste="pastePackageInput"
          /></label>
          <label for="python-package-version-policy"
            >{{ t('pythonPackages.versionChoice') }}
            <el-select
              id="python-package-version-policy"
              v-model="versionPolicy"
              :disabled="saving"
              @change="changeVersionPolicy"
            >
              <el-option value="auto" :label="t('pythonPackages.versionAuto')" />
              <el-option value="exact" :label="t('pythonPackages.versionExact')" />
              <el-option value="custom" :label="t('pythonPackages.versionCustom')" />
            </el-select>
          </label>
          <label v-if="versionPolicy === 'exact'" for="python-package-exact-version"
            >{{ t('pythonPackages.versionNumber') }}
            <el-input
              id="python-package-exact-version"
              v-model="exactVersion"
              :placeholder="t('pythonPackages.versionExample')"
              :disabled="saving"
            />
          </label>
        </template>
        <details
          :open="advancedOpen"
          @toggle="advancedOpen = ($event.target as HTMLDetailsElement).open"
        >
          <summary>{{ t('pythonPackages.advanced') }}</summary>
          <div class="python-package-advanced">
            <el-checkbox
              v-model="fullDeclaration"
              :disabled="saving"
              @change="changeDeclarationMode"
              >{{ t('pythonPackages.manualDeclaration') }}</el-checkbox
            >
            <template v-if="!fullDeclaration">
              <label v-if="versionPolicy === 'custom'" for="python-package-version"
                >{{ t('pythonPackages.versionConstraint') }}
                <el-input
                  id="python-package-version"
                  v-model="packageVersion"
                  placeholder=">=0.27,<1"
                  :disabled="saving"
                />
              </label>
              <label for="python-package-extras"
                >{{ t('pythonPackages.optionalFeatures')
                }}<el-input
                  id="python-package-extras"
                  v-model="packageExtras"
                  placeholder="socks"
                  :disabled="saving"
              /></label>
              <label for="python-package-marker"
                >{{ t('pythonPackages.marker')
                }}<el-input id="python-package-marker" v-model="packageMarker" :disabled="saving"
              /></label>
            </template>
            <label v-else for="python-package-requirement"
              >{{ t('pythonPackages.declaration')
              }}<el-input
                id="python-package-requirement"
                v-model="rawDeclaration"
                maxlength="4096"
                :disabled="saving"
            /></label>
          </div>
        </details>
        <p v-if="snapshot?.running" class="authoring-small authoring-muted">
          {{ t('pythonPackages.stopBeforeInstall') }}
        </p>
        <div class="python-packages-actions">
          <el-button :disabled="saving" @click="editorVisible = false">{{
            t('common.cancel')
          }}</el-button
          ><el-button :disabled="!canSubmit || busy" @click="applyEdit(false)">{{
            t('pythonPackages.saveOnly')
          }}</el-button
          ><el-button
            native-type="submit"
            type="primary"
            :loading="saving"
            :disabled="!canSubmit || !canSync"
            >{{
              t(editorIndex === null ? 'pythonPackages.addInstall' : 'pythonPackages.saveInstall')
            }}</el-button
          >
        </div>
      </form>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, toRef } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import type { AuthoringProject } from '@/api/pluginProjects'
import { projectErrorCode } from '@/api/pluginProjects'
import type { PythonPackageIssue, PythonRequirement } from '@/api/projectPythonPackages'
import { stopPlugin } from '@/api/plugins'
import { useProjectPythonPackages } from '@/composables/useProjectPythonPackages'
import { formatHttpError } from '@/utils/request'
import { parsePythonPackageInput, pythonVersionPolicy } from '@/utils/pythonPackageInput'
const props = defineProps<{ project: AuthoringProject }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const projectId = computed(() => props.project.project_id)
const {
  snapshot,
  task,
  draft,
  dirty,
  active,
  busy,
  loading,
  saving,
  error,
  clock,
  reload,
  save,
  run,
  cancel,
} = useProjectPythonPackages(toRef(projectId), () => emit('changed'))
const search = ref('')
const filter = ref<'all' | 'direct' | 'other'>('all')
const stopping = ref(false)
const editorVisible = ref(false)
const editorIndex = ref<number | null>(null)
const rawDeclaration = ref('')
const fullDeclaration = ref(false)
const packageName = ref('')
const packageVersion = ref('')
const packageExtras = ref('')
const packageMarker = ref('')
const versionPolicy = ref<'auto' | 'exact' | 'custom'>('auto')
const advancedOpen = ref(false)
const editorError = ref('')
const exactVersion = computed({
  get: () => packageVersion.value.replace(/^==/, ''),
  set: (value: string) => {
    packageVersion.value = value.trim() ? `==${value.trim()}` : ''
  },
})
const simpleDeclaration = computed(
  () =>
    packageName.value.trim() +
    (packageExtras.value.trim()
      ? '[' +
        packageExtras.value
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean)
          .join(',') +
        ']'
      : '') +
    packageVersion.value.trim() +
    (packageMarker.value.trim() ? '; ' + packageMarker.value.trim() : '')
)
const editorValue = computed(() =>
  fullDeclaration.value ? rawDeclaration.value : simpleDeclaration.value
)
const canSubmit = computed(
  () =>
    !!editorValue.value.trim() &&
    (fullDeclaration.value ||
      (!!packageName.value.trim() &&
        (versionPolicy.value !== 'exact' || !!exactVersion.value.trim())))
)
const checked = computed(
  () =>
    task.value?.status === 'succeeded' &&
    !!task.value.result?.after &&
    task.value.result.after.revision === snapshot.value?.revision &&
    JSON.stringify(task.value.result.after.installed) === JSON.stringify(snapshot.value?.installed)
)
const inspection = computed(() => (checked.value ? task.value!.result!.after : snapshot.value!))
const canSync = computed(
  () =>
    !!snapshot.value?.environment.can_sync &&
    !snapshot.value.running &&
    !busy.value &&
    !stopping.value
)
const filteredPackages = computed(
  () =>
    snapshot.value?.installed.filter(
      (pkg) =>
        pkg.name.toLowerCase().includes(search.value.toLowerCase()) &&
        (filter.value === 'all' || pkg.direct === (filter.value === 'direct'))
    ) ?? []
)
const elapsed = computed(() =>
  Math.max(
    0,
    Math.floor(
      ((task.value?.finished_at ? task.value.finished_at * 1000 : clock.value) -
        (task.value?.started_at ?? clock.value / 1000) * 1000) /
        1000
    )
  )
)
const phaseLabel = computed(() =>
  t(
    `pythonPackages.${{ preparing: 'prepare', installing: 'installing', checking: 'checking', replacing: 'replacing', refreshing: 'refreshing', finished: 'succeeded' }[task.value?.phase ?? 'preparing']}`
  )
)
const errorKeys: Record<string, string> = {
  PYTHON_PACKAGES_RUNNING: 'running',
  PYTHON_PACKAGES_TASK_NOT_FOUND: 'taskMissing',
  PYTHON_PACKAGES_REVISION_CONFLICT: 'conflict',
  PYTHON_PACKAGES_REQUIREMENTS_INVALID: 'invalidRequirement',
  PYTHON_PACKAGES_REQUIREMENT_SOURCE_UNSUPPORTED: 'sourceUnsupported',
  PYTHON_PACKAGES_FORMAT_UNSUPPORTED: 'formatUnsupported',
  PYTHON_PACKAGES_PROJECT_INVALID: 'fileFailed',
  PYTHON_PACKAGES_VENDOR_UNREADABLE: 'fileFailed',
  PYTHON_PACKAGES_WRITE_FAILED: 'fileFailed',
  PYTHON_PACKAGES_BUSY: 'busy',
  PLUGIN_OPERATION_BUSY: 'busy',
}
const errorText = computed(() => {
  const code = projectErrorCode(error.value) ?? ''
  return t(`pythonPackages.${Object.hasOwn(errorKeys, code) ? errorKeys[code] : 'actionFailed'}`)
})
const diagnostic = computed(
  () => formatHttpError(error.value) || (error.value instanceof Error ? error.value.message : '')
)
function savedRequirement(value: string) {
  return snapshot.value?.requirements.find((item) => item.requirement === value)
}
function editable(value: string) {
  return savedRequirement(value)?.editable ?? !value.includes('@')
}
function canPin(value: string) {
  const item = savedRequirement(value)
  return !!item?.editable && !!item.installed_version && item.status !== 'host_provided'
}
function statusLabel(status: PythonRequirement['status']) {
  return t(
    `pythonPackages.${{ satisfied: 'satisfied', missing: 'missing', version_mismatch: 'incompatible', not_applicable: 'inactive', host_provided: 'host', invalid_metadata: 'invalid' }[status]}`
  )
}
function statusType(status: PythonRequirement['status']): 'success' | 'warning' | 'info' {
  return status === 'satisfied'
    ? 'success'
    : ['missing', 'version_mismatch', 'invalid_metadata'].includes(status)
      ? 'warning'
      : 'info'
}
function openEditor(index: number | null = null) {
  editorIndex.value = index
  const original = index === null ? '' : (draft.value[index] ?? '')
  const parsed = parsePythonPackageInput(original)
  fullDeclaration.value = !!original && !parsed
  rawDeclaration.value = original
  packageName.value = parsed?.name ?? ''
  packageVersion.value = parsed?.specifier ?? ''
  packageExtras.value = parsed?.extras ?? ''
  packageMarker.value = parsed?.marker ?? ''
  versionPolicy.value = pythonVersionPolicy(packageVersion.value)
  advancedOpen.value =
    fullDeclaration.value ||
    versionPolicy.value === 'custom' ||
    !!packageExtras.value ||
    !!packageMarker.value
  editorError.value = ''
  error.value = null
  editorVisible.value = true
}
function changeVersionPolicy() {
  packageVersion.value = ''
  if (versionPolicy.value === 'custom') advancedOpen.value = true
}
function normalizeNameInput() {
  const parsed = parsePythonPackageInput(packageName.value)
  if (!parsed) return
  const hasOptions =
    parsed.name !== packageName.value.trim() ||
    !!parsed.specifier ||
    !!parsed.extras ||
    !!parsed.marker
  packageName.value = parsed.name
  if (hasOptions) {
    packageVersion.value = parsed.specifier
    packageExtras.value = parsed.extras
    packageMarker.value = parsed.marker
    versionPolicy.value = pythonVersionPolicy(parsed.specifier)
    if (versionPolicy.value === 'custom' || parsed.extras || parsed.marker)
      advancedOpen.value = true
  }
}
function pastePackageInput(event: ClipboardEvent) {
  const value = event.clipboardData?.getData('text') ?? ''
  const parsed = parsePythonPackageInput(value)
  if (!parsed || parsed.name === value.trim()) return
  event.preventDefault()
  packageName.value = value
  normalizeNameInput()
}
function changeDeclarationMode(value: boolean | string | number) {
  if (value) {
    rawDeclaration.value = simpleDeclaration.value
    return
  }
  if (!rawDeclaration.value.trim()) {
    packageName.value = ''
    packageVersion.value = ''
    packageExtras.value = ''
    packageMarker.value = ''
    versionPolicy.value = 'auto'
    return
  }
  const parsed = parsePythonPackageInput(rawDeclaration.value)
  if (!parsed) {
    fullDeclaration.value = true
    return
  }
  packageName.value = parsed.name
  packageExtras.value = parsed.extras
  packageVersion.value = parsed.specifier
  packageMarker.value = parsed.marker
  versionPolicy.value = pythonVersionPolicy(packageVersion.value)
}
function issueLabel(issue: PythonPackageIssue) {
  const keys: Record<string, string> = {
    missing: 'missing',
    version_mismatch: 'incompatible',
    invalid_requirement: 'invalidRequirement',
    invalid_metadata: 'invalid',
    duplicate_distribution: 'duplicateDistribution',
    python_version_mismatch: 'pythonVersionMismatch',
    dependency_missing: 'dependencyMissing',
    dependency_version_mismatch: 'dependencyVersionMismatch',
  }
  return t(
    'pythonPackages.' + (Object.hasOwn(keys, issue.code) ? keys[issue.code] : 'unknownIssue')
  )
}
async function applyEdit(install: boolean) {
  if (busy.value || (install && !canSync.value)) return
  editorError.value = ''
  if (!fullDeclaration.value) {
    normalizeNameInput()
    if (!/^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$/.test(packageName.value)) {
      editorError.value = 'invalidName'
      return
    }
    if (versionPolicy.value === 'exact' && !/^[vV]?\d[\w.!+-]*$/.test(exactVersion.value)) {
      editorError.value = 'invalidVersion'
      return
    }
  }
  const value = editorValue.value.trim()
  if (!canSubmit.value) return
  const name = parsePythonPackageInput(value)
    ?.name.toLowerCase()
    .replace(/[-_.]+/g, '-')
  if (
    name &&
    draft.value.some(
      (item, index) =>
        index !== editorIndex.value &&
        parsePythonPackageInput(item)
          ?.name.toLowerCase()
          .replace(/[-_.]+/g, '-') === name
    )
  ) {
    editorError.value = 'alreadyAdded'
    return
  }
  const previous = [...draft.value]
  const projectAtSubmit = props.project.project_id
  if (editorIndex.value === null) draft.value.push(value)
  else draft.value[editorIndex.value] = value
  const saved = await save(install)
  if (props.project.project_id !== projectAtSubmit) return
  if (saved) editorVisible.value = false
  else draft.value = previous
}
function remove(index: number) {
  draft.value.splice(index, 1)
}
function pin(index: number) {
  const item = savedRequirement(draft.value[index] ?? '')
  if (!item?.editable || !item.installed_version) return
  draft.value[index] =
    `${item.name}${item.extras.length ? `[${item.extras.join(',')}]` : ''}==${item.installed_version}${item.marker ? `; ${item.marker}` : ''}`
}
async function stop() {
  if (!props.project.plugin_id || stopping.value) return
  stopping.value = true
  try {
    await stopPlugin(props.project.plugin_id)
    await reload()
    emit('changed')
  } catch (cause) {
    error.value = cause
  } finally {
    stopping.value = false
  }
}
async function confirmDiscard() {
  if (!dirty.value && !editorVisible.value) return true
  try {
    await ElMessageBox.confirm(t('pythonPackages.discard'), t('pythonPackages.tab'), {
      type: 'warning',
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
    })
    return true
  } catch {
    return false
  }
}
async function discard() {
  if (await confirmDiscard()) {
    editorVisible.value = false
    await reload(true)
  }
}
onBeforeRouteLeave(confirmDiscard)
onBeforeRouteUpdate((to, from) =>
  to.query.project === from.query.project ? true : confirmDiscard()
)
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value || editorVisible.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
defineExpose({ reload, dirty })
</script>

<style scoped>
.python-packages {
  display: grid;
  gap: 16px;
  min-width: 0;
}
.python-packages h3 {
  margin-top: 0;
}
.python-package-advanced {
  display: grid;
  gap: 14px;
  padding-top: 14px;
}
.python-packages-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.python-packages-actions .el-button + .el-button {
  margin-left: 0;
}
.python-package-requirements {
  padding: 0;
  list-style: none;
}
.python-package-requirements > li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.python-package-declaration {
  display: grid;
  gap: 6px;
  flex: 1 1 260px;
  min-width: 0;
}
.python-packages-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.python-packages-filters .el-input {
  flex: 1 1 200px;
}
.python-packages-filters .el-select {
  width: 200px;
}
.python-package-details {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
summary {
  cursor: pointer;
  overflow-wrap: anywhere;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}
.python-package-log {
  max-height: 320px;
  overflow: auto;
  background: var(--el-fill-color-light);
  padding: 12px;
}
</style>
