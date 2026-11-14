<template>
  <div class="authoring-publish-repository">
    <div>
      <el-button :loading="updating" @click="previewWorkflows">
        {{ t('authoring.workflowPreview') }}
      </el-button>
    </div>
    <div v-if="changes">
      <ul class="authoring-publish-notes">
        <li v-for="change in changes" :key="change.path">{{ change.path }} · {{ change.status }}</li>
      </ul>
      <el-button :disabled="!canApply" :loading="updating" @click="updateWorkflows">
        {{ t('authoring.workflowApply') }}
      </el-button>
    </div>
    <el-alert v-if="workflowError" :title="workflowError" type="error" :closable="false" show-icon />
    <div v-if="canCreate" class="authoring-precheck-actions">
      <el-button type="primary" :icon="Plus" :loading="creating" :disabled="environmentBlocked" @click="createRepo">
        {{ creating ? t('authoring.publishCreatingRepo') : t('authoring.publishCreateRepo') }}
      </el-button>
    </div>
    <el-alert
      v-if="createdUrl"
      :title="t('authoring.publishRepoCreated', { repo: createdUrl })"
      type="success"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="manualSteps.length"
      :title="t('authoring.publishRepoManualTitle')"
      type="info"
      :closable="false"
      show-icon
    >
      <p class="authoring-small">{{ t('authoring.publishRepoManualHint') }}</p>
      <pre class="authoring-publish-manual">{{ manualSteps.join('\n') }}</pre>
    </el-alert>
    <el-alert v-if="createError" :title="createError" type="error" :closable="false" show-icon />
    <ul v-if="notes.length" class="authoring-publish-notes">
      <li v-for="note in notes" :key="note.key" class="authoring-muted authoring-small">
        {{ t(`authoring.${note.key}`, note.params ?? {}) }}
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLocalizedError } from '@/composables/useLocalizedError'
import { Plus } from '@element-plus/icons-vue'
import {
  applyProjectWorkflows,
  createPluginProjectRepo,
  previewProjectWorkflows,
  type ProjectWorkflowChange,
  type Toolchain,
} from '@/api/pluginProjects'
import type { FlowMessage } from '@/components/authoring/publishFlow'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'

/**
 * Step 1 extras: repository creation, offered only when the project is a git
 * repo with no origin yet (a foreign origin is reported in the step detail),
 * and the non-blocking repository notes.
 */
const props = defineProps<{
  projectId: string
  canCreate: boolean
  notes: FlowMessage[]
  environment?: Toolchain['tool_environment']
}>()
const emit = defineEmits<{ created: [] }>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const creating = ref(false)
const createdUrl = ref('')
const manualSteps = ref<string[]>([])
const createFailure = useLocalizedError(describeError, 'repoCreateFailed')
const createError = createFailure.message
const workflowFailure = useLocalizedError(describeError, 'workflowUpdateFailed')
const workflowError = workflowFailure.message
const changes = ref<ProjectWorkflowChange[]>()
const updating = ref(false)
const canApply = computed(() =>
  changes.value?.some(change => change.status === 'ADD' || change.status === 'UPGRADE') &&
  !changes.value.some(change => change.status === 'CONFLICT')
)
const environmentBlocked = computed(() =>
  !!props.environment && props.environment !== 'source' && props.environment !== 'ready'
)
let disposed = false
onUnmounted(() => {
  disposed = true
})

async function previewWorkflows() {
  updating.value = true
  workflowFailure.clear()
  changes.value = undefined
  try {
    changes.value = await previewProjectWorkflows(props.projectId)
  } catch (error) {
    workflowFailure.set(error)
  } finally {
    updating.value = false
  }
}

async function updateWorkflows() {
  updating.value = true
  workflowFailure.clear()
  try {
    const applied = await applyProjectWorkflows(props.projectId)
    if (applied.some(change => change.status === 'CONFLICT')) {
      changes.value = applied
      return
    }
    changes.value = await previewProjectWorkflows(props.projectId)
    emit('created')
  } catch (error) {
    workflowFailure.set(error)
  } finally {
    updating.value = false
  }
}

async function createRepo() {
  if (creating.value) return
  creating.value = true
  createdUrl.value = ''
  manualSteps.value = []
  createFailure.clear()
  try {
    const result = await createPluginProjectRepo(props.projectId)
    if (disposed) return
    if (result.status === 'created') {
      createdUrl.value = result.repository_url
      emit('created')
    } else if (result.status === 'manual') {
      // gh is unavailable: surface the steps the CLI printed so the user can
      // create the repository by hand, then refresh.
      manualSteps.value = result.log
    } else {
      createFailure.set(result.log.length ? new Error(result.log.join('\n')) : null)
    }
  } catch (error) {
    createFailure.set(error)
  } finally {
    creating.value = false
  }
}
</script>

<style scoped>
.authoring-publish-repository {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
}
.authoring-publish-repository:empty {
  display: none;
}
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
.authoring-publish-notes {
  margin: 0;
  padding-left: var(--spacing-md);
}
</style>
