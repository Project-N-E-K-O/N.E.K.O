import { DATAFORSEO_ENDPOINTS } from './client.mjs'

const MODES = new Set(['all', 'keywords', 'serp'])
const DEVICES = new Set(['desktop', 'mobile'])

function canonicalKeyword(value) {
  return value.trim().toLocaleLowerCase('en-US')
}

function normalizeTargetDomain(value) {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new TypeError('targetDomain must be a non-empty domain name')
  }
  const raw = value.trim()
  let parsed
  try {
    parsed = new URL(raw.includes('://') ? raw : `https://${raw}`)
  } catch {
    throw new TypeError(`targetDomain is not valid: ${raw}`)
  }
  if (parsed.pathname !== '/' || parsed.search || parsed.hash) {
    throw new TypeError('targetDomain must not contain a path, query, or fragment')
  }
  return parsed.hostname.toLowerCase().replace(/\.$/, '')
}

function normalizeLandingPage(value) {
  if (value == null || value === '') return null
  if (typeof value !== 'string' || !value.startsWith('/')) {
    throw new TypeError('landingPage must be an absolute site path beginning with /')
  }
  const parsed = new URL(value, 'https://docs.invalid')
  if (parsed.origin !== 'https://docs.invalid' || parsed.search || parsed.hash) {
    throw new TypeError('landingPage must not contain an origin, query, or fragment')
  }
  return parsed.pathname === '/' ? '/' : parsed.pathname.replace(/\/$/, '')
}

function normalizeDepth(value) {
  const depth = Number(value)
  if (!Number.isInteger(depth) || depth < 1 || depth > 100) {
    throw new TypeError('serpDepth must be an integer from 1 to 100')
  }
  return depth
}

export function validateConfig(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new TypeError('DataForSEO config must be a JSON object')
  }

  const targetDomain = normalizeTargetDomain(input.targetDomain)
  const locationCode = Number(input.locationCode)
  if (!Number.isInteger(locationCode) || locationCode <= 0) {
    throw new TypeError('locationCode must be a positive integer')
  }

  const languageCode = input.languageCode
  if (typeof languageCode !== 'string' || !/^[a-z]{2,3}(?:-[A-Z]{2})?$/.test(languageCode)) {
    throw new TypeError('languageCode must be a DataForSEO language code such as en')
  }

  const device = input.device ?? 'desktop'
  if (!DEVICES.has(device)) {
    throw new TypeError('device must be desktop or mobile')
  }

  if (!Array.isArray(input.keywords) || input.keywords.length === 0) {
    throw new TypeError('keywords must contain at least one entry')
  }
  if (input.keywords.length > 1000) {
    throw new TypeError('keywords cannot contain more than 1000 entries')
  }

  const seen = new Set()
  const keywords = input.keywords.map((entry, index) => {
    const source = typeof entry === 'string' ? { keyword: entry } : entry
    if (!source || typeof source !== 'object' || Array.isArray(source)) {
      throw new TypeError(`keywords[${index}] must be a string or object`)
    }
    if (typeof source.keyword !== 'string' || source.keyword.trim().length < 3) {
      throw new TypeError(`keywords[${index}].keyword must contain at least 3 characters`)
    }
    const keyword = source.keyword.trim()
    if (keyword.length > 80) {
      throw new TypeError(`keywords[${index}].keyword cannot exceed 80 characters`)
    }
    if (keyword.split(/\s+/u).length > 10) {
      throw new TypeError(`keywords[${index}].keyword cannot exceed 10 words`)
    }
    const key = canonicalKeyword(keyword)
    if (seen.has(key)) {
      throw new TypeError(`Duplicate keyword: ${keyword}`)
    }
    seen.add(key)

    const intent = source.intent == null ? null : String(source.intent).trim() || null
    return {
      keyword,
      landingPage: normalizeLandingPage(source.landingPage),
      intent,
    }
  })

  return {
    targetDomain,
    locationCode,
    languageCode,
    device,
    serpDepth: normalizeDepth(input.serpDepth ?? 10),
    keywords,
  }
}

function normalizeMode(mode) {
  if (!MODES.has(mode)) {
    throw new TypeError(`mode must be one of: ${[...MODES].join(', ')}`)
  }
  return mode
}

export function buildPlan(config, { mode = 'all', includeAiOverview = false, depth } = {}) {
  const normalizedMode = normalizeMode(mode)
  const serpDepth = normalizeDepth(depth ?? config.serpDepth)
  const includesKeywords = normalizedMode === 'all' || normalizedMode === 'keywords'
  const includesSerp = normalizedMode === 'all' || normalizedMode === 'serp'
  const serpRequests = includesSerp ? config.keywords.length : 0

  return {
    mode: normalizedMode,
    keywordCount: config.keywords.length,
    serpDepth,
    includeAiOverview: Boolean(includeAiOverview && includesSerp),
    requests: {
      searchVolume: includesKeywords ? 1 : 0,
      keywordDifficulty: includesKeywords ? 1 : 0,
      organicSerp: serpRequests,
      total: (includesKeywords ? 2 : 0) + serpRequests,
    },
    maximumSerpPages: serpRequests * Math.ceil(serpDepth / 10),
    asynchronousAiOverviewRequests: includeAiOverview && includesSerp ? serpRequests : 0,
  }
}

function taskResults(payload) {
  return (payload.tasks ?? []).flatMap(task => Array.isArray(task?.result) ? task.result : [])
}

function payloadCost(payload) {
  const topLevelCost = Number(payload?.cost)
  if (Number.isFinite(topLevelCost)) return topLevelCost
  return (payload?.tasks ?? []).reduce((sum, task) => {
    const cost = Number(task?.cost)
    return sum + (Number.isFinite(cost) ? cost : 0)
  }, 0)
}

export function mergeKeywordMetrics(config, searchVolumePayload, difficultyPayload) {
  const volumes = new Map(
    taskResults(searchVolumePayload).map(item => [canonicalKeyword(item.keyword), item]),
  )
  const difficulties = new Map(
    taskResults(difficultyPayload)
      .flatMap(result => Array.isArray(result?.items) ? result.items : [])
      .map(item => [canonicalKeyword(item.keyword), item]),
  )

  return config.keywords.map(entry => {
    const volume = volumes.get(canonicalKeyword(entry.keyword))
    const difficulty = difficulties.get(canonicalKeyword(entry.keyword))
    return {
      ...entry,
      searchVolume: volume?.search_volume ?? null,
      keywordDifficulty: difficulty?.keyword_difficulty ?? null,
      adsCompetition: volume?.competition ?? null,
      adsCompetitionIndex: volume?.competition_index ?? null,
      cpcUsd: volume?.cpc ?? null,
      monthlySearches: volume?.monthly_searches ?? null,
    }
  })
}

function candidateDomain(value) {
  if (typeof value !== 'string' || value.trim() === '') return null
  const raw = value.trim()
  try {
    return new URL(raw.includes('://') ? raw : `https://${raw}`).hostname
      .toLowerCase()
      .replace(/\.$/, '')
  } catch {
    return null
  }
}

export function domainMatchesTarget(value, targetDomain) {
  const domain = candidateDomain(value)
  return domain === targetDomain || domain?.endsWith(`.${targetDomain}`) === true
}

function visitObjects(value, visitor) {
  if (Array.isArray(value)) {
    for (const child of value) visitObjects(child, visitor)
    return
  }
  if (!value || typeof value !== 'object') return
  visitor(value)
  for (const child of Object.values(value)) visitObjects(child, visitor)
}

function aiOverviewBlocks(items) {
  const blocks = []
  visitObjects(items, value => {
    if (value.type === 'ai_overview') blocks.push(value)
  })
  return blocks
}

function aiOverviewReferences(blocks, targetDomain) {
  const references = new Map()
  for (const block of blocks) {
    visitObjects(block, value => {
      const domainValue = value.domain ?? value.url
      if (!domainMatchesTarget(domainValue, targetDomain)) return
      const key = value.url ?? value.domain
      references.set(key, {
        domain: candidateDomain(domainValue),
        url: value.url ?? null,
        title: value.title ?? null,
        source: value.source ?? null,
      })
    })
  }
  return [...references.values()]
}

function normalizeResultPath(url) {
  if (typeof url !== 'string') return null
  try {
    const pathname = new URL(url).pathname
    return pathname === '/' ? '/' : pathname.replace(/\/$/, '')
  } catch {
    return null
  }
}

export function summarizeSerpResult(configEntry, targetDomain, result) {
  const items = Array.isArray(result?.items) ? result.items : []
  const organicMatches = items
    .filter(item => item?.type === 'organic')
    .filter(item => domainMatchesTarget(item.domain ?? item.url, targetDomain))
    .sort((left, right) => (left.rank_group ?? Infinity) - (right.rank_group ?? Infinity))
  const bestMatch = organicMatches[0] ?? null
  const aioBlocks = aiOverviewBlocks(items)
  const aioReferences = aiOverviewReferences(aioBlocks, targetDomain)

  return {
    ...configEntry,
    organicRank: bestMatch?.rank_group ?? null,
    absoluteRank: bestMatch?.rank_absolute ?? null,
    matchedUrl: bestMatch?.url ?? null,
    matchedTitle: bestMatch?.title ?? null,
    landingPageMatched: configEntry.landingPage == null || bestMatch == null
      ? null
      : normalizeResultPath(bestMatch.url) === configEntry.landingPage,
    aiOverviewTriggered: result?.item_types?.includes('ai_overview') === true || aioBlocks.length > 0,
    aiOverviewCitedTarget: aioReferences.length > 0,
    aiOverviewReferences: aioReferences,
    checkUrl: result?.check_url ?? null,
    capturedAt: result?.datetime ?? null,
  }
}

async function collectKeywordMetrics(client, config) {
  const common = {
    location_code: config.locationCode,
    language_code: config.languageCode,
    keywords: config.keywords.map(entry => entry.keyword),
  }
  const searchVolumePayload = await client.post(DATAFORSEO_ENDPOINTS.searchVolume, [{
    ...common,
    search_partners: false,
  }])
  const difficultyPayload = await client.post(DATAFORSEO_ENDPOINTS.keywordDifficulty, [common])

  return {
    items: mergeKeywordMetrics(config, searchVolumePayload, difficultyPayload),
    costs: {
      searchVolumeUsd: payloadCost(searchVolumePayload),
      keywordDifficultyUsd: payloadCost(difficultyPayload),
    },
  }
}

async function collectSerp(client, config, { depth, includeAiOverview }) {
  const items = []
  let costUsd = 0

  for (const entry of config.keywords) {
    const task = {
      keyword: entry.keyword,
      location_code: config.locationCode,
      language_code: config.languageCode,
      device: config.device,
      depth,
      max_crawl_pages: Math.ceil(depth / 10),
      stop_crawl_on_match: [{
        match_type: 'with_subdomains',
        match_value: config.targetDomain,
      }],
      find_targets_in: ['organic'],
    }
    if (includeAiOverview) task.load_async_ai_overview = true

    const payload = await client.post(DATAFORSEO_ENDPOINTS.organicSerp, [task])
    costUsd += payloadCost(payload)
    const result = taskResults(payload)[0] ?? null
    items.push(summarizeSerpResult(entry, config.targetDomain, result))
  }

  return { items, costUsd }
}

export async function createDataForSeoReport({
  client,
  config,
  mode = 'all',
  includeAiOverview = false,
  depth,
  dryRun = false,
  generatedAt = new Date().toISOString(),
}) {
  const plan = buildPlan(config, { mode, includeAiOverview, depth })
  const report = {
    schemaVersion: 1,
    generatedAt,
    dryRun,
    target: {
      domain: config.targetDomain,
      locationCode: config.locationCode,
      languageCode: config.languageCode,
      device: config.device,
    },
    plan,
    keywordMetrics: null,
    serp: null,
    costs: null,
  }

  if (dryRun) return report
  if (!client) throw new TypeError('A DataForSEO client is required outside dry-run mode')

  const costs = {
    searchVolumeUsd: 0,
    keywordDifficultyUsd: 0,
    organicSerpUsd: 0,
    totalUsd: 0,
  }

  if (plan.mode === 'all' || plan.mode === 'keywords') {
    const keywordMetrics = await collectKeywordMetrics(client, config)
    report.keywordMetrics = keywordMetrics.items
    Object.assign(costs, keywordMetrics.costs)
  }

  if (plan.mode === 'all' || plan.mode === 'serp') {
    const serp = await collectSerp(client, config, {
      depth: plan.serpDepth,
      includeAiOverview: plan.includeAiOverview,
    })
    report.serp = serp.items
    costs.organicSerpUsd = serp.costUsd
  }

  costs.totalUsd = costs.searchVolumeUsd + costs.keywordDifficultyUsd + costs.organicSerpUsd
  report.costs = costs
  return report
}
