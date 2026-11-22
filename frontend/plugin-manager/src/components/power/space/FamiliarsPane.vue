<template>
  <section class="panel" data-testid="familiars-pane">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="familiars-error" />
    <el-empty v-if="loaded && !members.length" :description="t('power.space.familiars.empty')" />

    <div v-if="members.length" class="members">
      <FamiliarCard
        v-for="card in members"
        :key="card.id"
        :card="card"
        :class="{ chosen: card.id === selectedId, sub: !card.leader && hasLeader }"
        @open="emit('select', card.id)"
      />
    </div>

    <p v-if="members.length && !selected" class="muted">{{ t('power.space.familiars.pick') }}</p>

    <article v-if="selected" class="detail" :data-detail="selected.id">
      <header>
        <div>
          <h3>{{ selected.name }}</h3>
          <p class="muted">{{ shownTitle }}<template v-if="selected.duty"> · {{ selected.duty }}</template></p>
        </div>
        <el-button size="small" data-testid="card-edit" @click="edit">{{ t('power.space.familiars.editCard') }}</el-button>
      </header>

      <dl class="facts">
        <div v-if="selected.runtime"><dt>{{ t('power.space.familiars.runtime') }}</dt><dd>{{ selected.runtime }}</dd></div>
        <div v-if="selected.model"><dt>{{ t('power.space.familiars.model') }}</dt><dd>{{ selected.model }}</dd></div>
        <div v-if="selected.context_tokens">
          <dt>{{ t('power.space.familiars.context') }}</dt><dd>{{ selected.context_tokens.toLocaleString() }}</dd>
        </div>
      </dl>

      <h4>{{ t('power.space.familiars.summary') }}</h4>
      <p class="text" data-testid="familiar-summary">{{ selected.summary || t('power.space.familiars.noSummary') }}</p>

      <h4>{{ t('power.space.familiars.doing') }}</h4>
      <p v-if="!selected.commission" class="muted" data-testid="familiar-idle">{{ t('power.space.familiars.nothing') }}</p>
      <template v-else>
        <p class="text">
          <strong>{{ selected.commission.title }}</strong>
          <template v-if="selected.doing"><br>{{ selected.doing }}</template>
        </p>

        <div v-if="ask" class="ask" data-testid="familiar-ask">
          <strong>{{ t('power.space.familiars.asks') }}</strong>
          <p class="text">{{ askText }}</p>
          <div v-if="ask.options?.length" class="choices">
            <el-button
              v-for="option in ask.options"
              :key="option"
              :type="option === 'deny' ? 'danger' : 'primary'"
              plain
              :loading="busy === `choice:${option}`"
              :disabled="busy !== ''"
              :data-choice="option"
              @click="answer({ choice: option })"
            >{{ choiceLabel(option) }}</el-button>
          </div>
          <div v-else class="say">
            <el-input v-model="reply" :placeholder="t('power.space.familiars.answerPlaceholder')" data-testid="familiar-reply" @keyup.enter="answer({ text: reply })" />
            <el-button type="primary" :loading="busy === 'answer'" :disabled="!reply.trim() || busy !== ''" data-testid="familiar-answer" @click="answer({ text: reply })">
              {{ t('power.space.familiars.answer') }}
            </el-button>
          </div>
        </div>

        <div v-if="selected.state === 'working'" class="say">
          <el-input v-model="sentence" :placeholder="t('power.space.familiars.say')" data-testid="familiar-say" @keyup.enter="tell" />
          <el-button type="primary" :loading="busy === 'tell'" :disabled="!sentence.trim() || busy !== ''" data-testid="familiar-send" @click="tell">
            {{ t('power.space.familiars.send') }}
          </el-button>
        </div>
        <div class="say">
          <span v-if="note" class="muted" role="status" data-testid="familiar-note">{{ note }}</span>
          <span class="grow" />
          <el-button type="danger" plain :disabled="busy !== ''" data-testid="familiar-stop" @click="stopping = true">
            {{ t('power.space.familiars.stop') }}
          </el-button>
        </div>
      </template>
    </article>

    <el-dialog v-model="editing" :title="t('power.space.familiars.editTitle', { name: selected?.name ?? '' })" width="560px">
      <p class="muted">{{ t('power.space.familiars.editHint') }}</p>
      <el-form label-position="top" @submit.prevent>
        <el-form-item :label="t('power.home.title')"><el-input v-model="draft.title" maxlength="60" data-testid="card-title" /></el-form-item>
        <el-form-item :label="t('power.home.duty')"><el-input v-model="draft.duty" maxlength="200" data-testid="card-duty" /></el-form-item>
        <el-form-item :label="t('power.space.familiars.summary')">
          <el-input v-model="draft.summary" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" maxlength="600" data-testid="card-summary" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editing = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy === 'card'" data-testid="card-save" @click="saveCard">{{ t('power.space.familiars.save') }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="stopping" :title="t('power.space.familiars.stopTitle')" width="440px">
      <p>{{ selected?.commission?.title }}</p>
      <p class="muted">{{ t('power.space.familiars.stopText') }}</p>
      <template #footer>
        <el-button @click="stopping = false">{{ t('common.cancel') }}</el-button>
        <el-button type="danger" :loading="busy === 'stop'" data-testid="familiar-stop-confirm" @click="stop">{{ t('power.space.familiars.stop') }}</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { answerCommission, getProjectBoard, stopCommission, tellCommission, writeCard } from '@/api/console'
import type { Board, BoardCard } from '@/api/console'
import FamiliarCard from '../home/FamiliarCard.vue'
import { describeConsoleError } from '../powerFormat'

/** The familiars of a project, or with `project` null those that belong to none; and the one chosen, up close. */
const props = defineProps<{ project: string | null; selectedId: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const { t, te } = useI18n()
const board = ref<Board | null>(null)
const loaded = ref(false)
const error = ref('')
const note = ref('')
const busy = ref('')
const sentence = ref('')
const reply = ref('')
const editing = ref(false)
const stopping = ref(false)
const draft = reactive({ title: '', duty: '', summary: '' })
// Asked for as often as the home panel asks: it answers "unchanged" quickly.
const POLL_MS = 2000
const POLL_FAILED_MS = 15000
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true
let turn = 0

const members = computed<BoardCard[]>(() => {
  const now = board.value
  if (!now) return []
  if (props.project === null) return now.loose ?? []
  return (now.projects ?? []).find(project => project.name === props.project)?.members ?? []
})
const hasLeader = computed(() => members.value.some(card => card.leader))
const selected = computed(() => members.value.find(card => card.id === props.selectedId) ?? null)
const ask = computed(() => (selected.value?.state === 'waiting_user' ? selected.value.commission?.ask ?? null : null))
const shownTitle = computed(() => {
  const card = selected.value
  if (!card) return ''
  const key = `power.home.presets.${card.preset}`
  return card.title || (te(key) ? t(key) : card.label || card.preset)
})
/** What it asks, as words: the question it put, or what it wants to be allowed to do. */
const askText = computed(() => {
  const now = ask.value
  if (!now) return ''
  if (now.question) return now.question
  const detail = now.detail ?? {}
  return [now.category, detail.tool, detail.command, detail.path].filter(Boolean).join(' · ')
})

function choiceLabel(option: string): string {
  const key = `power.space.familiars.choices.${option}`
  return te(key) ? t(key) : option
}

async function load() {
  const mine = ++turn
  if (timer) clearTimeout(timer)
  timer = null
  let wait = POLL_MS
  try {
    const answer = await getProjectBoard(props.project, board.value?.revision)
    if (mine !== turn) return
    if (!answer.unchanged) board.value = answer
    error.value = ''
  } catch (failure) {
    if (mine !== turn) return
    error.value = describeConsoleError(failure, t, te)
    wait = POLL_FAILED_MS
  } finally {
    if (mine === turn) {
      loaded.value = true
      if (alive && !document.hidden) timer = setTimeout(() => void load(), wait)
    }
  }
}

/** Do one thing to the familiar's task, then show where it stands; `done` is said when it took. */
async function act(name: string, work: () => Promise<unknown>, done = ''): Promise<boolean> {
  busy.value = name
  note.value = ''
  let took = true
  try {
    await work()
    error.value = ''
    note.value = done
  } catch (failure) {
    took = false
    error.value = describeConsoleError(failure, t, te)
  }
  const failed = error.value
  await load()
  if (!took) error.value = failed
  busy.value = ''
  return took
}

async function tell() {
  const commission = selected.value?.commission
  const text = sentence.value.trim()
  if (!commission || !text) return
  if (await act('tell', () => tellCommission(commission.id, text), t('power.space.familiars.sent'))) sentence.value = ''
}

async function answer(given: { text?: string; choice?: string }) {
  const commission = selected.value?.commission
  if (!commission || (!given.choice && !given.text?.trim())) return
  const name = given.choice ? `choice:${given.choice}` : 'answer'
  const sent = { ...given, text: given.text?.trim(), question_id: ask.value?.id }
  if (await act(name, () => answerCommission(commission.id, sent))) reply.value = ''
}

async function stop() {
  const commission = selected.value?.commission
  if (!commission) return
  await act('stop', () => stopCommission(commission.id), t('power.space.familiars.stopped'))
  stopping.value = false
}

function edit() {
  const card = selected.value
  if (!card) return
  Object.assign(draft, { title: card.title, duty: card.duty, summary: card.summary ?? '' })
  editing.value = true
}

async function saveCard() {
  const card = selected.value
  if (!card) return
  // Only the lines that were changed: the rest keep whoever wrote them.
  const lines: { title?: string; duty?: string; summary?: string } = {}
  if (draft.title !== card.title) lines.title = draft.title
  if (draft.duty !== card.duty) lines.duty = draft.duty
  if (draft.summary !== (card.summary ?? '')) lines.summary = draft.summary
  if (Object.keys(lines).length) await act('card', () => writeCard(card.id, lines))
  editing.value = false
}

function onVisibility() {
  if (!document.hidden && alive) void load()
}

watch(() => props.project, () => {
  board.value = null
  loaded.value = false
  void load()
}, { immediate: true })
watch(() => props.selectedId, () => {
  note.value = ''
  sentence.value = ''
  reply.value = ''
})
document.addEventListener('visibilitychange', onVisibility)
onBeforeUnmount(() => {
  alive = false
  document.removeEventListener('visibilitychange', onVisibility)
  if (timer) clearTimeout(timer)
})
</script>

<style scoped>
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); padding: 18px 20px; }
.members { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.members .chosen { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary); }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 0; }
.detail { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.detail header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.detail h3 { margin: 0 0 2px; font-size: 17px; overflow-wrap: anywhere; }
.detail h4 { margin: 16px 0 6px; font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); }
.facts { display: flex; flex-wrap: wrap; gap: 6px 24px; margin: 10px 0 0; font-size: 13px; }
.facts > div { display: flex; gap: 8px; }
.facts dt { color: var(--el-text-color-secondary); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.text { margin: 0; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.ask { margin-top: 12px; padding: 12px 14px; border: 1px solid var(--el-color-warning); border-radius: 10px; background: var(--el-color-warning-light-9); display: flex; flex-direction: column; gap: 8px; }
.choices { display: flex; flex-wrap: wrap; gap: 8px; }
.choices .el-button + .el-button { margin-left: 0; }
.say { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.grow { flex: 1; }
.block { margin-bottom: 12px; }
</style>
