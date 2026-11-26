/** What a familiar asks the user while it waits, as the pages word it. */
import { inject, provide } from 'vue'
import type { InjectionKey } from 'vue'
import { consoleErrorCode } from '@/api/console'
import type { BoardAsk, CommissionAnswer } from '@/api/console'
import { describeConsoleError } from './powerFormat'

type Translate = (key: string, named?: Record<string, unknown>) => string
type Exists = (key: string) => boolean

/** One of the questions of an `ask_user_question` call as a line: what it asks, then what it offers. */
function questionLine(item: unknown): string {
  if (!item || typeof item !== 'object') return String(item ?? '').trim()
  const { question, options } = item as { question?: unknown; options?: unknown }
  const labels = (Array.isArray(options) ? options : [])
    .map(option => String((option && typeof option === 'object' ? (option as { label?: unknown }).label : option) ?? '').trim())
    .filter(Boolean)
  const text = String(question ?? '').trim()
  return text && labels.length ? `${text} (${labels.join(' / ')})` : text
}

const text = (value: unknown) => (typeof value === 'string' ? value : '')

/**
 * How a question is answered: a request for approval by one of its choices, a plugin's request
 * for confirmation by yes or no, questions that offer options by picking some, anything else in
 * words. Words can be added to each (to a choice as a note, which is passed on after it).
 */
export type AskShape = 'permission' | 'confirmation' | 'questions' | 'words'

/** One of the questions of an `ask_user_question` call, as the pages show it with its options to pick. */
export interface AskQuestion {
  /** What the answer names it by: where it stands among the questions ("0", "1", ...). Its own id is the model's, and need not differ from another's. */
  id: string
  text: string
  options: Array<{ label: string; description: string }>
  multi: boolean
}

export function askQuestions(ask: BoardAsk): AskQuestion[] {
  return (Array.isArray(ask.questions) ? ask.questions : []).flatMap((item, index) => {
    if (!item || typeof item !== 'object') return []
    const { question, options, multi_select } = item as { question?: unknown; options?: unknown; multi_select?: unknown }
    const offered = (Array.isArray(options) ? options : [])
      .map(option => (option && typeof option === 'object'
        ? { label: text((option as { label?: unknown }).label).trim(), description: text((option as { description?: unknown }).description).trim() }
        : { label: String(option ?? '').trim(), description: '' }))
      .filter(option => option.label)
    return [{ id: String(index), text: String(question ?? '').trim(), options: offered, multi: multi_select === true }]
  })
}

export function askShape(ask: BoardAsk): AskShape {
  if (ask.options?.length) return 'permission'
  if (ask.kind === 'confirmation') return 'confirmation'
  return askQuestions(ask).some(question => question.options.length) ? 'questions' : 'words'
}

/** What the user gave as the answer, as whoever shows the question hands it on. */
export type GivenAnswer = Pick<CommissionAnswer, 'text' | 'choice' | 'selected' | 'note'>

/** Whether it says anything: a choice, words, or an option that was picked. */
export function saysSomething(given: GivenAnswer): boolean {
  return Boolean(given.choice || given.text?.trim() || Object.values(given.selected ?? {}).some(labels => labels.length))
}

/** What an answer is being sent as, for the button that shows it is on its way. */
export const answeringAs = (given: GivenAnswer) => (given.choice ? `choice:${given.choice}` : 'answer')

/** The answer as it is sent: against the question that was shown, so that one put meanwhile does not get it. */
export function answerBody(given: GivenAnswer, ask: BoardAsk | null | undefined): CommissionAnswer {
  const selected = Object.fromEntries(Object.entries(given.selected ?? {}).filter(([, labels]) => labels.length))
  return {
    text: given.text?.trim() || undefined,
    choice: given.choice,
    selected: Object.keys(selected).length ? selected : undefined,
    note: given.note?.trim() || undefined,
    question_id: ask?.id,
  }
}

/** A plugin's request for confirmation answered with a button: yes or no, with what the user added. */
export const confirmation = (yes: boolean, added: string): GivenAnswer => ({ choice: yes ? 'yes' : 'no', text: added.trim() || undefined })

/**
 * How long the buttons of a question that took the place of another are held back, in
 * milliseconds: a press meant for the one that was there a moment ago must not answer this one.
 */
export const SETTLE_MS = 900

/**
 * Which question each commission was last shown asking, kept by the page that shows them
 * (`rememberAsks`). The box of a question goes away once it is answered, and the next one of
 * the same commission is drawn anew: by this it knows that it takes another's place, and holds
 * its buttons back as one that changed where it stood does.
 */
const ASK_MEMORY: InjectionKey<Map<string, string>> = Symbol('asks shown')
export function rememberAsks(): void {
  provide(ASK_MEMORY, new Map())
}
/** For the box of a question: note that this one is shown for `place` (its commission); true if another was before. */
export function useTookPlace(): (place: string | null | undefined, id: string | undefined) => boolean {
  const memory = inject(ASK_MEMORY, null)
  return (place, id) => {
    if (!memory || !place) return false
    const last = memory.get(place)
    memory.set(place, id ?? '')
    return last !== undefined && last !== (id ?? '')
  }
}

/**
 * Why an answer was not taken, in the pages' words. One that came too late (the question was
 * answered elsewhere, taken back, or another is put by now) says so: what is shown is read anew.
 */
export function describeAnswerError(failure: unknown, t: Translate, te: Exists): string {
  return consoleErrorCode(failure) === 'not_taken' ? t('power.ask.stale') : describeConsoleError(failure, t, te)
}

/**
 * Whether NekoPower asks whether another familiar is to do, in a worktree of its own, what waits
 * for one busy with something else (in a project without a leader). Asked while the commission
 * waits for its turn: it is at no familiar then.
 */
export function isBeside(ask: BoardAsk | null | undefined): boolean {
  return ask?.detail?.tool === 'beside'
}

/** What such a question says: what came, who is busy (and at what), and who would be opened for it. */
export function besideOf(ask: BoardAsk) {
  const detail = ask.detail ?? {}
  return {
    title: text(detail.title),
    project: text(detail.project),
    /** The name the one to be opened would have; it may come to have a number more. */
    member: text(detail.member),
    /** The name and the session of the busy one, and what it is at. */
    beside: text(detail.beside),
    besideSession: text(detail.beside_session),
    atHand: text(detail.at_hand),
  }
}

/** What a button of the answer says: for such a question, what becomes of the work. */
export function choiceLabel(ask: BoardAsk, option: string, t: Translate, te: Exists): string {
  const keys = [`power.space.familiars.choices.${option}`]
  if (isBeside(ask)) keys.unshift(`power.ask.beside.choices.${option}`)
  const key = keys.find(item => te(item))
  return key ? t(key) : option
}

/** What has to be known to answer such a question rightly; null for any other. */
export function besideHint(ask: BoardAsk, t: Translate, te: Exists): string | null {
  if (!isBeside(ask)) return null
  const { beside } = besideOf(ask)
  return t('power.ask.beside.hint', { name: beside || t('power.ask.beside.busyOne'), deny: choiceLabel(ask, 'deny', t, te) })
}

function categoryName(ask: BoardAsk, t: Translate, te: Exists): string {
  const category = String(ask.category ?? '')
  // The names the permission table has for them, and those of the kinds that table does not show.
  for (const key of [`power.runtime.permissions.categories.${category}`, `power.ask.categories.${category}`]) {
    if (te(key)) return t(key)
  }
  return category
}

/**
 * Return what it asks, as lines: the question or questions it put, or what
 * kind of thing it wants to be allowed to do (what exactly is shown beside
 * it, whole: `askWhole`).
 */
export function askLines(ask: BoardAsk, t: Translate, te: Exists): string[] {
  if (ask.question) return [ask.question]
  const lines = (Array.isArray(ask.questions) ? ask.questions : []).map(questionLine).filter(Boolean)
  if (lines.length) return lines.length > 1 ? lines.map((line, index) => `${index + 1}. ${line}`) : lines
  if (isBeside(ask)) return [t('power.ask.beside.question')]
  const detail = ask.detail ?? {}
  const tool = typeof detail.tool === 'string' ? detail.tool : ''
  // What a leader would do, in words where the pages have some: hand the work over, form a team.
  const toolName = tool && te(`power.ask.tools.${tool}`) ? t(`power.ask.tools.${tool}`) : tool
  return [[categoryName(ask, t, te), toolName].filter(Boolean).join(' · ')].filter(Boolean)
}

/** Return the one thing a request for approval is about, whole: the command it would run, or the path it would touch. */
export function askWhole(ask: BoardAsk): { kind: 'command' | 'path'; text: string } | null {
  const detail = ask.detail ?? {}
  for (const kind of ['command', 'path'] as const) {
    const text = detail[kind]
    if (typeof text === 'string' && text.trim()) return { kind, text }
  }
  return null
}

// Each call of `neko-dsh-plugin` in a command line, with the word after it; a call whose first word
// is not one of these may bring a plugin in. The same as `adds_plugin` in
// `nekopower/dsh/plugin_command.py`.
// The word is looked for on the same line only: a command on the next one is no argument.
const DSH_PLUGIN_CALL = /neko-dsh-plugin(?:\.cmd)?['"]?(?:[ \t]+['"]?([^\s'"]*))?/gi
// Not `allow-version`: it lets dsh load a plugin version it would refuse (`HARMLESS_COMMANDS`).
const DSH_PLUGIN_LOCAL = new Set([
  'list', 'ls', 'remove', 'rm', 'uninstall', 'un', 'why', 'revoke-version', 'version-exemptions',
])

/**
 * Return what has to be known before saying yes, when the request may add a dsh plugin: it runs
 * inside dsh with all that dsh may do, without a sandbox. Null for any other request.
 */
export function askWarning(ask: BoardAsk, t: Translate): string | null {
  const command = ask.detail?.command
  if (ask.category !== 'run_command' || typeof command !== 'string') return null
  const adds = [...command.matchAll(DSH_PLUGIN_CALL)].some(call => !DSH_PLUGIN_LOCAL.has((call[1] ?? '').toLowerCase()))
  return adds ? t('power.runtime.plugins.warning') : null
}

const SHOWN_APART = new Set(['command', 'path', 'tool'])

/** Return what else a request for approval says, each with its name: where it would run, to whom something goes. */
export function askFacts(ask: BoardAsk, t: Translate, te: Exists): Array<{ name: string; label: string; value: string }> {
  if (isBeside(ask)) {
    // What came beside what the busy one is at: set side by side, the user tells whether it goes on with that.
    const said = besideOf(ask)
    return [
      { name: 'title', label: t('power.ask.beside.came'), value: said.title },
      { name: 'at_hand', label: t('power.ask.beside.atHand', { name: said.beside || t('power.ask.beside.busyOne') }), value: said.atHand },
      { name: 'member', label: t('power.ask.beside.member'), value: said.member },
      { name: 'tree', label: label('tree', t, te), value: ask.detail?.tree === true ? t('power.ask.treeNote') : '' },
    ].filter(fact => fact.value)
  }
  const facts = []
  const detail = ask.detail ?? {}
  // A team is said one familiar a line (`members`); its `title` is the same in one line. Where the
  // service names the kind of each apart (`parts`), the kind is said by the name the pages give it.
  const parts = (Array.isArray(detail.parts) ? detail.parts : [])
    .filter((item): item is { preset?: unknown; description?: unknown } => !!item && typeof item === 'object')
    .map(item => {
      const preset = typeof item.preset === 'string' ? item.preset : ''
      const kind = te(`power.home.presets.${preset}`) ? t(`power.home.presets.${preset}`) : preset
      return [kind, typeof item.description === 'string' ? item.description : ''].filter(Boolean).join(': ')
    })
    .filter(Boolean)
  const strings = Array.isArray(detail.members) ? detail.members.filter((item): item is string => typeof item === 'string' && item !== '') : []
  const members = parts.length ? parts : strings
  for (const [name, value] of Object.entries(detail)) {
    if (name === 'members' && members.length) {
      facts.push({ name, label: label(name, t, te), value: members.map(item => `· ${item}`).join('\n') })
      continue
    }
    if (name === 'title' && members.length) continue
    // Of one that would change files: where, and what then becomes of what it changed.
    if (name === 'tree' && value === true) {
      facts.push({ name, label: label(name, t, te), value: t('power.ask.treeNote') })
      continue
    }
    if (SHOWN_APART.has(name) || (typeof value !== 'string' && typeof value !== 'number') || value === '') continue
    const key = `power.ask.facts.${name}`
    // The kind of familiar it would start, by the name the pages give that kind.
    const kind = `power.home.presets.${value}`
    const shown = name === 'preset' && te(kind) ? t(kind) : String(value)
    facts.push({ name, label: te(key) ? t(key) : name, value: shown })
  }
  return facts
}

function label(name: string, t: Translate, te: Exists): string {
  const key = `power.ask.facts.${name}`
  return te(key) ? t(key) : name
}

/** Return what a familiar asks in one line, for a list: its question, or the command or path it asks about, cut short. */
export function askSummary(ask: BoardAsk, t: Translate, te: Exists, limit = 80): string {
  const whole = askWhole(ask)
  let line = whole ? `${askLines(ask, t, te)[0] ?? ''}: ${whole.text.replace(/\s+/g, ' ')}` : askLines(ask, t, te).join(' ')
  if (isBeside(ask)) line = t('power.ask.beside.summary', { title: besideOf(ask).title })
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line
}
