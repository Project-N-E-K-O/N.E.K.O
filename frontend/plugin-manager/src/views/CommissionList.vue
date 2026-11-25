<template>
  <div class="commissions">
    <header class="page-heading">
      <h1>{{ t('power.commissions.title') }}</h1>
      <p>{{ t('power.commissions.subtitle') }}</p>
    </header>

    <div class="filters">
      <select v-model="status" class="choice" :aria-label="t('power.commissions.status')" data-testid="commissions-status">
        <option value="">{{ t('power.commissions.allStatuses') }}</option>
        <option v-for="name in STATUSES" :key="name" :value="name">{{ statusName(name) }}</option>
      </select>
      <select v-model="scope" class="choice" :aria-label="t('power.commissions.project')" data-testid="commissions-project">
        <option value="">{{ t('power.commissions.allProjects') }}</option>
        <option :value="LOOSE">{{ t('power.home.loose') }}</option>
        <option v-for="name in projects" :key="name" :value="`p:${name}`">{{ name }}</option>
      </select>
      <select v-model.number="days" class="choice" :aria-label="t('power.commissions.since')" data-testid="commissions-days">
        <option :value="7">{{ t('power.commissions.days', { count: 7 }) }}</option>
        <option :value="30">{{ t('power.commissions.days', { count: 30 }) }}</option>
        <option :value="0">{{ t('power.commissions.allTime') }}</option>
      </select>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="block" data-testid="commissions-error" />
    <p v-if="loaded && !rows.length" class="muted pad" data-testid="commissions-empty">{{ t('power.commissions.empty') }}</p>

    <table v-if="rows.length" class="rows" data-testid="commissions-table">
      <thead>
        <tr>
          <th>{{ t('power.commissions.status') }}</th>
          <th>{{ t('power.commissions.what') }}</th>
          <th>{{ t('power.commissions.who') }}</th>
          <th>{{ t('power.commissions.project') }}</th>
          <th>{{ t('power.commissions.began') }}</th>
          <th>{{ t('power.commissions.took') }}</th>
          <th>{{ t('power.commissions.used') }}</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="row.item.id">
          <tr class="row" :class="{ child: row.child, open: opened === row.item.id }" :data-commission="row.item.id" @click="toggle(row.item)">
            <td><span class="state" :class="`state--${row.item.status}`">{{ statusName(row.item.status) }}</span></td>
            <td class="what">
              <button
                type="button" class="title" :aria-expanded="opened === row.item.id" :aria-controls="`commission-detail-${row.item.id}`"
                @click.stop="toggle(row.item)"
              >{{ row.item.title }}</button>
            </td>
            <td>{{ doer(row.item) }}</td>
            <td>{{ row.item.project || t('power.home.loose') }}</td>
            <td class="num">{{ began(row.item) }}</td>
            <td class="num">{{ formatDuration(took(row.item)) }}</td>
            <td class="num">{{ row.item.usage.tokens ? formatTokens(row.item.usage.tokens) : '' }}</td>
          </tr>
          <tr v-if="opened === row.item.id" :id="`commission-detail-${row.item.id}`" class="detail" :data-detail="row.item.id">
            <td colspan="7">
              <p v-if="row.item.result" class="text"><strong>{{ t('power.commissions.result') }}</strong> {{ row.item.result }}</p>
              <p v-if="row.item.reason && saysWhy(row.item)" class="muted">{{ t('power.commissions.reason') }} {{ row.item.reason }}</p>
              <p v-if="usage" class="muted" data-testid="commission-usage">
                {{ t('power.commissions.usage', {
                  requests: usage.commission.requests, input: formatTokens(usage.commission.input + usage.commission.cache_read + usage.commission.cache_write),
                  cached: formatTokens(usage.commission.cache_read), output: formatTokens(usage.commission.output),
                }) }}
                <template v-if="usage.limit"> · {{ t('power.usage.ofLimit', { used: formatTokens(usage.used), limit: formatTokens(usage.limit) }) }}</template>
              </p>
              <AskBox v-if="row.item.ask" :ask="row.item.ask" :busy="busy" @answer="answer(row.item, $event)" />
              <div v-if="row.item.status === 'working' && row.item.route === 'task_agent'" class="say">
                <el-input v-model="sentence" :placeholder="t('power.space.familiars.say')" data-testid="commission-say" @keyup.enter="tell(row.item)" />
                <el-button type="primary" :loading="busy === 'tell'" :disabled="!sentence.trim() || busy !== ''" data-testid="commission-send" @click="tell(row.item)">
                  {{ t('power.space.familiars.send') }}
                </el-button>
              </div>
              <div class="say">
                <span v-if="note" class="muted" role="status">{{ note }}</span>
                <span class="grow" />
                <el-button v-if="row.item.session_id" size="small" data-testid="commission-familiar" @click.stop="openFamiliar(row.item)">
                  {{ t('power.commissions.openFamiliar') }}
                </el-button>
                <el-button
                  v-if="!isOver(row.item)" size="small" type="danger" plain :loading="busy === 'stop'" :disabled="busy !== ''"
                  data-testid="commission-stop" @click.stop="stop(row.item)"
                >{{ t('power.space.familiars.stop') }}</el-button>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
    <p v-if="more" class="muted pad" data-testid="commissions-more">{{ t('power.commissions.more') }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { getCommissionUsage, listCommissions, listProjects, stopCommission, tellCommission } from '@/api/console'
import type { CommissionRow, CommissionUsage } from '@/api/console'
import AskBox from '@/components/power/AskBox.vue'
import { answeringAs, describeAnswerError, saysSomething } from '@/components/power/ask'
import type { GivenAnswer } from '@/components/power/ask'
import { sendAnswer } from '@/components/power/useAskAnswers'
import { clockTime } from '@/components/power/colony/activity'
import { describeConsoleError, formatDuration, formatTokens } from '@/components/power/powerFormat'

/** Everything she commissioned: what is under way, and what ended lately. */
const STATUSES = ['working', 'waiting_user', 'queued', 'resting', 'done', 'failed', 'cancelled', 'blocked']
const OVER = new Set(['done', 'failed', 'cancelled'])
const LOOSE = 'loose'
const POLL_MS = 5000
const { t, te } = useI18n()
const router = useRouter()
const items = ref<CommissionRow[]>([])
const more = ref(false)
const loaded = ref(false)
const error = ref('')
const status = ref('')
const scope = ref('')
const days = ref(7)
const projects = ref<string[]>([])
const opened = ref('')
const usage = ref<CommissionUsage | null>(null)
const sentence = ref('')
// Which draft it is: another one with every change, also when the same words are typed again.
let draft = 0
watch(sentence, () => { draft += 1 }, { flush: 'sync' })
const busy = ref('')
const note = ref('')
let timer: ReturnType<typeof setTimeout> | null = null
let alive = true
let turn = 0

/** In the order they came, one that a familiar started under the one it was started for. */
const rows = computed(() => {
  const here = new Set(items.value.map(item => item.id))
  const below = new Map<string, CommissionRow[]>()
  for (const item of items.value) {
    if (item.parent_id && here.has(item.parent_id)) {
      const list = below.get(item.parent_id) ?? []
      list.push(item)
      below.set(item.parent_id, list)
    }
  }
  const made: Array<{ item: CommissionRow; child: boolean }> = []
  const add = (item: CommissionRow, child: boolean) => {
    made.push({ item, child })
    // The ones it started in the order they were started.
    for (const next of [...(below.get(item.id) ?? [])].reverse()) add(next, true)
  }
  for (const item of items.value) if (!item.parent_id || !here.has(item.parent_id)) add(item, false)
  return made
})

function statusName(name: string): string {
  const key = `power.commissions.statuses.${name}`
  return te(key) ? t(key) : name
}
const isOver = (item: CommissionRow) => OVER.has(item.status)
// Why it stopped is said of one that is stuck as well; that one can still be called off.
const saysWhy = (item: CommissionRow) => isOver(item) || item.status === 'blocked'
function doer(item: CommissionRow): string {
  if (item.session) {
    // The title it earned; the name of a project's leader (the project's); else what kind it is
    // (the name of any other is the commission's title, or its starter's with a number).
    const { kind: sessionKind, name, preset, title } = item.session
    const key = `power.home.presets.${preset}`
    const kind = te(key) ? t(key) : preset
    if (sessionKind === 'line') {
      // A member of a project: the project, then the member as its card names it, by its title or its kind
      // (its own name is the host's, made of the project's and its kind's).
      return `${item.project || name} · ${title || kind}`
    }
    return title || (sessionKind === 'project' && name) || kind
  }
  return item.plugin_id ? t('power.commissions.byPlugin', { plugin: item.plugin_id }) : ''
}
function began(item: CommissionRow): string {
  const at = item.started_at ?? item.created_at
  const date = new Date(at * 1000)
  const today = new Date().toDateString() === date.toDateString()
  return today ? clockTime(at) : `${date.getMonth() + 1}-${String(date.getDate()).padStart(2, '0')} ${clockTime(at)}`
}
function took(item: CommissionRow): number | null {
  if (!item.started_at) return null
  return (item.ended_at ?? Date.now() / 1000) - item.started_at
}

async function load() {
  const mine = ++turn
  if (timer) clearTimeout(timer)
  timer = null
  try {
    const answer = await listCommissions({
      status: status.value ? [status.value] : undefined,
      loose: scope.value === LOOSE,
      project: scope.value.startsWith('p:') ? scope.value.slice(2) : null,
      days: days.value,
    })
    if (mine !== turn) return
    items.value = answer.commissions
    more.value = answer.more
    error.value = ''
    if (usageFailed) void readUsage()
  } catch (failure) {
    if (mine !== turn) return
    error.value = describeConsoleError(failure, t, te)
  } finally {
    if (mine === turn) {
      loaded.value = true
      if (alive && !document.hidden) timer = setTimeout(() => void load(), POLL_MS)
    }
  }
}

async function toggle(item: CommissionRow) {
  note.value = ''
  sentence.value = ''
  usage.value = null
  if (opened.value === item.id) {
    opened.value = ''
    return
  }
  opened.value = item.id
}
// What the one that is open used: read when it is opened, and again whenever the list says it
// used more or stands otherwise (it went on, it ended). Of several reads the one asked for last
// holds, whichever answers first: an older answer would put back what it had used before.
let usageTurn = 0
// Whether the read asked for last failed: it is asked for again when the list is read next.
let usageFailed = false
async function readUsage() {
  const id = opened.value
  const mine = ++usageTurn
  usageFailed = false
  if (!id) return
  try {
    const answer = await getCommissionUsage(id)
    if (mine === usageTurn) usage.value = answer
  } catch {
    // Shown without what it used, until it can be read.
    if (mine === usageTurn) usageFailed = true
  }
}
watch(() => {
  const item = items.value.find(some => some.id === opened.value)
  return item ? `${item.id}:${item.status}:${item.usage.requests}:${item.usage.tokens}` : ''
}, key => {
  if (key) void readUsage()
})

async function act(name: string, work: () => Promise<unknown>, done = '', words = describeConsoleError) {
  busy.value = name
  note.value = ''
  let failed = ''
  try {
    await work()
    note.value = done
  } catch (failure) {
    failed = words(failure, t, te)
  }
  await load()
  error.value = failed
  busy.value = ''
  return !failed
}
async function tell(item: CommissionRow) {
  const text = sentence.value.trim()
  if (!text || busy.value) return
  const mine = draft
  const sent = await act('tell', () => tellCommission(item.id, text), t('power.space.familiars.sent'))
  // Only what was sent is taken away: what was typed since, for this one or another, stays.
  if (sent && draft === mine) sentence.value = ''
}
async function answer(item: CommissionRow, given: GivenAnswer) {
  // One answer to a question: nothing more is sent while one is on its way.
  if (!saysSomething(given) || busy.value) return
  let lost = ''
  const took = await act(answeringAs(given), async () => { lost = await sendAnswer(item.id, given, item.ask, t) }, '', describeAnswerError)
  // The answer is in; the sentence that was to go with it was not taken.
  if (took && lost) note.value = lost
}
const stop = (item: CommissionRow) => act('stop', () => stopCommission(item.id), t('power.space.familiars.stopped'))

function openFamiliar(item: CommissionRow) {
  void router.push({ path: '/familiars/space', query: { project: item.project || undefined, familiar: item.session_id || undefined } })
}

function onVisibility() {
  if (!document.hidden && alive) void load()
}
watch([status, scope, days], () => void load())
onMounted(async () => {
  document.addEventListener('visibilitychange', onVisibility)
  void load()
  try {
    projects.value = (await listProjects()).projects.filter(project => project.status !== 'archived').map(project => project.name)
  } catch {
    // The filter offers no project then; the list itself says what went wrong.
  }
})
onBeforeUnmount(() => {
  alive = false
  document.removeEventListener('visibilitychange', onVisibility)
  if (timer) clearTimeout(timer)
})
</script>

<style scoped>
.commissions { max-width: 1200px; margin: 0 auto; padding: var(--spacing-lg, 24px); }
.page-heading { margin-bottom: 14px; }
h1 { margin: 0 0 8px; font-size: 26px; }
.page-heading p { margin: 0; color: var(--el-text-color-secondary); line-height: 1.6; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.choice { font: inherit; padding: 4px 8px; color: var(--el-text-color-regular); background: var(--el-bg-color); border: 1px solid var(--el-border-color); border-radius: 6px; }
.block { margin-bottom: 12px; }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; }
.pad { padding: 24px 4px; }
.rows { width: 100%; border-collapse: collapse; background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 10px); }
.rows th, .rows td { text-align: left; padding: 9px 10px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: top; }
.rows th { font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); border-top: 0; }
.row { cursor: pointer; }
.row:hover, .row.open { background: var(--el-fill-color-lighter); }
.row.child .what { padding-left: 28px; }
.what { overflow-wrap: anywhere; }
.title { font: inherit; color: inherit; text-align: left; background: none; border: 0; padding: 0; cursor: pointer; overflow-wrap: anywhere; }
.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
.state { display: inline-block; padding: 0 8px; font-size: 12px; white-space: nowrap; border-radius: 10px; color: var(--el-text-color-secondary); background: var(--el-fill-color); }
.state--working { color: var(--el-color-success); background: var(--el-color-success-light-9); }
.state--waiting_user { color: var(--el-color-warning); background: var(--el-color-warning-light-9); font-weight: 600; }
.state--failed, .state--blocked { color: var(--el-color-danger); background: var(--el-color-danger-light-9); }
.detail td { background: var(--el-fill-color-lighter); }
.text { margin: 0 0 6px; line-height: 1.6; overflow-wrap: anywhere; }
.say { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.grow { flex: 1; }
</style>
