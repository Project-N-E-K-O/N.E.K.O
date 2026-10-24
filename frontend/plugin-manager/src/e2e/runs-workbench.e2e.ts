import { expect, test, type Page, type WebSocketRoute } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'
import type { ExportItem, RunRecord } from '../api/runs'

async function fixture(page: Page, dark = false) {
  const unexpected: string[] = []
  await page.route('**/*', (route) => {
    const request = route.request(),
      url = new URL(request.url())
    if (
      url.origin === PREVIEW_ORIGIN &&
      url.pathname.startsWith('/ui/') &&
      ['document', 'script', 'stylesheet', 'image', 'font'].includes(request.resourceType())
    )
      return route.continue()
    unexpected.push(`${request.method()} ${url.pathname}`)
    return route.abort('blockedbyclient')
  })
  await stubCorePluginManagerApis(page)
  await page.route('**/market/status', (route) => route.fulfill({ json: { market_url: null } }))
  await page.addInitScript((dark) => {
    localStorage.setItem('locale', 'en-US')
    localStorage.setItem('neko-dark-mode', String(dark))
  }, dark)
  const now = Math.floor(Date.now() / 1000)
  const runs: RunRecord[] = ['running', 'output', 'empty', 'failed'].map((id, index) => ({
    run_id: id,
    plugin_id: index === 3 ? 'web_search' : 'neko_arcade',
    entry_id: index === 3 ? 'search' : 'get_arcade_state',
    status: index === 0 ? 'running' : index === 3 ? 'failed' : 'succeeded',
    created_at: now - 30 - index * 3,
    updated_at: now - index * 3,
    started_at: now - 20 - index * 3,
    finished_at: index ? now - index * 3 : null,
    progress: index === 0 ? null : 1,
    error:
      index === 3
        ? {
            code: 'TIMEOUT',
            message: 'The search service did not respond.',
            details: { timeout: 10000 },
          }
        : null,
  }))
  const outputs: Record<string, ExportItem[]> = {
    output: [
      {
        run_id: 'output',
        export_item_id: 'board',
        type: 'json',
        created_at: now,
        label: 'Board state',
        json: { board: 'gomoku', turn: 'player' },
      },
    ],
  }
  const requests: Array<{ method: string; params: { run_id: string }; id: string }> = []
  const holds = new Set<string>(),
    failures = new Set<string>()
  const replies = new Map<string, () => void>()
  let socket: WebSocketRoute
  await page.routeWebSocket('**/*', (route) => {
    socket = route
    route.onMessage((data) => {
      const request = JSON.parse(String(data))
      if (request.type !== 'req') return
      requests.push(request)
      const id = request.params.run_id
      const key = `${request.method}:${id || ''}`
      const run = runs.find((run) => run.run_id === id)
      if (request.method === 'run.cancel' && run) run.status = 'cancel_requested'
      const value =
        request.method === 'runs.list'
          ? runs
          : request.method === 'export.list'
            ? { items: outputs[id] || [] }
            : run
      // Freeze the reply at request time so tests can reproduce late snapshots.
      const result = JSON.parse(JSON.stringify(value))
      const reply = () => {
        const ok = !failures.delete(key)
        route.send(
          JSON.stringify({
            type: 'resp',
            id: request.id,
            ok,
            result,
            error: ok ? undefined : 'fixture failure',
          })
        )
      }
      if (holds.has(key)) replies.set(key, reply)
      else reply()
    })
  })
  return {
    runs,
    outputs,
    requests,
    holds,
    failures,
    replies,
    unexpected,
    disconnect() {
      return socket.close({ code: 1001, reason: 'fixture disconnect' })
    },
    emit(bus: string, payload: object) {
      socket.send(JSON.stringify({ type: 'event', event: 'bus.change', data: { bus, payload } }))
    },
  }
}

for (const size of [320, 768, 1024, 1440, 1920]) {
  test(`continuous run workbench at ${size}px preserves bounds, selection and narrow back navigation`, async ({
    page,
  }) => {
    const api = await fixture(page, size === 1024)
    await page.setViewportSize({ width: size, height: 850 })
    await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
    await expect(page.locator('button[data-run-id]')).toHaveCount(4)
    await expect(page.locator('[data-yui-guide-id="runs-refresh"]')).toHaveCount(1)
    await expect(page.locator('[data-yui-guide-id="runs-refresh"]')).toBeVisible()
    const sidebar = await page.locator('.app-sidebar').boundingBox()
    await page.locator('button[data-run-id="output"]').click()
    await expect(page.locator('.output-code')).toContainText('gomoku')
    await expect(page.locator('.output-item__type')).toHaveCSS('white-space', 'nowrap')
    await expect(page.locator('[data-yui-guide-id="runs-refresh"]')).toHaveCount(1)
    await expect(page.locator('[data-yui-guide-id="runs-refresh"]')).toBeVisible()
    expect(await page.locator('.app-sidebar').boundingBox()).toEqual(sidebar)
    expect(
      await page
        .locator('.app-main')
        .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
    ).toBe(true)
    const compact = await page
      .locator('.runs-page')
      .evaluate((element) => element.clientWidth < 900)
    if (compact) {
      await expect(page.locator('.runs-back')).toBeFocused()
      await expect(page.locator('.runs-list-pane')).toBeHidden()
      await page.locator('.runs-back').click()
      await expect(page.locator('button[data-run-id="output"]')).toBeFocused()
      await expect(page.locator('[data-yui-guide-id="runs-refresh"]')).toBeVisible()
      await expect(page.locator('button[data-run-id="output"]')).toHaveAttribute(
        'aria-pressed',
        'true'
      )
    } else {
      const list = await page.locator('.runs-list-pane').boundingBox()
      const detail = await page.locator('.runs-detail-pane').boundingBox()
      expect(list!.y).toBe(detail!.y)
      expect(list!.height).toBe(detail!.height)
    }
    expect(api.unexpected).toEqual([])
  })
}

test('rows keep the same rounded silhouette on hover/selection and one refresh updates the workbench', async ({
  page,
}) => {
  const api = await fixture(page)
  await page.setViewportSize({ width: 1440, height: 850 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  const row = page.locator('[data-run-id="output"]')
  const radius = await row.evaluate((element) => getComputedStyle(element).borderRadius)
  expect(radius).not.toBe('0px')
  await row.hover()
  await expect(row).toHaveCSS('border-radius', radius)
  await row.click()
  await expect(row).toHaveCSS('border-radius', radius)
  await expect(page.locator('.output-code')).toContainText('gomoku')
  await expect(page.locator('.output-item')).toHaveCSS('border-radius', radius)
  await expect(page.locator('.output-item')).toHaveCSS('overflow', 'clip')
  await expect(page.locator('.run-summary .run-status')).toHaveCSS('border-radius', radius)
  await expect(page.locator('.runs-page')).not.toContainText('Ordered by update time')
  api.outputs.output![0]!.json = { board: 'manually refreshed' }
  await page.locator('[data-yui-guide-id="runs-refresh"]').click()
  await expect(page.locator('.output-code')).toContainText('manually refreshed')
  for (const method of ['runs.list', 'run.get', 'export.list']) {
    expect(api.requests.filter((request) => request.method === method)).toHaveLength(2)
  }
  expect(api.unexpected).toEqual([])
})

test('empty, unknown progress, failure and output states are distinct and localized', async ({
  page,
}) => {
  const api = await fixture(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="running"]').click()
  await expect(page.locator('.run-detail-content')).toContainText('Progress has not been reported')
  await expect(page.locator('.run-progress')).toHaveCount(0)
  await page.locator('button[data-run-id="failed"]').click()
  await expect(page.locator('.run-error')).toContainText('The search service did not respond.')
  await page.locator('button[data-run-id="empty"]').click()
  await expect(page.getByTestId('run-output-empty')).toHaveCount(1)
  await expect(page.locator('.el-table__empty-block, .el-empty')).toHaveCount(0)
  await expect(page.locator('.run-progress, .run-error')).toHaveCount(0)
  expect(api.unexpected).toEqual([])
})

test('switching A B C immediately selects C and never presents late A data or errors as C', async ({
  page,
}) => {
  const api = await fixture(page)
  api.holds.add('run.get:running')
  api.holds.add('export.list:running')
  api.failures.add('run.get:running')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="running"]').click()
  await expect.poll(() => api.replies.size).toBe(2)
  await page.locator('button[data-run-id="output"]').click()
  await page.locator('button[data-run-id="failed"]').click()
  await expect(page.locator('button[data-run-id="failed"]')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('.run-summary h2')).toHaveText('search')
  api.replies.get('run.get:running')!()
  api.replies.get('export.list:running')!()
  await expect(page.locator('.run-error')).toContainText('The search service did not respond.')
  await expect(page.locator('.detail-feedback')).toHaveCount(0)
  expect(
    api.requests
      .filter((request) => request.method === 'run.get')
      .map((request) => request.params.run_id)
  ).toEqual(['running', 'failed'])
  expect(api.unexpected).toEqual([])
})

test('a failed output request retries independently and export events refresh the selected run', async ({
  page,
}) => {
  const api = await fixture(page)
  api.failures.add('export.list:output')
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="output"]').click()
  await expect(page.locator('.run-facts')).toBeVisible()
  await expect(page.locator('.output-feedback')).toBeVisible()
  await page.locator('.output-feedback').getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.locator('.output-code')).toContainText('gomoku')
  expect(api.requests.filter((request) => request.method === 'run.get').length).toBe(1)
  api.outputs.output![0]!.json = { board: 'updated board' }
  api.emit('export', { run_id: 'output' })
  await expect(page.locator('.output-code')).toContainText('updated board')
  expect(api.unexpected).toEqual([])
})

test('no records produces one empty state, and large text/long identifiers do not overflow', async ({
  page,
}) => {
  const api = await fixture(page)
  api.runs.splice(0)
  await page.setViewportSize({ width: 1440, height: 800 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await expect(page.locator('[data-yui-guide-id="runs-empty"]')).toBeVisible()
  await expect(page.locator('[data-yui-guide-id="runs-detail-empty"]')).toHaveCount(0)
  api.runs.push({
    run_id: 'long_' + 'x'.repeat(90),
    entry_id: 'very_long_entry_' + 'detail'.repeat(24),
    plugin_id: 'long_plugin_' + 'name'.repeat(20),
    status: 'succeeded',
    created_at: 1,
    updated_at: 2,
  })
  await page.locator('[data-yui-guide-id="runs-refresh"]').click()
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '24px'
    document.documentElement.style.zoom = '1.5'
  })
  await page.locator('button[data-run-id]').click()
  await expect(page.locator('.run-facts')).toBeVisible()
  expect(
    await page
      .locator('.app-main')
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
  ).toBe(true)
  expect(
    await page
      .locator('.run-detail-content')
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
  ).toBe(true)
  expect(api.unexpected).toEqual([])
})

test('late detail/output snapshots cannot undo a completed live run', async ({ page }) => {
  const api = await fixture(page)
  api.holds.add('run.get:running')
  api.holds.add('export.list:running')
  await page.setViewportSize({ width: 1440, height: 850 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="running"]').click()
  await expect.poll(() => api.replies.size).toBe(2)
  const run = api.runs[0]!
  Object.assign(run, {
    status: 'succeeded',
    progress: 1,
    finished_at: run.updated_at + 1,
    updated_at: run.updated_at + 1,
  })
  api.outputs.running = [
    {
      export_item_id: 'final',
      run_id: 'running',
      type: 'text',
      text: 'Final live result',
      created_at: run.updated_at,
    },
  ]
  api.emit('runs', run)
  api.emit('export', { run_id: 'running' })
  api.holds.clear()
  api.holds.add('export.list:running')
  api.replies.get('run.get:running')!()
  api.replies.get('export.list:running')!()
  await expect
    .poll(() => api.requests.filter((request) => request.method === 'export.list').length)
    .toBe(2)
  await expect(page.getByTestId('run-output-empty')).toHaveCount(0)
  api.replies.get('export.list:running')!()
  await expect(page.locator('.output-text')).toHaveText('Final live result')
  await expect(page.locator('.run-summary .run-status')).toHaveText('Succeeded')
  await expect(page.locator('[data-yui-guide-id="runs-detail-cancel"]')).toHaveCount(0)
  expect(api.unexpected).toEqual([])
})

test('cancellation is confirmed for the selected record and remains a request until acknowledged', async ({
  page,
}) => {
  const api = await fixture(page)
  await page.setViewportSize({ width: 1440, height: 850 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="running"]').click()
  await expect(page.locator('.run-facts')).toBeVisible()
  await page.locator('[data-yui-guide-id="runs-detail-cancel"]').click()
  const dialog = page.locator('.el-message-box')
  await expect(dialog).toContainText('running')
  await dialog.locator('.el-message-box__btns .el-button--primary').click()
  await expect(page.locator('.run-summary .run-status')).toHaveText('Cancel requested')
  await expect(page.locator('[data-yui-guide-id="runs-detail-cancel"]')).toHaveCount(0)
  expect(
    api.requests
      .filter((request) => request.method === 'run.cancel')
      .map((request) => request.params.run_id)
  ).toEqual(['running'])
  expect(api.unexpected).toEqual([])
})

test('long translated headers share their actual track height at large text and zoom', async ({
  page,
}) => {
  const api = await fixture(page, true)
  await page.addInitScript(() => localStorage.setItem('locale', 'ru'))
  await page.setViewportSize({ width: 1500, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="running"]').click()
  await page.evaluate(() => {
    document.documentElement.style.fontSize = '24px'
    document.documentElement.style.zoom = '1.25'
  })
  await expect
    .poll(() =>
      page.locator('.runs-pane-header').evaluateAll((headers) => {
        const [left, right] = headers.map((header) => header.getBoundingClientRect())
        return Math.abs(left!.height - right!.height)
      })
    )
    .toBeLessThanOrEqual(1)
  expect(
    await page
      .locator('.app-main')
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
  ).toBe(true)
  expect(api.unexpected).toEqual([])
})

test('record and output disclosure controls work with the keyboard', async ({ page }) => {
  const api = await fixture(page)
  await page.setViewportSize({ width: 1440, height: 850 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="running"]').focus()
  await page.keyboard.press('ArrowDown')
  await expect(page.locator('button[data-run-id="output"]')).toBeFocused()
  await expect(page.locator('.run-detail-content')).toHaveCount(0)
  await page.keyboard.press('Enter')
  await expect(page.locator('.output-code')).toBeVisible()
  await page.locator('.output-item__header').focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('.output-code')).toBeHidden()
  await page.keyboard.press('Enter')
  await expect(page.locator('.output-code')).toBeVisible()
  expect(api.unexpected).toEqual([])
})

test('legacy JSON and link outputs preserve data and use the existing external-browser bridge', async ({
  page,
}) => {
  const api = await fixture(page)
  api.outputs.output = [
    {
      run_id: 'output',
      export_item_id: 'legacy',
      type: 'json',
      json_data: { legacy: true },
      created_at: 1,
    },
    {
      run_id: 'output',
      export_item_id: 'url',
      type: 'url',
      url: 'https://example.test/result',
      created_at: 1,
    },
    {
      run_id: 'output',
      export_item_id: 'invalid',
      type: 'url',
      url: 'javascript:alert(1)',
      created_at: 1,
    },
  ]
  await page.addInitScript(() => {
    const target = window as unknown as {
      electronShell: { openExternal: (url: string) => void }
      externalUrls: string[]
    }
    target.externalUrls = []
    target.electronShell = {
      openExternal: (url) => {
        target.externalUrls.push(url)
      },
    }
  })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="output"]').click()
  await expect(page.locator('.output-code')).toContainText('"legacy": true')
  await page.locator('.output-link').first().click()
  await page.locator('.output-link').last().click()
  expect(
    await page.evaluate(() => (window as unknown as { externalUrls: string[] }).externalUrls)
  ).toEqual(['https://example.test/result'])
  expect(api.unexpected).toEqual([])
})

test('reconnecting through the shared refresh resynchronizes the selected output', async ({
  page,
}) => {
  const api = await fixture(page)
  await page.setViewportSize({ width: 1440, height: 850 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('button[data-run-id="output"]').click()
  await expect(page.locator('.output-code')).toContainText('gomoku')
  await api.disconnect()
  await expect(page.locator('[data-yui-guide-id="runs-ws-disconnected"]')).toBeVisible()
  await expect(page.locator('.output-code')).toContainText('gomoku')
  api.outputs.output![0]!.json = { board: 'changed while disconnected' }
  await page.locator('[data-yui-guide-id="runs-refresh"]').click()
  await expect(page.locator('.output-code')).toContainText('changed while disconnected')
  await expect(page.locator('button[data-run-id="output"]')).toHaveAttribute('aria-pressed', 'true')
  expect(api.unexpected).toEqual([])
})

test('long JSON has compact metadata, searchable folded branches, exact copy and an accessible reading dialog', async ({
  page,
}) => {
  const api = await fixture(page)
  const payload = {
    success: true,
    code: 0,
    data: {
      games: Array.from({ length: 6 }, (_, i) => ({
        id: `game_${i}`,
        name: `Game ${i}`,
        description: 'A real nested game response with longer descriptions and commands.',
        help_commands: {
          primary: {
            command: i === 4 || i === 5 ? 'unique-needle' : `start-${i}`,
            label: '<img src=x onerror=alert(1)>',
          },
        },
        enabled: false,
        version: '0.1.0',
        values: [0, null, ''],
      })),
    },
  }
  api.outputs.output![0]!.json = payload
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          ;(window as unknown as { copiedOutput: string }).copiedOutput = value
        },
      },
    })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('[data-run-id="output"]').click()
  await expect(page.locator('.run-facts')).toBeVisible()
  expect((await page.locator('.output-heading').boundingBox())!.y).toBeLessThan(350)
  await expect(page.locator('.run-metadata')).not.toHaveAttribute('open')
  await expect(page.locator('.output-code')).not.toContainText('unique-needle')
  await page.getByRole('searchbox', { name: 'Find a field or value' }).fill('unique-needle')
  await expect(page.locator('.reader-search__count')).toHaveText('1/2')
  await expect(page.locator('.json-line--active')).toContainText('unique-needle')
  const firstMatch = await page.locator('.json-line--active').getAttribute('data-json-line')
  await page.getByRole('button', { name: 'Next match', exact: true }).click()
  await expect(page.locator('.reader-search__count')).toHaveText('2/2')
  expect(await page.locator('.json-line--active').getAttribute('data-json-line')).not.toBe(
    firstMatch
  )
  await page.getByRole('button', { name: 'Collapse all', exact: true }).click()
  await expect(page.locator('.json-line')).toHaveCount(1)
  await page.getByRole('button', { name: 'Copy', exact: true }).click()
  expect(
    await page.evaluate(() => (window as unknown as { copiedOutput: string }).copiedOutput)
  ).toBe(JSON.stringify(payload, null, 2))
  await page.evaluate(() => {
    navigator.clipboard.writeText = async () => {
      throw new Error('Clipboard denied')
    }
  })
  await page.getByRole('button', { name: /^Cop(y|ied)$/ }).click()
  await expect(page.locator('.el-message--error')).toContainText('Copy failed')
  await expect(page.getByRole('button', { name: 'Copy', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Expand all', exact: true }).click()
  await expect(page.locator('.output-code')).toContainText('<img src=x onerror=alert(1)>')
  await expect(page.locator('.output-code img')).toHaveCount(0)
  await expect(page.locator('.output-code')).toHaveCSS('overflow-y', 'visible')
  await page.getByRole('button', { name: 'Expand reader', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('searchbox').fill('unique-needle')
  await expect(dialog.locator('.json-line--active')).toContainText('unique-needle')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Expand reader', exact: true })).toBeFocused()
  expect(api.unexpected).toEqual([])
})

test('large arrays page their DOM and search still reaches values in later sections', async ({
  page,
}) => {
  const api = await fixture(page)
  api.outputs.output![0]!.json = Array.from({ length: 1600 }, (_, i) => `value-${i}`)
  await page.setViewportSize({ width: 390, height: 850 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('[data-run-id="output"]').click()
  await expect(page.locator('.json-line')).toHaveCount(400)
  await page.getByRole('searchbox', { name: 'Find a field or value' }).fill('value-1599')
  await expect(page.locator('.json-line--active')).toContainText('value-1599')
  expect(await page.locator('.json-line').count()).toBeLessThanOrEqual(400)
  expect(
    await page
      .locator('.app-main')
      .evaluate((element) => element.scrollWidth <= element.clientWidth + 1)
  ).toBe(true)
  await page.getByRole('searchbox').fill('missing-value')
  await expect(page.locator('.reader-search__count')).toHaveText('No matches')
  expect(api.unexpected).toEqual([])
})

test('an identical export refresh and expanded reading preserve the current JSON reading state', async ({
  page,
}) => {
  const api = await fixture(page)
  api.outputs.output![0]!.json = {
    branch: { nested: { leaf: { value: 'preserved-branch' } } },
    values: Array.from({ length: 1600 }, (_, index) =>
      index === 799 || index === 1599 ? 'preserved-match' : `value-${index}`
    ),
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('[data-run-id="output"]').click()
  const reader = page.locator('.output-item').first().locator('.output-reader')
  await reader.getByRole('button', { name: 'Expand leaf', exact: true }).click()
  await expect(reader).toContainText('preserved-branch')
  await reader.getByRole('searchbox').fill('preserved-match')
  await expect(reader.locator('.reader-search__count')).toHaveText('1/2')
  await reader.getByRole('button', { name: 'Next match', exact: true }).click()
  await expect(reader.locator('.reader-search__count')).toHaveText('2/2')
  const activeLine = await reader.locator('.json-line--active').getAttribute('data-json-line')
  const chunk = (await reader.locator('.reader-pages').textContent())!
  expect(chunk).toContain('1601')

  api.outputs.output!.push({
    export_item_id: 'additional-output',
    run_id: 'output',
    type: 'text',
    text: 'A later independent output',
    created_at: Math.floor(Date.now() / 1000),
  })
  api.emit('export', { run_id: 'output' })
  await expect(page.locator('.output-item')).toHaveCount(2)
  await expect(reader.getByRole('searchbox')).toHaveValue('preserved-match')
  await expect(reader.locator('.reader-search__count')).toHaveText('2/2')
  await expect(reader.locator('.json-line--active')).toHaveAttribute('data-json-line', activeLine!)
  await expect(reader.locator('.reader-pages')).toHaveText(chunk)

  await reader.getByRole('button', { name: 'Expand reader', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('searchbox')).toHaveValue('preserved-match')
  await expect(dialog.locator('.reader-search__count')).toHaveText('2/2')
  await expect(dialog.locator('.json-line--active')).toHaveAttribute('data-json-line', activeLine!)
  await expect(dialog.locator('.reader-pages')).toHaveText(chunk)
  await dialog.getByRole('button', { name: 'Previous section', exact: true }).click()
  await expect(dialog.locator('.reader-pages')).not.toHaveText(chunk)
  const browsedChunk = (await dialog.locator('.reader-pages').textContent())!
  await dialog.getByRole('button', { name: 'Close this dialog' }).click()
  await expect(dialog).toHaveCount(0)
  await expect(reader.locator('.reader-search__count')).toHaveText('2/2')
  await expect(reader.locator('.reader-pages')).toHaveText(browsedChunk)
  await reader.getByRole('searchbox').fill('')
  const previous = reader.getByRole('button', { name: 'Previous section', exact: true })
  while (await previous.isEnabled()) await previous.click()
  await expect(reader.getByRole('button', { name: 'Collapse leaf', exact: true })).toHaveAttribute(
    'aria-expanded',
    'true'
  )
  expect(api.unexpected).toEqual([])
})

test('returning to a background run reloads complete terminal details after a partial live event', async ({
  page,
}) => {
  const api = await fixture(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('[data-run-id="running"]').click()
  await expect(page.locator('.run-detail-content')).toContainText('Progress has not been reported')
  await page.locator('[data-run-id="output"]').click()
  await expect(page.locator('.output-code')).toContainText('gomoku')
  const run = api.runs.find((item) => item.run_id === 'running')!
  run.status = 'failed'
  run.started_at = run.created_at + 1
  run.finished_at = run.started_at + 42
  run.updated_at = run.finished_at
  run.error = {
    code: 'BACKGROUND_FAILURE',
    message: 'The background run failed after selection changed.',
  }
  api.emit('runs', {
    run_id: run.run_id,
    status: run.status,
    progress: 1,
    updated_at: run.updated_at,
  })
  await expect(page.locator('[data-run-id="running"]')).toContainText('Failed')
  await page.locator('[data-run-id="running"]').click()
  await expect(page.locator('.run-error')).toContainText(run.error.message)
  await expect
    .poll(
      () =>
        api.requests.filter(
          (request) => request.method === 'run.get' && request.params.run_id === 'running'
        ).length
    )
    .toBe(2)
  for (const [label, timestamp] of [
    ['Started at', run.started_at],
    ['Finished at', run.finished_at],
  ] as const) {
    const formatted = await page.evaluate((timestamp) => {
      const date = new Date(timestamp * 1000)
      const pad = (number: number) => String(number).padStart(2, '0')
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
    }, timestamp)
    await expect(
      page
        .locator('.run-facts > div')
        .filter({ has: page.locator('dt', { hasText: label }) })
        .locator('dd')
    ).toHaveText(formatted)
  }
  expect(api.unexpected).toEqual([])
})

test('oversized JSON defers its outline, copies the complete original and loads searchable bounded rows on demand', async ({
  page,
}) => {
  const api = await fixture(page)
  const payload = Array.from({ length: 11000 }, (_, index) => `large-value-${index}`)
  api.outputs.output![0]!.json = payload
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: async (value: string) => {
          ;(window as unknown as { copiedOutput: string }).copiedOutput = value
        },
      },
    })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${PREVIEW_ORIGIN}/ui/runs`)
  await page.locator('[data-run-id="output"]').click()
  const reader = page.locator('.output-reader')
  await expect(reader.getByRole('button', { name: 'Load JSON outline', exact: true })).toBeVisible()
  await expect(reader.locator('.json-line')).toHaveCount(0)
  await reader.getByRole('button', { name: 'Copy', exact: true }).click()
  expect(
    await page.evaluate(() => (window as unknown as { copiedOutput: string }).copiedOutput)
  ).toBe(JSON.stringify(payload, null, 2))
  await reader.getByRole('button', { name: 'Load JSON outline', exact: true }).click()
  await expect(reader.locator('.json-line')).toHaveCount(400)
  await reader.getByRole('searchbox').fill('large-value-10999')
  await expect(reader.locator('.json-line--active')).toContainText('large-value-10999')
  expect(await reader.locator('.json-line').count()).toBeLessThanOrEqual(400)
  expect(api.unexpected).toEqual([])
})
