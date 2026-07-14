import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveConfiguredPort } from '../port-config.js'

function configReader(config) {
  return (_path, encoding) => {
    assert.equal(encoding, 'utf8')
    return JSON.stringify(config)
  }
}

test('environment ports take precedence over desktop config', () => {
  const port = resolveConfiguredPort('MAIN_SERVER_PORT', 48911, {
    env: { NEKO_MAIN_SERVER_PORT: '43101', MAIN_SERVER_PORT: '43102' },
    platform: 'linux',
    homeDir: '/home/neko',
    readFileSync: configReader({ MAIN_SERVER_PORT: 43103 }),
  })
  assert.equal(port, 43101)
})

test('desktop port_config is used when environment ports are absent', () => {
  let requestedPath = ''
  const port = resolveConfiguredPort('MAIN_SERVER_PORT', 48911, {
    env: { XDG_CONFIG_HOME: '/tmp/neko-config' },
    platform: 'linux',
    homeDir: '/home/neko',
    readFileSync: (filePath) => {
      requestedPath = filePath
      return JSON.stringify({ MAIN_SERVER_PORT: 43103 })
    },
  })
  assert.equal(requestedPath, '/tmp/neko-config/N.E.K.O/port_config.json')
  assert.equal(port, 43103)
})

test('invalid or unreadable config falls back to the source default', () => {
  assert.equal(
    resolveConfiguredPort('MAIN_SERVER_PORT', 48911, {
      env: {},
      platform: 'darwin',
      homeDir: '/Users/neko',
      readFileSync: configReader({ MAIN_SERVER_PORT: 70000 }),
    }),
    48911,
  )
  assert.equal(
    resolveConfiguredPort('MAIN_SERVER_PORT', 48911, {
      env: {},
      platform: 'win32',
      homeDir: 'C:/Users/neko',
      readFileSync: () => {
        throw new Error('missing')
      },
    }),
    48911,
  )
})
