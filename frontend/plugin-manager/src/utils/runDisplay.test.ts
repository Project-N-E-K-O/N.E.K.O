import { describe, expect, it, vi } from 'vitest'
import type { RunRecord } from '@/api/runs'
import { formatRunTimestamp, runDurationSeconds, runProgress, runStatusKey } from './runDisplay'

describe('run presentation semantics', () => {
  it('does not invent zero progress or accept non-finite samples', () => {
    for (const value of [null, undefined, NaN, Infinity, '0.5'])
      expect(runProgress(value)).toBeNull()
    expect(runProgress(0)).toBe(0)
    expect(runProgress(0.425)).toBe(43)
    expect(runProgress(5)).toBe(100)
    expect(runProgress(-1)).toBe(0)
  })
  it('only ticks active runs with a reported start time and preserves completed durations', () => {
    const run = { status: 'running', started_at: 10, finished_at: null } as RunRecord
    expect(runDurationSeconds(run, 30)).toBe(20)
    expect(runDurationSeconds({ ...run, started_at: null }, 30)).toBeNull()
    expect(runDurationSeconds({ ...run, status: 'succeeded' }, 30)).toBeNull()
    expect(runDurationSeconds({ ...run, status: 'succeeded', finished_at: 12 }, 300)).toBe(2)
    expect(runDurationSeconds({ ...run, finished_at: 9 }, 30)).toBeNull()
  })
  it('does not expose invalid dates or prototype keys as a status translation', () => {
    for (const value of [null, undefined, Infinity, 0, '1', 1e300])
      expect(formatRunTimestamp(value)).toBe('—')
    expect(runStatusKey('succeeded')).toBe('runs.statuses.succeeded')
    expect(runStatusKey('constructor')).toBe('common.unknown')
  })
  it('preserves seconds for historical records that may be only seconds apart', () => {
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date(2026, 9, 22, 12))
      const timestamp = new Date(2026, 9, 21, 11, 22, 3).getTime() / 1000
      expect(formatRunTimestamp(timestamp, true)).toBe('10-21 11:22:03')
    } finally { vi.useRealTimers() }
  })

})
