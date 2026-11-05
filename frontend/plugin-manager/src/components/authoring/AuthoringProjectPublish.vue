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
    </div>
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
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Refresh } from '@element-plus/icons-vue'
import { getPluginProjectPublishStatus, type PublishStatus } from '@/api/pluginProjects'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'

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

const props = defineProps<{ projectId: string }>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const loading = ref(false)
const status = ref<PublishStatus | null>(null)
const requestError = ref('')

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
onMounted(load)

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
