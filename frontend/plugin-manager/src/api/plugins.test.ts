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

  it('merges locale with existing plugin list parameters', async () => {
    const { getPlugins } = await import('./plugins')

    getPlugins('zh-CN', {
      params: { source: 'local' },
      timeout: 1000,
    })

    expect(getMock).toHaveBeenCalledWith('/plugins', {
      params: { source: 'local', locale: 'zh-CN' },
      timeout: 1000,
    })
  })

  it('preserves URLSearchParams when merging locale', async () => {
    const { getPlugins } = await import('./plugins')
    const input = new URLSearchParams([
      ['source', 'local'],
      ['tag', 'one'],
      ['tag', 'two'],
    ])

    getPlugins('zh-CN', { params: input })

    const requestConfig = getMock.mock.calls[0]?.[1]
    expect(requestConfig.params).toBeInstanceOf(URLSearchParams)
    expect(Array.from(requestConfig.params.entries())).toEqual([
      ['source', 'local'],
      ['tag', 'one'],
      ['tag', 'two'],
      ['locale', 'zh-CN'],
    ])
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

  it('passes an action abort signal to the HTTP request', async () => {
    postMock.mockResolvedValue({ ok: true })
    const { callPluginHostedSurfaceAction } = await import('./plugins')
    const controller = new AbortController()

    await callPluginHostedSurfaceAction('demo', 'slow', {}, {
      kind: 'panel',
      id: 'main',
      signal: controller.signal,
    })

    expect(postMock).toHaveBeenCalledWith(
      '/plugin/demo/hosted-ui/action/slow',
      expect.any(Object),
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it('passes the requested locale when loading hosted surface source', async () => {
    getMock.mockResolvedValue({ source: 'Guide' })
    const { getPluginHostedSurfaceSource } = await import('./plugins')

    await getPluginHostedSurfaceSource('study companion', {
      kind: 'docs',
      id: 'onboarding',
      locale: 'pt',
    })

    expect(getMock).toHaveBeenCalledWith(
      '/plugin/study%20companion/hosted-ui/source',
      {
        params: {
          kind: 'docs',
          id: 'onboarding',
          locale: 'pt',
        },
      },
    )
  })
})
