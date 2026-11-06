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

      <AuthoringSubmissionStatus
        v-if="submission && phase"
        :key="submission.id"
        :submission="submission"
        :phase="phase"
        :workspace="workspace"
        :market-web-url="marketAuth.market_web_url ?? ''"
        :pushed-head="pushedHead"
        :zones="zones"
        :zones-loading="zonesLoading"
        :zones-error="zonesError"
        @updated="onUpdated"
        @stale="onStale"
      />
      <el-alert
        v-else-if="loaded"
        :title="t('authoring.submissionNone')"
        type="info"
        :closable="false"
        show-icon
      />

      <AuthoringSubmissionDraft
        v-if="canCreateDraft || (submission && phase === 'draft')"
        :key="phase === 'draft' ? submission!.id : 'new'"
        :draft="phase === 'draft' ? submission : null"
        :repo-url="repoUrl"
        :plugin-id="pluginId"
        :plugin-name="pluginName"
        :plugin-description="pluginDescription"
        :pushed-head="pushedHead"
        :zones="zones"
        :zones-loading="zonesLoading"
        :zones-error="zonesError"
        @updated="onUpdated"
        @conflict="onConflict"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { User } from '@element-plus/icons-vue'
import AuthoringSubmissionDraft from '@/components/authoring/AuthoringSubmissionDraft.vue'
import AuthoringSubmissionStatus from '@/components/authoring/AuthoringSubmissionStatus.vue'
import { describeSubmissionError } from '@/components/authoring/submissionErrors'
import { useMarketAuth } from '@/composables/useMarketAuth'
import {
  RESUBMITTABLE_PHASES,
  REVISABLE_PHASES,
  getSubmissionWorkspace,
  listMarketSubmissions,
  listMarketZones,
  submissionMatchesRepository,
  submissionPhase,
  type MarketSubmission,
  type MarketZone,
  type SubmissionPanelState,
  type SubmissionPhase,
  type SubmissionWorkspace,
} from '@/api/marketSubmissions'

/**
 * The Market submission of this repository. This is the one place that reads
 * the Market (submissions, the workspace overview, zones); the draft form and
 * the status part write and hand the result back.
 */
const props = defineProps<{
  githubRepository: string | null
  /** plugin.toml `[plugin].id`: the Market requires it as the plugin slug. */
  pluginId: string
  pluginName: string
  pluginDescription: string
  pushedHead: string | null
}>()
const emit = defineEmits<{ (event: 'state', state: SubmissionPanelState): void }>()

const { t } = useI18n()
const {
  marketAuth,
  marketAuthBusy,
  marketAuthDisplayName,
  marketAuthStateMessageKey,
  loadMarketAuthStatus,
  startMarketLogin,
  stopMarketAuthPolling,
} = useMarketAuth()

const signedIn = computed(() => marketAuth.value.authenticated === true)
/** Signed in and accepted by the Market; only then are submission calls made. */
const marketReady = computed(() => signedIn.value && !marketAuthStateMessageKey.value)
const loading = ref(false)
/** True once the submission list for this repository has been read. */
const loaded = ref(false)
const loadError = ref('')
const notice = ref('')
const submission = ref<MarketSubmission | null>(null)
const workspace = ref<SubmissionWorkspace | null>(null)

/** The repo URL the draft is created from, derived from the GitHub slug. */
const repoUrl = computed(() =>
  props.githubRepository ? `https://github.com/${props.githubRepository}` : ''
)
const phase = computed<SubmissionPhase | null>(() =>
  submission.value ? submissionPhase(submission.value) : null
)
/** A new draft is possible when nothing matched or the latest submission is closed for good. */
const canCreateDraft = computed(
  () =>
    marketReady.value &&
    loaded.value &&
    !!repoUrl.value &&
    (phase.value === null || RESUBMITTABLE_PHASES.includes(phase.value))
)

const zones = ref<MarketZone[]>([])
const zonesLoading = ref(false)
const zonesError = ref('')

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

watch(
  [phase, submission],
  () =>
    emit('state', {
      phase: phase.value,
      marketPluginId: phase.value === 'approved' ? (submission.value?.plugin_id ?? null) : null,
      resolvedCommit: submission.value?.current_snapshot?.resolved_commit ?? null,
    }),
  { immediate: true }
)

let reloadGeneration = 0

/** The workspace overview carries the reviewer feedback waiting for the author. */
async function loadWorkspace(current: MarketSubmission, generation: number) {
  workspace.value = null
  if (!REVISABLE_PHASES.includes(submissionPhase(current))) return
  try {
    const next = await getSubmissionWorkspace(current.id)
    if (generation === reloadGeneration) workspace.value = next
  } catch (error) {
    if (generation === reloadGeneration) {
      loadError.value = describeSubmissionError(t, error, 'submissionWorkspaceLoadFailed')
    }
  }
}

/** Read this repository's submissions; `selectId` picks one the Market just named. */
async function reload(selectId?: number) {
  const repository = props.githubRepository
  const generation = ++reloadGeneration
  if (!marketReady.value || !repository) {
    submission.value = null
    workspace.value = null
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
    if (submission.value) await loadWorkspace(submission.value, generation)
  } catch (error) {
    if (generation !== reloadGeneration) return
    submission.value = null
    workspace.value = null
    loaded.value = false
    loadError.value = describeSubmissionError(t, error, 'submissionLoadFailed')
  } finally {
    if (generation === reloadGeneration) loading.value = false
  }
}

function onUpdated(next: MarketSubmission) {
  notice.value = ''
  loadError.value = ''
  submission.value = next
  // The Market's answer supersedes any read still in flight.
  loading.value = false
  void loadWorkspace(next, ++reloadGeneration)
}

async function onConflict(submissionId: number) {
  await reload(submissionId)
  notice.value = t('authoring.submissionOpenExists')
}

async function onStale() {
  const id = submission.value?.id
  await reload(id)
  notice.value = t('authoring.submissionRevisionStale')
}

// Reading starts once the login is confirmed, and again when the repository changes.
watch([marketReady, () => props.githubRepository], () => void reload())
onMounted(() => {
  void loadMarketAuthStatus()
  void loadZones()
})
onUnmounted(stopMarketAuthPolling)
/** The publish page's single Refresh re-reads the submission through this. */
defineExpose({ reload: () => Promise.all([reload(), zonesError.value ? loadZones() : undefined]) })
</script>

<style scoped>
.authoring-submission {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-sm);
}
</style>
