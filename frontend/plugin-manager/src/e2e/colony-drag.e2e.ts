import { expect, test } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

// The colony of the home page in a real browser: what a pointer does there goes through the
// browser's own capture of it, which a made-up DOM does not have.

const card = (over: Record<string, unknown> = {}) => ({
  id: 'lead', name: 'Blog', kind: 'project', lanlan_name: 'Lan', runtime: 'builtin', workdir: 'D:/blog',
  preset: 'boss', avatar: 'boss', label: '', description: '', title: '', duty: '', duty_by: '', doing: '',
  state: 'idle', leader: true, parent: null, model: 'agent-model', commission: null, ...over,
})
const project = (name: string, member: Record<string, unknown>) => ({
  name, description: '', workdir: `D:/${name}`, archived: false, done_today: 0, members: [member],
})

test('the gate closes when the project of the familiar that is held is put away elsewhere', async ({ page }) => {
  await stubCorePluginManagerApis(page)
  // Two projects at first; then only the other one, as when the first was put away in another window.
  let blogIsThere = true
  await page.route('**/console/board*', (route) =>
    route.fulfill({
      json: {
        success: true,
        revision: blogIsThere ? 'with-blog' : 'without-blog',
        projects: [
          ...(blogIsThere ? [project('Blog', card())] : []),
          project('Notes', card({ id: 'notes', name: 'Notes' })),
        ],
        loose: [],
      },
    })
  )
  await page.route('**/console/waits*', (route) => route.fulfill({ json: { success: true, waits: [], more: false } }))

  await page.goto(`${PREVIEW_ORIGIN}/ui/`)
  const held = page.locator('[data-cat="lead"]')
  await expect(held).toBeVisible()
  const gate = page.getByTestId('colony-gate')
  await expect(gate).not.toHaveClass(/(^|\s)on(\s|$)/)

  const box = await held.boundingBox()
  if (!box) throw new Error('the familiar to pick up has no place on the page')
  const x = box.x + box.width / 2
  const y = box.y + box.height / 2
  await page.mouse.move(x, y)
  await page.mouse.down()
  await page.mouse.move(x + 40, y + 30, { steps: 5 })
  await expect(gate).toHaveClass(/(^|\s)on(\s|$)/)

  // Its project goes while it is held: the next reading of the board takes the territory away.
  blogIsThere = false
  await expect(page.locator('[data-territory="project:Blog"]')).toHaveCount(0, { timeout: 10_000 })
  await expect(page.locator('[data-territory="project:Notes"]')).toBeVisible()
  await expect(gate).not.toHaveClass(/(^|\s)on(\s|$)/)
  await page.mouse.up()
  // Nothing is asked about a project that is not there, and the other familiar can be picked up as before.
  await expect(page.getByTestId('colony-part-confirm')).toHaveCount(0)
  const other = page.locator('[data-cat="notes"]')
  const place = await other.boundingBox()
  if (!place) throw new Error('the other familiar has no place on the page')
  await page.mouse.move(place.x + place.width / 2, place.y + place.height / 2)
  await page.mouse.down()
  await page.mouse.move(place.x + place.width / 2 + 40, place.y + place.height / 2 + 30, { steps: 5 })
  await expect(gate).toHaveClass(/(^|\s)on(\s|$)/)
  await page.mouse.up()
  await expect(gate).not.toHaveClass(/(^|\s)on(\s|$)/)
})
