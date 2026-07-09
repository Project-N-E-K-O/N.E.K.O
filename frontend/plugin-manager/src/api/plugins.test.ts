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

  it('passes hosted action timeout to the request body and axios config', async () => {
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
      { timeout: 80000 },
    )
  })
})
