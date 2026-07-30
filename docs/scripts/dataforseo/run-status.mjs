#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const MODES = new Set(['dry-run', 'auth-check', 'keywords', 'serp', 'all'])

function normalizedOutcome(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : 'unknown'
}

function numericOrNull(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function booleanValue(value) {
  if (value === true || value === 'true') return true
  if (value === false || value === 'false' || value == null) return false
  throw new TypeError(`Expected a boolean value, received: ${value}`)
}

function hasCapturedSerpEvidence(item) {
  if (!item || item.error != null) return false
  return [item.checkUrl, item.capturedAt]
    .some(value => typeof value === 'string' && value.trim().length > 0)
}

function reportSummary(report, aiOverviewRequested) {
  const serp = Array.isArray(report?.serp) ? report.serp : null
  const observedSerp = serp?.filter(hasCapturedSerpEvidence) ?? null
  return {
    apiRequestCount: numericOrNull(report?.plan?.requests?.total),
    trackedKeywordCount: observedSerp?.length ?? null,
    topTenCount: observedSerp?.filter(item => item?.organicRank != null && item.organicRank <= 10).length ?? null,
    aiOverviewCitationCount:
      aiOverviewRequested && observedSerp
        ? observedSerp.filter(item => item?.aiOverviewCitedTarget === true).length
        : null,
    reportedCostUsd: numericOrNull(report?.costs?.totalUsd),
  }
}

export function buildRunStatus({
  mode,
  credentialsOutcome,
  authOutcome,
  dryRunOutcome,
  paidOutcome,
  includeAiOverview = false,
  report = null,
  reportReadError = null,
  generatedAt = new Date().toISOString(),
  segment = null,
}) {
  if (!MODES.has(mode)) throw new TypeError(`Unsupported report mode: ${mode}`)

  const outcomes = {
    credentials: normalizedOutcome(credentialsOutcome),
    authCheck: normalizedOutcome(authOutcome),
    dryRun: normalizedOutcome(dryRunOutcome),
    paidReport: normalizedOutcome(paidOutcome),
  }
  const selectedStep = mode === 'dry-run' ? 'dryRun' : mode === 'auth-check' ? 'authCheck' : 'paidReport'
  const expectsReport = mode !== 'auth-check'
  const expectsRanking = mode === 'serp' || mode === 'all'
  const expectsKeywordMetrics = mode === 'keywords' || mode === 'all'
  const expectsAiOverview = expectsRanking && booleanValue(includeAiOverview)

  let failureReason = null
  if (mode !== 'dry-run' && outcomes.credentials !== 'success') {
    failureReason = `Credential validation ${outcomes.credentials}; paid collection did not complete.`
  } else if (outcomes[selectedStep] !== 'success') {
    failureReason = `${selectedStep} step ${outcomes[selectedStep]}; requested collection did not complete.`
  } else if (reportReadError) {
    failureReason = `The generated report could not be read: ${reportReadError}`
  } else if (expectsReport && !report) {
    failureReason = 'The collection step succeeded but the expected JSON report is missing.'
  } else if (report?.status === 'failed') {
    failureReason = 'The generated DataForSEO report has failed status.'
  } else if (expectsRanking && (!Array.isArray(report?.serp) || report.serp.length === 0)) {
    failureReason = 'The generated DataForSEO report has no ranking rows.'
  } else if (
    expectsRanking
    && report?.status !== 'partial'
    && report.serp.some(item => !hasCapturedSerpEvidence(item))
  ) {
    failureReason = 'The generated DataForSEO report contains ranking rows without captured SERP evidence.'
  } else if (expectsKeywordMetrics && (!Array.isArray(report?.keywordMetrics) || report.keywordMetrics.length === 0)) {
    failureReason = 'The generated DataForSEO report has no keyword metric rows.'
  }

  const failed = failureReason !== null
  const partial = !failed && report?.status === 'partial'
  const runStatus = failed
    ? 'failed'
    : partial
      ? 'partial'
    : mode === 'auth-check'
      ? 'authenticated'
      : mode === 'dry-run'
        ? 'planned'
        : 'complete'

  return {
    schemaVersion: 1,
    generatedAt,
    segment,
    mode,
    runStatus,
    rankingStatus: expectsRanking ? (failed ? 'failed' : partial ? 'partial' : 'complete') : 'not_run',
    keywordMetricsStatus: expectsKeywordMetrics
      ? failed
        ? 'failed'
        : partial
          ? 'partial'
          : Array.isArray(report?.keywordMetrics) && report.keywordMetrics.length > 0
            ? 'complete'
            : 'unknown'
      : 'not_run',
    aiOverviewStatus: expectsAiOverview ? (failed ? 'failed' : partial ? 'partial' : 'complete') : 'not_run',
    dataReportPresent: Boolean(report),
    selectedStep,
    selectedStepOutcome: outcomes[selectedStep],
    stepOutcomes: outcomes,
    failureReason,
    summary: reportSummary(report, expectsAiOverview),
    github: {
      runId: process.env.GITHUB_RUN_ID ?? null,
      runAttempt: process.env.GITHUB_RUN_ATTEMPT ?? null,
      eventName: process.env.GITHUB_EVENT_NAME ?? null,
      repository: process.env.GITHUB_REPOSITORY ?? null,
      ref: process.env.GITHUB_REF ?? null,
      sha: process.env.GITHUB_SHA ?? null,
    },
  }
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
    if (argument === '--mode') options.mode = valueAfter(argv, index++, '--mode')
    else if (argument === '--segment') options.segment = valueAfter(argv, index++, '--segment')
    else if (argument === '--credentials-outcome') {
      options.credentialsOutcome = valueAfter(argv, index++, '--credentials-outcome')
    } else if (argument === '--auth-outcome') {
      options.authOutcome = valueAfter(argv, index++, '--auth-outcome')
    } else if (argument === '--dry-run-outcome') {
      options.dryRunOutcome = valueAfter(argv, index++, '--dry-run-outcome')
    } else if (argument === '--paid-outcome') {
      options.paidOutcome = valueAfter(argv, index++, '--paid-outcome')
    } else if (argument === '--include-ai-overview') {
      options.includeAiOverview = valueAfter(argv, index++, '--include-ai-overview')
    } else if (argument === '--report') options.report = valueAfter(argv, index++, '--report')
    else if (argument === '--output') options.output = valueAfter(argv, index++, '--output')
    else throw new TypeError(`Unknown argument: ${argument}`)
  }
  if (!options.mode || !options.report || !options.output) {
    throw new TypeError('--mode, --report and --output are required')
  }
  return options
}

async function loadOptionalReport(reportPath) {
  try {
    return { report: JSON.parse(await readFile(resolve(reportPath), 'utf8')), reportReadError: null }
  } catch (error) {
    if (error?.code === 'ENOENT') return { report: null, reportReadError: null }
    return { report: null, reportReadError: error.message }
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const loaded = await loadOptionalReport(options.report)
  const status = buildRunStatus({ ...options, ...loaded })
  const outputPath = resolve(options.output)
  await mkdir(dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(status, null, 2)}\n`, 'utf8')
  console.log(`DataForSEO execution status written to ${outputPath}`)
  console.log(
    `Run status: ${status.runStatus}; ranking: ${status.rankingStatus}; keyword metrics: ${status.keywordMetricsStatus}; AI Overview: ${status.aiOverviewStatus}`,
  )
  if (status.failureReason) {
    console.error(status.failureReason)
    process.exitCode = 1
  }
}

const isDirectRun = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href
if (isDirectRun) {
  main().catch(error => {
    console.error(`Cannot record DataForSEO execution status: ${error.message}`)
    process.exitCode = 1
  })
}
