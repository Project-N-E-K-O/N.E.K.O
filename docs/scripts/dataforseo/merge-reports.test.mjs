import assert from 'node:assert/strict'
import test from 'node:test'

import { mergeSplitCollection } from './merge-reports.mjs'

const target = {
  domain: 'project-neko.online',
  locationCode: 2156,
  locale: 'zh-CN',
  serpLanguageCode: 'zh-CN',
  volumeLanguageCode: null,
  keywordDifficultyLanguageCode: null,
  device: 'desktop',
}

function metricsReport(overrides = {}) {
  return {
    generatedAt: '2026-08-03T00:00:00.000Z',
    dryRun: false,
    status: 'complete',
    target,
    plan: {
      mode: 'keywords',
      keywordCount: 1,
      serpDepth: 100,
      includeAiOverview: false,
      includeKeywordDifficulty: false,
      requests: { searchVolume: 1, keywordDifficulty: 0, organicSerp: 0, total: 1 },
      maximumSerpPages: 0,
      asynchronousAiOverviewRequests: 0,
    },
    keywordMetrics: [{ keyword: '喵可智能', searchVolume: 10, keywordDifficulty: null }],
    serp: null,
    costs: {
      searchVolumeUsd: 0.01,
      keywordDifficultyUsd: 0,
      organicSerpUsd: 0,
      totalUsd: 0.01,
    },
    errors: [],
    ...overrides,
  }
}

function rankingReport(overrides = {}) {
  return {
    generatedAt: '2026-08-03T00:00:01.000Z',
    dryRun: false,
    status: 'complete',
    target,
    plan: {
      mode: 'serp',
      keywordCount: 1,
      serpDepth: 100,
      includeAiOverview: true,
      includeKeywordDifficulty: false,
      requests: { searchVolume: 0, keywordDifficulty: 0, organicSerp: 1, total: 1 },
      maximumSerpPages: 10,
      asynchronousAiOverviewRequests: 1,
    },
    keywordMetrics: null,
    serp: [{
      keyword: '喵可智能',
      organicRank: 1,
      aiOverviewCitedTarget: false,
      checkUrl: 'https://www.google.com/search?q=%E5%96%B5%E5%8F%AF%E6%99%BA%E8%83%BD',
      capturedAt: '2026-08-03 00:00:01 +00:00',
      error: null,
    }],
    costs: {
      searchVolumeUsd: 0,
      keywordDifficultyUsd: 0,
      organicSerpUsd: 0.02,
      totalUsd: 0.02,
    },
    errors: [],
    ...overrides,
  }
}

function merge(overrides = {}) {
  return mergeSplitCollection({
    collectionKind: 'paid',
    credentialsOutcome: 'success',
    keywordMetricsOutcome: 'success',
    rankingOutcome: 'success',
    includeAiOverview: true,
    keywordMetricsReport: metricsReport(),
    rankingReport: rankingReport(),
    generatedAt: '2026-08-03T00:00:02.000Z',
    segment: 'online-zh',
    ...overrides,
  })
}

test('complete independent tasks merge into the existing all-mode report contract', () => {
  const { report, execution } = merge()

  assert.equal(report.status, 'complete')
  assert.equal(report.plan.mode, 'all')
  assert.deepEqual(report.plan.requests, {
    searchVolume: 1,
    keywordDifficulty: 0,
    organicSerp: 1,
    total: 2,
  })
  assert.equal(report.keywordMetrics[0].searchVolume, 10)
  assert.equal(report.serp[0].organicRank, 1)
  assert.equal(report.costs.totalUsd, 0.03)
  assert.equal(execution.runStatus, 'complete')
  assert.equal(execution.rankingStatus, 'complete')
  assert.equal(execution.keywordMetricsStatus, 'complete')
  assert.equal(execution.aiOverviewStatus, 'complete')
})

test('a metrics failure cannot erase successful ranking evidence', () => {
  const { report, execution } = merge({
    keywordMetricsOutcome: 'failure',
    keywordMetricsReport: null,
  })

  assert.equal(report.status, 'partial')
  assert.equal(report.keywordMetrics, null)
  assert.equal(report.serp[0].organicRank, 1)
  assert.equal(report.costs.totalUsd, null)
  assert.equal(report.costs.knownTotalUsd, 0.02)
  assert.equal(execution.runStatus, 'partial')
  assert.equal(execution.rankingStatus, 'complete')
  assert.equal(execution.keywordMetricsStatus, 'failed')
  assert.equal(execution.summary.topTenCount, 1)
  assert.match(execution.failureReason, /keyword metrics/u)
})

test('a ranking failure cannot erase successful Volume and KD evidence', () => {
  const { report, execution } = merge({
    rankingOutcome: 'failure',
    rankingReport: null,
  })

  assert.equal(report.status, 'partial')
  assert.equal(report.keywordMetrics[0].searchVolume, 10)
  assert.equal(report.serp, null)
  assert.equal(report.costs.totalUsd, null)
  assert.equal(report.costs.knownTotalUsd, 0.01)
  assert.equal(execution.runStatus, 'partial')
  assert.equal(execution.rankingStatus, 'failed')
  assert.equal(execution.keywordMetricsStatus, 'complete')
  assert.match(execution.failureReason, /ranking/u)
})

test('two independent dry-run plans merge without claiming observed data', () => {
  const metrics = metricsReport({ dryRun: true, status: 'planned', keywordMetrics: null, costs: null })
  const ranking = rankingReport({ dryRun: true, status: 'planned', serp: null, costs: null })
  const { report, execution } = merge({
    collectionKind: 'dry-run',
    credentialsOutcome: 'skipped',
    keywordMetricsReport: metrics,
    rankingReport: ranking,
  })

  assert.equal(report.status, 'planned')
  assert.equal(report.dryRun, true)
  assert.equal(report.costs, null)
  assert.equal(execution.runStatus, 'planned')
  assert.equal(execution.mode, 'dry-run')
  assert.equal(execution.rankingStatus, 'not_run')
  assert.equal(execution.keywordMetricsStatus, 'not_run')
  assert.equal(execution.aiOverviewStatus, 'not_run')
})

test('mismatched task targets fail closed instead of merging unrelated evidence', () => {
  const otherTarget = { ...target, domain: 'project-neko.cn' }
  const { report, execution } = merge({
    rankingReport: rankingReport({ target: otherTarget }),
  })

  assert.equal(report.status, 'failed')
  assert.equal(execution.runStatus, 'failed')
  assert.match(execution.failureReason, /different domain\/location\/language/u)
  assert.equal(report.errors.some(error => error.phase === 'merge'), true)
})
