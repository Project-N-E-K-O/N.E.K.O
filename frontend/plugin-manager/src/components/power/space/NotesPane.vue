<template>
  <section v-loading="loading" class="panel">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" />
    <div class="heading">
      <p class="muted">{{ t('power.space.notes.hint') }}</p>
      <div class="tools">
        <el-button size="small" data-testid="notes-index" @click="showIndex">{{ t('power.space.notes.index') }}</el-button>
        <el-button size="small" data-testid="notes-removed" @click="showRemoved">{{ t('power.space.notes.removed') }}</el-button>
        <el-button v-if="!readonly" size="small" type="primary" data-testid="notes-add" @click="create">{{ t('power.space.notes.add') }}</el-button>
      </div>
    </div>

    <el-empty v-if="!loading && !entries.length" :description="t('power.space.notes.empty')" />
    <table v-else class="rows">
      <thead>
        <tr>
          <th>{{ t('power.space.notes.title') }}</th>
          <th>{{ t('power.space.notes.status') }}</th>
          <th>{{ t('power.space.notes.confidence') }}</th>
          <th>{{ t('power.space.notes.seen') }}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        <tr v-for="entry in entries" :key="entry.id" :data-entry="entry.id">
          <td>
            <div class="title">{{ entry.title }}</div>
            <div v-if="entry.description" class="muted small">{{ entry.description }}</div>
          </td>
          <td>
            <el-tag :type="statusType(entry.status)" size="small" effect="plain">{{ label('statuses', entry.status) }}</el-tag>
            <div v-if="entry.outdated" class="muted small">{{ entry.outdated }}</div>
          </td>
          <td class="muted">{{ label('confidences', entry.confidence || 'none') }}</td>
          <td class="muted">{{ t('power.space.notes.seenTimes', { count: entry.seen }) }}</td>
          <td class="act">
            <el-button size="small" @click="open(entry)">{{ t('power.space.notes.open') }}</el-button>
            <el-button size="small" type="danger" plain @click="askRemove(entry)">{{ t('power.space.notes.remove') }}</el-button>
          </td>
        </tr>
      </tbody>
    </table>

    <el-dialog v-model="editing" :title="t(form.id ? 'power.space.notes.edit' : 'power.space.notes.create')" width="640px">
      <el-form label-position="top" @submit.prevent>
        <el-form-item :label="t('power.space.notes.title')">
          <el-input v-model="form.title" maxlength="200" data-testid="note-title" />
        </el-form-item>
        <el-form-item :label="t('power.space.notes.description')">
          <el-input v-model="form.description" maxlength="300" data-testid="note-description" />
        </el-form-item>
        <el-form-item :label="t('power.space.notes.kind')">
          <el-select v-model="form.kind" :aria-label="t('power.space.notes.kind')">
            <el-option v-for="kind in KINDS" :key="kind" :value="kind" :label="label('kinds', kind)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('power.space.notes.content')">
          <el-input v-model="form.content" type="textarea" :autosize="{ minRows: 6, maxRows: 18 }" data-testid="note-content" />
        </el-form-item>
        <el-form-item v-if="form.evidence.length" :label="t('power.space.notes.evidence')">
          <ul class="evidence"><li v-for="path in form.evidence" :key="path">{{ path }}</li></ul>
        </el-form-item>
      </el-form>
      <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon />
      <template #footer>
        <el-button @click="editing = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="saving" data-testid="note-save" @click="save">{{ t('power.space.notes.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="removing" :title="t('power.space.notes.removeTitle')" width="440px">
      <p>{{ target?.title }}</p>
      <p class="muted">{{ t('power.space.notes.removeText') }}</p>
      <template #footer>
        <el-button @click="removing = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="saving" data-testid="note-remove-confirm" @click="remove">{{ t('power.space.notes.remove') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="reading" :title="readingTitle" width="640px">
      <p class="muted">{{ readingHint }}</p>
      <pre class="plain" data-testid="notes-read">{{ readingText }}</pre>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteEntry, editEntry, fileEntry, listEntries, listRemoved, readEntry, readIndex } from '@/api/console'
import type { SpaceEntry, SpaceScope } from '@/api/console'
import { describeConsoleError } from '../powerFormat'

const props = defineProps<{ scope: SpaceScope; readonly?: boolean }>()
const KINDS = ['lesson', 'reflection', 'reference', 'progress']
const { t, te } = useI18n()
const entries = ref<SpaceEntry[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const editing = ref(false)
const removing = ref(false)
const reading = ref(false)
const readingTitle = ref('')
const readingHint = ref('')
const readingText = ref('')
const formError = ref('')
const target = ref<SpaceEntry | null>(null)
const form = reactive({ id: '', title: '', description: '', kind: 'lesson', content: '', evidence: [] as string[] })
let turn = 0

/** The words for a value of the server, or the value itself when the pages have none for it. */
function label(group: 'statuses' | 'confidences' | 'kinds', value: string): string {
  const key = `power.space.notes.${group}.${value}`
  return te(key) ? t(key) : value
}
const statusType = (status: string) => (status === 'active' ? 'success' : status === 'pending' ? 'warning' : 'info')

/** Read the list; `keep` leaves the message of what just failed where it is. */
async function load(keep = false) {
  const mine = ++turn
  loading.value = true
  try {
    const answer = await listEntries(props.scope)
    if (mine !== turn) return
    entries.value = answer.entries
    if (!keep) error.value = ''
  } catch (failure) {
    if (mine === turn) error.value = describeConsoleError(failure, t, te)
  } finally {
    if (mine === turn) loading.value = false
  }
}

function create() {
  Object.assign(form, { id: '', title: '', description: '', kind: 'lesson', content: '', evidence: [] })
  formError.value = ''
  editing.value = true
}

async function open(entry: SpaceEntry) {
  try {
    const whole = (await readEntry(entry.id, props.scope)).entry
    Object.assign(form, {
      id: whole.id, title: whole.title, description: whole.description, kind: whole.type,
      content: whole.content, evidence: [...whole.evidence, ...whole.evidence_outside],
    })
    formError.value = ''
    error.value = ''
    editing.value = true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
    void load(true)
  }
}

async function save() {
  if (!form.title.trim() || !form.content.trim()) {
    formError.value = t('power.space.notes.required')
    return
  }
  saving.value = true
  try {
    const fields = { title: form.title, description: form.description, kind: form.kind, content: form.content }
    if (form.id) await editEntry(form.id, fields, props.scope)
    else await fileEntry(fields, props.scope)
    editing.value = false
    await load()
  } catch (failure) {
    formError.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
  }
}

function askRemove(entry: SpaceEntry) {
  target.value = entry
  removing.value = true
}

async function remove() {
  if (!target.value) return
  saving.value = true
  let failed = false
  try {
    await deleteEntry(target.value.id, props.scope)
    error.value = ''
  } catch (failure) {
    failed = true
    error.value = describeConsoleError(failure, t, te)
  } finally {
    saving.value = false
    removing.value = false
    await load(failed)
  }
}

async function showIndex() {
  try {
    readingText.value = (await readIndex(props.scope)).text
    readingTitle.value = t('power.space.notes.index')
    readingHint.value = t('power.space.notes.indexHint')
    reading.value = true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  }
}

async function showRemoved() {
  try {
    const removed = (await listRemoved(props.scope)).removed
    const titles = removed.map(item => String((item as { title?: unknown })?.title ?? '')).filter(Boolean)
    readingText.value = titles.length ? titles.join('\n') : t('power.space.notes.removedEmpty')
    readingTitle.value = t('power.space.notes.removed')
    readingHint.value = t('power.space.notes.removedHint')
    reading.value = true
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  }
}

watch(() => [props.scope.project, props.scope.archive_year], () => void load(), { immediate: true })
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.tools { display: flex; gap: 8px; flex: none; }
.tools .el-button + .el-button { margin-left: 0; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.small { font-size: 12px; }
.rows { width: 100%; border-collapse: collapse; margin-top: 12px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: top; }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.title { font-weight: 600; overflow-wrap: anywhere; }
.act { text-align: right; white-space: nowrap; }
.act .el-button + .el-button { margin-left: 6px; }
.evidence { margin: 0; padding-left: 18px; font-size: 12px; overflow-wrap: anywhere; }
.plain { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.6; max-height: 60vh; overflow: auto; }
.block { margin-bottom: 12px; }
</style>
