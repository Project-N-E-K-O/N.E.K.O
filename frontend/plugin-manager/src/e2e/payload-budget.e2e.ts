import { expect, test } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

test('keeps the initial plugin manager payload within the cold-start budget', async ({
  request,
}) => {
  const indexResponse = await request.get(`${PREVIEW_ORIGIN}/ui/`)
  expect(indexResponse.ok()).toBe(true)
  const indexHtml = await indexResponse.text()
  const entryPath = indexHtml.match(/<script[^>]+src="([^"]+index-[^"]+\.js)"/)?.[1]
  const stylesheetPath = indexHtml.match(/<link[^>]+href="([^"]+index-[^"]+\.css)"/)?.[1]

  expect(entryPath).toBeTruthy()
  expect(stylesheetPath).toBeTruthy()

  const [entryResponse, stylesheetResponse] = await Promise.all([
    request.get(`${PREVIEW_ORIGIN}${entryPath}`),
    request.get(`${PREVIEW_ORIGIN}${stylesheetPath}`),
  ])
  const [entryBody, stylesheetBody] = await Promise.all([
    entryResponse.body(),
    stylesheetResponse.body(),
  ])

  expect(entryBody.byteLength).toBeLessThan(750_000)
  expect(stylesheetBody.byteLength).toBeLessThan(80_000)
})

test('keeps the complete initial route script and stylesheet payload bounded', async ({ page }) => {
  await stubCorePluginManagerApis(page)
  const scriptBodies: Array<Promise<Uint8Array>> = []
  const stylesheetBodies: Array<Promise<Uint8Array>> = []
  page.on('response', (response) => {
    if (!response.url().startsWith(PREVIEW_ORIGIN)) return
    const resourceType = response.request().resourceType()
    if (resourceType === 'script') scriptBodies.push(response.body())
    if (resourceType === 'stylesheet') stylesheetBodies.push(response.body())
  })

  await page.goto(`${PREVIEW_ORIGIN}/ui/`)
  await expect(page.locator('.app-root')).toBeVisible()
  await expect(page.locator('.dashboard')).toBeVisible()

  const [scripts, stylesheets] = await Promise.all([
    Promise.all(scriptBodies),
    Promise.all(stylesheetBodies),
  ])
  const scriptBytes = scripts.reduce((total, body) => total + body.byteLength, 0)
  const stylesheetBytes = stylesheets.reduce((total, body) => total + body.byteLength, 0)

  expect(scriptBytes).toBeLessThan(1_000_000)
  expect(stylesheetBytes).toBeLessThan(120_000)
})
