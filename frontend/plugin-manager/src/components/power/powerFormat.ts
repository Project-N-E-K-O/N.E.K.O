/** Small helpers the pages about the task agents share. */
import { consoleErrorCode } from '@/api/console'
import type { BoardCard } from '@/api/console'

type Translate = (key: string, named?: Record<string, unknown>) => string
type Exists = (key: string) => boolean

/** Return a size as a person reads it: `1.3 GB`, `310 MB`, `2 KB`. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = Math.max(0, bytes)
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unit]}`
}

/**
 * Return the words for a failed request of a console route, in the user's
 * language: those of its code when the pages have some, else the general ones.
 */
export function describeConsoleError(error: unknown, t: Translate, te: Exists): string {
  const code = consoleErrorCode(error)
  // Of a refusal that says why (a project that is busy), the words for why where the pages have some.
  const reason = (error as { response?: { data?: { detail?: { reason?: unknown } } } })?.response?.data?.detail?.reason
  if (code && typeof reason === 'string' && reason && te(`power.errors.${code}_${reason}`)) return t(`power.errors.${code}_${reason}`)
  if (code && te(`power.errors.${code}`)) return t(`power.errors.${code}`)
  const status = (error as { response?: { status?: number } })?.response?.status
  if (status === 502 || status === undefined) return t('power.errors.agent_unreachable')
  return t('power.errors.generic')
}

export type IdleUnit = 'minutes' | 'hours' | 'days'
const UNIT_SECONDS: Record<IdleUnit, number> = { minutes: 60, hours: 3600, days: 86400 }

/** Split a number of seconds into the largest unit that divides it evenly. */
export function splitIdle(seconds: number): { amount: number; unit: IdleUnit } {
  for (const unit of ['days', 'hours'] as const) {
    if (seconds >= UNIT_SECONDS[unit] && seconds % UNIT_SECONDS[unit] === 0) {
      return { amount: seconds / UNIT_SECONDS[unit], unit }
    }
  }
  return { amount: Math.round((seconds / 60) * 100) / 100, unit: 'minutes' }
}

export function joinIdle(amount: number, unit: IdleUnit): number {
  return amount * UNIT_SECONDS[unit]
}

// Stand-ins until there is art for each kind of familiar: one face per avatar key.
export const FAMILIAR_FACES: Record<string, string> = {
  boss: '😼', worker: '🐱', scout: '🙀', inspector: '😾', planner: '😺', general: '🐈', shadow: '🐈‍⬛',
}

/** Return a number of tokens in a few characters: `950`, `12k`, `1.3M`. */
export function formatTokens(tokens: number | null | undefined): string {
  if (tokens === null || tokens === undefined || !Number.isFinite(tokens)) return ''
  const value = Math.max(0, tokens)
  if (value < 1000) return String(Math.round(value))
  if (value < 999_500) return `${Math.round(value / 1000)}k`
  const millions = value / 1_000_000
  return `${millions >= 100 ? Math.round(millions) : Math.round(millions * 10) / 10}M`
}

/** Return how long something took, as a person reads it: `45s`, `12m`, `3h 5m`. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return ''
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`
}

// The last four are of one that works in a git worktree of its own: what it changed is being
// merged into the project's directory, it merges what clashed there, or what it changed waits there
// for the user (`held`: the user is asked first; `unlanded`: it could not be merged).
const LOOKS = ['working', 'waiting_user', 'idle', 'asleep', 'compacting', 'landing', 'resolving', 'held', 'unlanded'] as const
export type FamiliarLook = (typeof LOOKS)[number] | 'resting' | 'queued'

/**
 * Return how a familiar is drawn. One that is asleep with something in hand
 * is resting until it is woken, or waiting its turn: told apart by what it
 * has in hand. A state the page does not know is drawn as asleep.
 */
export function familiarLook(card: Pick<BoardCard, 'state' | 'commission'>): FamiliarLook {
  const { state, commission } = card
  if (state === 'asleep' && commission && (commission.status === 'resting' || commission.status === 'queued')) {
    return commission.status
  }
  return (LOOKS as readonly string[]).includes(state) ? (state as FamiliarLook) : 'asleep'
}

/** Return the name of the kind of familiar a card is: a template of the user's names itself, one without a name is shown by its key. */
export function presetName(card: Pick<BoardCard, 'preset' | 'label'>, t: Translate, te: Exists): string {
  const key = `power.home.presets.${card.preset}`
  return te(key) ? t(key) : card.label || card.preset
}

/**
 * Return what a familiar is called where there is room for one short name:
 * the title it earned; else, for one another familiar started, what kind it
 * is (its own name is the starter's with a number); else its name.
 */
export function familiarName(
  card: Pick<BoardCard, 'name' | 'title' | 'parent' | 'preset' | 'label'>, t: Translate, te: Exists,
): string {
  if (card.title) return card.title
  return (!card.parent && card.name) || presetName(card, t, te)
}
