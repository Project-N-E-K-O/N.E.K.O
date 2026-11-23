<template>
  <div class="ask" data-testid="familiar-ask">
    <strong>{{ t('power.space.familiars.asks') }}</strong>
    <p v-for="(line, index) in lines" :key="index" class="text" data-testid="familiar-asked">{{ line }}</p>

    <!-- What it wants to be allowed to do, whole: a long command is never shown cut here. -->
    <template v-if="whole">
      <div class="whole-head">
        <span class="muted">{{ t(`power.ask.${whole.kind}`) }}</span>
        <button type="button" class="copy" data-testid="ask-copy" @click="copy">{{ copied ? t('power.ask.copied') : t('power.ask.copy') }}</button>
      </div>
      <pre class="whole" data-testid="ask-whole" tabindex="0">{{ whole.text }}</pre>
    </template>
    <dl v-if="facts.length" class="facts" data-testid="ask-facts">
      <div v-for="fact in facts" :key="fact.name"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
    </dl>

    <div v-if="ask.options?.length" class="choices">
      <el-button
        v-for="option in ask.options"
        :key="option"
        :type="option === 'deny' ? 'danger' : 'primary'"
        plain
        :loading="busy === `choice:${option}`"
        :disabled="busy !== ''"
        :data-choice="option"
        @click="emit('answer', { choice: option })"
      >{{ choiceLabel(option) }}</el-button>
    </div>
    <div v-else class="say">
      <el-input v-model="reply" :placeholder="t('power.space.familiars.answerPlaceholder')" data-testid="familiar-reply" @keyup.enter="send" />
      <el-button type="primary" :loading="busy === 'answer'" :disabled="!reply.trim() || busy !== ''" data-testid="familiar-answer" @click="send">
        {{ t('power.space.familiars.answer') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { BoardAsk } from '@/api/console'
import { askFacts, askLines, askWhole } from './ask'

/**
 * What a familiar asks the user while it waits, with what it would do shown
 * whole, and the ways to answer. Whoever shows it sends the answer.
 */
const props = defineProps<{ ask: BoardAsk; busy: string }>()
const emit = defineEmits<{ answer: [given: { text?: string; choice?: string }] }>()
const { t, te } = useI18n()
const reply = ref('')
const copied = ref(false)

const lines = computed(() => askLines(props.ask, t, te))
const whole = computed(() => askWhole(props.ask))
const facts = computed(() => askFacts(props.ask, t, te))

function choiceLabel(option: string): string {
  const key = `power.space.familiars.choices.${option}`
  return te(key) ? t(key) : option
}
function send() {
  if (reply.value.trim()) emit('answer', { text: reply.value })
}
async function copy() {
  if (!whole.value) return
  try {
    await navigator.clipboard.writeText(whole.value.text)
    copied.value = true
  } catch {
    // No clipboard here: the text is there to be selected by hand.
    copied.value = false
  }
}
// Another question: what was typed for the last one is not its answer.
watch(() => props.ask.id, () => { reply.value = ''; copied.value = false })
defineExpose({ clear: () => { reply.value = '' } })
</script>

<style scoped>
.ask { margin-top: 10px; padding: 12px; border: 1px solid var(--el-color-warning); border-radius: 10px; background: var(--el-color-warning-light-9); }
.text { margin: 6px 0; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.muted { color: var(--el-text-color-secondary); font-size: 12px; }
.whole-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 6px; }
.copy { font: inherit; font-size: 12px; color: var(--el-color-primary); background: none; border: 0; padding: 0; cursor: pointer; }
.whole { margin: 4px 0 8px; padding: 10px 12px; max-height: 220px; overflow: auto; white-space: pre-wrap; word-break: break-all; font: 12.5px/1.6 Consolas, Menlo, monospace; border-radius: 8px; background: var(--el-fill-color); }
.facts { margin: 0 0 8px; font-size: 12px; }
.facts div { display: flex; gap: 8px; }
.facts dt { flex: none; color: var(--el-text-color-secondary); }
.facts dd { margin: 0; overflow-wrap: anywhere; }
.choices { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.say { display: flex; gap: 8px; margin-top: 8px; }
</style>
