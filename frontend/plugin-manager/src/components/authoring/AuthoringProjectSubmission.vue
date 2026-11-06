<template>
  <div class="authoring-submission">
    <!-- Not signed in: a single call to the shared Market login. -->
    <template v-if="!signedIn">
      <el-alert
        :title="t('authoring.submissionLoginRequired')"
        type="info"
        :closable="false"
        show-icon
      />
      <div class="authoring-precheck-actions">
        <el-button
          type="primary"
          :icon="User"
          :loading="marketAuthBusy"
          @click="startMarketLogin()"
        >
          {{ t('authoring.submissionLogin') }}
        </el-button>
      </div>
    </template>

    <template v-else>
      <p class="authoring-muted authoring-small">
        {{ t('authoring.submissionLoggedInAs', { name: marketAuthDisplayName }) }}
      </p>
      <el-alert
        v-if="marketAuthStateMessageKey"
        :title="t(marketAuthStateMessageKey)"
        type="warning"
        :closable="false"
        show-icon
      />
      <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />
      <el-alert v-if="notice" :title="notice" type="info" :closable="false" show-icon />
      <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon />
      <el-alert
        v-if="previewNotes.length"
        :title="t('authoring.submissionPreviewNotes')"
        type="warning"
        :closable="false"
        show-icon
      >
        <ul class="authoring-submission-notes">
          <li v-for="note in previewNotes" :key="note">{{ note }}</li>
        </ul>
      </el-alert>

      <!-- Matched submission: its state and the actions it allows. -->
      <template v-if="submission && phase">
        <div class="authoring-submission-state">
          <el-tag size="small" :type="STATE_TAG_TYPES[phase]">{{ t(`authoring.${STATE_LABELS[phase]}`) }}</el-tag>
          <span v-if="stateDetail" class="authoring-muted authoring-small">{{ stateDetail }}</span>
          <a
            v-if="submissionUrl"
            class="authoring-small"
            href="#"
            @click.prevent="openExternalUrl(submissionUrl)"
            >{{ t('authoring.submissionOpenOnMarket') }}</a
          >
        </div>
        <p v-if="progressDescription" class="authoring-muted authoring-small">
          {{ progressDescription }}
        </p>
      </template>
      <el-alert
        v-else-if="loaded"
        :title="t('authoring.submissionNone')"
        type="info"
        :closable="false"
        show-icon
      />

      <!-- Zone and tags: the Market takes them only from the draft, so they are set here. -->
      <div v-if="editingMetadata" class="authoring-submission-metadata">
        <el-alert v-if="zonesError" :title="zonesError" type="warning" :closable="false" show-icon />
        <el-form label-position="top" size="small" @submit.prevent>
          <el-form-item :label="t('authoring.submissionZone')" :required="metadataRequired">
            <el-select
              v-model="zoneSlug"
              filterable
              :loading="zonesLoading"
              :disabled="acting"
              :placeholder="t('authoring.submissionZonePlaceholder')"
            >
              <el-option v-for="zone in zones" :key="zone.id" :label="zoneLabel(zone)" :value="zone.id" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('authoring.submissionTags')" :required="metadataRequired">
            <div class="authoring-submission-tags">
              <el-check-tag
                v-for="tag in tagOptions"
                :key="tag"
                :checked="tags.includes(tag)"
                :disabled="acting || (!tags.includes(tag) && tags.length >= SUBMISSION_TAGS_MAX)"
                @change="toggleTag(tag)"
              >
                {{ tagLabel(tag) }}
              </el-check-tag>
            </div>
            <p class="authoring-muted authoring-small">
              {{ t('authoring.submissionTagsCount', { count: tags.length, max: SUBMISSION_TAGS_MAX }) }}
            </p>
          </el-form-item>
        </el-form>
        <p v-if="metadataRequired && !metadataComplete" class="authoring-muted authoring-small">
          {{ t('authoring.submissionMetadataRequired') }}
        </p>
      </div>

      <div class="authoring-precheck-actions">
        <el-button
          v-if="phase === 'draft'"
          type="primary"
          :icon="Promotion"
          :loading="acting"
          :disabled="metadataBlocking"
          @click="confirmSubmit"
        >
          {{ t('authoring.submissionSubmitReview') }}
        </el-button>
        <el-button
          v-if="phase === 'draft' && metadataDirty"
          :icon="Check"
          :disabled="acting"
          @click="saveMetadata"
        >
          {{ t('authoring.submissionSaveMetadata') }}
        </el-button>
        <el-button
          v-if="canCreateDraft"
          type="primary"
          :icon="Plus"
          :loading="acting"
          :disabled="metadataBlocking"
          @click="createDraft"
        >
          {{ acting ? t('authoring.submissionCreatingDraft') : t('authoring.submissionCreateDraft') }}
        </el-button>
        <el-button v-if="isOpen" :icon="CircleClose" :disabled="acting" @click="confirmWithdraw">
          {{ t('authoring.submissionWithdraw') }}
        </el-button>
        <el-button :icon="Refresh" :loading="loading" @click="reload()">
          {{ t('authoring.publishRefresh') }}
        </el-button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Check, CircleClose, Plus, Promotion, Refresh, User } from '@element-plus/icons-vue'
import { useMarketAuth } from '@/composables/useMarketAuth'
import {
  MarketSubmissionError,
  PLUGIN_NAME_MAX_LENGTH,
  RESUBMITTABLE_PHASES,
  STANDARD_SUBMISSION_TAGS,
  SUBMISSION_TAGS_MAX,
  createSubmissionDraft,
  listMarketSubmissions,
  listMarketZones,
  marketSubmissionUrl,
  previewSubmissionRepository,
  submissionMatchesRepository,
  submissionPhase,
  submitSubmission,
  updateSubmissionDraft,
  withdrawSubmission,
  type MarketSubmission,
  type MarketZone,
  type RepositoryPreview,
  type SubmissionDraftUpdate,
  type SubmissionPanelState,
  type SubmissionPhase,
} from '@/api/marketSubmissions'
import type { AuthoringMessageKey } from '@/i18n/authoring'
import { openExternalUrl } from '@/utils/openExternal'

const props = defineProps<{ githubRepository: string | null }>()
const emit = defineEmits<{ (event: 'state', state: SubmissionPanelState): void }>()

const { t, te } = useI18n()
const {
  marketAuth,
  marketAuthBusy,
  marketAuthDisplayName,
  marketAuthStateMessageKey,
  loadMarketAuthStatus,
  startMarketLogin,
  stopMarketAuthPolling,
} = useMarketAuth()

const STATE_TAG_TYPES: Record<SubmissionPhase, 'success' | 'warning' | 'info' | 'danger'> = {
  draft: 'warning',
  submitted: 'info',
  in_review: 'info',
  author_action: 'warning',
  approved: 'success',
  rejected: 'danger',
  withdrawn: 'info',
  superseded: 'info',
  closed: 'info',
}
const STATE_LABELS: Record<SubmissionPhase, AuthoringMessageKey> = {
  draft: 'submissionStateDraft',
  submitted: 'submissionStateSubmitted',
  in_review: 'submissionStateInReview',
  author_action: 'submissionStateAuthorAction',
  approved: 'submissionStateApproved',
  rejected: 'submissionStateRejected',
  withdrawn: 'submissionStateWithdrawn',
  superseded: 'submissionStateSuperseded',
  closed: 'submissionStateClosed',
}
const OPEN_PHASES: readonly SubmissionPhase[] = ['draft', 'submitted', 'in_review', 'author_action']
/** Error codes from the bridge or the Market that have their own message. */
const ERROR_MESSAGES: Record<string, AuthoringMessageKey> = {
  market_auth_required: 'marketAuthRequired',
  market_auth_pending: 'marketAuthPending',
  market_email_unverified: 'marketEmailUnverified',
  market_auth_rejected: 'marketAuthRejected',
  market_submission_unavailable: 'marketSubmissionUnavailable',
  market_submission_redirect_rejected: 'marketSubmissionRedirectRejected',
  market_submission_not_configured: 'marketSubmissionNotConfigured',
  market_submission_id_invalid: 'marketSubmissionIdInvalid',
  market_bridge_unreachable: 'marketBridgeUnreachable',
  submission_content_too_large: 'submissionContentTooLarge',
}

const signedIn = computed(() => marketAuth.value.authenticated === true)
/** Signed in and accepted by the Market; only then are submission calls made. */
const marketReady = computed(() => signedIn.value && !marketAuthStateMessageKey.value)
const loading = ref(false)
/** True once the submission list for this repository has been read. */
const loaded = ref(false)
const acting = ref(false)
const loadError = ref('')
const actionError = ref('')
const notice = ref('')
const previewNotes = ref<string[]>([])
const submission = ref<MarketSubmission | null>(null)

/** The repo URL the draft is created from, derived from the GitHub slug. */
const repoUrl = computed(() =>
  props.githubRepository ? `https://github.com/${props.githubRepository}` : ''
)
const phase = computed<SubmissionPhase | null>(() =>
  submission.value ? submissionPhase(submission.value) : null
)
const isOpen = computed(() => !!phase.value && OPEN_PHASES.includes(phase.value))
/** A new draft is possible when nothing matched or the latest submission is closed for good. */
const canCreateDraft = computed(
  () =>
    marketReady.value &&
    loaded.value &&
    !!repoUrl.value &&
    (phase.value === null || RESUBMITTABLE_PHASES.includes(phase.value))
)
const submissionUrl = computed(() => {
  const base = marketAuth.value.market_web_url
  return base && submission.value ? marketSubmissionUrl(base, submission.value.id) : ''
})

const zones = ref<MarketZone[]>([])
const zonesLoading = ref(false)
const zonesError = ref('')
const zoneSlug = ref('')
const tags = ref<string[]>([])

/** The form shows while a draft can be created or the draft can still change. */
const editingMetadata = computed(() => canCreateDraft.value || phase.value === 'draft')
/** The Market blocks a first approval without them; an existing plugin keeps its own. */
const metadataRequired = computed(
  () => phase.value !== 'draft' || submission.value?.plugin_id == null
)
const metadataComplete = computed(() => !!zoneSlug.value && tags.value.length > 0)
const metadataBlocking = computed(() => metadataRequired.value && !metadataComplete.value)
const metadataDirty = computed(() => {
  const snapshot = submission.value?.current_snapshot
  if (phase.value !== 'draft' || !snapshot) return false
  return (
    zoneSlug.value !== (snapshot.zone_slug ?? '') ||
    tags.value.join('\n') !== (snapshot.tags ?? []).join('\n')
  )
})

function zoneLabel(zone: MarketZone): string {
  const key = `market.zones.${zone.id}`
  return te(key) ? t(key) : zone.name
}

const standardTagIds = new Map<string, string>(STANDARD_SUBMISSION_TAGS.map((tag) => [tag.value, tag.id]))
/** The standard tags, plus any other tag a draft already carries. */
const tagOptions = computed(() => [
  ...STANDARD_SUBMISSION_TAGS.map((tag) => tag.value),
  ...tags.value.filter((tag) => !standardTagIds.has(tag)),
])

function tagLabel(tag: string): string {
  const id = standardTagIds.get(tag)
  return id ? t(`authoring.submissionTag${id[0]!.toUpperCase()}${id.slice(1)}`) : tag
}

function toggleTag(tag: string) {
  if (tags.value.includes(tag)) {
    tags.value = tags.value.filter((current) => current !== tag)
  } else if (tags.value.length < SUBMISSION_TAGS_MAX) {
    tags.value = [...tags.value, tag]
  }
}

async function loadZones() {
  zonesLoading.value = true
  zonesError.value = ''
  try {
    zones.value = await listMarketZones()
  } catch {
    zonesError.value = t('authoring.submissionZonesLoadFailed')
  } finally {
    zonesLoading.value = false
  }
}

// A draft read from the Market shows its own zone and tags.
watch(submission, (current) => {
  const snapshot = current?.current_snapshot
  if (!snapshot || submissionPhase(current) !== 'draft') return
  zoneSlug.value = snapshot.zone_slug ?? ''
  tags.value = [...(snapshot.tags ?? [])]
})

function metadataPayload(): Pick<SubmissionDraftUpdate, 'zone_slug' | 'tags'> {
  return { zone_slug: zoneSlug.value || undefined, tags: tags.value }
}

const stateDetail = computed(() => {
  const current = submission.value
  if (!current || !phase.value) return ''
  if (phase.value === 'approved' && current.plugin_id != null) {
    return t('authoring.submissionApprovedDetail', { id: current.plugin_id })
  }
  if (RESUBMITTABLE_PHASES.includes(phase.value)) return t('authoring.submissionClosedDetail')
  if (phase.value === 'draft') return ''
  // The Market localizes its own progress line; it says what the author must do.
  return current.review_progress?.title || t('authoring.submissionInReviewDetail')
})
const progressDescription = computed(() =>
  phase.value && phase.value !== 'draft' && phase.value !== 'approved'
    ? submission.value?.review_progress?.description || ''
    : ''
)

watch(
  [phase, submission],
  () =>
    emit('state', {
      phase: phase.value,
      marketPluginId: phase.value === 'approved' ? (submission.value?.plugin_id ?? null) : null,
    }),
  { immediate: true }
)

function describe(error: unknown, fallback: AuthoringMessageKey): string {
  if (!(error instanceof MarketSubmissionError)) return t(`authoring.${fallback}`)
  if (error.code === 'plugin_slug_conflict') {
    return t('authoring.submissionSlugConflict', { slug: String(error.detail.plugin_slug ?? '') })
  }
  if (error.code === 'plugin_identity_mismatch') {
    return t('authoring.submissionIdentityMismatch', {
      manifest: String(error.detail.manifest_plugin_id ?? ''),
      repository: String(error.detail.repository_plugin_id ?? ''),
    })
  }
  if (Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, error.code)) {
    return t(`authoring.${ERROR_MESSAGES[error.code]}`)
  }
  // The Market localizes its business errors; show them after the action that failed.
  const reason = error.serverMessage
  return reason ? `${t(`authoring.${fallback}`)} ${reason}` : t(`authoring.${fallback}`)
}

let reloadGeneration = 0

/** Read this repository's submissions; `selectId` picks one the Market just named. */
async function reload(selectId?: number) {
  const repository = props.githubRepository
  const generation = ++reloadGeneration
  if (!marketReady.value || !repository) {
    submission.value = null
    loaded.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const page = await listMarketSubmissions(repository)
    if (generation !== reloadGeneration) return
    const items = page.items.filter((item) => submissionMatchesRepository(item, repository))
    submission.value =
      items.find((item) => item.id === selectId) ??
      items.find((item) => item.status !== 'closed') ??
      items[0] ??
      null
    loaded.value = true
  } catch (error) {
    if (generation !== reloadGeneration) return
    submission.value = null
    loaded.value = false
    loadError.value = describe(error, 'submissionLoadFailed')
  } finally {
    if (generation === reloadGeneration) loading.value = false
  }
}

/** Preview the repository; null when the Market cannot read a plugin ID from it. */
async function readPreview(submittedRef?: string): Promise<RepositoryPreview | null> {
  const preview = await previewSubmissionRepository({
    repo_url: repoUrl.value,
    submitted_ref: submittedRef,
  })
  previewNotes.value = [
    ...preview.warnings,
    ...(preview.workflow_status === 'available' ? [] : [t('authoring.submissionWorkflowMissing')]),
  ]
  if (!preview.plugin_id) {
    actionError.value = t('authoring.submissionNoManifest')
    return null
  }
  return preview
}

function startAction() {
  acting.value = true
  actionError.value = ''
  notice.value = ''
  previewNotes.value = []
}

async function createDraft() {
  if (!canCreateDraft.value || acting.value) return
  startAction()
  try {
    const preview = await readPreview()
    if (!preview?.plugin_id) return
    submission.value = await createSubmissionDraft({
      repo_url: repoUrl.value,
      plugin_name: (preview.plugin_name || preview.plugin_id).slice(0, PLUGIN_NAME_MAX_LENGTH),
      plugin_slug: preview.plugin_id,
      ...metadataPayload(),
      description: preview.description ?? undefined,
      short_description: preview.short_description ?? undefined,
      license_name: preview.license_name ?? undefined,
      submitted_ref: preview.submitted_ref,
      resolved_commit: preview.resolved_commit,
    })
  } catch (error) {
    if (error instanceof MarketSubmissionError && error.code === 'open_submission_exists') {
      await reload(Number(error.detail.submission_id))
      notice.value = t('authoring.submissionOpenExists')
      return
    }
    actionError.value = describe(error, 'submissionCreateFailed')
  } finally {
    acting.value = false
  }
}

async function confirm(message: AuthoringMessageKey, title: AuthoringMessageKey, button: AuthoringMessageKey) {
  try {
    await ElMessageBox.confirm(t(`authoring.${message}`), t(`authoring.${title}`), {
      type: 'warning',
      confirmButtonText: t(`authoring.${button}`),
      cancelButtonText: t('common.cancel'),
    })
    return true
  } catch {
    return false
  }
}

async function confirmSubmit() {
  const current = submission.value
  if (!current || acting.value || metadataBlocking.value) return
  if (!(await confirm('submissionConfirmSubmit', 'submissionConfirmSubmitTitle', 'submissionSubmitReview'))) {
    return
  }
  startAction()
  try {
    // The Market resolves the branch again at submit and refuses a draft whose
    // commit is no longer its head, so point the draft at the current head first.
    const snapshot = current.current_snapshot
    const preview = await readPreview(snapshot?.submitted_ref ?? undefined)
    if (!preview?.plugin_id) return
    const update: SubmissionDraftUpdate = {}
    if (preview.resolved_commit !== snapshot?.resolved_commit) {
      update.submitted_ref = preview.submitted_ref
      update.resolved_commit = preview.resolved_commit
    }
    if (preview.plugin_id !== snapshot?.plugin_slug) update.plugin_slug = preview.plugin_id
    if (metadataDirty.value) Object.assign(update, metadataPayload())
    if (Object.keys(update).length) {
      submission.value = await updateSubmissionDraft(current.id, update)
    }
    submission.value = await submitSubmission(current.id)
  } catch (error) {
    actionError.value = describe(error, 'submissionSubmitFailed')
  } finally {
    acting.value = false
  }
}

async function saveMetadata() {
  const current = submission.value
  if (!current || acting.value) return
  startAction()
  try {
    submission.value = await updateSubmissionDraft(current.id, metadataPayload())
  } catch (error) {
    actionError.value = describe(error, 'submissionSaveMetadataFailed')
  } finally {
    acting.value = false
  }
}

async function confirmWithdraw() {
  const current = submission.value
  if (!current || acting.value) return
  if (!(await confirm('submissionConfirmWithdraw', 'submissionConfirmWithdrawTitle', 'submissionWithdraw'))) {
    return
  }
  startAction()
  try {
    submission.value = await withdrawSubmission(current.id)
  } catch (error) {
    actionError.value = describe(error, 'submissionWithdrawFailed')
  } finally {
    acting.value = false
  }
}

// Reading starts once the login is confirmed, and again when the repository changes.
watch([marketReady, () => props.githubRepository], () => void reload())
onMounted(() => {
  void loadMarketAuthStatus()
  void loadZones()
})
onUnmounted(stopMarketAuthPolling)
</script>

<style scoped>
.authoring-submission-metadata .el-select {
  width: 100%;
}
.authoring-submission-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.authoring-submission {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-sm);
}
.authoring-submission-state {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
}
.authoring-submission-notes {
  margin: 0;
  padding-left: var(--spacing-md);
}
</style>
