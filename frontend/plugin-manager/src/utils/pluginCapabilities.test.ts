import { describe, expect, it, vi } from 'vitest'

const request = vi.hoisted(() => ({ readErrorCode: vi.fn() }))
vi.mock('@/utils/request', () => request)

import { AxiosError } from 'axios'
import {
  analysisFoundNothing,
  blindSpots,
  capabilityMessageKey,
  consentSaveErrorKey,
  isPartlyUnconfirmed,
  needsPermissionReview,
  usedPermissions,
  capabilityOrigin,
} from './pluginCapabilities'
import type { CapabilityAnalysis } from '@/api/pluginConsent'
import zhCnConsent from '@/i18n/plugin-consent/zh-CN'
import zhTwConsent from '@/i18n/plugin-consent/zh-TW'
import enUsConsent from '@/i18n/plugin-consent/en-US'
import jaConsent from '@/i18n/plugin-consent/ja'
import koConsent from '@/i18n/plugin-consent/ko'
import ruConsent from '@/i18n/plugin-consent/ru'
import esConsent from '@/i18n/plugin-consent/es'
import ptConsent from '@/i18n/plugin-consent/pt'

// One module per language (see i18n/localeBundles.ts); the tests check all eight.
const pluginConsentMessages = {
  'zh-CN': zhCnConsent,
  'zh-TW': zhTwConsent,
  'en-US': enUsConsent,
  ja: jaConsent,
  ko: koConsent,
  ru: ruConsent,
  es: esConsent,
  pt: ptConsent,
}

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

  it('keeps the origin and code locations of a permission listed under its earlier name', () => {
    const report = analysis({
      capabilities: ['memory.read', 'messages.push', 'conversations.read'],
      evidence: [
        { capability: 'memory.read', method: 'bus.memory.get', file: 'main.py', line: 1 },
        { capability: 'conversations.read', method: 'bus.conversations.get', file: 'main.py', line: 4 },
      ],
      permissions: {
        'plugin_sdk.main_agent.memory:read': {
          declared: 'require',
          findings: [{ file: 'main.py', line: 1 }],
        },
        // The evidence list was already full when this call was found.
        'plugin_sdk.main_agent.messages:push': {
          declared: null,
          findings: [{ file: 'main.py', line: 900 }],
        },
      },
    })
    expect(capabilityOrigin(report, 'memory.read')).toEqual({
      declared: true,
      locations: ['main.py:1'],
    })
    expect(capabilityOrigin(report, 'messages.push')).toEqual({
      declared: false,
      locations: ['main.py:900'],
    })
    // No permission entry: no origin to show, evidence still listed.
    expect(capabilityOrigin(report, 'conversations.read')).toEqual({
      declared: null,
      locations: ['main.py:4'],
    })
  })

  it.each([
    ['partial', {}],
    ['failed', {}],
    ['completed', { permissions: undefined }],
  ] as const)('does not report nothing found for a %s analysis %o', (status, extras) => {
    expect(analysisFoundNothing(analysis({ status, ...extras }))).toBe(false)
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
  ] as const)('%o -> pending %s, partly unconfirmed %s', (consent, pending, partial) => {
    expect(needsPermissionReview(consent)).toBe(pending)
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
  it.each(Object.entries(pluginConsentMessages))('%s labels system.network:listen', (_, messages) => {
    for (const field of ['title', 'description'] as const) {
      const key = capabilityMessageKey('system.network:listen', field).replace(/^pluginConsent\./, '')
      expect(lookup(messages, key)).toEqual(expect.any(String))
    }
  })
})
