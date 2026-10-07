import { del, get, post, put } from './index'
import request from '@/utils/request'
import { PLUGIN_LIFECYCLE_TIMEOUT } from '@/utils/constants'

export interface DevelopmentRef { registration_id: string; revision: number }
export interface DevelopmentRegistration extends DevelopmentRef {
  plugin_id: string
  source_dir: string
  name?: string
  version?: string
  entry?: string
  error?: string | null
  runtime_alive?: boolean | null
}
export interface DevelopmentState { enabled: boolean; registrations: DevelopmentRegistration[]; disable_timeout_ms?: number }
const config = { headers: { 'X-Neko-Development': '1' }, timeout: PLUGIN_LIFECYCLE_TIMEOUT }
export const downloadDevelopmentPackage = async (packagePath: string): Promise<void> => {
  const blob = await get<Blob>('/plugins/development/download', {
    ...config, params: { package: packagePath }, responseType: 'blob', timeout: 300_000,
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = packagePath.split(/[\\/]/).pop() || 'development.neko-plugin'
  document.body.appendChild(link)
  try {
    link.click()
  } finally {
    link.remove()
    // Let the browser/Electron consume the download before releasing the blob.
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
export const getDevelopment = (): Promise<DevelopmentState> => get('/plugins/development', config)
export const setDevelopmentEnabled = async (enabled: boolean): Promise<DevelopmentState> => {
  if (enabled) return put('/plugins/development/settings', { enabled }, config)
  // Refresh the count/configuration estimate rather than using the page's stale
  // snapshot. A lost response must eventually release the UI; never retry this
  // mutation automatically, since the server may still be stopping plugins.
  const state = await getDevelopment()
  const estimate = state.disable_timeout_ms
  const fallback = Math.max(300_000, (state.registrations.length + 1) * PLUGIN_LIFECYCLE_TIMEOUT)
  const timeout = typeof estimate === 'number' && Number.isFinite(estimate) && estimate > 0
    ? Math.min(2_147_483_647, Math.max(300_000, estimate))
    : Math.min(2_147_483_647, fallback)
  return put('/plugins/development/settings', { enabled }, { ...config, timeout })
}
export const registerDevelopment = (source_dir: string, preview = false, ref?: DevelopmentRef): Promise<DevelopmentRegistration> =>
  post('/plugins/development/registrations', { source_dir, preview, ...(ref ? { registration_id: ref.registration_id, revision: ref.revision } : {}) }, config)
export const rebindDevelopment = (ref: DevelopmentRef, source_dir: string): Promise<DevelopmentRegistration> =>
  request.patch(`/plugins/development/registrations/${encodeURIComponent(ref.registration_id)}`, { source_dir, revision: ref.revision }, config)
export const removeDevelopment = (ref: DevelopmentRef): Promise<unknown> =>
  del(`/plugins/development/registrations/${encodeURIComponent(ref.registration_id)}`, { ...config, params: { revision: ref.revision } })
export const runDevelopmentAction = (record: DevelopmentRegistration, action: 'start' | 'stop' | 'reload'): Promise<{ success: boolean; message?: string }> =>
  post(`/plugin/${encodeURIComponent(record.plugin_id)}/${action}`, undefined, {
    ...config, params: { registration_id: record.registration_id, revision: record.revision },
  })
