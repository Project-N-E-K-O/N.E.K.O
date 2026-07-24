#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

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
  renderMarkdown,
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

const configPath = resolve(arg('--config', 'seo/monitoring.config.json'))
const dataForSeoPath = resolve(arg(
  '--dataforseo',
  process.env.DATAFORSEO_REPORT_PATH ?? '.seo-reports/dataforseo-report.json',
))
const outputJson = resolve(arg('--output-json', '.seo-reports/seo-monitoring.json'))
const outputMarkdown = resolve(arg('--output-markdown', '.seo-reports/seo-monitoring.md'))
const config = await readJson(configPath)
const window = reportingWindow()

const dataForSeoReport = await safely(() => readJson(dataForSeoPath))
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
  generatedAt: new Date().toISOString(),
  window,
  dataForSeoReport,
  sitemap,
  gsc,
  ga4,
})

await mkdir(dirname(outputJson), { recursive: true })
await mkdir(dirname(outputMarkdown), { recursive: true })
await writeFile(outputJson, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
await writeFile(outputMarkdown, renderMarkdown(report), 'utf8')

console.log(`SEO/GEO JSON report written to ${outputJson}`)
console.log(`SEO/GEO Markdown report written to ${outputMarkdown}`)
console.log(`Collection blockers: ${report.blockers.length}`)
