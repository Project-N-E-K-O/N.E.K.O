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

  it.each([[true, false], [false, true]])('keeps global enabled=%s separate from saved pack_enabled=%s', async (enabled, packEnabled) => {
    const payload = { ok: true, pack_id: 'p1', enabled, pack_enabled: packEnabled }
    axiosMocks.request.mockResolvedValue({ data: payload })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.setPackEnabled({ pack_id: 'p1', enabled: packEnabled })).resolves.toEqual(payload)
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/packs/enabled', method: 'POST', data: { pack_id: 'p1', enabled: packEnabled },
    })
  })

  it('rejects a logical failure with its reason', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: false, reason: 'duplicate_title' } })
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    const failure = await knowledgeApi.removePack({ pack_id: 'p1' }).catch((error) => error)

    expect(failure).toBeInstanceOf(KnowledgeApiError)
    expect(failure.reason).toBe('duplicate_title')
  })

  it('retries a saved task using only its existing identity', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true, job_id: 'retry-1', state: 'queued' } })
    const { knowledgeApi } = await loadKnowledgeApi()
    await knowledgeApi.retryPackJob({ job_id: 'original-1' })
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/packs/jobs/retry', method: 'POST', data: { job_id: 'original-1' },
    })
  })

  it('keeps management search text and private entry titles out of request URLs', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()
    await knowledgeApi.entries({ query: '私密查询', pack_id: 'imp.demo', limit: 20 })
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      params: { pack_id: 'imp.demo', limit: 20 }, headers: { 'X-Knowledge-Query': encodeURIComponent('私密查询') },
    })
    expect(axiosMocks.request.mock.calls[0]![0].params).not.toHaveProperty('query')
    await knowledgeApi.entry({ pack_id: 'imp.demo', title: '私密文件.txt · 1' })
    expect(axiosMocks.request.mock.calls[1]![0]).toMatchObject({
      params: { pack_id: 'imp.demo' }, headers: { 'X-Knowledge-Title': encodeURIComponent('私密文件.txt · 1') },
    })
    expect(axiosMocks.request.mock.calls[1]![0].params).not.toHaveProperty('title')
  })

  it('bounds the management search header to the server Unicode character limit', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()
    await knowledgeApi.entries({ query: '  ' + '😺'.repeat(201) + '  ' })
    expect(axiosMocks.request.mock.calls[0]![0].headers['X-Knowledge-Query']).toBe(encodeURIComponent('😺'.repeat(200)))
  })

  it('waits longer than the bounded complete publication flow without changing normal mutations', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true } })
    const { knowledgeApi } = await loadKnowledgeApi()
    await knowledgeApi.publish({ pack_id: 'p', artifact_id: 'a'.repeat(64), publish_confirmed: true,
      client_request_id: 'request', title: 'Title', description: '' })
    expect(axiosMocks.request.mock.calls[0]![0].timeout).toBe(250000)
    await knowledgeApi.removePack({ pack_id: 'p' })
    expect(axiosMocks.request.mock.calls[1]![0].timeout).toBe(50000)
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
    const oversized = { size: MAX_KNOWLEDGE_PACK_FILE_BYTES + 1, type: 'application/json' } as Blob

    await expect(knowledgeApi.importPack(oversized)).rejects.toMatchObject({
      reason: 'pack_too_large',
    })
    expect(axiosMocks.get).not.toHaveBeenCalled()
    expect(axiosMocks.request).not.toHaveBeenCalled()
  })

  it('uploads a document preview through the same bridge with its target identity', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true, pack_id: 'imp.test', preview: { preview_id: 'preview-1' } } })
    const { knowledgeApi } = await loadKnowledgeApi()
    const file = new Blob(['source'], { type: 'text/plain' })
    await knowledgeApi.previewSource(file, { filename: 'private.txt', pack_id: 'imp.test', doc_id: 'doc-1' })
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/sources/preview', method: 'POST', data: file,
      params: { token: 'fixture-token', pack_id: 'imp.test', doc_id: 'doc-1' },
      headers: { 'Content-Type': 'application/octet-stream', 'X-Knowledge-Filename': 'private.txt' },
    })
  })

  it('downloads a completed export as binary while retaining the bridge token', async () => {
    const blob = new Blob(['archive'], { type: 'application/zip' })
    axiosMocks.request.mockResolvedValue({ data: blob })
    const { knowledgeApi } = await loadKnowledgeApi()
    await expect(knowledgeApi.artifact('a'.repeat(64))).resolves.toEqual({ blob, filename: '' })
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({
      url: '/market/knowledge/packs/artifacts', method: 'GET', responseType: 'blob',
      params: { artifact_id: 'a'.repeat(64), token: 'fixture-token' },
    })
  })

  it('refreshes the bridge after a binary JSON error without losing the artifact', async () => {
    const blob = new Blob(['archive'])
    axiosMocks.get.mockResolvedValueOnce({ data: { bridge_token: 'stale' } }).mockResolvedValueOnce({ data: { bridge_token: 'fresh' } })
    axiosMocks.request.mockRejectedValueOnce(httpError(403, new Blob([JSON.stringify({ detail: { code: 'invalid_bridge_token' } })])))
      .mockResolvedValueOnce({ data: blob, headers: { 'content-disposition': 'attachment; filename="Notes_2026-11-19_15-30-42.neko-knowledge.zip"' } })
    const { knowledgeApi } = await loadKnowledgeApi()
    await expect(knowledgeApi.artifact('a'.repeat(64))).resolves.toEqual({ blob, filename: 'Notes_2026-11-19_15-30-42.neko-knowledge.zip' })
    expect(axiosMocks.request.mock.calls[1]![0].params.token).toBe('fresh')
  })

  it('uses the readable Unicode filename in the download response', async () => {
    const blob = new Blob(['archive'], { type: 'application/zip' })
    const filename = '历史资料_2026-11-19_15-30-42.neko-knowledge.zip'
    axiosMocks.request.mockResolvedValue({ data: blob, headers: {
      'content-disposition': `attachment; filename*=utf-8''${encodeURIComponent(filename)}`,
    } })
    const { knowledgeApi } = await loadKnowledgeApi()
    const result = await knowledgeApi.artifact('a'.repeat(64))
    expect(result.blob).toBe(blob)
    expect(result.filename).toBe(filename)
  })

  it.each(["attachment; filename*=utf-8''%E4%ZZ", 'attachment; filename="../notes.neko-knowledge.zip"',
    "attachment; filename*=utf-8''notes%0D%0A.neko-knowledge.zip"])(
    'does not use malformed or unsafe response filenames: %s', async disposition => {
      axiosMocks.request.mockResolvedValue({ data: new Blob(['archive']), headers: { 'content-disposition': disposition } })
      const { knowledgeApi } = await loadKnowledgeApi()
      expect((await knowledgeApi.artifact('a'.repeat(64))).filename).toBe('')
    },
  )

  it('does not download a successful-HTTP JSON failure as an artifact', async () => {
    axiosMocks.request.mockResolvedValue({ data: new Blob([JSON.stringify({ ok: false, reason: 'vectors_not_ready' })], { type: 'application/json' }) })
    const { knowledgeApi } = await loadKnowledgeApi()
    await expect(knowledgeApi.artifact('a'.repeat(64))).rejects.toMatchObject({ reason: 'vectors_not_ready' })
  })

  it('rejects a text JSON pack returned instead of the finished ZIP', async () => {
    axiosMocks.request.mockResolvedValue({ data: new Blob(['{"pack_id":"p1"}'], { type: 'application/json' }) })
    const { knowledgeApi } = await loadKnowledgeApi()
    await expect(knowledgeApi.artifact('a'.repeat(64))).rejects.toMatchObject({ reason: 'knowledge_invalid_response' })
  })

  it('does not forward custom release versions from an untyped caller', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true, state: 'queued' } })
    const { knowledgeApi } = await loadKnowledgeApi()
    const input = { pack_id: 'p1', version: 'user-entered', format_version: 1 }
    await knowledgeApi.exportPack(input)
    expect(axiosMocks.request.mock.calls[0]![0].data).toEqual({ pack_id: 'p1', format_version: 2 })
  })

  it('uses the archive size limit and ZIP media type for finished packages', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true, pack_id: 'p1', state: 'queued' } })
    const { knowledgeApi, MAX_KNOWLEDGE_ARCHIVE_FILE_BYTES } = await loadKnowledgeApi()
    const file = { size: 20 * 1024 * 1024, type: 'application/zip' } as Blob
    await knowledgeApi.importPack(file)
    expect(axiosMocks.request.mock.calls[0]![0].headers).toEqual({ 'Content-Type': 'application/zip' })
    await expect(knowledgeApi.importPack({ size: MAX_KNOWLEDGE_ARCHIVE_FILE_BYTES + 1, type: 'application/zip' } as Blob))
      .rejects.toMatchObject({ reason: 'pack_too_large' })
  })

  it('creates a publication UUID on LAN HTTP without randomUUID', async () => {
    vi.stubGlobal('crypto', { getRandomValues: (bytes: Uint8Array) => { bytes.fill(7); return bytes } })
    try {
      const { createKnowledgePublicationId } = await loadKnowledgeApi()
      expect(createKnowledgePublicationId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    } finally { vi.unstubAllGlobals() }
  })

  it.each([
    ['setEnabled', [false], 'settings', { enabled: false }],
    ['setPackEnabled', [{ pack_id: 'p1', enabled: false }], 'packs/enabled', { pack_id: 'p1', enabled: false }],
    ['commitSource', [{ preview_id: 'preview-1', sha256: 'digest', enabled: false, enabled_confirmed: false }], 'sources/commit', { preview_id: 'preview-1', sha256: 'digest', enabled: false, enabled_confirmed: false }],
    ['discardSourcePreview', [{ preview_id: 'preview-1' }], 'sources/preview/discard', { preview_id: 'preview-1' }],
    ['removeSource', [{ pack_id: 'p1', doc_id: 'doc-1' }], 'sources/remove', { pack_id: 'p1', doc_id: 'doc-1' }],
    ['restoreSource', [{ pack_id: 'p1', doc_id: 'doc-1', version: 1, enabled_confirmed: false }], 'sources/restore', { pack_id: 'p1', doc_id: 'doc-1', version: 1, enabled_confirmed: false }],
    ['restoreSource', [{ pack_id: 'p1', doc_id: 'doc-1', version: 1, enabled_confirmed: true }], 'sources/restore', { pack_id: 'p1', doc_id: 'doc-1', version: 1, enabled_confirmed: true }],
    ['exportPack', [{ pack_id: 'p1' }], 'packs/export', { pack_id: 'p1', format_version: 2 }],
    ['deleteExport', [{ job_id: 'export-1' }], 'packs/exports/delete', { job_id: 'export-1' }],
    ['publish', [{ pack_id: 'p1', artifact_id: 'artifact', client_request_id: 'request', publish_confirmed: true, title: 'Pack', description: '' }], 'publish', { pack_id: 'p1', artifact_id: 'artifact', client_request_id: 'request', publish_confirmed: true, title: 'Pack', description: '' }],
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
      timeout: method === 'publish' ? 250000 : 50000,
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

describe('knowledge market pairing', () => {
  beforeEach(() => {
    vi.resetModules()
    axiosMocks.request.mockReset()
  })

  it('asks the local server for a pairing code and builds the market link with it', async () => {
    axiosMocks.request.mockResolvedValue({ data: { one_time_code: 'code-1', port: 48911, expires_in: 300 } })
    const { createMarketPairing, knowledgeMarketUrl } = await loadKnowledgeApi()

    const pairing = await createMarketPairing()
    expect(axiosMocks.request.mock.calls[0]![0]).toMatchObject({ url: '/market/pair-code', method: 'POST' })
    expect(pairing).toEqual({ one_time_code: 'code-1', port: 48911 })
    expect(knowledgeMarketUrl('https://market.example/', pairing)).toBe(
      'https://market.example/#/knowledge?neko_pair=code-1&neko_port=48911',
    )
  })

  it.each([
    { one_time_code: '', port: 48911 },
    { one_time_code: 'c', port: 'x' },
    { one_time_code: 'c', port: 70000 },
  ])('rejects a malformed pairing reply %o', async (data) => {
    axiosMocks.request.mockResolvedValue({ data })
    const { createMarketPairing, KnowledgeApiError } = await loadKnowledgeApi()
    await expect(createMarketPairing()).rejects.toBeInstanceOf(KnowledgeApiError)
  })
})
