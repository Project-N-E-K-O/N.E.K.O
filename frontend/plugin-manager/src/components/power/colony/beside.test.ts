// @vitest-environment happy-dom
/**
 * Work that waits for a familiar busy with something else, in a project without a leader: whether
 * another is to do it in a worktree of its own. Asked while the commission is at no familiar, read
 * from the commissions waiting for the user, answered with a button where the busy one is shown;
 * and what the user set about it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { Board, BoardAsk, BoardCard, CommissionRow } from '@/api/console'
import AskBox from '../AskBox.vue'
import { askFacts, askLines, askSummary, besideHint, choiceLabel, isBeside } from '../ask'
import FamiliarBoard from '../home/FamiliarBoard.vue'
import LandingCard from '../LandingCard.vue'
import FamiliarColony from './FamiliarColony.vue'
import { BESIDE_POLL_MS, besideAsksOf, mayBeAsked } from './useBesideAsks'

const api = vi.hoisted(() => ({
  getBoard: vi.fn(), getTimeline: vi.fn(), getTimelineStart: vi.fn(), getWaits: vi.fn(),
  answerCommission: vi.fn(), archiveProject: vi.fn(), listCommissions: vi.fn(), getLanding: vi.fn(), setLanding: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const refusal = (code: string, status = 409) =>
  Object.assign(new Error(code), { code, response: { status, data: { detail: { code } } } })
const card = (over: Partial<BoardCard> = {}): BoardCard => ({
  id: 'shop', name: 'Shop/general', kind: 'project', lanlan_name: 'Lan', runtime: 'builtin', workdir: 'D:/shop', preset: 'general',
  avatar: 'general', label: '', description: '', title: 'Shelf keeper', duty: '', duty_by: '', doing: '', state: 'working', leader: false,
  parent: null, model: null, commission: { id: 'c1', title: 'Restock the shelves', status: 'working', started_at: 0 }, ...over,
})
const board = (members: BoardCard[] = [card()], revision = 'r1'): Board => ({
  revision,
  projects: [{ name: 'Shop', description: '', workdir: 'D:/shop', archived: false, done_today: 0, members }],
  loose: [],
})
const beside = (over: Partial<BoardAsk> = {}): BoardAsk => ({
  id: 'q9', kind: 'permission', category: 'open_beside', options: ['always', 'session', 'once', 'deny'],
  detail: {
    tool: 'beside', title: 'Write the newsletter', preset: 'general', tree: true, member: 'Shop/general-2', project: 'Shop',
    beside: 'Shop/general', beside_session: 'shop', at_hand: 'Restock the shelves',
  },
  ...over,
})
const row = (over: Partial<CommissionRow> = {}): CommissionRow => ({
  id: 'c9', title: 'Write the newsletter', status: 'waiting_user', reason: 'waiting for the user', result: '', source: 'dispatch',
  route: 'task_agent', project: 'Shop', lanlan_name: 'Lan', parent_id: null, session_id: null, session: null, runtime: null,
  plugin_id: null, entry_id: null, created_at: 0, started_at: null, ended_at: null, updated_at: 0, usage: { requests: 0, tokens: 0 },
  ask: beside(), ...over,
})
const asked = (rows: CommissionRow[] = [row()]) => ({ commissions: rows, more: false })

let cleanup: (() => void) | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() {
  for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() }
  await vi.dynamicImportSettled()
  for (let i = 0; i < 4; i++) { await Promise.resolve(); await nextTick() }
}
async function mount(component: Component, props: Record<string, unknown> = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const state = reactive({ props })
  const app = createApp({ render: () => h(component, state.props) })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words, common: { cancel: 'Cancel' } } } }))
  app.mount(container)
  cleanup = () => { app.unmount(); container.remove() }
  await flush()
  return { container, state }
}
const byTest = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
const cat = (id: string) => document.querySelector(`[data-cat="${id}"]`) as HTMLElement | null
const choice = (option: string, within: ParentNode = document) => within.querySelector(`[data-choice="${option}"]`) as HTMLElement | null
async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy()
  ;(element as HTMLElement).click()
  await flush()
}

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.resetAllMocks()
  localStorage.clear()
  api.getBoard.mockResolvedValue(board())
  api.getTimelineStart.mockResolvedValue({ after: '', latest: 'e1.m0' })
  api.getTimeline.mockResolvedValue({ entries: [], next: 'e1.m0', more: false, latest: 'e1.m0' })
  api.getWaits.mockResolvedValue({ waits: [], more: false })
  api.listCommissions.mockResolvedValue(asked())
})
afterEach(() => {
  cleanup?.(); cleanup = undefined; document.body.innerHTML = ''
  vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

const t = (key: string, named: Record<string, unknown> = {}) => {
  const found = key.split('.').slice(1).reduce<unknown>((at, part) => (at as Record<string, unknown> | undefined)?.[part], words)
  return String(found ?? key).replace(/\{(\w+)\}/g, (_, name: string) => String(named[name] ?? ''))
}
const te = (key: string) => t(key) !== key

describe('what is asked of opening another beside a busy one', () => {
  it('says what came beside what the busy one is at, and who would do it, and nothing it is known by inside', () => {
    const ask = beside()
    expect(isBeside(ask)).toBe(true)
    expect(askLines(ask, t, te)).toEqual([words.ask.beside.question])
    const facts = askFacts(ask, t, te)
    expect(facts.map(fact => fact.name)).toEqual(['title', 'at_hand', 'member', 'tree'])
    expect(facts.map(fact => fact.value)).toEqual(['Write the newsletter', 'Restock the shelves', 'Shop/general-2', words.ask.treeNote])
    expect(facts[1].label).toBe(words.ask.beside.atHand.replace('{name}', 'Shop/general'))
    // The session it waits for is no fact for the user.
    expect(JSON.stringify(facts)).not.toContain('"shop"')
    expect(askSummary(ask, t, te)).toBe(words.ask.beside.summary.replace('{title}', 'Write the newsletter'))
    expect(besideHint(ask, t, te)).toBe(
      words.ask.beside.hint.replace('{name}', 'Shop/general').replace('{deny}', words.ask.beside.choices.deny),
    )
    expect(['always', 'session', 'once', 'deny'].map(option => choiceLabel(ask, option, t, te))).toEqual([
      words.ask.beside.choices.always, words.ask.beside.choices.session, words.ask.beside.choices.once, words.ask.beside.choices.deny,
    ])
  })

  it('says of the busy one by what it is where its name is not given, and leaves out what is not there', () => {
    const ask = beside({ detail: { tool: 'beside', title: 'Write the newsletter', tree: false } })
    expect(askFacts(ask, t, te).map(fact => fact.name)).toEqual(['title'])
    expect(besideHint(ask, t, te)).toContain(words.ask.beside.busyOne)
    const nameless = askFacts(beside({ detail: { tool: 'beside', title: 'Write the newsletter', at_hand: 'Restock the shelves' } }), t, te)
    expect(nameless.find(fact => fact.name === 'at_hand')?.label).toBe(words.ask.beside.atHand.replace('{name}', words.ask.beside.busyOne))
  })

  it('leaves every other question as it was', () => {
    const other: BoardAsk = { id: 'q1', kind: 'permission', category: 'open_session', detail: { tool: 'hand_over', title: 'Fix it' }, options: ['once', 'deny'] }
    expect(isBeside(other)).toBe(false)
    expect(isBeside(null)).toBe(false)
    expect(besideHint(other, t, te)).toBeNull()
    expect(choiceLabel(other, 'deny', t, te)).toBe(words.space.familiars.choices.deny)
    expect(choiceLabel(other, 'later', t, te)).toBe('later')
    expect(askFacts(other, t, te).map(fact => fact.name)).toEqual(['title'])
  })

  it('is answered with a button, and saying no to it is drawn as no danger', async () => {
    await mount(AskBox, { ask: beside(), busy: '' })
    expect(document.body.textContent).toContain(words.ask.beside.heading)
    expect(byTest('ask-hint')?.textContent).toContain(words.ask.beside.choices.deny)
    expect(document.querySelector('[data-fact="at_hand"]')?.textContent).toContain('Restock the shelves')
    expect(choice('once')?.textContent).toContain(words.ask.beside.choices.once)
    expect(choice('deny')?.className).not.toContain('danger')
    // Nothing to type: it is answered with the buttons.
    expect(byTest('familiar-reply')).toBeNull()
  })
})

describe('where such a question is looked for', () => {
  it('is among those waiting for the user that are at no familiar', () => {
    const rows = [
      row(),
      row({ id: 'c2', session_id: 'shop' }),
      row({ id: 'c3', status: 'queued' }),
      row({ id: 'c4', ask: { ...beside(), detail: { tool: 'hand_over' } } }),
      row({ id: 'c5', ask: null }),
    ]
    expect(besideAsksOf(rows)).toEqual([
      { id: 'c9', title: 'Write the newsletter', project: 'Shop', ask: rows[0].ask, beside: 'shop', member: 'Shop/general-2' },
    ])
  })

  it('is read only while a project without a leader has a familiar at something', () => {
    expect(mayBeAsked(board())).toBe(true)
    expect(mayBeAsked(board([card({ commission: { id: 'c1', title: 'x', status: 'waiting_user', started_at: 0 } })]))).toBe(true)
    expect(mayBeAsked(null)).toBe(false)
    expect(mayBeAsked(board([card({ state: 'idle', commission: null })]))).toBe(false)
    expect(mayBeAsked(board([card({ commission: { id: 'c1', title: 'x', status: 'resting', started_at: 0 } })]))).toBe(false)
    expect(mayBeAsked(board([card(), card({ id: 'boss', leader: true, commission: null })]))).toBe(false)
    const away = board()
    away.projects![0].archived = true
    expect(mayBeAsked(away)).toBe(false)
  })
})

describe('the question in the colony', () => {
  it('is said among those that wait, and the one that would be opened stands beside the busy one, not there yet', async () => {
    await mount(FamiliarColony)
    expect(api.listCommissions).toHaveBeenCalledWith({ status: ['waiting_user'] })
    const line = document.querySelector('[data-beside="c9"]')
    expect(line?.textContent).toContain(words.colony.besideWho.replace('{name}', 'Shelf keeper'))
    expect(line?.textContent).toContain('Write the newsletter')
    const ghost = cat('beside:c9')
    expect(ghost?.closest('[data-territory]')?.getAttribute('data-territory')).toBe('project:Shop')
    expect(ghost?.getAttribute('data-look')).toBe('unborn')
    expect(ghost?.textContent).toContain('Shop/general-2')
    // What it asks is said when it is pointed at, not in a bubble over the busy one's name.
    expect(ghost?.getAttribute('title')).toBe(words.colony.besideTip)
    expect(ghost?.querySelector('.bubble')).toBeNull()
    expect(ghost?.style.cursor).toBe('pointer')
    expect(ghost?.getAttribute('aria-label')).toBe(words.colony.unbornLabel.replace('{name}', 'Shop/general-2'))
    expect(byTest('board-tally')?.textContent).toContain(words.home.waiting.replace('{count}', '1'))
    // Beside the busy one, as one it started would stand: that one keeps the middle of its territory.
    expect([cat('shop')?.style.left, cat('shop')?.style.top]).toEqual(['50%', '52%'])
  })

  it('is answered with a button from the one not there yet, which is no familiar to open', async () => {
    api.answerCommission.mockResolvedValue({ success: true })
    await mount(FamiliarColony)
    // Not picked up to be dragged: it is not there.
    cat('beside:c9')!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }))
    await flush()
    expect(cat('beside:c9')?.className).not.toContain('cat--grabbed')
    await click(cat('beside:c9'))
    expect(push).not.toHaveBeenCalled()
    expect(document.querySelector('[data-fact="at_hand"]')?.textContent).toContain('Restock the shelves')
    expect(document.querySelector('.el-dialog__title')?.textContent).toBe(words.colony.besideTitle)
    // What came is said among the facts, not once more above the question.
    expect(byTest('colony-ask-title')).toBeNull()
    api.listCommissions.mockResolvedValue(asked([]))
    await click(choice('once'))
    expect(api.answerCommission).toHaveBeenCalledWith('c9', { choice: 'once', text: undefined, question_id: 'q9' })
    // Read again at once: it is asked no more, and the one not there yet is gone.
    expect(cat('beside:c9')).toBeNull()
    expect(document.querySelector('[data-beside]')).toBeNull()
    expect(document.querySelector('[data-fact]')).toBeNull()
  })

  it('is answered from its line as well, and says why when the answer is not taken', async () => {
    api.answerCommission.mockRejectedValue(refusal('not_taken'))
    await mount(FamiliarColony)
    await click(document.querySelector('[data-beside="c9"] button'))
    expect(byTest('colony-ask-title')).toBeNull()
    await click(choice('deny'))
    expect(api.answerCommission).toHaveBeenCalledWith('c9', { choice: 'deny', text: undefined, question_id: 'q9' })
    expect(byTest('colony-ask-error')?.textContent).toContain(words.errors.not_taken)
  })

  it('goes once it is taken back', async () => {
    vi.useFakeTimers()
    await mount(FamiliarColony)
    await click(cat('beside:c9'))
    expect(document.querySelector('[data-fact]')).toBeTruthy()
    // Read again a while later: what was asked was taken back meanwhile.
    api.listCommissions.mockResolvedValue(asked([]))
    await vi.advanceTimersByTimeAsync(BESIDE_POLL_MS + 100)
    await flush()
    expect(cat('beside:c9')).toBeNull()
    expect(document.querySelector('[data-fact]')).toBeNull()
  })

  it('goes at once when nobody is at anything any more, and is not looked for then', async () => {
    vi.useFakeTimers()
    await mount(FamiliarColony)
    expect(cat('beside:c9')).toBeTruthy()
    // The busy one came free, before the commissions are read again: none can be asked of any more.
    api.getBoard.mockResolvedValue(board([card({ state: 'idle', commission: null })], 'r2'))
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(cat('beside:c9')).toBeNull()
    expect(document.querySelector('[data-beside]')).toBeNull()
    const calls = api.listCommissions.mock.calls.length
    await vi.advanceTimersByTimeAsync(BESIDE_POLL_MS * 3)
    await flush()
    expect(api.listCommissions.mock.calls.length).toBe(calls)
  })

  it('is not looked for in a project with a leader', async () => {
    api.getBoard.mockResolvedValue(board([card(), card({ id: 'boss', leader: true, commission: null, state: 'idle' })]))
    await mount(FamiliarColony)
    expect(api.listCommissions).not.toHaveBeenCalled()
    expect(cat('beside:c9')).toBeNull()
  })

  it('stands in the territory of its project even where the busy one is not on the board', async () => {
    api.listCommissions.mockResolvedValue(asked([row({ ask: beside({ detail: { ...beside().detail, beside_session: 'gone' } }) })]))
    await mount(FamiliarColony)
    expect(cat('beside:c9')?.closest('[data-territory]')?.getAttribute('data-territory')).toBe('project:Shop')
    expect(document.querySelector('[data-beside="c9"]')?.textContent).toContain(words.colony.besideWho.replace('{name}', words.activity.someone))
  })
})

describe('the question on the board', () => {
  it('is not drawn among the loose ones, whatever its project is called', async () => {
    const both = board()
    both.loose = [card({ id: 'odd', name: 'Sort the photos', kind: 'single', state: 'idle', commission: null })]
    api.getBoard.mockResolvedValue(both)
    api.listCommissions.mockResolvedValue(asked([row({ project: words.home.loose, ask: beside({ detail: { ...beside().detail, beside_session: 'gone' } }) })]))
    await mount(FamiliarBoard)
    expect(document.querySelector('[data-hive="loose"] [data-testid="beside-ask"]')).toBeNull()
  })

  it('stands under the busy one, answered with a button', async () => {
    api.answerCommission.mockResolvedValue({ success: true })
    await mount(FamiliarBoard)
    const asking = byTest('beside-ask')
    expect(asking?.previousElementSibling?.getAttribute('data-familiar')).toBe('shop')
    expect(asking?.textContent).toContain('Shop/general-2')
    expect(asking?.textContent).toContain(words.home.states.unborn)
    expect(asking?.textContent).toContain('Restock the shelves')
    expect(byTest('board-tally')?.textContent).toContain(words.home.waiting.replace('{count}', '1'))
    api.listCommissions.mockResolvedValue(asked([]))
    await click(choice('once', asking!))
    expect(api.answerCommission).toHaveBeenCalledWith('c9', { choice: 'once', text: undefined, question_id: 'q9' })
    expect(byTest('beside-ask')).toBeNull()
  })

  it('says why an answer was not taken, beside the question', async () => {
    api.answerCommission.mockRejectedValue(refusal('not_taken'))
    await mount(FamiliarBoard)
    await click(choice('deny', byTest('beside-ask')!))
    expect(byTest('beside-error')?.textContent).toContain(words.errors.not_taken)
  })

  it('stands at the end of its project where the busy one is not on the board', async () => {
    api.listCommissions.mockResolvedValue(asked([row({ ask: beside({ detail: { ...beside().detail, beside_session: 'gone' } }) })]))
    await mount(FamiliarBoard)
    const asking = byTest('beside-ask')
    expect(asking?.closest('[data-hive]')?.getAttribute('data-hive')).toBe('project:Shop')
    expect(asking?.nextElementSibling).toBeNull()
  })
})

describe('what the user set about it', () => {
  it('offers the three, each with what it comes to, and saves the one chosen', async () => {
    api.getLanding.mockResolvedValue({ mode: 'auto', inspect: false, beside: 'ask' })
    api.setLanding.mockResolvedValue({ mode: 'auto', inspect: false, beside: 'queue' })
    await mount(LandingCard)
    for (const option of ['ask', 'open', 'queue'] as const) {
      expect(byTest(`landing-beside-${option}`)?.textContent).toContain(words.landing.beside[option])
      expect(byTest(`landing-beside-${option}-hint`)?.textContent).toContain(words.landing.beside[`${option}Hint`])
    }
    // What starting one without asking may come to is said beside it before it is chosen.
    expect(byTest('landing-beside-open-hint')?.className).toContain('warn')
    await click(byTest('landing-beside-queue')?.querySelector('input'))
    expect(api.setLanding).toHaveBeenCalledWith({ beside: 'queue' })
    expect((byTest('landing-beside-queue')?.querySelector('input') as HTMLInputElement).checked).toBe(true)
  })

  it('is not offered by a service that has no such setting', async () => {
    api.getLanding.mockResolvedValue({ mode: 'auto', inspect: false })
    await mount(LandingCard)
    expect(byTest('landing-settings')).toBeTruthy()
    expect(byTest('landing-beside')).toBeNull()
  })
})
