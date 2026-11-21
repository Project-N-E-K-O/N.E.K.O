// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { Board, BoardCard } from '@/api/console'
import FamiliarBoard from './FamiliarBoard.vue'
import FamiliarCard from './FamiliarCard.vue'
import PowerBar from './PowerBar.vue'

const api = vi.hoisted(() => ({
  getBoard: vi.fn(), getSwitches: vi.fn(), setPower: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)
const metrics = vi.hoisted(() => ({ getAllMetrics: vi.fn() }))
vi.mock('@/api/metrics', () => metrics)
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const card = (over: Partial<BoardCard> = {}): BoardCard => ({
  id: 's1', name: 'Ledger tool', kind: 'project', lanlan_name: 'Lan', runtime: 'builtin', workdir: 'D:/code/ledger-tool',
  preset: 'boss', avatar: 'boss', label: '', description: '', title: '', duty: 'Keeps the ledger tool working', duty_by: 'creator',
  doing: '', state: 'idle', leader: true, parent: null, model: 'agent-model', commission: null, ...over,
})
const board = (over: Partial<Board> = {}): Board => ({
  revision: 'r1',
  projects: [{
    name: 'Ledger tool', description: 'Keeps the books', workdir: 'D:/code/ledger-tool', archived: false, done_today: 5,
    members: [
      card({ state: 'working', doing: 'Adding an Excel export', commission: { id: 'c1', title: 'Add an export', status: 'working', started_at: 0 } }),
      card({ id: 's2', name: 'Look it up', kind: 'child', preset: 'scout', avatar: 'scout', leader: false, parent: 's1', state: 'working', duty: 'Finds how openpyxl merges cells' }),
    ],
  }, {
    name: 'Trip', description: '', workdir: null, archived: false, done_today: 0,
    members: [card({ id: 's3', name: 'Trip', state: 'waiting_user', commission: { id: 'c3', title: 'Book the train', status: 'waiting_user', started_at: 0, ask: { id: 'q1', question: 'Which day?' } } })],
  }, {
    name: 'Old', description: '', workdir: null, archived: true, done_today: 0, members: [card({ id: 's9' })],
  }],
  loose: [card({ id: 's4', name: 'Screenshot to table', kind: 'single', preset: 'general', avatar: 'general', leader: false, state: 'asleep' })],
  ...over,
})

let cleanup: (() => void) | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() } }
async function mount(component: Component, props: Record<string, unknown> = {}, listeners: Record<string, unknown> = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({ render: () => h(component, { ...props, ...listeners }) })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words } } }))
  app.mount(container)
  cleanup = () => { app.unmount(); container.remove() }
  await flush()
  return container
}
const byTest = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  vi.clearAllMocks()
  metrics.getAllMetrics.mockResolvedValue({ metrics: [], count: 0, time: '' })
})
afterEach(() => { cleanup?.(); cleanup = undefined; document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals() })

describe('a familiar on the board', () => {
  it('is named by its title once it has one, and by what kind it is until then', async () => {
    const page = await mount(FamiliarCard, { card: card() })
    expect(page.querySelector('.title')?.textContent).toBe(words.home.presets.boss)
    expect(page.querySelector('.line')?.textContent).toBe('Keeps the ledger tool working')
    cleanup?.()
    const titled = await mount(FamiliarCard, { card: card({ title: 'Keeper of the books', doing: 'Adding an Excel export' }) })
    expect(titled.querySelector('.title')?.textContent).toBe('Keeper of the books')
    // What it does now comes before what it is there for.
    expect(titled.querySelector('.line')?.textContent).toBe('Adding an Excel export')
  })

  it('made from a template of the user\'s goes by the template\'s own name, or its key', async () => {
    const named = await mount(FamiliarCard, { card: card({ preset: 'translator', avatar: 'general', label: 'Translator' }) })
    expect(named.querySelector('.title')?.textContent).toBe('Translator')
    cleanup?.()
    const bare = await mount(FamiliarCard, { card: card({ preset: 'translator', avatar: 'nothing-drawn-for-it', label: '' }) })
    expect(bare.querySelector('.title')?.textContent).toBe('translator')
    expect(bare.querySelector('.cat')?.textContent).toBeTruthy()
  })

  it.each([
    ['working', null, 'working', words.home.states.working],
    ['waiting_user', null, 'waiting_user', words.home.states.waiting_user],
    ['compacting', null, 'compacting', words.home.states.compacting],
    ['asleep', null, 'asleep', words.home.states.asleep],
    ['asleep', 'resting', 'resting', words.home.states.resting],
    ['asleep', 'queued', 'queued', words.home.states.queued],
    // One the page has no words for yet is shown as it came, and breaks nothing.
    ['landing', null, 'asleep', 'landing'],
  ])('in state %s with %s in hand is drawn as %s', async (state, status, look, label) => {
    const commission = status ? { id: 'c', title: 't', status, started_at: 0 } : null
    const page = await mount(FamiliarCard, { card: card({ state, commission }) })
    expect(page.querySelector('.familiar')?.getAttribute('data-state')).toBe(look)
    expect(page.querySelector('.state')?.textContent).toBe(label)
  })
})

describe('the board', () => {
  it('groups the familiars by project, the lead first, and leaves out archived projects', async () => {
    api.getBoard.mockResolvedValue(board())
    const page = await mount(FamiliarBoard)
    expect([...page.querySelectorAll('[data-hive]')].map(hive => hive.getAttribute('data-hive'))).toEqual([
      'project:Ledger tool', 'project:Trip', 'loose',
    ])
    const ledger = page.querySelector('[data-hive="project:Ledger tool"]')!
    expect([...ledger.querySelectorAll('[data-familiar]')].map(item => item.getAttribute('data-familiar'))).toEqual(['s1', 's2'])
    expect(ledger.textContent).toContain('D:/code/ledger-tool')
    expect(ledger.textContent).toContain('5 done today')
    expect(page.querySelector('[data-hive="project:Trip"]')?.textContent).toContain(words.home.defaultWorkdir)
    expect(page.querySelector('[data-hive="loose"]')?.textContent).toContain(words.home.loose)
    expect(byTest('board-tally')?.textContent).toContain('2 working')
    expect(byTest('board-tally')?.textContent).toContain('1 waiting for you')
  })

  it('opens a familiar in the familiar space, in its project', async () => {
    api.getBoard.mockResolvedValue(board())
    const page = await mount(FamiliarBoard)
    ;(page.querySelector('[data-familiar="s2"]') as HTMLElement).click()
    expect(push).toHaveBeenLastCalledWith({ path: '/familiars/space', query: { project: 'Ledger tool', familiar: 's2' } })
    ;(page.querySelector('[data-familiar="s4"]') as HTMLElement).click()
    expect(push).toHaveBeenLastCalledWith({ path: '/familiars/space', query: { project: undefined, familiar: 's4' } })
  })

  it('asks again with what it has, and keeps showing it while nothing changed', async () => {
    vi.useFakeTimers()
    api.getBoard.mockResolvedValueOnce(board())
    api.getBoard.mockResolvedValueOnce({ revision: 'r1', unchanged: true })
    api.getBoard.mockResolvedValueOnce(board({ revision: 'r2', projects: [], loose: [] }))
    const page = await mount(FamiliarBoard)
    expect(api.getBoard).toHaveBeenLastCalledWith(undefined)
    await vi.advanceTimersByTimeAsync(2000)
    await flush()
    expect(api.getBoard).toHaveBeenLastCalledWith('r1')
    expect(page.querySelectorAll('[data-familiar]').length).toBe(4)
    await vi.advanceTimersByTimeAsync(2000)
    await flush()
    expect(page.querySelectorAll('[data-familiar]').length).toBe(0)
    expect(byTest('board-empty')).toBeTruthy()
  })

  it('says so when the service has no board to give, and asks less often then', async () => {
    vi.useFakeTimers()
    api.getBoard.mockRejectedValue({ response: { status: 404, data: { detail: 'Not Found' } } })
    await mount(FamiliarBoard)
    expect(byTest('board-error')?.textContent).toContain(words.home.unavailable)
    expect(byTest('board-empty')).toBeNull()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.getBoard).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000)
    expect(api.getBoard).toHaveBeenCalledTimes(2)
  })

  it('stops asking once it is gone, also when an answer was still on its way', async () => {
    vi.useFakeTimers()
    api.getBoard.mockResolvedValueOnce(board())
    await mount(FamiliarBoard)
    let answer: (value: Board) => void = () => {}
    api.getBoard.mockReturnValueOnce(new Promise<Board>((resolve) => { answer = resolve }))
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.getBoard).toHaveBeenCalledTimes(2)
    cleanup?.()
    cleanup = undefined
    answer({ revision: 'r1', unchanged: true })
    await flush()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.getBoard).toHaveBeenCalledTimes(2)
  })
})

describe('the switch for all of it', () => {
  const switches = (enabled: boolean, gate: Record<string, unknown> = { ready: true }) => ({
    switches: { enabled, flags: {}, gate, capabilities: {}, model_images: { sees: true } },
  })

  it('shows where the service stands after a switch, and tells the page', async () => {
    api.getSwitches.mockResolvedValueOnce(switches(false))
    api.setPower.mockResolvedValue({ success: true })
    api.getSwitches.mockResolvedValueOnce(switches(true))
    const changed = vi.fn()
    await mount(PowerBar, {}, { onChanged: changed })
    expect(byTest('power-state')?.textContent).toBe(words.home.off)
    expect(byTest('power-switch')?.getAttribute('aria-checked')).toBe('false')
    byTest('power-switch')!.click()
    await flush()
    expect(api.setPower).toHaveBeenCalledWith(true)
    expect(byTest('power-switch')?.getAttribute('aria-checked')).toBe('true')
    expect(byTest('power-state')?.textContent).toBe(words.home.on)
    expect(changed).toHaveBeenCalledWith(true)
  })

  it('keeps saying that a switch did not take, with the switch where the service has it', async () => {
    api.getSwitches.mockResolvedValue(switches(false))
    api.setPower.mockRejectedValue(Object.assign(new Error('x'), { code: 'switch_failed', response: { status: 502 } }))
    await mount(PowerBar)
    byTest('power-switch')!.click()
    await flush()
    expect(byTest('power-error')?.textContent).toBe(words.errors.switch_failed)
    expect(byTest('power-switch')?.getAttribute('aria-checked')).toBe('false')
  })

  it('says why it is on and not ready, and shows the meters when there are some', async () => {
    api.getSwitches.mockResolvedValue(switches(true, { ready: false, reasons: ['AGENT_ENDPOINT_NOT_CONFIGURED'] }))
    metrics.getAllMetrics.mockResolvedValue({
      metrics: [], count: 0, time: '',
      global: { total_cpu_percent: 23.4, total_memory_mb: 512, total_memory_percent: 12, total_threads: 9, active_plugins: 2 },
    })
    await mount(PowerBar)
    expect(byTest('power-reason')?.textContent).toContain('AGENT_ENDPOINT_NOT_CONFIGURED')
    expect(byTest('power-meters')?.textContent).toContain('23%')
    expect(byTest('power-meters')?.textContent).toContain('512 MB')
  })
})
