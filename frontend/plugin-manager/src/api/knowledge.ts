import axios from 'axios'

/**
 * Client for the public-knowledge management API.
 *
 * Transport: `/market/knowledge/<path>?token=<bridge token>` on the plugin
 * server, which relays to Main `/api/public-knowledge/<path>` and from there
 * to the Memory Server. Replies are JSON except artifact downloads; business
 * failures are `{ok: false, reason}` and may carry a non-2xx HTTP status.
 */

export const MAX_KNOWLEDGE_PACK_FILE_BYTES = 10 * 1024 * 1024
export const MAX_KNOWLEDGE_ARCHIVE_FILE_BYTES = 64 * 1024 * 1024
export const MAX_KNOWLEDGE_SOURCE_FILE_BYTES = 16 * 1024 * 1024
const KNOWLEDGE_GET_REQUEST_TIMEOUT_MS = 15_000
const KNOWLEDGE_MUTATION_REQUEST_TIMEOUT_MS = 50_000
// The plugin bounds the complete publication flow to 240 seconds, including upload.
const KNOWLEDGE_PUBLICATION_REQUEST_TIMEOUT_MS = 250_000

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
  'job_not_retryable',
  'process_interrupted',
  'pack_changed',
  'capacity_entries',
  'capacity_chunks',
  'capacity_bytes',
  'capacity_packs',
  'too_many_chunks',
  'pack_too_large',
  'payload_too_large',
  'invalid_json',
  'invalid_pack',
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
  'subscription_identity_mismatch',
  'csrf_validation_failed',
  'operation_failed',
  'unsupported_document',
  'document_too_large',
  'document_text_too_large',
  'document_too_many_pages',
  'invalid_text_encoding',
  'invalid_docx',
  'invalid_pdf',
  'encrypted_pdf_unsupported',
  'scan_unsupported',
  'no_readable_text',
  'preview_not_found',
  'preview_changed',
  'source_not_found',
  'source_version_not_found',
  'source_confirmation_required',
  'vectors_not_ready',
  'embedding_unavailable',
  'package_artifact_hash_mismatch',
  'package_invalid_archive',
  'package_invalid_vector',
  'package_capacity_exceeded',
  'package_unsupported_format',
  'package_embedding_identity_mismatch',
  'market_auth_required',
  'market_not_connected',
  'market_publication_not_confirmed',
  'market_identity_mismatch',
  'market_publication_failed',
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
export type KnowledgeVectorState =
  | 'none'
  | 'complete'
  | 'building'
  | 'waiting'
  | 'paused'
  | 'off'
  | 'partial'
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
  /** Missing fields from older servers retain their existing enabled state. */
  enabled?: boolean
  origin?: 'builtin' | 'market' | 'imported'
  /** True only when this installation retains the pack's local source originals. */
  sources_managed?: boolean
  /** A removed source group remains manageable for version recovery only. */
  retained?: boolean
  chunks_total: number
  chunks_ready: number
  chunks_failed: number
  /** Complete stored vectors share one proven specification, regardless of the active encoder. */
  export_ready: boolean
  vector_state: KnowledgeVectorState
  broken: boolean
  /** Set for a pack installed from the plugin market; null for a local import. */
  subscription: KnowledgeSubscription | null
  installed_at: string
  updated_at: string
}

export interface KnowledgeSubscription {
  provider: string
  provider_package_id: string
  remote_id: string
  pack_id: string
  version: string
  channel: string
  artifact_sha256: string
  material_type: KnowledgeMaterialType
}

export interface KnowledgeMarketPairing {
  one_time_code: string
  port: number
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
  kind?: 'pack_import' | 'import_commit' | 'pack_export'
  result?: KnowledgeArtifact | null
}

export interface KnowledgeArtifact {
  artifact_id: string
  sha256: string
  bytes: number
  filename: string
  format_version: 2
  pack_id: string
  version: string
  name?: string
}

export interface KnowledgeArtifactDownload {
  blob: Blob
  filename: string
}

export interface KnowledgeDocument {
  doc_id: string
  name: string
  current_version: number | null
  removed?: boolean
  versions_count: number
  chars: number
  updated_at: string
}

export interface KnowledgeDocumentVersion {
  version: number
  name: string
  chars: number
  sha256: string
  created_at: string
  current: boolean
}

export interface KnowledgeDocumentPreview {
  preview_id: string
  doc_id: string
  sha256: string
  name: string
  content: string
  chars: number
  sections: Array<{ text: string; location: Record<string, unknown> }>
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
      // Retained wire response from older servers; this is not UI copy.
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
  responseType?: 'blob'
  timeout?: number
  onHeaders?: (headers: Record<string, unknown>) => void
}

/** Binary downloads still expose JSON errors and refresh an expired bridge token. */
async function decodeBinaryFailure(error: unknown): Promise<unknown> {
  const response = (error as { response?: { data?: unknown } })?.response
  if (!(response?.data instanceof Blob)) return error
  try {
    const data = JSON.parse(await response.data.text())
    return { response: { ...response, data } }
  } catch {
    return error
  }
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
    ...(options.responseType ? { responseType: options.responseType } : {}),
    timeout: options.timeout ?? (options.method === 'POST'
      ? KNOWLEDGE_MUTATION_REQUEST_TIMEOUT_MS
      : KNOWLEDGE_GET_REQUEST_TIMEOUT_MS),
  })
  options.onHeaders?.(response.headers || {})
  return response.data
}

/** Honor the server's frozen Unicode filename, never a path from a header. */
function artifactFilename(disposition: string): string {
  const encoded = /filename\*\s*=\s*utf-8''([^;]+)/i.exec(disposition)?.[1]
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;\s]+))/i.exec(disposition)
  let name = ''
  try { name = encoded ? decodeURIComponent(encoded.trim()) : plain?.[1] || plain?.[2] || '' } catch { return '' }
  const hasControl = [...name].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
  return /[\\/]/.test(name) || hasControl || !name.endsWith('.neko-knowledge.zip') ? '' : name
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
    } catch (original) {
      const error = await decodeBinaryFailure(original)
      if (!isInvalidBridgeToken(error)) throw error
      if (bridgeToken === usedToken) bridgeToken = ''
      data = await executeRequest<T>(path, options, await token())
    }
  } catch (original) {
    const error = await decodeBinaryFailure(original)
    throw structuredFailure(error) ?? error
  }
  if (!data || typeof data !== 'object') throw new KnowledgeApiError('knowledge_invalid_response')
  if (data.ok === false) throw new KnowledgeApiError(String(data.reason || 'operation_failed'))
  return data
}

function post<T extends KnowledgeEnvelope>(path: string, data: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', data })
}

/**
 * Ask the local plugin server for a one-time pairing code to hand to the
 * remote Market page, which exchanges it for the bridge token. The returned
 * port is Main's (the page reaches the plugin server through Main's proxy).
 */
export async function createMarketPairing(): Promise<KnowledgeMarketPairing> {
  const response = await axios.request({ url: '/market/pair-code', method: 'POST', timeout: 5000 })
  const code = String(response.data?.one_time_code || '')
  const port = Number(response.data?.port)
  if (!code || !Number.isInteger(port) || port <= 0 || port > 65535) {
    throw new KnowledgeApiError('knowledge_invalid_response')
  }
  return { one_time_code: code, port }
}

/** Market page URL that opens the knowledge section paired with this client. */
export function knowledgeMarketUrl(base: string, pairing: KnowledgeMarketPairing): string {
  const query = new URLSearchParams({ neko_pair: pairing.one_time_code, neko_port: String(pairing.port) })
  return `${base.replace(/\/+$/, '')}/#/knowledge?${query}`
}

/** LAN HTTP still supplies getRandomValues when randomUUID is unavailable. */
export function createKnowledgePublicationId(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export const knowledgeApi = {
  status: () => request<KnowledgeStatusResponse>('status'),
  entries: (params: KnowledgeEntriesParams = {}) => {
    const cleaned: Record<string, unknown> = {}
    if (params.pack_id) cleaned.pack_id = params.pack_id
    if (params.limit !== undefined) cleaned.limit = params.limit
    if (params.offset !== undefined) cleaned.offset = params.offset
    // Private search text and document-derived titles must never enter access-log URLs.
    return request<KnowledgeEntriesResponse>('entries', {
      // Match list_entries' existing 200-character limit before percent encoding;
      // otherwise a long Unicode query can exceed the HTTP header budget first.
      params: cleaned, headers: { 'X-Knowledge-Query': encodeURIComponent(Array.from((params.query || '').trim()).slice(0, 200).join('')) },
    })
  },
  entry: (params: { pack_id: string; title: string }) =>
    request<KnowledgeEnvelope & { entry: KnowledgeEntryDetail }>('entry', {
      params: { pack_id: params.pack_id }, headers: { 'X-Knowledge-Title': encodeURIComponent(params.title) },
    }),
  packs: () => request<KnowledgeEnvelope & { packs: KnowledgePack[] }>('packs'),
  packJobs: () => request<KnowledgeEnvelope & { jobs: KnowledgePackJob[] }>('packs/jobs'),
  diagnostics: () => request<KnowledgeEnvelope & {
    queries: KnowledgeDiagnosticQuery[]
    index_batches: KnowledgeIndexBatch[]
  }>('diagnostics/recent'),

  setEnabled: (enabled: boolean) =>
    post<KnowledgeEnvelope & { enabled: boolean }>('settings', { enabled }),
  setPackEnabled: (data: { pack_id: string; enabled: boolean }) =>
    post<KnowledgeEnvelope & { pack_id: string; enabled: boolean }>('packs/enabled', data),
  setEntryDisabled: (data: { pack_id: string; title: string; disabled: boolean }) =>
    post<KnowledgeEnvelope & { pack_id: string; disabled: boolean; disabled_entries: number }>(
      'entry/disabled',
      data,
    ),
  /** Uploads the raw pack file bytes; the server parses and validates them. */
  importPack: async (file: Blob): Promise<KnowledgeImportResponse> => {
    const archive = file.type.includes('zip') || String((file as File).name || '').toLowerCase().endsWith('.zip')
    const limit = archive ? MAX_KNOWLEDGE_ARCHIVE_FILE_BYTES : MAX_KNOWLEDGE_PACK_FILE_BYTES
    if (file.size > limit) throw new KnowledgeApiError('pack_too_large')
    return request<KnowledgeImportResponse>('packs/import', {
      method: 'POST',
      data: file,
      headers: { 'Content-Type': archive ? 'application/zip' : 'application/json' },
    })
  },
  sources: (pack_id: string) => request<KnowledgeEnvelope & { pack_id: string; sources: KnowledgeDocument[] }>(
    'sources', { params: { pack_id } },
  ),
  sourceVersions: (data: { pack_id: string; doc_id: string }) => request<KnowledgeEnvelope & {
    pack_id: string; doc_id: string; versions: KnowledgeDocumentVersion[]
  }>('sources/versions', { params: data }),
  previewSource: (file: Blob, params: { filename: string; pack_id?: string; doc_id?: string; name?: string }) => {
    if (file.size > MAX_KNOWLEDGE_SOURCE_FILE_BYTES) return Promise.reject(new KnowledgeApiError('document_too_large'))
    // Filenames and pack names belong in request headers rather than URLs,
    // which are routinely recorded by HTTP access logs and browser history.
    const { filename, name, ...identity } = params
    return request<KnowledgeEnvelope & { pack_id: string; preview: KnowledgeDocumentPreview }>('sources/preview', {
      method: 'POST', params: identity, data: file, headers: {
        'Content-Type': 'application/octet-stream',
        'X-Knowledge-Filename': encodeURIComponent(filename),
        ...(name !== undefined ? { 'X-Knowledge-Name': encodeURIComponent(name) } : {}),
      },
    })
  },
  commitSource: (data: { preview_id: string; sha256: string; enabled: boolean; enabled_confirmed: boolean }) =>
    post<KnowledgeImportResponse>('sources/commit', data),
  removeSource: (data: { pack_id: string; doc_id: string }) => post<KnowledgeImportResponse>('sources/remove', data),
  restoreSource: (data: { pack_id: string; doc_id: string; version: number }) =>
    post<KnowledgeImportResponse>('sources/restore', data),
  // The server assigns the release version. Whitelist the pack identity so an
  // untyped caller cannot forward editable versions or change the ZIP format.
  exportPack: (data: { pack_id: string }) =>
    post<KnowledgeImportResponse>('packs/export', { pack_id: data.pack_id, format_version: 2 }),
  deleteExport: (data: { job_id: string }) =>
    post<KnowledgeEnvelope & { job_id: string; artifact_retained: boolean }>('packs/exports/delete', data),
  artifact: async (artifact_id: string): Promise<KnowledgeArtifactDownload> => {
    let filename = ''
    const blob = await request<KnowledgeEnvelope & Blob>('packs/artifacts', {
      params: { artifact_id }, responseType: 'blob',
      // Keep the response name through the same authenticated/retrying transport.
      onHeaders: headers => { filename = artifactFilename(String(headers['content-disposition'] || '')) },
    })
    if (!(blob instanceof Blob)) throw new KnowledgeApiError('knowledge_invalid_response')
    if (blob.type.split(';', 1)[0] === 'application/json') {
      // Exports are ZIPs. JSON is an error envelope, never a text-pack download;
      // bound decoding while preserving the server's actionable failure reason.
      let envelope: KnowledgeEnvelope | null = null
      if (blob.size <= 64 * 1024) {
        try { envelope = JSON.parse(await blob.text()) as KnowledgeEnvelope } catch { /* Use the invalid-response reason below. */ }
      }
      if (envelope?.ok === false) throw new KnowledgeApiError(String(envelope.reason || 'operation_failed'))
      throw new KnowledgeApiError('knowledge_invalid_response')
    }
    return { blob, filename }
  },
  publish: (data: { pack_id: string; artifact_id: string; publish_confirmed: true; client_request_id: string; title: string; description: string }) =>
    request<KnowledgeEnvelope & { share: { id: number } }>('publish', {
      method: 'POST', data, timeout: KNOWLEDGE_PUBLICATION_REQUEST_TIMEOUT_MS,
    }),
  cancelPackJob: (data: { job_id: string }) =>
    post<KnowledgeEnvelope & { job_id: string }>('packs/jobs/cancel', data),
  // The server reuses frozen task input; clients never reconstruct its source set.
  retryPackJob: (data: { job_id: string }) =>
    post<KnowledgeImportResponse>('packs/jobs/retry', data),
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
