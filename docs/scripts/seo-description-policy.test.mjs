import assert from 'node:assert/strict'
import test from 'node:test'

import { descriptionLengthWarning } from './seo-description-policy.mjs'

test('short CJK descriptions produce an advisory warning', () => {
  const description =
    '在不依赖标签触发云端构建的情况下，构建、签名、验证并发布稳定桌面资产。'

  assert.equal(Array.from(description).length, 35)
  assert.equal(
    descriptionLengthWarning(description),
    'meta description length is outside the recommended 40-180 character range, found 35',
  )
})

test('description length counts Unicode code points', () => {
  const description = `${'a'.repeat(38)}😀`

  assert.equal(Array.from(description).length, 39)
  assert.equal(description.length, 40)
  assert.match(descriptionLengthWarning(description), /found 39$/)
})

test('descriptions within the recommended range produce no warning', () => {
  assert.equal(descriptionLengthWarning('a'.repeat(40)), null)
  assert.equal(descriptionLengthWarning('a'.repeat(180)), null)
})

test('long descriptions produce an advisory warning', () => {
  assert.equal(
    descriptionLengthWarning('a'.repeat(181)),
    'meta description length is outside the recommended 40-180 character range, found 181',
  )
})
