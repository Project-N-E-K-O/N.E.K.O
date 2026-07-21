import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DATAFORSEO_ENDPOINTS,
  DataForSeoApiError,
  DataForSeoClient,
} from './client.mjs'

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

test('client sends Basic Auth and a JSON task array', async () => {
  let captured
  const client = new DataForSeoClient({
    login: 'api-login',
    password: 'api-password',
    baseUrl: 'https://example.test/',
    fetchImpl: async (url, options) => {
      captured = { url, options }
      return jsonResponse({
        status_code: 20000,
        status_message: 'Ok.',
        tasks_error: 0,
        tasks: [{ status_code: 20000, result: [] }],
      })
    },
  })

  const tasks = [{ keywords: ['open source ai companion'] }]
  await client.post(DATAFORSEO_ENDPOINTS.searchVolume, tasks)

  assert.equal(
    captured.url,
    'https://example.test/v3/keywords_data/google_ads/search_volume/live',
  )
  assert.equal(captured.options.method, 'POST')
  assert.equal(
    captured.options.headers.Authorization,
    `Basic ${Buffer.from('api-login:api-password').toString('base64')}`,
  )
  assert.equal(captured.options.headers['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(captured.options.body), tasks)
})

test('client rejects a top-level DataForSEO error without exposing credentials', async () => {
  const client = new DataForSeoClient({
    login: 'private-login',
    password: 'private-password',
    fetchImpl: async () => jsonResponse({
      status_code: 40100,
      status_message: 'Authentication failed.',
      tasks_error: 0,
      tasks: [],
    }),
  })

  await assert.rejects(
    client.post(DATAFORSEO_ENDPOINTS.keywordDifficulty, [{}]),
    error => {
      assert.ok(error instanceof DataForSeoApiError)
      assert.match(error.message, /40100/)
      assert.doesNotMatch(error.message, /private-login|private-password/)
      return true
    },
  )
})

test('client rejects failed tasks even when the response envelope succeeded', async () => {
  const client = new DataForSeoClient({
    login: 'login',
    password: 'password',
    fetchImpl: async () => jsonResponse({
      status_code: 20000,
      status_message: 'Ok.',
      tasks_error: 1,
      tasks: [{ status_code: 40501, status_message: 'Invalid field.' }],
    }),
  })

  await assert.rejects(
    client.post(DATAFORSEO_ENDPOINTS.organicSerp, [{}]),
    /40501: Invalid field/,
  )
})

test('client rejects missing credentials before sending a request', () => {
  assert.throws(
    () => new DataForSeoClient({ login: '', password: 'password' }),
    /login must be a non-empty string/,
  )
  assert.throws(
    () => new DataForSeoClient({ login: 'login', password: '' }),
    /password must be a non-empty string/,
  )
})
