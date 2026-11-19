import type { MarketPluginComment, MarketPluginComments } from './market'
import { fetchBridge, readErrorCode } from './marketBridge'

const SESSION_HEADER = 'X-Market-Feedback-Session'
const SESSION_CHANGED_HEADER = 'X-Market-Feedback-Session-Changed'
const SENT_AFTER_ACCOUNT_CHANGE = 'market_feedback_sent_session_changed'

export interface MarketConversationRead<T> {
  data: T
  session: string | null
}

export interface MarketMessageDraft {
  body: string
  reply_to_id: number | null
  client_message_id: string
}

export interface MarketIssueSummary {
  id: number
  title: string | null
  state: 'open' | 'closed'
  is_hidden: boolean
  capabilities: { can_reply: boolean; can_close: boolean; can_reopen: boolean }
}

export interface MarketIssueDetail extends MarketIssueSummary {
  messages: MarketPluginComment[]
  next_message_cursor: number | null
}

interface MarketThreadPage<T> {
  items: T[]
  next_cursor: number | null
}

export class MarketConversationError extends Error {
  constructor(
    readonly status: number,
    readonly code: string
  ) {
    super(code)
  }
}

/**
 * The Market accepted the write, but the desktop login switched accounts while
 * it was in flight. The caller must not offer a resend: the content is posted.
 */
export function isSentAfterAccountChange(error: unknown): boolean {
  return error instanceof MarketConversationError && error.code === SENT_AFTER_ACCOUNT_CHANGE
}

export function marketConversationSessionChanged(error: unknown): boolean {
  return (
    error instanceof MarketConversationError &&
    (error.status === 401 ||
      error.code === 'market_feedback_session_changed' ||
      error.code === SENT_AFTER_ACCOUNT_CHANGE ||
      error.code === 'market_auth_required' ||
      error.code === 'market_auth_pending')
  )
}

function pluginPath(pluginId: number | string): string {
  return `/market/conversations/plugins/${encodeURIComponent(String(pluginId))}`
}

async function readConversation<T>(
  path: string,
  signal: AbortSignal,
  session?: string | null
): Promise<MarketConversationRead<T>> {
  const headers = new Headers({
    Accept: 'application/json',
    'Accept-Language': document.documentElement.lang,
  })
  if (session) headers.set(SESSION_HEADER, session)
  const init: RequestInit = { headers, signal, cache: 'no-store' }
  let response = await fetchBridge(path, init, { authorizationHeader: true })
  if (!session && (!response || response.status === 401 || response.status === 403)) {
    response = await fetch(path, init)
  }
  const data = await conversationJson<T>(response)
  const current = response?.headers.get(SESSION_HEADER) ?? null
  if (session && current !== session) {
    throw new MarketConversationError(409, 'market_feedback_session_changed')
  }
  return { data, session: current }
}

async function conversationJson<T>(response: Response | null): Promise<T> {
  if (!response) throw new MarketConversationError(0, 'market_feedback_unavailable')
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new MarketConversationError(response.status, readErrorCode(body))
  }
  return response.json()
}

async function writeConversation<T>(
  path: string,
  session: string,
  draft?: MarketMessageDraft
): Promise<T> {
  const headers = new Headers({
    Accept: 'application/json',
    'Accept-Language': document.documentElement.lang,
    [SESSION_HEADER]: session,
  })
  if (draft) headers.set('Content-Type', 'application/json')
  const response = await fetchBridge(
    path,
    { method: 'POST', headers, body: draft ? JSON.stringify(draft) : undefined, cache: 'no-store' },
    { authorizationHeader: true, retryOnForbidden: false }
  )
  const data = await conversationJson<T>(response)
  if (response?.headers.get(SESSION_CHANGED_HEADER)) {
    throw new MarketConversationError(response.status, SENT_AFTER_ACCOUNT_CHANGE)
  }
  return data
}

export function readMarketComments(
  pluginId: number | string,
  signal: AbortSignal,
  afterId?: number,
  session?: string | null
): Promise<MarketConversationRead<MarketPluginComments>> {
  return readConversation(
    `${pluginPath(pluginId)}/comments${afterId ? `?after_id=${afterId}` : ''}`,
    signal,
    session
  )
}

export function sendMarketComment(
  pluginId: number | string,
  session: string,
  draft: MarketMessageDraft
) {
  return writeConversation<MarketPluginComment>(
    `${pluginPath(pluginId)}/comments/messages`,
    session,
    draft
  )
}

export function readMarketIssues(
  pluginId: number | string,
  signal: AbortSignal,
  beforeId?: number,
  session?: string | null
) {
  return readConversation<MarketThreadPage<MarketIssueSummary>>(
    `${pluginPath(pluginId)}/issues${beforeId ? `?before_id=${beforeId}` : ''}`,
    signal,
    session
  )
}

export function readMarketIssue(pluginId: number | string, issueId: number, signal: AbortSignal) {
  return readConversation<MarketIssueDetail>(`${pluginPath(pluginId)}/issues/${issueId}`, signal)
}

export function readMarketIssueMessages(
  pluginId: number | string,
  issueId: number,
  afterId: number,
  signal: AbortSignal,
  session?: string | null
) {
  return readConversation<MarketThreadPage<MarketPluginComment>>(
    `${pluginPath(pluginId)}/issues/${issueId}/messages?after_id=${afterId}`,
    signal,
    session
  )
}

export function sendMarketIssueReply(
  pluginId: number | string,
  issueId: number,
  session: string,
  draft: MarketMessageDraft
) {
  return writeConversation<MarketPluginComment>(
    `${pluginPath(pluginId)}/issues/${issueId}/messages`,
    session,
    draft
  )
}

export function changeMarketIssueState(
  pluginId: number | string,
  issueId: number,
  session: string,
  action: 'close' | 'reopen'
) {
  return writeConversation<MarketIssueDetail>(
    `${pluginPath(pluginId)}/issues/${issueId}/${action}`,
    session
  )
}
