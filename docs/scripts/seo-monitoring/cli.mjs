#!/usr/bin/env node

import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { basename, dirname, join, resolve } from 'node:path'

import {
  collectGa4,
  collectGsc,
  collectSitemap,
  GA4_SCOPE,
  GSC_SCOPE,
  reportingWindow,
} from './collectors.mjs'
import { getGoogleAccessToken } from './google-auth.mjs'
import {
  buildMonitoringReport,
  buildPeriodReview,
  renderMarkdown,
  renderPeriodMarkdown,
  safely,
  unavailable,
} from './report.mjs'

function arg(name, fallback) {
  const exact = process.argv.indexOf(name)
  if (exact >= 0) {
    const value = process.argv[exact + 1]
    if (!value || value.startsWith('--')) throw new TypeError(`${name} requires a value`)
    return value
  }
  return process.argv.find(value => value.startsWith(`${name}=`))?.slice(name.length + 1)
    ?? fallback
}

async function readJson(path) {
  return JSON.parse(await readFile(path, 'utf8'))
}

async function readOptionalJson(path) {
  try {
    return await readJson(path)
  } catch (error) {
    if (error?.code === 'ENOENT') return null
    console.warn(`Ignoring unreadable cached SEO state ${basename(path)}: ${error.message}`)
    return null
  }
}

function localDate(iso, timezone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(new Date(iso)).map(part => [part.type, part.value]),
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}

function successfulSerp(item) {
  return item?.error == null
}

function mergeSerpBaseline(current, previous) {
  if (!Array.isArray(current?.serp)) return previous
  const merged = new Map(
    (previous?.serp ?? []).filter(successfulSerp)
      .map(item => [String(item.keyword).trim().toLocaleLowerCase('en-US'), item]),
  )
  for (const item of current.serp.filter(successfulSerp)) {
    merged.set(String(item.keyword).trim().toLocaleLowerCase('en-US'), {
      ...item,
      rankObservedAt: current.generatedAt,
    })
  }
  if (merged.size === 0) return previous
  return {
    schemaVersion: 1,
    generatedAt: current.generatedAt,
    plan: current.plan,
    serp: [...merged.values()],
  }
}

async function readDailyReports(dailyDirectory) {
  let names
  try {
    names = await readdir(dailyDirectory)
  } catch (error) {
    if (error?.code === 'ENOENT') return []
    throw error
  }
  const reports = []
  for (const name of names.filter(value => value.endsWith('.json'))) {
    try {
      reports.push(await readJson(join(dailyDirectory, name)))
    } catch (error) {
      console.warn(`Skipping unreadable SEO history file ${name}: ${error.message}`)
    }
  }
  return reports
}

const configPath = resolve(arg('--config', 'seo/monitoring.config.json'))
const config = await readJson(configPath)
const dataForSeoConfigPath = resolve(arg(
  '--dataforseo-config',
  config.dataForSeoConfigPath ?? 'seo/dataforseo.config.json',
))
const dataForSeoConfig = await readJson(dataForSeoConfigPath)
const dataForSeoPath = resolve(arg(
  '--dataforseo',
  process.env.DATAFORSEO_REPORT_PATH ?? '.seo-reports/dataforseo-report.json',
))
const outputJson = resolve(arg('--output-json', '.seo-reports/seo-monitoring.json'))
const outputMarkdown = resolve(arg('--output-markdown', '.seo-reports/seo-monitoring.md'))
const stateDirectory = resolve(arg('--state-dir', '.seo-history'))
const dailyDirectory = join(stateDirectory, 'daily')
const generatedAt = new Date().toISOString()
const window = reportingWindow()

const dataForSeoReport = await safely(() => readJson(dataForSeoPath))
const previousReport = await readOptionalJson(join(stateDirectory, 'latest.json'))
const metricState = await readOptionalJson(join(stateDirectory, 'keyword-metrics.json'))
const persistedBaseline = await readOptionalJson(join(stateDirectory, 'serp-baseline.json'))
const sitemap = await safely(() => collectSitemap(config.site.sitemapUrl))

let accessToken = null
let googleAuthError = 'GOOGLE_SERVICE_ACCOUNT_JSON is not configured'
if (process.env.GOOGLE_SERVICE_ACCOUNT_JSON) {
  try {
    accessToken = await getGoogleAccessToken({
      serviceAccount: process.env.GOOGLE_SERVICE_ACCOUNT_JSON,
      scopes: [GSC_SCOPE, GA4_SCOPE],
    })
    googleAuthError = null
  } catch (error) {
    googleAuthError = error.message
  }
}

const siteUrl = process.env[config.gsc.siteUrlEnv] || config.gsc.defaultSiteUrl
const gsc = accessToken
  ? await safely(() => collectGsc({
    siteUrl,
    sitemapUrl: config.site.sitemapUrl,
    categoryQueryRegex: config.gsc.categoryQueryRegex,
    opportunities: config.gsc.opportunities,
  }, window, { accessToken }))
  : unavailable(googleAuthError)

const propertyId = process.env[config.ga4.propertyIdEnv]
const ga4 = accessToken && propertyId
  ? await safely(() => collectGa4({
    propertyId,
    hostname: config.site.hostname,
    aiReferralRegex: config.ga4.aiReferralRegex,
    ctaEvent: config.ga4.ctaEvent,
  }, window, { accessToken }))
  : unavailable(googleAuthError ?? `${config.ga4.propertyIdEnv} is not configured`)

const report = buildMonitoringReport({
  config,
  generatedAt,
  window,
  dataForSeoReport,
  dataForSeoMetricReport: metricState,
  dataForSeoBaselineReport: persistedBaseline,
  trackedKeywords: dataForSeoConfig.keywords,
  previousReport,
  sitemap,
  gsc,
  ga4,
})

await mkdir(dirname(outputJson), { recursive: true })
await mkdir(dirname(outputMarkdown), { recursive: true })
await mkdir(dailyDirectory, { recursive: true })
await writeFile(outputJson, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
await writeFile(outputMarkdown, renderMarkdown(report), 'utf8')
await writeFile(join(stateDirectory, 'latest.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
await writeFile(
  join(dailyDirectory, `${localDate(generatedAt, config.timezone)}.json`),
  `${JSON.stringify(report, null, 2)}\n`,
  'utf8',
)

if (Array.isArray(dataForSeoReport.keywordMetrics) && dataForSeoReport.keywordMetrics.length > 0) {
  await writeFile(
    join(stateDirectory, 'keyword-metrics.json'),
    `${JSON.stringify({
      schemaVersion: 1,
      generatedAt: dataForSeoReport.generatedAt ?? generatedAt,
      keywordMetrics: dataForSeoReport.keywordMetrics,
    }, null, 2)}\n`,
    'utf8',
  )
}

const updatedBaseline = mergeSerpBaseline(dataForSeoReport, persistedBaseline)
if (updatedBaseline && updatedBaseline !== persistedBaseline) {
  await writeFile(
    join(stateDirectory, 'serp-baseline.json'),
    `${JSON.stringify(updatedBaseline, null, 2)}\n`,
    'utf8',
  )
}

const history = await readDailyReports(dailyDirectory)
for (const period of ['weekly', 'monthly']) {
  const review = buildPeriodReview({ period, reports: history, generatedAt, timezone: config.timezone })
  const jsonPath = join(dirname(outputJson), `seo-monitoring-${period}.json`)
  const markdownPath = join(dirname(outputMarkdown), `seo-monitoring-${period}.md`)
  await writeFile(jsonPath, `${JSON.stringify(review, null, 2)}\n`, 'utf8')
  await writeFile(markdownPath, renderPeriodMarkdown(review), 'utf8')
  await mkdir(join(stateDirectory, period), { recursive: true })
  await writeFile(
    join(stateDirectory, period, 'latest.json'),
    `${JSON.stringify(review, null, 2)}\n`,
    'utf8',
  )
}

console.log(`SEO/GEO JSON report written to ${outputJson}`)
console.log(`SEO/GEO Markdown report written to ${outputMarkdown}`)
console.log(`Rolling weekly and monthly reviews written beside ${basename(outputJson)}`)
console.log(`Collection blockers: ${report.blockers.length}`)
