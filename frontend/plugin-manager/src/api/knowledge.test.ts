import { beforeEach, describe, expect, it, vi } from 'vitest'

const axiosMocks = vi.hoisted(() => ({
  get: vi.fn(),
  request: vi.fn(),
  isAxiosError: vi.fn(),
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
    axiosMocks.isAxiosError.mockReset()
    axiosMocks.isAxiosError.mockReturnValue(false)
    axiosMocks.get.mockResolvedValue({ data: { bridge_token: 'fixture-token' } })
  })

  it('rejects a logical API failure with stable issues', async () => {
    axiosMocks.request.mockResolvedValue({
      data: {
        ok: false,
        issues: [{ path: 'pack_id', code: 'invalid_identifier', message: 'invalid pack id' }],
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
    expect(failure).toMatchObject({ code: 'invalid_identifier' })
  })

  it('returns a successful response unchanged', async () => {
    const payload = { ok: true, collections: [] }
    axiosMocks.request.mockResolvedValue({ data: payload })
    const { knowledgeApi } = await loadKnowledgeApi()

    await expect(knowledgeApi.collections()).resolves.toEqual(payload)
    expect(axiosMocks.request).toHaveBeenCalledWith(expect.objectContaining({
      params: undefined,
      headers: { Authorization: 'Bearer fixture-token' },
    }))
  })

  it('preserves business params while moving the bridge token to a header', async () => {
    axiosMocks.request.mockResolvedValue({ data: { ok: true, items: [] } })
    const { knowledgeApi } = await loadKnowledgeApi()

    await knowledgeApi.entries({ collection: 'meme', offset: 50 })

    expect(axiosMocks.request).toHaveBeenCalledWith(expect.objectContaining({
      params: { collection: 'meme', offset: 50 },
      headers: { Authorization: 'Bearer fixture-token' },
    }))
  })

  it('converts a non-2xx response into KnowledgeApiError', async () => {
    const error = {
      response: {
        data: {
          issues: [{ path: 'collection', code: 'not_found', message: 'collection not found' }],
        },
      },
    }
    axiosMocks.isAxiosError.mockReturnValue(true)
    axiosMocks.request.mockRejectedValue(error)
    const { KnowledgeApiError, knowledgeApi } = await loadKnowledgeApi()

    const failure = await knowledgeApi.collections().catch((caught) => caught)

    expect(failure).toBeInstanceOf(KnowledgeApiError)
    expect(failure).toMatchObject({ code: 'not_found', message: 'collection not found' })
  })
})
