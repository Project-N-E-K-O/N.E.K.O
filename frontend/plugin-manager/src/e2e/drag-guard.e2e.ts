import { expect, test } from '@playwright/test'
import { PREVIEW_ORIGIN, stubCorePluginManagerApis } from './plugin-manager-test-helpers'

test.beforeEach(async ({ page }) => {
  await stubCorePluginManagerApis(page)
})

test('prevents native dragging for dynamically rendered links and images', async ({ page }) => {
  await page.goto(`${PREVIEW_ORIGIN}/ui/`)
  await expect(page.locator('.app-root')).toBeVisible()

  const result = await page.evaluate(() => {
    const link = document.createElement('a')
    link.href = '/plugins'
    link.textContent = 'Plugins'
    const image = document.createElement('img')
    image.alt = ''
    image.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=='
    document.querySelector('.app-root')?.append(link, image)

    const linkDragEvent = new DragEvent('dragstart', { bubbles: true, cancelable: true })
    const imageDragEvent = new DragEvent('dragstart', { bubbles: true, cancelable: true })

    return {
      linkUserDrag: getComputedStyle(link).getPropertyValue('-webkit-user-drag'),
      imageUserDrag: getComputedStyle(image).getPropertyValue('-webkit-user-drag'),
      linkDragAllowed: link.dispatchEvent(linkDragEvent),
      imageDragAllowed: image.dispatchEvent(imageDragEvent),
    }
  })

  expect(result).toEqual({
    linkUserDrag: 'none',
    imageUserDrag: 'none',
    linkDragAllowed: false,
    imageDragAllowed: false,
  })
})
