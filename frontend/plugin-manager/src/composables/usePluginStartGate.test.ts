// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { panel, row } from '@/utils/permissionTab.fixtures'
import { usePermissionStartDialog } from '@/stores/permissionStartDialog'
import { START_REVIEW_TIMEOUT_MS, readStartReview, usePluginStartGate } from './usePluginStartGate'

const api = vi.hoisted(() => ({
  getPendingPermissions: vi.fn(),
  getPermissionPanel: vi.fn(),
}))
vi.mock('@/api/pluginPermissions', () => api)
const dialogModule = vi.hoisted(() => ({ preload: vi.fn() }))
vi.mock('@/components/plugin/permissionStartDialog', () => ({ PermissionStartDialog: dialogModule }))
const pluginStore = vi.hoisted(() => ({
  start: vi.fn(),
  getPluginById: vi.fn((id: string) => ({ id, name: `Plugin ${id}` })),
}))
vi.mock('@/stores/plugin', () => ({ usePluginStore: () => pluginStore }))

const NET = 'system.network:connect'
const pending = (over = {}) => ({ first_use: [], incomplete: [], to_confirm: [], upgraded: [], ...over })
const flush = async () => {
  for (let n = 0; n < 10; n++) await Promise.resolve()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  pluginStore.start.mockResolvedValue(undefined)
  dialogModule.preload.mockResolvedValue({ default: {} })
  api.getPermissionPanel.mockResolvedValue(panel({ rows: [row({ state: 'undecided', condition: null })] }))
})
afterEach(() => {
  vi.useRealTimers()
})

describe('readStartReview', () => {
  it('asks ① for a plugin never decided and ③ for one an update asks more of', async () => {
    api.getPendingPermissions.mockResolvedValue(pending({ to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))
    expect((await readStartReview('demo'))?.request).toEqual({ kind: 'first', items: [NET] })
    api.getPendingPermissions.mockResolvedValue(pending({ upgraded: [{ plugin_id: 'demo', items: [NET] }] }))
    expect((await readStartReview('demo'))?.request).toEqual({ kind: 'upgrade', items: [NET] })
    expect(dialogModule.preload).toHaveBeenCalled()
  })

  it('asks nothing for a plugin not listed, or only used while undecided', async () => {
    api.getPendingPermissions.mockResolvedValue(pending({
      to_confirm: [{ plugin_id: 'other', items: [NET] }],
      first_use: [{ plugin_id: 'demo', permission: NET, at: '2026-11-27T10:00:00Z', count: 1, count_is_lower_bound: false }],
    }))
    expect(await readStartReview('demo')).toBeNull()
    expect(api.getPermissionPanel).not.toHaveBeenCalled()
  })

  it.each([
    ['the pending list fails', () => api.getPendingPermissions.mockRejectedValue(new Error('offline'))],
    ['the pending list has another shape', () => api.getPendingPermissions.mockResolvedValue({})],
    ['the panel fails', () => {
      api.getPendingPermissions.mockResolvedValue(pending({ to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))
      api.getPermissionPanel.mockRejectedValue(new Error('offline'))
    }],
    ['the panel has another shape', () => {
      api.getPendingPermissions.mockResolvedValue(pending({ to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))
      api.getPermissionPanel.mockResolvedValue({ rows: [] })
    }],
    ['the dialog cannot be loaded', () => {
      api.getPendingPermissions.mockResolvedValue(pending({ to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))
      dialogModule.preload.mockRejectedValue(new Error('chunk'))
    }],
  ])('asks nothing when %s', async (_case, arrange) => {
    arrange()
    expect(await readStartReview('demo')).toBeNull()
  })

  it('gives up after a fixed short time', async () => {
    expect(START_REVIEW_TIMEOUT_MS).toBe(3000)
    vi.useFakeTimers()
    api.getPendingPermissions.mockReturnValue(new Promise(() => {}))
    const result = readStartReview('demo')
    await vi.advanceTimersByTimeAsync(START_REVIEW_TIMEOUT_MS - 1)
    let settled = false
    void result.then(() => { settled = true })
    await flush()
    expect(settled).toBe(false)
    await vi.advanceTimersByTimeAsync(1)
    expect(await result).toBeNull()
  })
})

describe('usePluginStartGate', () => {
  it('starts at once when nothing waits', async () => {
    api.getPendingPermissions.mockResolvedValue(pending())
    expect(await usePluginStartGate().startWithReview('demo')).toBe(true)
    expect(pluginStore.start).toHaveBeenCalledWith('demo')
    expect(usePermissionStartDialog().active).toBeNull()
  })

  it('starts when the reads fail', async () => {
    api.getPendingPermissions.mockRejectedValue(new Error('offline'))
    expect(await usePluginStartGate().startWithReview('demo')).toBe(true)
    expect(pluginStore.start).toHaveBeenCalledWith('demo')
  })

  it('starts when the reads time out', async () => {
    vi.useFakeTimers()
    api.getPendingPermissions.mockReturnValue(new Promise(() => {}))
    const started = usePluginStartGate().startWithReview('demo')
    await vi.advanceTimersByTimeAsync(START_REVIEW_TIMEOUT_MS)
    expect(await started).toBe(true)
    expect(pluginStore.start).toHaveBeenCalledWith('demo')
  })

  it('shows the dialog first and starts when the user chooses to', async () => {
    api.getPendingPermissions.mockResolvedValue(pending({ to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))
    const started = usePluginStartGate().startWithReview('demo')
    await flush()
    const dialog = usePermissionStartDialog()
    expect(dialog.active?.request).toEqual({ kind: 'first', items: [NET] })
    expect(dialog.active?.pluginName).toBe('Plugin demo')
    expect(pluginStore.start).not.toHaveBeenCalled()
    dialog.answer(true)
    expect(await started).toBe(true)
    expect(pluginStore.start).toHaveBeenCalledTimes(1)
  })

  it('sends no start when the user chooses not to start', async () => {
    api.getPendingPermissions.mockResolvedValue(pending({ upgraded: [{ plugin_id: 'demo', items: [NET] }] }))
    const started = usePluginStartGate().startWithReview('demo')
    await flush()
    usePermissionStartDialog().answer(false)
    expect(await started).toBe(false)
    expect(pluginStore.start).not.toHaveBeenCalled()
  })

  it('sends one start for clicks repeated while the dialog is open', async () => {
    api.getPendingPermissions.mockResolvedValue(pending({ to_confirm: [{ plugin_id: 'demo', items: [NET] }] }))
    const gate = usePluginStartGate()
    const first = gate.startWithReview('demo')
    await flush()
    expect(await gate.startWithReview('demo')).toBe(false)
    expect(await usePluginStartGate().startWithReview('demo')).toBe(false)
    usePermissionStartDialog().answer(true)
    expect(await first).toBe(true)
    expect(pluginStore.start).toHaveBeenCalledTimes(1)
    expect(api.getPendingPermissions).toHaveBeenCalledTimes(1)
    // Once done, the plugin can be started again.
    api.getPendingPermissions.mockResolvedValue(pending())
    expect(await gate.startWithReview('demo')).toBe(true)
    expect(pluginStore.start).toHaveBeenCalledTimes(2)
  })

  it('lets the start fail as before, and a later click try again', async () => {
    api.getPendingPermissions.mockResolvedValue(pending())
    pluginStore.start.mockRejectedValueOnce(new Error('boom'))
    const gate = usePluginStartGate()
    await expect(gate.startWithReview('demo')).rejects.toThrow('boom')
    expect(await gate.startWithReview('demo')).toBe(true)
  })
})

describe('usePermissionStartDialog', () => {
  it('a new request answers the open one with "don\'t start"; a stale answer is dropped', async () => {
    const dialog = usePermissionStartDialog()
    const base = { panel: panel(), pending: pending(), pluginName: 'x' }
    const first = dialog.ask({ ...base, request: { kind: 'first', items: [NET] } })
    const firstKey = dialog.active!.key
    const second = dialog.ask({ ...base, request: { kind: 'upgrade', items: [NET] } })
    expect(await first).toBe(false)
    dialog.answer(true, firstKey)
    expect(dialog.active?.request.kind).toBe('upgrade')
    dialog.answer(true, dialog.active!.key)
    expect(await second).toBe(true)
    expect(dialog.active).toBeNull()
  })
})
