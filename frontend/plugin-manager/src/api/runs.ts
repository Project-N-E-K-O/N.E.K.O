import { post } from './index'

export function createRun(payload: {
  plugin_id: string
  entry_id: string
  args: Record<string, any>
  task_id?: string
}): Promise<{ run_id: string; status: string; run_token?: string; expires_at?: number }> {
  return post('/runs', payload)
}
