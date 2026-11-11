/** Shared capability/identity helpers mirroring server consent policy. */

export const ANALYZING_STATUSES = new Set(['queued', 'running', 'unavailable'])
export const INCOMPLETE_STATUSES = new Set(['partial', 'failed'])

export function capabilityMessageKey(capability: string, field: 'title' | 'description') {
  return `pluginConsent.capabilities.${capability.replace(/\./g, '_')}.${field}`
}
