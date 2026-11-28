import { describe, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => ({ readErrorCode: vi.fn() }))
vi.mock('@/utils/request', () => request)

import { AxiosError } from 'axios'
import { capabilityMessageKey, consentSaveErrorKey, isPartlyUnconfirmed } from './pluginCapabilities'
import { SUPPORTED_LOCALES } from '@/i18n'
import { allPluginConsentMessages as pluginConsentMessages } from '@/i18n/plugin-consent/testing'

// Mirrors PERMISSION_IDS in plugin/sdk/shared/permissions/catalog.py; a Python
// test checks this list against the catalog.
const PERMISSION_IDS = [
  'plugin_sdk.main_agent.memory:read',
  'plugin_sdk.main_agent.conversations:read',
  'plugin_sdk.models:invoke',
  'system.network:connect',
  'system.network:listen',
  'system.fs:read',
  'system.fs:write',
  'system.process:spawn',
  'system.env:read',
  'plugin_sdk.main_agent.messages:push',
]
const LEGACY_NAMES = ['memory.read', 'conversations.read', 'messages.push']

function lookup(messages: unknown, key: string): unknown {
  return key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], messages)
}

describe('capabilityMessageKey', () => {
  it('derives keys from earlier names and from permission ids', () => {
    expect(capabilityMessageKey('memory.read', 'title')).toBe('pluginConsent.capabilities.memory_read.title')
    expect(capabilityMessageKey('plugin_sdk.main_agent.memory:read', 'description')).toBe(
      'pluginConsent.capabilities.plugin_sdk_main_agent_memory_read.description',
    )
  })

  it.each(Object.entries(pluginConsentMessages))('%s labels every permission id and earlier name', (_, messages) => {
    for (const capability of [...PERMISSION_IDS, ...LEGACY_NAMES]) {
      for (const field of ['title', 'description'] as const) {
        const key = capabilityMessageKey(capability, field).replace(/^pluginConsent\./, '')
        const value = lookup(messages, key)
        expect(typeof value, `${capability} ${field}`).toBe('string')
        expect((value as string).length).toBeGreaterThan(0)
      }
    }
  })

  it('reuses the earlier label for ids that had one', () => {
    const caps = pluginConsentMessages['en-US'].capabilities
    expect(caps.plugin_sdk_main_agent_memory_read).toEqual(caps.memory_read)
    expect(caps.plugin_sdk_main_agent_messages_push).toEqual(caps.messages_push)
  })
})

describe('list marks', () => {
  it.each([
    [undefined, false, false],
    [{ state: 'clear', decision: 'approved', upgraded: false }, false, false],
    [{ state: 'clear', decision: null, upgraded: false, blind_spots: 0 }, false, false],
    [{ state: 'analyzing', decision: null, upgraded: false }, false, false],
    [{ state: 'confirm', decision: null, upgraded: false }, true, false],
    [{ state: 'confirm', decision: 'approved', upgraded: true }, true, false],
    [{ state: 'clear', decision: 'approved', upgraded: false, blind_spots: 2 }, false, true],
    [{ state: 'confirm', decision: null, upgraded: false, blind_spots: 1 }, true, true],
    // A failed analysis reports no blind spots but checked none of the code.
    [{ state: 'clear', decision: 'approved', upgraded: false, blind_spots: 0, analysis_incomplete: true }, false, true],
    [{ state: 'confirm', decision: null, upgraded: false, blind_spots: 0, analysis_incomplete: true }, true, true],
  ] as const)('%o -> partly unconfirmed %s', (consent, _pending, partial) => {
    expect(isPartlyUnconfirmed(consent)).toBe(partial)
  })
})

describe('consentSaveErrorKey', () => {
  it('names a permission-store failure and keeps the generic message otherwise', () => {
    const error = new AxiosError('failed')
    request.readErrorCode.mockReturnValueOnce('PLUGIN_PERMISSIONS_CORRUPT')
    expect(consentSaveErrorKey(error)).toBe('pluginConsent.errors.corrupt')
    request.readErrorCode.mockReturnValueOnce('PLUGIN_CONSENT_WRITE_FAILED')
    expect(consentSaveErrorKey(error)).toBe('pluginConsent.saveFailed')
    expect(consentSaveErrorKey(new Error('offline'))).toBe('pluginConsent.saveFailed')
  })
})

describe('permission labels', () => {
  it('checks every supported language', () => {
    expect(Object.keys(pluginConsentMessages).sort()).toEqual([...SUPPORTED_LOCALES].sort())
  })

  it.each(Object.entries(pluginConsentMessages))('%s labels system.network:listen', (_, messages) => {
    for (const field of ['title', 'description'] as const) {
      const key = capabilityMessageKey('system.network:listen', field).replace(/^pluginConsent\./, '')
      expect(lookup(messages, key)).toEqual(expect.any(String))
    }
  })
})
