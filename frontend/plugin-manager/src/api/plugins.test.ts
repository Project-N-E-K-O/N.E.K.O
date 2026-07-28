import { beforeEach, describe, expect, it, vi } from 'vitest'

const postMock = vi.fn()
const getMock = vi.fn()

vi.mock('@/api', () => ({
  get: getMock,
  post: postMock,
}))

describe('plugin hosted UI API', () => {
  beforeEach(() => {
    postMock.mockReset()
    getMock.mockReset()
  })

  it('passes hosted action timeout and suppresses global error messages', async () => {
    postMock.mockResolvedValue({ ok: true })
    const { callPluginHostedSurfaceAction } = await import('./plugins')

    await callPluginHostedSurfaceAction(
      'demo plugin',
      'long action',
      { input: 'x' },
      { kind: 'panel', id: 'main', locale: 'zh-CN', timeoutMs: 80000 },
    )

    expect(postMock).toHaveBeenCalledWith(
      '/plugin/demo%20plugin/hosted-ui/action/long%20action',
      {
        args: { input: 'x' },
        kind: 'panel',
        surface_id: 'main',
        locale: 'zh-CN',
        timeout_ms: 80000,
      },
      { suppressErrorMessage: true, timeout: 80000 },
    )
  })

  it('suppresses global error messages even without a custom timeout', async () => {
    postMock.mockResolvedValue({ ok: true })
    const { callPluginHostedSurfaceAction } = await import('./plugins')

    await callPluginHostedSurfaceAction('demo', 'status')

    expect(postMock).toHaveBeenCalledWith(
      '/plugin/demo/hosted-ui/action/status',
      expect.objectContaining({ timeout_ms: undefined }),
      { suppressErrorMessage: true },
    )
  })
})
