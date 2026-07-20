import assert from 'node:assert/strict'
import test from 'node:test'

import {
  INDEXNOW_KEY_FILENAME,
  SITE_ORIGIN,
  collectIndexNowUrls,
  documentationPathToUrl,
  normalizeSiteUrl,
  parseGitNameStatus,
  readIndexNowKey,
  submitIndexNow,
} from './submit-indexnow.mjs'

test('maps VitePress Markdown source paths to clean production URLs', () => {
  assert.equal(documentationPathToUrl('docs/index.md'), `${SITE_ORIGIN}/`)
  assert.equal(
    documentationPathToUrl('docs/guide/index.md'),
    `${SITE_ORIGIN}/guide/`,
  )
  assert.equal(
    documentationPathToUrl('docs/zh-CN/guide/quick-start.md'),
    `${SITE_ORIGIN}/zh-CN/guide/quick-start`,
  )
  assert.equal(documentationPathToUrl('docs/public/example.md'), null)
  assert.equal(documentationPathToUrl('docs/README_en.md'), null)
  assert.equal(documentationPathToUrl('README.md'), null)
})

test('parses added, deleted, and renamed paths from null-delimited git output', () => {
  const output = [
    'M',
    'docs/guide/index.md',
    'D',
    'docs/guide/old.md',
    'R100',
    'docs/guide/before.md',
    'docs/guide/after.md',
    '',
  ].join('\0')

  assert.deepEqual(parseGitNameStatus(output), [
    'docs/guide/index.md',
    'docs/guide/old.md',
    'docs/guide/before.md',
    'docs/guide/after.md',
  ])
})

test('collects, normalizes, and deduplicates changed URLs', () => {
  const urls = collectIndexNowUrls({
    changedPaths: [
      'docs/guide/index.md',
      'docs/guide/index.md',
      'docs/guide/quick-start.md',
      `docs/public/${INDEXNOW_KEY_FILENAME}`,
      'docs/public/logo.jpg',
    ],
    explicitUrls: ['/guide/quick-start#install'],
  })

  assert.deepEqual(urls, [
    `${SITE_ORIGIN}/`,
    `${SITE_ORIGIN}/guide/`,
    `${SITE_ORIGIN}/guide/quick-start`,
  ])
})

test('rejects explicit URLs outside the production host', () => {
  assert.throws(
    () => normalizeSiteUrl('https://example.com/copied-page'),
    /must belong to/,
  )
})

test('the public key file name and contents agree', async () => {
  const key = await readIndexNowKey()
  assert.equal(`${key}.txt`, INDEXNOW_KEY_FILENAME)
})

test('submits the documented IndexNow payload', async () => {
  const requests = []
  const result = await submitIndexNow([`${SITE_ORIGIN}/guide/`], {
    key: INDEXNOW_KEY_FILENAME.replace(/\.txt$/, ''),
    fetchImpl: async (url, options) => {
      requests.push({ url, options })
      return new Response('', { status: 202 })
    },
  })

  assert.deepEqual(result, { status: 202, attempts: 1, submitted: 1 })
  assert.equal(requests.length, 1)
  const payload = JSON.parse(requests[0].options.body)
  assert.equal(payload.host, 'project-neko.online')
  assert.equal(
    payload.keyLocation,
    `${SITE_ORIGIN}/${INDEXNOW_KEY_FILENAME}`,
  )
  assert.deepEqual(payload.urlList, [`${SITE_ORIGIN}/guide/`])
})

test('retries a rate-limited submission and then succeeds', async () => {
  let attempts = 0
  const result = await submitIndexNow([`${SITE_ORIGIN}/`], {
    key: INDEXNOW_KEY_FILENAME.replace(/\.txt$/, ''),
    maxAttempts: 2,
    sleepImpl: async () => {},
    fetchImpl: async () => {
      attempts += 1
      if (attempts === 1) {
        return new Response('slow down', {
          status: 429,
          headers: { 'retry-after': '0' },
        })
      }
      return new Response('', { status: 200 })
    },
  })

  assert.equal(attempts, 2)
  assert.deepEqual(result, { status: 200, attempts: 2, submitted: 1 })
})
