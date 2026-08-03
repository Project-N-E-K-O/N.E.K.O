import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { validateConfig } from './report.mjs'
import { applyMonitoringDefaults } from '../seo-monitoring/cli.mjs'

async function readConfig(name) {
  const source = await readFile(new URL(`../../seo/${name}`, import.meta.url), 'utf8')
  return validateConfig(JSON.parse(source))
}

async function readRawMonitoringConfig() {
  const source = await readFile(new URL('../../seo/monitoring.config.json', import.meta.url), 'utf8')
  return JSON.parse(source)
}

async function readMonitoringConfig() {
  return applyMonitoringDefaults(await readRawMonitoringConfig())
}

test('committed configs keep each location/language/domain segment fixed', async () => {
  const [onlineEn, cn, onlineZh] = await Promise.all([
    readConfig('dataforseo.config.json'),
    readConfig('dataforseo.cn.config.json'),
    readConfig('dataforseo.online-zh.config.json'),
  ])

  assert.deepEqual(
    [
      onlineEn.targetDomain,
      onlineEn.locationCode,
      onlineEn.locale,
      onlineEn.serpLanguageCode,
      onlineEn.volumeLanguageCode,
      onlineEn.keywordDifficultyLanguageCode,
      onlineEn.serpDepth,
      onlineEn.keywords.length,
    ],
    ['project-neko.online', 2840, 'en', 'en', 'en', 'en', 100, 19],
  )
  assert.deepEqual(
    [
      cn.targetDomain,
      cn.locationCode,
      cn.locale,
      cn.serpLanguageCode,
      cn.volumeLanguageCode,
      cn.keywordDifficultyLanguageCode,
      cn.serpDepth,
      cn.keywords.length,
    ],
    ['project-neko.cn', 2156, 'zh-CN', 'zh-CN', null, null, 100, 8],
  )
  assert.deepEqual(
    [
      onlineZh.targetDomain,
      onlineZh.locationCode,
      onlineZh.locale,
      onlineZh.serpLanguageCode,
      onlineZh.volumeLanguageCode,
      onlineZh.keywordDifficultyLanguageCode,
      onlineZh.serpDepth,
      onlineZh.keywords.length,
    ],
    ['project-neko.online', 2156, 'zh-CN', 'zh-CN', null, null, 100, 3],
  )
})

test('three feature queries track both the .cn homepage and their concrete documentation pages', async () => {
  const [cn, onlineZh] = await Promise.all([
    readConfig('dataforseo.cn.config.json'),
    readConfig('dataforseo.online-zh.config.json'),
  ])
  const cnKeywords = new Set(cn.keywords.map(item => item.keyword))
  const mappings = new Map(onlineZh.keywords.map(item => [item.keyword, item.landingPage]))

  assert.equal(cnKeywords.has('本地 AI 助手'), true)
  assert.equal(cnKeywords.has('Live2D AI 助手'), true)
  assert.equal(cnKeywords.has('长期记忆 AI 助手'), true)
  assert.equal(mappings.get('本地 AI 助手'), '/zh-CN/guide/local-and-offline')
  assert.equal(mappings.get('Live2D AI 助手'), '/zh-CN/frontend/live2d')
  assert.equal(mappings.get('长期记忆 AI 助手'), '/zh-CN/architecture/memory-system')
  assert.ok(onlineZh.keywords.every(item => item.cta))
  assert.ok(cn.keywords.every(item => item.landingPage === '/'))
})

test('monitoring config binds both sites and all three DataForSEO segments', async () => {
  const [rawConfig, config] = await Promise.all([readRawMonitoringConfig(), readMonitoringConfig()])
  assert.equal(config.schemaVersion, 2)
  assert.deepEqual(config.sites.map(site => site.id), ['cn', 'online'])
  assert.deepEqual(config.dataForSeoSegments.map(segment => segment.id), [
    'cn',
    'online-en',
    'online-zh',
  ])
  assert.equal(config.sites.find(site => site.id === 'cn').ga4.propertyIdEnv, 'GA4_CN_PROPERTY_ID')
  assert.equal(config.sites.find(site => site.id === 'online').ga4.propertyIdEnv, 'GA4_PROPERTY_ID')
  assert.equal(config.sites.find(site => site.id === 'cn').ga4.docsToHomeEvent, undefined)
  assert.equal(config.sites.find(site => site.id === 'online').ga4.docsToHomeEvent, 'docs_home_click')
  assert.ok(rawConfig.sites.every(site => !Object.hasOwn(site.ga4, 'aiReferralRegex')))
  for (const site of config.sites) {
    assert.match(site.ga4.aiReferralRegex, /chatgpt/u)
    assert.match(site.ga4.aiReferralRegex, /deepseek/u)
    assert.match(site.ga4.aiReferralRegex, /qwen/u)
    assert.match(site.ga4.aiReferralRegex, /doubao/u)
    assert.match(site.ga4.aiReferralRegex, /poe/u)
  }
  assert.equal(config.dataForSeoSegments.find(segment => segment.id === 'cn').keywordDifficulty, 'unsupported')
  assert.equal(config.dataForSeoSegments.find(segment => segment.id === 'online-en').keywordDifficulty, 'supported')
})

test('GSC category matching retains English and Chinese product terms', async () => {
  const config = await readMonitoringConfig()
  const patterns = config.sites.map(site => new RegExp(site.gsc.categoryQueryRegex, 'iu'))
  for (const query of ['best AI desktop pet', 'AI桌宠', 'AI 桌面助手', '长期记忆 AI 助手']) {
    assert.equal(patterns.some(pattern => pattern.test(query)), true, `${query} must match a site segment`)
  }
  assert.equal(patterns.some(pattern => pattern.test('python api framework')), false)
})
