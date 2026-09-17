import { describe, expect, it } from 'vitest'
import { parseNumberText, settleNumberText } from './numberInput'

describe('numeric text field', () => {
  it('parses complete numbers, including negative and exponent forms', () => {
    expect(parseNumberText('5')).toBe(5)
    expect(parseNumberText('-5')).toBe(-5)
    expect(parseNumberText('1.5')).toBe(1.5)
    expect(parseNumberText('1e2')).toBe(100)
    expect(parseNumberText(' 7 ')).toBe(7)
  })

  it('rejects empty and in-progress text', () => {
    expect(parseNumberText('')).toBeUndefined()
    expect(parseNumberText('   ')).toBeUndefined()
    expect(parseNumberText('-')).toBeUndefined()
    expect(parseNumberText('1e')).toBeUndefined()
    expect(parseNumberText('abc')).toBeUndefined()
  })

  it('settles on blur to a canonical value or the previous one', () => {
    expect(settleNumberText('-5', '8')).toBe('-5')
    expect(settleNumberText('1.50', '8')).toBe('1.5')
    expect(settleNumberText('1.', '8')).toBe('1')
    expect(settleNumberText('', '8')).toBe('8')
    expect(settleNumberText('-', '8')).toBe('8')
    expect(settleNumberText('1e', '8')).toBe('8')
  })
})
