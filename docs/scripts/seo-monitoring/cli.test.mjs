import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { applyMonitoringDefaults, readOptionalJson } from './cli.mjs'

test('monitoring defaults are shared while site overrides remain authoritative', () => {
  const config = applyMonitoringDefaults({
    defaults: { ga4: { aiReferralRegex: 'shared', ctaEvent: 'default-event' } },
    sites: [
      { id: 'cn', ga4: { propertyIdEnv: 'GA4_CN_PROPERTY_ID' } },
      { id: 'online', ga4: { propertyIdEnv: 'GA4_PROPERTY_ID', ctaEvent: 'site-event' } },
    ],
  })

  assert.equal(config.sites[0].ga4.aiReferralRegex, 'shared')
  assert.equal(config.sites[0].ga4.ctaEvent, 'default-event')
  assert.equal(config.sites[1].ga4.aiReferralRegex, 'shared')
  assert.equal(config.sites[1].ga4.ctaEvent, 'site-event')
})

test('optional IndexNow evidence distinguishes missing files from unreadable JSON', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'neko-indexnow-'))
  try {
    const absent = await readOptionalJson(
      join(directory, 'absent.json'),
      'IndexNow evidence is not configured',
      { missingStatus: 'not_run' },
    )
    const malformedPath = join(directory, 'malformed.json')
    await writeFile(malformedPath, '{broken', 'utf8')
    const malformed = await readOptionalJson(
      malformedPath,
      'IndexNow evidence is not configured',
      { missingStatus: 'not_run' },
    )

    assert.equal(absent.status, 'not_run')
    assert.match(absent.reason, /file not found/u)
    assert.equal(malformed.status, 'unavailable')
    assert.match(malformed.reason, /JSON|Expected property/u)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})
