// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import chinese from '@/i18n/power/zh-CN'
import type {
  BoardAsk, CommissionRow, McpView, RoleModelsView, SessionUsage, TranscriptEntry, UsageLimit,
} from '@/api/console'
import Commissions from '@/views/CommissionList.vue'
import McpTools from '@/views/McpTools.vue'
import AskBox from './AskBox.vue'
import RoleModelsTab from './RoleModelsTab.vue'
import UsageLimitCard from './UsageLimitCard.vue'
import { askFacts, askLines, askSummary, askWarning, askWhole } from './ask'
import dshPluginCommands from './dshPluginCommands.json'
import { formatDuration, formatTokens } from './powerFormat'
import ActivityPane from './space/ActivityPane.vue'
import TranscriptPane from './space/TranscriptPane.vue'
import UsagePane from './space/UsagePane.vue'

const api = vi.hoisted(() => ({
  getTranscript: vi.fn(), getSessionUsage: vi.fn(), getCommissionUsage: vi.fn(), getRoleModels: vi.fn(), setRoleModel: vi.fn(),
  getUsageLimit: vi.fn(), setUsageLimit: vi.fn(), getMcp: vi.fn(), setMcpPermission: vi.fn(),
  listCommissions: vi.fn(), listProjects: vi.fn(), tellCommission: vi.fn(), answerCommission: vi.fn(), stopCommission: vi.fn(),
  getTimeline: vi.fn(), getTimelineStart: vi.fn(), getProjectBoard: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const refused = (code: string, status = 400) => ({ code, response: { status, data: { detail: { code } } } })
let cleanup: (() => void) | undefined
// The language of the page that was mounted last: set it to see the page in another.
let language: { value: string } | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() } }
async function mount(component: Component, props: Record<string, unknown> = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const state = reactive({ props })
  const app = createApp({ render: () => h(component, state.props) })
  app.use(ElementPlus)
  const mounted = createI18n({
    legacy: false, locale: 'en-US',
    messages: { 'en-US': { power: words, common: { cancel: 'Cancel' } }, 'zh-CN': { power: chinese, common: { cancel: '取消' } } },
  })
  language = mounted.global.locale as unknown as { value: string }
  app.use(mounted)
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
async function choose(select: Element | null, value: string) {
  expect(select).toBeTruthy()
  ;(select as HTMLSelectElement).value = value
  select!.dispatchEvent(new Event('change', { bubbles: true }))
  await flush()
}
async function type(testId: string, value: string) {
  const input = document.querySelector([
    `input[data-testid="${testId}"]`, `[data-testid="${testId}"] input`,
  ].join(', ')) as HTMLInputElement
  expect(input).toBeTruthy()
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}
/** Press Enter in a field. */
function enter(testId: string) {
  const input = document.querySelector([
    `input[data-testid="${testId}"]`, `[data-testid="${testId}"] input`,
  ].join(', ')) as HTMLInputElement
  expect(input).toBeTruthy()
  input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
}
const i18n = createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words } } })
const { t, te } = i18n.global

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.resetAllMocks()
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals() })

const LONG = `npm run build -- --mode production && node scripts/check-links.mjs --root dist ${'--ignore https://example.com/a/very/long/path '.repeat(12)}&& echo done`
const permission = (over: Partial<BoardAsk> = {}): BoardAsk => ({
  id: 'q1', kind: 'permission', category: 'run_command', detail: { tool: 'bash', command: LONG, cwd: 'D:/blog' }, options: ['once', 'deny'], ...over,
})

describe('what a familiar asks the user', () => {
  it('is told by the kind of thing, with the command it would run whole however long it is', () => {
    expect(askLines(permission(), t, te)).toEqual([`${words.runtime.permissions.categories.run_command} · bash`])
    expect(askWhole(permission())).toEqual({ kind: 'command', text: LONG })
    expect(askFacts(permission(), t, te)).toEqual([{ name: 'cwd', label: words.ask.facts.cwd, value: 'D:/blog' }])
    // A path where there is no command; neither for a question put in words.
    expect(askWhole(permission({ detail: { tool: 'write', path: 'C:/notes/todo.md' } }))).toEqual({ kind: 'path', text: 'C:/notes/todo.md' })
    expect(askWhole({ id: 'q', question: 'Which day?' })).toBeNull()
    expect(askLines({ id: 'q', question: 'Which day?' }, t, te)).toEqual(['Which day?'])
    // A kind the permission table does not show has a name of its own; one nobody named is said as it came.
    expect(askLines({ category: 'usage_limit', detail: { used: '5M', more: '5M' } }, t, te)).toEqual([words.ask.categories.usage_limit])
    expect(askFacts({ category: 'usage_limit', detail: { used: '5M', more: '5M' } }, t, te).map(fact => fact.label)).toEqual([
      words.ask.facts.used, words.ask.facts.more,
    ])
    expect(askLines({ category: 'brand_new' }, t, te)).toEqual(['brand_new'])
    // A boss asking to summon a familiar that changes files: the kind of familiar by its name.
    const summon = { category: 'open_session', detail: { tool: 'hand_over', title: 'general', preset: 'worker' } }
    expect(askLines(summon, t, te)).toEqual([`${words.runtime.permissions.categories.open_session} · ${words.ask.tools.hand_over}`])
    expect(askFacts(summon, t, te)).toEqual([
      { name: 'title', label: words.ask.facts.title, value: 'general' },
      { name: 'preset', label: words.ask.facts.preset, value: words.home.presets.worker },
    ])
    // One that will work in a copy of its own is said to.
    const inTree = askFacts({ category: 'open_session', detail: { tool: 'hand_over', preset: 'worker', tree: true } }, t, te)
    expect(inTree.find(fact => fact.name === 'tree')).toEqual({ name: 'tree', label: words.ask.facts.tree, value: words.ask.treeNote })
    expect(askFacts({ detail: { tree: false } }, t, te)).toEqual([])
    // A team: how many, one familiar a line, and not the same again as one line.
    const team = {
      category: 'open_session',
      detail: { tool: 'start_team', title: 'worker: a; scout: b', members: ['worker: a', 'scout: b'], count: 2, tree: true },
    }
    expect(askLines(team, t, te)).toEqual([`${words.runtime.permissions.categories.open_session} · ${words.ask.tools.start_team}`])
    expect(askFacts(team, t, te)).toEqual([
      { name: 'members', label: words.ask.facts.members, value: '· worker: a\n· scout: b' },
      { name: 'count', label: words.ask.facts.count, value: '2' },
      { name: 'tree', label: words.ask.facts.tree, value: words.ask.treeNote },
    ])
    // Where the kind of each is given apart, it is said by its name.
    const named = { ...team, detail: { ...team.detail, parts: [{ preset: 'worker', description: 'a' }, { preset: 'translator', description: 'b' }] } }
    expect(askFacts(named, t, te)[0]).toEqual({ name: 'members', label: words.ask.facts.members, value: `· ${words.home.presets.worker}: a\n· translator: b` })
    // A tool the pages have no words for is named as it is.
    expect(askLines({ category: 'open_session', detail: { tool: 'brand_new' } }, t, te)[0]).toContain('brand_new')
    expect(askFacts({ category: 'open_session', detail: { preset: 'translator' } }, t, te)[0].value).toBe('translator')
    expect(askFacts({ detail: { odd: 3, nested: { a: 1 }, empty: '' } }, t, te)).toEqual([{ name: 'odd', label: 'odd', value: '3' }])
    // In a list it is one short line.
    expect(askSummary(permission(), t, te).length).toBeLessThanOrEqual(80)
    expect(askSummary(permission(), t, te)).toContain('npm run build')
    expect(askSummary({ question: 'Which day?' }, t, te)).toBe('Which day?')
  })

  it('shows the whole command to read and copy, and hands back the answer that was pressed', async () => {
    const written: string[] = []
    vi.stubGlobal('navigator', { clipboard: { writeText: async (text: string) => { written.push(text) } } })
    const answers: unknown[] = []
    await mount(AskBox, { ask: permission(), busy: '', onAnswer: (given: unknown) => answers.push(given) })
    expect(byTest('ask-whole')?.textContent).toBe(LONG)
    expect(byTest('ask-facts')?.textContent).toContain('D:/blog')
    await click(byTest('ask-copy'))
    expect(written).toEqual([LONG])
    expect(byTest('ask-copy')?.textContent).toBe(words.ask.copied)
    await click(document.querySelector('[data-choice="deny"]'))
    expect(answers).toEqual([{ choice: 'deny' }])
    // Nothing to type an answer into: it is answered by its choices.
    expect(byTest('familiar-reply')).toBeNull()
  })

  it('says what a dsh plugin may do when the command would add one, and only then', async () => {
    const adding = permission({ detail: { tool: 'bash', command: 'cd D:/x && neko-dsh-plugin add dsh-free-search' } })
    expect(askWarning(adding, t)).toBe(words.runtime.plugins.warning)
    // The cases the backend's `adds_plugin` is tested with too, so the warning and her question agree.
    for (const command of dshPluginCommands.adds) {
      expect(askWarning(permission({ detail: { command } }), t), command).toBe(words.runtime.plugins.warning)
    }
    for (const command of [...dshPluginCommands.local, LONG]) {
      expect(askWarning(permission({ detail: { command } }), t), command).toBeNull()
    }
    // Only a command asks to run it: a path that happens to read so is no plugin being added.
    expect(askWarning(permission({ category: 'outside_read', detail: { command: 'neko-dsh-plugin add a' } }), t)).toBeNull()
    await mount(AskBox, { ask: adding, busy: '' })
    expect(byTest('ask-warning')?.textContent).toContain(words.runtime.plugins.warning)
    expect(byTest('ask-whole')?.textContent).toBe('cd D:/x && neko-dsh-plugin add dsh-free-search')
  })

  it('shows no warning for any other request', async () => {
    await mount(AskBox, { ask: permission(), busy: '' })
    expect(byTest('ask-warning')).toBeNull()
  })

  it('takes an answer in words for a question put in words, and none that says nothing', async () => {
    const answers: unknown[] = []
    const { state } = await mount(AskBox, { ask: { id: 'q2', question: 'Which day?' }, busy: '', onAnswer: (given: unknown) => answers.push(given) })
    expect(byTest('ask-whole')).toBeNull()
    expect((byTest('familiar-answer') as HTMLButtonElement).disabled).toBe(true)
    await type('familiar-reply', 'Friday')
    await click(byTest('familiar-answer'))
    expect(answers).toEqual([{ text: 'Friday' }])
    // Enter sends as the button does: nothing while an answer is on its way.
    state.props = { ...state.props, busy: 'answer' }
    await flush()
    enter('familiar-reply')
    expect(answers).toEqual([{ text: 'Friday' }])
    state.props = { ...state.props, busy: '' }
    await flush()
    enter('familiar-reply')
    expect(answers).toEqual([{ text: 'Friday' }, { text: 'Friday' }])
  })

  it('marks the whole command, to be copied with the keys, where the page has no clipboard', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: async () => { throw new Error('not allowed') } } })
    await mount(AskBox, { ask: permission(), busy: '' })
    await click(byTest('ask-copy'))
    // It does not say it was copied; all of it is marked instead.
    expect(byTest('ask-copy')?.textContent).toBe(words.ask.copy)
    const marked = window.getSelection()
    expect(marked?.rangeCount).toBe(1)
    expect(marked?.getRangeAt(0).commonAncestorContainer).toBe(byTest('ask-whole'))
    expect(marked?.toString()).toBe(LONG)
  })
})

describe('numbers as a person reads them', () => {
  it('says tokens and durations in a few characters', () => {
    expect([0, 950, 1_000, 12_345, 999_499, 999_500, 1_250_001, 5_000_000, 123_400_000].map(formatTokens)).toEqual([
      '0', '950', '1k', '12k', '999k', '1M', '1.3M', '5M', '123M',
    ])
    expect([formatTokens(null), formatTokens(undefined), formatTokens(Number.NaN)]).toEqual(['', '', ''])
    expect([0, 45, 60, 725, 3600, 11_100].map(formatDuration)).toEqual(['0s', '45s', '1m', '12m', '1h', '3h 5m'])
    expect([formatDuration(null), formatDuration(-1)]).toEqual(['', ''])
  })
})

const said = (over: Partial<TranscriptEntry> = {}): TranscriptEntry => ({
  at: 0, role: 'user', text: 'sort the photos', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: 1_700_000_000, ...over,
})

describe('what was said in a familiar', () => {
  it('shows the last page, reads the one before above it, and says where it began', async () => {
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: 400, entries: [
      said({ at: 400, role: 'assistant', text: 'Looking.', calls: [{ name: 'glob', arguments: '{"pattern":"*.jpg"}', cut: false }] }),
      said({ at: 520, role: 'tool_result', text: 'a.jpg', tool: 'glob' }),
      said({ at: 600, role: 'tool_result', text: 'no such file', tool: 'read', failed: true }),
    ] })
    await mount(TranscriptPane, { sessionId: 's1' })
    expect(api.getTranscript).toHaveBeenCalledWith('s1', { before: null })
    const roles = () => [...document.querySelectorAll('[data-entry]')].map(item => item.getAttribute('data-role'))
    expect(roles()).toEqual(['assistant', 'tool_result', 'tool_result'])
    expect(document.body.textContent).toContain(words.transcript.calls.replace('{tool}', 'glob'))
    expect(document.body.textContent).toContain(words.transcript.roles.tool_failed.replace('{tool}', 'read'))

    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [said({ at: 0, pictures: 2 })] })
    await click(byTest('transcript-earlier'))
    expect(api.getTranscript).toHaveBeenLastCalledWith('s1', { before: 400 })
    expect(roles()).toEqual(['user', 'assistant', 'tool_result', 'tool_result'])
    expect(document.body.textContent).toContain(words.transcript.pictures.replace('{count}', '2'))
    // The beginning: nothing earlier is offered.
    expect(byTest('transcript-earlier')).toBeNull()
    expect(document.body.textContent).toContain(words.transcript.beginning)
  })

  it('reads an entry that was cut whole when asked, in its place', async () => {
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      said({ at: 0 }), said({ at: 90, role: 'tool_result', tool: 'read', text: 'the beginning…', cut: true }),
    ] })
    await mount(TranscriptPane, { sessionId: 's1' })
    expect(document.querySelector('[data-whole="0"]')).toBeNull()
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      said({ at: 90, role: 'tool_result', tool: 'read', text: 'the beginning and all the rest', cut: false }),
    ] })
    await click(document.querySelector('[data-whole="90"]'))
    expect(api.getTranscript).toHaveBeenLastCalledWith('s1', { at: 90 })
    expect(document.querySelector('[data-entry="90"]')?.textContent).toContain('the beginning and all the rest')
    expect(document.querySelector('[data-whole="90"]')).toBeNull()
    expect(document.querySelectorAll('[data-entry]').length).toBe(2)
  })

  it('does not say of another familiar why the one before could not be read', async () => {
    api.getTranscript.mockResolvedValueOnce({ readable: false, reason: 'dsh', entries: [], before: null })
    const { state } = await mount(TranscriptPane, { sessionId: 's1' })
    expect(byTest('transcript-unreadable')?.textContent).toBe(words.transcript.inDsh)
    // The next one cannot be read right now: that is said, with the way to read again, and not what held for the first.
    api.getTranscript.mockRejectedValueOnce(refused('record_unreadable', 409))
    state.props = { sessionId: 's2' }
    await flush()
    expect(byTest('transcript-unreadable')).toBeNull()
    expect(byTest('transcript-error')?.textContent).toContain(words.errors.record_unreadable)
    expect(byTest('transcript-latest')).toBeTruthy()
    // A third one, still being read: that the second could not be read is not said of it.
    api.getTranscript.mockReturnValueOnce(new Promise(() => {}))
    state.props = { sessionId: 's3' }
    await flush()
    expect(byTest('transcript-error')).toBeNull()
  })

  it('does not put an entry that was read whole into the conversation of another familiar opened meanwhile', async () => {
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [said({ at: 0, text: 'the first of A…', cut: true })] })
    const { state } = await mount(TranscriptPane, { sessionId: 's1' })
    let answer: (page: unknown) => void = () => {}
    api.getTranscript.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
    await click(document.querySelector('[data-whole="0"]'))
    // Another familiar is opened before the answer comes: its conversation begins at the same place.
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [said({ at: 0, text: 'the first of B' })] })
    state.props = { sessionId: 's2' }
    await flush()
    expect(document.querySelector('[data-entry="0"]')?.textContent).toContain('the first of B')
    answer({ readable: true, before: null, entries: [said({ at: 0, text: 'the first of A, all of it', cut: false })] })
    await flush()
    expect(document.querySelector('[data-entry="0"]')?.textContent).toContain('the first of B')
    expect(document.body.textContent).not.toContain('the first of A')
  })

  it('says why there is nothing to read of a session dsh holds, and of one nothing was said in', async () => {
    api.getTranscript.mockResolvedValueOnce({ readable: false, reason: 'dsh', entries: [], before: null })
    const { state } = await mount(TranscriptPane, { sessionId: 's1' })
    expect(byTest('transcript-unreadable')?.textContent).toBe(words.transcript.inDsh)
    // It runs elsewhere: there will never be anything here to read again.
    expect(byTest('transcript-latest')).toBeNull()
    api.getTranscript.mockResolvedValueOnce({ readable: false, reason: 'empty', entries: [], before: null })
    state.props = { sessionId: 's2' }
    await flush()
    expect(byTest('transcript-unreadable')?.textContent).toBe(words.transcript.empty)
    // Nothing was said in it yet; once something was, it is read from right here.
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [said({ at: 0 })] })
    await click(byTest('transcript-latest'))
    expect(api.getTranscript).toHaveBeenLastCalledWith('s2', { before: null })
    expect(document.querySelectorAll('[data-entry]').length).toBe(1)
    expect(byTest('transcript-unreadable')).toBeNull()
    // Another familiar: what is shown is its own, read anew.
    api.getTranscript.mockRejectedValueOnce(refused('no_familiar', 404))
    state.props = { sessionId: 's3' }
    await flush()
    expect(byTest('transcript-error')?.textContent).toContain(words.errors.no_familiar)
    // Not read is not the same as nothing said: only the one is said, and it can be read again.
    expect(document.body.textContent).not.toContain(words.transcript.nothing)
    expect(byTest('transcript-latest')).toBeTruthy()
    api.getTranscript.mockRejectedValueOnce(refused('record_unreadable', 409))
    await click(byTest('transcript-latest'))
    expect(byTest('transcript-error')?.textContent).toContain(words.errors.record_unreadable)
  })
})

const usage = (over: Partial<SessionUsage> = {}): SessionUsage => ({
  session: { requests: 38, input: 113_000, output: 42_000, cache_read: 297_000, cache_write: 0, tokens: 452_000, context_tokens: 62_000 },
  window: { window: 200_000 },
  commission: { id: 'c1', title: 'Check the build', status: 'working', used: 4_100_000, limit: 5_000_000,
    own: { requests: 9, input: 1, output: 1, cache_read: 0, cache_write: 0, tokens: 2 } },
  ...over,
})

describe('what a familiar used', () => {
  it('shows what it used all told, how full its context is, and how near its task is to the limit', async () => {
    api.getSessionUsage.mockResolvedValue(usage())
    const { state } = await mount(UsagePane, { sessionId: 's1', refresh: 'a' })
    const tiles = byTest('usage-tiles')?.textContent ?? ''
    expect(tiles).toContain('38')
    // Input is what was sent anew and what was read from the cache together.
    expect(tiles).toContain('410k')
    expect(tiles).toContain('297k')
    expect(tiles).toContain('31% (62k)')
    expect(byTest('usage-limit-line')?.textContent).toBe(words.usage.ofLimit.replace('{used}', '4.1M').replace('{limit}', '5M'))
    expect(document.querySelector('.bar i')?.classList.contains('near')).toBe(true)
    expect((document.querySelector('.bar i') as HTMLElement).style.width).toBe('82%')
    // Read again when what it has in hand moved on.
    state.props = { sessionId: 's1', refresh: 'b' }
    await flush()
    expect(api.getSessionUsage).toHaveBeenCalledTimes(2)
  })

  it('shows nothing of the familiar before when another one is opened, read or not', async () => {
    api.getSessionUsage.mockResolvedValueOnce(usage())
    const { state } = await mount(UsagePane, { sessionId: 's1', refresh: 'a' })
    expect(byTest('usage-tiles')?.textContent).toContain('38')
    // Read again for the same one: its numbers stay while the read is out.
    let again: (answer: unknown) => void = () => {}
    api.getSessionUsage.mockReturnValueOnce(new Promise(resolve => { again = resolve }))
    state.props = { sessionId: 's1', refresh: 'b' }
    await flush()
    expect(byTest('usage-tiles')?.textContent).toContain('38')
    again(usage())
    await flush()
    // Another one, whose usage cannot be read: the numbers of the first are gone, and why is said.
    api.getSessionUsage.mockRejectedValueOnce(refused('no_familiar', 404))
    state.props = { sessionId: 's2', refresh: 'b' }
    await flush()
    expect(byTest('usage-tiles')).toBeNull()
    expect(byTest('usage-error')?.textContent).toContain(words.errors.no_familiar)
    // A third one, still being read: that the second could not be read is not said of it.
    api.getSessionUsage.mockReturnValueOnce(new Promise(() => {}))
    state.props = { sessionId: 's3', refresh: 'b' }
    await flush()
    expect(byTest('usage-error')).toBeNull()
    // The same one read again after a failure: why stays said until it is read.
    cleanup?.()
    api.getSessionUsage.mockRejectedValueOnce(refused('no_familiar', 404))
    const next = await mount(UsagePane, { sessionId: 's4', refresh: 'a' })
    api.getSessionUsage.mockReturnValueOnce(new Promise(() => {}))
    next.state.props = { sessionId: 's4', refresh: 'b' }
    await flush()
    expect(byTest('usage-error')).toBeTruthy()
  })

  it('says so when no limit is set, and shows nothing in hand for one that never had a task', async () => {
    api.getSessionUsage.mockResolvedValueOnce(usage({ commission: { ...usage().commission!, limit: null, used: 900 } }))
    const { state } = await mount(UsagePane, { sessionId: 's1' })
    expect(byTest('usage-limit-line')?.textContent).toBe(words.usage.noLimit.replace('{used}', '900'))
    expect(document.querySelector('.bar')).toBeNull()
    api.getSessionUsage.mockResolvedValueOnce(usage({ commission: null, window: null }))
    state.props = { sessionId: 's2' }
    await flush()
    expect(byTest('usage-limit-line')).toBeNull()
    expect(byTest('usage-tiles')?.textContent).toContain('62k')
    api.getSessionUsage.mockRejectedValueOnce(refused('no_familiar', 404))
    state.props = { sessionId: 's3' }
    await flush()
    expect(byTest('usage-error')?.textContent).toContain(words.errors.no_familiar)
  })
})

const roles = (over: Partial<RoleModelsView> = {}): RoleModelsView => ({
  presets: [
    { preset: 'boss', label: '', source: 'builtin', valid: true, setting: null, default: 'agent', can_inherit: false,
      in_effect: { follows: null, slot: 'slot_agent', model: 'agent-model', agent: true, free: true, fallback: null } },
    { preset: 'scout', label: '', source: 'builtin', valid: true, setting: 'slot_gone', default: 'inherit', can_inherit: true,
      in_effect: { follows: 'starter', fallback: 'slot_not_found' } },
    { preset: 'translator', label: 'Translator', source: 'user', valid: false, setting: 'slot_own', default: 'agent', can_inherit: true,
      in_effect: { follows: null, slot: 'slot_own', model: 'own-model', agent: false, free: false, fallback: null } },
  ],
  slots: [
    { slot_id: 'slot_agent', name: 'Follows the familiar model', source: 'host_follow', host_api: 'agent', model: 'agent-model', configured: true, free: true, error_code: null },
    { slot_id: 'slot_premium', name: 'Familiar model · premium', source: 'host_follow', host_api: 'agent_premium', model: null, configured: false, free: false, error_code: null },
    { slot_id: 'slot_own', name: 'Cheap one', source: 'own', host_api: null, model: 'own-model', configured: true, free: false, error_code: null },
  ],
  slots_error: null,
  ...over,
})
const options = (preset: string) => [...document.querySelectorAll(`[data-setting="${preset}"] option`)].map(option => [
  (option as HTMLOptionElement).value, option.textContent?.trim(),
])

describe('the model of each kind of familiar', () => {
  it('offers whatever slots the host lists, the default, and following the starter only where one can', async () => {
    api.getRoleModels.mockResolvedValue(roles())
    await mount(RoleModelsTab)
    // Every slot the host gave is on offer, by its own name: none of them is known to the page beforehand.
    expect(options('boss')).toEqual([
      ['', words.roles.default.replace('{what}', words.roles.agentModel)],
      ['slot_agent', 'Follows the familiar model (agent-model)'],
      ['slot_premium', `Familiar model · premium · ${words.roles.slotEmpty}`],
      ['slot_own', 'Cheap one (own-model)'],
    ])
    expect(options('scout')[0]).toEqual(['', words.roles.default.replace('{what}', words.roles.inherit)])
    expect(options('scout')[1]).toEqual(['inherit', words.roles.inherit])
    // A slot that is set and gone is still named, so that what is set can be read.
    expect(options('scout').at(-1)).toEqual(['slot_gone', words.roles.goneSlot])
    expect((document.querySelector('[data-setting="scout"]') as HTMLSelectElement).value).toBe('slot_gone')

    expect(document.querySelector('[data-effect="boss"]')?.textContent).toContain('agent-model')
    expect(document.querySelector('[data-effect="boss"]')?.textContent).toContain(words.roles.free)
    const fallen = document.querySelector('[data-effect="scout"]')?.textContent ?? ''
    expect(fallen).toContain(words.roles.inherit)
    expect(fallen).toContain(words.roles.fallbacks.slot_not_found)
    const template = document.querySelector('[data-preset="translator"]')?.textContent ?? ''
    expect(template).toContain('Translator')
    expect(template).toContain(words.roles.invalid)
    expect(document.querySelector('[data-preset="scout"]')?.textContent).toContain(words.roles.advice.scout)
  })

  it('sets what was chosen and shows what the host then says; a choice that is refused goes back', async () => {
    api.getRoleModels.mockResolvedValue(roles())
    await mount(RoleModelsTab)
    const next = roles()
    next.presets[0].setting = 'slot_own'
    api.setRoleModel.mockResolvedValueOnce(next)
    await choose(document.querySelector('[data-setting="boss"]'), 'slot_own')
    expect(api.setRoleModel).toHaveBeenCalledWith('boss', 'slot_own')
    expect((document.querySelector('[data-setting="boss"]') as HTMLSelectElement).value).toBe('slot_own')
    // Its default is no slot: asked for as nothing.
    api.setRoleModel.mockResolvedValueOnce(roles())
    await choose(document.querySelector('[data-setting="boss"]'), '')
    expect(api.setRoleModel).toHaveBeenLastCalledWith('boss', null)

    api.setRoleModel.mockRejectedValueOnce(refused('capability_missing'))
    await choose(document.querySelector('[data-setting="boss"]'), 'slot_premium')
    expect(byTest('roles-error')?.textContent).toContain(words.errors.capability_missing)
    expect((document.querySelector('[data-setting="boss"]') as HTMLSelectElement).value).toBe('')
    // What is shown was read anew: the slots may be others by now.
    expect(api.getRoleModels).toHaveBeenCalledTimes(2)
  })

  it('says so when the slots cannot be read, and names a reason it has no words for by its code', async () => {
    const view = roles({ slots: [], slots_error: 'model_config_unreadable' })
    view.presets[0].in_effect.fallback = 'something_new'
    api.getRoleModels.mockResolvedValue(view)
    await mount(RoleModelsTab)
    expect(byTest('roles-slots-error')?.textContent).toContain(words.roles.fallbacks.model_config_unreadable)
    expect(options('boss').map(option => option[0])).toEqual([''])
    expect(document.querySelector('[data-effect="boss"]')?.textContent).toContain('something_new')
  })
})

const limit = (over: Partial<UsageLimit> = {}): UsageLimit => ({
  tokens: 5_000_000, set: null, default: 5_000_000, min: 500_000, max: 50_000_000, ...over,
})

describe('the usage limit', () => {
  it('is typed in the unit of the language and saved in tokens', async () => {
    api.getUsageLimit.mockResolvedValue(limit())
    await mount(UsageLimitCard)
    const amount = byTest('limit-amount') as HTMLInputElement
    // English counts in thousands: five million tokens is five thousand of them.
    expect(amount.value).toBe('5000')
    expect((byTest('limit-save') as HTMLButtonElement).disabled).toBe(true)
    expect((byTest('limit-default') as HTMLButtonElement).disabled).toBe(true)
    expect(byTest('usage-limit')?.textContent).toContain(
      words.limit.range.replace('{min}', '500').replace('{max}', '50000').replace('{default}', '5000'),
    )
    api.setUsageLimit.mockResolvedValueOnce(limit({ tokens: 800_000, set: 800_000 }))
    await type('limit-amount', '800')
    await click(byTest('limit-save'))
    expect(api.setUsageLimit).toHaveBeenCalledWith(800_000)
    expect(byTest('limit-note')?.textContent).toBe(words.limit.saved)
    expect((byTest('limit-default') as HTMLButtonElement).disabled).toBe(false)

    api.setUsageLimit.mockResolvedValueOnce(limit())
    await click(byTest('limit-default'))
    expect(api.setUsageLimit).toHaveBeenLastCalledWith(null)
    expect(amount.value).toBe('5000')
  })

  it('keeps the tokens that were meant when the language, and with it the unit, changes', async () => {
    api.getUsageLimit.mockResolvedValue(limit())
    await mount(UsageLimitCard)
    const amount = byTest('limit-amount') as HTMLInputElement
    expect(amount.value).toBe('5000')
    // Chinese counts in ten thousands: the same five million tokens are five hundred of those.
    language!.value = 'zh-CN'
    await flush()
    expect(amount.value).toBe('500')
    expect((byTest('limit-save') as HTMLButtonElement).disabled).toBe(true)
    // What was typed and not saved yet is turned as well, and saved as the tokens it meant.
    await type('limit-amount', '80')
    language!.value = 'en-US'
    await flush()
    expect(amount.value).toBe('800')
    api.setUsageLimit.mockResolvedValueOnce(limit({ tokens: 800_000, set: 800_000 }))
    await click(byTest('limit-save'))
    expect(api.setUsageLimit).toHaveBeenCalledWith(800_000)
  })

  it('keeps them to the token: a number that does not come out even in the other unit is not rounded', async () => {
    // Set to a number that is not a round one in either unit: shown as it is, and not as changed.
    api.getUsageLimit.mockResolvedValue(limit({ tokens: 5_000_001, set: 5_000_001 }))
    await mount(UsageLimitCard)
    const amount = byTest('limit-amount') as HTMLInputElement
    expect(amount.value).toBe('5000.001')
    expect((byTest('limit-save') as HTMLButtonElement).disabled).toBe(true)
    language!.value = 'zh-CN'
    await flush()
    expect(amount.value).toBe('500.0001')
    expect((byTest('limit-save') as HTMLButtonElement).disabled).toBe(true)
    // Typed in one unit and saved in the other: the tokens that were typed.
    await type('limit-amount', '123.4567')
    language!.value = 'en-US'
    await flush()
    expect(amount.value).toBe('1234.567')
    api.setUsageLimit.mockResolvedValueOnce(limit({ tokens: 1_234_567, set: 1_234_567 }))
    await click(byTest('limit-save'))
    expect(api.setUsageLimit).toHaveBeenCalledWith(1_234_567)
  })

  it('is saved once however often Enter is pressed while it is being saved', async () => {
    api.getUsageLimit.mockResolvedValue(limit())
    await mount(UsageLimitCard)
    let saved: (now: UsageLimit) => void = () => {}
    api.setUsageLimit.mockImplementation(() => new Promise(resolve => { saved = resolve }))
    await type('limit-amount', '800')
    enter('limit-amount')
    enter('limit-amount')
    await flush()
    expect(api.setUsageLimit).toHaveBeenCalledTimes(1)
    saved(limit({ tokens: 800_000, set: 800_000 }))
    await flush()
    // Saved: Enter saves again.
    await type('limit-amount', '900')
    enter('limit-amount')
    await flush()
    expect(api.setUsageLimit).toHaveBeenCalledTimes(2)
  })

  it('says why a number was not taken and keeps what was typed', async () => {
    api.getUsageLimit.mockResolvedValue(limit())
    await mount(UsageLimitCard)
    api.setUsageLimit.mockRejectedValueOnce(refused('out_of_range'))
    await type('limit-amount', '3')
    await click(byTest('limit-save'))
    expect(api.setUsageLimit).toHaveBeenCalledWith(3000)
    expect(byTest('limit-error')?.textContent).toContain(words.errors.out_of_range)
    expect((byTest('limit-amount') as HTMLInputElement).value).toBe('3')
    // Nothing typed is no number to save.
    await type('limit-amount', '')
    expect((byTest('limit-save') as HTMLButtonElement).disabled).toBe(true)
  })
})

const mcp = (over: Partial<McpView> = {}): McpView => ({
  version: 4, adapter: 'running', plugins_on: true, category: { mode: 'auto', auto: 'first_ask' }, unreadable: false,
  servers: [
    { server: 'notes', mode: 'deny', effective: 'deny', tools: [{ id: 'mcp:notes:search', name: 'search', description: 'Search the notes' }] },
    { server: 'calendar', mode: 'auto', effective: 'first_ask', tools: [] },
  ],
  ...over,
})

describe('the MCP tools', () => {
  it('lists the servers with their tools and how each is set, and sets one on its own', async () => {
    api.getMcp.mockResolvedValue(mcp())
    await mount(McpTools)
    const notes = document.querySelector('[data-mcp="notes"]')
    expect(notes?.textContent).toContain('search')
    expect(notes?.textContent).toContain(words.mcp.denied)
    expect((document.querySelector('[data-server="notes"]') as HTMLSelectElement).value).toBe('deny')
    const calendar = document.querySelector('[data-mcp="calendar"]')?.textContent ?? ''
    expect(calendar).toContain(words.mcp.follows.replace('{mode}', words.runtime.permissions.modes.first_ask))
    expect(byTest('mcp-notice')).toBeNull()

    api.setMcpPermission.mockResolvedValueOnce(mcp({ version: 5 }))
    await choose(document.querySelector('[data-server="calendar"]'), 'allow')
    expect(api.setMcpPermission).toHaveBeenCalledWith('calendar', 'allow', 4)
    // All of them at once is no server's name; the version is the one just read.
    api.setMcpPermission.mockResolvedValueOnce(mcp({ version: 6 }))
    await choose(document.querySelector('[data-server=""]'), 'always_ask')
    expect(api.setMcpPermission).toHaveBeenLastCalledWith(null, 'always_ask', 5)
    await click(byTest('mcp-configure'))
    expect(push).toHaveBeenCalledWith('/plugins/mcp_adapter')
  })

  it('reads anew when a change was made on an older view, and says what went wrong', async () => {
    api.getMcp.mockResolvedValue(mcp())
    await mount(McpTools)
    api.setMcpPermission.mockRejectedValueOnce(refused('stale', 409))
    await choose(document.querySelector('[data-server="notes"]'), 'allow')
    expect(byTest('mcp-error')?.textContent).toContain(words.errors.stale)
    expect(api.getMcp).toHaveBeenCalledTimes(2)
    expect((document.querySelector('[data-server="notes"]') as HTMLSelectElement).value).toBe('deny')
  })

  it('shows a server as it is still set when the change did not reach the service at all', async () => {
    api.getMcp.mockResolvedValue(mcp())
    await mount(McpTools)
    const notes = () => document.querySelector('[data-server="notes"]') as HTMLSelectElement
    // No answer with a code: the service is not there. Read anew, which works again by then.
    api.setMcpPermission.mockRejectedValueOnce(new Error('Network Error'))
    await choose(notes(), 'allow')
    expect(api.getMcp).toHaveBeenCalledTimes(2)
    expect(notes().value).toBe('deny')
    expect(document.querySelector('.el-alert')).toBeTruthy()
    // Nor can it be read anew: what is shown is still what was read last, not what was picked.
    api.setMcpPermission.mockRejectedValueOnce(new Error('Network Error'))
    api.getMcp.mockRejectedValueOnce(new Error('Network Error'))
    await choose(notes(), 'allow')
    expect(notes().value).toBe('deny')
    expect(document.querySelector('.el-alert')).toBeTruthy()
  })

  it('says why no tool is on offer, and lets nothing be set while the permission file cannot be read', async () => {
    api.getMcp.mockResolvedValueOnce(mcp({ adapter: 'missing', servers: [] }))
    await mount(McpTools)
    expect(byTest('mcp-notice')?.textContent).toContain(words.mcp.adapterMissing)
    expect(byTest('mcp-empty')).toBeTruthy()
    cleanup?.()
    api.getMcp.mockResolvedValueOnce(mcp({ adapter: 'stopped' }))
    await mount(McpTools)
    expect(byTest('mcp-notice')?.textContent).toContain(words.mcp.adapterStopped)
    cleanup?.()
    // Switched off outweighs the rest: that is why nothing is there.
    api.getMcp.mockResolvedValueOnce(mcp({ plugins_on: false, adapter: 'missing', unreadable: true }))
    await mount(McpTools)
    expect(byTest('mcp-notice')?.textContent).toContain(words.mcp.pluginsOff)
    expect(byTest('mcp-unreadable')).toBeTruthy()
    expect((document.querySelector('[data-server="notes"]') as HTMLSelectElement).disabled).toBe(true)
  })
})

const row = (over: Partial<CommissionRow> = {}): CommissionRow => ({
  id: 'c1', title: 'Move the blog', status: 'working', reason: '', result: '', source: 'dispatch', route: 'task_agent', project: 'Blog',
  lanlan_name: 'Lan', parent_id: null, session_id: 's1', session: { name: 'Blog', kind: 'project', preset: 'boss', title: '' },
  runtime: 'builtin', plugin_id: null, entry_id: null, created_at: 1_700_000_000, started_at: 1_700_000_010, ended_at: null,
  updated_at: 1_700_000_100, usage: { requests: 9, tokens: 310_000 }, ask: null, ...over,
})
const listed = () => [
  row({ id: 'c4', title: 'Translate the readme', status: 'done', project: null, route: 'plugin', session_id: null, session: null,
    plugin_id: 'translator', started_at: 1_700_000_300, ended_at: 1_700_000_345, result: 'translated', usage: { requests: 0, tokens: 0 } }),
  row({ id: 'c3', title: 'Check the build', status: 'waiting_user', parent_id: 'c1', session_id: 's3',
    session: { name: 'look', kind: 'child', preset: 'inspector', title: 'Build checker' }, ask: permission() }),
  row({ id: 'c2', title: 'Find the templates', status: 'done', parent_id: 'c1', session_id: 's2',
    session: { name: 'find', kind: 'child', preset: 'scout', title: '' }, ended_at: 1_700_000_200, result: 'in themes/old' }),
  row(),
]

describe('the commissions', () => {
  beforeEach(() => {
    api.listCommissions.mockResolvedValue({ commissions: listed(), more: false })
    api.listProjects.mockResolvedValue({ projects: [{ name: 'Blog', status: 'active' }, { name: 'Old', status: 'archived' }] })
    api.getCommissionUsage.mockResolvedValue({
      commission: { requests: 9, input: 13_000, output: 4_000, cache_read: 297_000, cache_write: 0, tokens: 314_000 },
      used: 314_000, limit: 5_000_000, session: null, window: null,
    })
  })

  it('lists them newest first with those a familiar started under the one they were started for', async () => {
    await mount(Commissions)
    expect(api.listCommissions).toHaveBeenCalledWith({ status: undefined, loose: false, project: null, days: 7 })
    const rows = [...document.querySelectorAll('tr.row')]
    expect(rows.map(item => item.getAttribute('data-commission'))).toEqual(['c4', 'c1', 'c2', 'c3'])
    expect(rows.map(item => item.classList.contains('child'))).toEqual([false, false, true, true])
    // Who does it: the title a familiar earned, its name, or the plugin.
    expect(rows[0].textContent).toContain(words.commissions.byPlugin.replace('{plugin}', 'translator'))
    expect(rows[0].textContent).toContain(words.home.loose)
    expect(rows[0].textContent).toContain('45s')
    expect(rows[3].textContent).toContain('Build checker')
    expect(rows[1].textContent).toContain('Blog')
    expect(rows[2].textContent).toContain(words.home.presets.scout)
    expect(rows[2].textContent).not.toContain('find')
    expect(rows[3].textContent).toContain(words.commissions.statuses.waiting_user)
    expect(rows[1].textContent).toContain('310k')
    // Only the projects that are not put away are offered to keep to.
    expect([...byTest('commissions-project')!.querySelectorAll('option')].map(option => option.value)).toEqual(['', 'loose', 'p:Blog'])
  })

  it('names a member of a project by the project, then by its title or its kind', async () => {
    const member = (id: string, name: string, preset: string, title = '') =>
      row({ id, session_id: id, session: { name, kind: 'line', preset, title } })
    api.listCommissions.mockResolvedValue({ commissions: [
      member('m1', 'Blog', 'general'), member('m2', 'Blog/scout', 'scout'), member('m3', 'Blog', 'general', 'Keeper'),
      row({ id: 'm4', project: null, session_id: 'm4', session: { name: 'Garden', kind: 'line', preset: 'general', title: '' } }),
      row({ id: 'm5', session_id: 'm5' }),
    ], more: false })
    await mount(Commissions)
    const doer = (id: string) => document.querySelector(`tr.row[data-commission="${id}"]`)!.querySelectorAll('td')[2].textContent
    expect(['m1', 'm2', 'm3', 'm4', 'm5'].map(doer)).toEqual([
      `Blog · ${words.home.presets.general}`, `Blog · ${words.home.presets.scout}`, 'Blog · Keeper', `Garden · ${words.home.presets.general}`, 'Blog',
    ])
  })

  it('keeps to what is chosen', async () => {
    await mount(Commissions)
    await choose(byTest('commissions-status'), 'waiting_user')
    expect(api.listCommissions).toHaveBeenLastCalledWith({ status: ['waiting_user'], loose: false, project: null, days: 7 })
    await choose(byTest('commissions-project'), 'p:Blog')
    expect(api.listCommissions).toHaveBeenLastCalledWith({ status: ['waiting_user'], loose: false, project: 'Blog', days: 7 })
    await choose(byTest('commissions-project'), 'loose')
    await choose(byTest('commissions-days'), '0')
    expect(api.listCommissions).toHaveBeenLastCalledWith({ status: ['waiting_user'], loose: true, project: null, days: 0 })
  })

  it('opens one to what it used and what it asks, whole, and answers it from there', async () => {
    api.answerCommission.mockResolvedValue({ success: true })
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c3"]'))
    expect(api.getCommissionUsage).toHaveBeenCalledWith('c3')
    const detail = document.querySelector('[data-detail="c3"]')
    expect(byTest('commission-usage')?.textContent).toContain('310k')
    expect(byTest('ask-whole')?.textContent).toBe(LONG)
    // It waits: there is nobody at work to pass a sentence to.
    expect(byTest('commission-say')).toBeNull()
    await click(detail?.querySelector('[data-choice="once"]'))
    expect(api.answerCommission).toHaveBeenCalledWith('c3', { choice: 'once', text: undefined, question_id: 'q1' })
    await click(byTest('commission-familiar'))
    expect(push).toHaveBeenCalledWith({ path: '/familiars/space', query: { project: 'Blog', familiar: 's3' } })
    // Pressed again it closes.
    await click(document.querySelector('[data-commission="c3"]'))
    expect(document.querySelector('[data-detail="c3"]')).toBeNull()
  })

  it('passes a sentence to one at work, stops it, and offers neither for one that is over', async () => {
    api.tellCommission.mockResolvedValue({ success: true })
    api.stopCommission.mockResolvedValue({ status: 'cancelled' })
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c1"]'))
    await type('commission-say', ' the dark theme too ')
    await click(byTest('commission-send'))
    expect(api.tellCommission).toHaveBeenCalledWith('c1', 'the dark theme too')
    await click(byTest('commission-stop'))
    expect(api.stopCommission).toHaveBeenCalledWith('c1')

    await click(document.querySelector('[data-commission="c2"]'))
    expect(document.querySelector('[data-detail="c2"]')?.textContent).toContain('in themes/old')
    expect(byTest('commission-say')).toBeNull()
    expect(byTest('commission-stop')).toBeNull()
  })

  it('is opened with the keys as well: its title is a button that says whether it is open', async () => {
    await mount(Commissions)
    const title = document.querySelector('[data-commission="c2"] button.title') as HTMLButtonElement
    expect(title.getAttribute('aria-expanded')).toBe('false')
    await click(title)
    expect(title.getAttribute('aria-expanded')).toBe('true')
    const detail = document.querySelector('[data-detail="c2"]')
    expect(detail?.id).toBeTruthy()
    expect(title.getAttribute('aria-controls')).toBe(detail?.id)
    await click(title)
    expect(document.querySelector('[data-detail="c2"]')).toBeNull()
  })

  it('tells one at work a thing once, and takes away only what was sent', async () => {
    api.listCommissions.mockResolvedValue({ commissions: [...listed(), row({ id: 'c5', title: 'Write the notes' })], more: false })
    let taken: (answer: unknown) => void = () => {}
    api.tellCommission.mockImplementation(() => new Promise(resolve => { taken = resolve }))
    const typed = () => (document.querySelector('[data-testid="commission-say"] input, input[data-testid="commission-say"]') as HTMLInputElement).value
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c1"]'))
    await type('commission-say', 'the dark theme too')
    enter('commission-say')
    enter('commission-say')
    await flush()
    expect(api.tellCommission).toHaveBeenCalledTimes(1)
    // More was typed before it was taken: that stays.
    await type('commission-say', 'and the light one')
    taken({ success: true })
    await flush()
    expect(typed()).toBe('and the light one')
    // What was sent and not changed since is taken away.
    enter('commission-say')
    await flush()
    taken({ success: true })
    await flush()
    expect(api.tellCommission).toHaveBeenCalledTimes(2)
    expect(typed()).toBe('')
    // Another one was opened meanwhile and the same words typed for it: they are that one's, and stay.
    await type('commission-say', 'same words')
    enter('commission-say')
    await flush()
    await click(document.querySelector('[data-commission="c5"]'))
    await type('commission-say', 'same words')
    taken({ success: true })
    await flush()
    expect(typed()).toBe('same words')
    // Sent, another one opened, back to the one it was sent for and the same words typed again
    // before the answer came: they are a new draft, and stay.
    enter('commission-say')
    await flush()
    expect(api.tellCommission).toHaveBeenLastCalledWith('c5', 'same words')
    await click(document.querySelector('[data-commission="c1"]'))
    await click(document.querySelector('[data-commission="c5"]'))
    expect(document.querySelector('[data-detail="c5"]')).toBeTruthy()
    await type('commission-say', 'same words')
    taken({ success: true })
    await flush()
    expect(typed()).toBe('same words')
  })

  it('reads what the open one used again when the list says it used more or stands otherwise', async () => {
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c1"]'))
    expect(api.getCommissionUsage).toHaveBeenCalledTimes(1)
    const again = async (over: Partial<CommissionRow>) => {
      api.listCommissions.mockResolvedValue({ commissions: listed().map(item => (item.id === 'c1' ? { ...item, ...over } : item)), more: false })
      document.dispatchEvent(new Event('visibilitychange'))
      await flush()
    }
    // Read again with nothing new about it: what it used is not asked for again.
    await again({})
    expect(api.getCommissionUsage).toHaveBeenCalledTimes(1)
    // It used more: asked for again, and shown.
    api.getCommissionUsage.mockResolvedValueOnce({
      commission: { requests: 12, input: 20_000, output: 6_000, cache_read: 400_000, cache_write: 0, tokens: 426_000 },
      used: 426_000, limit: 5_000_000, session: null, window: null,
    })
    await again({ usage: { requests: 12, tokens: 426_000 } })
    expect(api.getCommissionUsage).toHaveBeenCalledTimes(2)
    expect(byTest('commission-usage')?.textContent).toContain('12')
    // It ended, having used no more: asked for once more, as how it stands changed.
    await again({ usage: { requests: 12, tokens: 426_000 }, status: 'done' })
    expect(api.getCommissionUsage).toHaveBeenCalledTimes(3)
    expect(api.getCommissionUsage).toHaveBeenLastCalledWith('c1')
  })

  it('shows what the open one used as it was read last, whichever read answers first', async () => {
    const used = (requests: number, tokens: number) => ({
      commission: { requests, input: tokens, output: 0, cache_read: 0, cache_write: 0, tokens }, used: tokens, limit: 5_000_000, session: null, window: null,
    })
    let early: (answer: unknown) => void = () => {}
    api.getCommissionUsage.mockReset()
    api.getCommissionUsage.mockReturnValueOnce(new Promise(resolve => { early = resolve }))
    api.getCommissionUsage.mockResolvedValueOnce(used(31, 900_000))
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c1"]'))
    // It used more before the first read answered: read again, and that one answers first.
    api.listCommissions.mockResolvedValue({
      commissions: listed().map(item => (item.id === 'c1' ? { ...item, usage: { requests: 31, tokens: 900_000 } } : item)), more: false,
    })
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(byTest('commission-usage')?.textContent).toContain('900k')
    early(used(9, 314_000))
    await flush()
    expect(byTest('commission-usage')?.textContent).toContain('900k')
    expect(byTest('commission-usage')?.textContent).not.toContain('314k')
    // Closed while a read is out: what comes back is not shown for the one opened next.
    let late: (answer: unknown) => void = () => {}
    api.getCommissionUsage.mockReturnValueOnce(new Promise(resolve => { late = resolve }))
    api.getCommissionUsage.mockResolvedValueOnce(used(2, 2_000))
    await click(document.querySelector('[data-commission="c2"]'))
    await click(document.querySelector('[data-commission="c3"]'))
    expect(byTest('commission-usage')?.textContent).toContain('2 ')
    late(used(77, 7_000_000))
    await flush()
    expect(byTest('commission-usage')?.textContent).not.toContain('77')
  })

  it('asks again for what the open one used, when the list is read next, after that could not be read', async () => {
    api.getCommissionUsage.mockReset()
    api.getCommissionUsage.mockRejectedValueOnce(new Error('Network Error'))
    api.getCommissionUsage.mockResolvedValue({
      commission: { requests: 9, input: 13_000, output: 4_000, cache_read: 297_000, cache_write: 0, tokens: 314_000 },
      used: 314_000, limit: 5_000_000, session: null, window: null,
    })
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c1"]'))
    expect(byTest('commission-usage')).toBeNull()
    // The list is read again with nothing new about it: what could not be read is asked for again.
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(api.getCommissionUsage).toHaveBeenCalledTimes(2)
    expect(byTest('commission-usage')?.textContent).toContain('310k')
    // Read by now: the next reading of the list asks for nothing.
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(api.getCommissionUsage).toHaveBeenCalledTimes(2)
  })

  it('says why one that is stuck stopped, and still offers to call it off', async () => {
    api.listCommissions.mockResolvedValue({ commissions: [
      row({ id: 'c7', status: 'blocked', reason: 'the build never finished' }),
      row({ id: 'c8', status: 'working', reason: 'left from an earlier try' }),
    ], more: false })
    await mount(Commissions)
    await click(document.querySelector('[data-commission="c7"]'))
    expect(document.querySelector('[data-detail="c7"]')?.textContent).toContain('the build never finished')
    expect(byTest('commission-stop')).toBeTruthy()
    // One at work says nothing of why an earlier try of it stopped.
    await click(document.querySelector('[data-commission="c8"]'))
    expect(document.querySelector('[data-detail="c8"]')?.textContent).not.toContain('left from an earlier try')
  })

  it('says so when there is nothing, when more is left out, and when the list could not be read', async () => {
    api.listCommissions.mockResolvedValueOnce({ commissions: [], more: false })
    await mount(Commissions)
    expect(byTest('commissions-empty')).toBeTruthy()
    cleanup?.()
    api.listCommissions.mockResolvedValueOnce({ commissions: listed(), more: true })
    await mount(Commissions)
    expect(byTest('commissions-more')).toBeTruthy()
    cleanup?.()
    api.listCommissions.mockRejectedValueOnce(refused('bad_status'))
    await mount(Commissions)
    expect(byTest('commissions-error')?.textContent).toContain(words.errors.bad_status)
  })
})

describe('what happened in a project', () => {
  it('is not read a second time beside a read that is under way when the page is looked at again', async () => {
    api.getTimelineStart.mockResolvedValue({ after: '', latest: 'e2.m0' })
    api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [], loose: [] })
    let answer: (page: unknown) => void = () => {}
    api.getTimeline.mockReturnValue(new Promise(resolve => { answer = resolve }))
    await mount(ActivityPane, { project: 'Blog' })
    expect(api.getTimeline).toHaveBeenCalledTimes(1)
    // Looked away and back while the read is still out: the one read is waited for.
    document.dispatchEvent(new Event('visibilitychange'))
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(api.getTimeline).toHaveBeenCalledTimes(1)
    answer({ next: 'e1.m0', more: false, latest: 'e1.m0', entries: [
      { cursor: 'e1.m0', at: 10, kind: 'started', lanlan_name: 'Lan', project: 'Blog', session_id: 's1', commission_id: 'c1', peer: null, text: '', detail: {} },
    ] })
    await flush()
    // Each thing that happened is listed once.
    expect(document.querySelectorAll('.row').length).toBe(1)
  })

  it('asks again where to begin after that could not be read, and never reads everything instead', async () => {
    vi.useFakeTimers()
    api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [], loose: [] })
    api.getTimelineStart.mockRejectedValueOnce(refused('unavailable', 503))
    api.getTimelineStart.mockResolvedValue({ after: 'e50.m2', latest: 'e99.m9' })
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e99.m9', more: false, latest: 'e99.m9' })
    await mount(ActivityPane, { project: null })
    expect(api.getTimeline).not.toHaveBeenCalled()
    expect(document.querySelector('.el-alert')).toBeTruthy()
    await vi.advanceTimersByTimeAsync(15_100)
    await flush()
    expect(api.getTimelineStart).toHaveBeenCalledTimes(2)
    expect(api.getTimeline).toHaveBeenCalledWith({ project: null, after: 'e50.m2', limit: 1000 })
    expect(document.querySelector('.el-alert')).toBeNull()
    // Known now: what came since is asked for from where the last read stopped, not from there again.
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(api.getTimelineStart).toHaveBeenCalledTimes(2)
    expect(api.getTimeline).toHaveBeenLastCalledWith({ project: null, after: 'e99.m9', limit: 1000 })
  })

  it('reads no further page while nobody looks, and reads on from there when they look again', async () => {
    let hidden = false
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    try {
      api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [], loose: [] })
      let answer: (page: unknown) => void = () => {}
      api.getTimeline.mockResolvedValue({ entries: [], next: 'e9.m0', more: false, latest: 'e9.m0' })
      api.getTimeline.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
      await mount(ActivityPane, { project: 'Blog' })
      hidden = true
      answer({ next: 'e1.m0', more: true, latest: 'e9.m0', entries: [
        { cursor: 'e1.m0', at: 10, kind: 'started', lanlan_name: 'Lan', project: 'Blog', session_id: 's1', commission_id: 'c1', peer: null, text: '', detail: {} },
      ] })
      await flush()
      // What came is shown; the page after it is not asked for.
      expect(api.getTimeline).toHaveBeenCalledTimes(1)
      expect(document.querySelectorAll('.row').length).toBe(1)
      hidden = false
      document.dispatchEvent(new Event('visibilitychange'))
      await flush()
      expect(api.getTimeline).toHaveBeenCalledTimes(2)
      expect(api.getTimeline).toHaveBeenLastCalledWith({ project: 'Blog', after: 'e1.m0', limit: 1000 })
    } finally {
      delete (document as unknown as { hidden?: boolean }).hidden
    }
  })

  it('reads the names again with every new entry, also once as many are held as are kept', async () => {
    vi.useFakeTimers()
    const step = (n: number) => ({
      cursor: `e${n}.m0`, at: n, kind: 'reported', lanlan_name: 'Lan', project: 'Blog', session_id: 's1', commission_id: 'c1', peer: null, text: `step ${n}`, detail: {},
    })
    api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [], loose: [] })
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e9.m0', more: false, latest: 'e9.m0' })
    // As many as are kept, then one more: the oldest is let go and their number stays.
    api.getTimeline.mockResolvedValueOnce({ entries: Array.from({ length: 3000 }, (_, n) => step(n)), next: 'e9.m0', more: false, latest: 'e9.m0' })
    await mount(ActivityPane, { project: 'Blog' })
    // All of it is held, from its beginning: nothing more to read, nothing to say.
    expect(byTest('activity-whole')).toBeNull()
    expect(byTest('activity-capped')).toBeNull()
    const read = api.getProjectBoard.mock.calls.length
    api.getTimeline.mockResolvedValueOnce({ entries: [step(3000)], next: 'e9.m0', more: false, latest: 'e9.m0' })
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(api.getProjectBoard.mock.calls.length).toBe(read + 1)
    // The oldest was let go: reading from the beginning would end here again, so it is said and not offered.
    expect(byTest('activity-whole')).toBeNull()
    expect(byTest('activity-capped')?.textContent).toBe(words.activity.onlyLatest.replace('{count}', '3000'))
    // Nothing new: not read again.
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(api.getProjectBoard.mock.calls.length).toBe(read + 1)
  })

  it('is read for that project and told with the names the board has', async () => {
    api.getTimelineStart.mockResolvedValue({ after: '', latest: 'e2.m0' })
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e2.m0', more: false, latest: 'e2.m0' })
    api.getTimeline.mockResolvedValueOnce({ next: 'e2.m0', more: false, latest: 'e2.m0', entries: [
      { cursor: 'e1.m0', at: 10, kind: 'started', lanlan_name: 'Lan', project: 'Blog', session_id: 's1', commission_id: 'c1', peer: null, text: '', detail: {} },
      { cursor: 'e2.m0', at: 20, kind: 'child_started', lanlan_name: 'Lan', project: 'Blog', session_id: 's1', commission_id: 'c1',
        peer: { type: 'session', session_id: 'gone', commission_id: 'c2' }, text: 'Find the templates', detail: {} },
    ] })
    api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [{ name: 'Blog', members: [{ id: 's1', name: 'Blog lead', preset: 'boss', label: '' }] }], loose: [] })
    const { state } = await mount(ActivityPane, { project: 'Blog' })
    expect(api.getTimeline).toHaveBeenCalledWith({ project: 'Blog', after: undefined, limit: 1000 })
    // Of one project all of it is read, from its beginning: where the last stretch begins is not asked.
    expect(api.getTimelineStart).not.toHaveBeenCalled()
    expect(byTest('activity-whole')).toBeNull()
    const lines = [...document.querySelectorAll('.row')].map(item => item.textContent)
    expect(lines[0]).toContain('Blog lead started working')
    // One that is not on the board any more is named as the entries name it.
    expect(lines[1]).toContain('Blog lead sent out Find the templates')

    // Of all the familiars together the last stretch is read, and the rest is offered.
    api.getTimelineStart.mockResolvedValue({ after: 'e50.m2', latest: 'e99.m9' })
    api.getTimeline.mockResolvedValueOnce({ next: 'e99.m9', more: false, latest: 'e99.m9', entries: [
      { cursor: 'e99.m9', at: 30, kind: 'started', lanlan_name: 'Lan', project: null, session_id: 's9', commission_id: 'c9', peer: null, text: '', detail: {} },
    ] })
    state.props = { project: null }
    await flush()
    expect(api.getTimelineStart).toHaveBeenCalledTimes(1)
    expect(api.getTimeline).toHaveBeenLastCalledWith({ project: null, after: 'e50.m2', limit: 1000 })
    expect(byTest('activity-whole')).toBeTruthy()
  })
})
