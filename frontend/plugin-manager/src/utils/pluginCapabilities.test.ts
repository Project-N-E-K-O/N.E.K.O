import { describe, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => ({ readErrorCode: vi.fn() }))
vi.mock('@/utils/request', () => request)

import { AxiosError } from 'axios'
import {
  analysisFoundNothing,
  blindSpots,
  capabilityMessageKey,
  consentSaveErrorKey,
  needsPermissionReview,
  usedPermissions,
} from './pluginCapabilities'
import type { CapabilityAnalysis } from '@/api/pluginConsent'
import { pluginConsentMessages } from '@/i18n/plugin-consent'

// Mirrors PERMISSION_IDS in plugin/sdk/shared/permissions/catalog.py; a Python
// test checks this list against the catalog.
const PERMISSION_IDS = [
  'plugin_sdk.main_agent.memory:read',
  'plugin_sdk.main_agent.conversations:read',
  'plugin_sdk.models:invoke',
  'system.network:connect',
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

describe('permission findings of an analysis', () => {
  function analysis(extras: Partial<CapabilityAnalysis> = {}): CapabilityAnalysis {
    return {
      status: 'completed',
      capabilities: [],
      evidence: [],
      warnings: [],
      permissions: {},
      blind_spots: [],
      ...extras,
    }
  }

  it('reports nothing found only without capabilities, permissions or blind spots', () => {
    expect(analysisFoundNothing(analysis())).toBe(true)
  })

  it('lists declared and detected permissions, so nothing is reported as found', () => {
    const report = analysis({
      permissions: {
        'system.network:connect': {
          declared: null,
          findings: [
            { file: 'main.py', line: 3 },
            { file: 'main.py', line: 3 },
          ],
        },
        'plugin_sdk.models:invoke': { declared: 'required', findings: [] },
      },
    })
    expect(usedPermissions(report)).toEqual([
      { id: 'plugin_sdk.models:invoke', declared: true, locations: [] },
      { id: 'system.network:connect', declared: false, locations: ['main.py:3'] },
    ])
    expect(analysisFoundNothing(report)).toBe(false)
  })

  it('counts blind spots, so nothing is reported as found', () => {
    const report = analysis({
      blind_spots: [
        { type: 'dynamic_import', file: 'main.py', line: 8 },
        { type: 'not_analyzed', file: 'lib/big.py', line: null },
      ],
    })
    expect(blindSpots(report)).toHaveLength(2)
    expect(usedPermissions(report)).toEqual([])
    expect(analysisFoundNothing(report)).toBe(false)
  })

  it('leaves permissions already listed under their earlier capability name to that list', () => {
    const report = analysis({
      capabilities: ['memory.read'],
      permissions: {
        'plugin_sdk.main_agent.memory:read': {
          declared: null,
          findings: [{ file: 'main.py', line: 1 }],
        },
      },
    })
    expect(usedPermissions(report)).toEqual([])
    expect(analysisFoundNothing(report)).toBe(false)
  })

  it.each([
    ['partial', {}],
    ['failed', {}],
    ['completed', { permissions: undefined }],
  ] as const)('does not report nothing found for a %s analysis %o', (status, extras) => {
    expect(analysisFoundNothing(analysis({ status, ...extras }))).toBe(false)
  })
})

describe('needsPermissionReview', () => {
  it.each([
    [undefined, false],
    [{ state: 'clear', decision: 'approved', upgraded: false }, false],
    [{ state: 'clear', decision: null, upgraded: false, blind_spots: 0 }, false],
    [{ state: 'analyzing', decision: null, upgraded: false }, false],
    [{ state: 'confirm', decision: null, upgraded: false }, true],
    [{ state: 'confirm', decision: 'approved', upgraded: true }, true],
    [{ state: 'clear', decision: 'approved', upgraded: false, blind_spots: 2 }, true],
  ] as const)('%o -> %s', (consent, expected) => {
    expect(needsPermissionReview(consent)).toBe(expected)
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
  it.each(Object.entries(pluginConsentMessages))('%s labels system.network:listen', (_, messages) => {
    for (const field of ['title', 'description'] as const) {
      const key = capabilityMessageKey('system.network:listen', field).replace(/^pluginConsent\./, '')
      expect(lookup(messages, key)).toEqual(expect.any(String))
    }
  })
})
