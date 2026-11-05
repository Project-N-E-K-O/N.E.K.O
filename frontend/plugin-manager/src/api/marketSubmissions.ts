/**
 * Market review-submission calls, proxied through the local plugin-server
 * bridge (`/market/submissions/*`). The server attaches the desktop OAuth
 * access token; the browser only ever sends the bridge token, so these use
 * the shared {@link fetchBridge} helper rather than the plain request client.
 *
 * Every function returns parsed JSON on success and throws a
 * {@link MarketSubmissionError} carrying the stable error code on failure, so
 * callers localize by code the same way the workbench does elsewhere.
 */
import { fetchBridge, readErrorCode } from './marketBridge'

/** Identity verdict from the Market's repository preview / preflight. */
export type SubmissionIdentityStatus = 'matched' | 'mismatched' | 'unknown'
export type SubmissionWorkflowStatus = 'available' | 'missing' | 'disabled' | 'unknown'

/** One row of `GET /mine`; only the fields the workbench reads are typed. */
export interface MarketSubmission {
  id: number | string
  status: string
  decision?: string | null
  plugin_id?: number | string | null
  plugin_directory?: string | null
  repo_url?: string | null
  repo_name?: string | null
  snapshot?: {
    repo_url?: string | null
    repo_name?: string | null
    plugin_name?: string | null
    version?: string | null
  } | null
  review_progress?: {
    phase?: string | null
    next_actor?: string | null
    terminal_kind?: string | null
  } | null
}

/** Response of `POST /repository-preview`; drives the naming-conflict hint. */
export interface RepositoryPreview {
  identity_status: SubmissionIdentityStatus
  workflow_status: SubmissionWorkflowStatus
  repository_plugin_id?: string | null
  plugin_id?: string | null
  expected_repository_name?: string | null
  plugin_name?: string | null
  description?: string | null
  short_description?: string | null
  license_name?: string | null
  resolved_commit?: string | null
  submitted_ref?: string | null
  commit_url?: string | null
  warnings: string[]
}

/** Fields the Market's `SubmissionDraftCreate` accepts (it forbids extras). */
export interface SubmissionDraftCreate {
  repo_url: string
  plugin_name: string
  /** The Market plugin ID; required, and must already be a valid ID. */
  plugin_slug: string
  plugin_directory?: string
  description?: string
  short_description?: string
  icon_url?: string
  tags?: string[]
  submitted_ref?: string
  resolved_commit?: string
  license_name?: string
  metadata?: Record<string, unknown>
}

/** Carries the stable bridge error code so callers can localize by code. */
export class MarketSubmissionError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message?: string
  ) {
    super(message || code || `HTTP ${status}`)
    this.name = 'MarketSubmissionError'
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetchBridge(path, init)
  if (!res) {
    // No bridge token: the plugin manager is not paired with the server.
    throw new MarketSubmissionError('market_bridge_unreachable', 0)
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    throw new MarketSubmissionError(
      readErrorCode(body) || 'market_submission_failed',
      res.status,
      typeof (body as { detail?: unknown })?.detail === 'string'
        ? (body as { detail: string }).detail
        : undefined
    )
  }
  return body as T
}

function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  }
}

/** List the signed-in account's submissions. Optional `status` filter is forwarded. */
export function listMarketSubmissions(status?: string): Promise<{ submissions?: MarketSubmission[] }> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return call(`/market/submissions/mine${query}`)
}

/** Preview the Market's view of a repository before creating a draft. */
export function previewSubmissionRepository(payload: {
  repo_url: string
  plugin_directory?: string
  submitted_ref?: string
}): Promise<RepositoryPreview> {
  return call('/market/submissions/repository-preview', jsonInit('POST', payload))
}

export function createSubmissionDraft(payload: SubmissionDraftCreate): Promise<MarketSubmission> {
  return call('/market/submissions/drafts', jsonInit('POST', payload))
}

export function submitSubmission(id: number | string, note?: string): Promise<MarketSubmission> {
  return call(`/market/submissions/${encodeURIComponent(String(id))}/submit`, jsonInit('POST', { note }))
}
