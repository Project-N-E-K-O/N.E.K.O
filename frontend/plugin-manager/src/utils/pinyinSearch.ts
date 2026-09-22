import { pinyin } from 'pinyin-pro'
import { boundedMemo } from './boundedMemo'

export type PinyinPattern = 'pinyin' | 'first'
export type PinyinSearch = (value: string, pattern: PinyinPattern) => string

function isCjkText(value: string): boolean {
  return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(value)
}

const memoizedPinyin = boundedMemo(1024, (key) => {
  const [value, pattern] = JSON.parse(key) as [string, PinyinPattern]
  try {
    return pinyin(value, {
      toneType: 'none',
      type: 'string',
      pattern,
      nonZh: 'consecutive',
      v: true,
      traditional: true,
    }).trim()
  } catch {
    return ''
  }
})

export const safePinyin: PinyinSearch = (value, pattern) => {
  if (!value.trim() || !isCjkText(value)) return ''
  return memoizedPinyin(JSON.stringify([value, pattern]))
}
