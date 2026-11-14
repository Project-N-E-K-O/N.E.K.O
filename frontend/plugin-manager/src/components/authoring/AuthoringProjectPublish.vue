<template>
  <div ref="root" class="authoring-publish">
    <AuthoringPublishStepper
      v-if="flow"
      :steps="pagerSteps"
      :current-step-id="flow.currentStepId"
      :selected-step-id="activeStep"
      :titles="STEP_TITLES"
      :state-labels="STATE_LABELS"
      @select="goToStep"
    >
      <template #actions>
        <el-button :icon="Refresh" :loading="loading" @click="refresh">
          {{ t('authoring.publishRefresh') }}
        </el-button>
      </template>
    </AuthoringPublishStepper>
    <div v-else class="authoring-precheck-actions">
      <el-button :icon="Refresh" :loading="loading" @click="refresh">
        {{ t('authoring.publishRefresh') }}
      </el-button>
    </div>
    <el-alert v-if="requestError" :title="requestError" type="error" :closable="false" show-icon />
    <el-alert
      v-if="flow?.remoteWarning"
      :title="text(flow.remoteWarning)"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert v-if="taskError" :title="taskError" type="error" :closable="false" show-icon />
    <el-skeleton v-if="!status && loading" class="authoring-publish-steps" :rows="5" animated />
    <!-- One step per page. Every page stays mounted (v-show): the submission
         page feeds the flow, and a check report survives paging away. -->
    <div v-if="status && flow" class="authoring-publish-steps">
      <section
        v-show="activeStep === 'toolchain'"
        :id="sectionId('toolchain')"
        class="authoring-publish-step"
        :class="{ 'authoring-publish-step--current': flow.currentStepId === 'toolchain' }"
        data-step="toolchain"
        :aria-labelledby="`${sectionId('toolchain')}-title`"
      >
        <header class="authoring-publish-step__header">
          <el-tag size="small" :type="TAG_TYPES[flow.toolchain.state]">
            {{ t(STATE_LABELS[flow.toolchain.state]) }}
          </el-tag>
          <h3
            :id="`${sectionId('toolchain')}-title`"
            :ref="(el) => setHeading('toolchain', el)"
            tabindex="-1"
          >
            0. {{ t(STEP_TITLES.toolchain) }}
          </h3>
        </header>
        <AuthoringPublishGuide page-id="toolchain" :params="guideParams" :open="guideOpen('toolchain')" />
        <AuthoringPublishToolchain :page="flow.toolchain" :error="toolchainError" />
      </section>
      <section
        v-for="(step, index) in flow.steps"
        v-show="step.id === activeStep"
        :id="sectionId(step.id)"
        :key="step.id"
        class="authoring-publish-step"
        :class="{ 'authoring-publish-step--current': step.id === flow.currentStepId }"
        :data-step="step.id"
        :aria-labelledby="`${sectionId(step.id)}-title`"
      >
        <header class="authoring-publish-step__header">
          <el-tag size="small" :type="TAG_TYPES[step.state]">{{ t(STATE_LABELS[step.state]) }}</el-tag>
          <h3
            :id="`${sectionId(step.id)}-title`"
            :ref="(el) => setHeading(step.id, el)"
            tabindex="-1"
          >
            {{ index + 1 }}. {{ t(STEP_TITLES[step.id]) }}
          </h3>
        </header>
        <AuthoringPublishGuide :page-id="step.id" :params="guideParams" :open="guideOpen(step.id)" />
        <p
          v-if="flow.prerequisites[step.id]"
          class="authoring-publish-step__prerequisite authoring-small"
        >
          {{ t('authoring.publishPrerequisite', stepRef(flow.prerequisites[step.id]!)) }}
          <el-button link type="primary" size="small" @click="goToStep(flow.prerequisites[step.id]!)">
            {{ t('authoring.publishGoToStep', stepRef(flow.prerequisites[step.id]!)) }}
          </el-button>
        </p>
        <p v-if="step.detail" class="authoring-muted authoring-small">{{ text(step.detail) }}</p>
        <el-alert
          v-if="step.warning"
          :title="text(step.warning)"
          type="warning"
          :closable="false"
          show-icon
        />
        <AuthoringPublishWorkspace
          v-if="step.id === 'repository'"
          :repository="status.repository"
          :workspace="status.workspace ?? null"
        />
        <AuthoringPublishRepository
          v-if="step.id === 'repository'"
          :project-id="projectId"
          :can-create="flow.canCreateRepository"
          :notes="flow.notes"
          @created="load({ fresh: true })"
        />
        <AuthoringProjectPrecheck v-else-if="step.id === 'verify'" :project-id="projectId" />
        <AuthoringProjectSubmission
          :key="status.repository.github_repository ?? ''"
          v-else-if="step.id === 'submission'"
          ref="submissionPanel"
          :github-repository="status.repository.github_repository"
          :plugin-id="status.plugin_id"
          :plugin-name="pluginName"
          :plugin-description="pluginDescription"
          :pushed-head="flow.pushedHead"
          @state="submissionState = $event"
        />
        <AuthoringPublishRelease
          v-else-if="step.id === 'release'"
          :modes="GITHUB_MODES"
          :task="taskFor(GITHUB_MODES)"
          :any-task-active="taskActive"
          :watched="taskWatched"
          :canceling="canceling"
          :error="startErrorFor(GITHUB_MODES)"
          @run="run"
          @cancel="confirmCancel"
        />
        <AuthoringPublishRelease
          v-else-if="step.id === 'market'"
          :modes="MARKET_MODES"
          :task="taskFor(MARKET_MODES)"
          :any-task-active="taskActive"
          :watched="taskWatched"
          :canceling="canceling"
          :error="startErrorFor(MARKET_MODES)"
          @run="run"
          @cancel="confirmCancel"
        />
      </section>
      <footer class="authoring-publish-pager">
        <el-button v-if="previousStep" :icon="ArrowLeft" @click="goToStep(previousStep)">
          {{ t('authoring.publishPreviousStep', stepRef(previousStep)) }}
        </el-button>
        <span class="authoring-publish-pager__count authoring-muted authoring-small">
          {{ t('authoring.publishPageCount', { n: activeIndex, total: PUBLISH_PAGE_ORDER.length - 1 }) }}
        </span>
        <el-button v-if="nextStep" @click="goToStep(nextStep)">
          {{ t('authoring.publishNextStep', stepRef(nextStep)) }}
          <el-icon class="el-icon--right"><ArrowRight /></el-icon>
        </el-button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, nextTick, onMounted, onUnmounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLocalizedError } from '@/composables/useLocalizedError'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, ArrowRight, Refresh } from '@element-plus/icons-vue'
import AuthoringProjectPrecheck from '@/components/authoring/AuthoringProjectPrecheck.vue'
import AuthoringProjectSubmission from '@/components/authoring/AuthoringProjectSubmission.vue'
import AuthoringPublishGuide from '@/components/authoring/AuthoringPublishGuide.vue'
import AuthoringPublishRelease from '@/components/authoring/AuthoringPublishRelease.vue'
import AuthoringPublishRepository from '@/components/authoring/AuthoringPublishRepository.vue'
import AuthoringPublishStepper from '@/components/authoring/AuthoringPublishStepper.vue'
import AuthoringPublishToolchain from '@/components/authoring/AuthoringPublishToolchain.vue'
import AuthoringPublishWorkspace from '@/components/authoring/AuthoringPublishWorkspace.vue'
import {
  PUBLISH_PAGE_ORDER,
  isReleaseUrlOf,
  publishFlow,
  type ActionBlocker,
  type FlowMessage,
  type PublishPageId,
  type PublishStepState,
} from '@/components/authoring/publishFlow'
import type { GuideParams } from '@/components/authoring/publishGuide'
import {
  cancelPluginProjectPublishTask,
  getPluginProjectPublishStatus,
  getPluginProjectPublishTask,
  getPluginToolchain,
  isMarketPublicationStatus,
  projectErrorCode,
  publishTaskActive,
  startPluginProjectPublish,
  type PublishMode,
  type MarketPublicationStatus,
  type PublishStatus,
  type PublishTask,
  type Toolchain,
} from '@/api/pluginProjects'
import { listMarketPublicationReceipts, type SubmissionPanelState } from '@/api/marketSubmissions'
import { fetchMarketPluginVersions } from '@/api/market'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'

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
const STEP_TITLES: Record<PublishPageId, string> = {
  toolchain: 'authoring.publishStepToolchain',
  repository: 'authoring.publishStepRepository',
  verify: 'authoring.publishStepVerify',
  submission: 'authoring.publishStepSubmission',
  release: 'authoring.publishStepRelease',
  market: 'authoring.publishStepMarket',
}
const GITHUB_MODES: PublishMode[] = ['github_and_market', 'github']
const MARKET_MODES: PublishMode[] = ['market_notify']
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
  /** The project folder, filled into the guide's commands. */
  sourceDir?: string
}>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const root = ref<HTMLElement>()
const loading = ref(false)
const status = ref<PublishStatus | null>(null)
const requestFailure = useLocalizedError(describeError, 'publishLoadFailed')
const requestError = requestFailure.message
const task = ref<PublishTask | null>(null)
/** True once this panel saw the task active, so its outcome is news, not history. */
const taskWatched = ref(false)
/** Polling a task failed; shown once for the page. */
const taskFailure = useLocalizedError(describeError, 'publishTaskPollFailed')
const taskError = taskFailure.message
/** A refused start, shown in the step of the mode that was refused. */
const startError = ref<{ mode: PublishMode; error: unknown } | null>(null)
const canceling = ref(false)
const submissionState = ref<SubmissionPanelState>({
  phase: null,
  marketPluginId: null,
  resolvedCommit: null,
})
// Inside v-for, a template ref collects into an array.
const submissionPanel = ref<InstanceType<typeof AuthoringProjectSubmission>[]>([])
/** Whether the Market lists this Release; read from the Market, not from the task. */
const marketListed = ref<boolean | null>(null)
const marketReceipt = ref<MarketPublicationStatus | null>(null)
let marketReads = 0
let disposed = false
let pollTimer: ReturnType<typeof setTimeout> | undefined
let pollFailures = 0
let reloadQueued = false
let queuedFresh = false

const flow = computed(() =>
  status.value
    ? publishFlow({
        status: status.value,
        submission: submissionState.value,
        task: task.value,
        marketListed: marketListed.value,
        marketReceipt: marketReceipt.value,
        toolchain: toolchain.value,
      })
    : null
)
/** Step 0 joins the five publish steps in the step bar. */
const pagerSteps = computed(() =>
  flow.value
    ? [
        { id: 'toolchain' as const, state: flow.value.toolchain.state, detail: null, warning: null },
        ...flow.value.steps,
      ]
    : []
)
const text = (message: FlowMessage) => t(`authoring.${message.key}`, message.params ?? {})
const taskActive = computed(() => !!task.value && publishTaskActive(task.value))
const stepRef = (id: PublishPageId) => ({
  n: PUBLISH_PAGE_ORDER.indexOf(id),
  step: t(STEP_TITLES[id]),
})
const taskFor = (modes: PublishMode[]) =>
  task.value && modes.includes(task.value.mode) ? task.value : null
const startErrorFor = (modes: PublishMode[]) =>
  startError.value && modes.includes(startError.value.mode) ? describeError(startError.value.error, 'publishStartFailed') : ''
/** Values the beginner guide fills into its commands and paths. */
const guideParams = computed<GuideParams>(() => ({
  repo: status.value?.repository.github_repository ?? undefined,
  tag: status.value?.repository.tag,
  version: status.value?.version,
  branch: status.value?.repository.branch ?? 'main',
  pluginId: status.value?.plugin_id,
  dir: props.sourceDir,
}))
/** The guide starts open on a page with something to do, folded on a finished one. */
function guideOpen(id: PublishPageId) {
  const current = flow.value
  if (!current) return true
  const state = id === 'toolchain' ? current.toolchain.state : current.steps.find((s) => s.id === id)?.state
  return state !== 'done'
}

// Step 0: the publishing tools of this machine, read once per panel and on Refresh.
const toolchain = ref<Toolchain | null>(null)
const toolchainFailure = useLocalizedError(describeError, 'toolchainLoadFailed')
const toolchainError = toolchainFailure.message
async function loadToolchain() {
  toolchainFailure.clear()
  try {
    const next = await getPluginToolchain(props.projectId)
    if (!disposed) toolchain.value = next
  } catch (error) {
    if (!disposed) toolchainFailure.set(error)
  }
}

// Paging: one page is shown at a time. It opens on the page to act on and
// follows it as the facts change, until the user picks a page themselves.
const pickedStep = ref<PublishPageId | null>(null)
const activeStep = computed<PublishPageId>(
  () => pickedStep.value ?? flow.value?.currentStepId ?? 'toolchain'
)
const activeIndex = computed(() => PUBLISH_PAGE_ORDER.indexOf(activeStep.value))
const previousStep = computed(() => PUBLISH_PAGE_ORDER[activeIndex.value - 1] ?? null)
const nextStep = computed(() => PUBLISH_PAGE_ORDER[activeIndex.value + 1] ?? null)

// A page change moves focus to the page heading, so keyboard and screen reader
// users land on the new content, and brings the page top into view.
const headings = new Map<PublishPageId, HTMLElement>()
function setHeading(id: PublishPageId, el: Element | ComponentPublicInstance | null) {
  if (el instanceof HTMLElement) headings.set(id, el)
  else headings.delete(id)
}
const sectionId = (id: PublishPageId) => `authoring-publish-${props.projectId}-${id}`
async function goToStep(id: PublishPageId) {
  pickedStep.value = id
  await nextTick()
  root.value?.scrollIntoView?.({ block: 'nearest' })
  headings.get(id)?.focus({ preventScroll: true })
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
  requestFailure.clear()
  try {
    const next = await getPluginProjectPublishStatus(props.projectId, !!options.fresh)
    if (!disposed) status.value = next
  } catch (error) {
    if (!disposed) requestFailure.set(error)
  } finally {
    loading.value = false
  }
  if (reloadQueued && !disposed) {
    const fresh = queuedFresh
    reloadQueued = false
    queuedFresh = false
    await load({ fresh })
    return
  }
  await loadMarketListing()
}

/**
 * Read whether the approved plugin's public version list has this Release's
 * tag (the Market stores the tag as the version). Only the newest read
 * counts; a failed read, or a plugin the Market does not list yet, is null.
 */
async function loadMarketListing() {
  const read = ++marketReads
  marketListed.value = null
  marketReceipt.value = null
  const pluginId = submissionState.value.marketPluginId
  const tag = status.value?.repository.tag
  if (pluginId === null || !tag) {
    marketListed.value = null
    return
  }
  const repository = status.value?.repository.github_repository
  const currentTask = task.value
  const requestId = repository && currentTask && isReleaseUrlOf(currentTask.release_url, repository, tag)
    ? currentTask.market_request_id
    : null
  const receiptsRequest = repository
    ? listMarketPublicationReceipts(pluginId, requestId ?? undefined).catch(() => [])
    : []
  const versions = await fetchMarketPluginVersions(pluginId)
  if (disposed || read !== marketReads) return
  marketListed.value = versions ? versions.some((item) => item.version === tag) : null
  const receipts = await receiptsRequest
  if (disposed || read !== marketReads) return
  // The task's own receipt is picked by its request id; the Release URL is
  // only the fallback for a receipt the task did not record.
  const receipt = repository && Array.isArray(receipts) && (
    (requestId ? receipts.find(item => item.id === requestId) : undefined) ??
    receipts.find(item => isReleaseUrlOf(item.release_url, repository, tag))
  )
  if (receipt && isMarketPublicationStatus(receipt.status)) marketReceipt.value = receipt.status
}
// The submission panel finds the approved plugin after the status is read.
watch([() => submissionState.value.marketPluginId, () => task.value?.market_request_id],
  () => void loadMarketListing())

function applyTask(next: PublishTask) {
  task.value = next
  taskFailure.clear()
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
      taskFailure.clear()
    } else {
      taskFailure.set(error)
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
      taskFailure.clear()
      await load({ fresh: true })
      return
    }
    pollFailures += 1
    if (pollFailures < POLL_MAX_FAILURES) schedulePoll()
    else taskFailure.set(error)
    return
  }
  if (disposed) return
  applyTask(next)
  // The run just ended; reconcile the read-only status against GitHub once.
  if (!publishTaskActive(next)) await load({ fresh: true })
}

/** The one Refresh: tools, GitHub (past the server cache), the task and the Market submission. */
function refresh() {
  return Promise.all([
    load({ fresh: true }),
    loadTask(),
    loadToolchain(),
    submissionPanel.value[0]?.reload(),
  ])
}

/** Re-read when the user returns to the tab; GitHub answers may stay cached. */
function reload() {
  return Promise.all([load(), loadTask(), loadToolchain()])
}

onMounted(reload)
onUnmounted(() => {
  disposed = true
  stopPolling()
})
defineExpose({ reload })

function reasonList(blockers: ActionBlocker[], intro: string) {
  return h('div', { class: 'authoring-publish-gate' }, [
    h('p', intro),
    h('ul', blockers.map((item) => h('li', text(item.reason)))),
  ])
}

/** A hard blocker: nothing runs; the dialog can take the user to the step to finish. */
async function explainHard(blockers: ActionBlocker[]) {
  const target = blockers.find((item) => item.step)?.step ?? null
  try {
    await ElMessageBox.confirm(
      reasonList(blockers, t('authoring.publishBlockedIntro')),
      t('authoring.publishBlockedTitle'),
      {
        type: 'error',
        confirmButtonText: target ? t('authoring.publishGoToStep', stepRef(target)) : t('common.close'),
        showCancelButton: !!target,
        cancelButtonText: t('common.close'),
      }
    )
  } catch {
    return
  }
  if (target) await goToStep(target)
}

/** Soft blockers: going back is the suggested answer, continuing is allowed. */
async function confirmSoft(blockers: ActionBlocker[]): Promise<boolean> {
  const target = blockers.find((item) => item.step)?.step ?? null
  const body = reasonList(blockers, t('authoring.publishSoftIntro'))
  if (!target) {
    try {
      await ElMessageBox.confirm(body, t('authoring.publishSoftTitle'), {
        type: 'warning',
        confirmButtonText: t('authoring.publishContinueAnyway'),
        cancelButtonText: t('common.cancel'),
      })
      return true
    } catch {
      return false
    }
  }
  try {
    await ElMessageBox.confirm(body, t('authoring.publishSoftTitle'), {
      type: 'warning',
      confirmButtonText: t('authoring.publishGoToStep', stepRef(target)),
      cancelButtonText: t('authoring.publishContinueAnyway'),
      distinguishCancelAndClose: true,
    })
  } catch (action) {
    // "Continue anyway" is the cancel button; closing the box does nothing.
    return action === 'cancel'
  }
  await goToStep(target)
  return false
}

/** Every publish button is clickable; what is missing is explained before anything runs. */
async function run(mode: PublishMode) {
  const current = flow.value
  if (!current || taskActive.value) return
  const blockers = current.blockers[mode]
  const hardOnes = blockers.filter((item) => item.severity === 'hard')
  if (hardOnes.length) return explainHard(hardOnes)
  const softOnes = blockers.filter((item) => item.severity === 'soft')
  if (softOnes.length && !(await confirmSoft(softOnes))) return
  await confirmPublish(mode)
}

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
  startError.value = null
  try {
    const started = await startPluginProjectPublish(props.projectId, mode)
    if (!disposed) applyTask(started)
  } catch (error) {
    // The server gate stays authoritative; its reason shows in the step that was refused.
    startError.value = { mode, error }
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
.authoring-publish {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  max-width: 960px;
}
.authoring-publish-steps {
  margin: var(--spacing-sm) 0 0;
}
.authoring-publish-step {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  padding: var(--spacing-md);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-panel);
  background: var(--surface-inset);
}
.authoring-publish-pager {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-md);
}
.authoring-publish-pager__count {
  flex: 1;
  text-align: center;
}
.authoring-publish-pager .el-button + .el-button {
  margin-left: 0;
}
.authoring-publish-step p {
  margin: 0;
}
.authoring-publish-step--current {
  border-left: 3px solid var(--el-color-primary);
  background: var(--surface-raised, var(--surface-inset));
}
.authoring-publish-step__header {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}
.authoring-publish-step__header h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.authoring-publish-step__header h3:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  border-radius: 2px;
}
.authoring-publish-step__prerequisite {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
  padding: 6px var(--spacing-sm);
  border-radius: var(--radius-control);
  background: color-mix(in srgb, var(--el-color-info) 10%, transparent);
  color: var(--el-text-color-regular);
}
</style>
