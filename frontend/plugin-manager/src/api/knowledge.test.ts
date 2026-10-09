import { beforeEach, describe, expect, it, vi } from 'vitest'

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  request: vi.fn(),
}))

vi.mock('axios', () => ({ default: axiosMocks }))

async function loadKnowledgeApi() {
  return import('./knowledge')
}

function httpError(status: number, data: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data } })
}

describe('knowledge API client', () => {
  beforeEach(() => {
    vi.resetModules()
    axiosMocks.get.mockReset()
    axiosMocks.request.mockReset()
    axiosMocks.get.mockResolvedValue({ data: { bridge_token: 'fixture-token' } })
  })

  it('returns a successful status reply unchanged through the bridge path', async () => {
    const payload = {
      ok: true,
      ready: true,
      enabled: true,
      tool_available: true,
      status: { state: 'ready', error_code: '', embedding: { state: 'ready', model_id: 'm' } },
    }
    axiosMocks.request.mockResolvedValue({ data: payload })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).resolves.toEqual(payload)
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/status',
      method: 'GET',
      params: { token: 'fixture-token' },
      timeout: 15000,
    })
  })

  it('rejects a logical failure with its reason', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: false, reason: 'duplicate_title' } })
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    const failure = await knowledgeApi.removePack({ pack_id: 'p1' }).catch((error) => error)

    expect(failure).toBeInstanceOf(KnowledgeApiError)
    expect(failure.reason).toBe('duplicate_title')
  })

  it('reads the failure reason from a non-2xx reply body', async () => {
    axiosMocks.request.mockRejectedValue(httpError(503, { ok: false, reason: 'knowledge_starting' }))
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    const failure = await knowledgeApi.packs().catch((error) => error)

    expect(failure).toBeInstanceOf(KnowledgeApiError)
    expect(failure.reason).toBe('knowledge_starting')
  })

  it('falls back to operation_failed when a failure carries no reason', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: false, job_id: 'j1' } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.cancelPackJob({ job_id: 'j1' })).rejects.toMatchObject({
      reason: 'operation_failed',
    })
  })

  it('preserves transport failures without a structured body', async () => {
    const upstream = new Error('bad gateway')
    axiosMocks.request.mockRejectedValue(upstream)
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.status()).rejects.toBe(upstream)
  })

  it('does not retry a timed-out mutation that may already have committed', async () => {
    const timeout = Object.assign(new Error('timeout'), { code: 'ECONNABORTED' })
    axiosMocks.request.mockRejectedValue(timeout)
    const { knowledgeApi, knowledgeFailureReason } = await loadKnowledgeApi()

    const failure = await knowledgeApi.removePack({ pack_id: 'p1' }).catch((error) => error)

    expect(failure).toBe(timeout)
    expect(knowledgeFailureReason(failure)).toBe('knowledge_timeout')
    expect(axiosMocks.request).toHaveBeenCalledTimes(1)
    expect(axiosMocks.request.mock.calls[0]![0].timeout).toBe(50000)
  })

  it('refreshes an invalid bridge token once and retries', async () => {
    axiosMocks.get
      .mockResolvedValueOnce({ data: { bridge_token: 'stale-token' } })
      .mockResolvedValueOnce({ data: { bridge_token: 'fresh-token' } })
    axiosMocks.request
      .mockRejectedValueOnce(httpError(403, { detail: { code: 'invalid_bridge_token' } }))
      .mockResolvedValueOnce({ data: { ok: true, packs: [] } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.packs()).resolves.toEqual({ ok: true, packs: [] })
    expect(axiosMocks.get).toHaveBeenCalledTimes(2)
    expect(axiosMocks.request).toHaveBeenCalledTimes(2)
    expect(axiosMocks.request.mock.calls[1]![0].params).toEqual({ token: 'fresh-token' })
  })

  it('also recognizes the legacy Chinese invalid-token detail', async () => {
    axiosMocks.request
      .mockRejectedValueOnce(httpError(403, { detail: '无效的 bridge token' }))
      .mockResolvedValueOnce({ data: { ok: true, jobs: [] } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.packJobs()).resolves.toEqual({ ok: true, jobs: [] })
    expect(axiosMocks.request.mock.calls[0]![0].url).toBe('/market/knowledge/packs/jobs')
    expect(axiosMocks.request).toHaveBeenCalledTimes(2)
  })

  it('uploads the raw pack file as a JSON body without an envelope', async () => {
    axiosMocks.request.mockResolvedValue({
      data: { ok: true, job_id: 'j1', pack_id: 'p1', state: 'queued' },
    })
    const { knowledgeApi } = await loadKnowledgeApi()
    const file = new Blob(['{"pack_id":"p1"}'], { type: 'application/json' })

    await expect(knowledgeApi.importPack(file)).resolves.toMatchObject({ job_id: 'j1' })
    const call = axiosMocks.request.mock.calls[0]![0]
    expect(call).toMatchObject({
      url: '/market/knowledge/packs/import',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    expect(call.data).toBe(file)
  })

  it('refuses a pack file over 10 MiB before uploading', async () => {
    const { knowledgeApi, MAX_KNOWLEDGE_PACK_FILE_BYTES } = await loadKnowledgeApi()
    const oversized = { size: MAX_KNOWLEDGE_PACK_FILE_BYTES + 1 } as Blob

    await expect(knowledgeApi.importPack(oversized)).rejects.toMatchObject({
      reason: 'pack_too_large',
    })
    expect(axiosMocks.get).not.toHaveBeenCalled()
    expect(axiosMocks.request).not.toHaveBeenCalled()
  })

  it.each([
    ['setEnabled', [false], 'settings', { enabled: false }],
    ['setEntryDisabled', [{ pack_id: 'p1', title: 'T', disabled: true }], 'entry/disabled', { pack_id: 'p1', title: 'T', disabled: true }],
    ['cancelPackJob', [{ job_id: 'j1' }], 'packs/jobs/cancel', { job_id: 'j1' }],
    ['discardPackJob', [{ job_id: 'j1' }], 'packs/jobs/discard', { job_id: 'j1' }],
    ['setPackAutoContext', [{ pack_id: 'p1', enabled: true }], 'packs/auto-context', { pack_id: 'p1', enabled: true }],
    ['setPackIndexPolicy', [{ pack_id: 'p1', local_embedding_enabled: false }], 'packs/index-policy', { pack_id: 'p1', local_embedding_enabled: false }],
    ['setPackMaterialType', [{ pack_id: 'p1', material_type: null }], 'packs/material-type', { pack_id: 'p1', material_type: null }],
    ['removePack', [{ pack_id: 'p1' }], 'packs/remove', { pack_id: 'p1' }],
  ] as const)('%s posts to %s', async (method, args, path, body) => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await (knowledgeApi[method] as (...values: unknown[]) => Promise<unknown>)(...args)

    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: `/market/knowledge/${path}`,
      method: 'POST',
      data: body,
      timeout: 50000,
    })
  })

  it('omits empty entry filters from the query string', async () => {
    axiosMocks.request.mockResolvedValue({
      data: { ok: true, total: 0, offset: 0, limit: 50, has_more: false, items: [] },
    })
    const { knowledgeApi } = await loadKnowledgeApi()

    await knowledgeApi.entries({ query: '', pack_id: 'p1', limit: 50, offset: 0 })

    expect(axiosMocks.request.mock.calls[0]![0].params).toEqual({
      pack_id: 'p1',
      limit: 50,
      offset: 0,
      token: 'fixture-token',
    })
  })

  it('has a translation for every failure reason in every locale', async () => {
    const { KNOWLEDGE_FAILURE_REASONS } = await loadKnowledgeApi()
    const locales = import.meta.glob('../i18n/locales/*.ts', { eager: true }) as Record<
      string,
      { default: { knowledge: { reasons: Record<string, string> } } }
    >
    expect(Object.keys(locales).length).toBeGreaterThanOrEqual(8)
    for (const [file, module] of Object.entries(locales)) {
      const reasons = module.default.knowledge.reasons
      const missing = KNOWLEDGE_FAILURE_REASONS.filter((reason) => !reasons[reason])
      expect(missing, file).toEqual([])
    }
  })

  it('maps unknown reasons to operation_failed', async () => {
    const { KnowledgeApiError, knowledgeFailureReason } = await loadKnowledgeApi()

    expect(knowledgeFailureReason(new KnowledgeApiError('capacity_chunks'))).toBe('capacity_chunks')
    expect(knowledgeFailureReason(new KnowledgeApiError('invalid_pack'))).toBe('invalid_pack')
    expect(knowledgeFailureReason(new KnowledgeApiError('something_new'))).toBe('operation_failed')
    expect(knowledgeFailureReason(new Error('boom'))).toBe('operation_failed')
  })
})
