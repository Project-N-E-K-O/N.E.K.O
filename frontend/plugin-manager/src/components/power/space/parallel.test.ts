// @vitest-environment happy-dom
/**
 * Several familiars at work in one project: what a familiar changed in a git worktree of its own and
 * what the user decides on it, one more familiar or a leader for a project, the landing settings,
 * looking for words in what was said, whose an archived familiar is, and whether another is to do
 * what waits for the one chosen, busy with something else.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { Board, BoardAsk, BoardCard, CommissionRow, SpaceProject, Worktree } from '@/api/console'
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
  getLanding: vi.fn(), setLanding: vi.fn(), getTemplates: vi.fn(), addMember: vi.fn(), giveLeader: vi.fn(), listCommissions: vi.fn(),
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

  it('that the user sees to keeps from being merged again what was not carried over, and is the user\'s to merge where it clashes', async () => {
    await mount(TreeActions, { tree: tree({ state: 'kept', reason: 'skipped', conflicts: ['big.bin'] }) })
    expect(byTest('tree-land')).toBeNull()
    expect(byTest('tree-conflicts')?.textContent).toContain(words.trees.skippedFiles.replace('{files}', 'big.bin'))
    cleanup?.()
    api.landWorktree.mockResolvedValue({ status: 'conflict', files: [], more: 0, conflicts: ['a.py'], resolving: false, detail: '' })
    await mount(TreeActions, { tree: tree({ state: 'kept', reason: 'conflict' }) })
    await click(byTest('tree-land'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.keptClashes)
    cleanup?.()
    // One not merged that clashes again and cannot go back: still the words for that.
    await mount(TreeActions, { tree: tree() })
    await click(byTest('tree-land'))
    expect(byTest('tree-note')?.textContent).toBe(words.trees.stillClashes)
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

  it('says nothing of what came of a decision on one worktree once another is shown', async () => {
    let answer: (value: unknown) => void = () => {}
    api.landWorktree.mockReturnValue(new Promise(resolve => { answer = resolve }))
    const { state, events } = await mount(TreeActions, { tree: tree() })
    await click(byTest('tree-land'))
    state.props = { tree: tree({ id: 't2', state: 'held', reason: 'ask' }) }
    await flush()
    answer({ status: 'landed', files: [{ path: 'a.py' }], more: 0, conflicts: [], resolving: false, detail: '' })
    await flush()
    expect(byTest('tree-note')).toBeNull()
    // Read anew all the same, as of the one it was made on.
    expect(events).toEqual([['onChanged', ['t1', '']]])
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

  it('offers the kinds of this project, read again for another, and never opens on one it does not have', async () => {
    api.getTemplates.mockResolvedValue({ templates: [
      { name: 'scout', label: '', description: '', avatar: 'scout', source: 'builtin', valid: true, error: '' },
      { name: 'translator', label: 'Translator', description: '', avatar: '', source: 'project', valid: true, error: '' },
      { name: 'general', label: '', description: '', avatar: 'general', source: 'builtin', valid: false, error: 'broken here' },
    ] })
    api.addMember.mockResolvedValue({ session_id: 's9', seat: '' })
    const { state } = await mount(CrewTools, { project: 'App', hasLeader: true })
    await click(byTest('member-add'))
    expect(api.getTemplates).toHaveBeenCalledWith('App')
    const offered = [...document.querySelectorAll('.el-select-dropdown__item')].map(item => item.textContent?.trim())
    expect(offered).toEqual([words.home.presets.scout, 'Translator'])
    await click(byTest('member-make'))
    expect(api.addMember).toHaveBeenLastCalledWith('App', expect.objectContaining({ preset: 'scout' }))
    // Opened again: read once, and still not on the kind this project does not have.
    await click(byTest('member-add'))
    await click(byTest('member-make'))
    expect(api.addMember).toHaveBeenLastCalledWith('App', expect.objectContaining({ preset: 'scout' }))
    expect(api.getTemplates).toHaveBeenCalledTimes(1)
    state.props = { project: 'Other', hasLeader: true }
    await flush()
    await click(byTest('member-add'))
    expect(api.getTemplates).toHaveBeenLastCalledWith('Other')
  })

  it('sends nothing while the kinds of the project are read, and reads them again after a failure', async () => {
    let answer: (value: unknown) => void = () => {}
    api.getTemplates.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
    api.addMember.mockResolvedValue({ session_id: 's9', seat: '' })
    await mount(CrewTools, { project: 'App', hasLeader: true })
    const opening = click(byTest('member-add'))
    await flush()
    // Not read yet: the kind it opens with is not known to be one of this project's.
    expect(byTest('member-make')?.hasAttribute('disabled')).toBe(true)
    answer({ templates: [{ name: 'scout', label: '', description: '', avatar: 'scout', source: 'builtin', valid: true, error: '' }] })
    await opening
    await flush()
    expect(byTest('member-make')?.hasAttribute('disabled')).toBe(false)
    await click(byTest('member-make'))
    expect(api.addMember).toHaveBeenLastCalledWith('App', expect.objectContaining({ preset: 'scout' }))
    cleanup?.()
    // Could not be read: the kind every project has, and asked again the next time.
    api.getTemplates.mockRejectedValueOnce(new Error('down'))
    await mount(CrewTools, { project: 'App', hasLeader: true })
    await click(byTest('member-add'))
    expect(byTest('member-make')?.hasAttribute('disabled')).toBe(false)
    await click(document.querySelector('.el-dialog__headerbtn'))
    await click(byTest('member-add'))
    expect(api.getTemplates).toHaveBeenCalledTimes(3)
  })

  it('sends nothing for another project while its kinds are read, though the last project\'s are held', async () => {
    api.getTemplates.mockResolvedValueOnce({ templates: [{ name: 'general', label: '', description: '', avatar: 'general', source: 'builtin', valid: true, error: '' }] })
    const { state } = await mount(CrewTools, { project: 'App', hasLeader: true })
    await click(byTest('member-add'))
    await click(document.querySelector('.el-dialog__headerbtn'))
    let answer: (value: unknown) => void = () => {}
    api.getTemplates.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
    state.props = { project: 'Other', hasLeader: true }
    await flush()
    const opening = click(byTest('member-add'))
    await flush()
    expect(byTest('member-make')?.hasAttribute('disabled')).toBe(true)
    answer({ templates: [{ name: 'general', label: '', description: '', avatar: 'general', source: 'builtin', valid: true, error: '' }] })
    await opening
    await flush()
    expect(byTest('member-make')?.hasAttribute('disabled')).toBe(false)
  })

  it('does not keep the kinds read for a project the user has left meanwhile', async () => {
    let answer: (value: unknown) => void = () => {}
    api.getTemplates.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
    const { state } = await mount(CrewTools, { project: 'App', hasLeader: true })
    await click(byTest('member-add'))
    state.props = { project: 'Other', hasLeader: true }
    await flush()
    answer({ templates: [{ name: 'scout', label: '', description: '', avatar: 'scout', source: 'builtin', valid: true, error: '' }] })
    await flush()
    await click(byTest('member-add'))
    // Read for the project now shown.
    expect(api.getTemplates).toHaveBeenLastCalledWith('Other')
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

  it('a familiar summoned is shown up close only while its project is still the one shown', async () => {
    api.listWorktrees.mockResolvedValue({ worktrees: [] })
    api.getTemplates.mockResolvedValue({ templates: [{ name: 'general', label: '', description: '', avatar: 'general', source: 'builtin', valid: true, error: '' }] })
    api.addMember.mockResolvedValue({ session_id: 's9', seat: '' })
    api.getProjectBoard.mockResolvedValue(board([card()]))
    const { state, events } = await mount(FamiliarsPane, { project: 'App', selectedId: '' })
    let answer: (value: unknown) => void = () => {}
    api.getProjectBoard.mockReturnValueOnce(new Promise(resolve => { answer = resolve }))
    await click(byTest('member-add'))
    await click(byTest('member-make'))
    state.props = { project: 'Other', selectedId: '' }
    await flush()
    answer(board([card(), card({ id: 's9' })]))
    await flush()
    expect(events.filter(([name]) => name === 'onSelect')).toEqual([])
    cleanup?.()
    // Still the one shown: chosen.
    api.getProjectBoard.mockResolvedValue(board([card()]))
    const second = await mount(FamiliarsPane, { project: 'App', selectedId: '' })
    await click(byTest('member-add'))
    await click(byTest('member-make'))
    expect(second.events.filter(([name]) => name === 'onSelect')).toEqual([['onSelect', ['s9']]])
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

  it('of a place opened whole that is too long to show whole, still says where the words were found', async () => {
    api.searchTranscript.mockResolvedValue({ readable: true, total: 1, whole: true, hits: [
      { at: 120, role: 'tool_result', tool: 'read', excerpt: '…the needle is here', time: null },
    ] })
    await mount(TranscriptPane, { sessionId: 's1' })
    await type('transcript-query', 'needle')
    await click(byTest('transcript-search'))
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      { at: 120, role: 'tool_result', text: 'a very long file, cut before the needle', cut: true, calls: [], tool: 'read', failed: false, pictures: 0, time: null },
    ] })
    await click(document.querySelector('[data-hit-open="120"]'))
    expect(byTest('transcript-hit-cut')?.textContent).toBe(words.transcript.hitCut)
    expect(document.querySelector('[data-hit="120"]')?.textContent).toContain('…the needle is here')
    // One shown whole has nothing of the kind.
    await click(document.querySelector('[data-hit-open="120"]'))
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      { at: 120, role: 'tool_result', text: 'the needle is here', cut: false, calls: [], tool: 'read', failed: false, pictures: 0, time: null },
    ] })
    await click(document.querySelector('[data-hit-open="120"]'))
    expect(byTest('transcript-hit-cut')).toBeNull()
    // Arguments of a call cut short are as good as a cut text.
    await click(document.querySelector('[data-hit-open="120"]'))
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      { at: 120, role: 'assistant', text: '', cut: false, calls: [{ name: 'write', arguments: '{...', cut: true }], tool: null, failed: false, pictures: 0, time: null },
    ] })
    await click(document.querySelector('[data-hit-open="120"]'))
    expect(byTest('transcript-hit-cut')).toBeTruthy()
  })

  it('shows the place asked to be opened last, whichever answer comes first', async () => {
    api.searchTranscript.mockResolvedValue({ readable: true, total: 2, whole: true, hits: [
      { at: 120, role: 'assistant', tool: null, excerpt: 'later beans', time: null },
      { at: 0, role: 'user', tool: null, excerpt: 'first beans', time: null },
    ] })
    await mount(TranscriptPane, { sessionId: 's1' })
    await type('transcript-query', 'beans')
    await click(byTest('transcript-search'))
    let late: (value: unknown) => void = () => {}
    api.getTranscript.mockReturnValueOnce(new Promise(resolve => { late = resolve }))
    api.getTranscript.mockResolvedValueOnce({ readable: true, before: null, entries: [
      { at: 0, role: 'user', text: 'the first beans, whole', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
    await click(document.querySelector('[data-hit-open="120"]'))
    await click(document.querySelector('[data-hit-open="0"]'))
    late({ readable: true, before: null, entries: [
      { at: 120, role: 'assistant', text: 'the later beans, whole', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
    await flush()
    expect(document.querySelector('[data-hit="0"]')?.textContent).toContain('the first beans, whole')
    expect(document.querySelector('[data-hit="120"]')?.textContent).not.toContain('the later beans, whole')
    // Asked to be opened, and searched anew before the answer came: the late answer opens nothing,
    // and the place is not shown as being opened any more.
    let older: (value: unknown) => void = () => {}
    api.getTranscript.mockReturnValueOnce(new Promise(resolve => { older = resolve }))
    await click(document.querySelector('[data-hit-open="120"]'))
    await click(byTest('transcript-search'))
    expect(document.querySelector('[data-hit-open="120"]')?.classList.contains('is-loading')).toBe(false)
    older({ readable: true, before: null, entries: [
      { at: 120, role: 'assistant', text: 'the later beans, whole', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
    await flush()
    expect(document.querySelector('[data-hit="120"]')?.textContent).not.toContain('the later beans, whole')
    // Asked to be opened, then the search was left and done anew: the late answer opens nothing.
    let stale: (value: unknown) => void = () => {}
    api.getTranscript.mockReturnValueOnce(new Promise(resolve => { stale = resolve }))
    await click(document.querySelector('[data-hit-open="120"]'))
    await click(byTest('transcript-search-end'))
    expect(byTest('transcript-latest')?.classList.contains('is-loading')).toBe(false)
    await type('transcript-query', 'beans')
    await click(byTest('transcript-search'))
    stale({ readable: true, before: null, entries: [
      { at: 120, role: 'assistant', text: 'the later beans, whole', cut: false, calls: [], tool: null, failed: false, pictures: 0, time: null },
    ] })
    await flush()
    expect(document.querySelector('[data-hit="120"]')?.textContent).not.toContain('the later beans, whole')
  })

  it('leaves no place of the conversation shown as being opened once the search is left', async () => {
    api.getTranscript.mockResolvedValue({ readable: true, before: null, entries: [
      { at: 120, role: 'tool_result', text: 'a long result', cut: true, calls: [], tool: 'read', failed: false, pictures: 0, time: null },
    ] })
    api.searchTranscript.mockResolvedValue({ readable: true, total: 1, whole: true, hits: [
      { at: 120, role: 'tool_result', tool: 'read', excerpt: 'long', time: null },
    ] })
    await mount(TranscriptPane, { sessionId: 's1' })
    await type('transcript-query', 'long')
    await click(byTest('transcript-search'))
    api.getTranscript.mockReturnValueOnce(new Promise(() => {}))
    await click(document.querySelector('[data-hit-open="120"]'))
    await click(byTest('transcript-search-end'))
    // Back in the conversation: the same place is not being opened.
    expect(document.querySelector('[data-whole="120"]')?.classList.contains('is-loading')).toBe(false)
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


describe('what is asked of opening another beside the one chosen, busy with something else', () => {
  const busy = (over: Partial<BoardCard> = {}) =>
    card({ state: 'working', commission: { id: 'c1', title: 'Add an export', status: 'working', started_at: 0 }, ...over })
  const beside = (over: Partial<BoardAsk['detail']> = {}): BoardAsk => ({
    id: 'q9', kind: 'permission', category: 'open_beside', options: ['always', 'session', 'once', 'deny'],
    detail: {
      tool: 'beside', title: 'Write the newsletter', preset: 'general', tree: true, member: 'App/general-2', project: 'App',
      beside: 'App', beside_session: 's1', at_hand: 'Add an export', ...over,
    },
  })
  const row = (ask: BoardAsk = beside()): CommissionRow => ({
    id: 'c9', title: 'Write the newsletter', status: 'waiting_user', reason: 'waiting for the user', result: '', source: 'dispatch',
    route: 'task_agent', project: 'App', lanlan_name: 'Lan', parent_id: null, session_id: null, session: null, runtime: null,
    plugin_id: null, entry_id: null, created_at: 0, started_at: null, ended_at: null, updated_at: 0, usage: { requests: 0, tokens: 0 }, ask,
  })
  const asked = (rows: CommissionRow[] = [row()]) => ({ commissions: rows, more: false })
  const choice = (option: string) => document.querySelector(`[data-testid="beside-ask"] [data-choice="${option}"]`) as HTMLElement | null

  beforeEach(() => {
    api.listWorktrees.mockResolvedValue({ worktrees: [] })
    api.getTemplates.mockResolvedValue({ templates: [] })
    api.getProjectBoard.mockResolvedValue(board([busy()]))
    api.listCommissions.mockResolvedValue(asked())
  })

  it('stands under it, answered with a button, and says why an answer was not taken', async () => {
    await mount(FamiliarsPane, { project: 'App', selectedId: 's1' })
    expect(api.listCommissions).toHaveBeenCalledWith({ status: ['waiting_user'] })
    const asking = document.querySelector('[data-detail="s1"] [data-testid="beside-ask"]')
    expect(asking?.textContent).toContain('App/general-2')
    expect(asking?.textContent).toContain(words.home.states.unborn)
    expect(asking?.textContent).toContain('Add an export')

    api.answerCommission.mockRejectedValueOnce(refusal('not_taken'))
    await click(choice('deny'))
    expect(byTest('beside-error')?.textContent).toContain(words.errors.not_taken)

    // On its way, its buttons wait; taken, the board and what is asked are read anew, and it is gone.
    let arrive: (value: unknown) => void = () => {}
    api.answerCommission.mockReturnValueOnce(new Promise(done => { arrive = done }))
    await click(choice('once'))
    expect(api.answerCommission).toHaveBeenLastCalledWith('c9', { choice: 'once', text: undefined, question_id: 'q9' })
    expect(choice('deny')?.hasAttribute('disabled')).toBe(true)
    expect(byTest('beside-error')).toBeNull()
    const boards = api.getProjectBoard.mock.calls.length
    api.listCommissions.mockResolvedValue(asked([]))
    arrive({ success: true })
    await flush()
    expect(api.getProjectBoard.mock.calls.length).toBeGreaterThan(boards)
    expect(byTest('beside-ask')).toBeNull()
    expect(document.querySelector('[data-detail="s1"] .besides')).toBeNull()
  })

  it('is not shown with another familiar, and not looked for while the one chosen is at nothing', async () => {
    api.getProjectBoard.mockResolvedValue(board([busy(), busy({ id: 's3', name: 'App/scout' }), card({ id: 's2', name: 'App/idle' })]))
    const { state } = await mount(FamiliarsPane, { project: 'App', selectedId: 's2' })
    expect(document.querySelector('[data-detail="s2"]')).toBeTruthy()
    expect(api.listCommissions).not.toHaveBeenCalled()
    // Another that is at something is chosen: looked for, and not shown with it.
    state.props = { project: 'App', selectedId: 's3' }
    await flush()
    expect(document.querySelector('[data-detail="s3"]')).toBeTruthy()
    expect(api.listCommissions).toHaveBeenCalled()
    expect(byTest('beside-ask')).toBeNull()
  })

  it('is not looked for among the loose ones', async () => {
    api.getProjectBoard.mockResolvedValue({ ...board([busy()]), loose: [busy({ id: 'l1', name: 'Sort the photos', kind: 'single' })] })
    await mount(FamiliarsPane, { project: null, selectedId: 'l1' })
    expect(document.querySelector('[data-detail="l1"]')).toBeTruthy()
    expect(api.listCommissions).not.toHaveBeenCalled()
  })
})
