<template>
  <el-dialog
    :model-value="visible"
    :title="t('market.pluginDetailTitle')"
    width="min(760px, calc(100vw - 32px))"
    top="16px"
    class="market-plugin-detail-dialog"
    append-to-body
    destroy-on-close
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="loading" class="market-plugin-detail">
      <template v-if="displayPlugin">
        <header class="market-plugin-detail__hero">
          <el-avatar :size="68" :src="displayPlugin.icon_url || displayPlugin.author.avatar || ''">
            {{ displayPlugin.name.slice(0, 1) }}
          </el-avatar>
          <div class="market-plugin-detail__title">
            <div class="market-plugin-detail__name-row">
              <h2>{{ displayPlugin.name }}</h2>
              <el-tag v-if="displayPlugin.is_recommended" size="small" type="warning" effect="plain">
                {{ t('market.recommended') }}
              </el-tag>
              <el-tag v-if="installed" size="small" type="success">{{ t('market.installed') }}</el-tag>
            </div>
            <p>{{ displayPlugin.short_description || displayPlugin.description || t('market.noDescription') }}</p>
            <div class="market-plugin-detail__meta">
              <span><el-icon><User /></el-icon>{{ displayPlugin.author.name || t('market.unknownAuthor') }}</span>
              <span><el-icon><Download /></el-icon>{{ formatCount(displayPlugin.downloads) }}</span>
              <span v-if="displayPlugin.rating_average !== undefined"><el-icon><Star /></el-icon>{{ formatRating(displayPlugin.rating_average) }}</span>
              <span v-if="actionablePlugin.version">v{{ actionablePlugin.version }}</span>
            </div>
          </div>
        </header>

        <div v-if="displayPlugin.tags.length || displayPlugin.zone" class="market-plugin-detail__tags">
          <el-tag v-if="displayPlugin.zone" size="small" type="info">{{ displayPlugin.zone }}</el-tag>
          <el-tag v-for="tag in displayPlugin.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
        </div>

        <el-alert
          v-if="loadFailed"
          class="market-plugin-detail__load-error"
          type="error"
          :title="t('market.detailLoadFailed')"
          :closable="false"
          show-icon
        />

        <el-tabs v-model="activeTab" class="market-plugin-detail__tabs">
          <el-tab-pane :label="t('market.detailReadme')" name="readme">
            <!-- README 经过转义后再渲染有限 Markdown，远端内容不能注入 HTML。 -->
            <div
              v-if="readmeSource"
              class="market-plugin-detail__readme markdown-body"
              v-html="readmeHtml"
              @click="handleReadmeClick"
              @auxclick.middle="handleReadmeClick"
            />
            <el-empty v-else :description="t('market.detailReadmeUnavailable')" :image-size="72" />
          </el-tab-pane>
          <el-tab-pane :label="t('market.detailVersions')" name="versions">
            <div v-if="versions.length" class="market-plugin-detail__versions">
              <article v-for="version in versions" :key="version.id" class="market-plugin-detail__version">
                <div>
                  <strong>v{{ version.version }}</strong>
                  <el-tag size="small" effect="plain">{{ version.channel }}</el-tag>
                  <el-tag v-if="version.is_latest" size="small" type="success">{{ t('market.detailLatest') }}</el-tag>
                </div>
                <time>{{ formatDate(version.created_at) }}</time>
                <p v-if="version.changelog">{{ version.changelog }}</p>
              </article>
            </div>
            <el-empty v-else :description="t('common.noData')" :image-size="72" />
          </el-tab-pane>
        </el-tabs>

        <section class="market-plugin-detail__section market-plugin-detail__facts">
          <span>{{ t('market.detailPublished') }}{{ formatDate(displayPlugin.created_at) }}</span>
          <span>{{ t('market.detailUpdated') }}{{ formatDate(displayPlugin.updated_at) }}</span>
          <span v-if="displayPlugin.rating_count">{{ t('market.detailRatings', { count: displayPlugin.rating_count }) }}</span>
        </section>
      </template>
    </div>

    <template #footer>
      <el-button v-if="actionablePlugin.github_repo" @click="openRepository">
        {{ t('market.detailSource') }}
      </el-button>
      <el-button @click="emit('update:visible', false)">{{ t('common.close') }}</el-button>
      <el-button
        v-if="showUpgrade"
        type="primary"
        :loading="upgrading"
        :disabled="upgrading"
        @click="emit('upgrade', actionablePlugin)"
      >
        {{ upgrading ? t('market.upgrading') : t('market.upgradeTo', { version: actionablePlugin.version }) }}
      </el-button>
      <el-button v-else-if="installed" type="primary" disabled>{{ t('market.installed') }}</el-button>
      <el-button v-else type="primary" :loading="installing" :disabled="installing || !actionablePlugin.has_release" @click="emit('install', actionablePlugin)">
        {{ actionablePlugin.has_release ? (installing ? t('market.installing') : t('market.install')) : t('market.noVersionAvailable') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Download, Star, User } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import {
  fetchMarketPlugin,
  fetchMarketPluginReadme,
  fetchMarketPluginVersions,
  type MarketPlugin,
  type MarketPluginReadme,
  type MarketPluginVersion,
} from '@/api/market'
import type { MarketWorkbenchItem } from '@/composables/useMarketWorkbench'
import { openExternalUrl } from '@/utils/openExternal'
import { resolveMarketReadmeLink } from '@/utils/marketReadmeLink'
import { compareVersion } from '@/utils/version'

interface Props {
  visible: boolean
  plugin: MarketWorkbenchItem
  channel: 'stable' | 'beta'
  installed?: boolean
  localVersion?: string
  installing?: boolean
  upgrading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  installed: false,
  localVersion: undefined,
  installing: false,
  upgrading: false,
})
const emit = defineEmits<{
  'update:visible': [value: boolean]
  install: [plugin: MarketWorkbenchItem]
  upgrade: [plugin: MarketWorkbenchItem]
}>()
const { t } = useI18n()
const loading = ref(false)
const loadFailed = ref(false)
const detail = ref<MarketPlugin | null>(null)
const versions = ref<MarketPluginVersion[]>([])
const repositoryReadme = ref<MarketPluginReadme | null>(null)
const activeTab = ref('readme')
let detailLoadSeq = 0
const displayPlugin = computed(() => detail.value || props.plugin)
const actionablePlugin = computed<MarketWorkbenchItem>(() => ({
  ...props.plugin,
  ...detail.value,
  id: props.plugin.id,
  rawId: props.plugin.rawId,
  searchIndex: props.plugin.searchIndex,
  // 列表请求已按当前 channel 选出可安装版本；详情接口不带 channel
  // 参数，因此不能用其默认 release 覆盖用户选定的版本。
  version: props.plugin.version,
  download_url: props.plugin.download_url,
  latest_channel: props.plugin.latest_channel,
  latest_package_sha256: props.plugin.latest_package_sha256,
  latest_payload_hash: props.plugin.latest_payload_hash,
  latest_published_at: props.plugin.latest_published_at,
  has_release: props.plugin.has_release,
}))
const readmeSource = computed(() => {
  if (repositoryReadme.value) {
    return repositoryReadme.value.availability === 'available' ? repositoryReadme.value.content || '' : ''
  }
  return displayPlugin.value.readme || ''
})
const readmeHtml = computed(() => {
  if (!readmeSource.value) return ''
  try {
    const html = marked.parse(readmeSource.value, {
      async: false,
      gfm: true,
      breaks: true,
    })
    const sanitizedHtml = DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        'a', 'blockquote', 'br', 'code', 'del', 'details', 'div', 'em',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'img', 'input', 'li',
        'ol', 'p', 'pre', 'span', 'strong', 'summary', 'table', 'tbody',
        'td', 'th', 'thead', 'tr', 'ul',
      ],
      ALLOWED_ATTR: [
        'align', 'alt', 'checked', 'class', 'colspan', 'disabled', 'height',
        'href', 'open', 'rowspan', 'src', 'start', 'title', 'type', 'width',
      ],
    })
    return rewriteReadmeUrls(sanitizedHtml)
  } catch {
    return ''
  }
})
const showUpgrade = computed(() =>
  props.installed && !!props.localVersion && !!actionablePlugin.value.version && actionablePlugin.value.has_release
    && compareVersion(props.localVersion, actionablePlugin.value.version) < 0,
)

async function loadDetail() {
  const requestSeq = ++detailLoadSeq
  loading.value = true
  loadFailed.value = false
  detail.value = null
  versions.value = []
  repositoryReadme.value = null
  activeTab.value = 'readme'
  try {
    const [pluginDetail, versionList, readme] = await Promise.all([
      fetchMarketPlugin(props.plugin.rawId),
      fetchMarketPluginVersions(props.plugin.rawId, { channel: props.channel }),
      fetchMarketPluginReadme(props.plugin.rawId),
    ])
    if (requestSeq !== detailLoadSeq) return
    if (!pluginDetail) {
      loadFailed.value = true
      return
    }
    detail.value = pluginDetail
    versions.value = versionList || []
    repositoryReadme.value = readme
  } catch {
    if (requestSeq === detailLoadSeq) loadFailed.value = true
  } finally {
    if (requestSeq === detailLoadSeq) loading.value = false
  }
}

watch(
  () => [props.visible, props.plugin.rawId, props.channel] as const,
  ([visible]) => {
    if (visible) {
      void loadDetail()
    } else {
      // 让关闭时仍在途的请求无法写回状态；下次打开会拥有新的序号。
      detailLoadSeq++
      loading.value = false
      loadFailed.value = false
    }
  },
  { immediate: true },
)

function formatCount(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value || 0)
}

function formatRating(value: number): string {
  return Number.isFinite(value) ? value.toFixed(1) : '-'
}

function formatDate(value?: string): string {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString()
}

function handleReadmeClick(event: MouseEvent) {
  const anchor = (event.target as Element | null)?.closest('a[href]')
  if (!anchor) return
  event.preventDefault()
  const href = anchor.getAttribute('href') || ''
  const url = resolveMarketReadmeLink(
    href,
    repositoryReadme.value?.repository_url || props.plugin.github_repo,
    window.location.origin,
    { sourceRef: repositoryReadme.value?.source_ref },
  )
  // 不可解析或非 HTTP(S) 链接保持在应用内无操作，绝不让浏览器默认导航。
  if (url) openExternalUrl(url)
}

function openRepository() {
  if (actionablePlugin.value.github_repo) openExternalUrl(actionablePlugin.value.github_repo)
}

function rewriteReadmeUrls(html: string): string {
  if (typeof document === 'undefined') return html
  const repositoryUrl = repositoryReadme.value?.repository_url || actionablePlugin.value.github_repo
  const sourceRef = repositoryReadme.value?.source_ref
  const container = document.createElement('div')
  container.innerHTML = html
  for (const anchor of container.querySelectorAll<HTMLAnchorElement>('a[href]')) {
    const url = resolveMarketReadmeLink(anchor.getAttribute('href') || '', repositoryUrl, window.location.origin, { sourceRef })
    if (url) anchor.href = url
    else anchor.removeAttribute('href')
  }
  for (const image of container.querySelectorAll<HTMLImageElement>('img[src]')) {
    const url = resolveMarketReadmeLink(image.getAttribute('src') || '', repositoryUrl, window.location.origin, {
      sourceRef,
      resource: 'image',
    })
    if (url) image.src = url
    else image.removeAttribute('src')
  }
  return container.innerHTML
}
</script>

<style scoped>
.market-plugin-detail { min-height: 140px; }
:global(.market-plugin-detail-dialog.el-dialog) {
  display: flex;
  flex-direction: column;
  height: calc(100dvh - 32px);
  max-height: calc(100dvh - 32px);
  margin: 16px auto !important;
  overflow: hidden;
}
:global(.market-plugin-detail-dialog .el-dialog__body) {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
:global(.market-plugin-detail-dialog .el-dialog__footer) { flex: none; }
.market-plugin-detail__hero { display: flex; align-items: flex-start; gap: 16px; }
.market-plugin-detail__title { min-width: 0; flex: 1; }
.market-plugin-detail__name-row, .market-plugin-detail__meta, .market-plugin-detail__tags { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.market-plugin-detail__name-row h2 { margin: 0; font-size: 21px; word-break: break-word; }
.market-plugin-detail__title > p { margin: 7px 0; color: var(--el-text-color-regular); line-height: 1.55; }
.market-plugin-detail__meta { font-size: 13px; color: var(--el-text-color-secondary); gap: 12px; }
.market-plugin-detail__meta span { display: inline-flex; align-items: center; gap: 4px; }
.market-plugin-detail__tags { margin-top: 16px; }
.market-plugin-detail__tabs { margin-top: 22px; }
.market-plugin-detail__load-error { margin-top: 16px; }
.market-plugin-detail__section { margin-top: 18px; }
.market-plugin-detail__description { margin: 0; white-space: pre-wrap; color: var(--el-text-color-regular); line-height: 1.65; }
.market-plugin-detail__readme { overflow-wrap: anywhere; color: var(--el-text-color-regular); line-height: 1.7; }
.market-plugin-detail__readme :deep(h1), .market-plugin-detail__readme :deep(h2), .market-plugin-detail__readme :deep(h3) { margin: 22px 0 10px; color: var(--el-text-color-primary); line-height: 1.3; }
.market-plugin-detail__readme :deep(h1) { font-size: 30px; }
.market-plugin-detail__readme :deep(h2) { font-size: 22px; }
.market-plugin-detail__readme :deep(h3) { font-size: 17px; }
.market-plugin-detail__readme :deep(p), .market-plugin-detail__readme :deep(ul) { margin: 0 0 12px; }
.market-plugin-detail__readme :deep(ul) { padding-left: 22px; }
.market-plugin-detail__readme :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin: 0 0 12px; }
.market-plugin-detail__readme :deep(th), .market-plugin-detail__readme :deep(td) { padding: 7px 10px; border: 1px solid var(--el-border-color-lighter); text-align: left; }
.market-plugin-detail__readme :deep(img) { max-width: 100%; height: auto; }
.market-plugin-detail__readme :deep(input[type='checkbox']) { margin-right: 6px; }
.market-plugin-detail__readme :deep(code) { padding: 2px 5px; border-radius: 4px; background: var(--el-fill-color-light); font-family: ui-monospace, monospace; }
.market-plugin-detail__readme :deep(pre) { overflow-x: auto; padding: 12px; border-radius: 8px; background: var(--el-fill-color-light); }
.market-plugin-detail__readme :deep(pre code) { padding: 0; background: transparent; }
.market-plugin-detail__readme :deep(blockquote) { margin: 0 0 12px; padding-left: 12px; border-left: 3px solid var(--el-border-color); color: var(--el-text-color-secondary); }
.market-plugin-detail__versions { display: grid; gap: 8px; }
.market-plugin-detail__version { padding: 10px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.market-plugin-detail__version > div { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.market-plugin-detail__version time { display: block; margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; }
.market-plugin-detail__version p { margin: 7px 0 0; white-space: pre-wrap; color: var(--el-text-color-regular); line-height: 1.5; }
.market-plugin-detail__facts { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12px; color: var(--el-text-color-secondary); }
@media (max-width: 520px) { .market-plugin-detail__hero { gap: 12px; } .market-plugin-detail__hero :deep(.el-avatar) { width: 48px !important; height: 48px !important; } }
</style>
