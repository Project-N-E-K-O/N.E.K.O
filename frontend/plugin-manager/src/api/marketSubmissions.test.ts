/// <reference types="node" />
import { afterEach, describe, expect, it, vi } from 'vitest'

const fetchBridge = vi.hoisted(() => vi.fn())
vi.mock('./marketBridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./marketBridge')>()),
  fetchBridge,
}))

import {
  MarketSubmissionError,
  createSubmissionDraft,
  createSubmissionRevision,
  getSubmissionWorkspace,
  listMarketSubmissions,
  listMarketZones,
  marketSubmissionUrl,
  preflightSubmission,
  submissionMatchesRepository,
  submissionPhase,
  submitSubmission,
  updateSubmissionDraft,
  withdrawSubmission,
  type MarketSubmission,
  type PreflightResult,
} from './marketSubmissions'

afterEach(() => {
  fetchBridge.mockReset()
  vi.unstubAllGlobals()
})

describe('listMarketZones', () => {
  it('reads the zones through the public catalog proxy', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, [{ id: 'game', name: 'Game' }]))
    vi.stubGlobal('fetch', fetchMock)
    await expect(listMarketZones()).resolves.toEqual([{ id: 'game', name: 'Game' }])
    expect(fetchMock).toHaveBeenCalledWith('/market/catalog/api/v1/zones')
  })

  it('fails with a stable code when the catalog is down', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(502, {})))
    await expect(listMarketZones()).rejects.toMatchObject({ code: 'market_catalog_unavailable' })
  })
})

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function submission(overrides: Partial<MarketSubmission> = {}): MarketSubmission {
  return {
    id: 1,
    status: 'draft',
    decision: null,
    terminal_kind: null,
    plugin_id: null,
    current_snapshot: {
      id: 11,
      repo_url: 'https://github.com/owner/repo',
      repo_owner: 'owner',
      repo_name: 'repo',
      plugin_name: 'Repo',
      plugin_slug: 'repo',
      zone_slug: null,
      tags: [],
      submitted_ref: 'main',
      resolved_commit: 'abc',
    },
    review_progress: null,
    ...overrides,
  }
}

describe('listMarketSubmissions', () => {
  it('asks for this repository only, on one large page, without retrying a 403', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(200, { items: [], total: 0, page: 1, page_size: 100, total_pages: 0 }))
    await expect(listMarketSubmissions('owner/repo')).resolves.toMatchObject({ items: [] })
    expect(fetchBridge).toHaveBeenCalledWith(
      '/market/submissions/mine?q=owner%2Frepo&page_size=100',
      undefined,
      { retryOnForbidden: false }
    )
  })
})

describe('write endpoints', () => {
  it('posts the draft body as JSON', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(201, { id: 1, status: 'draft' }))
    const payload = { repo_url: 'https://github.com/o/r', plugin_name: 'demo', plugin_slug: 'demo' }
    await createSubmissionDraft(payload)
    const [path, init] = fetchBridge.mock.calls[0]!
    expect(path).toBe('/market/submissions/drafts')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(payload)
  })

  it('posts the full draft to preflight and returns the Market verdict as is', async () => {
    // Shape of publication_preflight.submission_preflight's return value.
    const verdict: PreflightResult = {
      ready: false,
      issues: [
        {
          code: 'invalid_input',
          field: 'plugin_slug',
          message: 'plugin ID does not match plugin.toml',
          current: 'demo',
          expected: 'demo_plugin',
          remedy: 'correct the fields',
        },
      ],
      preview: null,
      normalized_icon_url: null,
    }
    fetchBridge.mockResolvedValue(jsonResponse(200, verdict))
    const payload = { repo_url: 'https://github.com/o/r', plugin_name: 'demo', plugin_slug: 'demo', tags: ['工具'] }
    await expect(preflightSubmission(payload)).resolves.toEqual(verdict)
    const [path, init] = fetchBridge.mock.calls[0]!
    expect(path).toBe('/market/submissions/preflight')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(payload)
  })

  it('posts a revision with its base snapshot and note', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(200, { id: 152, status: 'submitted' }))
    const body = {
      base_snapshot_id: 11,
      note: 'fix lint',
      submitted_ref: 'a'.repeat(40),
      resolved_commit: 'a'.repeat(40),
    }
    await createSubmissionRevision(152, body)
    const [path, init] = fetchBridge.mock.calls[0]!
    expect(path).toBe('/market/submissions/152/revision')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual(body)
  })

  it('reads the workspace with a plain GET', async () => {
    const workspace = {
      overview: { action_required: true, current_feedback_action_count: 2, next_actor: 'author' },
    }
    fetchBridge.mockResolvedValue(jsonResponse(200, workspace))
    await expect(getSubmissionWorkspace(152)).resolves.toEqual(workspace)
    expect(fetchBridge).toHaveBeenCalledWith('/market/submissions/152/workspace', undefined, {
      retryOnForbidden: false,
    })
  })

  it('puts a draft update, and posts submit and withdraw with an empty body', async () => {
    fetchBridge.mockImplementation(async () => jsonResponse(200, { id: 152, status: 'draft' }))
    await updateSubmissionDraft(152, { resolved_commit: 'def', submitted_ref: 'main' })
    await submitSubmission(152)
    await withdrawSubmission(152)
    const calls = fetchBridge.mock.calls.map(([path, init]) => [path, init.method, JSON.parse(init.body as string)])
    expect(calls).toEqual([
      ['/market/submissions/152/draft', 'PUT', { resolved_commit: 'def', submitted_ref: 'main' }],
      ['/market/submissions/152/submit', 'POST', {}],
      ['/market/submissions/152/withdraw', 'POST', {}],
    ])
  })
})

describe('error handling', () => {
  it('reads a local code from a detail object', async () => {
    fetchBridge.mockResolvedValue(
      jsonResponse(409, { detail: { code: 'market_auth_pending', message: 'not resolved yet' } })
    )
    await expect(listMarketSubmissions('o/r')).rejects.toMatchObject({
      name: 'MarketSubmissionError',
      code: 'market_auth_pending',
      status: 409,
      serverMessage: 'not resolved yet',
    })
  })

  it('reads a local code next to a string detail', async () => {
    fetchBridge.mockResolvedValue(
      jsonResponse(409, { detail: 'the Market account email is not verified', code: 'market_email_unverified' })
    )
    await expect(listMarketSubmissions('o/r')).rejects.toMatchObject({ code: 'market_email_unverified' })
  })

  it('keeps the structured detail of a Market business error', async () => {
    fetchBridge.mockResolvedValue(
      jsonResponse(409, { detail: { code: 'open_submission_exists', message: 'exists', submission_id: 9 } })
    )
    const error = await createSubmissionDraft({ repo_url: 'u', plugin_name: 'n', plugin_slug: 's' }).catch((e) => e)
    expect(error).toBeInstanceOf(MarketSubmissionError)
    expect(error.code).toBe('open_submission_exists')
    expect(error.detail.submission_id).toBe(9)
  })

  it('carries the Market text of a string detail and a validation list', async () => {
    fetchBridge.mockResolvedValueOnce(jsonResponse(400, { detail: 'repository is private' }))
    await expect(listMarketSubmissions('o/r')).rejects.toMatchObject({
      code: 'market_submission_failed',
      serverMessage: 'repository is private',
    })
    fetchBridge.mockResolvedValueOnce(
      jsonResponse(422, { detail: [{ loc: ['body', 'plugin_name'], msg: 'too long' }, { msg: 'bad slug' }] })
    )
    await expect(listMarketSubmissions('o/r')).rejects.toMatchObject({ serverMessage: 'too long; bad slug' })
  })

  it('has no server message when the body carries no text', async () => {
    fetchBridge.mockResolvedValue(jsonResponse(500, {}))
    await expect(listMarketSubmissions('o/r')).rejects.toMatchObject({
      code: 'market_submission_failed',
      status: 500,
      serverMessage: '',
    })
  })

  it('throws market_bridge_unreachable when the bridge returns null (no token)', async () => {
    fetchBridge.mockResolvedValue(null)
    await expect(listMarketSubmissions('o/r')).rejects.toMatchObject({
      code: 'market_bridge_unreachable',
      status: 0,
    })
  })
})

describe('submissionMatchesRepository', () => {
  it.each([
    ['https://github.com/Owner/Repo', true],
    ['https://github.com/owner/repo.git', true],
    ['https://github.com/owner/repo/', true],
    ['https://github.com/owner/repo-extra', false],
    ['https://github.com/other/repo', false],
    ['https://gitlab.com/owner/repo', false],
  ])('%s matches owner/repo: %s', (repoUrl, expected) => {
    const item = submission({ current_snapshot: { ...submission().current_snapshot!, repo_url: repoUrl } })
    expect(submissionMatchesRepository(item, 'owner/repo')).toBe(expected)
  })

  it('never matches a submission without a snapshot', () => {
    expect(submissionMatchesRepository(submission({ current_snapshot: null }), 'owner/repo')).toBe(false)
  })
})

describe('submissionPhase', () => {
  it.each([
    [{ status: 'draft' }, 'draft'],
    [{ status: 'submitted' }, 'submitted'],
    [{ status: 'in_review' }, 'in_review'],
    [
      {
        status: 'in_review',
        review_progress: { phase: 'human_review', next_actor: 'author', title: 't', description: 'd' },
      },
      'author_action',
    ],
    [{ status: 'closed', decision: 'approved' }, 'approved'],
    [{ status: 'closed', decision: 'rejected' }, 'rejected'],
    [{ status: 'closed', decision: 'canceled', terminal_kind: 'author_withdrawn' }, 'withdrawn'],
    [{ status: 'closed', decision: 'canceled', terminal_kind: 'reviewer_closed' }, 'closed'],
    [{ status: 'closed', decision: 'superseded', terminal_kind: 'superseded' }, 'superseded'],
  ] as const)('%j is %s', (overrides, phase) => {
    expect(submissionPhase(submission(overrides as Partial<MarketSubmission>))).toBe(phase)
  })
})

describe('marketSubmissionUrl', () => {
  it('links the submission in the Market developer area', () => {
    expect(marketSubmissionUrl('https://market.example/', 152)).toBe(
      'https://market.example/#/developer/submissions?submission=152'
    )
  })
})
