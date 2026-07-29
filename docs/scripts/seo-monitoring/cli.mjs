#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

import {
  collectGa4,
  collectGsc,
  collectTechnicalSeo,
  GA4_SCOPE,
  GSC_SCOPE,
  reportingWindow,
} from './collectors.mjs'
import { getGoogleAccessToken } from './google-auth.mjs'
import {
  buildMonitoringReport,
  notRun,
  renderMarkdown,
  safely,
  unavailable,
} from './report.mjs'

function valueAfter(argv, index, name) {
  const value = argv[index + 1]
  if (!value || value.startsWith('--')) throw new TypeError(`${name} requires a value`)
  return value
}

function assignment(value, name) {
  const separator = value.indexOf('=')
  if (separator <= 0 || separator === value.length - 1) {
    throw new TypeError(`${name} requires ID=PATH`)
  }
  return [value.slice(0, separator), value.slice(separator + 1)]
}

function parseArgs(argv) {
  const options = {
    config: 'seo/monitoring.config.json',
    outputJson: '.seo-reports/seo-monitoring.json',
    outputMarkdown: '.seo-reports/seo-monitoring.md',
    dataForSeo: new Map(),
    dataForSeoStatus: new Map(),
    indexNow: new Map(),
    previousReport: null,
    requireComplete: false,
  }
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--config') options.config = valueAfter(argv, index++, argument)
    else if (argument === '--output-json') options.outputJson = valueAfter(argv, index++, argument)
    else if (argument === '--output-markdown') options.outputMarkdown = valueAfter(argv, index++, argument)
    else if (argument === '--dataforseo') {
      const [id, path] = assignment(valueAfter(argv, index++, argument), argument)
      options.dataForSeo.set(id, path)
    } else if (argument === '--dataforseo-status') {
      const [id, path] = assignment(valueAfter(argv, index++, argument), argument)
      options.dataForSeoStatus.set(id, path)
    } else if (argument === '--indexnow') {
      const [id, path] = assignment(valueAfter(argv, index++, argument), argument)
      options.indexNow.set(id, path)
    } else if (argument === '--previous-report') {
      options.previousReport = valueAfter(argv, index++, argument)
    } else if (argument === '--require-complete') options.requireComplete = true
    else throw new TypeError(`Unknown argument: ${argument}`)
  }
  return options
}

async function readJson(path) {
  return JSON.parse(await readFile(resolve(path), 'utf8'))
}

async function readOptionalJson(path, missingReason) {
  if (!path) return unavailable(missingReason)
  try {
    return await readJson(path)
  } catch (error) {
    if (error?.code === 'ENOENT') return unavailable(`${missingReason}; file not found: ${path}`)
    return unavailable(`${missingReason}; ${error.message}`)
  }
}

function configuredPath(assignments, definition, envName, fallback) {
  return assignments.get(definition.id) || process.env[envName] || fallback
}

function duplicatePropertyIds(config) {
  const byId = new Map()
  for (const site of config.sites) {
    const value = process.env[site.ga4.propertyIdEnv]
    if (!value) continue
    const sites = byId.get(value) ?? []
    sites.push(site.id)
    byId.set(value, sites)
  }
  return new Map([...byId].filter(([, sites]) => sites.length > 1))
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  const config = await readJson(options.config)
  if (config.schemaVersion !== 2 || !Array.isArray(config.sites) || !Array.isArray(config.dataForSeoSegments)) {
    throw new TypeError('monitoring config must use schemaVersion 2 with sites and dataForSeoSegments')
  }
  const window = reportingWindow(new Date(), config.timezone)
  const previousReport = options.previousReport
    ? await readOptionalJson(options.previousReport, 'Previous unified report is not available')
    : null

  const definitions = await Promise.all(config.dataForSeoSegments.map(async definition => ({
    ...definition,
    keywordConfig: await readJson(definition.configPath),
  })))
  const dataForSeoInputs = await Promise.all(definitions.map(async definition => {
    const reportPath = configuredPath(
      options.dataForSeo,
      definition,
      definition.reportPathEnv,
      `.seo-reports/dataforseo-${definition.id}.json`,
    )
    const statusPath = configuredPath(
      options.dataForSeoStatus,
      definition,
      definition.statusPathEnv,
      `.seo-reports/dataforseo-${definition.id}-execution.json`,
    )
    const [report, execution] = await Promise.all([
      readOptionalJson(reportPath, `DataForSEO ${definition.id} report is not available`),
      readOptionalJson(statusPath, `DataForSEO ${definition.id} execution status is not available`),
    ])
    return { definition, report, execution }
  }))

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

  const duplicates = duplicatePropertyIds(config)
  const siteInputs = await Promise.all(config.sites.map(async definition => {
    const siteUrl = process.env[definition.gsc.siteUrlEnv] || definition.gsc.defaultSiteUrl
    const propertyId = process.env[definition.ga4.propertyIdEnv]
    const duplicateSites = propertyId ? duplicates.get(propertyId) : null
    const indexNowPath = options.indexNow.get(definition.id)
      || process.env[definition.indexNow.statusPathEnv]
    const [gsc, ga4, technical, indexNow] = await Promise.all([
      accessToken
        ? safely(() => collectGsc({
          siteUrl,
          sitemapUrl: definition.sitemapUrl,
          ...definition.gsc,
        }, window, { accessToken }))
        : unavailable(googleAuthError),
      accessToken && propertyId && !duplicateSites
        ? safely(async () => ({
          ...await collectGa4({
            propertyId,
            hostname: definition.hostname,
            ...definition.ga4,
          }, window, { accessToken }),
          propertyId,
        }))
        : unavailable(
          googleAuthError
          ?? (duplicateSites
            ? `${definition.ga4.propertyIdEnv} duplicates property ${propertyId} across ${duplicateSites.join(', ')}; each domain requires its own GA4 property.`
            : `${definition.ga4.propertyIdEnv} is not configured`),
        ),
      safely(() => collectTechnicalSeo(definition)),
      readOptionalJson(indexNowPath, `${definition.indexNow.statusPathEnv} is not configured`),
    ])
    return { definition, gsc, ga4, technical, indexNow: indexNow.status === 'unavailable' ? notRun(indexNow.reason) : indexNow }
  }))

  const report = buildMonitoringReport({
    config,
    generatedAt: new Date().toISOString(),
    window,
    dataForSeoInputs,
    siteInputs,
    previousReport: previousReport?.status === 'unavailable' ? null : previousReport,
    previousReportEvidence: process.env.PREVIOUS_REPORT_EVIDENCE ?? options.previousReport,
  })
  const outputJson = resolve(options.outputJson)
  const outputMarkdown = resolve(options.outputMarkdown)
  await mkdir(dirname(outputJson), { recursive: true })
  await mkdir(dirname(outputMarkdown), { recursive: true })
  await writeFile(outputJson, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
  await writeFile(outputMarkdown, renderMarkdown(report), 'utf8')

  console.log(`SEO/GEO JSON report written to ${outputJson}`)
  console.log(`SEO/GEO Markdown report written to ${outputMarkdown}`)
  console.log(`Overall status: ${report.overallStatus}; blockers: ${report.blockers.length}`)
  if (options.requireComplete && report.overallStatus !== 'complete') process.exitCode = 1
}

main().catch(error => {
  console.error(`SEO/GEO report failed: ${error.message}`)
  process.exitCode = 1
})
