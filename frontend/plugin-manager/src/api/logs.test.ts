// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getPluginLogStreamUrl } from './logs'

const config = vi.hoisted(() => ({ base: '' }))
vi.mock('./index', () => ({ get: vi.fn() }))
vi.mock('@/utils/constants', () => ({ get API_BASE_URL() { return config.base } }))

afterEach(() => { config.base = '' })

describe('log stream URL compatibility', () => {
  it.each(['', '/api', '/api/', 'api'])('accepts a same-origin base %j', base => {
    config.base = base
    const url = new URL(getPluginLogStreamUrl('demo id', { lines: 500, search: '猫 & ready' }))
    expect(url.host).toBe(window.location.host)
    expect(url.pathname).toBe('/ws/logs/demo%20id')
    expect(url.searchParams.get('search')).toBe('猫 & ready')
    expect(url.searchParams.get('lines')).toBe('500')
    expect(url.searchParams.get('search_scope')).toBe('all')
  })

  it.each([
    ['https://example.com/neko-api', 'wss:'],
    ['http://example.com/neko-api/', 'ws:'],
  ])('keeps the existing host-root stream path for %s', (base, protocol) => {
    config.base = base
    const url = new URL(getPluginLogStreamUrl('_server', { lines: 100, level: 'WARNING' }))
    expect(url.host).toBe('example.com')
    expect(url.protocol).toBe(protocol)
    expect(url.pathname).toBe('/ws/logs/_server')
    expect(url.searchParams.get('level')).toBe('WARNING')
  })
})
