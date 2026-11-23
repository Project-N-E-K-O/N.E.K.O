// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, h, markRaw, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { Board, BoardCard, TimelineEntry } from '@/api/console'
import ActivityFeed from './ActivityFeed.vue'
import ColonyTerritory from './ColonyTerritory.vue'
import type { ColonyMember, Territory } from './ColonyTerritory.vue'
import FamiliarColony from './FamiliarColony.vue'
import { SAID_SECONDS, describeEntry, firstSeen, ghostsOf, sceneAt } from './activity'
import { defaultSpots, inTerritory, intoTerritory, keepSpots, keptSpots, territorySize } from './layout'

const api = vi.hoisted(() => ({
  getBoard: vi.fn(), getTimeline: vi.fn(), getTimelineStart: vi.fn(), getWaits: vi.fn(),
  answerCommission: vi.fn(), archiveProject: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)
const push = vi.fn()
vi.mock('vue-router', () => ({ useRouter: () => ({ push }) }))

const entry = (kind: string, over: Partial<TimelineEntry> = {}): TimelineEntry => ({
  cursor: `e${over.at ?? 0}.m0`, at: 0, kind, lanlan_name: 'Lan', project: 'Blog', session_id: 'lead', commission_id: 'c1',
  peer: null, text: '', detail: {}, ...over,
})
// A lead is given a job, sends out a scout that reports back, then asks the user and goes on.
const story: TimelineEntry[] = [
  entry('filed', { at: 100, text: 'Move the blog' }),
  entry('started', { at: 101 }),
  entry('child_started', { at: 110, text: 'Find the templates', peer: { type: 'session', session_id: 'scout', commission_id: 'c2' }, detail: { preset: 'scout' } }),
  entry('filed', { at: 110, session_id: 'scout', commission_id: 'c2', text: 'Find the templates' }),
  entry('started', { at: 111, session_id: 'scout', commission_id: 'c2' }),
  entry('message', { at: 120, session_id: 'scout', commission_id: 'c2', text: 'They are in themes/old', peer: { type: 'session', session_id: 'lead', commission_id: 'c1' } }),
  entry('child_returned', { at: 130, session_id: 'scout', commission_id: 'c2', text: 'done', peer: { type: 'session', session_id: 'lead', commission_id: 'c1' }, detail: { outcome: 'done' } }),
  entry('asked_user', { at: 200, text: 'Overwrite the old theme?', peer: { type: 'user' } }),
  entry('started', { at: 260, detail: { from: 'waiting_user' } }),
  entry('teams_joined', { at: 261, text: 'a kind this page was not told of' }),
  entry('ended', { at: 300, text: 'moved', detail: { outcome: 'done' } }),
]

const card = (over: Partial<BoardCard> = {}): BoardCard => ({
  id: 'lead', name: 'Blog', kind: 'project', lanlan_name: 'Lan', runtime: 'builtin', workdir: 'D:/blog', preset: 'boss', avatar: 'boss',
  label: '', description: '', title: '', duty: '', duty_by: '', doing: '', state: 'idle', leader: true, parent: null, model: 'agent-model',
  commission: null, ...over,
})
const board = (over: Partial<Board> = {}): Board => ({
  revision: 'r1',
  projects: [{
    name: 'Blog', description: '', workdir: 'D:/blog', archived: false, done_today: 0,
    members: [
      card({ state: 'working', commission: { id: 'c1', title: 'Move the blog', status: 'working', started_at: 0 } }),
      card({ id: 'kid', name: 'Swap the template', kind: 'child', preset: 'worker', avatar: 'worker', leader: false, parent: 'lead', state: 'waiting_user',
        commission: { id: 'c3', title: 'Swap the template', status: 'waiting_user', started_at: 0, ask: {
          id: 'q1', kind: 'permission', category: 'run_command', detail: { tool: 'bash', command: 'npm run build -- --mode production' }, options: ['once', 'deny'],
        } } }),
    ],
  }],
  loose: [card({ id: 'odd', name: 'Sort the photos', kind: 'single', preset: 'general', avatar: 'general', leader: false })],
  ...over,
})

let cleanup: (() => void) | undefined
const NativeMutationObserver = globalThis.MutationObserver
async function flush() {
  for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() }
  // What is loaded when it is first needed (the dialogs) is there by then.
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
function pointer(element: Element | null, type: string, x: number, y: number) {
  expect(element).toBeTruthy()
  const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y, button: 0 })
  Object.defineProperty(event, 'pointerId', { value: 1 })
  element!.dispatchEvent(event)
}
/** Every territory is 400 by 300 at the top left; the gate is the 100 by 40 at its bottom right. */
function sized() {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const gate = this.hasAttribute('data-colony-gate')
    const box = gate ? { left: 300, top: 260, width: 100, height: 40 } : { left: 0, top: 0, width: 400, height: 300 }
    return { ...box, right: box.left + box.width, bottom: box.top + box.height, x: box.left, y: box.top, toJSON: () => box } as DOMRect
  })
}

beforeEach(() => {
  vi.stubGlobal('MutationObserver', class extends NativeMutationObserver {
    constructor(callback: MutationCallback) { super(callback); markRaw(this) }
  })
  // Reset, not only cleared: an answer queued for a test that never asked for it is not the next one's.
  vi.resetAllMocks()
  localStorage.clear()
  api.getBoard.mockResolvedValue(board())
  api.getTimelineStart.mockResolvedValue({ after: '', latest: 'e9.m0' })
  // All of it at first, and nothing new whenever it is asked again.
  api.getTimeline.mockResolvedValue({ entries: [], next: 'e9.m0', more: false, latest: 'e9.m0' })
  api.getTimeline.mockResolvedValueOnce({ entries: story, next: 'e9.m0', more: false, latest: 'e9.m0' })
  api.getWaits.mockResolvedValue({ waits: [], more: false })
})
afterEach(() => {
  cleanup?.(); cleanup = undefined; document.body.innerHTML = ''
  vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals()
})

const names: Record<string, string> = { lead: 'Blog', scout: 'Scout' }
const nameOf = (id: string | null | undefined) => names[id ?? ''] ?? 'a familiar'

describe('how the familiars stood at a moment of what happened', () => {
  it('is worked out from the entries up to it, one that asks the user waiting until it goes on', () => {
    expect([...sceneAt(story, 1)].map(([id, at]) => [id, at.look])).toEqual([['lead', 'working']])
    // The scout is there from the moment it is sent out, working once it started, idle once it handed back.
    expect(sceneAt(story, 2).get('scout')?.look).toBe('idle')
    expect(sceneAt(story, 4).get('scout')?.look).toBe('working')
    expect(sceneAt(story, 6).get('scout')?.look).toBe('idle')
    const asking = sceneAt(story, 7).get('lead')
    expect([asking?.look, asking?.said]).toEqual(['waiting_user', 'Overwrite the old theme?'])
    // What was asked through a tool is not recorded as answered: going on says it was.
    expect(sceneAt(story, 8).get('lead')?.look).toBe('working')
    // A kind the page does not know changes nothing.
    expect(sceneAt(story, 9).get('lead')?.look).toBe('working')
    expect(sceneAt(story, 10).get('lead')?.look).toBe('idle')
    expect(sceneAt([], 3).size).toBe(0)
  })

  it('keeps what a familiar said above it for a while and no longer', () => {
    expect(sceneAt(story, 5).get('scout')?.said).toBe('They are in themes/old')
    expect(sceneAt(story, 6).get('scout')?.said).toBe('They are in themes/old')
    // Seventy seconds on it is gone; what the user is asked stays for as long as it waits.
    expect(story[7].at - story[5].at).toBeGreaterThan(SAID_SECONDS)
    expect(sceneAt(story, 7).get('scout')?.said).toBe('')
    expect(sceneAt(story, 7).get('lead')?.said).toBe('Overwrite the old theme?')
    // Still waiting ten minutes on, while others went about their work: it is still what it asks.
    const waitingLong = [
      entry('asked_user', { at: 100, text: 'Which day?', peer: { type: 'user' } }),
      entry('started', { at: 700, session_id: 'other', commission_id: 'c9' }),
    ]
    expect(sceneAt(waitingLong, 1).get('lead')).toEqual({ look: 'waiting_user', said: 'Which day?', saidAt: 100 })
  })

  it('knows where each session is first named, and who is named that the board does not hold', () => {
    expect([...firstSeen(story, true)]).toEqual([['lead', 0], ['scout', 2]])
    // Entries that begin later than the record: only the one that was started among them is known to be new.
    expect([...firstSeen(story, false)]).toEqual([['scout', 2]])
    expect(ghostsOf(story, new Set(['lead']))).toEqual([
      { id: 'scout', name: 'Find the templates', preset: 'scout', project: 'Blog', parent: 'lead' },
    ])
    expect(ghostsOf(story, new Set(['lead', 'scout']))).toEqual([])
  })

  it('says an entry in a line, by its outcome where it has one, and names a kind it has no words for', () => {
    const i18n = createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words } } })
    const { t, te } = i18n.global
    const line = (index: number) => describeEntry(story[index], nameOf, t, te)
    expect(line(0)).toBe('Blog was given: Move the blog')
    expect(line(2)).toBe('Blog sent out Scout')
    expect(line(5)).toBe('Scout told Blog: They are in themes/old')
    expect(line(6)).toBe('Scout is done and handed its work back')
    expect(line(7)).toBe('Blog asks you: Overwrite the old theme?')
    expect(line(10)).toBe('Blog is done')
    expect(line(9)).toBe('Blog · teams_joined · a kind this page was not told of')
    // A request for approval is recorded by its kind: told in words, with the server where it names one.
    const approval = (text: string) => describeEntry(entry('asked_user', { text, detail: { question_kind: 'permission' } }), nameOf, t, te)
    expect(approval('run_command')).toBe(`Blog asks you: ${words.runtime.permissions.categories.run_command}`)
    expect(approval('mcp_server:notes')).toBe(`Blog asks you: ${words.ask.categories.mcp_server}: notes`)
    expect(approval('brand_new')).toBe('Blog asks you: brand_new')
    // With nothing said the colon goes too.
    expect(describeEntry(entry('reported'), nameOf, t, te)).toBe('Blog reports')
  })
})

describe('where the familiars of a territory stand', () => {
  it('puts a single leader in the middle, its own around it, and whoever one started beside that one', () => {
    const spots = defaultSpots([
      { id: 'lead', leader: true, parent: null }, { id: 'a', leader: false, parent: null }, { id: 'b', leader: false, parent: null },
      { id: 'a1', leader: false, parent: 'a' }, { id: 'l1', leader: false, parent: 'lead' }, { id: 'l2', leader: false, parent: 'lead' },
    ])
    expect(spots.lead).toEqual({ x: 0.5, y: 0.52 })
    const far = (p: string, q: string) => Math.hypot(spots[p].x - spots[q].x, spots[p].y - spots[q].y)
    // Closer to the one that started it than to anyone else of the territory's own.
    expect(far('a1', 'a')).toBeLessThan(far('a1', 'b'))
    expect(far('a1', 'a')).toBeLessThan(far('a1', 'lead'))
    expect(far('l1', 'lead')).toBeLessThan(far('l1', 'a'))
    expect(far('l1', 'lead')).toBeLessThan(0.35)
    expect(far('l2', 'lead')).toBeLessThan(0.35)
    expect(far('a1', 'a')).toBeLessThan(0.3)
    // Beside its starter, not between it and the leader, where it would look like one of the leader's.
    expect(far('a1', 'lead')).toBeGreaterThan(far('a', 'lead') * 0.9)
    // The two the leader started stand below it, one on either side.
    expect(spots.l1.x).toBeLessThan(spots.lead.x)
    expect(spots.l2.x).toBeGreaterThan(spots.lead.x)
    expect(Math.min(spots.l1.y, spots.l2.y)).toBeGreaterThan(spots.lead.y)
    for (const spot of Object.values(spots)) expect(inTerritory(spot)).toBe(true)
    // No two on one spot.
    expect(new Set(Object.values(spots).map(spot => `${spot.x.toFixed(3)},${spot.y.toFixed(3)}`)).size).toBe(6)
  })

  it('puts one that another started beside it where nobody stands yet', () => {
    // Four of its own around a leader: the fourth stands right below it, where the one the leader started would.
    const spots = defaultSpots([
      { id: 'lead', leader: true, parent: null }, ...['a', 'b', 'c', 'd'].map(id => ({ id, leader: false, parent: null })),
      { id: 'l1', leader: false, parent: 'lead' },
    ])
    const ids = Object.keys(spots)
    expect(ids.length).toBe(6)
    for (const p of ids) {
      for (const q of ids) {
        if (p >= q) continue
        // Far enough apart to be told apart: a tenth of the width, or an eighth of the height.
        expect(Math.hypot((spots[p].x - spots[q].x) / 0.1, (spots[p].y - spots[q].y) / 0.12), `${p} and ${q}`).toBeGreaterThanOrEqual(1)
      }
    }
    // Still beside the one that started it, and within the territory.
    expect(Math.hypot(spots.l1.x - spots.lead.x, spots.l1.y - spots.lead.y)).toBeLessThan(0.35)
    expect(inTerritory(spots.l1)).toBe(true)
  })

  it('keeps nothing, and does not fail, where the page may not use the store of the browser', () => {
    const own = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new DOMException('denied', 'SecurityError') } })
    try {
      expect(keptSpots('project:Blog')).toEqual({})
      expect(() => keepSpots('project:Blog', { lead: { x: 0.3, y: 0.4 } }, ['lead'])).not.toThrow()
    } finally {
      if (own) Object.defineProperty(globalThis, 'localStorage', own)
      else delete (globalThis as { localStorage?: Storage }).localStorage
    }
  })

  it('needs no leader: one alone stands in the middle, several stand around it', () => {
    expect(defaultSpots([{ id: 'only', leader: false, parent: null }]).only).toEqual({ x: 0.5, y: 0.52 })
    const three = defaultSpots([1, 2, 3].map(n => ({ id: `m${n}`, leader: false, parent: null })))
    expect(Object.values(three).some(spot => spot.x === 0.5 && spot.y === 0.52)).toBe(false)
    // Two leaders are no single one either: neither stands in the middle.
    const two = defaultSpots([{ id: 'x', leader: true, parent: null }, { id: 'y', leader: true, parent: null }])
    expect(two.x).not.toEqual(two.y)
    expect([two.x, two.y].some(spot => spot.x === 0.5 && spot.y === 0.52)).toBe(false)
    // Twenty of its own still all stand within it, each on a spot of its own.
    const many = defaultSpots(Array.from({ length: 20 }, (_, n) => ({ id: `m${n}`, leader: false, parent: null })))
    expect(Object.values(many).every(inTerritory)).toBe(true)
    expect(new Set(Object.values(many).map(spot => `${spot.x.toFixed(3)},${spot.y.toFixed(3)}`)).size).toBe(20)
    // One whose starter is not among them stands with the territory's own.
    expect(inTerritory(defaultSpots([{ id: 'orphan', leader: false, parent: 'gone' }]).orphan)).toBe(true)
  })

  it('brings a spot outside back to the edge and leaves one inside where it is', () => {
    expect(intoTerritory({ x: 0.5, y: 0.5 })).toEqual({ x: 0.5, y: 0.5 })
    const edge = intoTerritory({ x: 3, y: 0.52 })
    expect(edge.x).toBeCloseTo(0.9)
    expect(inTerritory({ x: edge.x - 0.001, y: edge.y })).toBe(true)
    expect(inTerritory({ x: 0.99, y: 0.99 })).toBe(false)
    expect(territorySize(1).width).toBeLessThan(territorySize(6).width)
    expect(territorySize(500)).toEqual({ width: 760, height: 460 })
  })

  it('keeps where the user put them by territory, and forgets those that are gone', () => {
    keepSpots('project:Blog', { lead: { x: 0.3, y: 0.4 }, gone: { x: 0.6, y: 0.6 } }, ['lead', 'kid'])
    keepSpots('loose', { odd: { x: 0.5, y: 0.5 } }, ['odd'])
    expect(keptSpots('project:Blog')).toEqual({ lead: { x: 0.3, y: 0.4 } })
    expect(keptSpots('loose')).toEqual({ odd: { x: 0.5, y: 0.5 } })
    expect(keptSpots('project:Other')).toEqual({})
    // Nothing left of a territory: it is forgotten, the others stay.
    keepSpots('project:Blog', {}, ['lead'])
    expect(keptSpots('project:Blog')).toEqual({})
    expect(keptSpots('loose')).toEqual({ odd: { x: 0.5, y: 0.5 } })
    expect(Object.keys(JSON.parse(localStorage.getItem('neko-power-colony-spots') ?? '{}'))).toEqual(['loose'])
    // What is in the storage is not trusted: no spot, a spot outside, something else altogether.
    localStorage.setItem('neko-power-colony-spots', JSON.stringify({ a: { one: { x: 'left', y: 1 }, two: { x: 9, y: 0.52 } }, b: 3 }))
    expect(keptSpots('a').one).toBeUndefined()
    expect(keptSpots('a').two.x).toBeCloseTo(0.9)
    expect(keptSpots('b')).toEqual({})
    localStorage.setItem('neko-power-colony-spots', '{not json')
    expect(keptSpots('a')).toEqual({})
  })
})

const member = (over: Partial<ColonyMember> = {}): ColonyMember => ({
  id: 'lead', name: 'Blog', avatar: 'boss', leader: true, parent: null, look: 'working', said: '', hidden: false, ...over,
})
const territory = (members: ColonyMember[]): Territory => ({ key: 'project:Blog', name: 'Blog', loose: false, members })
const pair = () => territory([
  member(), member({ id: 'kid', name: 'Kid', avatar: 'worker', leader: false, parent: 'lead', look: 'waiting_user' }),
  member({ id: 'other', name: 'Other', avatar: 'general', leader: false, look: 'idle', said: 'found it' }),
])

describe('a territory of the colony', () => {
  it('draws each familiar by how it stands, with what it said above it, and none that is not there yet', async () => {
    const { state } = await mount(ColonyTerritory, { territory: pair() })
    expect([...document.querySelectorAll('[data-cat]')].map(element => [element.getAttribute('data-cat'), element.getAttribute('data-look')])).toEqual([
      ['lead', 'working'], ['kid', 'waiting_user'], ['other', 'idle'],
    ])
    expect(cat('kid')?.querySelector('.bubble')?.textContent).toBe(words.colony.waitingBubble)
    expect(cat('other')?.querySelector('.bubble')?.textContent).toBe('found it')
    expect(cat('lead')?.querySelector('.bubble')).toBeNull()
    // Nothing is drawn between them while none is held.
    expect(byTest('colony-lines')).toBeNull()
    const now = pair()
    now.members[2].hidden = true
    state.props = { territory: now }
    await flush()
    expect(cat('other')).toBeNull()
  })

  it('opens a familiar that is pressed without being moved', async () => {
    sized()
    const opened: string[] = []
    await mount(ColonyTerritory, { territory: pair(), onOpen: (id: string) => opened.push(id) })
    pointer(cat('kid'), 'pointerdown', 100, 100)
    pointer(cat('kid'), 'pointermove', 102, 101)
    pointer(cat('kid'), 'pointerup', 102, 101)
    cat('kid')!.click()
    expect(opened).toEqual(['kid'])
    expect(keptSpots('project:Blog')).toEqual({})
  })

  it('moves a familiar that is dragged, shows whom it has to do with only while it is held, and keeps where it was put', async () => {
    sized()
    const opened: string[] = []
    const holding: boolean[] = []
    await mount(ColonyTerritory, {
      territory: pair(), links: { lead: ['other'] },
      onOpen: (id: string) => opened.push(id), onHolding: (held: boolean) => holding.push(held),
    })
    pointer(cat('lead'), 'pointerdown', 200, 156)
    pointer(cat('lead'), 'pointermove', 120, 150)
    await flush()
    // To the one it started and the one it waits for: two lines, and they go when it is let go.
    expect(byTest('colony-lines')?.querySelectorAll('line').length).toBe(2)
    expect(holding).toEqual([true])
    pointer(cat('lead'), 'pointerup', 120, 150)
    cat('lead')!.click()
    await flush()
    expect(byTest('colony-lines')).toBeNull()
    expect(holding).toEqual([true, false])
    // The click that ends a drag opens nothing.
    expect(opened).toEqual([])
    expect(keptSpots('project:Blog')).toEqual({ lead: { x: 0.3, y: 0.5 } })
    expect([cat('lead')!.style.left, cat('lead')!.style.top]).toEqual(['30%', '50%'])

    // One that another started: a line to that one, and none to the third.
    pointer(cat('kid'), 'pointerdown', 100, 200)
    pointer(cat('kid'), 'pointermove', 110, 215)
    await flush()
    expect(byTest('colony-lines')?.querySelectorAll('line').length).toBe(1)
    pointer(cat('kid'), 'pointerup', 110, 215)
    // No click came after this drag (the pointer was let go elsewhere): the next one opens it all the same.
    await new Promise(resolve => setTimeout(resolve, 5))
    cat('kid')!.click()
    expect(opened).toEqual(['kid'])
  })

  it('leaves a familiar dropped outside at the edge of its territory', async () => {
    sized()
    await mount(ColonyTerritory, { territory: pair() })
    pointer(cat('other'), 'pointerdown', 200, 150)
    pointer(cat('other'), 'pointermove', 5000, 156)
    pointer(cat('other'), 'pointerup', 5000, 156)
    await flush()
    expect(parseFloat(cat('other')!.style.left)).toBeLessThan(91)
    const kept = keptSpots('project:Blog').other
    expect(kept.x).toBeCloseTo(0.9)
    expect(inTerritory({ x: kept.x - 0.001, y: kept.y })).toBe(true)
  })

  it('sends the territory away when a familiar is dropped at the gate, where that is allowed', async () => {
    sized()
    const gate = document.createElement('div')
    gate.setAttribute('data-colony-gate', '')
    document.body.append(gate)
    let parted = 0
    const { state } = await mount(ColonyTerritory, { territory: pair(), canDismiss: true, onDismiss: () => { parted += 1 } })
    pointer(cat('kid'), 'pointerdown', 100, 100)
    pointer(cat('kid'), 'pointermove', 350, 280)
    pointer(cat('kid'), 'pointerup', 350, 280)
    await flush()
    expect(parted).toBe(1)
    // It goes back to where it stood: nothing is kept of the drag.
    expect(keptSpots('project:Blog')).toEqual({})
    // Dropped anywhere else it is only put there.
    pointer(cat('kid'), 'pointerdown', 100, 100)
    pointer(cat('kid'), 'pointermove', 160, 150)
    pointer(cat('kid'), 'pointerup', 160, 150)
    await flush()
    expect(parted).toBe(1)
    expect(keptSpots('project:Blog').kid).toEqual({ x: 0.4, y: 0.5 })

    state.props = { territory: pair(), canDismiss: false, onDismiss: () => { parted += 1 } }
    await flush()
    pointer(cat('kid'), 'pointerdown', 100, 100)
    pointer(cat('kid'), 'pointermove', 350, 280)
    pointer(cat('kid'), 'pointerup', 350, 280)
    await flush()
    expect(parted).toBe(1)
    expect(keptSpots('project:Blog').kid).toBeTruthy()
  })

  it('moves the familiar that has the focus with the arrow keys, and puts all back when tidied', async () => {
    const { state } = await mount(ColonyTerritory, { territory: pair(), tidy: 0 })
    const before = cat('lead')!.style.left
    cat('lead')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    await flush()
    expect(cat('lead')!.style.left).not.toBe(before)
    expect(keptSpots('project:Blog').lead.x).toBeCloseTo(0.47)
    cat('lead')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }))
    expect(keptSpots('project:Blog').lead.x).toBeCloseTo(0.47)
    state.props = { territory: pair(), tidy: 1 }
    await flush()
    expect(cat('lead')!.style.left).toBe(before)
    expect(keptSpots('project:Blog')).toEqual({})
  })

  it('lets only the idle stroll, never out of the territory, and not at all when motion is to be reduced', async () => {
    vi.useFakeTimers()
    vi.spyOn(Math, 'random').mockReturnValue(0.9)
    await mount(ColonyTerritory, { territory: pair() })
    const stood = { lead: cat('lead')!.style.left, other: cat('other')!.style.left }
    await vi.advanceTimersByTimeAsync(3000)
    expect(cat('other')!.style.left).not.toBe(stood.other)
    expect(cat('lead')!.style.left).toBe(stood.lead)
    const left = parseFloat(cat('other')!.style.left) / 100
    const top = parseFloat(cat('other')!.style.top) / 100
    expect(inTerritory({ x: left, y: top })).toBe(true)

    cleanup?.()
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    await mount(ColonyTerritory, { territory: pair() })
    const still = cat('other')!.style.left
    await vi.advanceTimersByTimeAsync(9000)
    expect(cat('other')!.style.left).toBe(still)
  })
})

describe('the list of what happened', () => {
  it('lists the entries a line each, marks the moment shown, and asks to be shown the one pressed', async () => {
    const picked: number[] = []
    const { state } = await mount(ActivityFeed, { entries: story, current: null, nameOf, onPick: (index: number) => picked.push(index) })
    const rows = [...document.querySelectorAll('.row')]
    expect(rows.length).toBe(story.length)
    expect(rows[0].textContent).toContain('Blog was given: Move the blog')
    expect(document.querySelector('.row.now')).toBeNull()
    ;(rows[2].querySelector('button') as HTMLElement).click()
    expect(picked).toEqual([2])
    state.props = { entries: story, current: 2, nameOf }
    await flush()
    expect(document.querySelector('.row.now')?.textContent).toContain('Blog sent out Scout')
    expect(document.querySelectorAll('.row.later').length).toBe(story.length - 3)
  })

  it('says so while nothing is there, and draws only the latest of very many', async () => {
    const { state } = await mount(ActivityFeed, { entries: [], current: null, nameOf, loading: true })
    expect(byTest('activity-feed')?.textContent).toContain(words.activity.loading)
    state.props = { entries: [], current: null, nameOf, loading: false }
    await flush()
    expect(byTest('activity-feed')?.textContent).toContain(words.activity.nothing)
    const many = Array.from({ length: 450 }, (_, n) => entry('started', { at: n, cursor: `e${n}.m0` }))
    state.props = { entries: many, current: null, nameOf }
    await flush()
    expect(document.querySelectorAll('.row').length).toBe(200)
    expect(byTest('activity-feed')?.textContent).toContain(words.activity.earlierLeftOut.replace('{count}', '250'))
    // The ones before them are drawn when asked for, two hundred at a time, until all are there.
    byTest('activity-earlier')!.click()
    await flush()
    expect(document.querySelectorAll('.row').length).toBe(400)
    expect(byTest('activity-feed')?.textContent).toContain(words.activity.earlierLeftOut.replace('{count}', '50'))
    byTest('activity-earlier')!.click()
    await flush()
    expect(document.querySelectorAll('.row').length).toBe(450)
    expect(byTest('activity-earlier')).toBeNull()
  })
})

describe('the colony on the home page', () => {
  it('shows the projects as territories with who waits for the user said first', async () => {
    await mount(FamiliarColony)
    expect([...document.querySelectorAll('[data-territory]')].map(element => element.getAttribute('data-territory'))).toEqual(['project:Blog', 'loose'])
    expect(cat('lead')?.getAttribute('data-look')).toBe('working')
    expect(cat('kid')?.getAttribute('data-look')).toBe('waiting_user')
    // What it said long ago is not above it now.
    expect(cat('lead')?.querySelector('.bubble')).toBeNull()
    const waiting = byTest('colony-waiting')
    // One that another familiar started is called by what kind it is: its own name is the starter's with a number.
    expect(waiting?.textContent).toContain(words.home.presets.worker)
    expect(cat('kid')?.textContent).toContain(words.home.presets.worker)
    expect(cat('lead')?.textContent).toContain('Blog')
    expect(waiting?.textContent).toContain('npm run build -- --mode production')
    expect(byTest('board-tally')?.textContent).toContain(words.home.waiting.replace('{count}', '1'))
    // It began where the host said to, and read on from there.
    expect(api.getTimeline).toHaveBeenCalledWith({ project: null, after: undefined, limit: 1000 })
    expect(document.querySelectorAll('.row').length).toBe(story.length)
    // Where it began is the beginning: there is nothing earlier to offer.
    expect(byTest('replay-whole')).toBeNull()
  })

  it('calls a familiar by the title it earned, and shows what one said lately only while it is fresh', async () => {
    const titled = board()
    titled.projects![0].members[1].title = 'Build checker'
    api.getBoard.mockResolvedValue(titled)
    // Said long ago by the clock on the wall, though it is the last thing that happened.
    api.getTimeline.mockReset()
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e1.m0', more: false, latest: 'e1.m0' })
    api.getTimeline.mockResolvedValueOnce({ next: 'e1.m0', more: false, latest: 'e1.m0', entries: [entry('reported', { at: 100, text: 'half way' })] })
    await mount(FamiliarColony)
    expect(cat('kid')?.textContent).toContain('Build checker')
    expect(byTest('colony-waiting')?.textContent).toContain('Build checker')
    expect(cat('lead')?.querySelector('.bubble')).toBeNull()
    // Replayed to the moment it was said, it is above it.
    ;(document.querySelector('.row button') as HTMLElement).click()
    await flush()
    expect(cat('lead')?.querySelector('.bubble')?.textContent).toBe('half way')
  })

  it('does not show a familiar of the board at a moment before it was first named', async () => {
    const withScout = board()
    withScout.projects![0].members.push(card({ id: 'scout', name: 'Blog/agent-1', kind: 'child', preset: 'scout', avatar: 'scout', leader: false, parent: 'lead' }))
    api.getBoard.mockResolvedValue(withScout)
    await mount(FamiliarColony)
    expect(cat('scout')).toBeTruthy()
    const slider = byTest('replay-slider') as HTMLInputElement
    slider.value = '1'
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    expect(cat('scout')).toBeNull()
    slider.value = '4'
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    expect(cat('scout')?.getAttribute('data-look')).toBe('working')
  })

  it('shows one that stood by before the entries begin, where they do not begin at the beginning', async () => {
    // Only the last stretch was read: the first of it that names the leader is something it said.
    api.getTimelineStart.mockResolvedValue({ after: 'e50.m2', latest: 'e99.m9' })
    api.getTimeline.mockReset()
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e99.m9', more: false, latest: 'e99.m9' })
    api.getTimeline.mockResolvedValueOnce({ next: 'e99.m9', more: false, latest: 'e99.m9', entries: [
      entry('reported', { session_id: 'kid', at: 10, cursor: 'e60.m0', text: 'the template is in' }),
      entry('reported', { at: 20, cursor: 'e61.m0', text: 'half way' }),
    ] })
    await mount(FamiliarColony)
    const slider = byTest('replay-slider') as HTMLInputElement
    slider.value = '0'
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    // Not named yet at that moment, and there all the same.
    expect(cat('lead')?.getAttribute('data-look')).toBe('idle')
    expect(cat('kid')).toBeTruthy()
  })

  it('answers a familiar that waits from the colony, with what it asks shown whole', async () => {
    api.answerCommission.mockResolvedValue({ success: true })
    await mount(FamiliarColony)
    ;(document.querySelector('[data-waiting="kid"] button') as HTMLElement).click()
    await flush()
    expect(byTest('ask-whole')?.textContent).toBe('npm run build -- --mode production')
    ;(document.querySelector('[data-choice="once"]') as HTMLElement).click()
    await flush()
    expect(api.answerCommission).toHaveBeenCalledWith('c3', { choice: 'once', text: undefined, question_id: 'q1' })
    expect(api.getBoard).toHaveBeenCalledTimes(2)
  })

  it('closes the question of one that waits no more, and does not open it by itself when it asks again', async () => {
    vi.useFakeTimers()
    await mount(FamiliarColony)
    ;(document.querySelector('[data-waiting="kid"] button') as HTMLElement).click()
    await flush()
    expect(byTest('ask-whole')).toBeTruthy()
    // Answered elsewhere: it works on.
    const went = board({ revision: 'r2' })
    went.projects![0].members[1] = card({
      ...went.projects![0].members[1], state: 'working', commission: { id: 'c3', title: 'Swap the template', status: 'working', started_at: 0 },
    })
    api.getBoard.mockResolvedValue(went)
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(byTest('ask-whole')).toBeNull()
    // It asks the next thing: said among those that wait, and opened only when the user asks for it.
    api.getBoard.mockResolvedValue(board({ revision: 'r3' }))
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    expect(document.querySelector('[data-waiting="kid"]')).toBeTruthy()
    expect(byTest('ask-whole')).toBeNull()
  })

  it('replays what happened: each familiar as the entries leave it, those gone since back where they were', async () => {
    await mount(FamiliarColony)
    const slider = byTest('replay-slider') as HTMLInputElement
    const to = async (index: number) => {
      slider.value = String(index)
      slider.dispatchEvent(new Event('input', { bubbles: true }))
      await flush()
    }
    await to(1)
    expect(cat('lead')?.getAttribute('data-look')).toBe('working')
    // The scout is not on the board any more and was not sent out yet; the kid the entries never name stands by.
    expect(cat('scout')).toBeNull()
    expect(cat('kid')?.getAttribute('data-look')).toBe('idle')
    // Nobody is asked to answer what is long over.
    expect(byTest('colony-waiting')).toBeNull()
    expect(byTest('replay-moment')?.textContent).toContain(words.colony.at.split('{')[0].trim())

    await to(4)
    expect(cat('scout')?.getAttribute('data-look')).toBe('working')
    expect(cat('scout')?.textContent).toContain('Find the templates')
    await to(7)
    expect(cat('lead')?.getAttribute('data-look')).toBe('waiting_user')
    expect(document.querySelector('.row.now')?.textContent).toContain('Blog asks you')

    byTest('replay-now')!.click()
    await flush()
    expect(cat('scout')).toBeNull()
    expect(cat('kid')?.getAttribute('data-look')).toBe('waiting_user')
    expect(byTest('colony-waiting')).toBeTruthy()
    expect(byTest('replay-moment')?.textContent).toBe(words.colony.live)
  })

  it('stays at the moment that is replayed when the oldest entries are let go for new ones', async () => {
    vi.useFakeTimers()
    const step = (n: number) => entry('reported', { at: n, cursor: `e${n}.m0`, text: `step ${n}` })
    api.getTimeline.mockReset()
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e9.m0', more: false, latest: 'e9.m0' })
    // As many as are held at most, then five more with the next read.
    api.getTimeline.mockResolvedValueOnce({ entries: Array.from({ length: 3000 }, (_, n) => step(n)), next: 'e9.m0', more: false, latest: 'e9.m0' })
    await mount(FamiliarColony)
    const slider = byTest('replay-slider') as HTMLInputElement
    slider.value = '100'
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    expect(document.querySelector('.row.now')?.textContent).toContain('step 100')
    api.getTimeline.mockResolvedValueOnce({ entries: Array.from({ length: 5 }, (_, n) => step(3000 + n)), next: 'e9.m0', more: false, latest: 'e9.m0' })
    await vi.advanceTimersByTimeAsync(2100)
    await flush()
    // Five were let go at the front: the same entry is five places further to the front.
    expect(slider.value).toBe('95')
    expect(document.querySelector('.row.now')?.textContent).toContain('step 100')
  })

  it('plays through to the end and comes back to now', async () => {
    vi.useFakeTimers()
    await mount(FamiliarColony)
    byTest('replay-play')!.click()
    await flush()
    expect(byTest('replay-play')?.textContent?.trim()).toBe(words.colony.pause)
    expect(cat('kid')?.getAttribute('data-look')).toBe('idle')
    await vi.advanceTimersByTimeAsync(700 * (story.length + 1))
    await flush()
    expect(byTest('replay-play')?.textContent?.trim()).toBe(words.colony.replay)
    expect(byTest('replay-moment')?.textContent).toBe(words.colony.live)
  })

  it('sends a project away only once the user said so, and says why when it stays', async () => {
    sized()
    await mount(FamiliarColony)
    pointer(cat('lead'), 'pointerdown', 100, 100)
    pointer(cat('lead'), 'pointermove', 350, 280)
    await flush()
    // Whom it waits for is read when it is picked up, and the gate shows.
    expect(api.getWaits).toHaveBeenCalledTimes(1)
    expect(byTest('colony-gate')?.classList.contains('on')).toBe(true)
    pointer(cat('lead'), 'pointerup', 350, 280)
    await flush()
    expect(api.archiveProject).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain(words.colony.partTitle.replace('{project}', 'Blog'))

    api.archiveProject.mockRejectedValueOnce({ code: 'project_busy', response: { status: 409 } })
    byTest('colony-part-confirm')!.click()
    await flush()
    expect(api.archiveProject).toHaveBeenCalledWith('Blog')
    expect(byTest('colony-part-error')?.textContent).toContain(words.errors.project_busy)

    api.archiveProject.mockResolvedValueOnce({ project: {} })
    api.getBoard.mockResolvedValue(board({ projects: [] }))
    byTest('colony-part-confirm')!.click()
    await flush()
    expect(document.querySelector('[data-territory="project:Blog"]')).toBeNull()
    expect(document.querySelector('[data-territory="loose"]')).toBeTruthy()
  })

  it('does not offer to send the loose ones away, nor anything while what happened is replayed', async () => {
    sized()
    await mount(FamiliarColony)
    pointer(cat('odd'), 'pointerdown', 100, 100)
    pointer(cat('odd'), 'pointermove', 350, 280)
    pointer(cat('odd'), 'pointerup', 350, 280)
    await flush()
    expect(document.body.textContent).not.toContain(words.colony.partText)
    // Nor a project while a moment of the past is shown: one dropped at the gate then is only put down.
    const slider = byTest('replay-slider') as HTMLInputElement
    slider.value = '2'
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    pointer(cat('lead'), 'pointerdown', 100, 100)
    pointer(cat('lead'), 'pointermove', 350, 280)
    pointer(cat('lead'), 'pointerup', 350, 280)
    await flush()
    expect(document.body.textContent).not.toContain(words.colony.partText)
    expect(api.archiveProject).not.toHaveBeenCalled()
  })

  it('says so when there are no familiars, and when the service has no board', async () => {
    api.getBoard.mockResolvedValue(board({ projects: [], loose: [] }))
    await mount(FamiliarColony)
    expect(byTest('board-empty')?.textContent).toContain(words.home.empty)
    expect(byTest('colony-field')).toBeNull()
    // What happened before can still be looked at: those who took part stand there while it is replayed.
    expect(document.querySelectorAll('.row').length).toBe(story.length)
    const slider = byTest('replay-slider') as HTMLInputElement
    slider.value = '4'
    slider.dispatchEvent(new Event('input', { bubbles: true }))
    await flush()
    expect(cat('scout')?.getAttribute('data-look')).toBe('working')
    expect(byTest('board-empty')).toBeNull()
    cleanup?.()
    // Nobody and nothing that happened: nothing to replay either.
    api.getTimeline.mockReset()
    api.getTimeline.mockResolvedValue({ entries: [], next: 'e0.m0', more: false, latest: 'e0.m0' })
    await mount(FamiliarColony)
    expect(byTest('board-empty')).toBeTruthy()
    expect(byTest('colony-replay')).toBeNull()
    cleanup?.()
    api.getBoard.mockRejectedValue({ response: { status: 404 } })
    await mount(FamiliarColony)
    expect(byTest('board-error')?.textContent).toBe(words.home.unavailable)
  })

  it('reads the whole of what happened when asked to, having begun with the last stretch of it', async () => {
    api.getTimelineStart.mockResolvedValue({ after: 'e50.m2', latest: 'e99.m9' })
    await mount(FamiliarColony)
    expect(api.getTimeline).toHaveBeenCalledWith({ project: null, after: 'e50.m2', limit: 1000 })
    api.getTimeline.mockClear()
    api.getTimeline.mockResolvedValueOnce({ entries: story, next: 'e9.m0', more: false, latest: 'e9.m0' })
    byTest('replay-whole')!.click()
    await flush()
    expect(api.getTimeline).toHaveBeenCalledWith({ project: null, after: undefined, limit: 1000 })
    // All of it is held now: there is nothing more to offer.
    expect(document.querySelectorAll('.row').length).toBe(story.length)
    expect(byTest('replay-whole')).toBeNull()
  })
})
