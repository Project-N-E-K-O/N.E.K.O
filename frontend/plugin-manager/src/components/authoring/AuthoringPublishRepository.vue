<template>
  <div class="authoring-publish-repository">
    <div v-if="canCreate" class="authoring-precheck-actions">
      <el-button type="primary" :icon="Plus" :loading="creating" @click="createRepo">
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
import { onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLocalizedError } from '@/composables/useLocalizedError'
import { Plus } from '@element-plus/icons-vue'
import { createPluginProjectRepo } from '@/api/pluginProjects'
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
}>()
const emit = defineEmits<{ created: [] }>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const creating = ref(false)
const createdUrl = ref('')
const manualSteps = ref<string[]>([])
const createFailure = useLocalizedError(describeError, 'repoCreateFailed')
const createError = createFailure.message
let disposed = false
onUnmounted(() => {
  disposed = true
})

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
