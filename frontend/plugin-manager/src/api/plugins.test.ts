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

  it('silences initial hosted action errors while passing its timeout', async () => {
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
      { suppressPluginNotRunningMessage: true, timeout: 80000 },
    )
  })

  it('keeps the global error message for a user-initiated hosted action', async () => {
    postMock.mockResolvedValue({ ok: true })
    const { callPluginHostedSurfaceAction } = await import('./plugins')

    await callPluginHostedSurfaceAction('demo', 'save', {}, {
      kind: 'panel',
      id: 'main',
      userInitiated: true,
    })

    expect(postMock).toHaveBeenCalledWith(
      '/plugin/demo/hosted-ui/action/save',
      expect.objectContaining({ timeout_ms: undefined }),
      { suppressPluginNotRunningMessage: false },
    )
  })

  it('queries and switches a plugin installation with an inventory generation', async () => {
    getMock.mockResolvedValue({ candidates: [] })
    postMock.mockResolvedValue({ success: true })
    const { getPluginInstallations, switchPluginInstallation } = await import('./plugins')

    await getPluginInstallations('demo plugin')
    await switchPluginInstallation('demo plugin', 'builtin:demo', 7)

    expect(getMock).toHaveBeenCalledWith(
      '/plugin/demo%20plugin/installations',
      { suppressErrorMessage: true },
    )
    expect(postMock).toHaveBeenCalledWith(
      '/plugin/demo%20plugin/active-installation',
      { selection_id: 'builtin:demo', expected_generation: 7 },
      { suppressErrorMessage: true },
    )
  })
})
