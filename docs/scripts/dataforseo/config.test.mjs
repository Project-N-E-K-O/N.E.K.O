import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { validateConfig } from './report.mjs'

const DIRECT_DESKTOP_PET_KEYWORDS = new Map([
  ['local ai desktop companion', '/guide'],
  ['ai desktop pet', '/'],
  ['open source ai desktop pet', '/'],
  ['virtual ai desktop pet', '/'],
  ['desktop ai companion', '/'],
  ['ai desktop companion', '/'],
  ['proactive desktop companion', '/'],
  ['open source desktop companion', '/'],
  ['desktop companion app', '/'],
  ['interactive ai desktop pet', '/'],
  ['customizable ai desktop pet', '/'],
  ['live2d ai desktop pet', '/frontend/live2d'],
])

async function readCommittedConfig() {
  const source = await readFile(new URL('../../seo/dataforseo.config.json', import.meta.url), 'utf8')
  return validateConfig(JSON.parse(source))
}

async function readMonitoringConfig() {
  const source = await readFile(new URL('../../seo/monitoring.config.json', import.meta.url), 'utf8')
  return JSON.parse(source)
}

test('committed config tracks the US English desktop-pet category baseline', async () => {
  const config = await readCommittedConfig()
  const tracked = new Map(config.keywords.map(item => [item.keyword, item.landingPage]))

  assert.equal(config.locationCode, 2840)
  assert.equal(config.languageCode, 'en')
  assert.equal(config.serpDepth, 10)
  assert.equal(config.keywords.length, 19)
  assert.equal(DIRECT_DESKTOP_PET_KEYWORDS.size, 12)

  for (const [keyword, landingPage] of DIRECT_DESKTOP_PET_KEYWORDS) {
    assert.equal(tracked.get(keyword), landingPage, `${keyword} must map to ${landingPage}`)
  }
})

test('daily monitoring uses the exact same 12-keyword desktop-pet segment', async () => {
  const config = await readMonitoringConfig()
  assert.deepEqual(
    new Set(config.desktopPetKeywords),
    new Set(DIRECT_DESKTOP_PET_KEYWORDS.keys()),
  )
})

test('GSC category matching retains English and Chinese desktop-pet terms', async () => {
  const config = await readMonitoringConfig()
  const categoryPattern = new RegExp(config.gsc.categoryQueryRegex, 'iu')

  for (const query of [
    'best AI desktop pet',
    'open source desktop companion',
    'AI桌宠',
    '开源AI桌宠',
    'AI桌面伴侣',
    '桌面AI伴侣',
  ]) {
    assert.equal(categoryPattern.test(query), true, `${query} must match the GSC category`)
  }

  assert.equal(categoryPattern.test('python api framework'), false)
})

test('unified monitoring keeps the keyword source, opportunity thresholds, and AI sources explicit', async () => {
  const config = await readMonitoringConfig()
  const aiPattern = new RegExp(config.ga4.aiReferralRegex, 'iu')

  assert.equal(config.dataForSeoConfigPath, 'seo/dataforseo.config.json')
  assert.equal(config.site.ctaGoal, 'Steam download')
  assert.deepEqual(config.gsc.opportunities, {
    minImpressions: 10,
    maxCtr: 0.03,
    strikingDistanceStart: 10,
    strikingDistanceEnd: 20,
  })
  for (const source of [
    'chatgpt.com',
    'perplexity.ai',
    'claude.ai',
    'gemini.google.com',
    'deepseek.com',
    'qwen.ai',
    'doubao.com',
    'poe.com',
    'edgeservices.bing.com',
  ]) {
    assert.equal(aiPattern.test(source), true, `${source} must be tracked as an AI referral`)
  }
})
