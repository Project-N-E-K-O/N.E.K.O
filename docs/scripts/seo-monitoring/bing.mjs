import { DATAFORSEO_ENDPOINTS, DataForSeoApiError } from '../dataforseo/client.mjs'
import { candidateDomain, createSerpRequester, domainMatchesTarget, validateConfig } from '../dataforseo/report.mjs'

const WEBMASTER_ORIGIN = 'https://ssl.bing.com'
const SERP_DEPTH = 10
const STORED_QUERY_LIMIT = 20
const CHINA_LOCATION_CODE = 2156

function bingDate(value) {
  if (typeof value !== 'string') return null
  const wrapped = /^\/Date\((-?\d+)(?:([+-])(\d{2})(\d{2}))?\)\/$/u.exec(value)
  if (!wrapped) {
    if (!/^\d{4}-\d{2}-\d{2}(?:T|$)/u.test(value) || !Number.isFinite(Date.parse(value))) return null
    const day = value.slice(0, 10)
    const date = new Date(`${day}T00:00:00.000Z`)
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === day ? day : null
  }
  const [, milliseconds, sign, hours, minutes] = wrapped
  const offset = sign ? (Number(hours) * 60 + Number(minutes)) * (sign === '-' ? -1 : 1) : 0
  const date = new Date(Number(milliseconds) + offset * 60_000)
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : null
}

function trafficRow(row) {
  const date = bingDate(row?.Date)
  const clicks = row?.Clicks
  const impressions = row?.Impressions
  if (date == null || !Number.isInteger(clicks) || clicks < 0 || !Number.isInteger(impressions) || impressions < 0) {
    return null
  }
  return { date, clicks, impressions }
}

function queryRow(row) {
  const traffic = trafficRow(row)
  if (!traffic || typeof row.Query !== 'string' || !row.Query.trim()) return null
  const position = row.AvgImpressionPosition
  return { ...traffic, query: row.Query, position: Number.isFinite(position) && position > 0 ? position : null }
}

async function getWebmasterRows(method, siteUrl, apiKey, parseRow, { fetchImpl, timeoutMs }) {
  if (!apiKey?.trim()) return { status: 'UNKNOWN', reason: 'BING_WEBMASTER_API_KEY 未配置', rows: [] }
  try {
    const request = new URL(`/webmaster/api.svc/json/${method}`, WEBMASTER_ORIGIN)
    request.searchParams.set('siteUrl', new URL('/', siteUrl).href)
    request.searchParams.set('apikey', apiKey.trim())
    const response = await fetchImpl(request, {
      headers: { Accept: 'application/json' },
      redirect: 'error',
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (!response.ok) {
      const fault = await response.json().catch(() => null)
      const code = Number.isInteger(fault?.ErrorCode) ? `（错误码 ${fault.ErrorCode}）` : ''
      return { status: 'FAILED', reason: `Bing 站长 API 返回 HTTP ${response.status}${code}`, rows: [] }
    }
    const payload = await response.json()
    if (!Array.isArray(payload?.d)) return { status: 'FAILED', reason: 'Bing 站长 API 返回格式异常', rows: [] }
    let rows = payload.d.map(parseRow).filter(Boolean)
    const invalidRowCount = payload.d.length - rows.length
    rows.sort((left, right) => right.date.localeCompare(left.date) || right.impressions - left.impressions)
    let period = {}
    if (method === 'GetQueryStats') {
      const dates = [...new Set(payload.d.map(row => bingDate(row?.Date)).filter(Boolean))]
      const latestDate = dates.sort().at(-1)
      if (latestDate) {
        rows = rows.filter(row => row.date === latestDate)
        period = { latestDate, distinctDateCount: dates.length, latestDateRowCount: rows.length }
        rows = rows.slice(0, STORED_QUERY_LIMIT)
      }
    }
    if (rows.length === 0) {
      if (period.latestDate) {
        return { ...period, status: 'FAILED', reason: 'Bing 站长 API 最新一周未返回有效查询', invalidRowCount, rows }
      }
      return invalidRowCount > 0
        ? { status: 'FAILED', reason: 'Bing 站长 API 未返回有效数据行', invalidRowCount, rows }
        : { status: 'UNKNOWN', reason: 'Bing 站长 API 未返回数据行', rows }
    }
    return { ...period, status: 'OBSERVED', collectedAt: new Date().toISOString(), invalidRowCount, rows }
  } catch (error) {
    const reason = error?.name === 'TimeoutError' || error?.name === 'AbortError'
      ? 'Bing 站长 API 请求超时'
      : 'Bing 站长 API 请求或响应解析失败'
    return { status: 'FAILED', reason, rows: [] }
  }
}

export async function collectBingWebmaster(siteUrl, {
  apiKey,
  fetchImpl = globalThis.fetch,
  timeoutMs = 15_000,
} = {}) {
  const options = { fetchImpl, timeoutMs }
  const [traffic, queries] = await Promise.all([
    getWebmasterRows('GetRankAndTrafficStats', siteUrl, apiKey, trafficRow, options),
    getWebmasterRows('GetQueryStats', siteUrl, apiKey, queryRow, options),
  ])
  return { traffic, queries }
}

async function checkChinaLocation(client, locationCode) {
  try {
    const payload = await client.get(DATAFORSEO_ENDPOINTS.bingChinaLocations, { timeoutMs: 15_000 })
    const tasks = payload?.tasks
    if (!Array.isArray(tasks) || tasks.length === 0 || !tasks.every(task => Array.isArray(task?.result))) {
      return { status: 'FAILED', reason: 'DataForSEO Bing 中国地区列表格式异常' }
    }
    const supported = tasks.some(task => task.result.some(location => (
      location?.location_code === locationCode && location.country_iso_code === 'CN'
    )))
    if (supported) return { status: 'OBSERVED' }
    return { status: 'UNSUPPORTED', reason: `DataForSEO Bing 中国地区列表未包含 ${locationCode}` }
  } catch (error) {
    const code = Number.isInteger(error?.statusCode) ? `（${error.statusCode}）` : ''
    return { status: 'FAILED', reason: `DataForSEO Bing 中国地区校验失败${code}` }
  }
}

export async function collectBingRankings(definition, {
  client,
  paid = false,
  includeCn = false,
  retryOptions,
} = {}) {
  const segment = { id: definition.id, label: definition.label, depth: SERP_DEPTH }
  const skipReason = definition.bingRanking === 'china-opt-in' && !includeCn
    ? '中文组开关未开启'
    : !paid
      ? 'dry-run 未请求付费排名'
      : !client
        ? 'DATAFORSEO_LOGIN 或 DATAFORSEO_PASSWORD 未配置'
        : null
  if (skipReason) return { ...segment, status: 'UNKNOWN', reason: skipReason, rows: [] }

  let config
  try {
    config = validateConfig(definition.keywordConfig)
  } catch {
    return { ...segment, status: 'FAILED', reason: 'Bing 排名配置无效', rows: [] }
  }
  if (definition.bingRanking === 'china-opt-in' || config.locationCode === CHINA_LOCATION_CODE) {
    if (!includeCn) return { ...segment, status: 'UNKNOWN', reason: '中文组开关未开启', rows: [] }
    const location = await checkChinaLocation(client, config.locationCode)
    if (location.status !== 'OBSERVED') return { ...segment, ...location, rows: [] }
  }
  const requestSerp = createSerpRequester(client, {
    endpoint: DATAFORSEO_ENDPOINTS.bingOrganicSerp,
    // Bound the serial Bing requests within the shared report job's time budget.
    timeoutMs: 20_000,
    retryOptions,
  })
  const rows = []
  let knownCostUsd = 0
  let billingUncertain = false
  let fatalReason = null
  for (const entry of config.keywords) {
    const row = { keyword: entry.keyword, rank: null, matchedUrl: null }
    if (fatalReason) {
      rows.push({ ...row, status: 'UNKNOWN', reason: fatalReason })
      continue
    }
    try {
      const request = await requestSerp(entry, {
        keyword: entry.keyword,
        location_code: config.locationCode,
        language_code: config.languageCode,
        device: config.device,
        depth: SERP_DEPTH,
      })
      knownCostUsd += request.costUsd
      if (request.error) {
        billingUncertain ||= request.error.billingUncertain
        const code = Number.isInteger(request.error.statusCode) ? `（${request.error.statusCode}）` : ''
        rows.push({ ...row, status: 'FAILED', reason: `DataForSEO 请求失败${code}` })
        continue
      }
      const items = request.payload.tasks?.[0]?.result?.[0]?.items
      if (!Array.isArray(items) || items.length === 0) {
        rows.push({ ...row, status: 'UNKNOWN', reason: 'DataForSEO 未返回搜索结果' })
        continue
      }
      const organic = items.filter(item => item?.type === 'organic').map(item => ({
        rank: item.rank_group,
        url: item.url,
        candidate: candidateDomain(item.domain) ?? candidateDomain(item.url),
      }))
      const ranked = organic.filter(item => Number.isInteger(item.rank)
        && item.rank > 0 && item.rank <= SERP_DEPTH
        && item.candidate)
      if (ranked.length === 0) {
        rows.push({ ...row, status: 'UNKNOWN', reason: 'DataForSEO 未返回有效自然搜索结果' })
        continue
      }
      const match = ranked
        .filter(item => domainMatchesTarget(item.candidate, config.targetDomain))
        .sort((left, right) => left.rank - right.rank)[0]
      const missingTargetRank = organic.some(item => (
        (!Number.isInteger(item.rank) || item.rank <= 0) && domainMatchesTarget(item.candidate, config.targetDomain)
      ))
      const missingDomain = organic.some(item => !item.candidate && (
        !Number.isInteger(item.rank) || item.rank <= 0 || item.rank < (match?.rank ?? SERP_DEPTH + 1)
      ))
      if (missingTargetRank || missingDomain) {
        rows.push({
          ...row,
          ...(match ? { confirmedRank: match.rank, matchedUrl: match.url ?? null } : {}),
          status: 'UNKNOWN',
          reason: missingTargetRank ? '目标域名结果缺少有效自然排名' : '部分自然结果缺少可识别的域名',
        })
        continue
      }
      rows.push({ ...row, status: 'OBSERVED', rank: match?.rank ?? null, matchedUrl: match?.url ?? null })
    } catch (error) {
      if (error instanceof DataForSeoApiError) knownCostUsd += error.costUsd
      billingUncertain ||= !(error instanceof DataForSeoApiError) || error.billingUncertain
      const code = Number.isInteger(error?.statusCode) ? `（${error.statusCode}）` : ''
      fatalReason = `DataForSEO 请求失败${code}`
      rows.push({ ...row, status: 'FAILED', reason: fatalReason })
    }
  }

  const observed = rows.filter(row => row.status === 'OBSERVED').length
  const status = observed > 0 ? 'OBSERVED' : rows.some(row => row.status === 'FAILED') ? 'FAILED' : 'UNKNOWN'
  return {
    ...segment,
    status,
    collectedAt: new Date().toISOString(),
    observed,
    target: {
      domain: config.targetDomain,
      locationCode: config.locationCode,
      languageCode: config.languageCode,
      device: config.device,
    },
    knownCostUsd,
    billingUncertain,
    rows,
  }
}
