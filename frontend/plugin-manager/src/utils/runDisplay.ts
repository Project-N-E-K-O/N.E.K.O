import dayjs from 'dayjs'
import type { RunRecord } from '@/api/runs'

export const RUN_STATUS_TONES: Record<string, string> = {
  queued: 'warning',
  running: 'primary',
  succeeded: 'success',
  failed: 'danger',
  canceled: 'info',
  timeout: 'danger',
  cancel_requested: 'warning',
}

export function runStatusKey(status: string): string {
  return Object.hasOwn(RUN_STATUS_TONES, status) ? `runs.statuses.${status}` : 'common.unknown'
}

export function formatRunTimestamp(value: unknown, compact = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '—'
  const date = dayjs(value * 1000)
  if (!date.isValid()) return '—'
  return date.format(
    compact ? (date.isSame(dayjs(), 'day') ? 'HH:mm:ss' : 'MM-DD HH:mm:ss') : 'YYYY-MM-DD HH:mm:ss'
  )
}

export function runProgress(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.round(Math.min(1, Math.max(0, value)) * 100)
    : null
}

export function runDurationSeconds(run: RunRecord, now: number): number | null {
  const start = run.started_at
  if (typeof start !== 'number' || !Number.isFinite(start) || start <= 0) return null
  const end = run.finished_at ?? (['running', 'cancel_requested'].includes(run.status) ? now : null)
  return typeof end === 'number' && Number.isFinite(end) && end >= start ? end - start : null
}

export function formatRunDuration(seconds: number | null, locale: string): string {
  if (seconds === null) return '—'
  const unit = (value: number, name: 'second' | 'minute' | 'hour') =>
    new Intl.NumberFormat(locale, {
      style: 'unit',
      unit: name,
      unitDisplay: 'short',
      maximumFractionDigits: 1,
    }).format(value)
  if (seconds < 60) return unit(seconds, 'second')
  if (seconds < 3600)
    return `${unit(Math.floor(seconds / 60), 'minute')} ${unit(Math.floor(seconds % 60), 'second')}`
  return `${unit(Math.floor(seconds / 3600), 'hour')} ${unit(Math.floor((seconds % 3600) / 60), 'minute')}`
}
