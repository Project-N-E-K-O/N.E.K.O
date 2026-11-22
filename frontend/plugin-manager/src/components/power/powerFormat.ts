/** Small helpers the pages about the task agents share. */
import { consoleErrorCode } from '@/api/console'

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
