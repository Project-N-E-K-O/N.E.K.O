import axios, { type AxiosResponse } from 'axios'

let bridgeToken = ''

export interface KnowledgeIssue {
  path: string
  code: string
  message: string
}

interface KnowledgeEnvelope {
  ok?: boolean
  issues?: KnowledgeIssue[]
}

export class KnowledgeApiError extends Error {
  readonly code: string
  readonly issues: KnowledgeIssue[]

  constructor(issues: KnowledgeIssue[] = []) {
    const first = issues[0]
    super(first?.message || 'Knowledge operation failed')
    this.name = 'KnowledgeApiError'
    this.code = first?.code || 'operation_failed'
    this.issues = issues
  }
}

async function token(): Promise<string> {
  if (bridgeToken) return bridgeToken
  const response = await axios.get('/market/bridge-token', { timeout: 3000 })
  bridgeToken = String(response.data?.bridge_token || '')
  if (!bridgeToken) throw new Error('knowledge bridge token unavailable')
  return bridgeToken
}

async function request<T extends KnowledgeEnvelope>(path: string, options: { method?: 'GET' | 'POST'; params?: any; data?: any } = {}): Promise<T> {
  const value = await token()
  let response: AxiosResponse<T>
  try {
    response = await axios.request<T>({
      url: `/market/knowledge/${path}`,
      method: options.method || 'GET',
      params: options.params,
      data: options.data,
      headers: { Authorization: `Bearer ${value}` },
      timeout: 15000,
    })
  } catch (error) {
    if (!axios.isAxiosError(error)) throw error
    const payload = error.response?.data as KnowledgeEnvelope | undefined
    const issues = Array.isArray(payload?.issues) ? payload.issues : []
    throw new KnowledgeApiError(issues)
  }
  const data = response.data
  if (data?.ok === false) throw new KnowledgeApiError(data.issues || [])
  return data
}

export interface KnowledgeCollection {
  collection_id: string
  name: string
  entries?: number
  integrity_ok: boolean | null
  status: 'ready' | 'degraded'
  auto_context: boolean
  disabled_entries?: number
  packs?: number
  sources?: Array<{ tag: string; entries: number }>
  error_type?: string
}

export interface KnowledgeEntrySummary {
  collection_id: string
  title: string
  terms: Record<string, string[]>
  tags: string[]
  summary: string
  content?: string
  disabled: boolean
  score?: number
  source: { tag: string; name: string; homepage: string; license: string }
}

export const knowledgeApi = {
  collections: () => request<{ ok: boolean; collections: KnowledgeCollection[] }>('collections'),
  entries: (params: any) => request<any>('entries', { params }),
  entry: (params: any) => request<any>('entry', { params }),
  setEntryDisabled: (data: any) => request<any>('entry/disabled', { method: 'POST', data }),
  setCollectionAutoContext: (data: any) => request<any>('collection/auto-context', { method: 'POST', data }),
  packs: (collection: string) => request<any>('packs', { params: { collection } }),
  importPack: (pack: any) => request<any>('packs/import', { method: 'POST', data: { pack } }),
  setPackAutoContext: (data: any) => request<any>('packs/auto-context', { method: 'POST', data }),
  removePack: (data: any) => request<any>('packs/remove', { method: 'POST', data }),
  diagnostics: () => request<any>('diagnostics/recent'),
}
