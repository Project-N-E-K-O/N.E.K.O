import { expect, type Page } from '@playwright/test'
import type { MarketMessageDraft } from '../api/marketConversations'
import { appearanceTest, PREVIEW_ORIGIN } from './plugin-manager-test-helpers'

const PROFILE_ID = '123e4567-e89b-42d3-a456-426614174000'
const MARKET = 'https://market.example.test'
const ISSUE_URL = `${MARKET}/#/plugin/42?tab=issues&issue=71`
const FEEDBACK_ROUTE = `${PREVIEW_ORIGIN}/ui/workbench/feedback`
const CATALOG = '**/market/catalog/api/v1/plugins'
const CONVERSATION = '**/market/conversations/plugins/42'

const issue = {
  kind: 'issue',
  plugin_id: 42,
  plugin_name: 'Example Plugin',
  plugin_listed: true,
  thread_id: 71,
  issue_title: 'Cannot load the plugin',
  message_id: 193,
  body: 'Feedback details',
  created_at: '2026-11-17T03:30:00Z',
  author: {
    id: 23,
    username: 'example',
    display_name: null,
    avatar_url: null,
    community_profile_id: PROFILE_ID,
  },
  unread: true,
  awaiting_reply: true,
}

const plugin = {
  id: 42,
  slug: 'example-plugin',
  name: issue.plugin_name,
  description: '',
  author_name: 'Example author',
  created_at: issue.created_at,
  updated_at: issue.created_at,
}
const original = {
  id: 181,
  author: issue.author,
  reply_to: null,
  body: 'Original comment',
  is_hidden: false,
  created_at: issue.created_at,
  format: 'plain',
}

async function stubMarketPluginCatalog(page: Page) {
  await page.route(`${CATALOG}?*`, (route) =>
    route.fulfill({ json: { items: [plugin], total: 1, page: 1, page_size: 12 } })
  )
  await page.route(`${CATALOG}/42`, (route) => route.fulfill({ json: plugin }))
  await page.route(`${CATALOG}/42/versions?*`, (route) => route.fulfill({ json: { items: [] } }))
  await page.route(`${CATALOG}/42/readme`, (route) =>
    route.fulfill({ json: { availability: 'unavailable' } })
  )
}

async function openMarketComments(page: Page) {
  const card = page.locator('.market-plugin-card').filter({ hasText: plugin.name })
  await card.getByTestId('plugin-details-button').click()
  const dialog = page.getByRole('dialog', { name: '插件详情' })
  await dialog.getByRole('tab', { name: '评论', exact: true }).click()
  return dialog
}

const replyBody = 'Comment with an uncertain send result'
const previousAccountWarning = '这段内容可能已经用之前的账号发出，再次发送前请先在上方对话里确认。'

async function sendReplyWithUncertainResult(page: Page) {
  let account = 'account-a'
  const attempts: { session: string | undefined; draft: MarketMessageDraft }[] = []
  await stubMarketPluginCatalog(page)
  await page.route('**/market/feedback/avatars', (route) => route.fulfill({ json: {} }))
  await page.route(`${CONVERSATION}/comments`, (route) =>
    route.fulfill({
      headers: { 'X-Market-Feedback-Session': account },
      json: { messages: [original], next_cursor: null, can_reply: true },
    })
  )
  await page.route(`${CONVERSATION}/comments/messages`, (route) => {
    const request = route.request()
    const session = request.headers()['x-market-feedback-session']
    const draft = request.postDataJSON() as MarketMessageDraft
    attempts.push({ session, draft })
    if (attempts.length === 1) {
      return route.fulfill({ status: 502, json: { code: 'market_feedback_unavailable' } })
    }
    return route.fulfill({
      status: 201,
      json: { ...original, id: 182, body: draft.body, reply_to: original },
    })
  })

  await page.goto(`${PREVIEW_ORIGIN}/ui/market`)
  const dialog = await openMarketComments(page)
  await dialog.getByRole('button', { name: '回复', exact: true }).click()
  await dialog.getByRole('textbox').fill(replyBody)
  await dialog.getByRole('button', { name: '发送', exact: true }).click()
  await expect(dialog.getByText('发送留言失败。')).toBeVisible()
  expect(attempts).toEqual([
    {
      session: 'account-a',
      draft: {
        body: replyBody,
        reply_to_id: original.id,
        client_message_id: expect.stringMatching(/.+/),
      },
    },
  ])

  async function switchAccount() {
    account = 'account-b'
    await page.evaluate(() => window.dispatchEvent(new Event('neko-market-auth-changed')))
  }

  return { dialog, attempts, switchAccount }
}

appearanceTest.use({ appLocale: 'zh-CN' })

appearanceTest(
  'posts comments and handles issues from the Market plugin card',
  async ({ page }) => {
    const conversationHeaders = { 'X-Market-Feedback-Session': 'account-a' }
    let waiting = true
    let open = true
    let nextMessageCursor: number | null = null
    const issueSummary = () => ({
      id: 71,
      title: issue.issue_title,
      state: open ? 'open' : 'closed',
      is_hidden: false,
      capabilities: { can_reply: open, can_close: open, can_reopen: !open },
    })
    const issueDetail = () => ({
      ...issueSummary(),
      messages: [{ ...original, id: issue.message_id, body: issue.body }],
      next_message_cursor: nextMessageCursor,
    })
    await stubMarketPluginCatalog(page)
    const avatarUrl = `${PREVIEW_ORIGIN}/ui/avatar-fixture.png`
    await page.route(avatarUrl, (route) =>
      route.fulfill({
        contentType: 'image/png',
        body: Buffer.from(
          'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN/YAAAAASUVORK5CYII=',
          'base64'
        ),
      })
    )
    await page.route('**/market/feedback/avatars', (route) =>
      route.fulfill({ json: { [PROFILE_ID]: avatarUrl } })
    )
    await page.route('**/market/feedback?*', (route) =>
      route.fulfill({
        headers: conversationHeaders,
        json: {
          items: [],
          next_cursor: null,
          unread_total: Number(waiting),
          awaiting_reply_total: Number(waiting),
          by_plugin: [
            { plugin_id: 42, unread_total: Number(waiting), awaiting_reply_total: Number(waiting) },
          ],
        },
      })
    )
    await page.route(`${CONVERSATION}/comments`, (route) =>
      route.fulfill({
        headers: conversationHeaders,
        json: { messages: [original], next_cursor: null, can_reply: true },
      })
    )
    await page.route(`${CONVERSATION}/issues`, (route) =>
      route.fulfill({
        headers: conversationHeaders,
        json: { items: [issueSummary()], next_cursor: null },
      })
    )
    await page.route(`${CONVERSATION}/issues/71`, (route) =>
      route.fulfill({
        headers: conversationHeaders,
        json: issueDetail(),
      })
    )
    const submittedComments: MarketMessageDraft[] = []
    await page.route(`${CONVERSATION}/comments/messages`, (route) => {
      expect(route.request().headers()['x-market-feedback-session']).toBe('account-a')
      const payload = route.request().postDataJSON()
      submittedComments.push(payload)
      if (submittedComments.length === 1) {
        return route.fulfill({ status: 502, json: { code: 'market_feedback_unavailable' } })
      }
      return route.fulfill({
        status: 201,
        json: {
          ...original,
          id: 182,
          body: payload.body,
          reply_to: { ...original, id: original.id },
        },
      })
    })
    await page.route(`${CONVERSATION}/issues/71/messages`, (route) => {
      expect(route.request().headers()['x-market-feedback-session']).toBe('account-a')
      const payload = route.request().postDataJSON()
      expect(payload.body).toBe('Issue reply from the desktop')
      waiting = false
      return route.fulfill({ status: 201, json: { ...original, id: 194, body: payload.body } })
    })
    await page.route(`${CONVERSATION}/issues/71/messages?after_id=${issue.message_id}`, (route) =>
      route.fulfill({
        headers: conversationHeaders,
        json: {
          items: [
            { ...original, id: 194, body: 'Issue reply from the desktop' },
            { ...original, id: 195, body: 'Another participant replied before close' },
          ],
          next_cursor: null,
        },
      })
    )
    for (const action of ['close', 'reopen']) {
      await page.route(`${CONVERSATION}/issues/71/${action}`, (route) => {
        expect(route.request().headers()['x-market-feedback-session']).toBe('account-a')
        open = action === 'reopen'
        nextMessageCursor = issue.message_id
        return route.fulfill({ json: issueDetail() })
      })
    }

    await page.goto(`${PREVIEW_ORIGIN}/ui/market`)
    const card = page.locator('.market-plugin-card').filter({ hasText: issue.plugin_name })
    const badge = card.getByTestId('market-awaiting-reply')
    await expect(badge).toHaveText('待回复 1')
    const dialog = await openMarketComments(page)
    await expect(dialog.locator(`img[src="${avatarUrl}"]`)).toHaveJSProperty('naturalWidth', 1)
    await dialog.getByRole('button', { name: '回复', exact: true }).click()
    await dialog.getByRole('textbox').fill('Comment from the desktop')
    await dialog.getByRole('button', { name: '发送', exact: true }).click()
    await expect(dialog.getByText('发送留言失败。')).toBeVisible()
    await expect(dialog.getByRole('textbox')).toHaveValue('Comment from the desktop')
    await dialog.getByRole('button', { name: '发送', exact: true }).click()
    await expect(dialog.getByText('Comment from the desktop', { exact: true })).toBeVisible()
    expect(submittedComments).toHaveLength(2)
    expect(submittedComments[0]).toEqual(submittedComments[1])
    expect(submittedComments[0]?.reply_to_id).toBe(181)

    await dialog.getByRole('tab', { name: '问题反馈' }).click()
    await dialog.getByRole('button', { name: /Cannot load the plugin/ }).click()
    await dialog.getByRole('textbox').fill('Issue reply from the desktop')
    await dialog.getByRole('button', { name: '发送', exact: true }).click()
    await expect(dialog.getByText('Issue reply from the desktop', { exact: true })).toBeVisible()
    await expect(badge).toHaveCount(0)
    await dialog.getByRole('button', { name: '关闭 Issue', exact: true }).click()
    await expect(dialog.getByText('已关闭', { exact: true })).toBeVisible()
    const loadMore = dialog.getByRole('button', { name: '加载更多', exact: true })
    await expect(loadMore).toBeVisible()
    await loadMore.click()
    await expect(dialog.getByText('Another participant replied before close')).toBeVisible()
    await expect(dialog.getByText('Issue reply from the desktop', { exact: true })).toHaveCount(1)
    await expect(loadMore).toHaveCount(0)
    await dialog.getByRole('button', { name: '重新打开 Issue', exact: true }).click()
    await expect(dialog.getByText('开放中', { exact: true })).toBeVisible()
  }
)

appearanceTest(
  'reuses the original reply ID after reopening the Market comment form',
  async ({ page }) => {
    const { dialog, attempts } = await sendReplyWithUncertainResult(page)
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(dialog).toBeHidden()
    await openMarketComments(page)

    await expect(dialog.getByRole('textbox')).toHaveValue(replyBody)
    await expect(dialog.getByText('回复 example', { exact: true })).toBeVisible()
    await expect(dialog.getByText(previousAccountWarning)).toHaveCount(0)
    await dialog.getByRole('button', { name: '发送', exact: true }).click()
    await expect(dialog.getByText(replyBody, { exact: true })).toBeVisible()
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
  }
)

appearanceTest(
  'warns after a Market account switch and uses a fresh ID for a manual resend',
  async ({ page }) => {
    const { dialog, attempts, switchAccount } = await sendReplyWithUncertainResult(page)
    await switchAccount()

    await expect(dialog.getByRole('textbox')).toHaveValue(replyBody)
    await expect(dialog.getByText('回复 example', { exact: true })).toBeVisible()
    await expect(dialog.getByText(previousAccountWarning)).toBeVisible()
    expect(attempts).toHaveLength(1)

    await dialog.getByRole('button', { name: '发送', exact: true }).click()
    await expect(dialog.getByText(replyBody, { exact: true })).toBeVisible()
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual({
      session: 'account-b',
      draft: {
        body: replyBody,
        reply_to_id: original.id,
        client_message_id: expect.stringMatching(/.+/),
      },
    })
    expect(attempts[1]?.draft.client_message_id).not.toBe(attempts[0]?.draft.client_message_id)
  }
)

appearanceTest(
  'opens feedback from the workbench, renders plugin badges and marks through the shown message',
  async ({ page, context }) => {
    await page.route('**/plugin-projects', (route) => route.fulfill({ json: [] }))
    await page.route('**/market/bridge-token', (route) =>
      route.fulfill({ json: { bridge_token: 'local-bridge' } })
    )
    await page.route('**/market/feedback/avatars', (route) => route.fulfill({ json: {} }))
    await context.route('https://market.example.test/**', (route) =>
      route.fulfill({
        contentType: 'text/html',
        body: '<title>Market thread fixture</title>',
      })
    )
    let unread = true
    const mutations: unknown[] = []
    await page.route(
      (url) => url.pathname === '/market/feedback',
      (route) =>
        route.fulfill({
          headers: { 'X-Market-Feedback-Session': 'account-a', 'X-Market-Web-Url': MARKET },
          json: {
            items: [
              { ...issue, unread },
              {
                ...issue,
                plugin_id: 77,
                plugin_name: 'Unlisted Plugin',
                thread_id: 72,
                plugin_listed: false,
              },
            ],
            next_cursor: null,
            unread_total: Number(unread),
            awaiting_reply_total: 1,
            by_plugin: [{ plugin_id: 42, unread_total: Number(unread), awaiting_reply_total: 1 }],
          },
        })
    )
    await page.route('**/market/feedback/mark-read', async (route) => {
      const request = route.request()
      mutations.push({
        body: request.postDataJSON(),
        session: request.headers()['x-market-feedback-session'],
        authorization: request.headers().authorization,
        query: new URL(request.url()).search,
      })
      unread = false
      await route.fulfill({ status: 204 })
    })

    await page.goto(`${PREVIEW_ORIGIN}/ui/workbench`)
    await page.getByRole('button', { name: '我的反馈', exact: true }).click()
    await expect(page).toHaveURL(FEEDBACK_ROUTE)
    const listed = page.locator('.feedback-group').filter({ hasText: 'Example Plugin' })
    await expect(listed.getByText('待回复 1')).toBeVisible()
    const history = page.locator('.feedback-group').filter({ hasText: 'Unlisted Plugin' })
    await expect(history.getByText('已下架 · 原帖不可打开')).toBeVisible()
    await expect(history.getByRole('link')).toHaveCount(0)
    const issueLink = listed.getByRole('link', { name: '在市场打开' })
    await expect(issueLink).toHaveAttribute('href', ISSUE_URL)
    const [marketTab] = await Promise.all([context.waitForEvent('page'), issueLink.click()])
    await expect(marketTab).toHaveURL(ISSUE_URL)
    await marketTab.close()

    await listed.getByRole('button', { name: '标为已读', exact: true }).click()
    // click() resolves before the request reaches the route handler.
    await expect.poll(() => mutations).toEqual([
      {
        body: { thread_kind: 'issue', thread_id: 71, through_message_id: 193 },
        session: 'account-a',
        authorization: 'Bearer local-bridge',
        query: '',
      },
    ])
    await expect(listed.getByRole('button', { name: '标为已读', exact: true })).toHaveCount(0)
    await expect(listed.getByText('待回复 1')).toBeVisible()
  }
)

appearanceTest(
  'uses the Electron browser bridge and reloads feedback when the Market account changes',
  async ({ page, context }) => {
    await page.addInitScript(() => {
      const host = window as unknown as {
        externalFeedbackUrl?: string
        electronShell: { openExternal: (url: string) => void }
      }
      host.electronShell = {
        openExternal: (url) => {
          host.externalFeedbackUrl = url
        },
      }
    })
    await page.route('**/market/bridge-token', (route) =>
      route.fulfill({ json: { bridge_token: 'local-bridge' } })
    )
    await page.route('**/market/feedback/avatars', (route) => route.fulfill({ json: {} }))
    let account = 'account-a'
    await page.route(
      (url) => url.pathname === '/market/feedback',
      (route) =>
        route.fulfill({
          headers: { 'X-Market-Feedback-Session': account, 'X-Market-Web-Url': MARKET },
          json: {
            items: account === 'account-a' ? [issue] : [],
            next_cursor: null,
            unread_total: Number(account === 'account-a'),
            awaiting_reply_total: Number(account === 'account-a'),
            by_plugin: [],
          },
        })
    )
    await page.route('**/market/feedback/mark-read', async (route) => {
      expect(route.request().headers()['x-market-feedback-session']).toBe('account-a')
      expect(route.request().postDataJSON()).toEqual({ all: true })
      account = 'account-b'
      await route.fulfill({
        status: 409,
        json: { detail: { code: 'market_feedback_session_changed' } },
      })
    })

    await page.goto(FEEDBACK_ROUTE)
    await page.getByRole('link', { name: '在市场打开' }).click()
    await expect
      .poll(() =>
        page.evaluate(
          () => (window as unknown as { externalFeedbackUrl?: string }).externalFeedbackUrl
        )
      )
      .toBe(ISSUE_URL)
    expect(context.pages()).toHaveLength(1)
    await page.getByRole('button', { name: '全部标为已读' }).click()
    await expect(page.getByText('没有符合条件的反馈。')).toBeVisible()
    await expect(page.getByText('Cannot load the plugin')).toHaveCount(0)
  }
)
