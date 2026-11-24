// @vitest-environment happy-dom
/**
 * Several familiars at work in one project: what a familiar changed in a git worktree of its own and
 * what the user decides on it, one more familiar or a leader for a project, the landing settings,
 * looking for words in what was said, and whose an archived familiar is.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { Board, BoardCard, SpaceProject, Worktree } from '@/api/console'
import FamiliarSpace from '@/views/FamiliarSpace.vue'
import LandingCard from '../LandingCard.vue'
import CrewTools from './CrewTools.vue'
import FamiliarsPane from './FamiliarsPane.vue'
import ProjectTrees from './ProjectTrees.vue'
import TranscriptPane from './TranscriptPane.vue'
import TreeActions from './TreeActions.vue'

const api = vi.hoisted(() => ({
  listProjects: vi.fn(), setProjectWorkdir: vi.fn(), pickDirectory: vi.fn(), readRules: vi.fn(), writeRules: vi.fn(),
  getProjectBoard: vi.fn(), writeCard: vi.fn(), tellCommission: vi.fn(), answerCommission: vi.fn(), stopCommission: vi.fn(),
  getTranscript: vi.fn(), searchTranscript: vi.fn(), getSessionUsage: vi.fn(),
  listWorktrees: vi.fn(), landWorktree: vi.fn(), discardWorktree: vi.fn(), keepWorktree: vi.fn(), openWorktree: vi.fn(),
  getLanding: vi.fn(), setLanding: vi.fn(), getTemplates: vi.fn(), addMember: vi.fn(), giveLeader: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)

const route = reactive<{ query: Record<string, string | undefined> }>({ query: {} })
const replace = vi.fn((to: { query: Record<string, string | undefined> }) => {
  route.query = Object.fromEntries(Object.entries(to.query).filter(([, value]) => value !== undefined))
  return Promise.resolve()
})
vi.mock('vue-router', () => ({ useRoute: () => route, useRouter: () => ({ replace }) }))

const refusal = (code: string, status = 409, more: Record<string, unknown> = {}) =>
  Object.assign(new Error(code), { code, response: { status, data: { detail: { code, ...more } } } })
const tree = (over: Partial<Worktree> = {}): Worktree => ({
  id: 't1', path: 'D:/wt/t1', workdir: 'D:/wt/t1/app', state: 'unlanded', rounds: 3, checks: 0, conflicts: ['a.py', 'b.py'],
  reason: 'conflict', project_dir: 'D:/code/app', commission_id: 'c1', session_id: 's2', lanlan_name: 'Lan', project: 'App',
  created_at: 0, updated_at: 0, title: 'Add an export', session_name: 'App/worker', ...over,
})
const card = (over: Partial<BoardCard> = {}): BoardCard => ({
  id: 's1', name: 'App', kind: 'line', lanlan_name: 'Lan', runtime: 'builtin', workdir: 'D:/code/app', preset: 'general',
  avatar: 'general', label: '', description: '', title: '', duty: '', duty_by: '', doing: '', state: 'idle', leader: false,
  parent: null, model: null, commission: null, ...over,
})
const board = (members: BoardCard[]): Board => ({
  revision: `r${members.length}`,
  projects: [{ name: 'App', description: '', workdir: 'D:/code/app', archived: false, done_today: 0, members }],
  loose: [],
})
const project = (over: Partial<SpaceProject> = {}): SpaceProject => ({
  name: 'App', description: '', workdir: 'D:/code/app', status: 'active', created_at: 0, last_commission_at: 0,
  commissions: 3, archived_at: null, archive_year: null, directory: null, ...over,
})

let cleanup: (() => void) | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() } }
async function mount(component: Component, props: Record<string, unknown> = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const state = reactive({ props })
  const events: Array<[string, unknown[]]> = []
  const listeners = Object.fromEntries(['onChanged', 'onSelect'].map(name => [name, (...args: unknown[]) => events.push([name, args])]))
  const app = createApp({ render: () => h(component, { ...state.props, ...listeners }) })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words, common: { cancel: 'Cancel' } } } }))
  app.mount(container)
  cleanup = () => { app.unmount(); container.remove() }
  await flush()
  return { container, state, events }
}
const byTest = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null
async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy()
  ;(element as HTMLElement).click()
  await flush()
}
async function type(testId: string, value: string) {
  const input = document.querySelector(`[data-testid="${testId}"] input, input[data-testid="${testId}"]`) as HTMLInputElement
  expect(input).toBeTruthy()
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flush()
}

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.clearAllMocks()
  route.query = {}
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.unstubAllGlobals() })

describe('what a familiar changed in a worktree of its own', () => {
  it('that was not merged offers to try again, discard, open and see to it, and says why and what clashed', async () => {
    await mount(TreeActions, { tree: tree(), title: 'Add an export' })
    const box = byTest('tree-actions')!
    expect(box.textContent).toContain(words.trees.states.unlanded)
    expect(box.textContent).toContain(words.trees.reasons.conflict)
    expect(byTest('tree-conflicts')?.textContent).toContain('a.py, b.py')
    expect(byTest('tree-path')?.textContent).toContain('D:/wt/t1/app')
    expect(byTest('tree-land')?.textContent?.trim()).toBe(words.trees.retry)
    for (const id of ['tree-discard', 'tree-open', 'tree-keep']) expect(byTest(id)).toBeTruthy()
  })

  it('that an inspector did not pass is merged, not tried again: the host merges it without another inspection', async () => {
    await mount(TreeActions, { tree: tree({ reason: 'inspection', conflicts: [] }) })
    expect(byTest('tree-land')?.textContent?.trim()).toBe(words.trees.land)
    expect(byTest('tree-actions')?.textContent).toContain(words.trees.reasons.inspection)
  })

  it('that waits for the user to say so offers to merge it, and one the user sees to is not offered to them again', async () => {
    await mount(TreeActions, { tree: tree({ state: 'held', reason: 'ask', conflicts: [] }) })
    expect(byTest('tree-land')?.textContent?.trim()).toBe(words.trees.land)
    // Asked first is what the state says already.
    expect(byTest('tree-actions')?.textContent).not.toContain(words.trees.reasons.ask)
    expect(byTest('tree-conflicts')).toBeNull()
    cleanup?.()
    await mount(TreeActions, { tree: tree({ state: 'kept' }) })
    expect(byTest('tree-land')).toBeTruthy()
    expect(byTest('tree-keep')).toBeNull()
  })

  it('of which files were not carried over offers no merging again, and warns that discarding takes them too', async () => {
    api.discardWorktree.mockResolvedValue({ removed: true })
    await mount(TreeActions, { tree: tree({ reason: 'skipped', conflicts: ['big.bin'] }) })
    expect(byTest('tree-land')).toBeNull()
    expect(byTest('tree-keep')).toBeTruthy()
    expect(byTest('tree-actions')?.textContent).toContain(words.trees.reasons.skipped)
    expect(byTest('tree-conflicts')?.textContent).toContain(words.trees.skippedFiles.replace('{files}', 'big.bin'))
    await click(byTest('tree-discard'))
    expect(byTest('tree-discard-skipped')?.textContent).toBe(words.trees.discardSkipped.replace('{files}', 'big.bin'))
    cleanup?.()
    // Of another reason nothing of the kind is said.
    await mount(TreeActions, { tree: tree() })
    await click(byTest('tree-discard'))
    expect(byTest('tree-discard-skipped')).toBeNull()
  })

  it('that landed says what of it was outside the project folder and what was not carried over', async () => {
    api.landWorktree.mockResolvedValue({
      status: 'landed', files: [{ path: 'a.py' }], more: 0, conflicts: [], resolving: false, detail: '',
      outside: ['../tools/x.py'], skipped: ['big.bin'],
    })
    await mount(TreeActions, { tree: tree({ state: 'held', reason: 'ask' }) })
    await click(byTest('tree-land'))
    const said = byTest('tree-note')?.textContent ?? ''
    expect(said).toContain(words.trees.landed.replace('{count}', '1'))
    expect(said).toContain(words.trees.outsideNote.replace('{files}', '../tools/x.py'))
    expect(said).toContain(words.trees.skippedNote.replace('{files}', 'big.bin'))
  })

  it.each(['making', 'active', 'landing', 'resolving'])('at work (%s) offers nothing to decide', async state => {
    await mount(TreeActions, { tree: tree({ state, reason: '' }) })
    expect(byTest('tree-actions')?.textContent).toContain(words.trees.states[state as keyof typeof words.trees.states])
    expect(byTest('tree-land')).toBeNull()
    expect(byTest('tree-discard')).toBeNull()
  })

  it('is merged as asked, and what came of it is said', async () => {
    api.landWorktree.mockResolvedValueOnce({ status: 'landed', files: [{ path: 'a.py' }, { path: 'b.py' }], more: 3, conflicts: [], resolving: false, detail: '' })
    const { events } = await mount(TreeActions, { tree: tree() })
    await click(byTest('tree-land'))
    expect(api.landWorktree).toHaveBeenCalledWith('t1')
    expect(byTest('tree-note')?.textContent).toBe(words.trees.landed.replace('{count}', '5'))
    expect(events.map(([name]) => name)).toEqual(['onChanged'])
    api.landWorktree.mockResolvedValueOnce({ status: 'conflict', files: [], more: 0, conflicts: ['a.py'], resolving: true, detail: '' })
    await click(byTest('tree-land'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.resolving)
    api.landWorktree.mockResolvedValueOnce({ status: 'conflict', files: [], more: 0, conflicts: ['a.py'], resolving: false, detail: '' })
    await click(byTest('tree-land'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.stillClashes)
    api.landWorktree.mockResolvedValueOnce({ status: 'failed', files: [], more: 0, conflicts: [], resolving: false, detail: 'git broke', kept_copies: ['D:/kept'] })
    await click(byTest('tree-land'))
    expect(byTest('tree-note')?.textContent).toContain('git broke')
    expect(byTest('tree-note')?.textContent).toContain('D:/kept')
    api.landWorktree.mockResolvedValueOnce({ status: 'unchanged', files: [], more: 0, conflicts: [], resolving: false, detail: '' })
    await click(byTest('tree-land'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.unchanged)
  })

  it('is discarded only once that is confirmed', async () => {
    api.discardWorktree.mockResolvedValue({ removed: true })
    await mount(TreeActions, { tree: tree() })
    await click(byTest('tree-discard'))
    expect(api.discardWorktree).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain(words.trees.discardTitle)
    await click(byTest('tree-discard-confirm'))
    expect(api.discardWorktree).toHaveBeenCalledWith('t1')
    expect(byTest('tree-note')?.textContent).toBe(words.trees.discarded)
    api.discardWorktree.mockResolvedValue({ removed: false })
    await click(byTest('tree-discard'))
    await click(byTest('tree-discard-confirm'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.discardedLater)
  })

  it('is kept for the user, opened in the file manager, and a refusal is said in words and read again', async () => {
    api.keepWorktree.mockResolvedValue({ worktree: tree({ state: 'kept' }) })
    api.openWorktree.mockResolvedValue({ success: true })
    const { events } = await mount(TreeActions, { tree: tree() })
    await click(byTest('tree-keep'))
    expect(api.keepWorktree).toHaveBeenCalledWith('t1')
    expect(byTest('tree-note')?.textContent).toBe(words.trees.keptNote)
    await click(byTest('tree-open'))
    expect(api.openWorktree).toHaveBeenCalledWith('t1')
    api.landWorktree.mockRejectedValueOnce(refusal('gone'))
    await click(byTest('tree-land'))
    expect(byTest('tree-error')?.textContent).toBe(words.errors.gone)
    // Whatever came of it, how it stands is read again.
    expect(events.length).toBe(3)
  })
})

describe('the worktrees of a project that wait for the user', () => {
  it('are listed apart from the familiars, read again when the board moves, and not the ones at work', async () => {
    api.listWorktrees.mockResolvedValue({ worktrees: [tree(), tree({ id: 't2', state: 'active', reason: '' }), tree({ id: 't3', state: 'held', reason: 'ask' })] })
    const { state } = await mount(ProjectTrees, { project: 'App', refresh: 'r1' })
    expect(api.listWorktrees).toHaveBeenCalledWith('App')
    expect([...document.querySelectorAll('[data-tree]')].map(item => item.getAttribute('data-tree'))).toEqual(['t1', 't3'])
    expect(byTest('project-trees')?.textContent).toContain(words.trees.waiting.replace('{count}', '2'))
    expect(document.querySelector('[data-tree="t1"]')?.textContent).toContain('App/worker · Add an export')
    state.props = { project: 'App', refresh: 'r2' }
    await flush()
    expect(api.listWorktrees).toHaveBeenCalledTimes(2)
  })

  it('say what came of the last one dealt with, also once it is not listed any more', async () => {
    api.listWorktrees.mockResolvedValueOnce({ worktrees: [tree({ state: 'held', reason: 'ask' })] }).mockResolvedValue({ worktrees: [] })
    api.landWorktree.mockResolvedValue({ status: 'landed', files: [{ path: 'a.py' }], more: 0, conflicts: [], resolving: false, detail: '' })
    const { state } = await mount(ProjectTrees, { project: 'App' })
    await click(byTest('tree-land'))
    expect(document.querySelector('[data-tree]')).toBeNull()
    expect(byTest('trees-said')?.textContent).toBe(words.trees.landed.replace('{count}', '1'))
    // Of another project it is not said.
    state.props = { project: 'Other' }
    await flush()
    expect(byTest('project-trees')).toBeNull()
    cleanup?.()
    // One that is still listed says what came of it itself: not said twice.
    api.listWorktrees.mockResolvedValue({ worktrees: [tree()] })
    api.keepWorktree.mockResolvedValue({ worktree: tree({ state: 'kept' }) })
    await mount(ProjectTrees, { project: 'App' })
    await click(byTest('tree-keep'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.keptNote)
    expect(byTest('trees-said')).toBeNull()
  })

  it('once a project has all it may, say so and which ones hold the places', async () => {
    api.getLanding.mockResolvedValue({ mode: 'auto', inspect: false, worktrees_per_project: 2 })
    api.listWorktrees.mockResolvedValue({ worktrees: [tree({ state: 'kept' }), tree({ id: 't2', state: 'active', reason: '', title: 'Fix the footer' })] })
    await mount(ProjectTrees, { project: 'App' })
    expect(byTest('trees-full')?.textContent).toBe(words.trees.full.replace('{used}', '2').replace('{most}', '2'))
    expect([...document.querySelectorAll('[data-tree-held]')].map(item => item.getAttribute('data-tree-held'))).toEqual(['t2'])
    expect(document.querySelector('[data-tree-held="t2"]')?.textContent).toContain('Fix the footer')
    cleanup?.()
    // Room left: nothing is said of it.
    api.getLanding.mockResolvedValue({ mode: 'auto', inspect: false, worktrees_per_project: 3 })
    await mount(ProjectTrees, { project: 'App' })
    expect(byTest('trees-full')).toBeNull()
  })

  it('are not shown when none waits', async () => {
    api.listWorktrees.mockResolvedValue({ worktrees: [tree({ state: 'active', reason: '' })] })
    await mount(ProjectTrees, { project: 'App' })
    expect(byTest('project-trees')).toBeNull()
  })
})

describe('more familiars for a project', () => {
  beforeEach(() => {
    api.getTemplates.mockResolvedValue({ templates: [
      { name: 'boss', label: '', description: '', avatar: 'boss', source: 'builtin', valid: true, error: '' },
      { name: 'general', label: '', description: '', avatar: 'general', source: 'builtin', valid: true, error: '' },
      { name: 'scout', label: '', description: '', avatar: 'scout', source: 'builtin', valid: true, error: '' },
      { name: 'broken', label: '', description: '', avatar: '', source: 'user', valid: false, error: 'bad' },
    ] })
  })

  it('one more is summoned for the project, of a kind that works, and where it works is said', async () => {
    api.addMember.mockResolvedValue({ session_id: 's9', seat: 'tree' })
    const { events } = await mount(CrewTools, { project: 'App', hasLeader: false, lanlanName: 'Lan' })
    await click(byTest('member-add'))
    // A leader is not one of them: a project gets one with the other button.
    const offered = [...document.querySelectorAll('.el-select-dropdown__item')].map(item => item.textContent?.trim())
    expect(offered).toEqual([words.home.presets.general, words.home.presets.scout])
    await type('member-title', 'Exporter')
    await click(byTest('member-make'))
    expect(api.addMember).toHaveBeenCalledWith('App', { preset: 'general', title: 'Exporter', duty: '', lanlan_name: 'Lan' })
    expect(byTest('crew-note')?.textContent).toBe(words.crew.addedTree)
    expect(events).toEqual([['onChanged', ['s9']]])
    api.addMember.mockResolvedValue({ session_id: 's10', seat: '' })
    await click(byTest('member-add'))
    await click(byTest('member-make'))
    expect(byTest('crew-note')?.textContent).toBe(words.crew.added)
  })

  it('one that cannot be summoned says why and the form stays', async () => {
    api.addMember.mockRejectedValue(refusal('no_project', 404))
    await mount(CrewTools, { project: 'App', hasLeader: true })
    await click(byTest('member-add'))
    await click(byTest('member-make'))
    expect(byTest('crew-error')?.textContent).toContain(words.errors.no_project)
    expect(byTest('crew-note')).toBeNull()
  })

  it('a leader is offered only to a project that has none, and given once that is confirmed', async () => {
    api.giveLeader.mockResolvedValue({ session_id: 'boss1', made: true })
    await mount(CrewTools, { project: 'App', hasLeader: true })
    expect(byTest('leader-give')).toBeNull()
    cleanup?.()
    const { events } = await mount(CrewTools, { project: 'App', hasLeader: false, lanlanName: 'Lan' })
    await click(byTest('leader-give'))
    expect(api.giveLeader).not.toHaveBeenCalled()
    await click(byTest('leader-confirm'))
    expect(api.giveLeader).toHaveBeenCalledWith('App', 'Lan')
    expect(byTest('crew-note')?.textContent).toBe(words.crew.leaderMade)
    expect(events).toEqual([['onChanged', ['boss1']]])
  })

  it('are offered on the familiars of a project, not on the loose ones, with what one chosen has in its worktree', async () => {
    api.listWorktrees.mockResolvedValue({ worktrees: [] })
    const member = card({ id: 's2', name: 'App/worker', state: 'unlanded', tree: tree({ state: 'unlanded' }) })
    api.getProjectBoard.mockResolvedValue(board([card(), member]))
    await mount(FamiliarsPane, { project: 'App', selectedId: 's2' })
    expect(byTest('crew-tools')).toBeTruthy()
    expect(byTest('leader-give')).toBeTruthy()
    expect(document.querySelector('[data-detail="s2"] [data-testid="tree-actions"]')).toBeTruthy()
    expect(document.querySelector('[data-familiar="s2"]')?.textContent).toContain(words.home.states.unlanded)
    // Let go from its card: once its worktree is gone from the card, what came of it is still said.
    api.discardWorktree.mockResolvedValue({ removed: true })
    api.getProjectBoard.mockResolvedValue(board([card(), { ...member, state: 'idle', tree: null }]))
    await click(document.querySelector('[data-detail="s2"] [data-testid="tree-discard"]'))
    await click(byTest('tree-discard-confirm'))
    expect(document.querySelector('[data-detail="s2"] [data-testid="tree-actions"]')).toBeNull()
    expect(byTest('tree-said')?.textContent).toBe(words.trees.discarded)
    cleanup?.()
    api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [], loose: [card()] })
    await mount(FamiliarsPane, { project: null, selectedId: '' })
    expect(byTest('crew-tools')).toBeNull()
  })
})

describe('the landing settings', () => {
  it('are shown and each is saved as it is changed', async () => {
    api.getLanding.mockResolvedValue({ mode: 'auto', inspect: false })
    api.setLanding.mockResolvedValue({ mode: 'ask', inspect: false })
    await mount(LandingCard)
    expect(byTest('landing-settings')?.textContent).toContain(words.landing.autoHint)
    await click(byTest('landing-ask')?.querySelector('input') ?? byTest('landing-ask'))
    expect(api.setLanding).toHaveBeenCalledWith({ mode: 'ask' })
    expect(byTest('landing-settings')?.textContent).toContain(words.landing.askHint)
    api.setLanding.mockResolvedValue({ mode: 'ask', inspect: true })
    await click(byTest('landing-inspect'))
    expect(api.setLanding).toHaveBeenLastCalledWith({ inspect: true })
    expect(byTest('landing-note')?.textContent).toBe(words.landing.saved)
  })

  it('that could not be saved say so and stay as they were', async () => {
    api.getLanding.mockResolvedValue({ mode: 'auto', inspect: false })
    api.setLanding.mockRejectedValue(refusal('landing_not_saved'))
    await mount(LandingCard)
    await click(byTest('landing-inspect'))
    expect(byTest('landing-error')?.textContent).toContain(words.errors.landing_not_saved)
    expect(byTest('landing-settings')?.textContent).toContain(words.landing.autoHint)
  })
})

describe('looking for words in what was said', () => {
  beforeEach(() => {
    api.getTranscript.mockResolvedValue({ readable: true, before: null, entries: [
      { at: 0, role: 'user', text: 'plant the beans', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
  })

  it('names the places found, the latest first, and opens one whole', async () => {
    api.searchTranscript.mockResolvedValue({ readable: true, total: 60, whole: false, hits: [
      { at: 120, role: 'assistant', tool: null, excerpt: '…beans planted', time: null },
      { at: 0, role: 'user', tool: null, excerpt: 'plant the beans', time: null },
    ] })
    await mount(TranscriptPane, { sessionId: 's1' })
    await type('transcript-query', ' beans ')
    await click(byTest('transcript-search'))
    expect(api.searchTranscript).toHaveBeenCalledWith('s1', 'beans')
    const said = byTest('transcript-found')?.textContent ?? ''
    expect(said).toContain(words.transcript.found.replace('{count}', '60'))
    expect(said).toContain(words.transcript.foundShown.replace('{count}', '2'))
    expect(said).toContain(words.transcript.foundPart)
    expect([...document.querySelectorAll('[data-hit]')].map(item => item.getAttribute('data-hit'))).toEqual(['120', '0'])
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      { at: 120, role: 'assistant', text: 'All the beans planted by the wall.', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
    await click(document.querySelector('[data-hit-open="120"]'))
    expect(api.getTranscript).toHaveBeenLastCalledWith('s1', { at: 120 })
    expect(document.querySelector('[data-hit="120"]')?.textContent).toContain('All the beans planted by the wall.')
    // Back to the conversation as it was.
    await click(byTest('transcript-search-end'))
    expect(document.querySelector('[data-hit]')).toBeNull()
    expect(document.querySelector('[data-entry="0"]')).toBeTruthy()
  })

  it('says when nothing was found, and a search of the familiar left is not shown for the next', async () => {
    api.searchTranscript.mockResolvedValueOnce({ readable: true, total: 0, whole: true, hits: [] })
    const { state } = await mount(TranscriptPane, { sessionId: 's1' })
    await type('transcript-query', 'cucumbers')
    await click(byTest('transcript-search'))
    expect(byTest('transcript-found')?.textContent?.trim()).toBe(words.transcript.foundNone)
    let answer: (value: unknown) => void = () => {}
    api.searchTranscript.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
    await type('transcript-query', 'beans')
    await click(byTest('transcript-search'))
    state.props = { sessionId: 's2' }
    await flush()
    answer({ readable: true, total: 1, whole: true, hits: [{ at: 0, role: 'user', tool: null, excerpt: 'beans', time: null }] })
    await flush()
    expect(byTest('transcript-found')).toBeNull()
  })
})

describe('a familiar of a project that was archived since', () => {
  beforeEach(() => {
    api.readRules.mockResolvedValue({ text: '' })
    api.getProjectBoard.mockResolvedValue({ revision: 'r', projects: [], loose: [] })
    api.getTranscript.mockResolvedValue({ readable: true, before: null, entries: [] })
    api.listWorktrees.mockResolvedValue({ worktrees: [] })
  })

  it('is said to be of that project, which is a click away; nothing is said of a project at work', async () => {
    api.listProjects.mockResolvedValue({ projects: [
      project({ name: 'Moving list', archive_year: '2025', directory: 'moving-list-1', archived_at: 1 }),
      project({ name: 'Moving list', archive_year: '2026', directory: 'moving-list', archived_at: 2 }),
    ] })
    route.query = { project: 'Moving list', familiar: 's7' }
    await mount(FamiliarSpace)
    expect(byTest('familiar-archived')?.textContent).toContain('"Moving list" (archived in 2026)')
    // The scope all familiars share is not shown as the one chosen.
    expect(document.querySelector('[data-scope=""]')?.classList.contains('on')).toBe(false)
    await click(byTest('familiar-archived-open'))
    expect(route.query).toEqual({ project: 'moving-list', year: '2026' })
    expect(byTest('familiar-archived')).toBeNull()
    cleanup?.()
    api.listProjects.mockResolvedValue({ projects: [project()] })
    route.query = { project: 'App', familiar: 's1' }
    await mount(FamiliarSpace)
    expect(byTest('familiar-archived')).toBeNull()
  })

  it('a project that is busy for what waits in its worktrees says so when its folder is to be changed', async () => {
    api.listProjects.mockResolvedValue({ projects: [project()] })
    api.setProjectWorkdir.mockRejectedValue(refusal('project_busy', 409, { reason: 'tree_not_landed' }))
    route.query = { project: 'App' }
    await mount(FamiliarSpace)
    await click(byTest('workdir-change'))
    await type('workdir-path', 'D:/elsewhere')
    await click(byTest('workdir-save'))
    expect(byTest('workdir-error')?.textContent).toContain(words.errors.project_busy_tree_not_landed)
    // Any other reason: the general words.
    api.setProjectWorkdir.mockRejectedValue(refusal('project_busy', 409, { reason: 'at_work' }))
    await click(byTest('workdir-save'))
    expect(byTest('workdir-error')?.textContent).toContain(words.errors.project_busy)
  })
})

