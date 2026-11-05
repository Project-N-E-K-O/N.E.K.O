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
      <el-alert v-if="loadError" :title="loadError" type="error" :closable="false" show-icon />

      <!-- Matched submission: show its state and the one action it allows. -->
      <template v-if="submission">
        <div class="authoring-submission-state">
          <el-tag size="small" :type="STATE_TAG_TYPES[phase]">{{ t(STATE_LABELS[phase]) }}</el-tag>
          <span v-if="stateDetail" class="authoring-muted authoring-small">{{ stateDetail }}</span>
        </div>
        <div v-if="phase === 'draft'" class="authoring-precheck-actions">
          <el-button
            type="primary"
            :icon="Promotion"
            :loading="acting"
            @click="confirmSubmit"
          >
            {{ t('authoring.submissionSubmitReview') }}
          </el-button>
        </div>
      </template>

      <!-- No submission yet: preview, then create a draft. -->
      <template v-else>
        <el-alert
          :title="t('authoring.submissionNone')"
          type="info"
          :closable="false"
          show-icon
        />
        <el-alert
          v-if="previewWarning"
          :title="previewWarning"
          type="warning"
          :closable="false"
          show-icon
        />
        <div class="authoring-precheck-actions">
          <el-button
            type="primary"
            :icon="Plus"
            :disabled="!canCreateDraft"
            :loading="acting"
            @click="createDraft"
          >
            {{ acting ? t('authoring.submissionCreatingDraft') : t('authoring.submissionCreateDraft') }}
          </el-button>
          <el-button :icon="Refresh" :loading="loading" @click="reload">
            {{ t('authoring.publishRefresh') }}
          </el-button>
        </div>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Promotion, Refresh, User } from '@element-plus/icons-vue'
import { useMarketAuth } from '@/composables/useMarketAuth'
import {
  MarketSubmissionError,
  createSubmissionDraft,
  listMarketSubmissions,
  previewSubmissionRepository,
  submitSubmission,
  type MarketSubmission,
} from '@/api/marketSubmissions'
import type { AuthoringMessageKey } from '@/i18n/authoring'

const props = defineProps<{
  projectId: string
  pluginId: string
  githubRepository: string | null
  pluginName: string | null
}>()
const emit = defineEmits<{ (event: 'changed'): void }>()

const { t } = useI18n()
const {
  marketAuth,
  marketAuthBusy,
  marketAuthDisplayName,
  loadMarketAuthStatus,
  startMarketLogin,
} = useMarketAuth()

type Phase = 'draft' | 'submitted' | 'in_review' | 'approved' | 'rejected' | 'closed' | 'withdrawn'
const STATE_TAG_TYPES: Record<Phase, 'success' | 'warning' | 'info' | 'danger'> = {
  draft: 'warning',
  submitted: 'info',
  in_review: 'info',
  approved: 'success',
  rejected: 'danger',
  closed: 'info',
  withdrawn: 'info',
}
const STATE_LABELS: Record<Phase, AuthoringMessageKey> = {
  draft: 'submissionStateDraft',
  submitted: 'submissionStateSubmitted',
  in_review: 'submissionStateInReview',
  approved: 'submissionStateApproved',
  rejected: 'submissionStateRejected',
  closed: 'submissionStateClosed',
  withdrawn: 'submissionStateWithdrawn',
}

const signedIn = computed(() => marketAuth.value.authenticated === true)
const loading = ref(false)
const acting = ref(false)
const loadError = ref('')
const previewWarning = ref('')
const submission = ref<MarketSubmission | null>(null)

/** The repo URL the draft is created from, derived from the GitHub slug. */
const repoUrl = computed(() =>
  props.githubRepository ? `https://github.com/${props.githubRepository}` : ''
)
const canCreateDraft = computed(() => !!repoUrl.value && !acting.value)

const phase = computed<Phase>(() => normalizePhase(submission.value))
const stateDetail = computed(() => {
  const current = submission.value
  if (!current) return ''
  if (phase.value === 'approved' && current.plugin_id != null) {
    return t('authoring.submissionApprovedDetail', { id: current.plugin_id })
  }
  if (phase.value === 'in_review' || phase.value === 'submitted') {
    return t('authoring.submissionInReviewDetail')
  }
  const note = current.review_progress?.terminal_kind
  return note ? t('authoring.submissionTimelineReason', { note }) : ''
})

/** Map the Market's status/decision onto the local phase the UI renders. */
function normalizePhase(current: MarketSubmission | null): Phase {
  if (!current) return 'draft'
  const status = String(current.status || '').toLowerCase()
  const decision = String(current.decision || '').toLowerCase()
  if (status === 'closed') {
    if (decision === 'approved') return 'approved'
    if (decision === 'rejected') return 'rejected'
    if (decision === 'withdrawn') return 'withdrawn'
    return 'closed'
  }
  if (status === 'in_review') return 'in_review'
  if (status === 'submitted') return 'submitted'
  return 'draft'
}

/** True when a submission's repository matches this project's GitHub repo. */
function matchesProject(item: MarketSubmission): boolean {
  const repo = props.githubRepository?.toLowerCase()
  if (!repo) return false
  const candidates = [
    item.repo_name,
    item.snapshot?.repo_name,
    item.repo_url,
    item.snapshot?.repo_url,
  ]
  return candidates.some((value) => {
    const text = String(value || '').toLowerCase()
    return text === repo || text.endsWith(`/${repo}`) || text.includes(`github.com/${repo}`)
  })
}

function describe(error: unknown, fallback: AuthoringMessageKey): string {
  if (error instanceof MarketSubmissionError) {
    const key = errorMessageKey(error.code)
    if (key) return t(`authoring.${key}`)
  }
  return t(`authoring.${fallback}`)
}

/** Only the submission error codes this feature can produce map to a message. */
function errorMessageKey(code: string): AuthoringMessageKey | undefined {
  const table: Record<string, AuthoringMessageKey> = {
    market_auth_required: 'marketAuthRequired',
    market_email_unverified: 'marketEmailUnverified',
    market_auth_rejected: 'marketAuthRejected',
    market_submission_unavailable: 'marketSubmissionUnavailable',
    market_submission_failed: 'marketSubmissionFailed',
    market_bridge_unreachable: 'marketBridgeUnreachable',
  }
  return Object.prototype.hasOwnProperty.call(table, code) ? table[code] : undefined
}

async function reload() {
  if (loading.value || !signedIn.value) return
  loading.value = true
  loadError.value = ''
  try {
    const result = await listMarketSubmissions()
    const items = result.submissions ?? []
    submission.value = items.find(matchesProject) ?? null
  } catch (error) {
    submission.value = null
    loadError.value = describe(error, 'submissionLoadFailed')
  } finally {
    loading.value = false
  }
}

async function createDraft() {
  if (!canCreateDraft.value) return
  acting.value = true
  previewWarning.value = ''
  try {
    // Preview first so a naming conflict is shown before a doomed draft.
    const preview = await previewSubmissionRepository({ repo_url: repoUrl.value })
    if (preview.identity_status === 'mismatched' && preview.expected_repository_name) {
      previewWarning.value = t('authoring.submissionMismatch', {
        expected: preview.expected_repository_name,
      })
      return
    }
    const created = await createSubmissionDraft({
      repo_url: repoUrl.value,
      plugin_name: preview.plugin_name || props.pluginName || props.pluginId,
      description: preview.description ?? undefined,
      short_description: preview.short_description ?? undefined,
      license_name: preview.license_name ?? undefined,
      submitted_ref: preview.submitted_ref ?? undefined,
      resolved_commit: preview.resolved_commit ?? undefined,
    })
    submission.value = created
    emit('changed')
  } catch (error) {
    if (error instanceof MarketSubmissionError && error.status >= 400 && error.status < 500) {
      previewWarning.value = t('authoring.submissionCreateFailed')
      return
    }
    ElMessage.error(describe(error, 'submissionCreateFailed'))
  } finally {
    acting.value = false
  }
}

async function confirmSubmit() {
  const current = submission.value
  if (!current) return
  try {
    await ElMessageBox.confirm(
      t('authoring.submissionConfirmSubmit'),
      t('authoring.submissionConfirmSubmitTitle'),
      {
        type: 'warning',
        confirmButtonText: t('authoring.submissionSubmitReview'),
        cancelButtonText: t('common.cancel'),
      }
    )
  } catch {
    return
  }
  acting.value = true
  try {
    submission.value = await submitSubmission(current.id)
    emit('changed')
  } catch (error) {
    ElMessage.error(describe(error, 'submissionSubmitFailed'))
  } finally {
    acting.value = false
  }
}

onMounted(async () => {
  await loadMarketAuthStatus()
  if (signedIn.value) await reload()
})

defineExpose({ phase, submission })
</script>

<style scoped>
.authoring-submission {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-sm);
}
.authoring-submission-state {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
}
</style>
