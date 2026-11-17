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
import { getLocale } from '@/i18n'

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
    /** The `base_snapshot_id` a new revision must name. */
    id: number
    repo_url: string
    repo_owner: string | null
    /** The bare repository name, not `owner/repo`. */
    repo_name: string | null
    plugin_name: string
    plugin_slug: string
    zone_slug: string | null
    tags: string[]
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

/** Submission page; multi-page lookups are combined into one client page. */
export interface MarketSubmissionPage {
  items: MarketSubmission[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

/** Market `RepositoryPreview`, as `POST /preflight` returns it; only the fields the workbench reads. */
export interface RepositoryPreview {
  identity_status: SubmissionIdentityStatus
  workflow_status: SubmissionWorkflowStatus
  /** The manifest's Market plugin ID; null when no readable plugin.toml was found. */
  plugin_id?: string | null
  plugin_name?: string | null
  description?: string | null
  short_description?: string | null
  license_name?: string | null
  /** The full 40-hex commit the ref resolved to. */
  resolved_commit: string
  submitted_ref: string
  commit_url: string
  /** Localized by the Market. */
  warnings: string[]
}

/** One problem found by `POST /preflight` (Market `publication_preflight.issue`). */
export interface PreflightIssue {
  /** Always `invalid_input` today. */
  code: string
  /** The draft field the problem is about, e.g. `plugin_slug` or `tags`. */
  field: string
  /** Localized by the Market. */
  message: string
  /** Truncated to 255 characters; empty when not applicable. */
  current: string
  expected: string
  /** Localized by the Market. */
  remedy: string
}

/** Response of `POST /preflight`; it never authorizes a later write. */
export interface PreflightResult {
  ready: boolean
  issues: PreflightIssue[]
  /** Null when the repository could not be read. */
  preview: RepositoryPreview | null
  /** The raw icon URL the Market would store; null without an `icon_url`. */
  normalized_icon_url: string | null
}

/** Fields the Market's `SubmissionDraftCreate` accepts (it forbids extras). */
export interface SubmissionDraftCreate {
  repo_url: string
  /** At most {@link PLUGIN_NAME_MAX_LENGTH} characters. */
  plugin_name: string
  /** The Market plugin ID; required, and must already be a valid ID. */
  plugin_slug: string
  /** The directory holding plugin.toml; `''` is the repository root. */
  plugin_directory?: string
  /** A slug from {@link listMarketZones}; a new plugin is not approved without one. */
  zone_slug?: string
  /** A new plugin is not approved without at least one tag. */
  tags?: string[]
  description?: string
  /** At most {@link SHORT_DESCRIPTION_MAX_LENGTH} characters. */
  short_description?: string
  submitted_ref?: string
  resolved_commit?: string
  commit_url?: string
  license_name?: string
  metadata?: Record<string, unknown>
}

/** The subset of the Market's `SubmissionDraftUpdate` the workbench changes. */
export interface SubmissionDraftUpdate {
  plugin_slug?: string
  zone_slug?: string
  tags?: string[]
  submitted_ref?: string
  resolved_commit?: string
}

/** Market `SubmissionRevisionCreate`: the draft-update fields plus the base and a note. */
export interface SubmissionRevisionCreate extends SubmissionDraftUpdate {
  /** Must be the submission's `current_snapshot.id`, or the Market answers 409. */
  base_snapshot_id: number
  /** 1 to {@link SUBMISSION_NOTE_MAX_LENGTH} characters after trimming. */
  note: string
}

/** Market `SubmissionWorkspace`; only `overview` is typed. */
export interface SubmissionWorkspace {
  overview: {
    action_required: boolean
    /** Reviewer feedback in the current round that waits for the author. */
    current_feedback_action_count: number
    next_actor: string
  }
}

/** `SubmissionDraftCreate.plugin_name` max_length on the Market. */
export const PLUGIN_NAME_MAX_LENGTH = 100
/** `SubmissionDraftCreate.short_description` max_length on the Market. */
export const SHORT_DESCRIPTION_MAX_LENGTH = 255
/** `SubmissionRevisionCreate.note` max_length on the Market. */
export const SUBMISSION_NOTE_MAX_LENGTH = 1000
/** The Market's own forms let an author pick at most this many tags. */
export const SUBMISSION_TAGS_MAX = 5

/**
 * The Market's standard tags (`STANDARD_PLUGIN_TAGS`), in its display order.
 * `value` is what the Market stores; `id` names the label in the workbench i18n.
 */
export const STANDARD_SUBMISSION_TAGS = [
  { id: 'game', value: '游戏' },
  { id: 'query', value: '查询' },
  { id: 'strategy', value: '攻略' },
  { id: 'auxiliary', value: '辅助' },
  { id: 'playWith', value: '陪玩' },
  { id: 'interactive', value: '互动' },
  { id: 'emotion', value: '情感' },
  { id: 'enhance', value: '增强' },
  { id: 'function', value: '功能' },
  { id: 'tool', value: '工具' },
  { id: 'system', value: '系统' },
  { id: 'monitor', value: '监控' },
  { id: 'entertainment', value: '娱乐' },
  { id: 'music', value: '音乐' },
  { id: 'video', value: '视频' },
  { id: 'joke', value: '笑话' },
  { id: 'develop', value: '开发' },
  { id: 'code', value: '代码' },
  { id: 'api', value: 'API' },
  { id: 'ai', value: 'AI' },
  { id: 'image', value: '图像' },
  { id: 'generate', value: '生成' },
  { id: 'translate', value: '翻译' },
  { id: 'language', value: '语言' },
  { id: 'study', value: '学习' },
  { id: 'schedule', value: '日程' },
  { id: 'task', value: '任务' },
  { id: 'manage', value: '管理' },
  { id: 'remind', value: '提醒' },
  { id: 'weather', value: '天气' },
  { id: 'statistics', value: '统计' },
  { id: 'data', value: '数据' },
] as const

/** One row of the Market's public `GET /zones`; `id` is the zone slug. */
export interface MarketZone {
  id: string
  name: string
  description?: string | null
}

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
  const headers = new Headers(init?.headers)
  headers.set('Accept-Language', getLocale())
  const res = await fetchBridge(path, { ...init, headers }, { retryOnForbidden: false })
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

export interface MarketPublicationReceipt {
  id: number
  release_url: string
  status: string
}

export function listMarketPublicationReceipts(pluginId: number, requestId?: number) {
  const query = requestId ? `?request_id=${requestId}` : ''
  return call<MarketPublicationReceipt[]>(`/market/publication-requests/${pluginId}${query}`)
}

/**
 * List the signed-in account's submissions whose repository URL contains
 * `repository` (`owner/repo`); the Market matches `q` against `repo_url`.
 * Fetch all matching pages before the panel applies exact repository matching.
 */
export async function listMarketSubmissions(repository: string): Promise<MarketSubmissionPage> {
  const query = new URLSearchParams({ q: repository, page_size: '100' })
  const first = await call<MarketSubmissionPage>(`/market/submissions/mine?${query.toString()}`)
  if (first.total_pages <= 1) return first
  const items = [...first.items]
  for (let page = first.page + 1; page <= first.total_pages; page++) {
    query.set('page', String(page))
    const next = await call<MarketSubmissionPage>(`/market/submissions/mine?${query.toString()}`)
    items.push(...next.items)
  }
  return { items, total: items.length, page: 1, page_size: items.length, total_pages: 1 }
}

/**
 * Check a complete draft without writing anything: schema, identity against
 * plugin.toml at the resolved commit, zone and tags. Always 200 when signed in.
 */
export function preflightSubmission(payload: SubmissionDraftCreate): Promise<PreflightResult> {
  return call('/market/submissions/preflight', jsonInit('POST', payload))
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

/** Send a new snapshot of a submitted submission; drafts use {@link updateSubmissionDraft}. */
export function createSubmissionRevision(
  id: number,
  payload: SubmissionRevisionCreate
): Promise<MarketSubmission> {
  return call(submissionPath(id, 'revision'), jsonInit('POST', payload))
}

export function getSubmissionWorkspace(id: number): Promise<SubmissionWorkspace> {
  return call(submissionPath(id, 'workspace'))
}

export function submitSubmission(id: number): Promise<MarketSubmission> {
  return call(submissionPath(id, 'submit'), jsonInit('POST', {}))
}

export function withdrawSubmission(id: number): Promise<MarketSubmission> {
  return call(submissionPath(id, 'withdraw'), jsonInit('POST', {}))
}

/** The Market's zones, read through the public same-origin catalog proxy. */
export async function listMarketZones(): Promise<MarketZone[]> {
  const res = await fetch('/market/catalog/api/v1/zones')
  const body = await res.json().catch(() => null)
  if (!res.ok || !Array.isArray(body)) {
    throw new MarketSubmissionError(readErrorCode(body) || 'market_catalog_unavailable', res.status)
  }
  return body as MarketZone[]
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

/** Phases that take a new revision (`status` submitted or in_review). */
export const REVISABLE_PHASES: readonly SubmissionPhase[] = ['submitted', 'in_review', 'author_action']

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
  /** `current_snapshot.resolved_commit`: the commit under review, or the approved one. */
  resolvedCommit: string | null
}

/** The submission's page on the Market web site (a HashRouter app). */
export function marketSubmissionUrl(marketWebUrl: string, id: number): string {
  return `${marketWebUrl.replace(/\/+$/, '')}/#/developer/submissions?submission=${encodeURIComponent(String(id))}`
}
