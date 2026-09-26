import { beforeEach, describe, expect, it, vi } from 'vitest'

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  request: vi.fn(),
}))

vi.mock('axios', () => ({ default: axiosMocks }))

async function loadKnowledgeApi() {
  return import('./knowledge')
}

describe('knowledge API response handling', () => {
  beforeEach(() => {
    vi.resetModules()
    axiosMocks.get.mockReset()
    axiosMocks.request.mockReset()
    axiosMocks.get.mockResolvedValue({ data: { bridge_token: 'fixture-token' } })
  })

  it('rejects a logical API failure with its reason and error type', async () => {
    axiosMocks.request.mockResolvedValue({
      data: {
        ok: false,
        reason: 'invalid_pack',
        error_type: 'ValueError',
      },
    })
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    let failure: unknown
    try {
      await knowledgeApi.importPack({})
    } catch (error) {
      failure = error
    }

    expect(failure).toBeInstanceOf(KnowledgeApiError)
    expect(failure).toMatchObject({
      reason: 'invalid_pack',
      errorType: 'ValueError',
    })
  })

  it('returns a successful response unchanged', async () => {
    const payload = { ok: true, status: { status: 'ready' } }
    axiosMocks.request.mockResolvedValue({ data: payload })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).resolves.toEqual(payload)
    expect(axiosMocks.request.mock.calls[0]![0].timeout).toBe(15000)
  })

  it('returns a structured degraded status despite ok false', async () => {
    const payload = {
      ok: false,
      status: {
        name: 'Public Knowledge',
        status: 'degraded',
        available: false,
        integrity_ok: false,
        error_code: 'knowledge_unavailable',
      },
    }
    axiosMocks.request.mockResolvedValue({ data: payload })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).resolves.toEqual(payload)
  })

  it('rejects malformed degraded status envelopes', async () => {
    axiosMocks.request.mockResolvedValue({
      data: {
        ok: false,
        status: { status: 'degraded', available: false },
      },
    })
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).rejects.toBeInstanceOf(KnowledgeApiError)
  })

  it('does not accept degraded status-shaped mutation failures', async () => {
    axiosMocks.request.mockResolvedValue({
      data: {
        ok: false,
        status: {
          name: 'Public Knowledge',
          status: 'degraded',
          available: false,
          integrity_ok: false,
          error_code: 'knowledge_unavailable',
        },
      },
    })
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.importPack({})).rejects.toBeInstanceOf(KnowledgeApiError)
    expect(axiosMocks.request.mock.calls[0]![0].timeout).toBe(50000)
  })

  it('requests durable pack job state through the knowledge bridge', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true, jobs: [] } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.packJobs()).resolves.toEqual({ ok: true, jobs: [] })
    expect(axiosMocks.request.mock.calls[0]![0].url).toBe(
      '/market/knowledge/packs/jobs',
    )
  })

  it('discards a quarantined pack job through the knowledge bridge', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(
      knowledgeApi.discardPackJob({ job_id: 'degraded-fixture' }),
    ).resolves.toEqual({ ok: true })
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/packs/jobs/discard',
      method: 'POST',
      data: { job_id: 'degraded-fixture' },
    })
  })

  it('preserves transport failures', async () => {
    const upstream = new Error('bad gateway')
    axiosMocks.request.mockRejectedValue(upstream)
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).rejects.toBe(upstream)
  })

  it('does not retry a timed-out mutation that may already have committed', async () => {
    const timeout = Object.assign(new Error('timeout'), { code: 'ECONNABORTED' })
    axiosMocks.request.mockRejectedValue(timeout)
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.removePack({ pack_id: 'fixture' })).rejects.toBe(timeout)
    expect(axiosMocks.request).toHaveBeenCalledTimes(1)
    expect(axiosMocks.request.mock.calls[0]![0].timeout).toBe(50000)
  })

  it('routes local pack removal through the main knowledge API', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { removeManagedPack } = await loadKnowledgeApi()

    await removeManagedPack({ pack_id: 'local-fixture' })

    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/packs/remove',
      method: 'POST',
      data: { pack_id: 'local-fixture' },
    })
  })

  it('routes subscribed pack removal through provider unsubscribe', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { removeManagedPack } = await loadKnowledgeApi()

    await removeManagedPack({
      pack_id: 'market-fixture',
      subscription: {
        provider: 'plugin-market',
        provider_package_id: '7',
        remote_id: 'knowledge/market-fixture',
        version: '1.0.0',
      },
    })

    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/unsubscribe',
      method: 'POST',
      data: { package_id: '7', pack_id: 'market-fixture' },
    })
  })

  it('fails closed when subscribed pack ownership is incomplete', async () => {
    const { removeManagedPack } = await loadKnowledgeApi()

    await expect(removeManagedPack({
      pack_id: 'market-fixture',
      subscription: {
        provider: 'plugin-market',
        version: '1.0.0',
      },
    })).rejects.toMatchObject({
      reason: 'subscription_identity_unverifiable',
    })
    expect(axiosMocks.request).not.toHaveBeenCalled()
  })

  it('refreshes an invalid bridge token and retries once', async () => {
    axiosMocks.get
      .mockResolvedValueOnce({ data: { bridge_token: 'stale-token' } })
      .mockResolvedValueOnce({ data: { bridge_token: 'fresh-token' } })
    axiosMocks.request
      .mockRejectedValueOnce({
        response: {
          status: 403,
          data: {
            detail: {
              code: 'invalid_bridge_token',
              message: '无效的 bridge token',
            },
          },
        },
      })
      .mockResolvedValueOnce({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).resolves.toEqual({ ok: true })

    expect(axiosMocks.get).toHaveBeenCalledTimes(2)
    expect(axiosMocks.request).toHaveBeenCalledTimes(2)
    expect(axiosMocks.request.mock.calls[0]![0].params.token).toBe('stale-token')
    expect(axiosMocks.request.mock.calls[1]![0].params.token).toBe('fresh-token')
  })

  it('keeps compatibility with the legacy localized bridge-token error', async () => {
    axiosMocks.get
      .mockResolvedValueOnce({ data: { bridge_token: 'stale-token' } })
      .mockResolvedValueOnce({ data: { bridge_token: 'fresh-token' } })
    axiosMocks.request
      .mockRejectedValueOnce({
        response: { status: 403, data: { detail: '无效的 bridge token' } },
      })
      .mockResolvedValueOnce({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).resolves.toEqual({ ok: true })
    expect(axiosMocks.get).toHaveBeenCalledTimes(2)
  })

  it('shares one bridge-token fetch between concurrent requests', async () => {
    let resolveToken!: (value: { data: { bridge_token: string } }) => void
    axiosMocks.get.mockReturnValue(
      new Promise((resolve) => {
        resolveToken = resolve
      })
    )
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()

    const first = knowledgeApi.status()
    const second = knowledgeApi.packs()
    resolveToken({ data: { bridge_token: 'shared-token' } })
    await Promise.all([first, second])

    expect(axiosMocks.get).toHaveBeenCalledTimes(1)
    expect(axiosMocks.request).toHaveBeenCalledTimes(2)
  })

  it('does not clear a refreshed token when a delayed stale 403 arrives', async () => {
    let rejectFirst!: (reason: unknown) => void
    let rejectSecond!: (reason: unknown) => void
    const invalidToken = {
      response: {
        status: 403,
        data: { detail: { code: 'invalid_bridge_token' } },
      },
    }
    axiosMocks.get
      .mockResolvedValueOnce({ data: { bridge_token: 'stale-token' } })
      .mockResolvedValueOnce({ data: { bridge_token: 'fresh-token' } })
    axiosMocks.request
      .mockImplementationOnce(() => new Promise((_resolve, reject) => {
        rejectFirst = reject
      }))
      .mockImplementationOnce(() => new Promise((_resolve, reject) => {
        rejectSecond = reject
      }))
      .mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()

    const first = knowledgeApi.status()
    const second = knowledgeApi.packs()
    await vi.waitFor(() => expect(axiosMocks.request).toHaveBeenCalledTimes(2))
    rejectFirst(invalidToken)
    await vi.waitFor(() => expect(axiosMocks.request).toHaveBeenCalledTimes(3))
    rejectSecond(invalidToken)
    await Promise.all([first, second])

    expect(axiosMocks.get).toHaveBeenCalledTimes(2)
    expect(axiosMocks.request).toHaveBeenCalledTimes(4)
    expect(axiosMocks.request.mock.calls[2]![0].params.token).toBe('fresh-token')
    expect(axiosMocks.request.mock.calls[3]![0].params.token).toBe('fresh-token')
  })
})

describe('knowledge vector presentation state', () => {
  it.each([
    [{ prebuilt_chunks_ready: 0, prebuilt_chunks_missing: 0 }, 'none'],
    [{ prebuilt_chunks_ready: 4, prebuilt_chunks_missing: 0 }, 'complete'],
    [{ prebuilt_chunks_ready: 0, prebuilt_chunks_missing: 4, local_embedding_enabled: true }, 'building'],
    [{ prebuilt_chunks_ready: 2, prebuilt_chunks_missing: 2, local_embedding_enabled: false }, 'partial'],
    [{ prebuilt_chunks_ready: 0, prebuilt_chunks_missing: 4, local_embedding_enabled: false }, 'waiting'],
    [{ prebuilt_chunks_ready: -2, prebuilt_chunks_missing: -3, local_embedding_enabled: true }, 'none'],
    [{ prebuilt_chunks_ready: 4, prebuilt_chunks_missing: -3 }, 'complete'],
  ])('derives one state for class and label consumers: %j', async (pack, expected) => {
    const { packVectorState } = await loadKnowledgeApi()
    expect(packVectorState(pack)).toBe(expected)
  })

  it('aggregates all packs by explicit priority independent of input order', async () => {
    const { aggregateVectorState } = await loadKnowledgeApi()
    const states = ['complete', 'waiting', 'partial', 'building'] as const

    expect(aggregateVectorState(states, { ready: 9, total: 9 })).toBe('building')
    expect(aggregateVectorState([...states].reverse(), { ready: 9, total: 9 })).toBe('building')
    expect(aggregateVectorState(['complete', 'partial'], { ready: 9, total: 9 })).toBe('partial')
  })

  it('uses global counts only when there are no pack states', async () => {
    const { aggregateVectorState } = await loadKnowledgeApi()

    expect(aggregateVectorState([], { ready: 4, total: 4 })).toBe('complete')
    expect(aggregateVectorState([], { ready: 2, total: 4 })).toBe('partial')
    expect(aggregateVectorState([], { ready: 0, total: 4 })).toBe('waiting')
    expect(aggregateVectorState([], { ready: -1, total: -1 })).toBe('none')
  })
})
