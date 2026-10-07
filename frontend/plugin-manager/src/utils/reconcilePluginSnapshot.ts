import type { PluginMeta } from '@/types/api'

// API payloads are JSON values. Compare current values, not a stale stored hash:
// callers can legitimately update runtime fields in the existing Pinia objects.
function equalJson(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const left = a as Record<string, unknown>, right = b as Record<string, unknown>
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length
    && keys.every(key => Object.prototype.hasOwnProperty.call(right, key) && equalJson(left[key], right[key]))
}

export function reconcilePluginSnapshot(previous: PluginMeta[], incoming: PluginMeta[]): PluginMeta[] {
  const byId = new Map(previous.map(plugin => [plugin.id, plugin]))
  const next = incoming.map(plugin => {
    const old = byId.get(plugin.id)
    return old && equalJson(old, plugin) ? old : plugin
  })
  return next.length === previous.length && next.every((plugin, i) => plugin === previous[i]) ? previous : next
}
