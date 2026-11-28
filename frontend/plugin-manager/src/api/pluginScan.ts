import { get, post } from './index'

/**
 * The plugin's static scan: the report and asking for a new one. These still
 * use the scan routes under /plugin-consent; the permission panel carries no
 * scan warnings and has no rescan of its own.
 */
export type AnalysisStatus =
  'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'unavailable'
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
  /** native_loader; a minimal marker until a shared risk-tier table exists. */
  high_risk?: boolean
  /** not_analyzed only: why the part was not read. */
  reason?: string
  /** not_analyzed only: a list cut short, this many more were left out. */
  omitted?: number
}
/** Counted blind spots of one file, without lines. */
export interface AnalysisBlindSpotFile {
  file: string
  count: number
  types: Record<string, number>
  high_risk: boolean
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
  /** Counted blind spots, with lines (developer details). */
  blind_spots?: AnalysisBlindSpot[]
  /** The same blind spots summed up per file; newer servers only. */
  blind_spot_files?: AnalysisBlindSpotFile[]
  /** Parts the scan did not read: a hint, never counted; newer servers only. */
  not_analyzed?: AnalysisBlindSpot[]
}
/** The record the scan route answers with; only the scan part is read here. */
export interface PluginScanRecord {
  plugin_id: string
  version: string
  analysis: CapabilityAnalysis
}

export function getPluginScan(id: string) {
  return get<PluginScanRecord>(`/plugin-consent/plugins/${encodeURIComponent(id)}`, {
    suppressErrorMessage: true,
  })
}
export function rescanPlugin(id: string) {
  return post<PluginScanRecord>(
    `/plugin-consent/plugins/${encodeURIComponent(id)}/analysis`,
    undefined,
    { suppressErrorMessage: true },
  )
}
