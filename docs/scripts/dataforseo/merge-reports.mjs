#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

import { buildRunStatus } from './run-status.mjs'

const COLLECTION_KINDS = new Set(['dry-run', 'paid'])
const USABLE_STATUSES = new Set(['complete', 'partial'])

function numberOrNull(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function booleanValue(value) {
  if (value === true || value === 'true') return true
  if (value === false || value === 'false' || value == null) return false
  throw new TypeError(`Expected a boolean value, received: ${value}`)
}

function targetSignature(target) {
  if (!target || typeof target !== 'object') return null
  return JSON.stringify({
    domain: target.domain ?? null,
    locationCode: target.locationCode ?? null,
    locale: target.locale ?? target.languageCode ?? null,
    serpLanguageCode: target.serpLanguageCode ?? target.languageCode ?? null,
    volumeLanguageCode: Object.hasOwn(target, 'volumeLanguageCode')
      ? target.volumeLanguageCode
      : target.languageCode ?? null,
    keywordDifficultyLanguageCode: Object.hasOwn(target, 'keywordDifficultyLanguageCode')
      ? target.keywordDifficultyLanguageCode
      : target.languageCode ?? null,
    device: target.device ?? null,
  })
}

function compatibilityError(keywordMetricsReport, rankingReport) {
  if (!keywordMetricsReport || !rankingReport) return null
  if (targetSignature(keywordMetricsReport.target) !== targetSignature(rankingReport.target)) {
    return 'Keyword metrics and ranking reports target different domain/location/language settings.'
  }
  const metricsKeywordCount = numberOrNull(keywordMetricsReport.plan?.keywordCount)
  const rankingKeywordCount = numberOrNull(rankingReport.plan?.keywordCount)
  if (metricsKeywordCount !== rankingKeywordCount) {
    return 'Keyword metrics and ranking reports contain different tracked keyword counts.'
  }
  return null
}

function mergePlans(keywordMetricsReport, rankingReport) {
  const metricsRequests = keywordMetricsReport?.plan?.requests ?? {}
  const rankingRequests = rankingReport?.plan?.requests ?? {}
  const searchVolume = numberOrNull(metricsRequests.searchVolume) ?? 0
  const keywordDifficulty = numberOrNull(metricsRequests.keywordDifficulty) ?? 0
  const organicSerp = numberOrNull(rankingRequests.organicSerp) ?? 0
  return {
    mode: 'all',
    keywordCount: numberOrNull(rankingReport?.plan?.keywordCount)
      ?? numberOrNull(keywordMetricsReport?.plan?.keywordCount),
    serpDepth: numberOrNull(rankingReport?.plan?.serpDepth)
      ?? numberOrNull(keywordMetricsReport?.plan?.serpDepth),
    includeAiOverview: rankingReport?.plan?.includeAiOverview === true,
    includeKeywordDifficulty: keywordMetricsReport?.plan?.includeKeywordDifficulty === true,
    requests: {
      searchVolume,
      keywordDifficulty,
      organicSerp,
      total: searchVolume + keywordDifficulty + organicSerp,
    },
    maximumSerpPages: numberOrNull(rankingReport?.plan?.maximumSerpPages) ?? 0,
    asynchronousAiOverviewRequests:
      numberOrNull(rankingReport?.plan?.asynchronousAiOverviewRequests) ?? 0,
  }
}

function mergeCosts(collectionKind, keywordMetricsReport, rankingReport) {
  if (collectionKind === 'dry-run') return null
  const metricsCost = numberOrNull(keywordMetricsReport?.costs?.totalUsd)
  const rankingCost = numberOrNull(rankingReport?.costs?.totalUsd)
  const knownTotalUsd = (metricsCost ?? 0) + (rankingCost ?? 0)
  return {
    searchVolumeUsd: numberOrNull(keywordMetricsReport?.costs?.searchVolumeUsd),
    keywordDifficultyUsd: numberOrNull(keywordMetricsReport?.costs?.keywordDifficultyUsd),
    organicSerpUsd: numberOrNull(rankingReport?.costs?.organicSerpUsd),
    totalUsd: metricsCost != null && rankingCost != null ? knownTotalUsd : null,
    knownTotalUsd,
    complete: metricsCost != null && rankingCost != null,
  }
}

function componentError(phase, execution) {
  if (!execution?.failureReason) return null
  return {
    phase,
    message: execution.failureReason,
    status: phase === 'ranking'
      ? execution.rankingStatus
      : execution.keywordMetricsStatus,
  }
}

function combinedPaidStatus(keywordMetricsExecution, rankingExecution, incompatible) {
  if (incompatible) return 'failed'
  const metricsStatus = keywordMetricsExecution.keywordMetricsStatus
  const rankingStatus = rankingExecution.rankingStatus
  if (metricsStatus === 'complete' && rankingStatus === 'complete') return 'complete'
  if (USABLE_STATUSES.has(metricsStatus) || USABLE_STATUSES.has(rankingStatus)) return 'partial'
  return 'failed'
}

function combinedFailureReason(collectionKind, keywordMetricsExecution, rankingExecution, incompatible) {
  if (incompatible) return incompatible
  const failures = []
  if (collectionKind === 'dry-run') {
    if (keywordMetricsExecution.runStatus !== 'planned') {
      failures.push(`keyword metrics plan: ${keywordMetricsExecution.failureReason ?? keywordMetricsExecution.runStatus}`)
    }
    if (rankingExecution.runStatus !== 'planned') {
      failures.push(`ranking plan: ${rankingExecution.failureReason ?? rankingExecution.runStatus}`)
    }
  } else {
    if (keywordMetricsExecution.keywordMetricsStatus !== 'complete') {
      failures.push(`keyword metrics: ${keywordMetricsExecution.failureReason ?? keywordMetricsExecution.keywordMetricsStatus}`)
    }
    if (rankingExecution.rankingStatus !== 'complete') {
      failures.push(`ranking: ${rankingExecution.failureReason ?? rankingExecution.rankingStatus}`)
    }
  }
  return failures.length > 0 ? failures.join(' | ') : null
}

export function mergeSplitCollection({
  collectionKind,
  credentialsOutcome,
  keywordMetricsOutcome,
  rankingOutcome,
  includeAiOverview = false,
  keywordMetricsReport = null,
  keywordMetricsReportReadError = null,
  rankingReport = null,
  rankingReportReadError = null,
  generatedAt = new Date().toISOString(),
  segment = null,
}) {
  if (!COLLECTION_KINDS.has(collectionKind)) {
    throw new TypeError(`collectionKind must be one of: ${[...COLLECTION_KINDS].join(', ')}`)
  }
  const aiOverviewRequested = booleanValue(includeAiOverview)
  const dryRun = collectionKind === 'dry-run'
  const keywordMetricsExecution = buildRunStatus({
    mode: dryRun ? 'dry-run' : 'keywords',
    credentialsOutcome,
    dryRunOutcome: keywordMetricsOutcome,
    paidOutcome: keywordMetricsOutcome,
    report: keywordMetricsReport,
    reportReadError: keywordMetricsReportReadError,
    generatedAt,
    segment,
  })
  const rankingExecution = buildRunStatus({
    mode: dryRun ? 'dry-run' : 'serp',
    credentialsOutcome,
    dryRunOutcome: rankingOutcome,
    paidOutcome: rankingOutcome,
    includeAiOverview: aiOverviewRequested,
    report: rankingReport,
    reportReadError: rankingReportReadError,
    generatedAt,
    segment,
  })
  const incompatible = compatibilityError(keywordMetricsReport, rankingReport)
  const runStatus = dryRun
    ? keywordMetricsExecution.runStatus === 'planned'
      && rankingExecution.runStatus === 'planned'
      && !incompatible
      ? 'planned'
      : 'failed'
    : combinedPaidStatus(keywordMetricsExecution, rankingExecution, incompatible)
  const failureReason = combinedFailureReason(
    collectionKind,
    keywordMetricsExecution,
    rankingExecution,
    incompatible,
  )
  const plan = mergePlans(keywordMetricsReport, rankingReport)
  const costs = mergeCosts(collectionKind, keywordMetricsReport, rankingReport)
  const errors = [
    ...(keywordMetricsReport?.errors ?? []),
    ...(rankingReport?.errors ?? []),
    componentError('keyword_metrics', keywordMetricsExecution),
    componentError('ranking', rankingExecution),
    incompatible ? { phase: 'merge', message: incompatible, status: 'failed' } : null,
  ].filter(Boolean)
  const report = {
    schemaVersion: 2,
    generatedAt,
    dryRun,
    status: runStatus,
    target: rankingReport?.target ?? keywordMetricsReport?.target ?? null,
    plan,
    keywordMetrics: keywordMetricsReport?.keywordMetrics ?? null,
    serp: rankingReport?.serp ?? null,
    costs,
    errors,
    components: {
      keywordMetrics: {
        status: keywordMetricsExecution.runStatus,
        generatedAt: keywordMetricsReport?.generatedAt ?? null,
      },
      ranking: {
        status: rankingExecution.runStatus,
        generatedAt: rankingReport?.generatedAt ?? null,
      },
    },
  }
  const execution = {
    schemaVersion: 2,
    generatedAt,
    segment,
    mode: dryRun ? 'dry-run' : 'all',
    runStatus,
    rankingStatus: dryRun ? 'not_run' : rankingExecution.rankingStatus,
    keywordMetricsStatus: dryRun ? 'not_run' : keywordMetricsExecution.keywordMetricsStatus,
    aiOverviewStatus: dryRun ? 'not_run' : rankingExecution.aiOverviewStatus,
    dataReportPresent: true,
    selectedStep: 'independentTasks',
    selectedStepOutcome: runStatus,
    stepOutcomes: {
      credentials: credentialsOutcome ?? 'unknown',
      keywordMetrics: keywordMetricsOutcome ?? 'unknown',
      ranking: rankingOutcome ?? 'unknown',
    },
    failureReason,
    summary: {
      apiRequestCount: plan.requests.total,
      trackedKeywordCount: dryRun ? null : rankingExecution.summary.trackedKeywordCount,
      topTenCount: dryRun ? null : rankingExecution.summary.topTenCount,
      aiOverviewCitationCount: dryRun ? null : rankingExecution.summary.aiOverviewCitationCount,
      reportedCostUsd: costs?.totalUsd ?? null,
      knownCostUsd: costs?.knownTotalUsd ?? null,
    },
    components: {
      keywordMetrics: keywordMetricsExecution,
      ranking: rankingExecution,
    },
    github: rankingExecution.github ?? keywordMetricsExecution.github,
  }
  return { report, execution }
}

function valueAfter(argv, index, name) {
  const value = argv[index + 1]
  if (!value || value.startsWith('--')) throw new TypeError(`${name} requires a value`)
  return value
}

function parseArgs(argv) {
  const options = {}
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--segment') options.segment = valueAfter(argv, index++, '--segment')
    else if (argument === '--collection-kind') {
      options.collectionKind = valueAfter(argv, index++, '--collection-kind')
    } else if (argument === '--credentials-outcome') {
      options.credentialsOutcome = valueAfter(argv, index++, '--credentials-outcome')
    } else if (argument === '--metrics-outcome') {
      options.keywordMetricsOutcome = valueAfter(argv, index++, '--metrics-outcome')
    } else if (argument === '--ranking-outcome') {
      options.rankingOutcome = valueAfter(argv, index++, '--ranking-outcome')
    } else if (argument === '--include-ai-overview') {
      options.includeAiOverview = valueAfter(argv, index++, '--include-ai-overview')
    } else if (argument === '--metrics-report') {
      options.keywordMetricsReportPath = valueAfter(argv, index++, '--metrics-report')
    } else if (argument === '--ranking-report') {
      options.rankingReportPath = valueAfter(argv, index++, '--ranking-report')
    } else if (argument === '--output-report') {
      options.outputReport = valueAfter(argv, index++, '--output-report')
    } else if (argument === '--output-status') {
      options.outputStatus = valueAfter(argv, index++, '--output-status')
    } else throw new TypeError(`Unknown argument: ${argument}`)
  }
  const required = [
    'segment',
    'collectionKind',
    'keywordMetricsReportPath',
    'rankingReportPath',
    'outputReport',
    'outputStatus',
  ]
  const missing = required.find(field => !options[field])
  if (missing) throw new TypeError(`${missing} is required`)
  return options
}

async function loadOptionalReport(path) {
  try {
    return {
      report: JSON.parse(await readFile(resolve(path), 'utf8')),
      reportReadError: null,
    }
  } catch (error) {
    if (error?.code === 'ENOENT') return { report: null, reportReadError: null }
    return { report: null, reportReadError: error.message }
  }
}

async function writeJson(path, value) {
  const outputPath = resolve(path)
  await mkdir(dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  return outputPath
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const [keywordMetrics, ranking] = await Promise.all([
    loadOptionalReport(options.keywordMetricsReportPath),
    loadOptionalReport(options.rankingReportPath),
  ])
  const artifacts = mergeSplitCollection({
    ...options,
    keywordMetricsReport: keywordMetrics.report,
    keywordMetricsReportReadError: keywordMetrics.reportReadError,
    rankingReport: ranking.report,
    rankingReportReadError: ranking.reportReadError,
  })
  const [reportPath, statusPath] = await Promise.all([
    writeJson(options.outputReport, artifacts.report),
    writeJson(options.outputStatus, artifacts.execution),
  ])
  console.log(`Merged DataForSEO report written to ${reportPath}`)
  console.log(`Merged DataForSEO execution status written to ${statusPath}`)
  console.log(
    `Run status: ${artifacts.execution.runStatus}; ranking: ${artifacts.execution.rankingStatus}; `
    + `keyword metrics: ${artifacts.execution.keywordMetricsStatus}`,
  )
  if (artifacts.execution.runStatus === 'failed') process.exitCode = 1
}

const isDirectRun = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
if (isDirectRun) {
  main().catch(error => {
    console.error(`Cannot merge DataForSEO reports: ${error.message}`)
    process.exitCode = 1
  })
}
