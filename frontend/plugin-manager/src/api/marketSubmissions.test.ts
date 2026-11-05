/// <reference types="node" />
import { afterEach, describe, expect, it, vi } from 'vitest'

const fetchBridge = vi.hoisted(() => vi.fn())
vi.mock('./marketBridge', () => ({ fetchBridge, readErrorCode: (body: unknown) => {
  const record = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>
  const detail = record.detail && typeof record.detail === 'object' ? (record.detail as Record<string, unknown>) : null
  return String(detail?.code || record.code || '')
} }))

import {
  MarketSubmissionError,
  createSubmissionDraft,
  listMarketSubmissions,
  previewSubmissionRepository,
  submitSubmission,
} from './marketSubmissions'

afterEach(() => {
  fetchBridge.mockReset()
})

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('listMarketSubmissions', () => {
  it('forwards the status filter as a query string', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(200, { submissions: [] }))
    await listMarketSubmissions('closed')
    expect(fetchBridge).toHaveBeenCalledWith('/market/submissions/mine?status=closed', undefined)
  })

  it('omits the query string when no filter is given', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(200, { submissions: [] }))
    await listMarketSubmissions()
    expect(fetchBridge).toHaveBeenCalledWith('/market/submissions/mine', undefined)
  })
})

describe('write endpoints', () => {
  it('posts the draft body as JSON', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(201, { id: 1, status: 'draft' }))
    const payload = {
      repo_url: 'https://github.com/o/r',
      plugin_name: 'demo',
      plugin_slug: 'demo',
      tags: ['a'],
    }
    await createSubmissionDraft(payload)
    const [path, init] = fetchBridge.mock.calls[0]!
    expect(path).toBe('/market/submissions/drafts')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(payload)
  })

  it('posts the repository preview body', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(200, { identity_status: 'matched', workflow_status: 'available', warnings: [] }))
    await previewSubmissionRepository({ repo_url: 'https://github.com/o/r', submitted_ref: 'HEAD' })
    const [path, init] = fetchBridge.mock.calls[0]!
    expect(path).toBe('/market/submissions/repository-preview')
    expect(JSON.parse(init.body as string)).toEqual({ repo_url: 'https://github.com/o/r', submitted_ref: 'HEAD' })
  })

  it('posts a submit with the note body', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(200, { id: 152, status: 'submitted' }))
    await submitSubmission(152, 'first')
    const [path, init] = fetchBridge.mock.calls[0]!
    expect(path).toBe('/market/submissions/152/submit')
    expect(JSON.parse(init.body as string)).toEqual({ note: 'first' })
  })
})

describe('error handling', () => {
  it('throws a MarketSubmissionError carrying the bridge code on failure', async () => {
    fetchBridge.mockResolvedValue(
      jsonResponse(409, { detail: { code: 'market_email_unverified', message: 'verify your email' } })
    )
    await expect(listMarketSubmissions()).rejects.toMatchObject({
      name: 'MarketSubmissionError',
      code: 'market_email_unverified',
      status: 409,
    })
  })

  it('throws market_bridge_unreachable when the bridge returns null (no token)', async () => {
    fetchBridge.mockResolvedValue(null)
    await expect(listMarketSubmissions()).rejects.toMatchObject({
      code: 'market_bridge_unreachable',
      status: 0,
    })
  })

  it('falls back to a generic code when the body carries none', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(500, { detail: 'boom' }))
    await expect(listMarketSubmissions()).rejects.toMatchObject({ code: 'market_submission_failed', status: 500 })
  })

  it('is an Error subclass with the bridge code', async () => {
    const error = new MarketSubmissionError('market_auth_required', 409)
    expect(error).toBeInstanceOf(Error)
    expect(error.code).toBe('market_auth_required')
  })
})
