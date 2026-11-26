<template>
  <!-- Above the head of a familiar that waits for the user: what it asks in a line or two, answered from here. -->
  <div
    class="ask-bubble" :class="{ below }" :style="{ '--shift': `${shift ?? 0}px`, width: room ? `${room}px` : undefined }" :data-ask-bubble="ask.id ?? ''" :data-shape="shape" role="group" :aria-label="t('power.colony.askLabel', { name })"
    @pointerdown.stop @pointerup.stop @keydown.stop
  >
    <p class="line" :title="whole" data-testid="bubble-asked">{{ line }}</p>
    <!-- The folder a command is to be run in, where it names one: part of what is approved, and never cut short. -->
    <p v-if="cwd" class="cwd" data-testid="bubble-cwd">{{ t('power.ask.facts.cwd') }} {{ cwd }}</p>
    <div class="picks">
      <!-- A request for approval: once, or not. The wider approvals are given where all of what it would do is shown. -->
      <template v-if="shape === 'permission'">
        <button
          v-for="option in quickChoices" :key="option" type="button" class="pick" :class="{ deny: option === 'deny' }"
          :disabled="held" :data-choice="option" @click="give({ choice: option })"
        >{{ choiceLabel(ask, option, t, te) }}</button>
      </template>
      <template v-else-if="shape === 'confirmation'">
        <button type="button" class="pick" :disabled="held" data-confirm="yes" @click="give(confirmation(true, reply))">{{ t('power.ask.confirm.yes') }}</button>
        <button type="button" class="pick deny" :disabled="held" data-confirm="no" @click="give(confirmation(false, reply))">{{ t('power.ask.confirm.no') }}</button>
      </template>
      <template v-else-if="quickQuestion">
        <button
          v-for="option in quickQuestion.options.slice(0, QUICK_OPTIONS)" :key="option.label" type="button" class="pick" :title="option.description || undefined"
          :disabled="held" :data-option="option.label" @click="give({ selected: { [quickQuestion.id]: [option.label] }, text: reply })"
        >{{ option.label }}</button>
      </template>
      <button v-if="shape !== 'permission' && !typing" type="button" class="pick plain" data-testid="bubble-say" @click="typing = true">{{ t('power.colony.say') }}</button>
      <button type="button" class="pick plain" data-testid="bubble-more" @click="emit('more')">{{ t('power.colony.more') }}</button>
    </div>
    <form v-if="typing" class="say" @submit.prevent="send">
      <input
        ref="box" v-model="reply" type="text" :placeholder="t('power.colony.sayPlaceholder')" :aria-label="t('power.colony.sayPlaceholder')"
        :disabled="held" data-testid="bubble-reply"
      >
      <button type="submit" class="pick" :disabled="held || !reply.trim()" :aria-label="t('power.space.familiars.answer')" data-testid="bubble-send">↵</button>
    </form>
    <p v-if="error" class="error" role="alert" data-testid="bubble-error">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BoardAsk } from '@/api/console'
import { askLines, askQuestions, askShape, askSummary, askWhole, choiceLabel, confirmation, useTookPlace } from '../ask'
import type { GivenAnswer } from '../ask'

/**
 * The small card above a familiar that waits for the user. It has room for a line or two and a
 * few buttons: the answers that are safe to give at a glance. Everything else (the command it
 * would run shown whole, an approval that holds from now on, questions with much to pick from)
 * is behind "more", where whoever shows this opens the whole question.
 */
/** `below`: it stands under the familiar and not above it (one that stands near the top, where a card above would be cut off). */
// `room`: how wide it may be, where the territory is narrower than it draws itself. `shift`: how far it is moved sideways from above the familiar, to stay within the territory; its pointer stays above the familiar.
/** `place`: the commission that asks, where the page keeps which question each showed last (`rememberAsks`). */
const props = defineProps<{ ask: BoardAsk; name: string; busy: string; error: string; below?: boolean; shift?: number; room?: number; place?: string | null }>()
const emit = defineEmits<{ answer: [given: GivenAnswer]; more: [] }>()
const { t, te } = useI18n()

// Approving for good, or for all of a commission, is not offered here: only this once, or not.
const QUICK_CHOICES = ['once', 'deny']
const QUICK_OPTIONS = 3

const reply = ref('')
const box = ref<HTMLInputElement | null>(null)
const shape = computed(() => askShape(props.ask))
// Where it can only be answered in words, the line to type them in is there from the start.
const typing = ref(shape.value === 'words')
const quickChoices = computed(() => QUICK_CHOICES.filter(option => props.ask.options?.includes(option)))
/** The one question that is answered by picking one of a few options; null where there is more to it. */
const quickQuestion = computed(() => {
  const questions = askQuestions(props.ask)
  const only = questions.length === 1 ? questions[0] : null
  return only && !only.multi && only.options.length && only.options.length <= QUICK_OPTIONS ? only : null
})
const line = computed(() => askSummary(props.ask, t, te, 72))
const whole = computed(() => askWhole(props.ask)?.text ?? askLines(props.ask, t, te).join('\n'))
const cwd = computed(() => (shape.value === 'permission' && typeof props.ask.detail?.cwd === 'string' ? props.ask.detail.cwd.trim() : ''))

// Another question took this one's place a moment ago: its buttons wait, so that a press on
// its way to the last one does not answer it.
const tookPlace = useTookPlace()
const settling = ref(false)
let settled: ReturnType<typeof setTimeout> | null = null
function holdBack(ms: number) {
  if (settled) clearTimeout(settled)
  settled = null
  settling.value = ms > 0
  if (ms > 0) settled = setTimeout(() => { settling.value = false }, ms)
}
// Drawn anew for a commission that showed another question a moment ago: held back as well, and
// for what is left of that moment where it is this one that was taken away and is drawn again.
holdBack(tookPlace(props.place, props.ask.id))
const held = computed(() => props.busy !== '' || settling.value)

function give(given: GivenAnswer) {
  if (!held.value) emit('answer', given)
}
function send() {
  if (reply.value.trim()) give({ text: reply.value })
}
watch(typing, async shown => {
  if (!shown) return
  await nextTick()
  box.value?.focus()
})
// Another question: what was typed for the last one is not its answer.
watch(() => props.ask.id, () => {
  reply.value = ''
  typing.value = shape.value === 'words'
  holdBack(tookPlace(props.place, props.ask.id, true))
})
onBeforeUnmount(() => { if (settled) clearTimeout(settled) })
</script>

<style scoped>
.ask-bubble { position: absolute; left: 50%; bottom: 0; transform: translateX(calc(-50% + var(--shift, 0px))); width: 216px; padding: 6px 8px; display: flex; flex-direction: column; gap: 5px; font-size: 12px; line-height: 1.45; text-align: left; cursor: default; user-select: text; border: 1.5px solid var(--el-color-warning); border-radius: 10px; background: var(--el-bg-color); box-shadow: 0 4px 12px rgb(0 0 0 / 12%); }
.ask-bubble::after { content: ''; position: absolute; left: calc(50% - var(--shift, 0px)); bottom: -6px; width: 9px; height: 9px; transform: translateX(-50%) rotate(45deg); background: var(--el-bg-color); border-right: 1.5px solid var(--el-color-warning); border-bottom: 1.5px solid var(--el-color-warning); }
.ask-bubble.below { bottom: auto; top: 0; }
.ask-bubble.below::after { bottom: auto; top: -6px; border: 0; border-left: 1.5px solid var(--el-color-warning); border-top: 1.5px solid var(--el-color-warning); }
.line { margin: 0; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow-wrap: anywhere; }
.cwd { margin: 0; font-size: 11px; opacity: 0.85; overflow-wrap: anywhere; }
.picks { display: flex; flex-wrap: wrap; gap: 4px; }
.pick { max-width: 100%; padding: 2px 8px; font: inherit; font-size: 11px; line-height: 1.6; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--el-color-primary); border: 1px solid var(--el-color-primary); border-radius: 6px; background: var(--el-bg-color); }
.pick:hover:not(:disabled) { background: var(--el-color-primary-light-9); }
.pick:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 1px; }
.pick:disabled { cursor: default; opacity: 0.55; }
.pick.deny { color: var(--el-color-danger); border-color: var(--el-color-danger); }
.pick.plain { color: var(--el-text-color-secondary); border-color: var(--el-border-color); }
.say { display: flex; gap: 4px; }
.say input { flex: 1; min-width: 0; padding: 2px 6px; font: inherit; font-size: 12px; color: inherit; border: 1px solid var(--el-border-color); border-radius: 6px; background: var(--el-bg-color); }
.say input:focus-visible { border-color: var(--el-color-primary); outline: none; }
.error { margin: 0; color: var(--el-color-danger); }
</style>
