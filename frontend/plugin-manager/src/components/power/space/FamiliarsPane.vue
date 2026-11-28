<template>
  <section class="panel" data-testid="familiars-pane">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="familiars-error" />
    <!-- Of a project: one more familiar for it, and a leader where it has none. Not of the loose ones. -->
    <template v-if="project !== null && loaded && !error">
      <CrewTools :project="project" :has-leader="hasLeader" :lanlan-name="lanlanName" @changed="crewChanged" />
      <ProjectTrees :project="project" :refresh="board?.revision" />
    </template>
    <el-empty v-if="loaded && !members.length && !selectedId" :description="t('power.space.familiars.empty')" />

    <div v-if="members.length" class="members">
      <FamiliarCard
        v-for="card in members"
        :key="card.id"
        :card="card"
        :class="{ chosen: card.id === selectedId, sub: !card.leader && hasLeader }"
        @open="emit('select', card.id)"
      />
    </div>

    <p v-if="members.length && !selected && !selectedId" class="muted">{{ t('power.space.familiars.pick') }}</p>

    <!-- One the address names that is not on the board (not in use for days, its work long over):
         what was said in it and what it used are kept, and are read by its session all the same.
         Said only of a board that was read, and that is the one the address means. -->
    <article v-if="offBoard" class="detail" :data-detail="selectedId" data-testid="familiar-past">
      <p class="muted">{{ t('power.space.familiars.notOnBoard') }}</p>
      <div class="views" role="tablist">
        <button
          v-for="name in PAST_VIEWS"
          :key="name"
          type="button"
          role="tab"
          class="view"
          :class="{ on: pastView === name }"
          :aria-selected="pastView === name"
          :data-view="name"
          @click="pastView = name"
        >{{ t(`power.space.familiars.views.${name}`) }}</button>
      </div>
      <TranscriptPane v-if="pastView === 'talk'" :session-id="selectedId" />
      <UsagePane v-else :session-id="selectedId" />
    </article>

    <article v-if="selected" class="detail" :data-detail="selected.id">
      <header>
        <div>
          <h3>{{ selected.name }}</h3>
          <p class="muted">{{ shownTitle }}<template v-if="selected.duty"> · {{ selected.duty }}</template></p>
        </div>
        <el-button size="small" data-testid="card-edit" @click="edit">{{ t('power.space.familiars.editCard') }}</el-button>
      </header>

      <dl class="facts">
        <div v-if="selected.runtime"><dt>{{ t('power.space.familiars.runtime') }}</dt><dd>{{ selected.runtime === 'builtin' ? t('power.space.familiars.builtinRuntime') : selected.runtime }}</dd></div>
        <div v-if="selected.model">
          <dt>{{ t('power.space.familiars.model') }}</dt>
          <dd>
            {{ selected.model }}
            <el-tooltip v-if="selected.model_fallback" :content="fallbackReason" placement="top">
              <span class="fell-back" tabindex="0" data-testid="familiar-fallback">{{ t('power.roles.fellBack') }}</span>
            </el-tooltip>
          </dd>
        </div>
        <div v-if="selected.context_tokens">
          <dt>{{ t('power.space.familiars.context') }}</dt><dd>{{ selected.context_tokens.toLocaleString() }}</dd>
        </div>
        <!-- Made another kind since it last started work: it is that from its next start. -->
        <div v-if="selected.preset_was" data-testid="familiar-kind-pending">
          <dt>{{ t('power.home.preset') }}</dt>
          <dd>{{ t('power.space.familiars.presetPending', { now: kindName(selected.preset, selected.label), was: kindName(selected.preset_was) }) }}</dd>
        </div>
      </dl>

      <!-- It works in a git worktree of its own, or left what it changed there for the user. -->
      <TreeActions v-if="selected.tree" :key="selected.tree.id" :tree="selected.tree" :title="selected.commission?.title" @changed="treeChanged" />
      <p v-if="treeSaid.said && selected.tree?.id !== treeSaid.tree" class="muted" role="status" data-testid="tree-said">{{ treeSaid.said }}</p>

      <!-- Whether another is to do what waits for it, busy with something else: drawn under it, not there yet. -->
      <div v-if="besides.length" class="besides">
        <BesideAsk
          v-for="item in besides" :key="item.id"
          :item="item" :busy="besideBusyOf(item)" :error="besideFailedOf(item)" @answer="answerBeside(item, $event)"
        />
      </div>

      <!-- What it asks is answered whichever view is open: the page may be opened at what was said in it. -->
      <AskBox v-if="ask" :ask="ask" :busy="busy" :place="selected.commission?.id" @answer="answer" />
      <!-- Said here and not in a view: the question is gone once it is answered, whichever view is open. -->
      <p v-if="answered?.id === selected.id" class="muted" role="status" data-testid="familiar-answer-note">{{ answered.said }}</p>

      <div class="views" role="tablist">
        <button
          v-for="name in VIEWS"
          :key="name"
          type="button"
          role="tab"
          class="view"
          :class="{ on: view === name }"
          :aria-selected="view === name"
          :data-view="name"
          @click="view = name"
        >{{ t(`power.space.familiars.views.${name}`) }}</button>
      </div>
      <TranscriptPane v-if="view === 'talk'" :session-id="selected.id" />
      <!-- Read again whenever the board says it used more, not only when what it has in hand changes. -->
      <UsagePane
        v-else-if="view === 'usage'" :session-id="selected.id"
        :refresh="`${selected.commission?.id}:${selected.state}:${selected.usage?.tokens ?? 0}:${selected.context_tokens ?? 0}`"
      />
      <template v-else>
      <h4>{{ t('power.space.familiars.summary') }}</h4>
      <p class="text" data-testid="familiar-summary">{{ selected.summary || t('power.space.familiars.noSummary') }}</p>

      <h4>{{ t('power.space.familiars.doing') }}</h4>
      <p v-if="!selected.commission" class="muted" data-testid="familiar-idle">{{ t('power.space.familiars.nothing') }}</p>
      <template v-else>
        <p class="text">
          <strong>{{ selected.commission.title }}</strong>
          <template v-if="selected.doing"><br>{{ selected.doing }}</template>
        </p>

        <div v-if="selected.state === 'working'" class="say">
          <el-input v-model="sentence" :placeholder="t('power.space.familiars.say')" data-testid="familiar-say" @keydown="enterKey.down" @keyup.enter="enterKey.up(tell)" />
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
        <!-- What kind of familiar it is: for one that works in a project or on its own, not a leader, a copy or one another started. -->
        <el-form-item v-if="kindChanges" :label="t('power.home.preset')">
          <el-select v-model="draft.preset" data-testid="card-preset" :loading="!kindsRead" :disabled="!kindsRead">
            <el-option v-for="item in kinds" :key="item.name" :label="kindName(item.name, item.label)" :value="item.name" />
          </el-select>
          <p class="muted kind-hint">{{ t('power.space.familiars.presetHint') }}</p>
          <p v-if="kindWidens" class="kind-hint" data-testid="card-preset-widens">{{ t('power.space.familiars.presetWidens') }}</p>
          <p v-if="kindInDsh" class="kind-hint" data-testid="card-preset-in-dsh">{{ t('power.space.familiars.presetInDsh') }}</p>
        </el-form-item>
      </el-form>
      <!-- Said here as well: the page behind the editor is covered by it. -->
      <el-alert v-if="cardError" :title="cardError" type="error" :closable="false" show-icon data-testid="card-error" />
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
import { changeDuty, getProjectBoard, getTemplates, stopCommission, tellCommission, writeCard } from '@/api/console'
import type { Board, BoardCard, FamiliarTemplate } from '@/api/console'
import AskBox from '../AskBox.vue'
import { answeringAs, describeAnswerError, rememberAsks, saysSomething } from '../ask'
import type { GivenAnswer } from '../ask'
import { useBesideAnswers, useBesideAsks } from '../colony/useBesideAsks'
import BesideAsk from '../home/BesideAsk.vue'
import FamiliarCard from '../home/FamiliarCard.vue'
import { describeConsoleError } from '../powerFormat'
import { sendAnswer } from '../useAskAnswers'
import CrewTools from './CrewTools.vue'
import ProjectTrees from './ProjectTrees.vue'
import TranscriptPane from './TranscriptPane.vue'
import TreeActions from './TreeActions.vue'
import UsagePane from './UsagePane.vue'
import { useEnterKey } from '@/utils/enterKey'

/** The familiars of a project, or with `project` null those that belong to none; and the one chosen, up close. */
const props = withDefaults(defineProps<{
  project: string | null
  selectedId: string
  /**
   * False while whoever shows this does not know yet which project the address means (`project`
   * is not that one then): nothing is concluded from a familiar being missing among those shown.
   * True when it is not said (a switch that is left out would read as false).
   */
  settled?: boolean
  /** The view a familiar the address names is opened at: `talk` for what was said in it. Its work, when it is not said. */
  startView?: string
}>(), { settled: true })
const emit = defineEmits<{ select: [id: string] }>()
const { t, te } = useI18n()
const board = ref<Board | null>(null)
const loaded = ref(false)
const error = ref('')
// Why the card was not taken, for its editor alone: reading the board anew neither clears nor sets it.
const cardError = ref('')
const note = ref('')
rememberAsks()
// What is said of an answer that was taken while the sentence added to it was not, and of which
// familiar's: shown with that one only (the answer may come back after another was chosen).
const answered = ref<{ id: string; said: string } | null>(null)
// What came of the last thing done to its worktree: said after the worktree is gone from its card as well.
const treeSaid = ref({ tree: '', said: '' })
const busy = ref('')
const sentence = ref('')
// Enter that picks a word of an input method is not Enter that sends.
const enterKey = useEnterKey()
// Which of the three views of the chosen familiar is open: what it has in hand, what was said in it, what it used.
const VIEWS = ['doing', 'talk', 'usage'] as const
const startingView = (): (typeof VIEWS)[number] => (props.startView === 'talk' ? 'talk' : 'doing')
const view = ref<(typeof VIEWS)[number]>(startingView())
// Of one that is not on the board there is no card to show what it is doing.
const PAST_VIEWS = ['talk', 'usage'] as const
const pastView = ref<(typeof PAST_VIEWS)[number]>('talk')
const editing = ref(false)
const stopping = ref(false)
// Counted up whenever the editor is opened: what a save that was under way meanwhile finds out it is no longer the editor's by.
let editorTurn = 0
const draft = reactive({ title: '', duty: '', summary: '', preset: '' })
// The card as it was when the editor was opened, and whose it is.
const opened = reactive({ id: '', title: '', duty: '', summary: '', preset: '', kind: '', runtime: '' })
// The kinds a familiar can be made, read when the editor is opened: the project's own templates among them.
const templates = ref<FamiliarTemplate[]>([])
const kindsRead = ref(false)
// Only one that works in a project or on its own is made another kind; a leader is no kind a member is made.
const kindChanges = computed(() => opened.kind === 'line' || opened.kind === 'single')
const kinds = computed(() => {
  const offered = templates.value.filter(item => item.valid && item.name !== 'boss')
  // The kind it is stays among them, also while the list is not read or its template cannot be used.
  if (opened.preset && !offered.some(item => item.name === opened.preset)) {
    offered.unshift({ name: opened.preset, label: '', description: '', avatar: 'general', source: '', valid: true, error: '' })
  }
  return offered
})
const kindOf = (name: string) => templates.value.find(item => item.name === name)
// To a kind that changes files, from one that is not known to: said before it is saved. (One whose
// template is gone or wrong is opened with nothing, and the host takes it for one that changes none.)
const kindWidens = computed(() =>
  draft.preset !== opened.preset && kindOf(draft.preset)?.changes_files === true && kindOf(opened.preset)?.changes_files !== true)
// Begun in the other runtime, and to be a kind that withholds tools: it stays there, where the host
// cannot keep that runtime's own tools from it, and is held to its kind by what it is told.
const kindInDsh = computed(() =>
  draft.preset !== opened.preset && !!opened.runtime && opened.runtime !== 'builtin' && kindOf(draft.preset)?.needs_builtin === true)
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
// Whose familiars they are: a new one of the project is that character's as well.
const lanlanName = computed(() => members.value.find(card => card.lanlan_name)?.lanlan_name ?? null)
const selected = computed(() => members.value.find(card => card.id === props.selectedId) ?? null)
// The one the address names is not on the board. Known only once the board was read, of the
// project the address means: a board that could not be read, or another project's, holds nobody.
const offBoard = computed(() => Boolean(
  loaded.value && board.value && !error.value && props.settled && props.selectedId && !selected.value,
))
const ask = computed(() => (selected.value?.state === 'waiting_user' ? selected.value.commission?.ask ?? null : null))
// Whether another familiar is to do what waits for the one chosen, busy with something else (as
// the home page asks it). Looked for only while one of a project is chosen that is at something:
// of none other is it shown here.
const { asks: allBesides, load: loadBesides } = useBesideAsks(
  computed(() => (props.project !== null && selected.value?.commission ? board.value : null)),
)
const besides = computed(() => allBesides.value.filter(item => item.beside === selected.value?.id))
const { busyOf: besideBusyOf, failedOf: besideFailedOf, answer: answerBeside } = useBesideAnswers(
  () => Promise.all([load(), loadBesides()]),
)
const shownTitle = computed(() => {
  const card = selected.value
  if (!card) return ''
  const key = `power.home.presets.${card.preset}`
  return card.title || (te(key) ? t(key) : card.label || card.preset)
})
/** Why the model set for its kind could not be used, in words; the code itself where the page has none for it. */
const fallbackReason = computed(() => {
  const code = selected.value?.model_fallback ?? ''
  const key = `power.roles.fallbacks.${code}`
  return te(key) ? t(key) : code
})

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
async function act(name: string, work: () => Promise<unknown>, done = '', words = describeConsoleError): Promise<boolean> {
  busy.value = name
  note.value = ''
  let took = true
  try {
    await work()
    error.value = ''
    note.value = done
  } catch (failure) {
    took = false
    error.value = words(failure, t, te)
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
  if (!commission || !text || busy.value) return
  if (await act('tell', () => tellCommission(commission.id, text), t('power.space.familiars.sent'))) sentence.value = ''
}

async function answer(given: GivenAnswer) {
  const commission = selected.value?.commission
  // One answer to a question: nothing more is sent while one is on its way.
  if (!commission || !saysSomething(given) || busy.value) return
  const asked = ask.value
  const whose = selected.value?.id ?? ''
  let lost = ''
  if (answered.value?.id === whose) answered.value = null
  const took = await act(answeringAs(given), async () => { lost = await sendAnswer(commission.id, given, asked, t) }, '', describeAnswerError)
  // The answer is in; the sentence that was to go with it was not taken.
  if (took && lost) answered.value = { id: whose, said: lost }
}

async function stop() {
  const commission = selected.value?.commission
  if (!commission) return
  await act('stop', () => stopCommission(commission.id), t('power.space.familiars.stopped'))
  stopping.value = false
}

/** Return the name a kind of familiar is shown by: the page's own for a built-in one, else the template's. */
function kindName(preset: string, label = ''): string {
  const key = `power.home.presets.${preset}`
  return te(key) ? t(key) : label || kindOf(preset)?.label || preset
}

async function readKinds() {
  // Of this opening of the editor: one that was closed and opened again meanwhile, for the same
  // familiar too, reads its own list, and what an earlier reading comes back with is not its.
  const turn = editorTurn
  const mine = () => editorTurn === turn && editing.value
  kindsRead.value = false
  // What was read for the editor before is another familiar's, maybe another project's: nothing
  // is chosen, nor said of what a kind can do, by that list while this one is read.
  templates.value = []
  try {
    const read = (await getTemplates(props.project)).templates
    if (mine()) templates.value = read
  } catch {
    // Not read: the kind it is stays the one choice, and its card can be written all the same.
    if (mine()) templates.value = []
  }
  if (mine()) kindsRead.value = true
}

function edit() {
  const card = selected.value
  if (!card) return
  Object.assign(opened, {
    id: card.id, title: card.title, duty: card.duty, summary: card.summary ?? '', preset: card.preset, kind: card.kind,
    runtime: card.runtime ?? '',
  })
  Object.assign(draft, { title: opened.title, duty: opened.duty, summary: opened.summary, preset: opened.preset })
  editorTurn += 1
  cardError.value = ''
  editing.value = true
  if (kindChanges.value) void readKinds()
}

async function saveCard() {
  if (!opened.id) return
  // Only the lines the user changed, told by what the editor was opened with: the board is read
  // anew every two seconds, and a line a review wrote meanwhile is not the user's to overwrite.
  const lines: { title?: string; duty?: string; summary?: string } = {}
  if (draft.title !== opened.title) lines.title = draft.title
  if (draft.duty !== opened.duty) lines.duty = draft.duty
  if (draft.summary !== opened.summary) lines.summary = draft.summary
  const id = opened.id
  // All of what is saved, as it stands now: while a write is under way the editor may be closed and
  // opened for another familiar, whose choices are not this one's to be sent.
  const kind = kindChanges.value && draft.preset && draft.preset !== opened.preset ? draft.preset : ''
  const turn = editorTurn
  // Whether the editor is still the one this save began in: only then is anything said or closed there.
  const mine = () => editorTurn === turn && editing.value
  // Not taken: the editor stays open with what was typed, and why is said.
  cardError.value = ''
  if (Object.keys(lines).length && !(await act('card', () => writeCard(id, lines)))) {
    if (mine()) cardError.value = error.value
    return
  }
  if (kind) {
    // After its card: a kind that is not taken leaves the lines that were, written.
    let atWork = false
    const change = async () => {
      atWork = (await changeDuty(id, kind)).at_work
    }
    if (!(await act('card', change))) {
      if (mine()) {
        cardError.value = error.value
        // What was written of its card is the card's by now: not sent again with the next try. Only
        // the lines that were sent: one typed while this waited is still to be written.
        Object.assign(opened, lines)
      }
      return
    }
    note.value = t(atWork ? 'power.space.familiars.presetChangedAtWork' : 'power.space.familiars.presetChanged')
  }
  if (mine()) editing.value = false
}

function treeChanged(tree: string, said: string) {
  treeSaid.value = { tree, said }
  void load()
}

/** One more familiar, or a leader, was made: shown at once, and up close. */
async function crewChanged(sessionId: string) {
  const project = props.project
  await load()
  // Another project was opened meanwhile: the new one is not among its familiars.
  if (props.project === project) emit('select', sessionId)
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
  // The editor was opened for the one the user has left.
  editing.value = false
  note.value = ''
  treeSaid.value = { tree: '', said: '' }
  sentence.value = ''
  view.value = startingView()
  pastView.value = 'talk'
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
.kind-hint { width: 100%; margin: 4px 0 0; font-size: 13px; line-height: 1.6; }
.detail { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--el-border-color-lighter); }
.detail header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.detail h3 { margin: 0 0 2px; font-size: 17px; overflow-wrap: anywhere; }
.detail h4 { margin: 16px 0 6px; font-size: 13px; font-weight: 600; color: var(--el-text-color-secondary); }
.facts { display: flex; flex-wrap: wrap; gap: 6px 24px; margin: 10px 0 0; font-size: 13px; }
.facts > div { display: flex; gap: 8px; }
.facts dt { color: var(--el-text-color-secondary); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.text { margin: 0; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.views { display: flex; gap: 4px; margin: 14px 0 4px; border-bottom: 1px solid var(--el-border-color-lighter); }
.view { font: inherit; font-size: 13px; color: var(--el-text-color-secondary); background: none; border: 0; border-bottom: 2px solid transparent; padding: 6px 12px; cursor: pointer; }
.view.on { color: var(--el-color-primary); border-bottom-color: var(--el-color-primary); font-weight: 600; }
.fell-back { margin-left: 6px; padding: 0 6px; font-size: 12px; border-radius: 8px; color: var(--el-color-warning); background: var(--el-color-warning-light-9); cursor: help; }
.say { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.besides { display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }
.grow { flex: 1; }
.block { margin-bottom: 12px; }
</style>
