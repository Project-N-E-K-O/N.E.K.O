import { describe, expect, it } from 'vitest'
import { resolveMarketReadmeLink } from './marketReadmeLink'

describe('resolveMarketReadmeLink', () => {
  const repository = 'https://github.com/example/plugin'

  it('resolves relative and protocol-relative links for the external browser', () => {
    expect(resolveMarketReadmeLink('./docs/setup', repository)).toBe(
      'https://github.com/example/plugin/docs/setup',
    )
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
