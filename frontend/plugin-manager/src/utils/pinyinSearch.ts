import { pinyin } from 'pinyin-pro'
import { boundedMemo } from './boundedMemo'

export type PinyinPattern = 'pinyin' | 'first'
export type PinyinSearch = (value: string, pattern: PinyinPattern) => string

function isCjkText(value: string): boolean {
  return /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(value)
}

// LRU scans thrash once the working set exceeds capacity, and each plugin
// contributes up to six entries (three texts x two patterns).
const memoizedPinyin = boundedMemo(8192, (key) => {
  const pattern = key[0] === 'f' ? 'first' : 'pinyin'
  try {
    return pinyin(key.slice(1), {
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
  return memoizedPinyin(`${pattern === 'first' ? 'f' : 'p'}${value}`)
}
