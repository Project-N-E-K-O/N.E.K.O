/** Localized text for the plugin tool environment of the publishing toolchain step. */
import type { Toolchain, ToolEnvironmentProgress } from '@/api/pluginProjects'
import type { AuthoringMessageKey } from '@/i18n/authoring'

/** Whether the CLI can run: a source checkout, or a prepared packaged environment. */
export const toolEnvironmentReady = (state: Toolchain['tool_environment']) =>
  state === 'source' || state === 'ready'

type Translate = (key: string, params?: Record<string, string | number>) => string

// A phase not listed here (one the server added later) shows the generic text.
const PHASE_KEYS: Partial<Record<string, AuthoringMessageKey>> = {
  downloading_uv: 'toolDownloadingUv',
  checking_uv: 'toolCheckingUv',
  installing_python: 'toolInstallingPython',
  checking_python: 'toolCheckingPython',
  creating_environment: 'toolCreatingEnvironment',
}

// Codes with their own text; the others (internal_error, environment_failed,
// canceled, busy) show the server's detail instead.
const ERROR_KEYS: Partial<Record<string, AuthoringMessageKey>> = {
  unsupported_platform: 'toolErrorUnsupportedPlatform',
  download_failed: 'toolErrorDownloadFailed',
  checksum_mismatch: 'toolErrorChecksumMismatch',
  python_install_failed: 'toolErrorPythonInstallFailed',
  python_mismatch: 'toolErrorPythonMismatch',
  timeout: 'toolErrorTimeout',
  resources_unavailable: 'toolEnvironmentUnavailable',
}

/** The message key for a failure code, or null when the server's detail is the best text. */
export function toolErrorKey(code: string | null | undefined): AuthoringMessageKey | null {
  return code && Object.hasOwn(ERROR_KEYS, code) ? (ERROR_KEYS[code] ?? null) : null
}

const megabytes = (bytes: number) => (bytes / (1024 * 1024)).toFixed(1)

export function toolProgressText(progress: ToolEnvironmentProgress, t: Translate): string {
  const key = Object.hasOwn(PHASE_KEYS, progress.phase) ? PHASE_KEYS[progress.phase] : undefined
  const label = t(`authoring.${key ?? 'toolEnvironmentPreparing'}`)
  if (progress.phase !== 'downloading_uv' || !progress.downloaded_bytes) return label
  const size = progress.total_bytes
    ? t('authoring.toolBytes', {
        received: megabytes(progress.downloaded_bytes),
        total: megabytes(progress.total_bytes),
      })
    : t('authoring.toolBytesUnknown', { received: megabytes(progress.downloaded_bytes) })
  return `${label} (${size})`
}

const STATE_KEYS: Record<Toolchain['tool_environment'], AuthoringMessageKey> = {
  source: 'toolEnvironmentSource',
  ready: 'toolEnvironmentReady',
  preparing: 'toolEnvironmentPreparing',
  missing: 'toolEnvironmentMissing',
  failed: 'toolEnvironmentFailed',
  unavailable: 'toolEnvironmentUnavailable',
}

/** One line describing the environment: its state, the current phase or why it failed. */
export function toolEnvironmentText(toolchain: Toolchain, t: Translate): string {
  const state = toolchain.tool_environment
  if (state === 'preparing' && toolchain.tool_progress) {
    return toolProgressText(toolchain.tool_progress, t)
  }
  if (state === 'failed') {
    const reason = toolErrorKey(toolchain.tool_environment_error_code)
    return t('authoring.toolEnvironmentFailed', {
      error: reason ? t(`authoring.${reason}`) : (toolchain.tool_environment_error ?? ''),
    })
  }
  return t(`authoring.${STATE_KEYS[state]}`, { error: toolchain.tool_environment_error ?? '' })
}
