/**
 * Market review-submission calls, proxied through the local plugin-server
 * bridge (`/market/submissions/*`). The server attaches the desktop OAuth
 * access token; the browser only ever sends the bridge token, so these use
 * the shared {@link fetchBridge} helper rather than the plain request client.
 *
 * Every function returns parsed JSON on success and throws a
 * {@link MarketSubmissionError} carrying the stable error code, the Market's
 * own message and its structured detail on failure, so callers localize by
 * code and fall back to the Market's text.
 */
import { fetchBridge, readErrorCode } from './marketBridge'

/** Identity verdict from the Market's repository preview / preflight. */
export type SubmissionIdentityStatus = 'matched' | 'mismatched' | 'unknown'
export type SubmissionWorkflowStatus = 'available' | 'missing' | 'disabled' | 'unknown'

/** One row of `GET /mine` (Market `PluginSubmission`); only the fields the workbench reads are typed. */
export interface MarketSubmission {
  id: number
  status: 'draft' | 'submitted' | 'in_review' | 'closed'
  decision: 'approved' | 'rejected' | 'canceled' | 'superseded' | null
  terminal_kind?: 'approved' | 'rejected' | 'author_withdrawn' | 'reviewer_closed' | 'superseded' | 'unknown' | null
  /** The Market plugin row id; null until the submission is approved. */
  plugin_id: number | null
  current_snapshot?: {
    repo_url: string
    repo_owner: string | null
    /** The bare repository name, not `owner/repo`. */
    repo_name: string | null
    plugin_name: string
    plugin_slug: string
    submitted_ref: string | null
    resolved_commit: string | null
  } | null
  review_progress?: {
    phase: string
    next_actor: 'system' | 'author' | 'reviewer' | 'none'
    /** Localized by the Market. */
    title: string
    description: string
    diagnostic_code?: string | null
  } | null
}

/** Market `PaginatedResponse` of `GET /mine`. */
export interface MarketSubmissionPage {
  items: MarketSubmission[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/** Response of `POST /repository-preview`. */
export interface RepositoryPreview {
  identity_status: SubmissionIdentityStatus
  workflow_status: SubmissionWorkflowStatus
  repository_plugin_id?: string | null
  /** The manifest's Market plugin ID; null when no readable plugin.toml was found. */
  plugin_id?: string | null
  plugin_name?: string | null
  description?: string | null
  short_description?: string | null
  license_name?: string | null
  resolved_commit: string
  submitted_ref: string
  /** Localized by the Market. */
  warnings: string[]
}

/** Fields the Market's `SubmissionDraftCreate` accepts (it forbids extras). */
export interface SubmissionDraftCreate {
  repo_url: string
  /** At most {@link PLUGIN_NAME_MAX_LENGTH} characters. */
  plugin_name: string
  /** The Market plugin ID; required, and must already be a valid ID. */
  plugin_slug: string
  description?: string
  short_description?: string
  submitted_ref?: string
  resolved_commit?: string
  license_name?: string
}

/** The subset of the Market's `SubmissionDraftUpdate` the workbench changes. */
export interface SubmissionDraftUpdate {
  plugin_slug?: string
  submitted_ref?: string
  resolved_commit?: string
}

/** `SubmissionDraftCreate.plugin_name` max_length on the Market. */
export const PLUGIN_NAME_MAX_LENGTH = 100

/** Carries the stable error code, the Market's message and its structured detail. */
export class MarketSubmissionError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message?: string,
    readonly detail: Record<string, unknown> = {}
  ) {
    super(message || code || `HTTP ${status}`)
    this.name = 'MarketSubmissionError'
  }

  /** The human-readable text the server or Market sent, if any. */
  get serverMessage(): string {
    return this.message === this.code || this.message === `HTTP ${this.status}` ? '' : this.message
  }
}

/** Text of a FastAPI `detail`: a string, a `{code, message}` dict, or a validation list. */
function detailMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === 'object' ? (item as { msg?: unknown }).msg : item))
      .filter((msg): msg is string => typeof msg === 'string' && !!msg)
      .join('; ')
  }
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    return typeof message === 'string' ? message : ''
  }
  return ''
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  // The Market's own 403s are passed through and must not be repeated; a
  // bridge-token 403 only follows a server restart mid-session (reload fixes it).
  const res = await fetchBridge(path, init, { retryOnForbidden: false })
  if (!res) {
    // No bridge token: the plugin manager is not paired with the server.
    throw new MarketSubmissionError('market_bridge_unreachable', 0)
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail
    throw new MarketSubmissionError(
      readErrorCode(body) || 'market_submission_failed',
      res.status,
      detailMessage(detail),
      detail && typeof detail === 'object' && !Array.isArray(detail)
        ? (detail as Record<string, unknown>)
        : {}
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

const submissionPath = (id: number, action: string) =>
  `/market/submissions/${encodeURIComponent(String(id))}/${action}`

/**
 * List the signed-in account's submissions whose repository URL contains
 * `repository` (`owner/repo`); the Market matches `q` against `repo_url`.
 */
export function listMarketSubmissions(repository: string): Promise<MarketSubmissionPage> {
  const query = new URLSearchParams({ q: repository, page_size: '100' })
  return call(`/market/submissions/mine?${query.toString()}`)
}

/** Preview the Market's view of a repository before creating or refreshing a draft. */
export function previewSubmissionRepository(payload: {
  repo_url: string
  submitted_ref?: string
}): Promise<RepositoryPreview> {
  return call('/market/submissions/repository-preview', jsonInit('POST', payload))
}

export function createSubmissionDraft(payload: SubmissionDraftCreate): Promise<MarketSubmission> {
  return call('/market/submissions/drafts', jsonInit('POST', payload))
}

export function updateSubmissionDraft(
  id: number,
  payload: SubmissionDraftUpdate
): Promise<MarketSubmission> {
  return call(submissionPath(id, 'draft'), jsonInit('PUT', payload))
}

export function submitSubmission(id: number): Promise<MarketSubmission> {
  return call(submissionPath(id, 'submit'), jsonInit('POST', {}))
}

export function withdrawSubmission(id: number): Promise<MarketSubmission> {
  return call(submissionPath(id, 'withdraw'), jsonInit('POST', {}))
}

/** `owner/repo` of a GitHub URL, lower-cased; null for anything else. */
function githubRepository(url: string): string | null {
  const match = /^(?:https?:\/\/|git@)(?:www\.)?github\.com[/:]([^/\s]+)\/([^/\s#?]+?)(?:\.git)?\/?$/i.exec(
    url.trim()
  )
  return match ? `${match[1]}/${match[2]}`.toLowerCase() : null
}

/** True when the submission's current snapshot is exactly this `owner/repo`. */
export function submissionMatchesRepository(item: MarketSubmission, repository: string): boolean {
  const url = item.current_snapshot?.repo_url
  return !!url && githubRepository(url) === repository.toLowerCase()
}

/** The state the workbench shows for one submission. */
export type SubmissionPhase =
  | 'draft'
  | 'submitted'
  | 'in_review'
  | 'author_action'
  | 'approved'
  | 'rejected'
  | 'withdrawn'
  | 'superseded'
  | 'closed'

/** Phases after which the Market accepts a new draft for the same repository. */
export const RESUBMITTABLE_PHASES: readonly SubmissionPhase[] = [
  'rejected',
  'withdrawn',
  'superseded',
  'closed',
]

/** Map the Market's status, decision, terminal kind and next actor onto one phase. */
export function submissionPhase(item: MarketSubmission): SubmissionPhase {
  if (item.status === 'closed') {
    if (item.decision === 'approved') return 'approved'
    if (item.decision === 'rejected') return 'rejected'
    if (item.decision === 'superseded' || item.terminal_kind === 'superseded') return 'superseded'
    if (item.terminal_kind === 'author_withdrawn') return 'withdrawn'
    return 'closed'
  }
  if (item.status === 'draft') return 'draft'
  if (item.review_progress?.next_actor === 'author') return 'author_action'
  return item.status === 'in_review' ? 'in_review' : 'submitted'
}

/** What the submission panel reports to the publish steps. */
export interface SubmissionPanelState {
  /** Null when there is no matching submission (or it could not be read). */
  phase: SubmissionPhase | null
  /** The approved Market plugin row id, when there is one. */
  marketPluginId: number | null
}

/** The submission's page on the Market web site (a HashRouter app). */
export function marketSubmissionUrl(marketWebUrl: string, id: number): string {
  return `${marketWebUrl.replace(/\/+$/, '')}/#/developer/submissions?submission=${encodeURIComponent(String(id))}`
}
