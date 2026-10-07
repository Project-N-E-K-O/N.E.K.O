import { beforeEach, describe, expect, it, vi } from 'vitest'
import { del, get, post, put } from './index'
import request from '@/utils/request'
import { registerDevelopment, rebindDevelopment, removeDevelopment, runDevelopmentAction, setDevelopmentEnabled } from './development'
import { buildPluginCli } from './pluginCli'
vi.mock('./index', () => ({ get: vi.fn(), post: vi.fn(), del: vi.fn(), put: vi.fn() }))
vi.mock('@/utils/request', () => ({ default: { patch: vi.fn() } }))
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(get).mockResolvedValue({ enabled: true, registrations: [], disable_timeout_ms: 300_000 })
})
const record = { registration_id: 'registration/1', revision: 3, plugin_id: 'demo', source_dir: 'C:/中文 folder/demo' }
describe('development API identity boundaries', () => {
  it('previews without registering and sends the local-action header', async () => {
    await registerDevelopment(record.source_dir, true)
    expect(post).toHaveBeenCalledWith('/plugins/development/registrations', { source_dir: record.source_dir, preview: true }, expect.objectContaining({ headers: { 'X-Neko-Development': '1' } }))
  })
  it('fences lifecycle, rebind and remove operations with the original revision', async () => {
    await runDevelopmentAction(record, 'reload')
    expect(post).toHaveBeenCalledWith('/plugin/demo/reload', undefined, expect.objectContaining({ params: { registration_id: record.registration_id, revision: 3 } }))
    await rebindDevelopment(record, 'D:/demo')
    expect(request.patch).toHaveBeenCalledWith('/plugins/development/registrations/registration%2F1', { source_dir: 'D:/demo', revision: 3 }, expect.any(Object))
    await removeDevelopment(record)
    expect(del).toHaveBeenCalledWith('/plugins/development/registrations/registration%2F1', expect.objectContaining({ params: { revision: 3 } }))
  })
  it('builds by registration identity, never by the source path', async () => {
    const payload = { mode: 'single' as const, development_ref: { registration_id: record.registration_id, revision: 3 } }
    await buildPluginCli(payload)
    expect(post).toHaveBeenCalledWith('/plugin-cli/build', payload, { timeout: 300_000, headers: { 'X-Neko-Development': '1' } })
  })
  it('preserves ordinary build requests and gates all-mode builds', async () => {
    await buildPluginCli({ mode: 'single', plugin: 'demo' })
    expect(post).toHaveBeenLastCalledWith('/plugin-cli/build', { mode: 'single', plugin: 'demo' })
    await buildPluginCli({ mode: 'all' })
    expect(post).toHaveBeenLastCalledWith('/plugin-cli/build', { mode: 'all' }, { timeout: 300_000, headers: { 'X-Neko-Development': '1' } })
    await setDevelopmentEnabled(false)
    expect(put).toHaveBeenCalledWith('/plugins/development/settings', { enabled: false }, expect.any(Object))
  })
  it('uses the current bulk shutdown budget without changing single-operation timeouts', async () => {
    vi.mocked(get).mockResolvedValueOnce({ enabled: true, registrations: [record], disable_timeout_ms: 980_000 })
    await setDevelopmentEnabled(false)
    expect(get).toHaveBeenLastCalledWith('/plugins/development', expect.objectContaining({ timeout: 45_000 }))
    expect(put).toHaveBeenLastCalledWith('/plugins/development/settings', { enabled: false }, {
      headers: { 'X-Neko-Development': '1' }, timeout: 980_000,
    })
    await setDevelopmentEnabled(true)
    expect(put).toHaveBeenLastCalledWith('/plugins/development/settings', { enabled: true }, {
      headers: { 'X-Neko-Development': '1' }, timeout: 45_000,
    })
    await runDevelopmentAction(record, 'stop')
    expect(post).toHaveBeenLastCalledWith('/plugin/demo/stop', undefined, expect.objectContaining({ timeout: 45_000 }))
    expect(get).toHaveBeenCalledTimes(1)
  })
  it.each([undefined, 0, -1, NaN, Infinity])('keeps a finite bulk fallback for an invalid budget %s', async (budget) => {
    vi.mocked(get).mockResolvedValueOnce({ enabled: true, registrations: Array(10).fill(record), disable_timeout_ms: budget })
    await setDevelopmentEnabled(false)
    expect(put).toHaveBeenLastCalledWith('/plugins/development/settings', { enabled: false }, expect.objectContaining({ timeout: 495_000 }))
  })
  it('does not mutate if fetching the current budget fails', async () => {
    vi.mocked(get).mockRejectedValueOnce(new Error('network timeout'))
    await expect(setDevelopmentEnabled(false)).rejects.toThrow('network timeout')
    expect(put).not.toHaveBeenCalled()
  })
  it('propagates a lost shutdown response without retrying the mutation', async () => {
    vi.mocked(put).mockRejectedValueOnce(new Error('request timeout'))
    await expect(setDevelopmentEnabled(false)).rejects.toThrow('request timeout')
    expect(put).toHaveBeenCalledTimes(1)
  })
  it.each(['selected', 'bundle'] as const)('gives %s development builds the long operation budget', async (mode) => {
    const payload = { mode, development_refs: [{ registration_id: record.registration_id, revision: 3 }] }
    await buildPluginCli(payload)
    expect(post).toHaveBeenLastCalledWith('/plugin-cli/build', payload, {
      timeout: 300_000, headers: { 'X-Neko-Development': '1' },
    })
  })
})
