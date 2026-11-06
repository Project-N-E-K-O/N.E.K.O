<template>
  <div class="authoring-publish">
    <el-alert v-if="requestError" :title="requestError" type="error" :closable="false" show-icon />
    <el-alert
      v-if="flow?.remoteWarning"
      :title="text(flow.remoteWarning)"
      type="warning"
      :closable="false"
      show-icon
    />
    <div class="authoring-precheck-actions">
      <el-button :icon="Refresh" :loading="loading" @click="refresh">
        {{ t('authoring.publishRefresh') }}
      </el-button>
      <el-button
        v-if="flow?.canPublishGithub"
        type="primary"
        :icon="Position"
        :disabled="taskActive"
        @click="confirmPublish('github_and_market')"
      >
        {{ t('authoring.publishRunGithubMarket') }}
      </el-button>
      <el-button
        v-if="flow?.canPublishGithub"
        :disabled="taskActive"
        @click="confirmPublish('github')"
      >
        {{ t('authoring.publishRunGithub') }}
      </el-button>
      <el-button
        v-if="flow?.canNotifyMarket"
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
        <span v-if="taskFinishedAt" class="authoring-muted authoring-small">{{
          t('authoring.publishTaskLastRun', { time: taskFinishedAt })
        }}</span>
        <span v-if="task.message" class="authoring-small">{{ task.message }}</span>
        <a
          v-if="task.status === 'succeeded' && task.release_url"
          class="authoring-small"
          href="#"
          @click.prevent="openExternalUrl(task.release_url)"
          >{{ task.release_url }}</a
        >
      </h4>
      <!-- Only a failure watched here opens the log; an earlier run stays collapsed. -->
      <details v-if="task.log.length" :open="task.status === 'failed' && taskWatched">
        <summary>{{ t('authoring.publishTaskLog') }}</summary>
        <pre>{{ task.log.join('\n') }}</pre>
      </details>
    </section>
    <el-skeleton v-if="!status && loading" class="authoring-publish-steps" :rows="5" animated />
    <ol v-if="status && flow" class="authoring-publish-steps">
      <li
        v-for="step in flow.steps"
        :key="step.id"
        class="authoring-publish-step"
        :class="{
          'authoring-publish-step--current': step.id === flow.currentStepId,
          'authoring-publish-step--pending':
            flow.currentStepId &&
            step.id !== flow.currentStepId &&
            step.state !== 'done' &&
            step.id !== 'submission',
        }"
      >
        <el-tag size="small" :type="TAG_TYPES[step.state]">{{ t(STATE_LABELS[step.state]) }}</el-tag>
        <div>
          <strong>{{ t(STEP_TITLES[step.id]) }}</strong>
          <p v-if="step.detail" class="authoring-muted authoring-small">{{ text(step.detail) }}</p>
          <el-alert
            v-if="step.warning"
            :title="text(step.warning)"
            type="warning"
            :closable="false"
            show-icon
          />
          <template v-if="step.id === 'repository'">
            <div v-if="flow.canCreateRepository" class="authoring-precheck-actions">
              <el-button
                type="primary"
                :icon="Plus"
                :loading="creatingRepo"
                @click="createRepo"
              >
                {{ creatingRepo ? t('authoring.publishCreatingRepo') : t('authoring.publishCreateRepo') }}
              </el-button>
            </div>
            <el-alert
              v-if="createdRepoUrl"
              :title="t('authoring.publishRepoCreated', { repo: createdRepoUrl })"
              type="success"
              :closable="false"
              show-icon
            />
            <el-alert
              v-if="repoManualSteps.length"
              :title="t('authoring.publishRepoManualTitle')"
              type="info"
              :closable="false"
              show-icon
            >
              <p class="authoring-small">{{ t('authoring.publishRepoManualHint') }}</p>
              <pre class="authoring-publish-manual">{{ repoManualSteps.join('\n') }}</pre>
            </el-alert>
            <el-alert
              v-if="repoCreateError"
              :title="repoCreateError"
              type="error"
              :closable="false"
              show-icon
            />
          </template>
          <AuthoringProjectSubmission
            v-if="step.id === 'submission'"
            :github-repository="status.repository.github_repository"
            :plugin-id="status.plugin_id"
            :plugin-name="pluginName"
            :plugin-description="pluginDescription"
            :pushed-head="flow.pushedHead"
            @state="submissionState = $event"
          />
        </div>
      </li>
    </ol>
    <ul v-if="flow?.notes.length" class="authoring-publish-notes">
      <li v-for="note in flow.notes" :key="note.key" class="authoring-muted authoring-small">
        {{ text(note) }}
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleClose, Plus, Position, Refresh } from '@element-plus/icons-vue'
import AuthoringProjectSubmission from '@/components/authoring/AuthoringProjectSubmission.vue'
import {
  publishFlow,
  type FlowMessage,
  type PublishStepId,
  type PublishStepState,
} from '@/components/authoring/publishFlow'
import {
  cancelPluginProjectPublishTask,
  createPluginProjectRepo,
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
import type { SubmissionPanelState } from '@/api/marketSubmissions'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'
import { openExternalUrl } from '@/utils/openExternal'
import { formatRunTimestamp } from '@/utils/runDisplay'

const TAG_TYPES: Record<PublishStepState, 'success' | 'warning' | 'info'> = {
  done: 'success',
  todo: 'warning',
  waiting: 'info',
  unknown: 'info',
}
const STATE_LABELS: Record<PublishStepState, string> = {
  done: 'authoring.publishStatusDone',
  todo: 'authoring.publishStatusTodo',
  waiting: 'authoring.publishStatusWaiting',
  unknown: 'authoring.publishStatusUnknown',
}
const STEP_TITLES: Record<PublishStepId, string> = {
  repository: 'authoring.publishStepRepository',
  verify: 'authoring.publishStepVerify',
  submission: 'authoring.publishStepSubmission',
  release: 'authoring.publishStepRelease',
  market: 'authoring.publishStepMarket',
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
/** Consecutive failed polls tolerated before polling stops and asks for a refresh. */
const POLL_MAX_FAILURES = 5
const CONFIRM_MODES: Record<PublishMode, string> = {
  github_and_market: 'authoring.publishConfirmGithubMarket',
  github: 'authoring.publishConfirmGithub',
  market_notify: 'authoring.publishConfirmMarket',
}

const props = defineProps<{
  projectId: string
  /** plugin.toml name and description: the defaults of a new Market draft. */
  pluginName: string
  pluginDescription: string
}>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const loading = ref(false)
const status = ref<PublishStatus | null>(null)
const requestError = ref('')
const task = ref<PublishTask | null>(null)
/** True once this panel saw the task active, so its outcome is news, not history. */
const taskWatched = ref(false)
const taskError = ref('')
const canceling = ref(false)
const submissionState = ref<SubmissionPanelState>({
  phase: null,
  marketPluginId: null,
  resolvedCommit: null,
})
let disposed = false
let pollTimer: ReturnType<typeof setTimeout> | undefined
let pollFailures = 0
let reloadQueued = false
let queuedFresh = false

const flow = computed(() =>
  status.value
    ? publishFlow({ status: status.value, submission: submissionState.value, task: task.value })
    : null
)
const text = (message: FlowMessage) => t(`authoring.${message.key}`, message.params ?? {})
const taskActive = computed(() => !!task.value && publishTaskActive(task.value))
const taskFinishedAt = computed(() =>
  task.value?.finished_at ? formatRunTimestamp(task.value.finished_at, true) : ''
)

// Repository creation: offered only when the project is a git repo with no
// origin yet. A foreign (non-GitHub) origin is reported instead.
const creatingRepo = ref(false)
const createdRepoUrl = ref('')
const repoManualSteps = ref<string[]>([])
const repoCreateError = ref('')

async function createRepo() {
  if (creatingRepo.value) return
  creatingRepo.value = true
  createdRepoUrl.value = ''
  repoManualSteps.value = []
  repoCreateError.value = ''
  try {
    const result = await createPluginProjectRepo(props.projectId)
    if (disposed) return
    if (result.status === 'created') {
      createdRepoUrl.value = result.repository_url
      await load({ fresh: true })
    } else if (result.status === 'manual') {
      // gh is unavailable: surface the steps the CLI printed so the user can
      // create the repository by hand, then refresh.
      repoManualSteps.value = result.log
    } else {
      repoCreateError.value = result.log.join('\n') || describeError(null, 'repoCreateFailed')
    }
  } catch (error) {
    repoCreateError.value = describeError(error, 'repoCreateFailed')
  } finally {
    creatingRepo.value = false
  }
}

/**
 * Read the publish status. A call made while one is in flight is queued and
 * runs once it ends, because the in-flight read predates the change that
 * prompted the new call. A failed read keeps the last status on screen.
 */
async function load(options: { fresh?: boolean } = {}) {
  if (loading.value) {
    reloadQueued = true
    queuedFresh ||= !!options.fresh
    return
  }
  loading.value = true
  requestError.value = ''
  try {
    const next = await getPluginProjectPublishStatus(props.projectId, !!options.fresh)
    if (!disposed) status.value = next
  } catch (error) {
    if (!disposed) requestError.value = describeError(error, 'publishLoadFailed')
  } finally {
    loading.value = false
  }
  if (reloadQueued && !disposed) {
    const fresh = queuedFresh
    reloadQueued = false
    queuedFresh = false
    await load({ fresh })
  }
}

function applyTask(next: PublishTask) {
  task.value = next
  taskError.value = ''
  pollFailures = 0
  if (publishTaskActive(next)) {
    taskWatched.value = true
    schedulePoll()
  }
}

/** Read the latest task, resuming polling when it is still running on the server. */
async function loadTask() {
  try {
    const next = await getPluginProjectPublishTask(props.projectId)
    if (!disposed) applyTask(next)
  } catch (error) {
    if (disposed) return
    if (projectErrorCode(error) === 'PUBLISH_TASK_NOT_FOUND') {
      // Never having published is the normal state, not an error.
      task.value = null
      taskError.value = ''
    } else {
      taskError.value = describeError(error, 'publishTaskPollFailed')
    }
  }
}

function schedulePoll() {
  if (disposed || pollTimer) return
  pollTimer = setTimeout(() => {
    pollTimer = undefined
    void pollTask()
  }, POLL_INTERVAL_MS)
}
function stopPolling() {
  clearTimeout(pollTimer)
  pollTimer = undefined
}

/** One poll; the next is scheduled only after this one answers, so replies never overlap. */
async function pollTask() {
  let next: PublishTask
  try {
    next = await getPluginProjectPublishTask(props.projectId)
  } catch (error) {
    if (disposed) return
    if (projectErrorCode(error) === 'PUBLISH_TASK_NOT_FOUND') {
      // The server restarted and forgot the task; its outcome is in the status.
      task.value = null
      taskError.value = ''
      await load({ fresh: true })
      return
    }
    pollFailures += 1
    if (pollFailures < POLL_MAX_FAILURES) schedulePoll()
    else taskError.value = describeError(error, 'publishTaskPollFailed')
    return
  }
  if (disposed) return
  applyTask(next)
  // The run just ended; reconcile the read-only status against GitHub once.
  if (!publishTaskActive(next)) await load({ fresh: true })
}

/** The explicit Refresh: bypass the server's GitHub cache and re-read the task. */
function refresh() {
  return Promise.all([load({ fresh: true }), loadTask()])
}

/** Re-read when the user returns to the tab; GitHub answers may stay cached. */
function reload() {
  return Promise.all([load(), loadTask()])
}

onMounted(reload)
onUnmounted(() => {
  disposed = true
  stopPolling()
})
defineExpose({ reload })

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
    const started = await startPluginProjectPublish(props.projectId, mode)
    if (!disposed) applyTask(started)
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
    const canceled = await cancelPluginProjectPublishTask(props.projectId, current.task_id)
    if (!disposed) applyTask(canceled)
  } catch (error) {
    ElMessage.error(describeError(error, 'actionFailed'))
  } finally {
    canceling.value = false
  }
}
</script>

<style scoped>
.authoring-publish-manual {
  margin: var(--spacing-xs) 0 0;
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
  flex-wrap: wrap;
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
.authoring-publish-step--current {
  border-left: 3px solid var(--el-color-primary);
  background: var(--surface-raised, var(--surface-inset));
}
.authoring-publish-step--pending {
  opacity: 0.62;
}
.authoring-publish-notes {
  margin: var(--spacing-md) 0 0;
  padding-left: var(--spacing-md);
}
</style>
