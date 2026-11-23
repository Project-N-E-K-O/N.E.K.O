// @vitest-environment happy-dom

import { afterEach, describe, expect, it } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import SourceDetailRow from './SourceDetailRow.vue'
import en from '@/i18n/locales/en-US'
import es from '@/i18n/locales/es'
import ja from '@/i18n/locales/ja'
import ko from '@/i18n/locales/ko'
import pt from '@/i18n/locales/pt'
import ru from '@/i18n/locales/ru'
import zhCN from '@/i18n/locales/zh-CN'
import zhTW from '@/i18n/locales/zh-TW'
import type { PluginInstallSource } from '@/types/api'

const messages = { 'en-US': en, es, ja, ko, pt, ru, 'zh-CN': zhCN, 'zh-TW': zhTW }
const mountedApps: Array<() => void> = []

async function mountSourceDetailRow(
  installSource: PluginInstallSource,
  latestVersion: string | null = null,
) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const app = createApp(SourceDetailRow, { installSource, latestVersion })
  app.use(createI18n({ legacy: false, locale: 'en-US', messages }))
  const passthrough = defineComponent({
    setup(_props, { slots }) {
      return () => h('span', [slots.default?.()])
    },
  })
  app.component('el-icon', passthrough)
  app.mount(container)
  mountedApps.push(() => {
    app.unmount()
    container.remove()
  })
  await nextTick()
  return container
}

afterEach(() => {
  while (mountedApps.length) mountedApps.pop()?.()
})

describe('SourceDetailRow null detail handling', () => {
  it('renders market source with null source_detail without throwing', async () => {
    const container = await mountSourceDetailRow({
      source: 'market',
      reason: null,
      installed_at: '2026-11-20T00:00:00Z',
      source_detail: null,
    }, '2.0.0')

    const bar = container.querySelector('.source-detail-bar')
    expect(bar).not.toBeNull()
    const cells = container.querySelectorAll('.source-cell')
    expect(cells).toHaveLength(1)
    expect(cells[0]?.textContent).toContain(en.plugins.installSource.labels.installedAt)
  })

  it('renders imported source with null source_detail without throwing', async () => {
    const container = await mountSourceDetailRow({
      source: 'imported',
      reason: null,
      installed_at: null,
      source_detail: null,
    })

    const bar = container.querySelector('.source-detail-bar')
    expect(bar).not.toBeNull()
    const cells = container.querySelectorAll('.source-cell')
    expect(cells).toHaveLength(1)
    expect(cells[0]?.textContent).toContain('—')
  })

  it('renders full details when market source_detail is populated', async () => {
    const container = await mountSourceDetailRow({
      source: 'market',
      reason: null,
      installed_at: '2026-11-20T00:00:00Z',
      source_detail: {
        plugin_market_id: 'demo-market-id',
        version: '1.0.0',
        channel: 'beta',
        package_url: 'https://example.com/pkg.neko-plugin',
        package_sha256: '0123456789abcdef0123456789abcdef',
        previous_version: '0.9.0',
      },
    }, '1.2.0')

    const text = container.textContent || ''
    expect(text).toContain('demo-market-id')
    expect(text).toContain('1.0.0')
    expect(text).toContain('0.9.0')
    expect(text).toContain('1.2.0')
    expect(text).toContain('01234567…')
  })

  it('renders full details when imported source_detail is populated', async () => {
    const container = await mountSourceDetailRow({
      source: 'imported',
      reason: null,
      installed_at: '2026-11-20T00:00:00Z',
      source_detail: {
        package_filename: 'demo-plugin.neko-plugin',
        package_sha256: 'fedcba9876543210fedcba9876543210',
      },
    })

    const text = container.textContent || ''
    expect(text).toContain('demo-plugin.neko-plugin')
    expect(text).toContain('fedcba98…')
  })
})
