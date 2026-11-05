<template>
  <div class="authoring-publish">
    <el-alert v-if="requestError" :title="requestError" type="error" :closable="false" show-icon />
    <el-alert
      v-else-if="status?.remote?.rate_limited"
      :title="t('authoring.publishRateLimited')"
      type="warning"
      :closable="false"
      show-icon
    />
    <div class="authoring-precheck-actions">
      <el-button :icon="Refresh" :loading="loading" @click="load">
        {{ t('authoring.publishRefresh') }}
      </el-button>
      <el-button
        v-if="canStart && !releaseReady"
        type="primary"
        :icon="Position"
        :disabled="taskActive"
        @click="confirmPublish('github_and_market')"
      >
        {{ t('authoring.publishRunGithubMarket') }}
      </el-button>
      <el-button
        v-if="canStart && !releaseReady"
        :disabled="taskActive"
        @click="confirmPublish('github')"
      >
        {{ t('authoring.publishRunGithub') }}
      </el-button>
      <el-button
        v-if="canStart && releaseReady"
        type="primary"
        :icon="Position"
        :disabled="taskActive"
        @click="confirmPublish('market_notify')"
      >
        {{ t('authoring.publishRunMarket') }}
      </el-button>
      <el-button
        v-if="taskActive"
        :icon="CircleClose"
        :loading="canceling"
        @click="confirmCancel"
      >
        {{ t('authoring.publishCancelTask') }}
      </el-button>
    </div>
    <el-alert v-if="taskError" :title="taskError" type="error" :closable="false" show-icon />
    <section v-if="task" class="authoring-publish-task">
      <h4>
        <el-tag size="small" :type="TASK_TAG_TYPES[task.status]">
          {{ t(TASK_LABELS[task.status]) }}
        </el-tag>
        <span v-if="task.message" class="authoring-small">{{ task.message }}</span>
        <a
          v-if="task.status === 'succeeded' && task.release_url"
          class="authoring-small"
          href="#"
          @click.prevent="openExternalUrl(task.release_url)"
          >{{ task.release_url }}</a
        >
      </h4>
      <details v-if="task.log.length" :open="task.status === 'failed'">
        <summary>{{ t('authoring.publishTaskLog') }}</summary>
        <pre>{{ task.log.join('\n') }}</pre>
      </details>
    </section>
    <ol v-if="status" class="authoring-publish-steps">
      <li v-for="step in steps" :key="step.id" class="authoring-publish-step">
        <el-tag size="small" :type="TAG_TYPES[step.state]">{{ t(STATE_LABELS[step.state]) }}</el-tag>
        <div>
          <strong>{{ step.title }}</strong>
          <p v-if="step.detail" class="authoring-muted authoring-small">{{ step.detail }}</p>
        </div>
      </li>
    </ol>
    <ul v-if="notes.length" class="authoring-publish-notes">
      <li v-for="note in notes" :key="note" class="authoring-muted authoring-small">{{ note }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleClose, Position, Refresh } from '@element-plus/icons-vue'
import {
  cancelPluginProjectPublishTask,
  getPluginProjectPublishStatus,
  getPluginProjectPublishTask,
  projectErrorCode,
  publishTaskActive,
  startPluginProjectPublish,
  type PublishMode,
  type PublishStatus,
  type PublishTask,
  type PublishTaskStatus,
} from '@/api/pluginProjects'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'
import { openExternalUrl } from '@/utils/openExternal'

type StepState = 'done' | 'todo' | 'unknown' | 'later'
interface Step {
  id: string
  title: string
  state: StepState
  detail: string
}

const TAG_TYPES: Record<StepState, 'success' | 'warning' | 'info'> = {
  done: 'success',
  todo: 'warning',
  unknown: 'info',
  later: 'info',
}
const STATE_LABELS: Record<StepState, string> = {
  done: 'authoring.publishStatusDone',
  todo: 'authoring.publishStatusTodo',
  unknown: 'authoring.publishStatusUnknown',
  later: 'authoring.publishStatusLater',
}
const TASK_TAG_TYPES: Record<PublishTaskStatus, 'success' | 'danger' | 'warning' | 'info'> = {
  queued: 'info',
  running: 'warning',
  succeeded: 'success',
  failed: 'danger',
  canceled: 'info',
}
const TASK_LABELS: Record<PublishTaskStatus, string> = {
  queued: 'authoring.publishTaskQueued',
  running: 'authoring.publishTaskRunning',
  succeeded: 'authoring.publishTaskSucceeded',
  failed: 'authoring.publishTaskFailed',
  canceled: 'authoring.publishTaskCanceled',
}
// The CLI can run for up to 20 minutes; progress comes from polling, not the request.
const POLL_INTERVAL_MS = 3000
const CONFIRM_MODES: Record<PublishMode, string> = {
  github_and_market: 'authoring.publishConfirmGithubMarket',
  github: 'authoring.publishConfirmGithub',
  market_notify: 'authoring.publishConfirmMarket',
}

const props = defineProps<{ projectId: string }>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const loading = ref(false)
const status = ref<PublishStatus | null>(null)
const requestError = ref('')
const task = ref<PublishTask | null>(null)
const taskError = ref('')
const canceling = ref(false)
let poll: ReturnType<typeof setInterval> | undefined

const taskActive = computed(() => !!task.value && publishTaskActive(task.value))
const releaseReady = computed(
  () =>
    status.value?.remote?.release.state === 'published' &&
    !status.value.remote.release.missing_assets.length
)
/** The local gate the CLI enforces, mirrored so the button cannot offer a doomed run. */
const canStart = computed(() => {
  const repo = status.value?.repository
  if (!repo || taskActive.value) return false
  return (
    repo.is_repository &&
    !!repo.github_repository &&
    repo.worktree_clean === true &&
    repo.head_pushed === true
  )
})

async function load() {
  if (loading.value) return
  loading.value = true
  requestError.value = ''
  try {
    status.value = await getPluginProjectPublishStatus(props.projectId)
  } catch (error) {
    status.value = null
    requestError.value = describeError(error, 'publishLoadFailed')
  } finally {
    loading.value = false
  }
}

/** Resume a task still running on the server after the tab is reopened. */
async function loadTask() {
  try {
    task.value = await getPluginProjectPublishTask(props.projectId)
    taskError.value = ''
  } catch (error) {
    task.value = null
    // Never having published is the normal state, not an error.
    taskError.value =
      projectErrorCode(error) === 'PUBLISH_TASK_NOT_FOUND'
        ? ''
        : describeError(error, 'publishTaskNotFound')
  }
}

function startPolling() {
  if (poll) return
  poll = setInterval(() => void pollTask(), POLL_INTERVAL_MS)
}
function stopPolling() {
  if (!poll) return
  clearInterval(poll)
  poll = undefined
}

async function pollTask() {
  try {
    task.value = await getPluginProjectPublishTask(props.projectId)
    taskError.value = ''
  } catch (error) {
    taskError.value = describeError(error, 'publishTaskNotFound')
    stopPolling()
    return
  }
  if (!taskActive.value) {
    // The run just ended; reconcile the read-only status against GitHub once.
    stopPolling()
    await load()
  }
}

onMounted(async () => {
  await Promise.all([load(), loadTask()])
  if (taskActive.value) startPolling()
})
onUnmounted(stopPolling)

async function confirmPublish(mode: PublishMode) {
  const tag = status.value?.repository.tag ?? ''
  try {
    await ElMessageBox.confirm(
      t(CONFIRM_MODES[mode], { tag }),
      t('authoring.publishConfirmTitle'),
      {
        type: 'warning',
        confirmButtonText: t('authoring.publishConfirmTitle'),
        cancelButtonText: t('common.cancel'),
      }
    )
  } catch {
    return
  }
  try {
    task.value = await startPluginProjectPublish(props.projectId, mode)
    taskError.value = ''
    startPolling()
  } catch (error) {
    taskError.value = describeError(error, 'publishStartFailed')
  }
}

async function confirmCancel() {
  const current = task.value
  if (!current) return
  try {
    await ElMessageBox.confirm(
      t('authoring.publishCancelConfirm'),
      t('authoring.publishCancelTask'),
      {
        type: 'warning',
        confirmButtonText: t('authoring.publishCancelTask'),
        cancelButtonText: t('common.cancel'),
      }
    )
  } catch {
    return
  }
  canceling.value = true
  try {
    task.value = await cancelPluginProjectPublishTask(props.projectId, current.task_id)
  } catch (error) {
    ElMessage.error(describeError(error, 'actionFailed'))
  } finally {
    canceling.value = false
  }
}

const steps = computed<Step[]>(() => {
  const current = status.value
  if (!current) return []
  const { repository: repo, remote } = current
  const tag = repo.tag

  let repoState: StepState = 'unknown'
  let repoDetail = ''
  if (!repo.is_repository) {
    repoState = 'todo'
    repoDetail = t('authoring.publishRepoNoGit')
  } else if (!repo.github_repository) {
    repoState = 'todo'
    repoDetail = t('authoring.publishRepoNoGithub')
  } else if (remote && remote.visibility !== 'unknown') {
    if (remote.visibility !== 'public') {
      repoState = 'todo'
      repoDetail = t('authoring.publishRepoNotPublic')
    } else if (repo.worktree_clean !== true || repo.head_pushed !== true) {
      repoState = 'todo'
      repoDetail = t('authoring.publishRepoUnpushed')
    } else {
      repoState = 'done'
      repoDetail = t('authoring.publishRepoReady', { repo: repo.github_repository })
    }
  }

  let verifyState: StepState = 'unknown'
  let verifyDetail = ''
  if (remote && remote.verify !== 'unknown') {
    verifyState = remote.verify === 'success' ? 'done' : 'todo'
    verifyDetail = t(
      remote.verify === 'success' ? 'authoring.publishVerifyPassed' : 'authoring.publishVerifyPending'
    )
  }

  let releaseState: StepState = 'unknown'
  let releaseDetail = ''
  if (remote && remote.release.state !== 'unknown') {
    const missing = remote.release.missing_assets
    if (remote.release.state === 'missing') {
      releaseState = 'todo'
      releaseDetail = t('authoring.publishReleaseMissing', { tag })
    } else if (missing.length) {
      releaseState = 'todo'
      releaseDetail = t('authoring.publishReleaseAssets', { tag, assets: missing.join(', ') })
    } else {
      releaseState = 'done'
      releaseDetail = t('authoring.publishReleaseReady', { tag })
    }
  }

  return [
    { id: 'repository', title: t('authoring.publishStepRepository'), state: repoState, detail: repoDetail },
    { id: 'verify', title: t('authoring.publishStepVerify'), state: verifyState, detail: verifyDetail },
    { id: 'submission', title: t('authoring.publishStepSubmission'), state: 'later', detail: '' },
    { id: 'release', title: t('authoring.publishStepRelease'), state: releaseState, detail: releaseDetail },
    { id: 'market', title: t('authoring.publishStepMarket'), state: 'later', detail: '' },
  ]
})

const notes = computed(() => {
  const current = status.value
  if (!current) return []
  const result: string[] = []
  const outdated = Object.entries(current.actions)
    .filter(([, state]) => state !== 'CURRENT')
    .map(([file]) => file)
  if (outdated.length) {
    result.push(t('authoring.publishActionsOutdated', { files: outdated.join(', ') }))
  }
  const repoName = current.repository.github_repository?.split('/')[1]
  if (repoName && repoName.toLowerCase() !== `n.e.k.o_plugin_${current.plugin_id}`.toLowerCase()) {
    result.push(t('authoring.publishNameNote', { id: current.plugin_id }))
  }
  return result
})
</script>

<style scoped>
.authoring-publish-task {
  margin-top: var(--spacing-md);
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-panel);
  background: var(--surface-inset);
}
.authoring-publish-task h4 {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin: 0;
}
.authoring-publish-task details pre {
  margin: var(--spacing-xs) 0 0;
  max-height: 240px;
  overflow: auto;
  padding: var(--spacing-sm);
  border-radius: var(--radius-control);
  background: var(--surface-inset);
  color: var(--el-text-color-regular);
  font:
    12px/1.7 ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.authoring-publish-steps {
  list-style: none;
  margin: var(--spacing-md) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.authoring-publish-step {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-panel);
  background: var(--surface-inset);
}
.authoring-publish-step p {
  margin: var(--spacing-xs) 0 0;
}
.authoring-publish-notes {
  margin: var(--spacing-md) 0 0;
  padding-left: var(--spacing-md);
}
</style>
