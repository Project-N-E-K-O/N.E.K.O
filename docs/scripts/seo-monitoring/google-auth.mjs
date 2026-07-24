import { createPrivateKey, sign } from 'node:crypto'

const TOKEN_URL = 'https://oauth2.googleapis.com/token'

function base64url(value) {
  return Buffer.from(value).toString('base64url')
}

export function parseServiceAccount(source) {
  if (!source) throw new Error('GOOGLE_SERVICE_ACCOUNT_JSON is not configured')

  let account
  try {
    account = typeof source === 'string' ? JSON.parse(source) : source
  } catch {
    throw new Error('GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON')
  }
  if (account?.type !== 'service_account' || !account.client_email || !account.private_key) {
    throw new Error('Google service account JSON must contain type, client_email, and private_key')
  }
  return account
}

export async function getGoogleAccessToken({
  serviceAccount,
  scopes,
  fetchImpl = globalThis.fetch,
  nowSeconds = Math.floor(Date.now() / 1000),
}) {
  const account = parseServiceAccount(serviceAccount)
  if (!Array.isArray(scopes) || scopes.length === 0) {
    throw new TypeError('At least one Google OAuth scope is required')
  }

  const header = base64url(JSON.stringify({ alg: 'RS256', typ: 'JWT' }))
  const claim = base64url(JSON.stringify({
    iss: account.client_email,
    scope: scopes.join(' '),
    aud: TOKEN_URL,
    iat: nowSeconds,
    exp: nowSeconds + 3600,
  }))
  const unsigned = `${header}.${claim}`
  const signature = sign(
    'RSA-SHA256',
    Buffer.from(unsigned),
    createPrivateKey(account.private_key),
  ).toString('base64url')

  const response = await fetchImpl(TOKEN_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
      assertion: `${unsigned}.${signature}`,
    }),
  })
  const source = await response.text()
  let payload = {}
  try {
    payload = JSON.parse(source)
  } catch {
    // Keep token errors sanitized; the raw response is not useful in a report.
  }
  if (!response.ok || !payload.access_token) {
    throw new Error(
      `Google OAuth token request failed (HTTP ${response.status}): `
      + `${payload.error_description ?? payload.error ?? 'unknown error'}`,
    )
  }
  return payload.access_token
}
