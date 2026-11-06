<template>
  <div class="authoring-details">
    <el-alert
      v-if="notice"
      :title="t(`authoring.${notice}`, { id: project.plugin_id ?? '' })"
      :type="project.problem === 'runtime_inactive' ? 'info' : 'warning'"
      :closable="false"
      show-icon
    >
      <details v-if="project.error" class="authoring-diagnostic">
        <summary>{{ t('authoring.technicalDetails') }}</summary>
        <code>{{ project.error }}</code>
      </details>
    </el-alert>
    <section class="authoring-details-panel">
      <header class="authoring-details-header">
        <h3>{{ t('authoring.projectInfo') }}</h3>
        <div v-if="!editing">
          <el-button
            :icon="Download"
            text
            :loading="packing"
            :disabled="project.status !== 'ok'"
            @click="buildPackage"
            >{{ t('authoring.buildPackage') }}</el-button
          >
          <el-button
            :icon="Edit"
            type="primary"
            text
            :disabled="project.status !== 'ok'"
            @click="startEditing"
            >{{ t('common.edit') }}</el-button
          >
        </div>
      </header>
      <el-alert v-if="saveError" :title="saveError" type="error" :closable="false" show-icon />
      <form v-if="editing" class="authoring-form authoring-details-form" @submit.prevent="save">
        <label for="project-metadata-name"
          >{{ t('authoring.name') }}
          <el-input
            id="project-metadata-name"
            v-model="draft.name"
            maxlength="80"
            :disabled="saving"
          />
        </label>
        <div class="authoring-details-fields">
          <label for="project-metadata-version"
            >{{ t('authoring.version') }}
            <el-input
              id="project-metadata-version"
              v-model="draft.version"
              maxlength="64"
              :disabled="saving"
            />
          </label>
          <label for="project-metadata-author"
            >{{ t('authoring.author') }}
            <el-input
              id="project-metadata-author"
              v-model="draft.author"
              maxlength="80"
              :disabled="saving"
            />
          </label>
        </div>
        <label for="project-metadata-description"
          >{{ t('authoring.description') }}
          <el-input
            id="project-metadata-description"
            v-model="draft.description"
            type="textarea"
            :rows="6"
            maxlength="250"
            show-word-limit
            :disabled="saving"
          />
        </label>
        <div class="authoring-details-actions">
          <el-button :disabled="saving" @click="cancelEditing">{{ t('common.cancel') }}</el-button>
          <el-button native-type="submit" type="primary" :loading="saving" :disabled="!canSave">
            {{ t('common.save') }}
          </el-button>
        </div>
      </form>
      <el-descriptions v-else :column="2" border>
        <el-descriptions-item :label="t('authoring.version')">
          <span :class="{ 'authoring-muted': !project.version }">{{
            project.version || t('common.noData')
          }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('authoring.author')">
          <span :class="{ 'authoring-muted': !project.author }">{{
            project.author || t('common.noData')
          }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('authoring.description')" :span="2">
          <span :class="{ 'authoring-muted': !project.description }">{{
            project.description || t('common.noData')
          }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('authoring.id')">
          <code v-if="project.plugin_id">{{ project.plugin_id }}</code>
          <span v-else class="authoring-muted">{{ t('common.noData') }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('authoring.entry')">
          <code v-if="project.entry">{{ project.entry }}</code>
          <span v-else class="authoring-muted">{{ t('common.noData') }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('authoring.source')" :span="2">
          <code>{{ project.source_dir }}</code>
        </el-descriptions-item>
      </el-descriptions>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Download, Edit } from '@element-plus/icons-vue'
import { buildPluginCli, downloadPluginPackage } from '@/api/pluginCli'
import {
  updatePluginProjectMetadata,
  type AuthoringProject,
  type PluginProjectMetadata,
} from '@/api/pluginProjects'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'
import { formatHttpError } from '@/utils/request'

const PROBLEM_HINTS = {
  id_taken: 'unloadedIdTaken',
  source_rejected: 'unloadedSourceRejected',
  config_rejected: 'unloadedConfigRejected',
  runtime_inactive: 'unloadedRuntimeInactive',
  not_registered: 'unloadedNotRegistered',
  load_failed: 'loadFailedHint',
  reload_failed: 'reloadFailedHint',
  startup_degraded: 'startupDegradedHint',
} as const

const props = defineProps<{ project: AuthoringProject }>()
const emit = defineEmits<{ updated: [project: AuthoringProject] }>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const editing = ref(false)
const saving = ref(false)
const saveError = ref('')
const packing = ref(false)
const draft = reactive<PluginProjectMetadata>({
  name: '',
  version: '',
  author: '',
  description: '',
})
const canSave = computed(() => !saving.value && !!draft.name.trim() && !!draft.version.trim())
/** The one problem the overview reports, as an `authoring.` key. */
const notice = computed(() => {
  const project = props.project
  if (project.status === 'missing') return 'statusMissingHint'
  if (project.status === 'invalid') return 'statusInvalidHint'
  return project.problem ? PROBLEM_HINTS[project.problem] : null
})

/** Build the project's .neko-plugin from its current source and download it; running or not. */
async function buildPackage() {
  const pluginId = props.project.plugin_id
  if (!pluginId || packing.value) return
  packing.value = true
  try {
    const result = await buildPluginCli({
      mode: 'single',
      // A project folder is named after its plugin ID.
      plugin_ref: { root_id: 'project', directory_name: pluginId },
    })
    const [built] = result.built
    if (!built) {
      const detail = result.failed[0]?.error
      ElMessage.error(
        detail
          ? `${t('authoring.buildPackageFailed')} ${detail}`
          : t('authoring.buildPackageFailed')
      )
      return
    }
    downloadPluginPackage(built.package_path)
    ElMessage.success(t('authoring.buildPackageDone'))
  } catch (error) {
    const detail = formatHttpError(error)
    ElMessage.error(
      detail ? `${t('authoring.buildPackageFailed')} ${detail}` : t('authoring.buildPackageFailed')
    )
  } finally {
    packing.value = false
  }
}

function startEditing() {
  Object.assign(draft, {
    name: props.project.name || '',
    version: props.project.version || '',
    author: props.project.author || '',
    description: props.project.description || '',
  })
  saveError.value = ''
  editing.value = true
}

function cancelEditing() {
  editing.value = false
  saveError.value = ''
}

async function save() {
  if (!canSave.value) return
  saving.value = true
  saveError.value = ''
  try {
    const updated = await updatePluginProjectMetadata(props.project.project_id, { ...draft })
    emit('updated', updated)
    editing.value = false
    ElMessage.success(t('authoring.saved'))
  } catch (error) {
    saveError.value = describeError(error, 'saveFailed')
  } finally {
    saving.value = false
  }
}
</script>
