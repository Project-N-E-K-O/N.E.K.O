type Translate = (key: string, params?: Record<string, unknown>) => string

export type PluginInstallOutcome =
  | { level: 'success'; warnings: [] }
  | { level: 'warning'; warnings: string[] }
  | { level: 'error'; message: string; warnings: string[] }

interface PluginInstallResultLike {
  rollback_status?: string | null
  install_source_warning?: string | null
}

export function collectPluginInstallWarnings(result: PluginInstallResultLike | null | undefined): string[] {
  const raw = result?.install_source_warning
  if (typeof raw !== 'string') return []
  return raw
    .split('; ')
    .map((item) => item.trim())
    .filter(Boolean)
}

/**
 * A 2xx install response is not automatically clean: the backend reports a
 * best-effort install-source bookkeeping failure via ``install_source_warning``
 * and the replacement transaction reports ``rollback_status``.
 */
export function resolvePluginInstallOutcome(
  result: PluginInstallResultLike | null | undefined,
  t: Translate,
): PluginInstallOutcome {
  const warnings = collectPluginInstallWarnings(result)
  const rollbackStatus = result?.rollback_status
  if (rollbackStatus === 'completed') {
    return { level: 'error', message: t('package.install.rollbackCompleted'), warnings }
  }
  if (rollbackStatus === 'incomplete') {
    return { level: 'error', message: t('package.install.rollbackIncomplete'), warnings }
  }
  if (warnings.length > 0) {
    return { level: 'warning', warnings }
  }
  return { level: 'success', warnings: [] }
}

/**
 * Shows the success message for clean results, otherwise a warning/error that
 * carries the backend reasons. Returns the outcome so callers can decide
 * whether to continue with their success-only follow-ups.
 */
export function notifyPluginInstallOutcome(
  result: PluginInstallResultLike | null | undefined,
  t: Translate,
  notify: {
    success: (message: string) => void
    warning: (message: string) => void
    error: (message: string) => void
  },
  context: { plugin: string; successMessage: string },
): PluginInstallOutcome {
  const outcome = resolvePluginInstallOutcome(result, t)
  if (outcome.level === 'success') {
    notify.success(context.successMessage)
  } else if (outcome.level === 'warning') {
    notify.warning(t('package.install.completedWithWarnings', {
      plugin: context.plugin,
      reasons: outcome.warnings.join('; '),
    }))
  } else {
    notify.error(outcome.message)
  }
  return outcome
}
