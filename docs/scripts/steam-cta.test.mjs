import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import test from 'node:test'

const docsRoot = new URL('../', import.meta.url)
const homePages = [
  { file: 'index.md', locale: 'en' },
  { file: 'zh-CN/index.md', locale: 'zh_cn' },
  { file: 'ja/index.md', locale: 'ja' },
]
const buyerGuidePages = [
  { slug: 'cost-and-providers', placement: 'cost' },
  { slug: 'data-and-privacy', placement: 'privacy' },
  { slug: 'install-options', placement: 'install' },
  { slug: 'local-and-offline', placement: 'offline' },
]
const buyerGuideLocales = [
  { directory: 'guide', locale: 'en' },
  { directory: 'zh-CN/guide', locale: 'zh_cn' },
  { directory: 'ja/guide', locale: 'ja' },
]
const steamUrlPattern = /https:\/\/store\.steampowered\.com\/app\/4099310\/__NEKO\/\?[^\s'"`)]+/g

function markdownFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = new URL(entry.name, directory)
    if (entry.isDirectory()) return markdownFiles(new URL(`${entry.name}/`, directory))
    return entry.isFile() && entry.name.endsWith('.md') ? [target] : []
  })
}

test('every localized home-page Steam CTA carries attributable UTM tags', () => {
  for (const { file, locale } of homePages) {
    const source = readFileSync(new URL(file, docsRoot), 'utf8')
    const links = source.match(steamUrlPattern) ?? []

    assert.equal(links.length, 2, `${file} must expose exactly two Steam CTAs`)
    const placements = new Set()
    for (const link of links) {
      const url = new URL(link)
      assert.equal(url.searchParams.get('utm_source'), 'project-neko.online')
      assert.equal(url.searchParams.get('utm_medium'), 'referral')
      assert.equal(url.searchParams.get('utm_campaign'), 'docs_home')
      placements.add(url.searchParams.get('utm_content'))
    }
    assert.deepEqual(
      placements,
      new Set([`hero_${locale}`, `feature_${locale}`]),
    )
  }
})

test('every buyer guide exposes a page-specific, attributable Steam CTA', () => {
  for (const { directory, locale } of buyerGuideLocales) {
    for (const { slug, placement } of buyerGuidePages) {
      const file = `${directory}/${slug}.md`
      const source = readFileSync(new URL(file, docsRoot), 'utf8')
      const links = source.match(steamUrlPattern) ?? []
      const expectedPlacement = `${placement}_footer_${locale}`

      assert.ok(links.length > 0, `${file} must expose a Steam CTA`)
      assert.ok(
        links.some((link) => {
          const url = new URL(link)
          return (
            url.searchParams.get('utm_campaign') === 'buyer_guides' &&
            url.searchParams.get('utm_content') === expectedPlacement
          )
        }),
        `${file} must expose the ${expectedPlacement} CTA`,
      )
    }
  }
})

test('every Steam store link in published Markdown carries attribution', () => {
  let checkedLinks = 0

  for (const file of markdownFiles(docsRoot)) {
    const source = readFileSync(file, 'utf8')
    const links = source.match(
      /https:\/\/store\.steampowered\.com\/app\/4099310\/__NEKO\/[^\s'"`)>]*/g,
    ) ?? []

    for (const link of links) {
      checkedLinks += 1
      const url = new URL(link)
      assert.equal(url.searchParams.get('utm_source'), 'project-neko.online')
      assert.equal(url.searchParams.get('utm_medium'), 'referral')
      assert.ok(url.searchParams.get('utm_campaign'))
      assert.ok(url.searchParams.get('utm_content'))
    }
  }

  assert.ok(checkedLinks > 0, 'expected at least one Steam store link')
})
