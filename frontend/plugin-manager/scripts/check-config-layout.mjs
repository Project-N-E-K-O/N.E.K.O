/**
 * Browser-surface regression check. Run through the in-app Browser runtime:
 *   const { checkConfigLayout } = await import('/absolute/path/to/this/file');
 *   await checkConfigLayout(tab, await browser.capabilities.get('viewport'), cases);
 *
 * Requires the web_search configuration tab, Chinese UI, and a clean draft.
 * It edits only a temporary draft, never saves/reloads a plugin, and undoes its
 * changes in finally. Viewport overrides are also reset. Each case uses the CSS
 * viewport equivalent of desktop zoom; this does not test native browser zoom UI.
 */
export async function checkConfigLayout(tab, viewport, cases) {
  const dirty = await tab.playwright.locator('.status-dot.dirty').count()
  if (dirty) throw new Error('Use a clean draft; refusing to discard existing edits')
  const originalValue = await tab.playwright.evaluate(
    () => document.querySelector('input[aria-label="search.max_results"]').value
  )
  const draftValue = originalValue === '9' ? '10' : '9'
  const results = []
  let edited = false
  try {
    await tab.playwright
      .getByRole('spinbutton', { name: 'search.max_results', exact: true })
      .fill(draftValue)
    edited = true
    for (const { width, height, zoom = 1 } of cases) {
      await viewport.set({ width: Math.floor(width / zoom), height: Math.floor(height / zoom) })
      // ResizeObserver mode changes can take a frame. Require settled geometry,
      // not an arbitrary screenshot immediately after a viewport command.
      let previous = '',
        stable = 0
      for (let attempt = 0; attempt < 20 && stable < 2; attempt++) {
        const geometry = JSON.stringify(
          await tab.playwright.evaluate(() => {
            const editor = document.querySelector('.plugin-config-editor'),
              pane = document.querySelector('.config-content')
            return [
              editor.className,
              pane.clientHeight,
              pane.getBoundingClientRect().top,
              document.querySelector('.app-main').scrollTop,
            ]
          })
        )
        stable = geometry === previous ? stable + 1 : 0
        previous = geometry
        if (stable < 2) await new Promise((resolve) => setTimeout(resolve, 50))
      }
      if (stable < 2) throw new Error('Layout did not settle after resizing')
      // Exercise real focus/scroll behavior rather than only measuring offscreen DOM.
      for (const name of ['search.max_results', 'search.duckduckgo_fallback_delay_seconds']) {
        await tab.playwright.getByRole('spinbutton', { name, exact: true }).click()
        const field = await tab.playwright.evaluate((label) => {
          const input = document.querySelector(`input[aria-label="${label}"]`)
          const rect = input.getBoundingClientRect()
          let top = 0,
            bottom = innerHeight,
            left = 0,
            right = innerWidth
          for (let parent = input.parentElement; parent; parent = parent.parentElement) {
            const style = getComputedStyle(parent),
              r = parent.getBoundingClientRect()
            if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) {
              top = Math.max(top, r.top)
              bottom = Math.min(bottom, r.bottom)
            }
            if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) {
              left = Math.max(left, r.left)
              right = Math.min(right, r.right)
            }
          }
          return {
            visible:
              rect.top >= top - 2 &&
              rect.bottom <= bottom + 2 &&
              rect.left >= left - 2 &&
              rect.right <= right + 2,
            width: rect.width,
          }
        }, name)
        if (!field.visible || field.width < 40)
          throw new Error(
            `Unreachable field ${name}: ${JSON.stringify({ width, height, zoom, field })}`
          )
      }
      // Focus without invoking the save action.
      await tab.playwright
        .getByRole('button', { name: '保存方案', exact: true })
        .press('ArrowRight')
      const state = await tab.playwright.evaluate(() => {
        const editor = document.querySelector('.plugin-config-editor')
        const pane = document.querySelector('.config-content')
        const save = [...document.querySelectorAll('.config-footer button')].find(
          (b) => b.textContent.trim() === '保存方案'
        )
        const r = save.getBoundingClientRect()
        const overflow = [
          ...document.querySelectorAll('.app-main,.config-toolbar,.config-content,.config-footer'),
        ]
          .filter((e) => e.scrollWidth > e.clientWidth + 2)
          .map((e) => e.className)
        return {
          mode: editor.classList.contains('page-scroll') ? 'page' : 'editor',
          editorHeight: pane.clientHeight,
          overflow,
          saveVisible: r.top >= 0 && r.bottom <= innerHeight + 2 && r.right <= innerWidth + 2,
          draftValue: document.querySelector('input[aria-label="search.max_results"]').value,
        }
      })
      results.push({ width, height, zoomEquivalent: zoom, ...state })
      if (state.overflow.length || !state.saveVisible || state.draftValue !== draftValue)
        throw new Error(`Layout failed: ${JSON.stringify(results.at(-1))}`)
    }
    return results
  } finally {
    try {
      if (edited)
        await tab.playwright
          .getByRole('button', { name: '撤销全部未保存修改', exact: true })
          .click()
    } finally {
      await viewport.reset()
    }
  }
}
