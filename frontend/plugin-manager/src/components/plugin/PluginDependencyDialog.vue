<template>
  <el-dialog
    class="plugin-dependency-dialog"
    :model-value="true"
    :title="t('pythonDependencies.title')"
    width="min(620px, 94vw)"
    :close-on-click-modal="false"
    @close="close"
  >
    <template v-if="status?.state === 'awaiting_confirmation'">
      <p>{{ t('pythonDependencies.introduction', { name: request.name }) }}</p>
      <el-table :data="packages" size="small" max-height="320" class="dependency-table">
        <el-table-column prop="name" :label="t('pythonDependencies.columns.name')" min-width="180" />
        <el-table-column prop="version" :label="t('pythonDependencies.columns.version')" min-width="110" />
        <el-table-column :label="t('pythonDependencies.columns.size')" min-width="100" align="right">
          <template #default="{ row }">{{ formatSize(row.size) }}</template>
        </el-table-column>
      </el-table>
      <p class="dependency-total">
        {{
          status.size_complete === false
            ? t('pythonDependencies.totalPartial', { count: packages.length, size: formatSize(status.total_size ?? 0) })
            : t('pythonDependencies.total', { count: packages.length, size: formatSize(status.total_size ?? 0) })
        }}
      </p>
    </template>
    <template v-else-if="status?.state === 'failed'">
      <el-alert
        :title="t('pythonDependencies.failedTitle')"
        :description="t(errorKey)"
        type="error"
        :closable="false"
        show-icon
      />
      <details v-if="status.error_detail" class="dependency-detail">
        <summary>{{ t('pythonDependencies.details') }}</summary>
        <code>{{ status.error_detail }}</code>
      </details>
    </template>
    <div v-else class="dependency-progress" role="status">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>{{ t(phaseKey) }}</span>
      <p v-if="status?.state === 'installing'" class="dependency-hint">{{ t('pythonDependencies.backgroundHint') }}</p>
    </div>
    <template #footer>
      <template v-if="status?.state === 'awaiting_confirmation'">
        <el-button @click="close">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="submitting" @click="install">{{
          t('pythonDependencies.installAndStart')
        }}</el-button>
      </template>
      <template v-else-if="status?.state === 'failed'">
        <el-button @click="close">{{ t('common.close') }}</el-button>
        <el-button type="primary" :loading="submitting" @click="retry">{{ t('common.retry') }}</el-button>
      </template>
      <el-button v-else @click="close">{{ t('common.close') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Loading } from '@element-plus/icons-vue'
import {
  getPythonDependencies,
  requestPythonDependencyInstall,
  type PythonDependencyStatus,
} from '@/api/plugins'
import { usePluginDependencyInstall, type DependencyInstallRequest } from '@/stores/pluginDependencyInstall'

const props = defineProps<{ request: DependencyInstallRequest }>()
// The deferred wrapper's close listener must not fall through to ElDialog; close() owns it.
defineEmits(['close'])
const request = computed(() => props.request)
const { t } = useI18n()
const store = usePluginDependencyInstall()
const status = ref<PythonDependencyStatus | null>(null)
const submitting = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
let closed = false

const POLL_MS = 1000
const ERROR_CODES = new Set([
  'tools_failed', 'lock_invalid', 'network', 'not_on_index', 'no_wheel', 'hash_mismatch',
  'unpinned_source', 'lock_changed', 'lock_mismatch', 'install_failed', 'not_applicable',
  'canceled', 'internal_error',
])

const packages = computed(() => status.value?.packages ?? [])
const errorKey = computed(() => {
  const code = status.value?.error_code
  return `pythonDependencies.errors.${code && ERROR_CODES.has(code) ? code : 'install_failed'}`
})
const phaseKey = computed(() => {
  const phase = status.value?.phase || ''
  if (status.value?.state === 'installing') {
    if (phase.startsWith('tools')) return 'pythonDependencies.phases.tools'
    if (phase === 'committing' || phase === 'verifying') return 'pythonDependencies.phases.finishing'
    return 'pythonDependencies.phases.installing'
  }
  if (phase.startsWith('tools')) return 'pythonDependencies.phases.tools'
  return 'pythonDependencies.phases.planning'
})

function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return t('pythonDependencies.sizeUnknown')
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function schedule() {
  if (timer) clearTimeout(timer)
  timer = setTimeout(poll, POLL_MS)
}

/** A state the dialog can always leave: Retry plans again, Close gives up. */
function retryable(errorCode = 'install_failed'): PythonDependencyStatus {
  return { plugin_id: request.value.pluginId, state: 'failed', pending: true, error_code: errorCode }
}

function apply(next: PythonDependencyStatus) {
  if (closed) return
  if (next.state === null && next.pending) {
    // The task is gone (the server restarted mid-install): offer to start over.
    status.value = retryable()
    return
  }
  status.value = next
  if (next.state === 'completed' || (next.state === null && !next.pending)) {
    // Installed (here, or by an earlier task): the caller starts the plugin.
    finish(true)
  } else if (next.state === 'planning' || next.state === 'installing') {
    schedule()
  }
}

async function poll() {
  try {
    apply(await getPythonDependencies(request.value.pluginId))
  } catch {
    // The request layer already showed the error; keep the dialog and try again.
    if (!closed) schedule()
  }
}

async function submit(confirmLockSha256: string | null) {
  submitting.value = true
  try {
    apply(await requestPythonDependencyInstall(request.value.pluginId, confirmLockSha256))
  } catch {
    // Shown by the request layer. The server may still have accepted it (a
    // lost response): ask what it is doing, else let the user retry.
    try {
      apply(await getPythonDependencies(request.value.pluginId, { suppressErrorMessage: true }))
    } catch {
      if (!closed) status.value = retryable()
    }
  } finally {
    submitting.value = false
  }
}

function install() {
  void submit(status.value?.lock_sha256 ?? null)
}

function retry() {
  // A failure after the user confirmed installs the same lock again without asking;
  // a failure while planning plans again and shows the list.
  void submit(status.value?.lock_sha256 ?? null)
}

function finish(installed: boolean) {
  if (closed) return
  closed = true
  if (timer) clearTimeout(timer)
  store.finish(installed)
}

function close() {
  // Closing never stops an install that is under way; the next start picks it up.
  finish(false)
}

onMounted(async () => {
  try {
    const current = await getPythonDependencies(request.value.pluginId)
    if (current.state === 'planning' || current.state === 'installing' || current.state === 'awaiting_confirmation') {
      apply(current)
      return
    }
    if (!current.pending) {
      finish(true)
      return
    }
  } catch {
    finish(false)
    return
  }
  await submit(null)
})

onBeforeUnmount(() => {
  closed = true
  if (timer) clearTimeout(timer)
})

defineExpose({ formatSize })
</script>

<style scoped>
.dependency-table {
  width: 100%;
}
.dependency-total {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  text-align: right;
}
.dependency-progress {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.dependency-hint {
  flex-basis: 100%;
  margin: 4px 0 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.dependency-detail {
  margin-top: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.dependency-detail code {
  display: block;
  margin-top: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
