import request from '@/utils/request'
import type { ErrorDisplayRequestConfig } from '@/utils/request'
import type { ModelBindings, ModelSlot, ModelSlotInput, ModelSlotTestResult, ModelUsageResult } from '@/types/model-api'

// These screens render errors inline, including authentication and missing-slot errors.
const config: ErrorDisplayRequestConfig = { suppressErrorMessage: true }
const slotsUrl = '/api/model-config/slots'
const slotUrl = (id: string) => `${slotsUrl}/${encodeURIComponent(id)}`
const bindingsUrl = (id: string) => `/api/model-config/plugins/${encodeURIComponent(id)}/bindings`

// Uncertain requests remain tracked across page navigation. A confirmation
// advances the server version if necessary, preventing a late write after GET.
type PendingBinding = { pluginId: string; usageId: string; version: number }
const pendingStorageKey = 'neko.model-binding-confirmations'
const uncertainBindings = new Map<string, PendingBinding>()
try {
  const saved: unknown = JSON.parse(sessionStorage.getItem(pendingStorageKey) || '[]')
  if (Array.isArray(saved)) for (const item of saved) {
    if (item && typeof item.pluginId === 'string' && typeof item.usageId === 'string' && Number.isSafeInteger(item.version) && item.version >= 0) {
      uncertainBindings.set(JSON.stringify([item.pluginId, item.usageId]), item)
    }
  }
} catch { /* Storage may be unavailable; keep tracking within this application. */ }
function persistPendingBindings(): void {
  try { sessionStorage.setItem(pendingStorageKey, JSON.stringify([...uncertainBindings.values()])) }
  catch { /* The in-memory confirmation state remains available. */ }
}
export class BindingResultUnknown extends Error {
  constructor() { super('MODEL_BINDING_RESULT_UNKNOWN') }
}
type ConfirmedBinding = { version: number; slot_id: string | null }
export async function confirmPendingBindings(pluginId?: string): Promise<Map<string, ConfirmedBinding>> {
  const results = new Map<string, ConfirmedBinding>()
  for (const [key] of uncertainBindings) {
    // A newer failure can arrive while this confirmation is in flight.
    // Keep confirming until the response fences the currently tracked version.
    let pending = uncertainBindings.get(key)
    while (pending && (pluginId === undefined || pending.pluginId === pluginId)) {
      let result: ConfirmedBinding
      try {
        result = await request.post(`${bindingsUrl(pending.pluginId)}/${encodeURIComponent(pending.usageId)}/confirm`,
          { expected_version: pending.version }, config)
      } catch { throw new BindingResultUnknown() }
      results.set(key, result)
      const current = uncertainBindings.get(key)
      if (current && result.version > current.version) {
        uncertainBindings.delete(key)
        persistPendingBindings()
      }
      pending = uncertainBindings.get(key)
    }
  }
  return results
}
async function mutateBinding<T>(pluginId: string, usageId: string, version: number, send: () => Promise<T>, recover: (result: ConfirmedBinding) => T | undefined): Promise<T> {
  await confirmPendingBindings(pluginId)
  try { return await send() }
  catch (error) {
    const status = (error as { response?: { status?: number } })?.response?.status
    if (status === undefined || status >= 500) {
      const key = JSON.stringify([pluginId, usageId])
      const pending = uncertainBindings.get(key)
      if (!pending || pending.version < version) {
        uncertainBindings.set(key, { pluginId, usageId, version })
        persistPendingBindings()
      }
      const confirmed = (await confirmPendingBindings(pluginId)).get(key)
      if (confirmed) {
        const recovered = recover(confirmed)
        if (recovered !== undefined) return recovered
      }
    }
    throw error
  }
}

export async function listModelSlots(): Promise<{ schema_version: 1; slots: ModelSlot[] }> {
  await confirmPendingBindings()
  return request.get(slotsUrl, config)
}
export function createModelSlot(payload: ModelSlotInput): Promise<ModelSlot> {
  return request.post(slotsUrl, payload, config)
}
export function updateModelSlot(id: string, payload: Partial<ModelSlotInput>): Promise<ModelSlot> {
  return request.patch(slotUrl(id), payload, config)
}
export function deleteModelSlot(id: string): Promise<{ success: boolean }> {
  return request.delete(slotUrl(id), config)
}
export function testModelSlot(id: string): Promise<ModelSlotTestResult> {
  return request.post(`${slotUrl(id)}/test`, undefined, { ...config, timeout: 35000 })
}
export async function getModelBindings(pluginId: string): Promise<ModelBindings> {
  await confirmPendingBindings(pluginId)
  return request.get(bindingsUrl(pluginId), config)
}
export function setModelBinding(pluginId: string, usageId: string, slotId: string, expectedVersion: number): Promise<{ plugin_id: string; usage_id: string; slot_id: string; version: number }> {
  return mutateBinding(pluginId, usageId, expectedVersion, () => request.put(`${bindingsUrl(pluginId)}/${encodeURIComponent(usageId)}`, { slot_id: slotId, expected_version: expectedVersion }, config),
    result => result.slot_id === slotId ? { plugin_id: pluginId, usage_id: usageId, slot_id: slotId, version: result.version } : undefined)
}
export function deleteModelBinding(pluginId: string, usageId: string, expectedVersion: number): Promise<{ success: boolean; version: number }> {
  return mutateBinding(pluginId, usageId, expectedVersion, () => request.delete(`${bindingsUrl(pluginId)}/${encodeURIComponent(usageId)}`, { ...config, params: { expected_version: expectedVersion } }),
    result => result.slot_id === null ? { success: true, version: result.version } : undefined)
}
export function getModelUsage(filters: { plugin_id?: string; slot_id?: string; limit?: number } = {}): Promise<ModelUsageResult> {
  return request.get('/api/model-config/usage', { ...config, params: filters })
}
