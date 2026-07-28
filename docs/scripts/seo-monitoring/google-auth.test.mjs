import assert from 'node:assert/strict'
import { generateKeyPairSync } from 'node:crypto'
import test from 'node:test'

import { getGoogleAccessToken, parseServiceAccount } from './google-auth.mjs'

test('service-account parsing requires the fields needed for read-only JWT auth', () => {
  assert.throws(() => parseServiceAccount(''), /not configured/)
  assert.throws(() => parseServiceAccount('{'), /not valid JSON/)
  assert.throws(
    () => parseServiceAccount({ type: 'service_account', client_email: 'reader@example.test' }),
    /type, client_email, and private_key/,
  )
})

test('Google token exchange signs a scoped assertion without exposing the private key', async () => {
  const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  const privateKeyPem = privateKey.export({ type: 'pkcs8', format: 'pem' })
  let request
  const accessToken = await getGoogleAccessToken({
    serviceAccount: {
      type: 'service_account',
      client_email: 'seo-reader@example.iam.gserviceaccount.com',
      private_key: privateKeyPem,
    },
    scopes: ['scope-one', 'scope-two'],
    nowSeconds: 1_700_000_000,
    fetchImpl: async (url, options) => {
      request = { url, options }
      return new Response(JSON.stringify({ access_token: 'read-only-token' }), { status: 200 })
    },
  })

  assert.equal(accessToken, 'read-only-token')
  assert.equal(request.url, 'https://oauth2.googleapis.com/token')
  assert.equal(request.options.method, 'POST')
  assert.ok(request.options.signal instanceof AbortSignal)
  assert.match(String(request.options.body), /grant_type=/)
  assert.match(String(request.options.body), /assertion=/)
  assert.doesNotMatch(String(request.options.body), /BEGIN PRIVATE KEY/)
})

test('Google token exchange keeps failed responses and private keys out of diagnostics', async () => {
  const { privateKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
  const privateKeyPem = privateKey.export({ type: 'pkcs8', format: 'pem' })
  const rawResponseMarker = 'raw-response-must-not-leak'

  await assert.rejects(
    getGoogleAccessToken({
      serviceAccount: {
        type: 'service_account',
        client_email: 'seo-reader@example.iam.gserviceaccount.com',
        private_key: privateKeyPem,
      },
      scopes: ['scope-one'],
      fetchImpl: async () => new Response(JSON.stringify({
        error: 'invalid_grant',
        debug: `${rawResponseMarker}:${privateKeyPem}`,
      }), { status: 401 }),
    }),
    error => {
      assert.match(error.message, /HTTP 401.*invalid_grant/)
      assert.doesNotMatch(error.message, /BEGIN PRIVATE KEY/)
      assert.doesNotMatch(error.message, new RegExp(rawResponseMarker))
      return true
    },
  )
})
