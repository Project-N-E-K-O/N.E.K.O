<template>
  <el-dialog :model-value="modelValue" :title="t('power.home.createTitle')" width="640px" @update:model-value="emit('update:modelValue', $event)">
    <p class="muted">{{ t('power.home.createHint') }}</p>
    <el-form label-position="top" @submit.prevent>
      <el-form-item :label="t('power.home.about')">
        <el-input
          v-model="about"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 5 }"
          :placeholder="t('power.home.aboutPlaceholder')"
          data-testid="familiar-about"
        />
      </el-form-item>
      <el-form-item :label="t('power.home.folder')">
        <div class="pick">
          <el-input v-model="workdir" :placeholder="t('power.home.folderHint')" data-testid="familiar-folder" />
          <el-button :loading="picking" data-testid="familiar-pick" @click="pick">{{ t('power.home.folderPick') }}</el-button>
        </div>
      </el-form-item>

      <div class="fill">
        <span class="muted">{{ t('power.home.fillHint') }}</span>
        <el-button type="primary" plain :loading="drafting" :disabled="!about.trim()" data-testid="familiar-fill" @click="fill">
          {{ t('power.home.fill') }}
        </el-button>
      </div>

      <el-form-item :label="t('power.home.name')" required>
        <el-input v-model="form.name" maxlength="80" data-testid="familiar-name" />
      </el-form-item>
      <el-form-item :label="t('power.home.title')">
        <el-input v-model="form.title" maxlength="60" :placeholder="t('power.home.titlePlaceholder')" data-testid="familiar-title" />
      </el-form-item>
      <el-form-item :label="t('power.home.preset')">
        <el-select v-model="form.preset" :aria-label="t('power.home.preset')" data-testid="familiar-preset">
          <el-option v-for="item in presets" :key="item.name" :value="item.name" :label="presetName(item)" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('power.home.duty')">
        <el-input v-model="form.duty" maxlength="200" data-testid="familiar-duty" />
      </el-form-item>
      <el-form-item :label="t('power.home.description')">
        <el-input v-model="form.description" maxlength="300" data-testid="familiar-description" />
      </el-form-item>
      <el-form-item :label="t('power.home.rules')">
        <el-input v-model="form.rules" type="textarea" :autosize="{ minRows: 3, maxRows: 10 }" data-testid="familiar-rules" />
        <span class="muted">{{ t('power.home.rulesHint') }}</span>
      </el-form-item>
    </el-form>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="familiar-error" />
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="making" data-testid="familiar-make" @click="make">{{ t('power.home.make') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { consoleErrorCode, draftFamiliar, getTemplates, makeFamiliar, pickDirectory } from '@/api/console'
import type { FamiliarForm, FamiliarTemplate } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [open: boolean]; made: [project: string, sessionId: string] }>()
const { t, te } = useI18n()

const blank = (): FamiliarForm => ({ name: '', title: '', preset: 'boss', duty: '', description: '', rules: '' })
const about = ref('')
const workdir = ref('')
const form = reactive<FamiliarForm>(blank())
const templates = ref<FamiliarTemplate[]>([])
const drafting = ref(false)
const making = ref(false)
const picking = ref(false)
const error = ref('')
// Counts the times the form was opened or closed: what was asked for one form is not put into the next.
let turn = 0

/** The kinds that can be chosen. Until the list is read (or when it cannot be), the one every familiar can be. */
const presets = computed<Array<Pick<FamiliarTemplate, 'name' | 'label'>>>(() => {
  const valid = templates.value.filter(item => item.valid)
  return valid.length ? valid : [{ name: 'boss', label: '' }]
})

function presetName(item: Pick<FamiliarTemplate, 'name' | 'label'>): string {
  const key = `power.home.presets.${item.name}`
  return te(key) ? t(key) : item.label || item.name
}

/** Why a draft or a familiar could not be made, in the user's words. */
function describe(failure: unknown): string {
  if (consoleErrorCode(failure) === 'draft_failed') {
    const reason = (failure as { response?: { data?: { detail?: { reason?: string } } } })?.response?.data?.detail?.reason
    if (reason && te(`power.home.drafts.${reason}`)) return t(`power.home.drafts.${reason}`)
  }
  return describeConsoleError(failure, t, te)
}

async function pick() {
  const mine = turn
  picking.value = true
  try {
    const chosen = await pickDirectory(workdir.value.trim() || undefined)
    if (mine !== turn) return
    if (chosen.path) workdir.value = chosen.path
    error.value = ''
  } catch (failure) {
    if (mine === turn) error.value = describe(failure)
  } finally {
    if (mine === turn) picking.value = false
  }
}

async function fill() {
  const mine = turn
  drafting.value = true
  try {
    const draft = (await draftFamiliar(about.value.trim(), workdir.value.trim())).draft
    // The form was closed, or closed and opened again, meanwhile: the draft was for the other one.
    if (mine !== turn) return
    // What the model drafted takes the place of what is in the form; a kind that cannot be chosen is left as it was.
    Object.assign(form, { ...draft, preset: presets.value.some(item => item.name === draft.preset) ? draft.preset : form.preset })
    error.value = ''
  } catch (failure) {
    if (mine === turn) error.value = describe(failure)
  } finally {
    if (mine === turn) drafting.value = false
  }
}

async function make() {
  if (!form.name.trim()) {
    error.value = t('power.home.nameRequired')
    return
  }
  making.value = true
  try {
    const made = await makeFamiliar({ ...form, name: form.name.trim(), workdir: workdir.value.trim() })
    error.value = ''
    emit('made', made.project.name, made.session_id)
    emit('update:modelValue', false)
  } catch (failure) {
    error.value = describe(failure)
  } finally {
    making.value = false
  }
}

// Opened: an empty form, and the kinds as they are now.
watch(() => props.modelValue, async (open) => {
  turn++
  drafting.value = false
  picking.value = false
  if (!open) return
  about.value = ''
  workdir.value = ''
  Object.assign(form, blank())
  error.value = ''
  try {
    templates.value = (await getTemplates()).templates
  } catch {
    templates.value = []
  }
}, { immediate: true })
</script>

<style scoped>
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
p.muted { margin: 0 0 12px; }
.pick { display: flex; gap: 8px; width: 100%; }
.fill { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin: 4px 0 18px; padding: 10px 12px; border: 1px dashed var(--el-border-color); border-radius: 8px; }
.fill .el-button { flex: none; }
</style>
