<template>
  <section class="talk" data-testid="transcript-pane">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon data-testid="transcript-error" />
    <p v-if="unreadable" class="muted" data-testid="transcript-unreadable">{{ t(`power.transcript.${unreadable}`) }}</p>
    <template v-else-if="loaded">
      <div class="more">
        <el-button v-if="before !== null" size="small" :loading="loading" data-testid="transcript-earlier" @click="earlier">
          {{ t('power.transcript.earlier') }}
        </el-button>
        <span v-else-if="entries.length" class="muted">{{ t('power.transcript.beginning') }}</span>
        <span class="grow" />
        <el-button size="small" :loading="loading" data-testid="transcript-latest" @click="latest">{{ t('power.transcript.refresh') }}</el-button>
      </div>
      <p v-if="!entries.length" class="muted">{{ t('power.transcript.nothing') }}</p>
      <article v-for="entry in entries" :key="entry.at" class="entry" :class="`entry--${entry.role}`" :data-entry="entry.at" :data-role="entry.role">
        <!-- What the host told it by itself is part of the record, and folded: it is not what anyone said. -->
        <details v-if="entry.role === 'notice'" class="call">
          <summary>{{ who(entry) }}<template v-if="entry.tool"> · {{ entry.tool }}</template></summary>
          <pre>{{ entry.text }}</pre>
        </details>
        <template v-else>
          <header>
            <span class="who">{{ who(entry) }}</span>
            <time v-if="entry.time" class="muted">{{ clockTime(entry.time, true) }}</time>
          </header>
          <p v-if="entry.text" class="text">{{ entry.text }}</p>
        </template>
        <p v-if="entry.pictures" class="muted">{{ t('power.transcript.pictures', { count: entry.pictures }) }}</p>
        <details v-for="(call, index) in entry.calls" :key="index" class="call">
          <summary>{{ t('power.transcript.calls', { tool: call.name }) }}</summary>
          <pre>{{ call.arguments }}</pre>
        </details>
        <el-button
          v-if="entry.cut || entry.calls.some(call => call.cut)"
          size="small" link type="primary" :loading="opening === entry.at" :data-whole="entry.at" @click="open(entry)"
        >{{ t('power.transcript.whole') }}</el-button>
      </article>
    </template>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getTranscript } from '@/api/console'
import type { TranscriptEntry } from '@/api/console'
import { clockTime } from '../colony/activity'
import { describeConsoleError } from '../powerFormat'

/** What was said in the session of one familiar, the last of it first; earlier pages are read when asked for. */
const props = defineProps<{ sessionId: string }>()
const { t, te } = useI18n()
const entries = ref<TranscriptEntry[]>([])
const before = ref<number | null>(null)
const loaded = ref(false)
const loading = ref(false)
const opening = ref<number | null>(null)
const unreadable = ref('')
const error = ref('')
let turn = 0

function who(entry: TranscriptEntry): string {
  if (entry.role === 'tool_result') {
    return t(entry.failed ? 'power.transcript.roles.tool_failed' : 'power.transcript.roles.tool_result', { tool: entry.tool || '' })
  }
  const key = `power.transcript.roles.${entry.role}`
  return te(key) ? t(key) : entry.role
}

async function read(from: number | null): Promise<void> {
  const mine = ++turn
  loading.value = true
  try {
    const page = await getTranscript(props.sessionId, { before: from })
    if (mine !== turn) return
    error.value = ''
    if (!page.readable) {
      unreadable.value = page.reason === 'dsh' ? 'inDsh' : 'empty'
      entries.value = []
      before.value = null
    } else {
      unreadable.value = ''
      // The page before the ones shown goes above them.
      entries.value = from === null ? page.entries : [...page.entries, ...entries.value]
      before.value = page.before
    }
  } catch (failure) {
    if (mine !== turn) return
    error.value = describeConsoleError(failure, t, te)
  } finally {
    if (mine === turn) {
      loading.value = false
      loaded.value = true
    }
  }
}
const latest = () => read(null)
const earlier = () => (before.value === null ? Promise.resolve() : read(before.value))

/** Read one entry whole and put it in the place of the cut one. */
async function open(entry: TranscriptEntry) {
  opening.value = entry.at
  try {
    const page = await getTranscript(props.sessionId, { at: entry.at })
    const whole = page.entries[0]
    if (whole) entries.value = entries.value.map(item => (item.at === entry.at ? whole : item))
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    opening.value = null
  }
}

watch(() => props.sessionId, () => {
  entries.value = []
  before.value = null
  loaded.value = false
  void latest()
}, { immediate: true })
</script>

<style scoped>
.talk { display: flex; flex-direction: column; gap: 8px; }
.more { display: flex; align-items: center; gap: 8px; }
.grow { flex: 1; }
.muted { margin: 0; color: var(--el-text-color-secondary); font-size: 12px; }
.entry { max-width: 88%; padding: 8px 12px; border-radius: 10px; background: var(--el-fill-color-light); }
.entry--user { align-self: flex-end; background: var(--el-color-primary-light-9); }
.entry--tool_result, .entry--notice, .entry--summary { max-width: 100%; background: none; border-left: 2px solid var(--el-border-color); border-radius: 0; padding: 2px 12px; }
.entry header { display: flex; gap: 8px; align-items: baseline; }
.who { font-size: 12px; font-weight: 600; color: var(--el-text-color-secondary); }
.text { margin: 4px 0 0; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.entry--tool_result .text { font: 12.5px/1.6 Consolas, Menlo, monospace; max-height: 240px; overflow: auto; }
.call summary { cursor: pointer; font: 12.5px Consolas, Menlo, monospace; color: var(--el-text-color-secondary); }
.call pre { margin: 4px 0; padding: 8px 10px; max-height: 240px; overflow: auto; white-space: pre-wrap; word-break: break-all; font: 12.5px/1.6 Consolas, Menlo, monospace; border-radius: 8px; background: var(--el-fill-color); }
</style>
