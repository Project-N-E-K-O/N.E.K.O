import { describe, expect, it } from 'vitest'

import { safePinyin } from './pinyinSearch'

describe('safePinyin', () => {
  it('returns no index for empty or non-CJK text', () => {
    expect(safePinyin('', 'pinyin')).toBe('')
    expect(safePinyin('plugin-manager', 'pinyin')).toBe('')
  })

  it('builds full and initial indexes for CJK text', () => {
    const full = safePinyin('插件管理', 'pinyin')
    const initials = safePinyin('插件管理', 'first')

    expect(full).toBeTruthy()
    expect(initials).toBeTruthy()
    expect(full).not.toMatch(/[\u3400-\u9fff]/)
    expect(initials).not.toMatch(/[\u3400-\u9fff]/)
  })

  it('is deterministic for repeated inputs', () => {
    expect(safePinyin('插件管理', 'pinyin')).toBe(safePinyin('插件管理', 'pinyin'))
    expect(safePinyin('插件管理', 'first')).toBe(safePinyin('插件管理', 'first'))
  })
})
