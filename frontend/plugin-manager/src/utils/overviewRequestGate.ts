import { createLatestRequestGate } from './latestRequest'

export type OverviewResource = 'status' | 'packs'

export interface OverviewRequestTicket {
  epoch: number
  requestId: number
}

export function createOverviewRequestGate() {
  let epoch = 0
  const resources = {
    status: createLatestRequestGate(),
    packs: createLatestRequestGate(),
  }

  return {
    invalidate(): number {
      epoch += 1
      resources.status.invalidate()
      resources.packs.invalidate()
      return epoch
    },
    begin(resource: OverviewResource, expectedEpoch = epoch): OverviewRequestTicket | null {
      if (expectedEpoch !== epoch) return null
      return { epoch, requestId: resources[resource].begin() }
    },
    isCurrent(resource: OverviewResource, ticket: OverviewRequestTicket): boolean {
      return ticket.epoch === epoch && resources[resource].isLatest(ticket.requestId)
    },
  }
}
