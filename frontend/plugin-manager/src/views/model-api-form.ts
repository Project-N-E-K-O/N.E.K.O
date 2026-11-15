import type { HostApiSource, ModelCapability, ModelProtocol, ModelSlot, ModelSlotInput, ModelSlotSource } from '@/types/model-api'

export interface ModelSlotForm {
  name: string
  source: ModelSlotSource
  host_api: HostApiSource
  protocol: ModelProtocol
  base_url: string
  model: string
  key_edited: boolean
  initial_api_key: string
  api_key: string
  capabilities: ModelCapability[]
  temperature: number | undefined
  max_output_tokens: number | undefined
  timeout_seconds: number
  fallback_slot_id: string
}

export function modelSlotForm(slot?: ModelSlot): ModelSlotForm {
  return {
    name: slot?.name ?? '',
    source: slot?.source ?? 'manual',
    host_api: slot?.host_api ?? 'assist',
    protocol: slot?.protocol ?? 'openai_chat',
    base_url: slot?.base_url ?? '',
    model: slot?.model ?? '',
    key_edited: false,
    initial_api_key: slot?.api_key ? slot.api_key_preview || '******' : '',
    api_key: slot?.api_key ? slot.api_key_preview || '******' : '',
    capabilities: [...(slot?.capabilities ?? ['text'])],
    temperature: slot?.defaults.temperature ?? undefined,
    max_output_tokens: slot?.defaults.max_output_tokens ?? undefined,
    timeout_seconds: slot?.timeout_seconds ?? 60,
    fallback_slot_id: slot?.fallback_slot_id ?? '',
  }
}

function endpoint(value: string): string {
  try { return new URL(value.trim()).href.replace(/\/+$/, '') }
  catch { return value.trim().replace(/\/+$/, '') }
}

export function needsModelKeyUpdate(form: ModelSlotForm, slot: ModelSlot | null): boolean {
  return Boolean(slot?.source !== 'host_follow' && form.source === 'manual' && slot?.api_key && !form.key_edited
    && (form.protocol !== slot.protocol || endpoint(form.base_url) !== endpoint(slot.base_url)))
}

export function modelSlotPayload(form: ModelSlotForm): ModelSlotInput {
  const payload: ModelSlotInput = {
    name: form.name.trim(), source: form.source,
    host_api: form.source === 'host_follow' ? form.host_api : null,
    protocol: form.protocol, base_url: form.base_url.trim(), model: form.model.trim(),
    capabilities: [...new Set<ModelCapability>(['text', ...form.capabilities])],
    defaults: { temperature: form.temperature ?? null, max_output_tokens: form.max_output_tokens ?? null },
    timeout_seconds: form.timeout_seconds,
    fallback_slot_id: form.fallback_slot_id || null,
  }
  if (form.source === 'host_follow') {
    // Endpoint, model and credentials are owned by the main app configuration.
    payload.protocol = 'openai_chat'
    payload.base_url = ''
    payload.model = ''
  } else if (form.key_edited) {
    payload.api_key = form.api_key.trim()
  }
  return payload
}

export function maskedKeyCopy(value: string): string {
  if (!value) return ''
  const characters = Array.from(value)
  const preview = characters.length > 10 ? `${characters.slice(0, 6).join('')}......${characters.slice(-4).join('')}` : '******'
  return preview === value ? (value === '******' ? '••••••' : '******') : preview
}