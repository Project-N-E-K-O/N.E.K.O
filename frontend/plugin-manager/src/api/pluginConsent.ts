import { del, get, post } from './index'

export type AnalysisStatus =
  'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'unavailable'
export type ConsentState = 'analyzing' | 'confirm' | 'clear'
export type ConsentDecision = 'approved' | 'denied'
export interface CapabilityEvidence {
  capability: string
  method: string
  file: string
  line: number
}
export interface PermissionFinding {
  file: string
  line: number
  method?: string
  basis?: string
}
/** One permission of the analysis: declared in plugin.toml, found in code, or both. */
export interface AnalyzedPermission {
  declared: string | null
  findings: PermissionFinding[]
  basis?: string
}
/** A place the static analysis could not see through. */
export interface AnalysisBlindSpot {
  type: string
  file: string | null
  line: number | null
}
export interface CapabilityAnalysis {
  status: AnalysisStatus
  analysis_id?: string
  version?: string
  analyzed_at?: string
  capabilities: string[]
  evidence: CapabilityEvidence[]
  warnings: Array<{ code: string; file?: string }>
  files_analyzed?: number
  /** Keyed by permission id; absent on reports that failed or are still pending. */
  permissions?: Record<string, AnalyzedPermission>
  blind_spots?: AnalysisBlindSpot[]
}
export interface PluginConsent {
  plugin_id: string
  version: string
  decision: ConsentDecision | null
  decided_at: string | null
  approved_capabilities: string[]
  unconfirmed: string[]
  state: ConsentState
  upgraded: boolean
  analysis: CapabilityAnalysis
}
export interface PluginConsentSummary {
  state: ConsentState
  decision: ConsentDecision | null
  upgraded: boolean
  /** Blind spots of the cached analysis; older servers omit it. */
  blind_spots?: number
}
export interface ConsentSnapshot {
  version: string
  unconfirmed: string[]
  analysis_status: AnalysisStatus
}
export interface PluginConsentNotice extends ConsentSnapshot {
  plugin_id: string
  name: string
  upgraded: boolean
}
export function getPluginConsent(id: string) {
  return get<PluginConsent>(`/plugin-consent/plugins/${encodeURIComponent(id)}`, {
    suppressErrorMessage: true,
  })
}
export function approvePluginConsent(id: string, snapshot: ConsentSnapshot) {
  return post<PluginConsent>(
    `/plugin-consent/plugins/${encodeURIComponent(id)}`,
    {
      approved: true,
      snapshot: {
        version: snapshot.version,
        unconfirmed: snapshot.unconfirmed,
        analysis_status: snapshot.analysis_status,
      },
    },
    { suppressErrorMessage: true }
  )
}
export function rejectPluginConsent(id: string) {
  return post<PluginConsent>(
    `/plugin-consent/plugins/${encodeURIComponent(id)}`,
    { approved: false },
    { suppressErrorMessage: true }
  )
}
export function revokePluginConsent(id: string) {
  return del<PluginConsent>(`/plugin-consent/plugins/${encodeURIComponent(id)}`, {
    suppressErrorMessage: true,
  })
}
export function getPluginConsentNotices() {
  return get<PluginConsentNotice[]>('/plugin-consent/notices', {
    suppressErrorMessage: true,
  })
}
export function analyzePluginCapabilities(id: string) {
  return post<PluginConsent>(
    `/plugin-consent/plugins/${encodeURIComponent(id)}/analysis`,
    undefined,
    {
      suppressErrorMessage: true,
    }
  )
}
