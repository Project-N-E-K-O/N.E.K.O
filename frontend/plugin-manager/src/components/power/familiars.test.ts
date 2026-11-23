// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { Board, BoardCard } from '@/api/console'
import NewFamiliarDialog from './home/NewFamiliarDialog.vue'
import FamiliarsPane from './space/FamiliarsPane.vue'

const api = vi.hoisted(() => ({
  getTemplates: vi.fn(), draftFamiliar: vi.fn(), makeFamiliar: vi.fn(), pickDirectory: vi.fn(),
  getProjectBoard: vi.fn(), writeCard: vi.fn(), tellCommission: vi.fn(), answerCommission: vi.fn(), stopCommission: vi.fn(),
  getTranscript: vi.fn(), getSessionUsage: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { response?: { data?: { detail?: { code?: string } } } })?.response?.data?.detail?.code ?? null,
}))
vi.mock('@/api/console', () => api)

const refusal = (code: string, more: Record<string, unknown> = {}, status = 400) =>
  ({ response: { status, data: { detail: { code, ...more } } } })
const card = (over: Partial<BoardCard> = {}): BoardCard => ({
  id: 's1', name: 'Ledger tool', kind: 'project', lanlan_name: 'Lan', runtime: 'builtin', workdir: 'D:/code/ledger-tool',
  preset: 'boss', avatar: 'boss', label: '', description: '', title: 'Keeper of the books', duty: 'Keeps the ledger tool working',
  duty_by: 'user', doing: '', summary: '', state: 'asleep', leader: true, parent: null, model: 'agent-model', commission: null, ...over,
})
const boardOf = (members: BoardCard[], revision = 'r1'): Board => ({
  revision, projects: [{ name: 'Ledger tool', description: '', workdir: null, archived: false, done_today: 0, members }], loose: [],
})

let cleanup: (() => void) | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() } }
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
const field = (id: string) => document.querySelector([
  `textarea[data-testid="${id}"]`, `input[data-testid="${id}"]`, `[data-testid="${id}"] textarea`, `[data-testid="${id}"] input`,
].join(', ')) as HTMLInputElement
async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy()
  ;(element as HTMLElement).click()
  await flush()
}
async function type(id: string, value: string) {
  const input = field(id)
  expect(input, id).toBeTruthy()
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.clearAllMocks()
  api.getTemplates.mockResolvedValue({ templates: [
    { name: 'boss', label: '', description: '', avatar: 'boss', source: 'builtin', valid: true, error: '' },
    { name: 'scout', label: '', description: '', avatar: 'scout', source: 'builtin', valid: true, error: '' },
    { name: 'translator', label: 'Translator', description: '', avatar: 'general', source: 'global', valid: true, error: '' },
    { name: 'broken', label: '', description: '', avatar: 'general', source: 'global', valid: false, error: 'unknown fields' },
  ] })
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals() })

describe('making a familiar', () => {
  const made = vi.fn()
  const closed = vi.fn()
  const open = () => mount(NewFamiliarDialog, { modelValue: true, onMade: made, 'onUpdate:modelValue': closed })
  beforeEach(() => { made.mockClear(); closed.mockClear() })

  it('fills the form in from a sentence and a folder, and makes it with what the user left in it', async () => {
    api.pickDirectory.mockResolvedValue({ path: 'D:/code/ledger-tool' })
    api.draftFamiliar.mockResolvedValue({ draft: {
      name: 'Ledger tool', title: 'Keeper of the books', preset: 'scout', duty: 'Keeps the ledger tool working',
      description: 'A small bookkeeping program', rules: '- Use Decimal for money.',
    } })
    api.makeFamiliar.mockResolvedValue({ project: { name: 'Ledger tool' }, session_id: 's1' })
    await open()
    expect((byTest('familiar-fill') as HTMLButtonElement).disabled).toBe(true)
    await type('familiar-about', 'looks after my bookkeeping tool')
    await click(byTest('familiar-pick'))
    expect(field('familiar-folder').value).toBe('D:/code/ledger-tool')
    await click(byTest('familiar-fill'))
    expect(api.draftFamiliar).toHaveBeenCalledWith('looks after my bookkeeping tool', 'D:/code/ledger-tool')
    expect(field('familiar-name').value).toBe('Ledger tool')
    expect(field('familiar-rules').value).toBe('- Use Decimal for money.')

    // What was drafted is the user's to change before it is made.
    await type('familiar-title', 'Bookkeeper')
    await click(byTest('familiar-make'))
    expect(api.makeFamiliar).toHaveBeenCalledWith({
      name: 'Ledger tool', title: 'Bookkeeper', preset: 'scout', duty: 'Keeps the ledger tool working',
      description: 'A small bookkeeping program', rules: '- Use Decimal for money.', workdir: 'D:/code/ledger-tool',
    })
    expect(made).toHaveBeenCalledWith('Ledger tool', 's1')
    expect(closed).toHaveBeenCalledWith(false)
  })

  it('can be filled in by hand, and is not sent without a name', async () => {
    api.makeFamiliar.mockResolvedValue({ project: { name: 'Trip' }, session_id: 's2' })
    await open()
    await click(byTest('familiar-make'))
    expect(api.makeFamiliar).not.toHaveBeenCalled()
    expect(byTest('familiar-error')?.textContent).toContain(words.home.nameRequired)
    await type('familiar-name', '  Trip ')
    await click(byTest('familiar-make'))
    expect(api.makeFamiliar).toHaveBeenCalledWith({ name: 'Trip', title: '', preset: 'boss', duty: '', description: '', rules: '', workdir: '' })
    expect(api.draftFamiliar).not.toHaveBeenCalled()
  })

  it('says why the form could not be filled in or the familiar not made, and keeps what was typed', async () => {
    api.draftFamiliar.mockRejectedValue(refusal('draft_failed', { reason: 'timeout' }, 502))
    api.makeFamiliar.mockRejectedValue(refusal('name_taken'))
    await open()
    await type('familiar-about', 'looks after my bookkeeping tool')
    await type('familiar-name', 'Ledger tool')
    await click(byTest('familiar-fill'))
    expect(byTest('familiar-error')?.textContent).toContain(words.home.drafts.timeout)
    expect(field('familiar-name').value).toBe('Ledger tool')
    await click(byTest('familiar-make'))
    expect(byTest('familiar-error')?.textContent).toContain(words.errors.name_taken)
    expect(made).not.toHaveBeenCalled()
    expect(closed).not.toHaveBeenCalled()
  })

  it('does not put a draft into a form that was closed and opened again meanwhile', async () => {
    let answer!: (value: unknown) => void
    api.draftFamiliar.mockReturnValue(new Promise(resolve => { answer = resolve }))
    const { state } = await mount(NewFamiliarDialog, { modelValue: true })
    await type('familiar-about', 'looks after the ledger tool')
    await click(byTest('familiar-fill'))
    state.props = { modelValue: false }
    await flush()
    state.props = { modelValue: true }
    await flush()
    await type('familiar-name', 'Travel plans')
    answer({ draft: { name: 'Ledger tool', title: 'Keeper of the books', preset: 'boss', duty: '', description: '', rules: '' } })
    await flush()
    expect(field('familiar-name').value).toBe('Travel plans')
    expect(field('familiar-title').value).toBe('')
  })

  it('keeps the kind that was chosen when the model drafts one that cannot be', async () => {
    api.draftFamiliar.mockResolvedValue({ draft: { name: 'N', title: '', preset: 'broken', duty: '', description: '', rules: '' } })
    api.makeFamiliar.mockResolvedValue({ project: { name: 'N' }, session_id: 's3' })
    await open()
    await type('familiar-about', 'something')
    await click(byTest('familiar-fill'))
    await click(byTest('familiar-make'))
    expect(api.makeFamiliar.mock.calls[0][0].preset).toBe('boss')
  })
})

describe('a familiar up close', () => {
  const selected = vi.fn()
  const show = (members: BoardCard[], selectedId = 's1') => {
    api.getProjectBoard.mockResolvedValue(boardOf(members))
    return mount(FamiliarsPane, { project: 'Ledger tool', selectedId, onSelect: selected })
  }
  beforeEach(() => selected.mockClear())

  it('lists the familiars of the project and tells which one was chosen', async () => {
    const { container } = await show([card(), card({ id: 's2', name: 'Look it up', kind: 'child', preset: 'scout', leader: false, parent: 's1', title: '' })], '')
    expect(api.getProjectBoard).toHaveBeenCalledWith('Ledger tool', undefined)
    expect([...container.querySelectorAll('[data-familiar]')].map(item => item.getAttribute('data-familiar'))).toEqual(['s1', 's2'])
    expect(container.textContent).toContain(words.space.familiars.pick)
    await click(container.querySelector('[data-familiar="s2"]'))
    expect(selected).toHaveBeenCalledWith('s2')
  })

  it('shows what it has in hand, takes a sentence while it works, and stops it when asked', async () => {
    const working = card({ state: 'working', doing: 'Changed export.py', commission: { id: 'c1', title: 'Add an Excel export', status: 'working', started_at: 0 } })
    const { container } = await show([working])
    expect(container.querySelector('[data-detail="s1"]')?.textContent).toContain('Add an Excel export')
    // The runtime is named in the user's words, not by its key.
    expect(container.querySelector('[data-detail="s1"]')?.textContent).toContain(words.space.familiars.builtinRuntime)
    expect(container.querySelector('[data-detail="s1"]')?.textContent).not.toContain('builtin')
    expect(byTest('familiar-ask')).toBeNull()

    api.tellCommission.mockResolvedValue({ success: true })
    await type('familiar-say', 'use openpyxl')
    await click(byTest('familiar-send'))
    expect(api.tellCommission).toHaveBeenCalledWith('c1', 'use openpyxl')
    expect(byTest('familiar-note')?.textContent).toBe(words.space.familiars.sent)
    expect(field('familiar-say').value).toBe('')

    api.stopCommission.mockResolvedValue({ status: 'cancelled' })
    api.getProjectBoard.mockResolvedValue(boardOf([card({ state: 'idle' })], 'r2'))
    await click(byTest('familiar-stop'))
    await click(byTest('familiar-stop-confirm'))
    expect(api.stopCommission).toHaveBeenCalledWith('c1')
    // Shown as the service has it afterwards: nothing in hand.
    expect(byTest('familiar-idle')).toBeTruthy()
    expect(byTest('familiar-say')).toBeNull()
  })

  it('keeps what was typed and says why when a sentence was not taken', async () => {
    const working = card({ state: 'working', commission: { id: 'c1', title: 'Add an Excel export', status: 'working', started_at: 0 } })
    await show([working])
    api.tellCommission.mockRejectedValue(refusal('not_taken', {}, 409))
    await type('familiar-say', 'still there?')
    await click(byTest('familiar-send'))
    expect(byTest('familiars-error')?.textContent).toContain(words.errors.not_taken)
    expect(field('familiar-say').value).toBe('still there?')
  })

  it('passes a sentence on once however often Enter is pressed while it is on its way', async () => {
    const working = card({ state: 'working', commission: { id: 'c1', title: 'Add an Excel export', status: 'working', started_at: 0 } })
    await show([working])
    api.tellCommission.mockReturnValue(new Promise(() => {}))
    await type('familiar-say', 'use openpyxl')
    field('familiar-say').dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
    field('familiar-say').dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
    await flush()
    expect(api.tellCommission).toHaveBeenCalledTimes(1)
  })

  it('shows a question with its choices and answers it by the one that was pressed', async () => {
    const asking = card({ state: 'waiting_user', commission: { id: 'c1', title: 'Add an Excel export', status: 'waiting_user', started_at: 0, ask: {
      id: 'q1', kind: 'permission', category: 'run_command', detail: { tool: 'bash', command: 'pip install openpyxl' }, options: ['always', 'session', 'once', 'deny'],
    } } })
    await show([asking])
    const shown = byTest('familiar-ask')?.textContent ?? ''
    expect(shown).toContain('pip install openpyxl')
    expect(shown).toContain(words.space.familiars.choices.session)
    // It is waiting, not working: there is nothing to pass a sentence to.
    expect(byTest('familiar-say')).toBeNull()
    api.answerCommission.mockResolvedValue({ success: true })
    await click(byTest('familiar-ask')?.querySelector('[data-choice="once"]'))
    expect(api.answerCommission).toHaveBeenCalledWith('c1', { choice: 'once', text: undefined, question_id: 'q1' })
  })

  it('answers a question put in words with what the user typed', async () => {
    const asking = card({ state: 'waiting_user', commission: { id: 'c1', title: 'Book the train', status: 'waiting_user', started_at: 0, ask: { id: 'q2', question: 'Which day?' } } })
    await show([asking])
    expect(byTest('familiar-ask')?.textContent).toContain('Which day?')
    expect((byTest('familiar-answer') as HTMLButtonElement).disabled).toBe(true)
    api.answerCommission.mockResolvedValue({ success: true })
    await type('familiar-reply', ' Friday ')
    await click(byTest('familiar-answer'))
    expect(api.answerCommission).toHaveBeenCalledWith('c1', { text: 'Friday', question_id: 'q2' })
  })

  it('shows each of the questions it put with what it offers', async () => {
    const questions = [
      { id: 'a', question: 'Which day?', options: [{ label: 'Friday' }, { label: 'Saturday' }] },
      { id: 'b', question: 'Window seat?' },
    ]
    const asking = card({ state: 'waiting_user', commission: { id: 'c1', title: 'Book the train', status: 'waiting_user', started_at: 0, ask: { id: 'q3', kind: 'questions', questions } } })
    const { container } = await show([asking])
    const lines = [...container.querySelectorAll('[data-testid="familiar-asked"]')].map(line => line.textContent)
    expect(lines).toEqual(['1. Which day? (Friday / Saturday)', '2. Window seat?'])
    // Answered in words, like a question that is one.
    expect(byTest('familiar-reply')).toBeTruthy()
  })

  it('shows what was said in it and what it used when asked, and reads what it used anew as it uses more', async () => {
    vi.useFakeTimers()
    const working = (tokens: number) => card({
      state: 'working', usage: { requests: 3, tokens }, context_tokens: 900,
      commission: { id: 'c1', title: 'Add an Excel export', status: 'working', started_at: 0 },
    })
    api.getTranscript.mockResolvedValue({ readable: true, before: null, entries: [
      { at: 0, role: 'user', text: 'add an export', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
    api.getSessionUsage.mockResolvedValue({
      session: { requests: 3, input: 100, output: 20, cache_read: 0, cache_write: 0, tokens: 120, context_tokens: 900 },
      window: null, commission: null,
    })
    await show([working(120)])
    // What it has in hand at first; neither of the other two was read for nothing.
    expect(byTest('familiar-summary')).toBeTruthy()
    expect(api.getTranscript).not.toHaveBeenCalled()
    expect(api.getSessionUsage).not.toHaveBeenCalled()
    await click(document.querySelector('[data-view="talk"]'))
    expect(api.getTranscript).toHaveBeenCalledWith('s1', { before: null })
    expect(byTest('transcript-pane')?.textContent).toContain('add an export')
    expect(byTest('familiar-summary')).toBeNull()
    await click(document.querySelector('[data-view="usage"]'))
    expect(api.getSessionUsage).toHaveBeenCalledTimes(1)

    // The board says it used more, the same thing still in hand: read again. Nothing new on it: not read again.
    api.getProjectBoard.mockResolvedValue(boardOf([working(120)], 'r2'))
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(api.getSessionUsage).toHaveBeenCalledTimes(1)
    api.getProjectBoard.mockResolvedValue(boardOf([working(4_300)], 'r3'))
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(api.getSessionUsage).toHaveBeenCalledTimes(2)
  })

  it('shows the whole command it asks about, and says when its model is not the one that was set', async () => {
    const long = `npm run build ${'--flag '.repeat(200)}&& echo done`
    const asking = card({
      state: 'waiting_user', model_fallback: 'slot_not_found',
      commission: { id: 'c1', title: 'Build it', status: 'waiting_user', started_at: 0, ask: {
        id: 'q1', kind: 'permission', category: 'run_command', detail: { tool: 'bash', command: long, cwd: 'D:/code' }, options: ['once', 'deny'],
      } },
    })
    await show([asking])
    expect(byTest('ask-whole')?.textContent).toBe(long)
    expect(byTest('familiar-fallback')?.textContent).toBe(words.roles.fellBack)
    cleanup?.()
    await show([card()])
    expect(byTest('familiar-fallback')).toBeNull()
    expect(byTest('ask-whole')).toBeNull()
  })

  it('writes only the lines of the card that were changed', async () => {
    await show([card({ summary: 'Set up.' })])
    expect(byTest('familiar-summary')?.textContent).toBe('Set up.')
    api.writeCard.mockResolvedValue({ written: ['title'] })
    await click(byTest('card-edit'))
    expect(field('card-duty').value).toBe('Keeps the ledger tool working')
    await type('card-title', 'Bookkeeper')
    await click(byTest('card-save'))
    expect(api.writeCard).toHaveBeenCalledWith('s1', { title: 'Bookkeeper' })
    // Nothing changed: nothing is sent.
    await click(byTest('card-edit'))
    await click(byTest('card-save'))
    expect(api.writeCard).toHaveBeenCalledTimes(1)
  })

  it('sends only the lines the user changed, though the card changed under the open editor', async () => {
    await show([card({ summary: '' })])
    api.writeCard.mockResolvedValue({ written: ['title'] })
    await click(byTest('card-edit'))
    await type('card-title', 'Bookkeeper')
    // A review wrote the summary and the duty while the editor was open, and the board was read anew.
    api.getProjectBoard.mockResolvedValue(boardOf([card({ summary: 'Set up the export.', duty: 'Keeps the books' })], 'r2'))
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(api.getProjectBoard).toHaveBeenLastCalledWith('Ledger tool', 'r1')
    await click(byTest('card-save'))
    expect(api.writeCard).toHaveBeenCalledWith('s1', { title: 'Bookkeeper' })
  })

  it('writes a card for the familiar its editor was opened for, whichever is chosen by then', async () => {
    const other = card({ id: 's2', name: 'Look it up', kind: 'child', preset: 'scout', leader: false, parent: 's1', title: 'Scout' })
    const { state } = await show([card(), other])
    api.writeCard.mockResolvedValue({ written: ['title'] })
    await click(byTest('card-edit'))
    await type('card-title', 'Bookkeeper')
    state.props = { ...state.props, selectedId: 's2' }
    await flush()
    // The editor is on its way out; a press that still reaches it writes for the one it was opened for.
    const save = byTest('card-save')
    if (save) await click(save)
    expect(api.writeCard.mock.calls.every(call => call[0] === 's1')).toBe(true)
  })

  it('keeps the editor open with what was typed when the card was not taken', async () => {
    await show([card()])
    api.writeCard.mockRejectedValue(refusal('agent_unreachable', {}, 502))
    await click(byTest('card-edit'))
    await type('card-title', 'Bookkeeper')
    await click(byTest('card-save'))
    expect(api.writeCard).toHaveBeenCalledTimes(1)
    expect(field('card-title').value).toBe('Bookkeeper')
    // Given the time a closing editor takes to go: it is still shown.
    await new Promise(resolve => setTimeout(resolve, 120))
    await flush()
    const overlay = byTest('card-save')?.closest('.el-overlay') as HTMLElement | null
    expect(overlay).toBeTruthy()
    expect(overlay?.style.display).not.toBe('none')
    // Why it was not taken is said in the editor, not only on the page it covers.
    expect(byTest('card-error')?.textContent).toContain(words.errors.agent_unreachable)
    // It is the editor's own: the board being read anew does not take it away.
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(byTest('card-error')?.textContent).toContain(words.errors.agent_unreachable)
    // Still the same editor: pressed again, the same line is sent again.
    await click(byTest('card-save'))
    expect(api.writeCard).toHaveBeenCalledTimes(2)
    expect(api.writeCard).toHaveBeenLastCalledWith('s1', { title: 'Bookkeeper' })
  })

  it('shows those that belong to no project when no project is named, and reads anew for another', async () => {
    api.getProjectBoard.mockResolvedValue({ revision: 'r1', projects: [], loose: [card({ id: 's9', name: 'Screenshot to table', kind: 'single', preset: 'general', leader: false })] })
    const { container, state } = await mount(FamiliarsPane, { project: null, selectedId: 's9', onSelect: selected })
    expect(api.getProjectBoard).toHaveBeenCalledWith(null, undefined)
    expect(container.querySelector('[data-detail="s9"]')).toBeTruthy()
    api.getProjectBoard.mockResolvedValue(boardOf([card()]))
    state.props = { project: 'Ledger tool', selectedId: 's1', onSelect: selected }
    await flush()
    // Asked for the other project from the start, not as a change of what was shown.
    expect(api.getProjectBoard).toHaveBeenLastCalledWith('Ledger tool', undefined)
    expect(container.querySelector('[data-detail="s1"]')).toBeTruthy()
  })
})
