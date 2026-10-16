export type RunsRequestErrorCode =
  | 'cancelled'
  | 'connection_failed'
  | 'connection_timeout'
  | 'connection_closed'
  | 'request_timeout'
  | 'request_failed'
  | 'outcome_unknown'

export class RunsRequestError extends Error {
  constructor(readonly code: RunsRequestErrorCode) {
    super(code)
    this.name = 'RunsRequestError'
  }
}

export function isRunsRequestCancelled(error: unknown): boolean {
  return error instanceof RunsRequestError && error.code === 'cancelled'
}

export function runsRequestErrorKey(error: unknown): string {
  if (!(error instanceof RunsRequestError)) return 'runs.requestFailed'
  const keys: Record<RunsRequestErrorCode, string> = {
    cancelled: 'runs.requestFailed',
    connection_failed: 'runs.connectionFailed',
    connection_timeout: 'runs.connectionTimeout',
    connection_closed: 'runs.connectionClosed',
    request_timeout: 'runs.requestTimeout',
    request_failed: 'runs.requestFailed',
    outcome_unknown: 'runs.operationOutcomeUnknown',
  }
  return keys[error.code]
}
