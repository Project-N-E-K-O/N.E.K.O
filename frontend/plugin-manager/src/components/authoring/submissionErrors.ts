import { MarketSubmissionError } from '@/api/marketSubmissions'
import type { AuthoringMessageKey } from '@/i18n/authoring'

type Translate = (key: string, params?: Record<string, unknown>) => string

/** Error codes from the bridge or the Market that have their own message. */
const ERROR_MESSAGES: Record<string, AuthoringMessageKey> = {
  market_auth_required: 'marketAuthRequired',
  market_auth_pending: 'marketAuthPending',
  market_email_unverified: 'marketEmailUnverified',
  market_auth_rejected: 'marketAuthRejected',
  market_submission_unavailable: 'marketSubmissionUnavailable',
  market_submission_redirect_rejected: 'marketSubmissionRedirectRejected',
  market_submission_not_configured: 'marketSubmissionNotConfigured',
  market_submission_id_invalid: 'marketSubmissionIdInvalid',
  market_bridge_unreachable: 'marketBridgeUnreachable',
  submission_content_too_large: 'submissionContentTooLarge',
}

/** The message for a failed submission call, after the action named by `fallback`. */
export function describeSubmissionError(
  t: Translate,
  error: unknown,
  fallback: AuthoringMessageKey
): string {
  if (!(error instanceof MarketSubmissionError)) return t(`authoring.${fallback}`)
  if (error.code === 'plugin_slug_conflict') {
    return t('authoring.submissionSlugConflict', { slug: String(error.detail.plugin_slug ?? '') })
  }
  if (error.code === 'plugin_identity_mismatch') {
    return t('authoring.submissionIdentityMismatch', {
      manifest: String(error.detail.manifest_plugin_id ?? ''),
      repository: String(error.detail.repository_plugin_id ?? ''),
    })
  }
  if (Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, error.code)) {
    return t(`authoring.${ERROR_MESSAGES[error.code]}`)
  }
  // The Market localizes its business errors; show them after the action that failed.
  const reason = error.serverMessage
  return reason ? `${t(`authoring.${fallback}`)} ${reason}` : t(`authoring.${fallback}`)
}

/**
 * A 409 the Market sends without a code: the submission moved on (another
 * revision, a review decision) since it was read. Coded 409s are handled by code.
 */
export function isStaleSubmission(error: unknown): boolean {
  return error instanceof MarketSubmissionError && error.status === 409 && error.code === 'market_submission_failed'
}
