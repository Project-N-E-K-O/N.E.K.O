import { DATAFORSEO_ENDPOINTS, DataForSeoApiError, payloadCost } from '../dataforseo/client.mjs'
import { domainMatchesTarget, validateConfig } from '../dataforseo/report.mjs'

const DEPTH = 10
const QUEUED_CODES = new Set([40601, 40602])
const TASK_ID = /^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$/iu

function statusCode(error) {
  return Number.isInteger(error?.statusCode) ? `（${error.statusCode}）` : ''
}

function summarizeResult(row, items, targetDomain) {
  const organic = Array.isArray(items) ? items.filter(item => (
    item?.type === 'organic' && Number.isInteger(item.rank_group)
    && item.rank_group > 0 && item.rank_group <= DEPTH
  )) : []
  if (organic.length === 0) return { ...row, status: 'UNKNOWN', reason: '未返回有效自然搜索结果' }

  const results = organic.map(item => {
    const rank = item.rank_group
    try {
      const url = new URL(item.url)
      const isRedirect = domainMatchesTarget(url.hostname, 'baidu.com')
        && /^\/(?:link|baidu)(?:\/|$)/u.test(url.pathname)
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !isRedirect) {
        return { rank, url }
      }
    } catch {
      // An unresolved organic URL can prevent a reliable domain match.
    }
    return { rank, url: null }
  })
  const match = results
    .filter(item => item.url && domainMatchesTarget(item.url.hostname, targetDomain))
    .sort((left, right) => left.rank - right.rank)[0]
  if (results.some(item => !item.url && (!match || item.rank < match.rank))) {
    return { ...row, status: 'UNKNOWN', reason: '部分自然结果缺少可识别的直达网址' }
  }
  return { ...row, status: 'OBSERVED', rank: match?.rank ?? null, matchedUrl: match?.url.href ?? null }
}

export async function collectBaiduRankings(definition, {
  client,
  paid = false,
  now = Date.now,
  sleep = delay => new Promise(resolve => setTimeout(resolve, delay)),
  maxWaitMs = 6 * 60_000,
} = {}) {
  const report = { id: definition.id, label: definition.label, depth: DEPTH, rows: [] }
  if (!paid) return { ...report, status: 'UNKNOWN', reason: 'dry-run 未提交百度付费排名任务' }
  if (!client) return { ...report, status: 'UNKNOWN', reason: 'DATAFORSEO_LOGIN 或 DATAFORSEO_PASSWORD 未配置' }

  let config
  try {
    config = validateConfig(definition.keywordConfig)
  } catch {
    return { ...report, status: 'FAILED', reason: '百度排名配置无效' }
  }
  if (config.keywords.length > 100) {
    return { ...report, status: 'FAILED', reason: '百度排名任务批次最多 100 个关键词' }
  }
  const entries = config.keywords.map(({ keyword }) => ({ keyword, rank: null, matchedUrl: null }))
  const tasks = entries.map(({ keyword }, index) => ({
    keyword: keyword.replaceAll('%', '%25').replaceAll('+', '%2B'),
    tag: `baidu-${index}`,
    location_code: config.locationCode,
    language_code: 'zh_CN',
    device: config.device,
    depth: DEPTH,
    get_website_url: true,
  }))
  let submission
  try {
    // A POST may already have billed tasks even when its response is lost.
    submission = await client.post(DATAFORSEO_ENDPOINTS.baiduTaskPost, tasks, {
      timeoutMs: 20_000,
      allowTaskErrors: true,
    })
  } catch (error) {
    return {
      ...report,
      status: 'FAILED',
      reason: `百度排队任务提交失败${statusCode(error)}`,
      knownCostUsd: error instanceof DataForSeoApiError ? error.costUsd : 0,
      billingUncertain: !(error instanceof DataForSeoApiError) || error.billingUncertain,
    }
  }
  if (Array.isArray(submission.tasks)) {
    report.knownCostUsd = payloadCost(submission)
  } else {
    return { ...report, status: 'FAILED', reason: '百度排名任务响应缺少任务列表', billingUncertain: true }
  }
  const byTag = new Map(submission.tasks.map(task => [task?.data?.tag, task]))
  const pending = new Map()
  report.rows = entries.map((entry, index) => {
    const task = byTag.get(tasks[index].tag)
    if (task?.status_code === 20100 && TASK_ID.test(task.id)) {
      pending.set(index, task.id)
      return { ...entry, status: 'UNKNOWN', reason: '等待 DataForSEO 排队结果' }
    }
    return {
      ...entry,
      status: 'FAILED',
      reason: `百度排名任务未创建${statusCode({ statusCode: task?.status_code })}`,
    }
  })

  const deadline = now() + maxWaitMs
  while (pending.size > 0 && now() < deadline) {
    const timeoutMs = Math.max(1, Math.min(15_000, deadline - now()))
    await Promise.all([...pending].map(async ([index, id]) => {
      try {
        const response = await client.get(`${DATAFORSEO_ENDPOINTS.baiduTaskGet}/${id}`, { timeoutMs })
        const result = response.tasks?.[0]?.result?.[0]
        report.rows[index] = summarizeResult(entries[index], result?.items, config.targetDomain)
      } catch (error) {
        if (QUEUED_CODES.has(error?.statusCode) || error?.retryable) return
        report.rows[index] = { ...entries[index], status: 'FAILED', reason: `读取百度排队结果失败${statusCode(error)}` }
      }
      pending.delete(index)
    }))
    const remaining = deadline - now()
    if (pending.size > 0 && remaining > 0) await sleep(Math.min(20_000, remaining))
  }
  const observed = report.rows.filter(row => row.status === 'OBSERVED').length
  return {
    ...report,
    observed,
    pending: pending.size,
    collectedAt: new Date().toISOString(),
    status: observed > 0 ? 'OBSERVED' : report.rows.some(row => row.status === 'FAILED') ? 'FAILED' : 'UNKNOWN',
  }
}

export function summarizeBaiduSubmission(receipt, origin, evidence) {
  if (receipt?.status === 'not_run' || !receipt?.provider) {
    return { status: 'UNKNOWN', reason: '暂无可读取的正式百度推送回执' }
  }
  if (receipt.provider !== 'Baidu' || ![origin, `${origin}/`].includes(receipt.site)) {
    return { status: 'FAILED', reason: '百度推送回执站点不匹配' }
  }
  const link = /^https:\/\/github\.com\/Project-N-E-K-O\/N\.E\.K\.O\.OfficialWebsite\/actions\/runs\/\d+$/u.test(evidence)
    ? evidence : null
  const submittedAt = typeof receipt.submittedAt === 'string' && Number.isFinite(Date.parse(receipt.submittedAt))
    ? new Date(receipt.submittedAt).toISOString() : null
  const accepted = receipt.accepted
  const observed = ['complete', 'partial'].includes(receipt.status)
    && receipt.httpStatus === 200 && Number.isInteger(accepted) && accepted > 0 && submittedAt !== null
  return observed
    ? { status: 'OBSERVED', accepted, submittedAt, partial: receipt.status === 'partial', evidence: link }
    : { status: 'FAILED', reason: '最新正式推送未确认有网址被接收', evidence: link }
}
