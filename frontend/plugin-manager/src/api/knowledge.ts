import axios from 'axios'

export const MAX_KNOWLEDGE_PACK_FILE_BYTES = 10 * 1024 * 1024
const KNOWLEDGE_GET_REQUEST_TIMEOUT_MS = 15_000
const KNOWLEDGE_MUTATION_REQUEST_TIMEOUT_MS = 50_000

let bridgeToken = ''
let bridgeTokenRequest: Promise<string> | null = null

interface KnowledgeEnvelope {
  ok?: boolean
  reason?: string
  error_type?: string
}

export class KnowledgeApiError extends Error {
  readonly reason: string
  readonly errorType?: string

  constructor(reason = 'operation_failed', errorType?: string) {
    super(reason)
    this.name = 'KnowledgeApiError'
    this.reason = reason
    this.errorType = errorType
  }
}

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

async function executeRequest<T extends KnowledgeEnvelope>(
  path: string,
  options: { method?: 'GET' | 'POST'; params?: any; data?: any },
  value: string,
): Promise<T> {
  const response = await axios.request<T>({
    url: `/market/knowledge/${path}`,
    method: options.method || 'GET',
    params: { ...(options.params || {}), token: value },
    data: options.data,
    timeout: options.method === 'POST'
      ? KNOWLEDGE_MUTATION_REQUEST_TIMEOUT_MS
      : KNOWLEDGE_GET_REQUEST_TIMEOUT_MS,
  })
  return response.data
}

async function request<T extends KnowledgeEnvelope>(
  path: string,
  options: { method?: 'GET' | 'POST'; params?: any; data?: any } = {},
  acceptFailure?: (data: T) => boolean,
): Promise<T> {
  let data: T
  const usedToken = await token()
  try {
    data = await executeRequest<T>(path, options, usedToken)
  } catch (error) {
    if (!isInvalidBridgeToken(error)) throw error
    if (bridgeToken === usedToken) bridgeToken = ''
    data = await executeRequest<T>(path, options, await token())
  }
  if (data?.ok === false && !acceptFailure?.(data)) {
    throw new KnowledgeApiError(
      String(data.reason || 'operation_failed'),
      data.error_type ? String(data.error_type) : undefined,
    )
  }
  return data
}

export interface KnowledgeStatus {
  name: string
  entries?: number
  integrity_ok: boolean
  status: 'ready' | 'degraded'
  available?: boolean
  disabled_entries?: number
  packs?: number
  knowledge_packs?: number
  corpus_packs?: number
  knowledge_entries?: number
  corpus_entries?: number
  chunks_total?: number
  chunks_ready?: number
  chunks_pending?: number
  chunks_stale?: number
  chunks_failed?: number
  indexed_percent?: number
  sources?: Array<{ tag: string; entries: number }>
  error_type?: string
  error_code?: string
}

interface KnowledgeStatusEnvelope extends KnowledgeEnvelope {
  ok: boolean
  status: KnowledgeStatus
}

function isConsumableDegradedStatus(data: KnowledgeStatusEnvelope): boolean {
  const status = data?.status
  return (
    data?.ok === false &&
    status !== null &&
    typeof status === 'object' &&
    status.status === 'degraded' &&
    status.available === false &&
    status.integrity_ok === false &&
    typeof status.name === 'string' &&
    status.name.trim().length > 0 &&
    typeof status.error_code === 'string' &&
    status.error_code.length > 0
  )
}

export interface KnowledgeEntrySummary {
  title: string
  terms: Record<string, string[]>
  tags: string[]
  summary: string
  content_preview?: string
  content?: string
  disabled: boolean
  score?: number
  source: { tag: string; name: string; homepage: string; license: string }
}

export interface KnowledgePackSummary {
  pack_id: string
  effective_material_type?: 'knowledge' | 'corpus'
  entries?: number
  auto_context?: boolean
  subscription?: {
    provider: string
    provider_package_id?: string
    remote_id?: string
    version: string
  }
  index_origin?: string
  index_trust?: string
  index_validation?: string
  index_fallback_reason?: string
  local_embedding_enabled?: boolean
  prebuilt_chunks_ready?: number
  prebuilt_chunks_missing?: number
}

export interface KnowledgePackIndexPolicy {
  pack_id: string
  local_embedding_enabled: boolean
}

export interface KnowledgePackJob {
  job_id: string
  pack_id: string
  state: string
  reason?: string
  indexed_percent?: number
  entries_total?: number
  chunks_total?: number
}

export const knowledgeApi = {
  status: () => request<KnowledgeStatusEnvelope>(
    'status',
    {},
    isConsumableDegradedStatus,
  ),
  entries: (params: any) => request<any>('entries', { params }),
  entry: (params: any) => request<any>('entry', { params }),
  setEntryDisabled: (data: any) => request<any>('entry/disabled', { method: 'POST', data }),
  packs: () => request<{ ok: boolean; packs: KnowledgePackSummary[] }>('packs'),
  packJobs: () => request<{ ok: boolean; jobs: KnowledgePackJob[] }>('packs/jobs'),
  discardPackJob: (data: { job_id: string }) => request<KnowledgeEnvelope>('packs/jobs/discard', { method: 'POST', data }),
  importPack: (pack: any) => request<any>('packs/import', { method: 'POST', data: { pack } }),
  setPackAutoContext: (data: any) => request<any>('packs/auto-context', { method: 'POST', data }),
  setPackMaterialType: (data: any) => request<any>('packs/material-type', { method: 'POST', data }),
  setPackIndexPolicy: (data: KnowledgePackIndexPolicy) => request<KnowledgeEnvelope>('packs/index-policy', { method: 'POST', data }),
  removePack: (data: any) => request<any>('packs/remove', { method: 'POST', data }),
  unsubscribePack: (data: { package_id: string; pack_id: string }) => request<any>('unsubscribe', { method: 'POST', data }),
  diagnostics: () => request<any>('diagnostics/recent'),
}

export async function removeManagedPack(pack: KnowledgePackSummary): Promise<any> {
  const subscription = pack.subscription
  if (!subscription) return knowledgeApi.removePack({ pack_id: pack.pack_id })
  const packageId = String(subscription.provider_package_id || '')
  if (
    subscription.provider !== 'plugin-market' ||
    !/^[1-9][0-9]{0,18}$/.test(packageId)
  ) {
    throw new KnowledgeApiError('subscription_identity_unverifiable')
  }
  return knowledgeApi.unsubscribePack({
    package_id: packageId,
    pack_id: pack.pack_id,
  })
}
