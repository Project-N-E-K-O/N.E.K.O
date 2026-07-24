#!/usr/bin/env node

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

import { DataForSeoClient } from './client.mjs'
import { createDataForSeoReport, validateConfig } from './report.mjs'

const DEFAULT_CONFIG = 'seo/dataforseo.config.json'
const DEFAULT_OUTPUT = '.seo-reports/dataforseo-report.json'

function usage() {
  return `Usage: npm run seo:dataforseo -- [options]

Options:
  --mode <all|keywords|serp>  Select paid API groups (default: all)
  --config <path>             JSON config path (default: ${DEFAULT_CONFIG})
  --output <path>             JSON report path (default: ${DEFAULT_OUTPUT})
  --depth <1-100>             Override SERP depth; every 10 results may add cost
  --include-ai-overview       Load asynchronous AI Overview data (extra charge)
  --dry-run                   Validate config and write a request plan without API calls
  --help                      Show this help

Credentials are read from DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD.
`
}

function valueAfter(argv, index, name) {
  const value = argv[index + 1]
  if (!value || value.startsWith('--')) throw new TypeError(`${name} requires a value`)
  return value
}

function parseArgs(argv) {
  const options = {
    mode: 'all',
    config: DEFAULT_CONFIG,
    output: DEFAULT_OUTPUT,
    depth: undefined,
    includeAiOverview: false,
    dryRun: false,
    help: false,
  }

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index]
    if (argument === '--help') options.help = true
    else if (argument === '--dry-run') options.dryRun = true
    else if (argument === '--include-ai-overview') options.includeAiOverview = true
    else if (argument === '--mode') options.mode = valueAfter(argv, index++, '--mode')
    else if (argument === '--config') options.config = valueAfter(argv, index++, '--config')
    else if (argument === '--output') options.output = valueAfter(argv, index++, '--output')
    else if (argument === '--depth') options.depth = valueAfter(argv, index++, '--depth')
    else if (argument.startsWith('--mode=')) options.mode = argument.slice('--mode='.length)
    else if (argument.startsWith('--config=')) options.config = argument.slice('--config='.length)
    else if (argument.startsWith('--output=')) options.output = argument.slice('--output='.length)
    else if (argument.startsWith('--depth=')) options.depth = argument.slice('--depth='.length)
    else throw new TypeError(`Unknown argument: ${argument}`)
  }

  return options
}

async function loadConfig(configPath) {
  const absolutePath = resolve(configPath)
  let source
  try {
    source = await readFile(absolutePath, 'utf8')
  } catch (error) {
    throw new Error(`Cannot read DataForSEO config ${absolutePath}: ${error.message}`)
  }

  try {
    return validateConfig(JSON.parse(source))
  } catch (error) {
    throw new Error(`Invalid DataForSEO config ${absolutePath}: ${error.message}`)
  }
}

function createClient(options) {
  if (options.dryRun) return null
  const login = process.env.DATAFORSEO_LOGIN
  const password = process.env.DATAFORSEO_PASSWORD
  if (!login || !password) {
    throw new Error(
      'Missing DataForSEO credentials. Set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD, or use --dry-run.',
    )
  }
  return new DataForSeoClient({ login, password })
}

function printSummary(report, outputPath) {
  console.log(`DataForSEO report written to ${outputPath}`)
  console.log(`Report status: ${report.status}`)
  console.log(`Planned API requests: ${report.plan.requests.total}`)
  console.log(`Maximum SERP pages: ${report.plan.maximumSerpPages}`)
  if (report.dryRun) {
    console.log('Dry run completed; no DataForSEO request was sent and no account balance was used.')
    return
  }
  console.log(`Reported API cost: $${report.costs.totalUsd.toFixed(4)}`)
  if (report.serp) {
    const successfulSerp = report.serp.filter(item => item.error == null)
    const topTen = successfulSerp.filter(
      item => item.organicRank != null && item.organicRank <= 10,
    ).length
    const aioCitations = successfulSerp.filter(item => item.aiOverviewCitedTarget).length
    console.log(`SERP keyword requests completed: ${successfulSerp.length}/${report.serp.length}`)
    console.log(
      `Tracked keywords in Google Top 10: ${topTen}/${report.serp.length} tracked `
      + `(${successfulSerp.length} observed)`,
    )
    console.log(
      `AI Overview citations of target domain: ${aioCitations}/${report.serp.length} tracked `
      + `(${successfulSerp.length} observed)`,
    )
  }
  if (report.errors.length > 0) {
    console.warn(
      `DataForSEO report retained ${report.errors.length} keyword error(s); see the report artifact for details.`,
    )
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2))
  if (options.help) {
    console.log(usage())
    return
  }

  const config = await loadConfig(options.config)
  if (options.includeAiOverview && !options.dryRun) {
    console.warn('AI Overview asynchronous loading is enabled and can add a charge per SERP request.')
  }

  const report = await createDataForSeoReport({
    client: createClient(options),
    config,
    mode: options.mode,
    includeAiOverview: options.includeAiOverview,
    depth: options.depth,
    dryRun: options.dryRun,
  })

  const outputPath = resolve(options.output)
  await mkdir(dirname(outputPath), { recursive: true })
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8')
  printSummary(report, outputPath)
  if (report.status === 'failed') process.exitCode = 1
}

main().catch(error => {
  console.error(`DataForSEO report failed: ${error.message}`)
  process.exitCode = 1
})
