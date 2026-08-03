import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

async function readWorkflow() {
  return readFile(
    new URL('../../../.github/workflows/dataforseo.yml', import.meta.url),
    'utf8',
  )
}

async function readDeployWorkflow() {
  return readFile(
    new URL('../../../.github/workflows/docs.yml', import.meta.url),
    'utf8',
  )
}

async function readMaintainerGuide() {
  return readFile(
    new URL('../../contributing/dataforseo-seo-monitoring.md', import.meta.url),
    'utf8',
  )
}

test('scheduled and manually dispatched paid runs force a depth-100 AIO baseline', async () => {
  const workflow = await readWorkflow()

  assert.match(workflow, /cron: '15 0 \* \* \*'/)
  assert.match(workflow, /inputs\.run_mode != 'dry-run'/)
  assert.match(workflow, /\(github\.event_name == 'schedule' \|\| inputs\.run_mode == 'paid'\) && '100'/)
  assert.match(workflow, /\(github\.event_name == 'schedule' \|\| inputs\.run_mode == 'paid'\) && 'true'/)
  assert.doesNotMatch(workflow, /ENABLE_PAID_DATAFORSEO_SCHEDULE/)
})

test('manual CN brand verification is one keyword, depth 10, and AIO-free', async () => {
  const workflow = await readWorkflow()
  const fullConfig = JSON.parse(await readFile(
    new URL('../../seo/dataforseo.cn.config.json', import.meta.url),
    'utf8',
  ))
  const verificationConfig = JSON.parse(await readFile(
    new URL('../../seo/dataforseo.cn-brand-verification.config.json', import.meta.url),
    'utf8',
  ))

  assert.match(workflow, /- verify-cn-brand/)
  assert.match(workflow, /inputs\.run_mode == 'verify-cn-brand' && '10'/)
  assert.match(workflow, /inputs\.run_mode == 'verify-cn-brand' && 'false'/)
  assert.match(workflow, /dataforseo\.cn-brand-verification\.config\.json/)
  assert.match(workflow, /name: Enforce the one-keyword CN verification contract/)
  assert.match(workflow, /\.keywordMetrics\[0\]\.keyword == "喵可智能"/)
  assert.match(workflow, /env\.BRAND_VERIFICATION != 'true'/)
  assert.equal(verificationConfig.keywords.length, 1)
  assert.equal(verificationConfig.keywords[0].keyword, '喵可智能')
  assert.deepEqual(
    verificationConfig.keywords[0],
    fullConfig.keywords.find(item => item.keyword === '喵可智能'),
  )
  for (const key of [
    'targetDomain',
    'locationCode',
    'locale',
    'serpLanguageCode',
    'volumeLanguageCode',
    'keywordDifficultyLanguageCode',
    'device',
  ]) {
    assert.equal(verificationConfig[key], fullConfig[key])
  }
  assert.equal(verificationConfig.serpDepth, 10)
})

test('maintainer documentation cannot revive the obsolete paid-schedule kill switch', async () => {
  const guide = await readMaintainerGuide()

  assert.match(guide, /08:15 Asia\/Shanghai schedule always runs the paid baseline/)
  assert.match(guide, /fixed-name `seo-geo-daily-report` diagnostic artifact/)
  assert.match(guide, /`seo-geo-daily-paid-baseline`/)
  assert.match(guide, /obsolete `ENABLE_PAID_DATAFORSEO_SCHEDULE` variable/)
  assert.doesNotMatch(guide, /set `ENABLE_PAID_DATAFORSEO_SCHEDULE=true`/)
  assert.doesNotMatch(guide, /schedule is skipped unless/)
})

test('one run collects independent CN, online-English, and online-Chinese segments', async () => {
  const workflow = await readWorkflow()

  for (const config of [
    'dataforseo.config.json',
    'dataforseo.cn.config.json',
    'dataforseo.online-zh.config.json',
  ]) {
    assert.match(workflow, new RegExp(`${config.replaceAll('.', '\\.')} --mode keywords`))
    assert.match(workflow, new RegExp(`${config.replaceAll('.', '\\.')} --mode serp`))
  }
  assert.doesNotMatch(workflow, /dataforseo(?:\.online-zh|\.cn)?\.config\.json --mode all/)
  assert.equal((workflow.match(/id: (?:online_en|cn|online_zh)_metrics/gu) ?? []).length, 3)
  assert.equal((workflow.match(/id: (?:online_en|cn|online_zh)_ranking/gu) ?? []).length, 3)
  assert.equal((workflow.match(/node scripts\/dataforseo\/merge-reports\.mjs/gu) ?? []).length, 3)
  assert.match(workflow, /METRICS_OUTCOME: \$\{\{ steps\.online_en_metrics\.outcome \}\}/)
  assert.match(workflow, /RANKING_OUTCOME: \$\{\{ steps\.online_en_ranking\.outcome \}\}/)
  assert.match(workflow, /--segment online-en/)
  assert.match(workflow, /--segment cn/)
  assert.match(workflow, /--segment online-zh/)
  assert.match(workflow, /actions\/artifacts\?name=dataforseo-cn-report/)
  assert.match(workflow, /\.name == "dataforseo-cn-report"/)
  assert.match(workflow, /CN_EXTERNAL_REPORT/)
  assert.match(workflow, /mode.*all/)
  assert.match(workflow, /run_status.*complete/)
  assert.match(workflow, /ranking_status.*complete/)
  assert.match(workflow, /keyword_metrics_status.*complete/)
  assert.match(workflow, /ai_overview_status.*complete/)
  assert.match(workflow, /report_status.*complete/)
  assert.match(workflow, /serp_depth.*100/)
  assert.match(workflow, /include_ai_overview.*true/)
  assert.match(workflow, /artifact_date.*today/)
})

test('the unified artifact contains source statuses, raw reports, Markdown, and JSON', async () => {
  const workflow = await readWorkflow()

  assert.match(workflow, /secrets\.GOOGLE_SERVICE_ACCOUNT_JSON/)
  assert.match(workflow, /vars\.GA4_PROPERTY_ID/)
  assert.match(workflow, /vars\.GA4_CN_PROPERTY_ID/)
  assert.match(workflow, /vars\.GSC_SITE_URL/)
  assert.match(workflow, /vars\.GSC_CN_SITE_URL/)
  assert.match(workflow, /actions\/artifacts\?name=indexnow-online-submission/)
  assert.match(workflow, /--name "indexnow-online-submission"/)
  assert.match(workflow, /secrets\.SEO_REPORTS_TOKEN/)
  assert.match(workflow, /Project-N-E-K-O\/N\.E\.K\.O\.OfficialWebsite/)
  assert.match(workflow, /actions\/artifacts\?name=indexnow-cn-submission/)
  assert.match(workflow, /--name "indexnow-cn-submission"/)
  assert.equal((workflow.match(/sort_by\(\.created_at\) \| last/gu) ?? []).length, 4)
  assert.match(workflow, /--indexnow "cn=/)
  assert.match(workflow, /--indexnow "online=/)
  assert.match(workflow, /actions\/artifacts\?name=seo-geo-daily-paid-baseline/)
  assert.match(workflow, /\.name == "seo-geo-daily-paid-baseline"/)
  assert.match(workflow, /\.workflow_run\.head_branch == "main"/)
  assert.match(workflow, /--previous-report "\$PREVIOUS_REPORT"/)
  assert.match(workflow, /PREVIOUS_REPORT_EVIDENCE=https:\/\/github\.com/)
  assert.match(workflow, /npm run seo:report/)
  assert.match(workflow, /assert-report\.mjs --input "\$report" --level daily/)
  assert.match(workflow, /env\.COLLECTION_KIND == 'paid'/)
  assert.match(workflow, /name: Enforce the zero-cost planning contract/)
  assert.match(workflow, /env\.COLLECTION_KIND == 'dry-run'/)
  assert.match(workflow, /\.runStatus == "planned"/)
  assert.match(workflow, /include-hidden-files: true/)
  assert.match(workflow, /if-no-files-found: error/)
  assert.match(workflow, /docs\/\.seo-reports\/\*\*/)
  assert.match(workflow, /!docs\/\.seo-reports\/previous\/\*\*/)
  assert.match(workflow, /name: seo-geo-daily-report/)
  assert.match(workflow, /name: Publish verified paid comparison baseline/)
  assert.match(workflow, /name: seo-geo-daily-paid-baseline/)
  assert.match(workflow, /github\.ref == 'refs\/heads\/main'/)
})

test('documentation deployment retains a fixed-name .online IndexNow status artifact', async () => {
  const workflow = await readDeployWorkflow()

  assert.match(workflow, /--output docs\/\.seo-reports\/indexnow-online\.json/)
  assert.match(workflow, /name: indexnow-online-submission/)
  assert.match(workflow, /if: always\(\)/)
  assert.match(workflow, /if-no-files-found: error/)
  assert.match(workflow, /retention-days: 90/)
})
