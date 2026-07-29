import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { readOptionalJson } from './cli.mjs'

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
