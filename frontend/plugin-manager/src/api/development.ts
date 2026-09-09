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
export interface DevelopmentState { enabled: boolean; registrations: DevelopmentRegistration[] }
const config = { headers: { 'X-Neko-Development': '1' }, timeout: PLUGIN_LIFECYCLE_TIMEOUT }
export const getDevelopment = (): Promise<DevelopmentState> => get('/plugins/development', config)
export const setDevelopmentEnabled = (enabled: boolean): Promise<DevelopmentState> =>
  // Disabling stops every development plugin sequentially, like reload-all.
  put('/plugins/development/settings', { enabled }, { ...config, timeout: enabled ? config.timeout : 0 })
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
