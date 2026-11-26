<template>
  <div class="ask" data-testid="familiar-ask" :data-shape="shape">
    <strong>{{ beside ? t('power.ask.beside.heading') : t('power.space.familiars.asks') }}</strong>
    <!-- Its questions, each with what it offers to pick from; anything else it asks, as lines. -->
    <template v-if="shape === 'questions'">
      <div v-for="(item, index) in questions" :key="item.id" class="question" :data-question="item.id">
        <p class="text" data-testid="familiar-asked">{{ questions.length > 1 ? `${index + 1}. ${item.text}` : item.text }}</p>
        <div v-if="item.options.length" class="options" role="group" :aria-label="item.text">
          <button
            v-for="option in item.options" :key="option.label" type="button" class="option"
            :class="{ on: isPicked(item, option.label) }" :aria-pressed="quick ? undefined : isPicked(item, option.label)"
            :disabled="held" :data-option="option.label" @click="pick(item, option.label)"
          >
            <span class="label">{{ option.label }}</span>
            <span v-if="option.description" class="about">{{ option.description }}</span>
          </button>
        </div>
      </div>
    </template>
    <p v-for="(line, index) in shape === 'questions' ? [] : lines" :key="index" class="text" data-testid="familiar-asked">{{ line }}</p>
    <el-alert v-if="warning" :title="warning" type="warning" :closable="false" show-icon class="warning" data-testid="ask-warning" />

    <!-- What it wants to be allowed to do, whole: a long command is never shown cut here. -->
    <template v-if="whole">
      <div class="whole-head">
        <span class="muted">{{ t(`power.ask.${whole.kind}`) }}</span>
        <button type="button" class="copy" data-testid="ask-copy" @click="copy">{{ copied ? t('power.ask.copied') : t('power.ask.copy') }}</button>
      </div>
      <pre ref="wholeText" class="whole" data-testid="ask-whole" tabindex="0">{{ whole.text }}</pre>
    </template>
    <dl v-if="facts.length" class="facts" data-testid="ask-facts">
      <div v-for="fact in facts" :key="fact.name" :data-fact="fact.name"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
    </dl>
    <p v-if="hint" class="muted hint" data-testid="ask-hint">{{ hint }}</p>

    <template v-if="shape === 'permission'">
      <div class="choices">
        <el-button
          v-for="option in ask.options"
          :key="option"
          :type="option !== 'deny' ? 'primary' : beside ? 'info' : 'danger'"
          plain
          :loading="busy === `choice:${option}`"
          :disabled="held"
          :data-choice="option"
          @click="choose(option)"
        >{{ choiceLabel(ask, option, t, te) }}</el-button>
      </div>
      <!-- A sentence to go with the choice. Not an answer of its own: words alone would be read as a refusal. -->
      <el-input v-if="!noNote" v-model="reply" class="note" :placeholder="t('power.ask.notePlaceholder')" :aria-label="t('power.ask.noteLabel')" data-testid="familiar-note" />
    </template>
    <template v-else>
      <div v-if="shape === 'confirmation'" class="choices">
        <el-button type="primary" plain :disabled="held" data-confirm="yes" @click="confirm(true)">{{ t('power.ask.confirm.yes') }}</el-button>
        <el-button type="danger" plain :disabled="held" data-confirm="no" @click="confirm(false)">{{ t('power.ask.confirm.no') }}</el-button>
      </div>
      <div class="say">
        <el-input
          v-model="reply" :placeholder="t(shape === 'words' ? 'power.space.familiars.answerPlaceholder' : 'power.ask.addPlaceholder')"
          data-testid="familiar-reply" @keyup.enter="send"
        />
        <el-button type="primary" :loading="busy === 'answer'" :disabled="!ready || held" data-testid="familiar-answer" @click="send">
          {{ t('power.space.familiars.answer') }}
        </el-button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BoardAsk } from '@/api/console'
import {
  SETTLE_MS, askFacts, askLines, askQuestions, askShape, askWarning, askWhole, besideHint, choiceLabel, confirmation, isBeside, useTookPlace,
} from './ask'
import type { AskQuestion, GivenAnswer } from './ask'

/**
 * What a familiar asks the user while it waits, with what it would do shown
 * whole, and the ways to answer: buttons for what can be picked, and a line
 * for words beside them. Whoever shows it sends the answer.
 */
/** `noNote`: nothing could take a sentence added to an approval (a plugin does the work, not a familiar): the line for one is left out. */
/** `place`: the commission that asks, where the page keeps which question each showed last (`rememberAsks`). */
const props = defineProps<{ ask: BoardAsk; busy: string; noNote?: boolean; place?: string | null }>()
const emit = defineEmits<{ answer: [given: GivenAnswer] }>()
const { t, te } = useI18n()
const reply = ref('')
// Which options of each question are picked, by the question's id.
const picked = ref<Record<string, string[]>>({})
// Another question took this one's place a moment ago: its buttons wait, so that a press on
// its way to the last one does not answer it.
const tookPlace = useTookPlace()
// Drawn anew for a commission that showed another question a moment ago: held back as well.
const settling = ref(tookPlace(props.place, props.ask.id))
let settled: ReturnType<typeof setTimeout> | null = settling.value ? setTimeout(() => { settling.value = false }, SETTLE_MS) : null
const held = computed(() => props.busy !== '' || settling.value)
const copied = ref(false)
const wholeText = ref<HTMLElement | null>(null)

const lines = computed(() => askLines(props.ask, t, te))
const whole = computed(() => askWhole(props.ask))
const facts = computed(() => askFacts(props.ask, t, te))
const warning = computed(() => askWarning(props.ask, t))
// Whether another familiar is to do what waits for a busy one: refusing it is no danger, it only waits.
const beside = computed(() => isBeside(props.ask))
const hint = computed(() => besideHint(props.ask, t, te))
const shape = computed(() => askShape(props.ask))
const questions = computed(() => askQuestions(props.ask))
// One question with one option to pick: pressing the option is the answer, with what was typed.
const quick = computed(() => questions.value.length === 1 && !questions.value[0].multi)
const somePicked = computed(() => Object.values(picked.value).some(labels => labels.length))
const ready = computed(() => Boolean(reply.value.trim()) || (shape.value === 'questions' && somePicked.value))

const isPicked = (item: AskQuestion, label: string) => (picked.value[item.id] ?? []).includes(label)
function pick(item: AskQuestion, label: string) {
  if (held.value) return
  if (quick.value) {
    emit('answer', { selected: { [item.id]: [label] }, text: reply.value })
    return
  }
  const had = picked.value[item.id] ?? []
  const now = had.includes(label) ? had.filter(each => each !== label) : item.multi ? [...had, label] : [label]
  picked.value = { ...picked.value, [item.id]: now }
}
function choose(option: string) {
  if (held.value) return
  emit('answer', { choice: option, note: reply.value.trim() || undefined })
}
function confirm(yes: boolean) {
  if (held.value) return
  emit('answer', confirmation(yes, reply.value))
}
function send() {
  // Enter sends no more than the button would: nothing while an answer is on its way.
  if (held.value || !ready.value) return
  if (shape.value === 'questions' && somePicked.value) emit('answer', { selected: picked.value, text: reply.value })
  else emit('answer', { text: reply.value })
}
async function copy() {
  if (!whole.value) return
  try {
    await navigator.clipboard.writeText(whole.value.text)
    copied.value = true
  } catch {
    // No clipboard here (a page that is not served safely has none): all of the text is marked
    // instead, so that the keys copy it and it shows that nothing was copied yet.
    copied.value = false
    const box = wholeText.value
    const marked = window.getSelection()
    if (!box || !marked) return
    const all = document.createRange()
    all.selectNodeContents(box)
    marked.removeAllRanges()
    marked.addRange(all)
    box.focus()
  }
}
// Another question: what was typed for the last one is not its answer.
watch(() => props.ask.id, () => {
  reply.value = ''
  picked.value = {}
  copied.value = false
  tookPlace(props.place, props.ask.id)
  settling.value = true
  if (settled) clearTimeout(settled)
  settled = setTimeout(() => { settling.value = false }, SETTLE_MS)
})
onBeforeUnmount(() => { if (settled) clearTimeout(settled) })
defineExpose({ clear: () => { reply.value = ''; picked.value = {} } })
</script>

<style scoped>
.ask { margin-top: 10px; padding: 12px; border: 1px solid var(--el-color-warning); border-radius: 10px; background: var(--el-color-warning-light-9); }
.text { margin: 6px 0; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.warning { margin: 6px 0; }
.whole-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 6px; }
.copy { font: inherit; font-size: 12px; color: var(--el-color-primary); background: none; border: 0; padding: 0; cursor: pointer; }
.whole { margin: 4px 0 8px; padding: 10px 12px; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-all; font: 12.5px/1.6 Consolas, Menlo, monospace; border-radius: 8px; background: var(--el-fill-color); }
.facts { margin: 0 0 8px; font-size: 12px; }
.facts div { display: flex; gap: 8px; }
.facts dt { flex: none; color: var(--el-text-color-secondary); }
.facts dd { margin: 0; overflow-wrap: anywhere; white-space: pre-line; }
.hint { margin: 0 0 8px; line-height: 1.6; }
.choices { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.choices .el-button { margin-left: 0; }
.note { margin-top: 8px; }
.question + .question { margin-top: 8px; }
.options { display: flex; flex-wrap: wrap; gap: 8px; }
.option { display: flex; flex-direction: column; gap: 2px; max-width: 100%; padding: 6px 12px; font: inherit; text-align: left; cursor: pointer; color: inherit; border: 1px solid var(--el-border-color); border-radius: 8px; background: var(--el-bg-color); }
.option:hover:not(:disabled), .option:focus-visible { border-color: var(--el-color-primary); outline: none; }
.option:disabled { cursor: default; opacity: 0.6; }
.option.on { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.option .label { font-weight: 600; color: var(--el-color-primary); overflow-wrap: anywhere; }
.option .about { font-size: 12px; color: var(--el-text-color-secondary); overflow-wrap: anywhere; }
.say { display: flex; gap: 8px; margin-top: 8px; }
</style>
