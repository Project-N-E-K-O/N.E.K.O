// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { SpaceEntry, SpaceProject } from '@/api/console'
import FamiliarSpace from '@/views/FamiliarSpace.vue'
import NotesPane from './NotesPane.vue'
import PreferencesPane from './PreferencesPane.vue'
import ReviewsPane from './ReviewsPane.vue'
import RulesPane from './RulesPane.vue'

const api = vi.hoisted(() => ({
  listProjects: vi.fn(), setProjectWorkdir: vi.fn(), pickDirectory: vi.fn(), readRules: vi.fn(), writeRules: vi.fn(), readIndex: vi.fn(),
  listEntries: vi.fn(), readEntry: vi.fn(), fileEntry: vi.fn(), editEntry: vi.fn(), deleteEntry: vi.fn(), listRemoved: vi.fn(),
  listReviews: vi.fn(), readReview: vi.fn(), deleteReview: vi.fn(),
  listPreferences: vi.fn(), editPreference: vi.fn(), removePreference: vi.fn(),
  getProjectBoard: vi.fn(), writeCard: vi.fn(), tellCommission: vi.fn(), answerCommission: vi.fn(), stopCommission: vi.fn(),
  getTranscript: vi.fn(), getSessionUsage: vi.fn(), searchTranscript: vi.fn(),
  // What a project's familiars left in worktrees of their own: none here.
  listWorktrees: vi.fn(() => Promise.resolve({ worktrees: [] })), getTemplates: vi.fn(), addMember: vi.fn(), giveLeader: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)

const route = reactive<{ query: Record<string, string | undefined> }>({ query: {} })
const replace = vi.fn((to: { query: Record<string, string | undefined> }) => {
  route.query = Object.fromEntries(Object.entries(to.query).filter(([, value]) => value !== undefined))
  return Promise.resolve()
})
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace }) }))

const refusal = (code: string) => Object.assign(new Error(code), { code, response: { status: 404 } })
const project = (over: Partial<SpaceProject> = {}): SpaceProject => ({
  name: 'Ledger tool', description: 'Keeps the books', workdir: 'D:/code/ledger-tool', status: 'active', created_at: 0,
  last_commission_at: 0, commissions: 7, archived_at: null, archive_year: null, directory: null, ...over,
})
const entry = (over: Partial<SpaceEntry> = {}): SpaceEntry => ({
  id: 'python-path', project: null, title: 'Python lives in D:/py312', description: 'where python is', type: 'lesson',
  created: '2026-11-20', updated: '2026-11-20', content: '', confidence: 'verified', evidence: [], evidence_outside: [],
  seen: 3, status: 'active', outdated: '', ...over,
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
async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy()
  ;(element as HTMLElement).click()
  await flush()
}
async function type(testId: string, value: string) {
  // Element Plus puts what an input is given onto the inner field, or onto its wrapper: either is found.
  const input = document.querySelector([
    `textarea[data-testid="${testId}"]`, `input[data-testid="${testId}"]`,
    `[data-testid="${testId}"] textarea`, `[data-testid="${testId}"] input`,
  ].join(', ')) as HTMLInputElement
  expect(input).toBeTruthy()
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}
const buttonSaying = (root: ParentNode, text: string) =>
  [...root.querySelectorAll('button')].find(button => button.textContent?.trim() === text)

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.clearAllMocks()
  route.query = {}
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('the rules', () => {
  it('are read for the scope shown, saved as typed, and read again when the scope changes', async () => {
    api.readRules.mockResolvedValueOnce({ text: '- answer in Chinese\n' })
    api.writeRules.mockResolvedValue({ text: '- answer in Chinese\n- be brief\n' })
    const { state } = await mount(RulesPane, { scope: {} })
    expect((byTest('rules-save') as HTMLButtonElement).disabled).toBe(true)
    await type('rules-text', '- answer in Chinese\n- be brief\n')
    await click(byTest('rules-save'))
    expect(api.writeRules).toHaveBeenCalledWith('- answer in Chinese\n- be brief\n', {})
    expect((byTest('rules-save') as HTMLButtonElement).disabled).toBe(true)

    api.readRules.mockResolvedValueOnce({ text: '- use Decimal\n' })
    state.props = { scope: { project: 'Ledger tool' } }
    await flush()
    expect(api.readRules).toHaveBeenLastCalledWith({ project: 'Ledger tool' })
    expect((document.querySelector('textarea') as HTMLTextAreaElement).value).toBe('- use Decimal\n')
  })

  it('of a scope that could not be read are not shown as those of the one before, and not saved over them', async () => {
    api.readRules.mockResolvedValueOnce({ text: '- rules of A\n' })
    const { state } = await mount(RulesPane, { scope: { project: 'A' } })
    await type('rules-text', '- rules of A, changed\n')
    expect((byTest('rules-save') as HTMLButtonElement).disabled).toBe(false)

    api.readRules.mockRejectedValueOnce(refusal('agent_unreachable'))
    state.props = { scope: { project: 'B' } }
    await flush()
    expect(document.body.textContent).toContain(words.errors.agent_unreachable)
    const field = document.querySelector('textarea') as HTMLTextAreaElement
    expect(field.value).toBe('')
    expect(field.readOnly).toBe(true)
    expect((byTest('rules-save') as HTMLButtonElement).disabled).toBe(true)
    await click(byTest('rules-save'))
    expect(api.writeRules).not.toHaveBeenCalled()
  })

  it('of an archived project are shown and cannot be saved', async () => {
    api.readRules.mockResolvedValue({ text: '- old rule\n' })
    await mount(RulesPane, { scope: { project: 'moving', archive_year: '2026' }, readonly: true })
    expect(byTest('rules-save')).toBeNull()
    expect(document.body.textContent).toContain(words.space.rules.archivedReadOnly)
  })
})

describe('the notes', () => {
  it('are listed with how they stand, and one is opened, changed and saved', async () => {
    api.listEntries.mockResolvedValue({ entries: [
      entry(),
      entry({ id: 'tables', title: 'Likes reports as tables', status: 'pending', confidence: 'inferred', seen: 1 }),
      entry({ id: 'sheet', title: 'Second sheet', status: 'expired', outdated: 'the file it rests on is gone' }),
    ] })
    api.readEntry.mockResolvedValue({ entry: entry({ content: 'Use D:/py312/python.exe.', evidence: ['D:/py312/python.exe'] }) })
    api.editEntry.mockResolvedValue({ entry: entry() })
    const { container } = await mount(NotesPane, { scope: { project: 'Ledger tool' } })
    expect(container.querySelector('[data-entry="tables"]')?.textContent).toContain(words.space.notes.statuses.pending)
    expect(container.querySelector('[data-entry="tables"]')?.textContent).toContain(words.space.notes.confidences.inferred)
    expect(container.querySelector('[data-entry="sheet"]')?.textContent).toContain('the file it rests on is gone')

    await click(buttonSaying(container.querySelector('[data-entry="python-path"]')!, words.space.notes.open))
    expect(api.readEntry).toHaveBeenCalledWith('python-path', { project: 'Ledger tool' })
    expect(document.body.textContent).toContain('D:/py312/python.exe')
    await type('note-title', 'Where Python is')
    await click(byTest('note-save'))
    expect(api.editEntry).toHaveBeenCalledWith(
      'python-path',
      { title: 'Where Python is', description: 'where python is', kind: 'lesson', content: 'Use D:/py312/python.exe.' },
      { project: 'Ledger tool' },
    )
    expect(api.listEntries).toHaveBeenCalledTimes(2)
  })

  it('one that was asked for in a scope the user has left is not opened in the next', async () => {
    api.listEntries.mockResolvedValue({ entries: [entry()] })
    let answer!: (value: unknown) => void
    api.readEntry.mockReturnValue(new Promise(resolve => { answer = resolve }))
    const { container, state } = await mount(NotesPane, { scope: { project: 'A' } })
    await click(buttonSaying(container.querySelector('[data-entry="python-path"]')!, words.space.notes.open))
    state.props = { scope: { project: 'B' } }
    await flush()
    answer({ entry: entry({ content: 'What A wrote down.' }) })
    await flush()
    // An entry of A is not put into an editor that would save it into B.
    expect(document.body.textContent).not.toContain('What A wrote down.')
    expect(byTest('note-save')).toBeNull()
  })

  it('one that was opened in a scope is never saved into the next', async () => {
    api.listEntries.mockResolvedValue({ entries: [entry()] })
    api.readEntry.mockResolvedValue({ entry: entry({ content: 'What A wrote down.' }) })
    api.editEntry.mockResolvedValue({ entry: entry() })
    const { container, state } = await mount(NotesPane, { scope: { project: 'A' } })
    await click(buttonSaying(container.querySelector('[data-entry="python-path"]')!, words.space.notes.open))
    expect(byTest('note-save')).toBeTruthy()
    state.props = { scope: { project: 'B' } }
    await flush()
    // The editor is on its way out; a press that still reaches it saves where it was opened.
    const save = byTest('note-save')
    if (save) await click(save)
    expect(api.editEntry.mock.calls.every(call => call[2]?.project === 'A')).toBe(true)
    expect(api.fileEntry).not.toHaveBeenCalled()
  })

  it('of an archived project are read and not changed or removed', async () => {
    api.listEntries.mockResolvedValue({ entries: [entry()] })
    api.readEntry.mockResolvedValue({ entry: entry({ content: 'Use D:/py312/python.exe.', evidence: ['D:/py312/python.exe'] }) })
    const { container } = await mount(NotesPane, { scope: { project: 'moving', archive_year: '2026' }, readonly: true })
    const row = container.querySelector('[data-entry="python-path"]')!
    expect(buttonSaying(row, words.space.notes.remove)).toBeUndefined()
    expect(byTest('notes-add')).toBeNull()
    await click(buttonSaying(row, words.space.notes.open))
    expect(api.readEntry).toHaveBeenCalledWith('python-path', { project: 'moving', archive_year: '2026' })
    expect(document.body.textContent).toContain('D:/py312/python.exe')
    expect(byTest('note-save')).toBeNull()
    expect((document.querySelector('[data-testid="note-title"] input, input[data-testid="note-title"]') as HTMLInputElement).disabled).toBe(true)
  })

  it('a new one needs a title and text before anything is sent', async () => {
    api.listEntries.mockResolvedValue({ entries: [] })
    api.fileEntry.mockResolvedValue({ entry: entry() })
    await mount(NotesPane, { scope: {} })
    await click(byTest('notes-add'))
    await click(byTest('note-save'))
    expect(api.fileEntry).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain(words.space.notes.required)
    await type('note-title', 'A note')
    await type('note-content', 'What it says.')
    await click(byTest('note-save'))
    expect(api.fileEntry).toHaveBeenCalledWith({ title: 'A note', description: '', kind: 'lesson', content: 'What it says.' }, {})
  })

  it('removing one says that it is not kept again, and the list is read anew whatever came of it', async () => {
    api.listEntries.mockResolvedValue({ entries: [entry()] })
    api.deleteEntry.mockRejectedValue(refusal('no_entry'))
    const { container } = await mount(NotesPane, { scope: {} })
    await click(buttonSaying(container.querySelector('[data-entry="python-path"]')!, words.space.notes.remove))
    expect(document.body.textContent).toContain(words.space.notes.removeText)
    await click(byTest('note-remove-confirm'))
    expect(api.deleteEntry).toHaveBeenCalledWith('python-path', {})
    expect(api.listEntries).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain(words.errors.no_entry)
  })

  it('shows the index and what was removed, and nothing can be added to an archived project', async () => {
    api.listEntries.mockResolvedValue({ entries: [] })
    api.readIndex.mockResolvedValue({ text: '- Python lives in D:/py312' })
    api.listRemoved.mockResolvedValue({ removed: [{ title: 'Card number' }, { marks: ['x'] }] })
    await mount(NotesPane, { scope: { project: 'moving', archive_year: '2026' }, readonly: true })
    expect(byTest('notes-add')).toBeNull()
    await click(byTest('notes-index'))
    expect(byTest('notes-read')?.textContent).toBe('- Python lives in D:/py312')
    await click(byTest('notes-removed'))
    expect(byTest('notes-read')?.textContent).toBe('Card number')
    expect(api.listRemoved).toHaveBeenCalledWith({ project: 'moving', archive_year: '2026' })
  })
})

describe('the reviews and the preferences', () => {
  it('a review is read by its commission and removed', async () => {
    const review = { commission_id: 'c1', project: 'Ledger tool', archive_year: null, title: 'October bills', status: 'done', ended: '2026-11-20 21:14', entries: [], content: '' }
    api.listReviews.mockResolvedValue({ reviews: [review] })
    api.readReview.mockResolvedValue({ review: { ...review, content: 'What went well: the table.' } })
    api.deleteReview.mockResolvedValue({ success: true })
    const { container } = await mount(ReviewsPane, { project: 'Ledger tool' })
    expect(api.listReviews).toHaveBeenCalledWith('Ledger tool', undefined)
    await click(buttonSaying(container.querySelector('[data-review="c1"]')!, words.space.reviews.open))
    expect(byTest('review-text')?.textContent).toBe('What went well: the table.')
    await click(buttonSaying(container.querySelector('[data-review="c1"]')!, words.space.reviews.remove))
    await click(byTest('review-remove-confirm'))
    expect(api.deleteReview).toHaveBeenCalledWith('c1')
  })

  it('removes the review that was asked to be removed, whatever was read late', async () => {
    const first = { commission_id: 'c1', project: 'Ledger tool', archive_year: null, title: 'October bills', status: 'done', ended: '2026-11-20 21:14', entries: [], content: '' }
    const second = { ...first, commission_id: 'c2', title: 'November bills' }
    api.listReviews.mockResolvedValue({ reviews: [first, second] })
    let read!: (value: unknown) => void
    api.readReview.mockReturnValue(new Promise(resolve => { read = resolve }))
    api.deleteReview.mockResolvedValue({ success: true })
    const { container } = await mount(ReviewsPane, { project: 'Ledger tool' })
    await click(buttonSaying(container.querySelector('[data-review="c1"]')!, words.space.reviews.open))
    await click(buttonSaying(container.querySelector('[data-review="c2"]')!, words.space.reviews.remove))
    // The first one is read only now, with the confirmation for the second open.
    read({ review: { ...first, content: 'What went well: the table.' } })
    await flush()
    await click(byTest('review-remove-confirm'))
    expect(api.deleteReview).toHaveBeenCalledTimes(1)
    expect(api.deleteReview).toHaveBeenCalledWith('c2')
  })

  it('a preference is switched off, reworded and removed', async () => {
    const preference = { id: 4, kind: 'report', text: 'Tell me the result', origin: 'stated' as const, observations: 1, active: true, lanlan_name: 'Lan', updated_at: 0 }
    api.listPreferences.mockResolvedValue({ preferences: [preference] })
    api.editPreference.mockResolvedValueOnce({ preference: { ...preference, active: false } })
    const { container } = await mount(PreferencesPane)
    const row = container.querySelector('[data-preference="4"]')!
    expect(row.textContent).toContain(words.space.preferences.kinds.report)
    expect(row.textContent).toContain(words.space.preferences.origins.stated)
    await click(row.querySelector('.el-switch input'))
    expect(api.editPreference).toHaveBeenCalledWith(4, { active: false })
    expect((row.querySelector('.el-switch input') as HTMLInputElement).checked).toBe(false)

    api.editPreference.mockResolvedValueOnce({ preference: { ...preference, active: false, text: 'Report in one line', origin: 'console' } })
    await click(buttonSaying(row, words.space.preferences.edit))
    await type('preference-text', 'Report in one line')
    await click(byTest('preference-save'))
    expect(api.editPreference).toHaveBeenLastCalledWith(4, { text: 'Report in one line' })
    expect(container.textContent).toContain(words.space.preferences.origins.console)

    api.removePreference.mockResolvedValue({ success: true })
    api.listPreferences.mockResolvedValue({ preferences: [] })
    await click(buttonSaying(container.querySelector('[data-preference="4"]')!, words.space.preferences.remove))
    await click(byTest('preference-remove-confirm'))
    expect(api.removePreference).toHaveBeenCalledWith(4)
    expect(container.textContent).toContain(words.space.preferences.empty)
  })
})

describe('the familiar space page', () => {
  beforeEach(() => {
    api.listProjects.mockResolvedValue({ projects: [
      project(),
      project({ name: 'Moving list', archive_year: '2026', directory: 'moving-list', workdir: null, commissions: 2 }),
    ] })
    api.readRules.mockResolvedValue({ text: '' })
    api.listEntries.mockResolvedValue({ entries: [] })
    api.listReviews.mockResolvedValue({ reviews: [] })
    api.listPreferences.mockResolvedValue({ preferences: [] })
    api.getProjectBoard.mockResolvedValue({ revision: 'r1', projects: [], loose: [] })
  })

  it('opens on what all familiars share, with the task preferences and no reviews', async () => {
    const { container } = await mount(FamiliarSpace)
    expect(byTest('space-project')).toBeNull()
    expect(api.readRules).toHaveBeenCalledWith({})
    const tabs = [...container.querySelectorAll('.el-tabs__item')].map(tab => tab.textContent?.trim())
    expect(tabs).toEqual([
      words.space.familiars.tab, words.space.tabs.activity, words.space.tabs.rules, words.space.tabs.notes, words.space.tabs.preferences,
    ])
    // Opened on the rules: the familiars are read when their tab is.
    expect(container.querySelector('.el-tabs__item.is-active')?.textContent?.trim()).toBe(words.space.tabs.rules)
    expect(api.getProjectBoard).not.toHaveBeenCalled()
  })

  it('shows a project with where it works, its own rules and its reviews', async () => {
    const { container } = await mount(FamiliarSpace)
    await click(container.querySelector('[data-scope="Ledger tool"]'))
    expect(route.query).toEqual({ project: 'Ledger tool' })
    expect(byTest('space-project')?.textContent).toContain('D:/code/ledger-tool')
    expect(api.readRules).toHaveBeenLastCalledWith({ project: 'Ledger tool' })
    const tabs = [...container.querySelectorAll('.el-tabs__item')].map(tab => tab.textContent?.trim())
    expect(tabs).toEqual([
      words.space.familiars.tab, words.space.tabs.activity, words.space.tabs.rules, words.space.tabs.notes, words.space.tabs.reviews,
    ])
  })

  it('names an archived project by its directory and year, and shows it read-only', async () => {
    const { container } = await mount(FamiliarSpace)
    await click(container.querySelector('[data-scope="2026/moving-list"]'))
    expect(route.query).toEqual({ project: 'moving-list', year: '2026' })
    expect(api.readRules).toHaveBeenLastCalledWith({ project: 'moving-list', archive_year: '2026' })
    expect(byTest('space-project')?.textContent).toContain(words.space.workdirDefault)
    expect(byTest('rules-save')).toBeNull()
    // Nothing works in an archived project: no familiars to show.
    const tabs = [...container.querySelectorAll('.el-tabs__item')].map(tab => tab.textContent?.trim())
    expect(tabs).toEqual([words.space.tabs.rules, words.space.tabs.notes, words.space.tabs.reviews])
  })

  it('changes where a project works with a folder that was chosen or typed, and says why when it cannot', async () => {
    route.query = { project: 'Ledger tool' }
    await mount(FamiliarSpace)
    await click(byTest('workdir-change'))
    // What it is now is offered, and saving that again is not offered.
    expect((document.querySelector('input[data-testid="workdir-path"], [data-testid="workdir-path"] input') as HTMLInputElement).value).toBe('D:/code/ledger-tool')
    expect((byTest('workdir-save') as HTMLButtonElement).disabled).toBe(true)

    api.pickDirectory.mockResolvedValueOnce({ cancelled: true })
    await click(byTest('workdir-pick'))
    expect(api.pickDirectory).toHaveBeenCalledWith('D:/code/ledger-tool')
    // Closed without choosing: what was in the field stays.
    expect((document.querySelector('input[data-testid="workdir-path"], [data-testid="workdir-path"] input') as HTMLInputElement).value).toBe('D:/code/ledger-tool')
    expect((byTest('workdir-save') as HTMLButtonElement).disabled).toBe(true)
    api.pickDirectory.mockResolvedValueOnce({ path: 'D:/code/ledger-v2' })
    await click(byTest('workdir-pick'))

    api.setProjectWorkdir.mockRejectedValueOnce(refusal('project_busy'))
    await click(byTest('workdir-save'))
    expect(api.setProjectWorkdir).toHaveBeenCalledWith('Ledger tool', 'D:/code/ledger-v2')
    expect(byTest('workdir-error')?.textContent).toContain(words.errors.project_busy)
    expect(byTest('space-project')?.textContent).toContain('D:/code/ledger-tool')

    api.setProjectWorkdir.mockResolvedValueOnce({ project: project({ workdir: 'D:/code/ledger-v2' }) })
    await click(byTest('workdir-save'))
    expect(byTest('space-project')?.textContent).toContain('D:/code/ledger-v2')
    expect(byTest('workdir-error')).toBeNull()
  })

  it('offers no change of folder for an archived project', async () => {
    route.query = { project: 'moving-list', year: '2026' }
    await mount(FamiliarSpace)
    expect(byTest('space-project')).toBeTruthy()
    expect(byTest('workdir-change')).toBeNull()
  })

  it('goes to the rules from the familiar an address opened on', async () => {
    route.query = { project: 'Ledger tool', familiar: 's1' }
    const { container } = await mount(FamiliarSpace)
    const active = () => container.querySelector('.el-tabs__item.is-active')?.textContent?.trim()
    expect(active()).toBe(words.space.familiars.tab)
    const rules = [...container.querySelectorAll('.el-tabs__item')].find(tab => tab.textContent?.trim() === words.space.tabs.rules)
    await click(rules)
    expect(route.query.tab).toBe('rules')
    expect(active()).toBe(words.space.tabs.rules)
  })

  it('says that the familiar an address names is off the board only once it knows which project is meant', async () => {
    api.getTranscript.mockResolvedValue({ readable: true, before: null, entries: [] })
    // The projects are still being read: the board that is read meanwhile is that of no project.
    let known: (answer: unknown) => void = () => {}
    api.listProjects.mockReturnValue(new Promise(resolve => { known = resolve }))
    route.query = { project: 'Ledger tool', familiar: 's1' }
    await mount(FamiliarSpace)
    expect(api.getProjectBoard).toHaveBeenCalled()
    expect(byTest('familiar-past')).toBeNull()
    expect(api.getTranscript).not.toHaveBeenCalled()
    // Known now, and the board of that project has no such familiar: said.
    known({ projects: [project()] })
    await flush()
    expect(byTest('familiar-past')).toBeTruthy()
    cleanup?.()
    // The projects could not be read: nothing is known of the one that is named, nothing is concluded.
    api.getTranscript.mockClear()
    api.listProjects.mockRejectedValue({ code: 'agent_unreachable', response: { status: 503 } })
    route.query = { project: 'Ledger tool', familiar: 's1' }
    await mount(FamiliarSpace)
    expect(byTest('familiar-past')).toBeNull()
    expect(api.getTranscript).not.toHaveBeenCalled()
    cleanup?.()
    // Read, and the project that is named is not at work any more (put away, renamed): none of
    // its familiars is on a board, and what was said in the one that is named is read all the same.
    api.listProjects.mockResolvedValue({ projects: [
      project(), project({ name: 'Moving list', archive_year: '2026', directory: 'moving-list', workdir: null, commissions: 2 }),
    ] })
    route.query = { project: 'Moving list', familiar: 's7' }
    await mount(FamiliarSpace)
    expect(byTest('familiar-past')).toBeTruthy()
    expect(api.getTranscript).toHaveBeenCalledWith('s7', { before: null })
    cleanup?.()
    // No project named: the familiars of none are the ones meant.
    route.query = { familiar: 's9' }
    await mount(FamiliarSpace)
    expect(byTest('familiar-past')).toBeTruthy()
  })

  it('asks for the reviews of an archived project by its year', async () => {
    route.query = { project: 'moving-list', year: '2026', tab: 'reviews' }
    await mount(FamiliarSpace)
    expect(api.listReviews).toHaveBeenCalledWith(expect.any(String), '2026')
  })

  it('takes the project and the tab from the address', async () => {
    route.query = { project: 'Ledger tool', tab: 'reviews' }
    await mount(FamiliarSpace)
    expect(api.listReviews).toHaveBeenCalledWith('Ledger tool', null)
    // A tab the scope does not have falls back to the rules.
    cleanup?.()
    route.query = { tab: 'reviews' }
    const { container } = await mount(FamiliarSpace)
    expect(container.querySelector('.el-tabs__item.is-active')?.textContent?.trim()).toBe(words.space.tabs.rules)
  })
})
