import { describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/request', () => ({ readErrorCode: vi.fn() }))

import { capabilityMessageKey } from './pluginCapabilities'
import { pluginConsentMessages } from '@/i18n/plugin-consent'

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
