import { describe, expect, it } from 'vitest'
import { collectPluginDeleteWarnings } from './pluginDeleteResult'

const t = (key: string, params?: Record<string, unknown>) =>
  `${key}:${JSON.stringify(params ?? {})}`

describe('collectPluginDeleteWarnings', () => {
  it('returns nothing for a clean delete', () => {
    expect(collectPluginDeleteWarnings({
      restored_builtin_restart_error: null,
      permission_warning: null,
    }, 'Demo', t)).toEqual([])
    expect(collectPluginDeleteWarnings({ restored_builtin_restart_error: null }, 'Demo', t)).toEqual([])
  })

  it('reports grants that could not be cleared', () => {
    expect(collectPluginDeleteWarnings({
      restored_builtin_restart_error: null,
      permission_warning: { code: 'PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION', message: 'x' },
    }, 'Demo', t)).toEqual([
      'messages.pluginDeletedPermissionsNotCleared:{"plugin":"Demo"}',
    ])
  })

  it('keeps both warnings', () => {
    expect(collectPluginDeleteWarnings({
      restored_builtin_restart_error: { code: 'C', message: 'boom', error_type: 'E' },
      permission_warning: { code: 'PLUGIN_PERMISSIONS_CORRUPT', message: 'x' },
    }, 'Demo', t)).toEqual([
      'messages.pluginDeletedBuiltinRestartFailed:{"plugin":"Demo","error":"boom"}',
      'messages.pluginDeletedPermissionsNotCleared:{"plugin":"Demo"}',
    ])
  })
})
