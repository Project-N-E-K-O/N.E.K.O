import { describe, expect, it } from 'vitest'
import type { ToolEnvironmentProgress } from '@/api/pluginProjects'
import en from '@/i18n/authoring/en-US'
import { toolEnvironmentReady, toolErrorKey, toolProgressText } from './toolEnvironment'

const t = (key: string, params: Record<string, string | number> = {}) => {
  const text = (en as Record<string, string>)[key.replace(/^authoring\./, '')]
  return text.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name]))
}
const progress = (phase: string, downloaded: number | null = null, total: number | null = null) =>
  ({ phase, downloaded_bytes: downloaded, total_bytes: total }) as ToolEnvironmentProgress

describe('tool environment text', () => {
  it('names each known phase, with the uv download size', () => {
    expect(toolProgressText(progress('checking_python'), t)).toBe(en.toolCheckingPython)
    expect(toolProgressText(progress('creating_environment'), t)).toBe(en.toolCreatingEnvironment)
    expect(toolProgressText(progress('downloading_uv', 1024 * 1024, 4 * 1024 * 1024), t)).toBe(
      `${en.toolDownloadingUv} (1.0 / 4.0 MB)`
    )
  })

  it.each(['waiting', 'something_new', 'constructor'])('shows the generic text for the phase %s', (phase) => {
    expect(toolProgressText(progress(phase), t)).toBe(en.toolEnvironmentPreparing)
  })

  it('localizes known failure codes only', () => {
    expect(toolErrorKey('timeout')).toBe('toolErrorTimeout')
    expect(toolErrorKey('busy')).toBeNull()
    expect(toolErrorKey('internal_error')).toBeNull()
    expect(toolErrorKey(null)).toBeNull()
  })

  it('treats a source checkout and a prepared environment as ready', () => {
    expect(['source', 'ready', 'preparing', 'missing', 'failed', 'unavailable'].filter(
      (state) => toolEnvironmentReady(state as never)
    )).toEqual(['source', 'ready'])
  })
})
