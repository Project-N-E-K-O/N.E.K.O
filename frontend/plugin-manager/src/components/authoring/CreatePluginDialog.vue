<template>
  <el-dialog
    align-center
    :model-value="visible"
    :title="t(`authoring.${mode === 'create' ? 'dialogCreate' : 'dialogImport'}`)"
    width="min(800px, 94vw)"
    :close-on-click-modal="!busy"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    class="authoring-dialog"
    @update:model-value="emit('update:visible', $event)"
  >
    <p class="authoring-muted authoring-dialog-intro">
      {{ t(`authoring.${mode === 'create' ? 'dialogHint' : 'importHint'}`) }}
    </p>
    <div class="authoring-wizard-steps" :aria-label="t('authoring.review')">
      <span :class="{ active: step === 0 }"><b>1</b>{{ t('authoring.basics') }}</span
      ><span class="authoring-step-line" /><span :class="{ active: step === 1 }"
        ><b>2</b>{{ t('authoring.review') }}</span
      >
    </div>
    <el-alert
      v-if="backendError"
      :title="backendError"
      type="error"
      :closable="false"
      show-icon
      class="authoring-create-notice"
    />
    <el-alert
      v-if="created"
      :title="t(mounted ? 'authoring.creationMounted' : 'authoring.creationComplete')"
      type="success"
      :closable="false"
      show-icon
      class="authoring-create-notice"
    />
    <div class="authoring-create-grid">
      <form id="authoring-project-form" class="authoring-form" @submit.prevent="advance">
        <div v-if="mode === 'create'">
          <label class="authoring-field-label" for="authoring-parent">{{
            t('authoring.parentDirectory')
          }}</label>
          <div class="authoring-directory-field">
            <el-input id="authoring-parent" v-model="parentDir" :readonly="step === 1 || busy" />
            <el-button :icon="FolderOpened" :disabled="step === 1 || busy" @click="pickParent">{{
              t('authoring.chooseFolder')
            }}</el-button>
          </div>
        </div>
        <label for="authoring-name"
          >{{ t('authoring.name')
          }}<el-input
            id="authoring-name"
            v-model="draft.name"
            maxlength="80"
            :readonly="step === 1 || busy"
        /></label>
        <label for="authoring-id"
          >{{ t('authoring.id')
          }}<el-input
            id="authoring-id"
            v-model="draft.id"
            maxlength="64"
            :readonly="step === 1 || busy"
            placeholder="hello_world"
        /></label>
        <p
          class="authoring-small"
          :class="error ? 'authoring-error-text' : 'authoring-muted'"
          aria-live="polite"
        >
          {{ error || t('authoring.idHint') }}
        </p>
        <label for="authoring-description"
          >{{ t('authoring.description')
          }}<el-input
            id="authoring-description"
            v-model="draft.description"
            type="textarea"
            :rows="2"
            maxlength="250"
            :readonly="step === 1 || busy"
        /></label>
        <label for="authoring-author"
          >{{ t('authoring.author')
          }}<el-input id="authoring-author" v-model="draft.author" :readonly="step === 1 || busy"
        /></label>
        <div>
          <span class="authoring-field-label">{{ t('authoring.type') }}</span
          ><el-radio-group v-model="draft.type" :disabled="step === 1 || busy"
            ><el-radio value="plugin">Plugin</el-radio
            ><el-radio value="adapter">Adapter</el-radio></el-radio-group
          >
        </div>
      </form>
      <aside class="authoring-source-preview">
        <div class="authoring-panel-title">
          <el-icon><FolderOpened /></el-icon>{{ t('authoring.files') }}
        </div>
        <pre>{{ fileTree }}</pre>
        <div class="authoring-source-entry">
          <span>{{ t('authoring.entry') }}</span
          ><code>{{ entry }}</code>
        </div>
      </aside>
    </div>
    <template #footer
      ><div class="authoring-dialog-footer">
        <el-button :disabled="busy" @click="emit('update:visible', false)">{{
          t(created ? 'common.close' : 'common.cancel')
        }}</el-button>
        <div>
          <el-button v-if="created" :disabled="busy" :loading="busy" @click="openCreated">{{
            t('authoring.openFolder')
          }}</el-button>
          <el-button
            v-if="created && !mounted"
            type="primary"
            :disabled="busy"
            :loading="busy"
            @click="mountCreated"
            >{{ t('authoring.mountDevelopment') }}</el-button
          >
          <el-button
            v-if="created && mounted"
            type="primary"
            :disabled="busy"
            @click="goDevelopment"
            >{{ t('authoring.goDevelopment') }}</el-button
          >
          <template v-if="!created">
            <el-button v-if="step === 1" :disabled="busy" @click="step = 0">{{
              t('authoring.previous')
            }}</el-button>
            <el-button
              type="primary"
              native-type="submit"
              form="authoring-project-form"
              :loading="busy"
              :disabled="busy"
              >{{
                t(
                  step === 0
                    ? 'authoring.next'
                    : mode === 'create'
                      ? 'authoring.createPlugin'
                      : 'authoring.addDemo'
                )
              }}</el-button
            >
          </template>
        </div>
      </div></template
    >
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { FolderOpened } from '@element-plus/icons-vue'
import { authoringEntry } from '@/utils/authoringEntry'
import {
  createPluginProject,
  previewPluginProject,
  openProjectDirectory,
  pickProjectDirectory,
  type PluginProjectResult,
  type PluginProjectIdentity,
  type PluginProjectRequest,
} from '@/api/pluginProjects'
import { formatHttpError } from '@/utils/request'
import { mountDevelopment } from '@/api/development'
import type { AuthoringProjectDraft } from '@/types/authoring'

const props = defineProps<{ visible: boolean; mode: 'create' | 'import'; existingIds: string[] }>()
const emit = defineEmits<{
  'update:visible': [value: boolean]
  create: [draft: AuthoringProjectDraft]
  created: [result: PluginProjectResult, key: string]
}>()
const { t } = useI18n()
const router = useRouter()
const step = ref(0)
const attempted = ref(false)
const parentDir = ref('')
const busy = ref(false)
const backendError = ref('')
const preview = ref<PluginProjectResult | null>(null)
const created = ref<PluginProjectResult | null>(null)
const creationKey = ref('')
const mounted = ref(false)
const draft = reactive<AuthoringProjectDraft>({
  id: '',
  name: '',
  description: '',
  author: '',
  type: 'plugin',
})
watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    step.value = 0
    attempted.value = false
    backendError.value = ''
    preview.value = null
    created.value = null
    creationKey.value = crypto.randomUUID()
    mounted.value = false
    Object.assign(draft, { id: '', name: '', description: '', author: '', type: 'plugin' })
  }
)
watch([draft, parentDir], () => {
  if (step.value === 0) {
    preview.value = null
    backendError.value = ''
  }
})
const validId = computed(() => /^[a-z][a-z0-9_]*$/.test(draft.id))
// POSIX root, Windows drive or UNC; the server resolves and validates the rest.
const ABSOLUTE_PATH = /^(?:[a-zA-Z]:[\\/]|\\\\|\/|~)/
const error = computed(() => {
  if (props.mode === 'import' && props.existingIds.includes(draft.id))
    return t('authoring.duplicateId')
  if (draft.id && !validId.value) return t('authoring.invalidId')
  if (attempted.value && (!draft.name.trim() || !validId.value)) return t('authoring.required')
  if (attempted.value && props.mode === 'create' && !ABSOLUTE_PATH.test(parentDir.value.trim()))
    return t('authoring.invalidParent')
  return ''
})
const entry = computed(() =>
  props.mode === 'create'
    ? preview.value?.entry || '—'
    : authoringEntry(validId.value ? draft.id : 'hello_world')
)
const fileTree = computed(() => {
  if (props.mode === 'create') {
    const result = created.value || preview.value
    return result
      ? [
          result.project_dir + '/',
          ...result.files.map(
            (file, index) => `${index === result.files.length - 1 ? '└' : '├'}── ${file}`
          ),
        ].join('\n')
      : '—'
  }
  return `plugin/plugins/${validId.value ? draft.id : 'hello_world'}/\n├── plugin.toml\n├── config.example.toml\n├── __init__.py\n├── pyproject.toml\n├── README.md\n├── tests/test_smoke.py\n├── .github/workflows/\n│   ├── verify.yml\n│   └── release.yml\n└── .git/`
})
const identity = (): PluginProjectIdentity => ({
  plugin_id: draft.id,
  name: draft.name.trim(),
  description: draft.description.trim(),
  author: draft.author.trim(),
  plugin_type: draft.type,
})
const request = (): PluginProjectRequest => ({ parent_dir: parentDir.value.trim(), ...identity() })
function failure(error: unknown) {
  const detail = (error as { response?: { data?: { detail?: { code?: string } } } }).response?.data
    ?.detail
  const keys: Record<string, string> = {
    PROJECT_EXISTS: 'directoryExists',
    INVALID_PARENT: 'invalidParent',
    GIT_NOT_FOUND: 'gitMissing',
    directory_picker_unavailable: 'pickerUnavailable',
    directory_picker_failed: 'pickerUnavailable',
  }
  const code = detail?.code
  backendError.value =
    code && keys[code]
      ? t(`authoring.${keys[code]}`)
      : formatHttpError(error) || t('authoring.creationFailed')
}
async function pickParent() {
  if (busy.value) return
  busy.value = true
  backendError.value = ''
  try {
    const selected = await pickProjectDirectory(
      parentDir.value.trim(),
      t('authoring.parentDirectory')
    )
    if (selected) parentDir.value = selected
  } catch (error) {
    failure(error)
  } finally {
    busy.value = false
  }
}
async function advance() {
  if (busy.value || created.value) return
  attempted.value = true
  if (error.value || !draft.name.trim() || !validId.value) return
  if (props.mode === 'import') {
    if (step.value === 0) step.value = 1
    else
      emit('create', {
        ...draft,
        name: draft.name.trim(),
        description: draft.description.trim(),
        author: draft.author.trim(),
      })
    return
  }
  busy.value = true
  backendError.value = ''
  try {
    if (step.value === 0) {
      preview.value = await previewPluginProject(request())
      step.value = 1
    } else {
      created.value = await createPluginProject(request())
      preview.value = created.value
      emit('created', created.value, creationKey.value)
    }
  } catch (error) {
    failure(error)
  } finally {
    busy.value = false
  }
}
async function openCreated() {
  if (!created.value || busy.value) return
  busy.value = true
  backendError.value = ''
  try {
    await openProjectDirectory(created.value.project_dir)
  } catch (error) {
    failure(error)
  } finally {
    busy.value = false
  }
}
async function mountCreated() {
  if (!created.value || busy.value || mounted.value) return
  busy.value = true
  backendError.value = ''
  try {
    const result = await mountDevelopment(created.value.project_dir)
    if (result.error) throw new Error(result.error)
    mounted.value = true
  } catch (error) {
    backendError.value = t('authoring.mountFailed', {
      reason: formatHttpError(error) || String(error),
    })
  } finally {
    busy.value = false
  }
}
function goDevelopment() {
  emit('update:visible', false)
  void router.push('/development')
}
</script>
