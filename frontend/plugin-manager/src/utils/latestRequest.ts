export interface LatestRequestGate {
  begin: () => number
  invalidate: () => void
  isLatest: (requestId: number) => boolean
}

export function createLatestRequestGate(): LatestRequestGate {
  let latestRequestId = 0
  return {
    begin: () => ++latestRequestId,
    invalidate: () => { latestRequestId += 1 },
    isLatest: (requestId) => requestId === latestRequestId,
  }
}
