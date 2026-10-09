import axios from 'axios'

/**
 * Client for the public-knowledge management API.
 *
 * Transport: `/market/knowledge/<path>?token=<bridge token>` on the plugin
 * server, which relays to Main `/api/public-knowledge/<path>` and from there
 * to the Memory Server. Every reply is a JSON object; business failures are
 * `{ok: false, reason}` and may arrive with a non-2xx HTTP status.
 */

export const MAX_KNOWLEDGE_PACK_FILE_BYTES = 10 * 1024 * 1024
const KNOWLEDGE_GET_REQUEST_TIMEOUT_MS = 15_000
const KNOWLEDGE_MUTATION_REQUEST_TIMEOUT_MS = 50_000

let bridgeToken = ''
let bridgeTokenRequest: Promise<string> | null = null

/** Every failure reason the knowledge API can report (plus job-only ones). */
export const KNOWLEDGE_FAILURE_REASONS = [
  'knowledge_starting',
  'knowledge_unavailable',
  'knowledge_busy',
  'knowledge_error',
  'knowledge_timeout',
  'knowledge_stopping',
  'knowledge_invalid_response',
  'main_server_unavailable',
  'not_found',
  'invalid_request',
  'job_in_progress',
  'capacity_entries',
  'capacity_chunks',
  'capacity_bytes',
  'too_many_chunks',
  'pack_too_large',
  'payload_too_large',
  'invalid_json',
  'unexpected_pack_field',
  'unsupported_schema_version',
  'invalid_pack_id',
  'invalid_material_type',
  'invalid_source',
  'invalid_entries',
  'too_many_entries',
  'invalid_entry',
  'unexpected_entry_field',
  'duplicate_title',
  'csrf_validation_failed',
  'operation_failed',
] as const

export type KnowledgeFailureReason = (typeof KNOWLEDGE_FAILURE_REASONS)[number]

const KNOWN_FAILURE_REASONS: ReadonlySet<string> = new Set(KNOWLEDGE_FAILURE_REASONS)

export class KnowledgeApiError extends Error {
  readonly reason: string

  constructor(reason = 'operation_failed') {
    super(reason)
    this.name = 'KnowledgeApiError'
    this.reason = reason
  }
}

/** Normalizes any thrown value into a reason that has a localized message. */
export function knowledgeFailureReason(error: unknown): KnowledgeFailureReason {
  let reason = ''
  if (error instanceof KnowledgeApiError) {
    reason = error.reason
  } else if ((error as { code?: unknown })?.code === 'ECONNABORTED') {
    reason = 'knowledge_timeout'
  }
  return KNOWN_FAILURE_REASONS.has(reason)
    ? (reason as KnowledgeFailureReason)
    : 'operation_failed'
}

export function isKnownKnowledgeReason(reason: unknown): reason is KnowledgeFailureReason {
  return typeof reason === 'string' && KNOWN_FAILURE_REASONS.has(reason)
}

// ── shapes ───────────────────────────────────────────────────────────

export type KnowledgeMaterialType = 'knowledge' | 'corpus'
export type KnowledgeServiceState = 'ready' | 'starting' | 'unavailable'
export type KnowledgeEmbeddingState = 'ready' | 'loading' | 'disabled' | 'unavailable'
export type KnowledgeVectorState = 'none' | 'complete' | 'building' | 'waiting' | 'off' | 'partial'
export type KnowledgeJobState = 'queued' | 'building' | 'active' | 'failed' | 'cancelled'
export type KnowledgeQueryResult =
  | 'matched'
  | 'miss'
  | 'timeout'
  | 'busy'
  | 'error'
  | 'disabled'
  | 'unavailable'

export interface KnowledgeAvailability {
  ready?: boolean
  enabled?: boolean
  tool_available?: boolean
}

export interface KnowledgeEnvelope extends KnowledgeAvailability {
  ok?: boolean
  reason?: string
}

export interface KnowledgeSource {
  name: string
  homepage: string
  license: string
}

export interface KnowledgeStatus extends KnowledgeAvailability {
  state: KnowledgeServiceState
  error_code?: string
  embedding?: { state: KnowledgeEmbeddingState; model_id: string | null }
  packs?: number
  entries?: number
  disabled_entries?: number
  knowledge_packs?: number
  corpus_packs?: number
  knowledge_entries?: number
  corpus_entries?: number
  chunks_total?: number
  chunks_ready?: number
  chunks_failed?: number
  indexed_percent?: number
  broken_packs?: string[]
  sources?: Array<{ pack_id: string; name: string; entries: number }>
}

export interface KnowledgeEntry {
  pack_id: string
  title: string
  terms: { alias?: string[]; recognition?: string[] }
  tags: string[]
  summary: string
  content_preview: string
  material_type: KnowledgeMaterialType
  source: KnowledgeSource
  disabled: boolean
}

export interface KnowledgeEntryDetail extends KnowledgeEntry {
  content: string
}

export interface KnowledgePack {
  pack_id: string
  source: KnowledgeSource
  declared_material_type: KnowledgeMaterialType
  material_type_override: KnowledgeMaterialType | null
  effective_material_type: KnowledgeMaterialType
  entries: number
  disabled_entries: number
  auto_context: boolean
  local_embedding: boolean
  chunks_total: number
  chunks_ready: number
  chunks_failed: number
  vector_state: KnowledgeVectorState
  broken: boolean
  installed_at: string
  updated_at: string
}

export interface KnowledgePackJob {
  job_id: string
  pack_id: string
  state: KnowledgeJobState
  reason: string
  entries_total: number
  chunks_total: number
  created_at: string
  updated_at: string
}

export interface KnowledgeDiagnosticQuery {
  timestamp: string
  mode: 'lookup' | 'sample' | string
  result: KnowledgeQueryResult
  retrieval_mode: 'bm25' | 'hybrid' | 'sample' | ''
  hits: number
  entry_title: string
  pack_id: string
  elapsed_ms: number
  error_type: string
}

export interface KnowledgeIndexBatch {
  timestamp: string
  model_id: string
  selected: number
  stored: number
  failed: number
  elapsed_ms: number
}

export interface KnowledgeStatusResponse extends KnowledgeEnvelope {
  status: KnowledgeStatus
}

export interface KnowledgeEntriesParams {
  query?: string
  pack_id?: string
  limit?: number
  offset?: number
}

export interface KnowledgeEntriesResponse extends KnowledgeEnvelope {
  total: number | null
  offset: number
  limit: number
  has_more: boolean
  items: KnowledgeEntry[]
}

export interface KnowledgeImportResponse extends KnowledgeEnvelope {
  pack_id: string
  state: KnowledgeJobState
  job_id?: string
  unchanged?: boolean
  entries_total?: number
  chunks_total?: number
}

export const ACTIVE_KNOWLEDGE_JOB_STATES: readonly KnowledgeJobState[] = ['queued', 'building']

export function isActiveKnowledgeJob(job: Pick<KnowledgePackJob, 'state'>): boolean {
  return ACTIVE_KNOWLEDGE_JOB_STATES.includes(job.state)
}

// ── transport ────────────────────────────────────────────────────────

async function token(): Promise<string> {
  if (bridgeToken) return bridgeToken
  if (!bridgeTokenRequest) {
    bridgeTokenRequest = axios
      .get('/market/bridge-token', { timeout: 3000 })
      .then((response) => {
        bridgeToken = String(response.data?.bridge_token || '')
        if (!bridgeToken) throw new Error('knowledge bridge token unavailable')
        return bridgeToken
      })
  }
  const pending = bridgeTokenRequest
  try {
    return await pending
  } finally {
    if (bridgeTokenRequest === pending) bridgeTokenRequest = null
  }
}

function isInvalidBridgeToken(error: unknown): boolean {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })
    ?.response
  const detail = response?.data?.detail
  const code = detail && typeof detail === 'object'
    ? String((detail as { code?: unknown }).code || '').trim().toLowerCase()
    : ''
  const legacyDetail = typeof detail === 'string' ? detail.trim().toLowerCase() : ''
  return (
    response?.status === 403 &&
    (
      code === 'invalid_bridge_token' ||
      legacyDetail === 'invalid bridge token' ||
      legacyDetail === '无效的 bridge token'
    )
  )
}

/** A non-2xx reply that still carries the API's `{ok: false, reason}` body. */
function structuredFailure(error: unknown): KnowledgeApiError | null {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  if (!data || typeof data !== 'object') return null
  const envelope = data as KnowledgeEnvelope
  if (envelope.ok !== false) return null
  return new KnowledgeApiError(String(envelope.reason || 'operation_failed'))
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  params?: Record<string, unknown>
  data?: unknown
  headers?: Record<string, string>
}

async function executeRequest<T extends KnowledgeEnvelope>(
  path: string,
  options: RequestOptions,
  value: string,
): Promise<T> {
  const response = await axios.request<T>({
    url: `/market/knowledge/${path}`,
    method: options.method || 'GET',
    params: { ...(options.params || {}), token: value },
    data: options.data,
    headers: options.headers,
    timeout: options.method === 'POST'
      ? KNOWLEDGE_MUTATION_REQUEST_TIMEOUT_MS
      : KNOWLEDGE_GET_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

async function request<T extends KnowledgeEnvelope>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  let data: T
  const usedToken = await token()
  try {
    try {
      data = await executeRequest<T>(path, options, usedToken)
    } catch (error) {
      if (!isInvalidBridgeToken(error)) throw error
      if (bridgeToken === usedToken) bridgeToken = ''
      data = await executeRequest<T>(path, options, await token())
    }
  } catch (error) {
    throw structuredFailure(error) ?? error
  }
  if (!data || typeof data !== 'object') throw new KnowledgeApiError('knowledge_invalid_response')
  if (data.ok === false) throw new KnowledgeApiError(String(data.reason || 'operation_failed'))
  return data
}

function post<T extends KnowledgeEnvelope>(path: string, data: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', data })
}

export const knowledgeApi = {
  status: () => request<KnowledgeStatusResponse>('status'),
  entries: (params: KnowledgeEntriesParams = {}) => {
    const cleaned: Record<string, unknown> = {}
    if (params.query) cleaned.query = params.query
    if (params.pack_id) cleaned.pack_id = params.pack_id
    if (params.limit !== undefined) cleaned.limit = params.limit
    if (params.offset !== undefined) cleaned.offset = params.offset
    return request<KnowledgeEntriesResponse>('entries', { params: cleaned })
  },
  entry: (params: { pack_id: string; title: string }) =>
    request<KnowledgeEnvelope & { entry: KnowledgeEntryDetail }>('entry', { params: { ...params } }),
  packs: () => request<KnowledgeEnvelope & { packs: KnowledgePack[] }>('packs'),
  packJobs: () => request<KnowledgeEnvelope & { jobs: KnowledgePackJob[] }>('packs/jobs'),
  diagnostics: () => request<KnowledgeEnvelope & {
    queries: KnowledgeDiagnosticQuery[]
    index_batches: KnowledgeIndexBatch[]
  }>('diagnostics/recent'),

  setEnabled: (enabled: boolean) =>
    post<KnowledgeEnvelope & { enabled: boolean }>('settings', { enabled }),
  setEntryDisabled: (data: { pack_id: string; title: string; disabled: boolean }) =>
    post<KnowledgeEnvelope & { pack_id: string; disabled: boolean; disabled_entries: number }>(
      'entry/disabled',
      data,
    ),
  /** Uploads the raw pack file bytes; the server parses and validates them. */
  importPack: async (file: Blob): Promise<KnowledgeImportResponse> => {
    if (file.size > MAX_KNOWLEDGE_PACK_FILE_BYTES) throw new KnowledgeApiError('pack_too_large')
    return request<KnowledgeImportResponse>('packs/import', {
      method: 'POST',
      data: file,
      headers: { 'Content-Type': 'application/json' },
    })
  },
  cancelPackJob: (data: { job_id: string }) =>
    post<KnowledgeEnvelope & { job_id: string }>('packs/jobs/cancel', data),
  discardPackJob: (data: { job_id: string }) =>
    post<KnowledgeEnvelope & { job_id: string }>('packs/jobs/discard', data),
  setPackAutoContext: (data: { pack_id: string; enabled: boolean }) =>
    post<KnowledgeEnvelope & { pack_id: string; auto_context: boolean }>('packs/auto-context', data),
  setPackIndexPolicy: (data: { pack_id: string; local_embedding_enabled: boolean }) =>
    post<KnowledgeEnvelope & { pack_id: string; local_embedding: boolean }>('packs/index-policy', data),
  setPackMaterialType: (data: { pack_id: string; material_type: KnowledgeMaterialType | null }) =>
    post<KnowledgeEnvelope & {
      pack_id: string
      material_type_override: KnowledgeMaterialType | null
      effective_material_type: KnowledgeMaterialType
    }>('packs/material-type', data),
  removePack: (data: { pack_id: string }) =>
    post<KnowledgeEnvelope & { pack_id: string; removed_entries: number }>('packs/remove', data),
}
