/**
 * What NekoPower asks while work waits for a familiar busy with something else, in a project
 * without a leader: whether another familiar is to do it in a worktree of its own.
 *
 * No card has such a question: the commission it is about is at no familiar while it is asked
 * (`session_id` is empty), so it is read from the commissions that wait for the user. Read every
 * few seconds, and only while some project without a leader has a familiar at work or waiting
 * for the user: the question is taken back as soon as the busy one comes free, and none is put
 * otherwise. Not while nobody looks at the page.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Ref } from 'vue'
import { listCommissions } from '@/api/console'
import type { Board, BoardAsk, CommissionRow } from '@/api/console'
import { besideOf, isBeside } from '../ask'
import { useAskAnswers } from '../useAskAnswers'

export const BESIDE_POLL_MS = 3000
const BESIDE_POLL_FAILED_MS = 15000
/** How the colony names the one that would be opened, apart from the familiars there are. */
export const BESIDE_KEY = 'beside:'

/** One such question, with what the pages show it by. */
export interface BesideAsk {
  /** The commission it is about. */
  id: string
  title: string
  project: string
  ask: BoardAsk
  /** The session of the busy familiar it waits for. */
  beside: string
  /** The name the one to be opened would have. */
  member: string
}

/** The questions of whether to open another familiar, among these commissions. */
export function besideAsksOf(rows: CommissionRow[]): BesideAsk[] {
  const asks: BesideAsk[] = []
  for (const row of rows) {
    if (row.status !== 'waiting_user' || row.session_id || !row.ask || !isBeside(row.ask)) continue
    const said = besideOf(row.ask)
    asks.push({ id: row.id, title: row.title || said.title, project: row.project || said.project, ask: row.ask, beside: said.besideSession, member: said.member })
  }
  return asks
}

/** Whether such a question can be put now: a project without a leader has a familiar at something. */
export function mayBeAsked(board: Board | null): boolean {
  return (board?.projects ?? []).some(project =>
    !project.archived
    && !project.members.some(card => card.leader)
    && project.members.some(card => card.commission?.status === 'working' || card.commission?.status === 'waiting_user'))
}

export function useBesideAsks(board: Ref<Board | null>) {
  const asks = ref<BesideAsk[]>([])
  let timer: ReturnType<typeof setTimeout> | null = null
  let alive = true
  let turn = 0

  async function load() {
    const mine = ++turn
    if (timer) clearTimeout(timer)
    timer = null
    if (!alive) return
    if (!mayBeAsked(board.value)) {
      // None can be there: whatever was asked was taken back, and nothing is read until one can.
      asks.value = []
      return
    }
    let wait = BESIDE_POLL_MS
    try {
      const answer = await listCommissions({ status: ['waiting_user'] })
      if (mine !== turn) return
      asks.value = besideAsksOf(answer.commissions)
    } catch {
      // What was shown stays until it can be read again: NekoPower tells of it as well.
      wait = BESIDE_POLL_FAILED_MS
    } finally {
      if (mine === turn && alive && !document.hidden) timer = setTimeout(() => void load(), wait)
    }
  }

  function onVisibility() {
    if (!document.hidden && alive) void load()
  }

  // Read as soon as one can be asked, and let go of as soon as none can.
  watch(() => mayBeAsked(board.value), () => void load())
  onMounted(() => {
    document.addEventListener('visibilitychange', onVisibility)
    void load()
  })
  onBeforeUnmount(() => {
    alive = false
    document.removeEventListener('visibilitychange', onVisibility)
    if (timer) clearTimeout(timer)
  })

  return { asks, load }
}

/**
 * Such questions answered with a button where each is drawn as one card (`home/BesideAsk.vue`),
 * each apart: another can be answered while the answer to one is on its way. `after` is done once
 * an answer was taken or not: whoever shows them reads anew then.
 */
export function useBesideAnswers(after: () => Promise<unknown>) {
  // The same as for what a familiar asks on its card: by the commission that asks.
  return useAskAnswers(after)
}
