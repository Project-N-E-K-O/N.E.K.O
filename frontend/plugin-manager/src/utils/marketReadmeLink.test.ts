import { describe, expect, it } from 'vitest'
import { resolveMarketReadmeLink } from './marketReadmeLink'

describe('resolveMarketReadmeLink', () => {
  const repository = 'https://github.com/example/plugin'

  it('resolves reviewed GitHub-relative links and images at the source revision', () => {
    expect(resolveMarketReadmeLink('./docs/setup.md', repository, undefined, { sourceRef: 'abc123' })).toBe(
      'https://github.com/example/plugin/blob/abc123/docs/setup.md',
    )
    expect(resolveMarketReadmeLink('../images/logo.png', repository, undefined, {
      sourceRef: 'abc123',
      resource: 'image',
    })).toBe(
      'https://raw.githubusercontent.com/example/plugin/abc123/images/logo.png',
    )
  })

  it('resolves protocol-relative links for the external browser', () => {
    expect(resolveMarketReadmeLink('//docs.example.test/guide', repository)).toBe(
      'https://docs.example.test/guide',
    )
  })

  it('rejects unsafe or malformed link protocols', () => {
    expect(resolveMarketReadmeLink('javascript:alert(1)', repository)).toBeNull()
    expect(resolveMarketReadmeLink('data:text/html,unsafe', repository)).toBeNull()
    expect(resolveMarketReadmeLink('http://[invalid', repository)).toBeNull()
  })
})
