/** What a familiar asks the user while it waits, as the pages word it. */
import type { BoardAsk } from '@/api/console'

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
