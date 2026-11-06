<template>
  <div class="authoring-submission-draft">
    <el-alert v-if="actionError" :title="actionError" type="error" :closable="false" show-icon />
    <el-alert
      v-if="issues.length"
      :title="t('authoring.submissionPreflightIssues')"
      type="warning"
      :closable="false"
      show-icon
    >
      <ul class="authoring-submission-notes">
        <li v-for="issue in issues" :key="`${issue.field}:${issue.message}`">
          <strong>{{ issue.message }}</strong>
          <code class="authoring-small"> {{ issue.field }}</code>
          <span v-if="issue.current" class="authoring-small">
            · {{ t('authoring.submissionIssueCurrent', { value: issue.current }) }}</span
          >
          <span v-if="issue.expected" class="authoring-small">
            · {{ t('authoring.submissionIssueExpected', { value: issue.expected }) }}</span
          >
          <div v-if="issue.remedy" class="authoring-muted authoring-small">{{ issue.remedy }}</div>
        </li>
      </ul>
    </el-alert>
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

    <el-form label-position="top" size="small" @submit.prevent>
      <el-form-item v-if="!draft" :label="t('authoring.submissionPluginName')" required>
        <el-input
          v-model="pluginName"
          :maxlength="PLUGIN_NAME_MAX_LENGTH"
          :disabled="acting"
          show-word-limit
        />
      </el-form-item>
      <AuthoringSubmissionMetadata
        v-model:zone-slug="zoneSlug"
        v-model:tags="tags"
        :zones="zones"
        :zones-loading="zonesLoading"
        :zones-error="zonesError"
        :required="metadataRequired"
        :disabled="acting"
      />
    </el-form>
    <p v-if="metadataBlocking" class="authoring-muted authoring-small">
      {{ t('authoring.submissionMetadataRequired') }}
    </p>
    <p v-if="!pushedHead" class="authoring-muted authoring-small">
      {{ t('authoring.submissionHeadNotPushed') }}
    </p>

    <div class="authoring-precheck-actions">
      <el-button
        v-if="draft"
        type="primary"
        :icon="Promotion"
        :loading="acting"
        :disabled="metadataBlocking || !pushedHead"
        @click="confirmSubmit"
      >
        {{ t('authoring.submissionSubmitReview') }}
      </el-button>
      <el-button v-if="draft && metadataDirty" :icon="Check" :disabled="acting" @click="saveMetadata">
        {{ t('authoring.submissionSaveMetadata') }}
      </el-button>
      <el-button
        v-if="!draft"
        type="primary"
        :icon="Plus"
        :loading="acting"
        :disabled="metadataBlocking || !pushedHead || !pluginName.trim()"
        @click="createDraft"
      >
        {{ acting ? t('authoring.submissionCreatingDraft') : t('authoring.submissionCreateDraft') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Check, Plus, Promotion } from '@element-plus/icons-vue'
import AuthoringSubmissionMetadata from '@/components/authoring/AuthoringSubmissionMetadata.vue'
import { describeSubmissionError } from '@/components/authoring/submissionErrors'
import {
  MarketSubmissionError,
  PLUGIN_NAME_MAX_LENGTH,
  SHORT_DESCRIPTION_MAX_LENGTH,
  createSubmissionDraft,
  preflightSubmission,
  submitSubmission,
  updateSubmissionDraft,
  type MarketSubmission,
  type MarketZone,
  type PreflightIssue,
  type SubmissionDraftCreate,
  type SubmissionDraftUpdate,
} from '@/api/marketSubmissions'

/**
 * A new draft (when `draft` is null) or the author's open draft: plugin name,
 * zone and tags, then submit. Drafts change through PUT /draft and go to
 * review through /submit; /revision is only for submitted submissions.
 */
const props = defineProps<{
  draft: MarketSubmission | null
  repoUrl: string
  pluginId: string
  pluginName: string
  pluginDescription: string
  /** The commit the Market reviews; null until HEAD is pushed. */
  pushedHead: string | null
  zones: MarketZone[]
  zonesLoading: boolean
  zonesError: string
}>()
const emit = defineEmits<{
  (event: 'updated', submission: MarketSubmission): void
  (event: 'conflict', submissionId: number): void
}>()
const { t } = useI18n()

const acting = ref(false)
const actionError = ref('')
const issues = ref<PreflightIssue[]>([])
const previewNotes = ref<string[]>([])
const pluginName = ref(props.pluginName.slice(0, PLUGIN_NAME_MAX_LENGTH))
const zoneSlug = ref('')
const tags = ref<string[]>([])

// A draft read from the Market shows its own zone and tags.
watch(
  () => props.draft?.current_snapshot,
  (snapshot) => {
    if (!snapshot) return
    zoneSlug.value = snapshot.zone_slug ?? ''
    tags.value = [...(snapshot.tags ?? [])]
  },
  { immediate: true }
)

/** The Market blocks a first approval without them; an already bound plugin keeps its own. */
const metadataRequired = computed(() => props.draft?.plugin_id == null)
const metadataBlocking = computed(
  () => metadataRequired.value && (!zoneSlug.value || tags.value.length === 0)
)
const metadataDirty = computed(() => {
  const snapshot = props.draft?.current_snapshot
  if (!snapshot) return false
  return (
    zoneSlug.value !== (snapshot.zone_slug ?? '') ||
    tags.value.join('\n') !== (snapshot.tags ?? []).join('\n')
  )
})

function metadataPayload(): Pick<SubmissionDraftUpdate, 'zone_slug' | 'tags'> {
  return { zone_slug: zoneSlug.value || undefined, tags: tags.value }
}

function startAction() {
  acting.value = true
  actionError.value = ''
  issues.value = []
  previewNotes.value = []
}

async function createDraft() {
  const head = props.pushedHead
  if (props.draft || !head || acting.value) return
  startAction()
  const description = props.pluginDescription.trim()
  // Pin the pushed HEAD: the first release lists at once only from the approved commit.
  const payload: SubmissionDraftCreate = {
    repo_url: props.repoUrl,
    plugin_name: pluginName.value.trim(),
    plugin_slug: props.pluginId,
    plugin_directory: '',
    description: description || undefined,
    ...metadataPayload(),
    submitted_ref: head,
    resolved_commit: head,
    metadata: { source: 'neko_plugin_workbench' },
  }
  try {
    const check = await preflightSubmission(payload)
    issues.value = check.issues
    const preview = check.preview
    if (preview) {
      previewNotes.value = [
        ...preview.warnings,
        ...(preview.workflow_status === 'available' ? [] : [t('authoring.submissionWorkflowMissing')]),
      ]
    }
    if (!check.ready || !preview) return
    // The Market's own resolution of the repository wins over what was sent.
    const created = await createSubmissionDraft({
      ...payload,
      submitted_ref: preview.submitted_ref,
      resolved_commit: preview.resolved_commit,
      commit_url: preview.commit_url,
      license_name: preview.license_name ?? undefined,
      short_description:
        preview.short_description || description.slice(0, SHORT_DESCRIPTION_MAX_LENGTH) || undefined,
    })
    emit('updated', created)
  } catch (error) {
    if (error instanceof MarketSubmissionError && error.code === 'open_submission_exists') {
      emit('conflict', Number(error.detail.submission_id))
      return
    }
    actionError.value = describeSubmissionError(t, error, 'submissionCreateFailed')
  } finally {
    acting.value = false
  }
}

async function saveMetadata() {
  const current = props.draft
  if (!current || acting.value) return
  startAction()
  try {
    emit('updated', await updateSubmissionDraft(current.id, metadataPayload()))
  } catch (error) {
    actionError.value = describeSubmissionError(t, error, 'submissionSaveMetadataFailed')
  } finally {
    acting.value = false
  }
}

async function confirmSubmit() {
  const current = props.draft
  const head = props.pushedHead
  if (!current || !head || acting.value || metadataBlocking.value) return
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
  startAction()
  try {
    // Submit re-resolves the stored ref and commit, so the draft first points
    // at the pushed HEAD and the current plugin ID.
    const snapshot = current.current_snapshot
    const update: SubmissionDraftUpdate = {}
    if (head.toLowerCase() !== (snapshot?.resolved_commit ?? '').toLowerCase()) {
      update.submitted_ref = head
      update.resolved_commit = head
    }
    if (props.pluginId !== snapshot?.plugin_slug) update.plugin_slug = props.pluginId
    if (metadataDirty.value) Object.assign(update, metadataPayload())
    if (Object.keys(update).length) emit('updated', await updateSubmissionDraft(current.id, update))
    emit('updated', await submitSubmission(current.id))
  } catch (error) {
    actionError.value = describeSubmissionError(t, error, 'submissionSubmitFailed')
  } finally {
    acting.value = false
  }
}
</script>

<style scoped>
.authoring-submission-draft {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.authoring-submission-notes {
  margin: 0;
  padding-left: var(--spacing-md);
}
</style>
