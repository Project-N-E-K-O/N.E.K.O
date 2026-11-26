// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import type { Component } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import words from '@/i18n/power/en-US'
import type { BoardAsk, BoardCard } from '@/api/console'
import AskBox from './AskBox.vue'
import { SETTLE_MS, answerBody, askQuestions, askShape, describeAnswerError, rememberAsks, saysSomething } from './ask'
import AskBubble from './colony/AskBubble.vue'
import ColonyTerritory from './colony/ColonyTerritory.vue'
import type { ColonyMember } from './colony/ColonyTerritory.vue'
import FamiliarCard from './home/FamiliarCard.vue'
import { sendAnswer, useAskAnswers } from './useAskAnswers'

const api = vi.hoisted(() => ({
  answerCommission: vi.fn(),
  consoleErrorCode: (error: unknown) => (error as { code?: string })?.code ?? null,
}))
vi.mock('@/api/console', () => api)

let cleanup: Array<() => void> = []
async function flush() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() } }
async function mount(component: Component, props: Record<string, unknown> = {}) {
  const container = document.createElement('div')
  document.body.append(container)
  const state = reactive({ props })
  const app = createApp({ render: () => h(component, state.props) })
  app.use(ElementPlus)
  app.use(createI18n({ legacy: false, locale: 'en-US', messages: { 'en-US': { power: words } } }))
  app.mount(container)
  cleanup.push(() => { app.unmount(); container.remove() })
  await flush()
  return { container, state }
}
const t = (key: string) => key
const te = () => false
function typeInto(input: Element | null, value: string) {
  expect(input).toBeTruthy()
  ;(input as HTMLInputElement).value = value
  input!.dispatchEvent(new Event('input', { bubbles: true }))
}
const inputOf = (container: Element, testId: string) =>
  container.querySelector(`input[data-testid="${testId}"], [data-testid="${testId}"] input`)
async function press(element: Element | null | undefined) {
  expect(element).toBeTruthy()
  ;(element as HTMLElement).click()
  await flush()
}

const permission = (over: Partial<BoardAsk> = {}): BoardAsk => ({
  id: 'q1', kind: 'permission', category: 'run_command', detail: { command: 'npm run build' }, options: ['always', 'session', 'once', 'deny'], ...over,
})
const confirmation: BoardAsk = { id: 'q2', kind: 'confirmation', question: 'Send it to the group?' }
const oneQuestion: BoardAsk = {
  id: 'q3', kind: 'questions',
  questions: [{ id: 'theme', question: 'Which theme?', options: [{ label: 'Light', description: 'matches the logo' }, { label: 'Dark' }, 'System'] }],
}
const twoQuestions: BoardAsk = {
  id: 'q4', kind: 'questions',
  questions: [
    { id: 'same', question: 'Deploy where?', options: [{ label: 'Vercel' }, { label: 'Not now' }] },
    { id: 'same', question: 'Which tags?', options: ['a', 'b', 'Not now'], multi_select: true },
  ],
}
const inWords: BoardAsk = { id: 'q5', kind: 'report', question: 'Which file do I keep?' }

beforeEach(() => { api.answerCommission.mockReset() })
afterEach(() => {
  cleanup.forEach(done => done())
  cleanup = []
  vi.useRealTimers()
})

describe('how a question is answered', () => {
  it('tells the kinds of question apart, and names each question by where it stands', () => {
    expect([permission(), confirmation, oneQuestion, inWords].map(askShape)).toEqual(['permission', 'confirmation', 'questions', 'words'])
    // Questions that offer nothing to pick are answered in words.
    expect(askShape({ kind: 'questions', questions: [{ question: 'Why?' }] })).toBe('words')
    // By position: the model gave both the same id.
    expect(askQuestions(twoQuestions).map(item => [item.id, item.multi, item.options.map(option => option.label)])).toEqual([
      ['0', false, ['Vercel', 'Not now']], ['1', true, ['a', 'b', 'Not now']],
    ])
    expect(askQuestions(oneQuestion)[0].options).toEqual([
      { label: 'Light', description: 'matches the logo' }, { label: 'Dark', description: '' }, { label: 'System', description: '' },
    ])
  })

  it('sends an answer against the question that was shown, without what says nothing', () => {
    expect(answerBody({ choice: 'deny', note: '  use pnpm  ' }, permission())).toEqual({
      text: undefined, choice: 'deny', selected: undefined, note: 'use pnpm', question_id: 'q1',
    })
    expect(answerBody({ selected: { 0: ['Vercel'], 1: [] }, text: ' ' }, twoQuestions)).toEqual({
      text: undefined, choice: undefined, selected: { 0: ['Vercel'] }, note: undefined, question_id: 'q4',
    })
    expect([{}, { text: '  ' }, { selected: { 0: [] } }, { note: 'only a note' }].map(saysSomething)).toEqual([false, false, false, false])
    expect([{ choice: 'once' }, { text: 'x' }, { selected: { 0: ['a'] } }].map(saysSomething)).toEqual([true, true, true])
  })

  it('says of an answer that came too late that the question is another by now', async () => {
    expect(describeAnswerError({ code: 'not_taken' }, t, te)).toBe('power.ask.stale')
    // Any other refusal in the words the pages have for it.
    expect(describeAnswerError({ response: { status: 500 } }, t, te)).toBe('power.errors.generic')
    // The answer is in; the sentence that was to go with it was not taken.
    api.answerCommission.mockResolvedValue({ success: true, note_taken: false })
    expect(await sendAnswer('c1', { choice: 'deny', note: 'x' }, permission(), t)).toBe('power.ask.noteLost')
    expect(api.answerCommission).toHaveBeenCalledWith('c1', { text: undefined, choice: 'deny', selected: undefined, note: 'x', question_id: 'q1' })
    api.answerCommission.mockResolvedValue({ success: true })
    expect(await sendAnswer('c1', { choice: 'once' }, permission(), t)).toBe('')
  })
})

describe('the box a question is answered in', () => {
  it('takes a sentence along with an approval, and never the sentence alone', async () => {
    const answers: unknown[] = []
    const { container } = await mount(AskBox, { ask: permission(), busy: '', onAnswer: (given: unknown) => answers.push(given) })
    typeInto(inputOf(container, 'familiar-note'), 'use pnpm instead')
    await flush()
    // Words alone would be read as a refusal: Enter sends nothing, and there is no button for them.
    inputOf(container, 'familiar-note')!.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
    expect(answers).toEqual([])
    expect(container.querySelector('[data-testid="familiar-answer"]')).toBeNull()
    await press(container.querySelector('[data-choice="deny"]'))
    expect(answers).toEqual([{ choice: 'deny', note: 'use pnpm instead' }])
  })

  it('has no line for a sentence where a plugin does the work and no familiar could take one', async () => {
    const answers: unknown[] = []
    const { container } = await mount(AskBox, { ask: permission(), busy: '', noNote: true, onAnswer: (given: unknown) => answers.push(given) })
    expect(inputOf(container, 'familiar-note')).toBeNull()
    await press(container.querySelector('[data-choice="once"]'))
    expect(answers).toEqual([{ choice: 'once', note: undefined }])
  })

  it('answers a plugin that wants confirmation with yes or no, with words beside them', async () => {
    const answers: unknown[] = []
    const { container } = await mount(AskBox, { ask: confirmation, busy: '', onAnswer: (given: unknown) => answers.push(given) })
    expect(container.querySelector('[data-confirm="yes"]')?.textContent).toContain(words.ask.confirm.yes)
    await press(container.querySelector('[data-confirm="yes"]'))
    typeInto(inputOf(container, 'familiar-reply'), 'only the first two')
    await flush()
    await press(container.querySelector('[data-confirm="no"]'))
    await press(container.querySelector('[data-testid="familiar-answer"]'))
    expect(answers).toEqual([
      { choice: 'yes', text: undefined }, { choice: 'no', text: 'only the first two' }, { text: 'only the first two' },
    ])
  })

  it('answers the one question that offers one pick the moment an option is pressed', async () => {
    const answers: unknown[] = []
    const { container } = await mount(AskBox, { ask: oneQuestion, busy: '', onAnswer: (given: unknown) => answers.push(given) })
    // The options are buttons, said once: not printed into the question as well.
    expect(container.querySelector('[data-testid="familiar-asked"]')?.textContent).toBe('Which theme?')
    expect([...container.querySelectorAll('[data-option]')].map(option => option.getAttribute('data-option'))).toEqual(['Light', 'Dark', 'System'])
    expect(container.querySelector('[data-option="Light"]')?.textContent).toContain('matches the logo')
    typeInto(inputOf(container, 'familiar-reply'), 'bigger type too')
    await flush()
    await press(container.querySelector('[data-option="Dark"]'))
    expect(answers).toEqual([{ selected: { 0: ['Dark'] }, text: 'bigger type too' }])
  })

  it('lets several be picked where more is asked, and sends them together', async () => {
    const answers: unknown[] = []
    const { container } = await mount(AskBox, { ask: twoQuestions, busy: '', onAnswer: (given: unknown) => answers.push(given) })
    const option = (question: string, label: string) => container.querySelector(`[data-question="${question}"] [data-option="${label}"]`)
    const send = () => container.querySelector('[data-testid="familiar-answer"]') as HTMLButtonElement
    expect(send().disabled).toBe(true)
    await press(option('0', 'Vercel'))
    await press(option('0', 'Not now'))
    await press(option('1', 'a'))
    await press(option('1', 'Not now'))
    await press(option('1', 'a'))
    expect(answers).toEqual([])
    // One of the first (the later press took its place), any of the second; pressed again, off.
    expect(option('0', 'Vercel')?.getAttribute('aria-pressed')).toBe('false')
    expect(option('0', 'Not now')?.getAttribute('aria-pressed')).toBe('true')
    expect(option('1', 'a')?.getAttribute('aria-pressed')).toBe('false')
    expect(send().disabled).toBe(false)
    await press(send())
    expect(answers).toEqual([{ selected: { 0: ['Not now'], 1: ['Not now'] }, text: '' }])
  })

  it('holds its buttons back for a moment when another question takes the place of the one shown', async () => {
    vi.useFakeTimers()
    const answers: unknown[] = []
    const { container, state } = await mount(AskBox, { ask: permission(), busy: '', onAnswer: (given: unknown) => answers.push(given) })
    const once = () => container.querySelector('[data-choice="once"]') as HTMLButtonElement
    expect(once().disabled).toBe(false)
    state.props = { ...state.props, ask: permission({ id: 'q9', detail: { command: 'rm -rf dist' } }) }
    await flush()
    // The press that was on its way to the last question does not approve this one.
    expect(once().disabled).toBe(true)
    await press(once())
    expect(answers).toEqual([])
    vi.advanceTimersByTime(SETTLE_MS)
    await flush()
    expect(once().disabled).toBe(false)
    await press(once())
    expect(answers).toEqual([{ choice: 'once', note: undefined }])
  })
})

describe('a box drawn anew for a commission that showed another question a moment ago', () => {
  // A page that keeps which question each commission showed last, with one box (or bubble) at a time.
  const page = (component: Component) => defineComponent({
    props: { ask: { type: Object, default: null }, place: { type: String, default: 'c1' } },
    setup(props) {
      rememberAsks()
      return () => (props.ask ? h(component, { ask: props.ask as BoardAsk, busy: '', name: 'Tabby', error: '', place: props.place }) : null)
    },
  })
  const once = (container: Element) => container.querySelector('[data-choice="once"]') as HTMLButtonElement

  it.each([['the box', AskBox], ['the card above a head', AskBubble]])('holds %s back too, though the last one was taken away in between', async (_, component) => {
    vi.useFakeTimers()
    const { container, state } = await mount(page(component as Component), { ask: permission() })
    // The first one shown is answered at once.
    expect(once(container).disabled).toBe(false)
    // Answered: taken away. Then the commission asks the next thing, and its box is drawn anew.
    state.props = { ask: null }
    await flush()
    state.props = { ask: permission({ id: 'q2', detail: { command: 'rm -rf dist' } }) }
    await flush()
    expect(once(container).disabled).toBe(true)
    vi.advanceTimersByTime(SETTLE_MS)
    await flush()
    expect(once(container).disabled).toBe(false)
    // The same question drawn again (the page read anew), or the first of another commission: not held back.
    state.props = { ask: null }
    await flush()
    state.props = { ask: permission({ id: 'q2', detail: { command: 'rm -rf dist' } }) }
    await flush()
    expect(once(container).disabled).toBe(false)
    state.props = { ask: null }
    await flush()
    state.props = { ask: permission({ id: 'q7' }), place: 'c9' }
    await flush()
    expect(once(container).disabled).toBe(false)
  })

  it.each([['the box', AskBox], ['the card above a head', AskBubble]])('goes on holding %s back for what is left, taken away and drawn again before that moment is over', async (_, component) => {
    vi.useFakeTimers()
    const next = () => permission({ id: 'q2', detail: { command: 'rm -rf dist' } })
    const { container, state } = await mount(page(component as Component), { ask: permission() })
    state.props = { ask: null }
    await flush()
    state.props = { ask: next() }
    await flush()
    expect(once(container).disabled).toBe(true)
    // Another one's was opened in its stead, and then this one again: the press on its way is on its way still.
    vi.advanceTimersByTime(300)
    state.props = { ask: null }
    await flush()
    state.props = { ask: next() }
    await flush()
    expect(once(container).disabled).toBe(true)
    // For what was left of it, and not anew from where it was drawn again.
    vi.advanceTimersByTime(SETTLE_MS - 301)
    await flush()
    expect(once(container).disabled).toBe(true)
    vi.advanceTimersByTime(1)
    await flush()
    expect(once(container).disabled).toBe(false)
  })

  it.each([['the box', AskBox], ['the card above a head', AskBubble]])('holds %s back for what is left as well where the question changed in it and it is then drawn anew', async (_, component) => {
    vi.useFakeTimers()
    const next = () => permission({ id: 'q2', detail: { command: 'rm -rf dist' } })
    const { container, state } = await mount(page(component as Component), { ask: permission() })
    // In the same box this time: the question changes where it stands.
    state.props = { ask: next() }
    await flush()
    expect(once(container).disabled).toBe(true)
    vi.advanceTimersByTime(300)
    state.props = { ask: null }
    await flush()
    state.props = { ask: next() }
    await flush()
    expect(once(container).disabled).toBe(true)
    vi.advanceTimersByTime(SETTLE_MS - 300)
    await flush()
    expect(once(container).disabled).toBe(false)
  })

  it('is not held back where no page keeps what was shown', async () => {
    const { container } = await mount(AskBox, { ask: permission(), busy: '', place: 'c1' })
    expect(once(container).disabled).toBe(false)
    cleanup.forEach(done => done())
    cleanup = []
    const again = await mount(AskBox, { ask: permission({ id: 'q2' }), busy: '', place: 'c1' })
    expect(once(again.container).disabled).toBe(false)
  })
})

describe('the card above the head of a familiar that waits', () => {
  const bubble = (ask: BoardAsk, more: Record<string, unknown> = {}) => mount(AskBubble, { ask, name: 'Tabby', busy: '', error: '', ...more })

  it('offers an approval this once or not at all, and the rest behind "more"', async () => {
    const answers: unknown[] = []
    let opened = 0
    const { container } = await bubble(permission(), { onAnswer: (given: unknown) => answers.push(given), onMore: () => { opened += 1 } })
    expect(container.querySelector('[data-testid="bubble-asked"]')?.textContent).toContain('npm run build')
    // Not for good, nor for all of the commission: those are given where all of it is shown.
    expect([...container.querySelectorAll('[data-choice]')].map(choice => choice.getAttribute('data-choice'))).toEqual(['once', 'deny'])
    expect(container.querySelector('[data-testid="bubble-say"]')).toBeNull()
    await press(container.querySelector('[data-choice="once"]'))
    await press(container.querySelector('[data-testid="bubble-more"]'))
    expect(answers).toEqual([{ choice: 'once' }])
    expect(opened).toBe(1)
  })

  it('answers yes or no, a few options, or in words on a line of its own', async () => {
    const answers: unknown[] = []
    const onAnswer = (given: unknown) => answers.push(given)
    const yesNo = await bubble(confirmation, { onAnswer })
    await press(yesNo.container.querySelector('[data-confirm="no"]'))
    const picks = await bubble(oneQuestion, { onAnswer })
    await press(picks.container.querySelector('[data-option="System"]'))
    // Where it can only be answered in words the line is there from the start; elsewhere it is asked for.
    expect(picks.container.querySelector('[data-testid="bubble-reply"]')).toBeNull()
    const said = await bubble(inWords, { onAnswer })
    typeInto(said.container.querySelector('[data-testid="bubble-reply"]'), 'the newest one')
    await flush()
    said.container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flush()
    expect(answers).toEqual([{ choice: 'no', text: undefined }, { selected: { 0: ['System'] }, text: '' }, { text: 'the newest one' }])
  })

  it('leaves what has much to pick from to the whole question, and says why an answer was not taken', async () => {
    const { container } = await bubble(twoQuestions, { error: 'Not delivered' })
    expect(container.querySelector('[data-option]')).toBeNull()
    expect(container.querySelector('[data-testid="bubble-more"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="bubble-error"]')?.textContent).toBe('Not delivered')
    await press(container.querySelector('[data-testid="bubble-say"]'))
    expect(container.querySelector('[data-testid="bubble-reply"]')).toBeTruthy()
  })
})

describe('the Enter that picks a word being composed', () => {
  const key = (type: string, init: KeyboardEventInit & { keyCode?: number } = {}) => {
    const event = new KeyboardEvent(type, { key: 'Enter', bubbles: true, cancelable: true, ...init })
    if (init.keyCode !== undefined) Object.defineProperty(event, 'keyCode', { value: init.keyCode })
    return event
  }

  it('sends nothing from the box, let go of as any Enter is', async () => {
    const answers: unknown[] = []
    const { container } = await mount(AskBox, { ask: inWords, busy: '', onAnswer: (given: unknown) => answers.push(given) })
    const input = inputOf(container, 'familiar-reply')!
    typeInto(input, '最新的')
    await flush()
    for (const down of [key('keydown', { isComposing: true }), key('keydown', { keyCode: 229 })]) {
      input.dispatchEvent(down)
      input.dispatchEvent(key('keyup'))
    }
    expect(answers).toEqual([])
    // The Enter after it sends.
    input.dispatchEvent(key('keydown'))
    input.dispatchEvent(key('keyup'))
    expect(answers).toEqual([{ text: '最新的' }])
  })

  it('is kept from the form of the card above a head', async () => {
    const { container } = await mount(AskBubble, { ask: inWords, name: 'Tabby', busy: '', error: '' })
    const input = container.querySelector('[data-testid="bubble-reply"]') as HTMLInputElement
    const prevented = (event: KeyboardEvent) => { input.dispatchEvent(event); return event.defaultPrevented }
    expect(prevented(key('keydown', { isComposing: true }))).toBe(true)
    expect(prevented(key('keydown', { keyCode: 229 }))).toBe(true)
    expect(prevented(key('keydown'))).toBe(false)
  })
})

describe('the folder a command is to be run in', () => {
  it('is said on the card above a head, where the command names one', async () => {
    const elsewhere = permission({ detail: { command: 'rm -rf dist', cwd: 'D:/another/project' } })
    const { container } = await mount(AskBubble, { ask: elsewhere, name: 'Tabby', busy: '', error: '' })
    expect(container.querySelector('[data-testid="bubble-cwd"]')?.textContent).toContain('D:/another/project')
    // None where it names none, nor for what is no approval.
    const plain = await mount(AskBubble, { ask: permission(), name: 'Tabby', busy: '', error: '' })
    expect(plain.container.querySelector('[data-testid="bubble-cwd"]')).toBeNull()
    const other = await mount(AskBubble, { ask: { ...confirmation, detail: { cwd: 'D:/x' } }, name: 'Tabby', busy: '', error: '' })
    expect(other.container.querySelector('[data-testid="bubble-cwd"]')).toBeNull()
  })
})

describe('where the cards are shown', () => {
  const member = (over: Partial<ColonyMember>): ColonyMember => ({
    id: 's1', name: 'Tabby', avatar: 'worker', leader: false, parent: null, look: 'waiting_user', said: '', hidden: false, ...over,
  })

  it('puts what a familiar asks above its head, beside its button and not in it', async () => {
    const given: unknown[] = []
    const opened: string[] = []
    const { container } = await mount(ColonyTerritory, {
      territory: {
        key: 'project:blog', name: 'blog', loose: false,
        members: [
          member({ ask: permission(), commission: 'c1' }),
          // Nothing known of what it asks (what happened is replayed): only that it waits.
          member({ id: 's2', name: 'Soot' }),
          member({ id: 's3', name: 'Ash', look: 'working', said: 'half way', ask: permission(), commission: 'c3' }),
        ],
      },
      answering: { c1: '' }, failed: {},
      onAnswer: (id: string, answer: unknown) => given.push([id, answer]),
      onAsk: (id: string) => opened.push(id),
    })
    await flush()
    const card = container.querySelector('[data-ask-of="s1"]')
    expect(card).toBeTruthy()
    expect(card!.closest('button')).toBeNull()
    expect(container.querySelector('[data-cat="s1"] .bubble')).toBeNull()
    expect(container.querySelector('[data-cat="s2"] .bubble')?.textContent).toBe(words.colony.waitingBubble)
    expect(container.querySelector('[data-ask-of="s2"]')).toBeNull()
    // One at work asks nothing, whatever its card still carries.
    expect(container.querySelector('[data-ask-of="s3"]')).toBeNull()
    expect(container.querySelector('[data-cat="s3"] .bubble')?.textContent).toBe('half way')
    await press(card!.querySelector('[data-choice="deny"]'))
    await press(card!.querySelector('[data-testid="bubble-more"]'))
    expect(given).toEqual([['s1', { choice: 'deny' }]])
    expect(opened).toEqual(['s1'])
  })

  it('folds the cards into labels when several wait at once, and puts one near the top below its familiar', async () => {
    const members = ['s1', 's2', 's3'].map((id, index) => member({ id, name: id, ask: permission({ id: `q${index}` }), commission: `c${index}` }))
    const { container, state } = await mount(ColonyTerritory, { territory: { key: 'project:blog', name: 'blog', loose: false, members } })
    await flush()
    // Three cards would cover one another: each is a label, opened by pressing it, one at a time.
    expect(container.querySelectorAll('[data-ask-bubble]').length).toBe(0)
    expect(container.querySelectorAll('[data-testid="ask-label"]').length).toBe(3)
    await press(container.querySelector('[data-ask-of="s2"] [data-testid="ask-label"]'))
    // The card is loaded when the first one is shown.
    await vi.dynamicImportSettled()
    await flush()
    expect(container.querySelector('[data-ask-of="s2"] [data-ask-bubble]')).toBeTruthy()
    await press(container.querySelector('[data-ask-of="s3"] [data-testid="ask-label"]'))
    expect([...container.querySelectorAll('[data-ask-bubble]')].map(card => card.closest('[data-ask-of]')?.getAttribute('data-ask-of'))).toEqual(['s3'])
    // Two fit: both are shown.
    state.props = { territory: { key: 'project:blog', name: 'blog', loose: false, members: members.slice(0, 2) } }
    await flush()
    expect(container.querySelectorAll('[data-ask-bubble]').length).toBe(2)
    expect(container.querySelectorAll('[data-testid="ask-label"]').length).toBe(0)
    // Where the familiar stands decides on which side of it the card is.
    // Near the top (within what a card takes up, 150 pixels) it goes below; the two here stand apart in height.
    const height = parseFloat((container.querySelector('[data-territory]') as HTMLElement).style.height)
    const sides = [...container.querySelectorAll('[data-ask-of]')].map(spot =>
      [(parseFloat((spot as HTMLElement).style.top) / 100) * height < 150, spot.querySelector('[data-ask-bubble]')!.classList.contains('below')])
    expect(sides.every(([high, under]) => high === under)).toBe(true)
    expect(new Set(sides.map(([, under]) => under)).size).toBeGreaterThanOrEqual(1)
  })

  it('moves the card of one that stands at the edge inwards, its pointer staying above the familiar', async () => {
    localStorage.setItem('neko-power-colony-spots', JSON.stringify({ 'project:edge': { s1: { x: 0.02, y: 0.6 }, s2: { x: 0.5, y: 0.6 } } }))
    const members = [member({ ask: permission(), commission: 'c1' }), member({ id: 's2', name: 'Soot', ask: permission({ id: 'q2' }), commission: 'c2' })]
    const { container } = await mount(ColonyTerritory, { territory: { key: 'project:edge', name: 'edge', loose: false, members } })
    await vi.dynamicImportSettled()
    await flush()
    const width = parseFloat((container.querySelector('[data-territory]') as HTMLElement).style.width)
    const shift = (id: string) => parseFloat((container.querySelector(`[data-ask-of="${id}"] [data-ask-bubble]`) as HTMLElement).style.getPropertyValue('--shift'))
    const middle = (id: string) => (parseFloat((container.querySelector(`[data-ask-of="${id}"]`) as HTMLElement).style.left) / 100) * width
    // 216 wide, its middle near the left edge: moved right until its left edge is at the territory's.
    expect(middle('s1')).toBeLessThan(108)
    expect(shift('s1')).toBe(Math.round(108 - middle('s1')))
    expect(shift('s2')).toBe(0)
    localStorage.removeItem('neko-power-colony-spots')
  })

  it('goes by how wide the territory is drawn where the page gives it less room than it asks for', async () => {
    // What tells of the territory's size: kept here, to be told of a narrower page.
    const told: Array<() => void> = []
    vi.stubGlobal('ResizeObserver', class { constructor(callback: () => void) { told.push(callback) } observe() {} disconnect() {} })
    localStorage.setItem('neko-power-colony-spots', JSON.stringify({ 'project:wide': { s1: { x: 0.2, y: 0.6 } } }))
    // Seven of them: the territory asks for far more room than a card is wide.
    const members = [member({ ask: permission(), commission: 'c1' }), ...['a', 'b', 'c', 'd', 'e', 'f'].map(id => member({ id, name: id, look: 'idle' }))]
    const { container } = await mount(ColonyTerritory, { territory: { key: 'project:wide', name: 'wide', loose: false, members } })
    await vi.dynamicImportSettled()
    await flush()
    const territory = container.querySelector('[data-territory]') as HTMLElement
    const shift = () => parseFloat((container.querySelector('[data-ask-of="s1"] [data-ask-bubble]') as HTMLElement).style.getPropertyValue('--shift'))
    const left = parseFloat((container.querySelector('[data-ask-of="s1"]') as HTMLElement).style.left) / 100
    // With all the room it asks for, a card a fifth of the way in fits where it is.
    expect(left * parseFloat(territory.style.width)).toBeGreaterThan(108)
    expect(shift()).toBe(0)
    // The page is narrower: it is drawn 390 wide, and the card would reach out of it on the left.
    Object.defineProperty(territory, 'clientWidth', { configurable: true, value: 390 })
    told.forEach(tell => tell())
    await flush()
    expect(shift()).toBe(Math.round(108 - left * 390))
    expect(shift()).toBeGreaterThan(0)
    const drawnWidth = () => (container.querySelector('[data-ask-of="s1"] [data-ask-bubble]') as HTMLElement).style.width
    expect(drawnWidth()).toBe('216px')
    // Narrower than the card itself: the card is made as narrow, and put in its middle.
    Object.defineProperty(territory, 'clientWidth', { configurable: true, value: 200 })
    told.forEach(tell => tell())
    await flush()
    expect(shift()).toBe(Math.round(100 - left * 200))
    expect(drawnWidth()).toBe('200px')
    localStorage.removeItem('neko-power-colony-spots')
    vi.unstubAllGlobals()
  })

  it('tells whoever shows the questions that a sentence was not taken, where it asks to be told', async () => {
    const told: string[] = []
    let answers!: ReturnType<typeof useAskAnswers>
    let kept!: ReturnType<typeof useAskAnswers>
    await mount(defineComponent({
      setup() {
        answers = useAskAnswers(async () => {}, said => told.push(said))
        kept = useAskAnswers(async () => {})
        return () => null
      },
    }))
    api.answerCommission.mockResolvedValue({ success: true, note_taken: false })
    await answers.answer({ id: 'c1', ask: permission() }, { choice: 'deny', note: 'x' })
    await kept.answer({ id: 'c1', ask: permission() }, { choice: 'deny', note: 'x' })
    // Said elsewhere, since the card is gone with the answer; else kept with why answers were not taken.
    expect(told).toEqual([words.ask.noteLost])
    expect(answers.failedOf({ id: 'c1' })).toBe('')
    expect(kept.failedOf({ id: 'c1' })).toBe(words.ask.noteLost)
    // An answer that was not taken is kept with the question either way.
    api.answerCommission.mockRejectedValue({ code: 'not_taken' })
    await answers.answer({ id: 'c1', ask: permission() }, { choice: 'deny' })
    expect(answers.failedOf({ id: 'c1' })).toBe(words.ask.stale)
    expect(told.length).toBe(1)
  })

  it('answers in the card of the board, where pressing the card itself still opens the familiar', async () => {
    const card = {
      id: 's1', name: 'Tabby', preset: 'worker', avatar: 'worker', label: '', title: '', duty: '', doing: '', state: 'waiting_user', leader: false,
      commission: { id: 'c1', title: 'build the site', status: 'waiting_user', started_at: null, ask: confirmation },
    } as unknown as BoardCard
    const opened: unknown[] = []
    const answers: unknown[] = []
    const props = { card, answers: true, busy: '', error: 'It did not take that', onOpen: (which: unknown) => opened.push(which), onAnswer: (given: unknown) => answers.push(given) }
    const { container } = await mount(FamiliarCard, props)
    await press(container.querySelector('[data-confirm="yes"]'))
    expect(answers).toEqual([{ choice: 'yes', text: undefined }])
    expect(opened).toEqual([])
    expect(container.querySelector('[data-testid="familiar-card-error"]')?.textContent).toContain('It did not take that')
    await press(container.querySelector('[data-testid="familiar-open"]'))
    expect(opened).toEqual([card])
    // Where the card is one of a list to choose from, it only opens.
    const plain = await mount(FamiliarCard, { card })
    expect(plain.container.querySelector('[data-testid="familiar-ask"]')).toBeNull()
  })
})
