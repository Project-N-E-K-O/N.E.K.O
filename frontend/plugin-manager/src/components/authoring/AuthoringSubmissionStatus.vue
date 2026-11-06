<template>
  <div class="authoring-submission-status">
    <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon />
    <div class="authoring-submission-state">
      <el-tag size="small" :type="STATE_TAG_TYPES[phase]">{{ t(`authoring.${STATE_LABELS[phase]}`) }}</el-tag>
      <span v-if="stateDetail" class="authoring-muted authoring-small">{{ stateDetail }}</span>
      <a v-if="submissionUrl" class="authoring-small" href="#" @click.prevent="openExternalUrl(submissionUrl)">{{
        t('authoring.submissionOpenOnMarket')
      }}</a>
    </div>
    <p v-if="progressDescription" class="authoring-muted authoring-small">{{ progressDescription }}</p>
    <el-alert
      v-if="feedbackCount"
      :title="t('authoring.submissionFeedbackPending', { count: feedbackCount })"
      type="warning"
      :closable="false"
      show-icon
    >
      <a v-if="submissionUrl" href="#" @click.prevent="openExternalUrl(submissionUrl)">{{
        t('authoring.submissionOpenFeedback')
      }}</a>
    </el-alert>

    <!-- A new revision: the pushed HEAD, a new zone or new tags, with a note for the reviewer. -->
    <div v-if="revising" class="authoring-submission-revision">
      <p class="authoring-small">
        {{
          codeChanged
            ? t('authoring.submissionRevisionCode', { head: shortCommit(pushedHead!), current: shortCommit(submittedCommit) })
            : t('authoring.submissionRevisionSameCode')
        }}
      </p>
      <el-form label-position="top" size="small" @submit.prevent>
        <AuthoringSubmissionMetadata
          v-model:zone-slug="zoneSlug"
          v-model:tags="tags"
          :zones="zones"
          :zones-loading="zonesLoading"
          :zones-error="zonesError"
          :required="false"
          :disabled="acting"
        />
        <el-form-item :label="t('authoring.submissionRevisionNote')" required>
          <el-input
            v-model="note"
            type="textarea"
            :rows="3"
            :maxlength="SUBMISSION_NOTE_MAX_LENGTH"
            :disabled="acting"
            :placeholder="t('authoring.submissionRevisionNotePlaceholder')"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <p v-if="!codeChanged && !metadataChanged" class="authoring-muted authoring-small">
        {{ t('authoring.submissionRevisionNoChange') }}
      </p>
    </div>

    <div class="authoring-precheck-actions">
      <el-button v-if="revisable && !revising" :icon="EditPen" :disabled="acting" @click="startRevision">
        {{ t('authoring.submissionNewRevision') }}
      </el-button>
      <template v-if="revising">
        <el-button
          type="primary"
          :icon="Promotion"
          :loading="acting"
          :disabled="!canSendRevision"
          @click="sendRevision"
        >
          {{ t('authoring.submissionRevisionSend') }}
        </el-button>
        <el-button :disabled="acting" @click="revising = false">{{ t('common.cancel') }}</el-button>
      </template>
      <el-button v-if="withdrawable" :icon="CircleClose" :disabled="acting" @click="confirmWithdraw">
        {{ t('authoring.submissionWithdraw') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { CircleClose, EditPen, Promotion } from '@element-plus/icons-vue'
import AuthoringSubmissionMetadata from '@/components/authoring/AuthoringSubmissionMetadata.vue'
import { describeSubmissionError, isStaleSubmission } from '@/components/authoring/submissionErrors'
import {
  RESUBMITTABLE_PHASES,
  REVISABLE_PHASES,
  SUBMISSION_NOTE_MAX_LENGTH,
  createSubmissionRevision,
  marketSubmissionUrl,
  withdrawSubmission,
  type MarketSubmission,
  type MarketZone,
  type SubmissionPhase,
  type SubmissionRevisionCreate,
  type SubmissionWorkspace,
} from '@/api/marketSubmissions'
import type { AuthoringMessageKey } from '@/i18n/authoring'
import { openExternalUrl } from '@/utils/openExternal'

/** One submission's state on the Market and the author's actions on it. */
const props = defineProps<{
  submission: MarketSubmission
  phase: SubmissionPhase
  /** Read for submitted submissions; null otherwise or until read. */
  workspace: SubmissionWorkspace | null
  marketWebUrl: string
  /** The commit a new revision sends; null until HEAD is pushed. */
  pushedHead: string | null
  zones: MarketZone[]
  zonesLoading: boolean
  zonesError: string
}>()
const emit = defineEmits<{
  (event: 'updated', submission: MarketSubmission): void
  (event: 'stale'): void
}>()
const { t } = useI18n()

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
const OPEN_PHASES: readonly SubmissionPhase[] = ['draft', ...REVISABLE_PHASES]

const acting = ref(false)
const actionError = ref('')

const submissionUrl = computed(() =>
  props.marketWebUrl ? marketSubmissionUrl(props.marketWebUrl, props.submission.id) : ''
)
const withdrawable = computed(() => OPEN_PHASES.includes(props.phase))
const revisable = computed(() => REVISABLE_PHASES.includes(props.phase))
const feedbackCount = computed(() => props.workspace?.overview.current_feedback_action_count ?? 0)

const stateDetail = computed(() => {
  const current = props.submission
  if (props.phase === 'approved' && current.plugin_id != null) {
    return t('authoring.submissionApprovedDetail', { id: current.plugin_id })
  }
  if (RESUBMITTABLE_PHASES.includes(props.phase)) return t('authoring.submissionClosedDetail')
  if (props.phase === 'draft') return ''
  // The Market localizes its own progress line; it says what the author must do.
  return current.review_progress?.title || t('authoring.submissionInReviewDetail')
})
const progressDescription = computed(() =>
  props.phase !== 'draft' && props.phase !== 'approved'
    ? props.submission.review_progress?.description || ''
    : ''
)

const revising = ref(false)
const revisionBase = ref<MarketSubmission['current_snapshot']>(null)
const note = ref('')
const zoneSlug = ref('')
const tags = ref<string[]>([])
const snapshot = computed(() => revising.value ? revisionBase.value : props.submission.current_snapshot)
const submittedCommit = computed(() => snapshot.value?.resolved_commit ?? '')
const codeChanged = computed(
  () => !!props.pushedHead && props.pushedHead.toLowerCase() !== submittedCommit.value.toLowerCase()
)
const zoneChanged = computed(() => zoneSlug.value !== (snapshot.value?.zone_slug ?? ''))
const tagsChanged = computed(() => tags.value.join('\n') !== (snapshot.value?.tags ?? []).join('\n'))
const metadataChanged = computed(() => zoneChanged.value || tagsChanged.value)
const canSendRevision = computed(
  () => (codeChanged.value || metadataChanged.value) && !!note.value.trim() && !acting.value
)

const shortCommit = (sha: string) => sha.slice(0, 7)

function startRevision() {
  revisionBase.value = props.submission.current_snapshot
  zoneSlug.value = snapshot.value?.zone_slug ?? ''
  tags.value = [...(snapshot.value?.tags ?? [])]
  note.value = ''
  actionError.value = ''
  revising.value = true
}

async function sendRevision() {
  const base = snapshot.value
  if (!base || !canSendRevision.value) return
  const body: SubmissionRevisionCreate = { base_snapshot_id: base.id, note: note.value.trim() }
  const commit = codeChanged.value ? props.pushedHead : base.resolved_commit
  if (commit) {
    // An exact commit: with only resolved_commit, a branch ref would be resolved to its head instead.
    body.submitted_ref = commit
    body.resolved_commit = commit
  }
  if (zoneChanged.value) body.zone_slug = zoneSlug.value
  if (tagsChanged.value) body.tags = tags.value
  acting.value = true
  actionError.value = ''
  try {
    const next = await createSubmissionRevision(props.submission.id, body)
    revising.value = false
    emit('updated', next)
  } catch (error) {
    if (isStaleSubmission(error)) {
      revising.value = false
      emit('stale')
      return
    }
    actionError.value = describeSubmissionError(t, error, 'submissionRevisionFailed')
  } finally {
    acting.value = false
  }
}

async function confirmWithdraw() {
  if (acting.value) return
  try {
    await ElMessageBox.confirm(
      t('authoring.submissionConfirmWithdraw'),
      t('authoring.submissionConfirmWithdrawTitle'),
      {
        type: 'warning',
        confirmButtonText: t('authoring.submissionWithdraw'),
        cancelButtonText: t('common.cancel'),
      }
    )
  } catch {
    return
  }
  acting.value = true
  actionError.value = ''
  try {
    emit('updated', await withdrawSubmission(props.submission.id))
  } catch (error) {
    actionError.value = describeSubmissionError(t, error, 'submissionWithdrawFailed')
  } finally {
    acting.value = false
  }
}
</script>

<style scoped>
.authoring-submission-status {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.authoring-submission-state {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
}
.authoring-submission-revision p {
  margin: 0;
}
</style>
