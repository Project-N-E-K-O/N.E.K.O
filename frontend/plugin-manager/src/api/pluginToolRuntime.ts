/** Types of the shared plugin tool layer (plugin/server/infrastructure/plugin_tool_runtime.py). */

/**
 * The phases of plugin_tool_runtime.ProgressPhase this page has text for.
 * Any other phase the server sends reads as the generic "preparing".
 */
export type ToolProgressPhase = 'downloading_uv' | 'checking_uv' | 'installing_python' | 'checking_python'
/** Mirrors plugin_tool_runtime.ErrorCode. */
export type ToolErrorCode =
  | 'unsupported_platform'
  | 'download_failed'
  | 'checksum_mismatch'
  | 'python_install_failed'
  | 'python_mismatch'
  | 'canceled'
  | 'timeout'
  | 'busy'
  | 'internal_error'
export interface ToolProgress {
  phase: ToolProgressPhase
  downloaded_bytes: number | null
  total_bytes: number | null
}
/** Mirrors plugin_tool_runtime.RuntimeStatus. */
export interface ToolRuntimeStatus {
  state: 'source' | 'ready' | 'missing' | 'preparing' | 'failed' | 'unsupported'
  packaged: boolean
  uv_version: string
  python: string | null
  uv: string | null
  progress: ToolProgress | null
  error_code: ToolErrorCode | null
  error_detail: string | null
}
