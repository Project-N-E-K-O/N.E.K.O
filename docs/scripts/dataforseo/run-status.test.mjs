import assert from 'node:assert/strict'
import test from 'node:test'

import { buildRunStatus } from './run-status.mjs'

const successfulReport = {
  plan: { requests: { total: 8 }, includeAiOverview: true },
  costs: { totalUsd: 0.072 },
  keywordMetrics: [{ keyword: 'AI 桌面助手', searchVolume: 10 }],
  serp: [
    {
      organicRank: 4,
      aiOverviewCitedTarget: false,
      checkUrl: 'https://www.google.com/search?q=ai+desktop+pet',
      capturedAt: '2026-07-28 00:00:00 +00:00',
      error: null,
    },
    {
      organicRank: null,
      aiOverviewCitedTarget: true,
      checkUrl: 'https://www.google.com/search?q=ai+desktop+companion',
      capturedAt: '2026-07-28 00:00:01 +00:00',
      error: null,
    },
  ],
}

test('dry-run and auth-check never masquerade as ranking baselines', () => {
  const auth = buildRunStatus({
    mode: 'auth-check',
    credentialsOutcome: 'success',
    authOutcome: 'success',
  })
  const dryRun = buildRunStatus({
    mode: 'dry-run',
    dryRunOutcome: 'success',
    report: { dryRun: true, plan: { requests: { total: 10 } }, costs: null },
  })

  assert.equal(auth.runStatus, 'authenticated')
  assert.equal(auth.rankingStatus, 'not_run')
  assert.equal(dryRun.runStatus, 'planned')
  assert.equal(dryRun.rankingStatus, 'not_run')
  assert.equal(dryRun.summary.reportedCostUsd, null)
})

test('successful SERP collection records coverage, AIO, segment, and cost', () => {
  const status = buildRunStatus({
    mode: 'serp',
    segment: 'cn',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: true,
    report: successfulReport,
  })

  assert.equal(status.segment, 'cn')
  assert.equal(status.runStatus, 'complete')
  assert.equal(status.rankingStatus, 'complete')
  assert.equal(status.keywordMetricsStatus, 'not_run')
  assert.equal(status.aiOverviewStatus, 'complete')
  assert.equal(status.summary.topTenCount, 1)
  assert.equal(status.summary.aiOverviewCitationCount, 1)
  assert.equal(status.summary.reportedCostUsd, 0.072)
})

test('missing expected report is a hard failure, while unrequested AIO remains null', () => {
  const missing = buildRunStatus({
    mode: 'serp',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: true,
  })
  const noAio = buildRunStatus({
    mode: 'serp',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: false,
    report: successfulReport,
  })

  assert.equal(missing.runStatus, 'failed')
  assert.match(missing.failureReason, /expected JSON report is missing/)
  assert.equal(noAio.aiOverviewStatus, 'not_run')
  assert.equal(noAio.summary.aiOverviewCitationCount, null)
})

test('partial DataForSEO artifacts remain partial instead of becoming false complete runs', () => {
  const status = buildRunStatus({
    mode: 'serp',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: true,
    report: { ...successfulReport, status: 'partial' },
  })

  assert.equal(status.runStatus, 'partial')
  assert.equal(status.rankingStatus, 'partial')
  assert.equal(status.aiOverviewStatus, 'partial')
  assert.equal(status.failureReason, null)
})

test('missing or empty paid evidence fails closed instead of becoming complete', () => {
  const missingMetrics = buildRunStatus({
    mode: 'all',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: true,
    report: { ...successfulReport, keywordMetrics: [] },
  })
  const missingRanks = buildRunStatus({
    mode: 'all',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: true,
    report: { ...successfulReport, serp: [] },
  })

  assert.equal(missingMetrics.runStatus, 'failed')
  assert.equal(missingMetrics.keywordMetricsStatus, 'failed')
  assert.match(missingMetrics.failureReason, /no keyword metric rows/u)
  assert.equal(missingRanks.runStatus, 'failed')
  assert.equal(missingRanks.rankingStatus, 'failed')
  assert.match(missingRanks.failureReason, /no ranking rows/u)
})

test('placeholder SERP rows cannot become a complete paid baseline', () => {
  const status = buildRunStatus({
    mode: 'serp',
    credentialsOutcome: 'success',
    paidOutcome: 'success',
    includeAiOverview: true,
    report: {
      ...successfulReport,
      serp: [{
        keyword: 'ai desktop pet',
        organicRank: null,
        aiOverviewTriggered: false,
        aiOverviewCitedTarget: false,
        checkUrl: null,
        capturedAt: null,
        error: null,
      }],
    },
  })

  assert.equal(status.runStatus, 'failed')
  assert.equal(status.rankingStatus, 'failed')
  assert.equal(status.aiOverviewStatus, 'failed')
  assert.equal(status.summary.trackedKeywordCount, 0)
  assert.match(status.failureReason, /without captured SERP evidence/u)
})
