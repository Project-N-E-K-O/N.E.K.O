import assert from 'node:assert/strict'
import test from 'node:test'

import { DATAFORSEO_ENDPOINTS, DataForSeoApiError } from './client.mjs'
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
  assert.equal(report.status, 'complete')
  assert.deepEqual(report.errors, [])
  assert.equal(report.serp[0].requestAttempts, 1)
  assert.equal(report.serp[0].error, null)
})

test('SERP mode retries transient zero-cost failures with bounded exponential backoff', async () => {
  const config = validateConfig({ ...rawConfig, keywords: [rawConfig.keywords[0]] })
  const delays = []
  let calls = 0
  const client = {
    async post() {
      calls += 1
      if (calls < 3) {
        throw new DataForSeoApiError('temporary search engine failure', {
          endpoint: DATAFORSEO_ENDPOINTS.organicSerp,
          statusCode: 40101,
          retryable: true,
          costUsd: 0,
        })
      }
      return {
        cost: 0.01,
        tasks: [{ result: [{ items: [], item_types: [] }] }],
      }
    },
  }

  const report = await createDataForSeoReport({
    client,
    config,
    mode: 'serp',
    retryOptions: {
      maxAttempts: 3,
      baseDelayMs: 100,
      sleep: async delayMs => delays.push(delayMs),
      onRetry: () => {},
    },
  })

  assert.equal(calls, 3)
  assert.deepEqual(delays, [100, 200])
  assert.equal(report.status, 'complete')
  assert.deepEqual(report.errors, [])
  assert.equal(report.serp[0].requestAttempts, 3)
  assert.equal(report.costs.organicSerpUsd, 0.01)
})

test('SERP mode does not retry when a failed response reports a nonzero cost', async () => {
  const config = validateConfig({ ...rawConfig, keywords: [rawConfig.keywords[0]] })
  let calls = 0
  const client = {
    async post() {
      calls += 1
      throw new DataForSeoApiError('temporary but billed search engine failure', {
        endpoint: DATAFORSEO_ENDPOINTS.organicSerp,
        statusCode: 40101,
        retryable: true,
        costUsd: 0.004,
      })
    },
  }

  const report = await createDataForSeoReport({
    client,
    config,
    mode: 'serp',
    retryOptions: {
      maxAttempts: 3,
      baseDelayMs: 0,
      sleep: async () => {},
      onRetry: () => {},
    },
  })

  assert.equal(calls, 1)
  assert.equal(report.status, 'failed')
  assert.equal(report.errors.length, 1)
  assert.equal(report.errors[0].attempts, 1)
  assert.equal(report.errors[0].incurredCostUsd, 0.004)
  assert.equal(report.errors[0].retrySkippedDueToReportedCost, true)
  assert.equal(report.costs.organicSerpUsd, 0.004)
})

test('SERP mode records an exhausted zero-cost keyword error and continues', async () => {
  const config = validateConfig(rawConfig)
  const calls = []
  const client = {
    async post(_endpoint, tasks) {
      const keyword = tasks[0].keyword
      calls.push(keyword)
      if (keyword === config.keywords[0].keyword) {
        throw new DataForSeoApiError('temporary search engine failure', {
          endpoint: DATAFORSEO_ENDPOINTS.organicSerp,
          statusCode: 40101,
          retryable: true,
          costUsd: 0,
        })
      }
      return {
        cost: 0.01,
        tasks: [{ result: [{ items: [], item_types: [] }] }],
      }
    },
  }

  const report = await createDataForSeoReport({
    client,
    config,
    mode: 'serp',
    retryOptions: {
      maxAttempts: 2,
      baseDelayMs: 0,
      sleep: async () => {},
      onRetry: () => {},
    },
  })

  assert.deepEqual(calls, [
    config.keywords[0].keyword,
    config.keywords[0].keyword,
    config.keywords[1].keyword,
  ])
  assert.equal(report.status, 'partial')
  assert.equal(report.errors.length, 1)
  assert.equal(report.errors[0].keyword, config.keywords[0].keyword)
  assert.equal(report.errors[0].statusCode, 40101)
  assert.equal(report.errors[0].attempts, 2)
  assert.equal(report.errors[0].incurredCostUsd, 0)
  assert.equal(report.errors[0].retrySkippedDueToReportedCost, false)
  assert.equal(report.serp[0].organicRank, null)
  assert.equal(report.serp[0].requestAttempts, 2)
  assert.equal(report.serp[0].error.statusCode, 40101)
  assert.equal(report.serp[1].error, null)
  assert.equal(report.costs.organicSerpUsd, 0.01)
})

test('SERP mode marks an all-keyword outage as failed while retaining diagnostics', async () => {
  const config = validateConfig(rawConfig)
  const client = {
    async post() {
      throw new DataForSeoApiError('temporary search engine failure', {
        endpoint: DATAFORSEO_ENDPOINTS.organicSerp,
        statusCode: 40101,
        retryable: true,
      })
    },
  }

  const report = await createDataForSeoReport({
    client,
    config,
    mode: 'serp',
    retryOptions: {
      maxAttempts: 1,
      baseDelayMs: 0,
      sleep: async () => {},
      onRetry: () => {},
    },
  })

  assert.equal(report.status, 'failed')
  assert.equal(report.serp.length, config.keywords.length)
  assert.equal(report.errors.length, config.keywords.length)
  assert.ok(report.serp.every(item => item.error?.statusCode === 40101))
})

test('all mode preserves keyword metrics when every SERP request fails', async () => {
  const config = validateConfig(rawConfig)
  const client = {
    async post(endpoint) {
      if (endpoint === DATAFORSEO_ENDPOINTS.searchVolume) {
        return {
          cost: 0.01,
          tasks: [{ result: config.keywords.map(entry => ({
            keyword: entry.keyword,
            search_volume: 10,
          })) }],
        }
      }
      if (endpoint === DATAFORSEO_ENDPOINTS.keywordDifficulty) {
        return {
          cost: 0.02,
          tasks: [{ result: [{ items: config.keywords.map(entry => ({
            keyword: entry.keyword,
            keyword_difficulty: 20,
          })) }] }],
        }
      }
      throw new DataForSeoApiError('temporary search engine failure', {
        endpoint: DATAFORSEO_ENDPOINTS.organicSerp,
        statusCode: 40101,
        retryable: true,
      })
    },
  }

  const report = await createDataForSeoReport({
    client,
    config,
    mode: 'all',
    retryOptions: {
      maxAttempts: 1,
      baseDelayMs: 0,
      sleep: async () => {},
      onRetry: () => {},
    },
  })

  assert.equal(report.status, 'partial')
  assert.equal(report.keywordMetrics.length, config.keywords.length)
  assert.ok(report.keywordMetrics.every(item => item.searchVolume === 10))
  assert.equal(report.serp.length, config.keywords.length)
  assert.equal(report.errors.length, config.keywords.length)
  assert.equal(report.costs.totalUsd, 0.03)
})

test('SERP mode still aborts immediately for account-wide fatal failures', async () => {
  const config = validateConfig(rawConfig)
  let calls = 0
  const client = {
    async post() {
      calls += 1
      throw new DataForSeoApiError('authentication failed', {
        endpoint: DATAFORSEO_ENDPOINTS.organicSerp,
        statusCode: 40100,
        fatal: true,
      })
    },
  }

  await assert.rejects(
    createDataForSeoReport({
      client,
      config,
      mode: 'serp',
      retryOptions: {
        maxAttempts: 3,
        baseDelayMs: 0,
        sleep: async () => {},
        onRetry: () => {},
      },
    }),
    /authentication failed/,
  )
  assert.equal(calls, 1)
})
