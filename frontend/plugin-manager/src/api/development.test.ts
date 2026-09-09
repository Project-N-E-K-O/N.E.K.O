import { beforeEach, describe, expect, it, vi } from 'vitest'
import { del, post, put } from './index'
import request from '@/utils/request'
import { registerDevelopment, rebindDevelopment, removeDevelopment, runDevelopmentAction, setDevelopmentEnabled } from './development'
import { buildPluginCli } from './pluginCli'
vi.mock('./index', () => ({ get: vi.fn(), post: vi.fn(), del: vi.fn(), put: vi.fn() }))
vi.mock('@/utils/request', () => ({ default: { patch: vi.fn() } }))
beforeEach(() => vi.clearAllMocks())
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
    expect(post).toHaveBeenCalledWith('/plugin-cli/build', payload, { headers: { 'X-Neko-Development': '1' } })
  })
  it('preserves ordinary build requests and gates all-mode builds', async () => {
    await buildPluginCli({ mode: 'single', plugin: 'demo' })
    expect(post).toHaveBeenLastCalledWith('/plugin-cli/build', { mode: 'single', plugin: 'demo' })
    await buildPluginCli({ mode: 'all' })
    expect(post).toHaveBeenLastCalledWith('/plugin-cli/build', { mode: 'all' }, { headers: { 'X-Neko-Development': '1' } })
    await setDevelopmentEnabled(false)
    expect(put).toHaveBeenCalledWith('/plugins/development/settings', { enabled: false }, expect.any(Object))
  })
})
