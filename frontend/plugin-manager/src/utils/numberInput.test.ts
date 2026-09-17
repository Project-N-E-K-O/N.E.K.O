import { describe, expect, it } from 'vitest'
import { nextNumberText, settleNumberText } from './numberInput'

describe('numeric text input', () => {
  it('keeps the previous text when the browser reports an empty reading', () => {
    // Mid-edit readings: "-", "1." and "1e" all arrive as "".
    expect(nextNumberText('8', '')).toBe('8')
    expect(nextNumberText('-5', '')).toBe('-5')
  })

  it('accepts readable intermediate and final text', () => {
    expect(nextNumberText('', '-5')).toBe('-5')
    expect(nextNumberText('8', '8.5')).toBe('8.5')
  })

  it('settles incomplete text on blur and keeps finite values', () => {
    expect(settleNumberText('', '8')).toBe('8')
    expect(settleNumberText('-', '8')).toBe('8')
    expect(settleNumberText('1e', '8')).toBe('8')
    expect(settleNumberText('-5', '8')).toBe('-5')
    expect(settleNumberText('8.5', '8')).toBe('8.5')
  })
})
