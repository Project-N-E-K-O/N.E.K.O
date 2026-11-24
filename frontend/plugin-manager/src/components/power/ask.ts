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
  const detail = ask.detail ?? {}
  const tool = typeof detail.tool === 'string' ? detail.tool : ''
  return [[categoryName(ask, t, te), tool].filter(Boolean).join(' · ')].filter(Boolean)
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
  const facts = []
  for (const [name, value] of Object.entries(ask.detail ?? {})) {
    if (SHOWN_APART.has(name) || (typeof value !== 'string' && typeof value !== 'number') || value === '') continue
    const key = `power.ask.facts.${name}`
    // The kind of familiar it would start, by the name the pages give that kind.
    const kind = `power.home.presets.${value}`
    const shown = name === 'preset' && te(kind) ? t(kind) : String(value)
    facts.push({ name, label: te(key) ? t(key) : name, value: shown })
  }
  return facts
}

/** Return what a familiar asks in one line, for a list: its question, or the command or path it asks about, cut short. */
export function askSummary(ask: BoardAsk, t: Translate, te: Exists, limit = 80): string {
  const whole = askWhole(ask)
  const line = whole ? `${askLines(ask, t, te)[0] ?? ''}: ${whole.text.replace(/\s+/g, ' ')}` : askLines(ask, t, te).join(' ')
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line
}
