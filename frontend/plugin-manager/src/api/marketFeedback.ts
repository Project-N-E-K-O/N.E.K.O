import { fetchBridge, readErrorCode } from './marketBridge'
import { getLocale } from '@/i18n'

const FEEDBACK = '/market/feedback'
const SESSION_HEADER = 'X-Market-Feedback-Session'
const bridgeOptions = { authorizationHeader: true }

export type FeedbackKind = 'comment' | 'issue'
export type FeedbackStatus = 'all' | 'unread' | 'awaiting_reply'

export interface FeedbackActor {
  id: number
  username: string
  display_name: string | null
  avatar_url: string | null
  community_profile_id?: string | null
}

export interface FeedbackItem {
  kind: FeedbackKind
  plugin_id: number
  plugin_name: string
  plugin_listed: boolean
  thread_id: number
  issue_title: string | null
  message_id: number
  body: string
  created_at: string
  author: FeedbackActor
  unread: boolean
  awaiting_reply: boolean
}

export interface FeedbackCounts {
  unread_total: number
  awaiting_reply_total: number
}

export interface FeedbackPage extends FeedbackCounts {
  items: FeedbackItem[]
  next_cursor: string | null
  by_plugin: Array<FeedbackCounts & { plugin_id: number }>
  session: string
  marketWebUrl: string
}

export class FeedbackRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string
  ) {
    super(code)
  }
}

async function feedbackRequest(
  path: string,
  init: RequestInit,
  session?: string
): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('Accept-Language', getLocale())
  if (session) headers.set(SESSION_HEADER, session)
  const response = await fetchBridge(path, { ...init, headers, cache: 'no-store' }, bridgeOptions)
  if (!response) throw new FeedbackRequestError(0, 'market_feedback_unavailable')
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new FeedbackRequestError(response.status, readErrorCode(body))
  }
  return response
}

export async function fetchMarketFeedback(
  filters: { kind: FeedbackKind | 'all'; status: FeedbackStatus; cursor?: string; limit?: number },
  signal: AbortSignal,
  session?: string
): Promise<FeedbackPage> {
  const query = new URLSearchParams({ kind: filters.kind, status: filters.status })
  if (filters.cursor) query.set('cursor', filters.cursor)
  if (filters.limit !== undefined) query.set('limit', String(filters.limit))
  const response = await feedbackRequest(`${FEEDBACK}?${query}`, { signal }, session)
  const activeSession = response.headers.get(SESSION_HEADER)
  if (!activeSession) throw new FeedbackRequestError(502, 'market_feedback_unavailable')
  return {
    ...(await response.json()),
    session: activeSession,
    marketWebUrl: response.headers.get('X-Market-Web-Url') || '',
  }
}

export async function markMarketFeedbackRead(target: FeedbackItem | 'all', session: string) {
  const payload =
    target === 'all'
      ? { all: true }
      : {
          thread_kind: target.kind,
          thread_id: target.thread_id,
          through_message_id: target.message_id,
        }
  await feedbackRequest(
    `${FEEDBACK}/mark-read`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    session
  )
}

export function marketFeedbackLink(base: string, item: FeedbackItem): string {
  if (!item.plugin_listed || !base) return ''
  try {
    const url = new URL(base)
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return ''
    const query = new URLSearchParams({ tab: item.kind === 'issue' ? 'issues' : 'reviews' })
    if (item.kind === 'issue') query.set('issue', String(item.thread_id))
    url.hash = `/plugin/${encodeURIComponent(String(item.plugin_id))}?${query}`
    return url.href
  } catch {
    return ''
  }
}
