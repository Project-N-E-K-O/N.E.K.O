import assert from 'node:assert/strict'
import test from 'node:test'

import { DATAFORSEO_ENDPOINTS } from './client.mjs'
import {
  buildPlan,
  createDataForSeoReport,
  domainMatchesTarget,
  mergeKeywordMetrics,
  summarizeSerpResult,
  validateConfig,
} from './report.mjs'

const rawConfig = {
  targetDomain: 'project-neko.online',
  locationCode: 2840,
  languageCode: 'en',
  device: 'desktop',
  serpDepth: 10,
  keywords: [
    {
      keyword: 'live2d ai assistant',
      landingPage: '/frontend/live2d',
      intent: 'MOFU',
    },
    {
      keyword: 'open source ai companion',
      landingPage: '/',
      intent: 'BOFU',
    },
  ],
}

test('config validation normalizes tracked entries and rejects duplicates', () => {
  const config = validateConfig(rawConfig)
  assert.equal(config.targetDomain, 'project-neko.online')
  assert.equal(config.keywords[0].landingPage, '/frontend/live2d')

  assert.throws(
    () => validateConfig({
      ...rawConfig,
      keywords: ['Same Keyword', ' same keyword '],
    }),
    /Duplicate keyword/,
  )
})

test('config validation enforces the Google Ads keyword limits before paid calls', () => {
  assert.throws(
    () => validateConfig({
      ...rawConfig,
      keywords: ['one two three four five six seven eight nine ten eleven'],
    }),
    /cannot exceed 10 words/,
  )
  assert.throws(
    () => validateConfig({
      ...rawConfig,
      keywords: ['x'.repeat(81)],
    }),
    /cannot exceed 80 characters/,
  )
})

test('request plan makes paid call volume visible before execution', () => {
  const config = validateConfig(rawConfig)
  const plan = buildPlan(config, {
    mode: 'all',
    depth: 30,
    includeAiOverview: true,
  })

  assert.deepEqual(plan.requests, {
    searchVolume: 1,
    keywordDifficulty: 1,
    organicSerp: 2,
    total: 4,
  })
  assert.equal(plan.maximumSerpPages, 6)
  assert.equal(plan.asynchronousAiOverviewRequests, 2)
})

test('request plan labels an invalid CLI depth override as --depth', () => {
  const config = validateConfig(rawConfig)
  assert.throws(
    () => buildPlan(config, { depth: 0 }),
    /--depth must be an integer from 1 to 100/,
  )
})

test('keyword metrics merge Google Ads volume with organic keyword difficulty', () => {
  const config = validateConfig(rawConfig)
  const volumePayload = {
    tasks: [{
      result: [{
        keyword: 'live2d ai assistant',
        search_volume: 390,
        competition: 'LOW',
        competition_index: 12,
        cpc: 0.75,
        monthly_searches: [{ year: 2026, month: 6, search_volume: 390 }],
      }],
    }],
  }
  const difficultyPayload = {
    tasks: [{
      result: [{
        items: [{ keyword: 'live2d ai assistant', keyword_difficulty: 19 }],
      }],
    }],
  }

  const metrics = mergeKeywordMetrics(config, volumePayload, difficultyPayload)
  assert.equal(metrics[0].searchVolume, 390)
  assert.equal(metrics[0].keywordDifficulty, 19)
  assert.equal(metrics[0].adsCompetitionIndex, 12)
  assert.equal(metrics[1].searchVolume, null)
  assert.equal(metrics[1].keywordDifficulty, null)
})

test('SERP summary reports organic rank, landing-page match, and AIO citation', () => {
  const config = validateConfig(rawConfig)
  const result = {
    item_types: ['organic', 'ai_overview'],
    check_url: 'https://www.google.com/search?q=live2d+ai+assistant',
    datetime: '2026-07-21 01:02:03 +00:00',
    items: [
      {
        type: 'organic',
        rank_group: 1,
        rank_absolute: 2,
        domain: 'example.com',
        url: 'https://example.com/result',
      },
      {
        type: 'ai_overview',
        references: [{
          type: 'ai_overview_reference',
          source: 'Project N.E.K.O.',
          domain: 'project-neko.online',
          url: 'https://project-neko.online/frontend/live2d',
          title: 'Live2D models',
        }],
      },
      {
        type: 'organic',
        rank_group: 4,
        rank_absolute: 7,
        domain: 'www.project-neko.online',
        url: 'https://project-neko.online/frontend/live2d/',
        title: 'Live2D models',
      },
    ],
  }

  const summary = summarizeSerpResult(
    config.keywords[0],
    config.targetDomain,
    result,
  )
  assert.equal(summary.organicRank, 4)
  assert.equal(summary.absoluteRank, 7)
  assert.equal(summary.landingPageMatched, true)
  assert.equal(summary.aiOverviewTriggered, true)
  assert.equal(summary.aiOverviewCitedTarget, true)
  assert.equal(summary.aiOverviewReferences[0].source, 'Project N.E.K.O.')
})

test('domain matching accepts subdomains but not lookalike domains', () => {
  assert.equal(domainMatchesTarget('www.project-neko.online', 'project-neko.online'), true)
  assert.equal(domainMatchesTarget('https://docs.project-neko.online/x', 'project-neko.online'), true)
  assert.equal(domainMatchesTarget('evilproject-neko.online', 'project-neko.online'), false)
})

test('dry run returns a plan without credentials or client calls', async () => {
  const config = validateConfig(rawConfig)
  const report = await createDataForSeoReport({
    client: null,
    config,
    dryRun: true,
    generatedAt: '2026-07-21T00:00:00.000Z',
  })

  assert.equal(report.dryRun, true)
  assert.equal(report.plan.requests.total, 4)
  assert.equal(report.keywordMetrics, null)
  assert.equal(report.serp, null)
  assert.equal(report.costs, null)
})

test('keywords mode calls only the two metric endpoints and reports API cost', async () => {
  const config = validateConfig(rawConfig)
  const calls = []
  const client = {
    async post(endpoint) {
      calls.push(endpoint)
      if (endpoint === DATAFORSEO_ENDPOINTS.searchVolume) {
        return {
          cost: 0.01,
          tasks: [{ result: [{ keyword: 'live2d ai assistant', search_volume: 390 }] }],
        }
      }
      return {
        cost: 0.02,
        tasks: [{ result: [{ items: [{
          keyword: 'live2d ai assistant',
          keyword_difficulty: 19,
        }] }] }],
      }
    },
  }

  const report = await createDataForSeoReport({ client, config, mode: 'keywords' })
  assert.deepEqual(calls, [
    DATAFORSEO_ENDPOINTS.searchVolume,
    DATAFORSEO_ENDPOINTS.keywordDifficulty,
  ])
  assert.equal(report.costs.totalUsd, 0.03)
  assert.equal(report.serp, null)
})

test('SERP mode sends one organic-targeted task per keyword', async () => {
  const config = validateConfig(rawConfig)
  const calls = []
  const client = {
    async post(endpoint, tasks) {
      calls.push({ endpoint, tasks })
      return {
        cost: 0.01,
        tasks: [{ result: [{ items: [], item_types: [] }] }],
      }
    },
  }

  const report = await createDataForSeoReport({ client, config, mode: 'serp' })
  assert.equal(calls.length, config.keywords.length)
  assert.ok(calls.every(call => call.endpoint === DATAFORSEO_ENDPOINTS.organicSerp))
  assert.deepEqual(calls[0].tasks, [{
    keyword: config.keywords[0].keyword,
    location_code: 2840,
    language_code: 'en',
    device: 'desktop',
    depth: 10,
    max_crawl_pages: 1,
    stop_crawl_on_match: [{
      match_type: 'with_subdomains',
      match_value: 'project-neko.online',
    }],
    find_targets_in: ['organic'],
  }])
  assert.equal(report.costs.organicSerpUsd, 0.02)
})
