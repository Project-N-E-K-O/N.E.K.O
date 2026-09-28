import { describe, expect, it, vi } from 'vitest'

import {
  collectPluginInstallWarnings,
  notifyPluginInstallOutcome,
  resolvePluginInstallOutcome,
} from './pluginInstallResult'

const t = (key: string, params?: Record<string, unknown>) =>
  `${key}${params ? JSON.stringify(params) : ''}`

function notifier() {
  return { success: vi.fn(), warning: vi.fn(), error: vi.fn() }
}

describe('collectPluginInstallWarnings', () => {
  it('splits the joined backend warning string', () => {
    expect(collectPluginInstallWarnings({ install_source_warning: 'a; b ;  ; c' })).toEqual(['a', 'b', 'c'])
  })

  it('treats missing or null warnings as none', () => {
    expect(collectPluginInstallWarnings({})).toEqual([])
    expect(collectPluginInstallWarnings({ install_source_warning: null })).toEqual([])
    expect(collectPluginInstallWarnings(null)).toEqual([])
  })
})

describe('resolvePluginInstallOutcome', () => {
  it('keeps clean results as success', () => {
    expect(resolvePluginInstallOutcome({ rollback_status: 'not_needed' }, t)).toEqual({
      level: 'success',
      warnings: [],
    })
  })

  it('reports source warnings as warning', () => {
    expect(resolvePluginInstallOutcome({ install_source_warning: 'lock_failed' }, t)).toEqual({
      level: 'warning',
      warnings: ['lock_failed'],
    })
  })

  it.each([
    ['completed', 'package.install.rollbackCompleted'],
    ['incomplete', 'package.install.rollbackIncomplete'],
  ])('reports rollback_status=%s as error', (status, message) => {
    expect(resolvePluginInstallOutcome({ rollback_status: status, install_source_warning: 'w' }, t)).toEqual({
      level: 'error',
      message,
      warnings: ['w'],
    })
  })
})

describe('notifyPluginInstallOutcome', () => {
  it('shows the caller success message for clean results', () => {
    const notify = notifier()
    notifyPluginInstallOutcome({}, t, notify, { plugin: 'p', successMessage: 'ok' })
    expect(notify.success).toHaveBeenCalledWith('ok')
    expect(notify.warning).not.toHaveBeenCalled()
    expect(notify.error).not.toHaveBeenCalled()
  })

  it('shows a warning with reasons instead of success', () => {
    const notify = notifier()
    notifyPluginInstallOutcome({ install_source_warning: 'a; b' }, t, notify, { plugin: 'p', successMessage: 'ok' })
    expect(notify.success).not.toHaveBeenCalled()
    expect(notify.warning).toHaveBeenCalledWith(
      'package.install.completedWithWarnings{"plugin":"p","reasons":"a; b"}',
    )
  })

  it('shows an error for rollback results', () => {
    const notify = notifier()
    notifyPluginInstallOutcome({ rollback_status: 'incomplete' }, t, notify, { plugin: 'p', successMessage: 'ok' })
    expect(notify.success).not.toHaveBeenCalled()
    expect(notify.error).toHaveBeenCalledWith('package.install.rollbackIncomplete')
  })
})
