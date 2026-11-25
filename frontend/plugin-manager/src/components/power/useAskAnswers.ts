/**
 * Answers to what familiars ask, sent from where several questions are shown at once (the cards
 * of the board, the heads in the colony): each apart, so that another can be answered while the
 * answer to one is on its way.
 */
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { answerCommission } from '@/api/console'
import type { BoardAsk } from '@/api/console'
import { answerBody, answeringAs, describeAnswerError, saysSomething } from './ask'
import type { GivenAnswer } from './ask'

type Translate = (key: string, named?: Record<string, unknown>) => string

/** A question to answer: the commission that asks, and what it asks. */
export interface Asked {
  id: string
  ask: BoardAsk | null | undefined
}

/**
 * Send one answer. Returns what the user is to be told when the answer was taken and the
 * sentence added to it was not ('' when all of it was); an answer that was not taken throws.
 */
export async function sendAnswer(commissionId: string, given: GivenAnswer, ask: BoardAsk | null | undefined, t: Translate): Promise<string> {
  const done = await answerCommission(commissionId, answerBody(given, ask))
  return done?.note_taken === false ? t('power.ask.noteLost') : ''
}

/**
 * `after` is done once an answer was taken or not: whoever shows the questions reads anew then.
 * `noteLost`, where given, is told that an answer was taken while the sentence added to it was
 * not: where the question is shown is gone with the answer, so it is said elsewhere. Not given,
 * it is kept with why answers were not taken.
 */
export function useAskAnswers(after: () => Promise<unknown>, noteLost?: (said: string) => void) {
  const { t, te } = useI18n()
  // By its commission: with which button a question is being answered, and why an answer was not taken.
  const answering = ref<Record<string, string>>({})
  const failed = ref<Record<string, string>>({})
  const without = (kept: Record<string, string>, id: string) => Object.fromEntries(Object.entries(kept).filter(([key]) => key !== id))

  async function answer(item: Asked, given: GivenAnswer) {
    // One answer to a question: a second press while the first is on its way sends nothing.
    if (!saysSomething(given) || answering.value[item.id]) return
    answering.value = { ...answering.value, [item.id]: answeringAs(given) }
    failed.value = without(failed.value, item.id)
    try {
      const lost = await sendAnswer(item.id, given, item.ask, t)
      if (lost && noteLost) noteLost(lost)
      else if (lost) failed.value = { ...failed.value, [item.id]: lost }
    } catch (failure) {
      failed.value = { ...failed.value, [item.id]: describeAnswerError(failure, t, te) }
    }
    answering.value = without(answering.value, item.id)
    await after()
  }

  return {
    answering,
    failed,
    busyOf: (item: { id: string }) => answering.value[item.id] ?? '',
    failedOf: (item: { id: string }) => failed.value[item.id] ?? '',
    answer,
  }
}
