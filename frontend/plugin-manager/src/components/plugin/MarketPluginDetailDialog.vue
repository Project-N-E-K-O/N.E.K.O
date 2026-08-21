<template>
  <el-dialog
    :model-value="visible"
    :title="displayPlugin.name"
    width="min(760px, calc(100vw - 32px))"
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
              <span v-if="displayPlugin.version">v{{ displayPlugin.version }}</span>
            </div>
          </div>
        </header>

        <div v-if="displayPlugin.tags.length || displayPlugin.zone" class="market-plugin-detail__tags">
          <el-tag v-if="displayPlugin.zone" size="small" type="info">{{ displayPlugin.zone }}</el-tag>
          <el-tag v-for="tag in displayPlugin.tags" :key="tag" size="small" effect="plain">{{ tag }}</el-tag>
        </div>

        <section v-if="displayPlugin.description" class="market-plugin-detail__section">
          <h3>{{ t('market.detailDescription') }}</h3>
          <p class="market-plugin-detail__description">{{ displayPlugin.description }}</p>
        </section>

        <section v-if="displayPlugin.readme" class="market-plugin-detail__section">
          <h3>{{ t('market.detailReadme') }}</h3>
          <!-- 原样作为文本显示，避免远端 README 注入 HTML 到桌面端。 -->
          <pre class="market-plugin-detail__readme">{{ displayPlugin.readme }}</pre>
        </section>

        <section v-if="versions.length" class="market-plugin-detail__section">
          <h3>{{ t('market.detailVersions') }}</h3>
          <div class="market-plugin-detail__versions">
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
        </section>

        <section class="market-plugin-detail__section market-plugin-detail__facts">
          <span>{{ t('market.detailPublished') }}{{ formatDate(displayPlugin.created_at) }}</span>
          <span>{{ t('market.detailUpdated') }}{{ formatDate(displayPlugin.updated_at) }}</span>
          <span v-if="displayPlugin.rating_count">{{ t('market.detailRatings', { count: displayPlugin.rating_count }) }}</span>
        </section>
      </template>
    </div>

    <template #footer>
      <el-button v-if="plugin.github_repo" @click="openRepository">
        {{ t('market.detailSource') }}
      </el-button>
      <el-button @click="emit('update:visible', false)">{{ t('common.close') }}</el-button>
      <el-button
        v-if="showUpgrade"
        type="primary"
        :loading="upgrading"
        :disabled="upgrading"
        @click="emit('upgrade', plugin)"
      >
        {{ upgrading ? t('market.upgrading') : t('market.upgradeTo', { version: plugin.version }) }}
      </el-button>
      <el-button v-else-if="installed" type="primary" disabled>{{ t('market.installed') }}</el-button>
      <el-button v-else type="primary" :loading="installing" :disabled="installing || !plugin.has_release" @click="emit('install', plugin)">
        {{ plugin.has_release ? (installing ? t('market.installing') : t('market.install')) : t('market.noVersionAvailable') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Download, Star, User } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { fetchMarketPlugin, fetchMarketPluginVersions, type MarketPlugin, type MarketPluginVersion } from '@/api/market'
import type { MarketWorkbenchItem } from '@/composables/useMarketWorkbench'
import { openExternalUrl } from '@/utils/openExternal'
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
const detail = ref<MarketPlugin | null>(null)
const versions = ref<MarketPluginVersion[]>([])
const displayPlugin = computed(() => detail.value || props.plugin)
const showUpgrade = computed(() =>
  props.installed && !!props.localVersion && !!props.plugin.version && props.plugin.has_release
    && compareVersion(props.localVersion, props.plugin.version) < 0,
)

async function loadDetail() {
  loading.value = true
  detail.value = null
  versions.value = []
  try {
    const [pluginDetail, versionList] = await Promise.all([
      fetchMarketPlugin(props.plugin.rawId),
      fetchMarketPluginVersions(props.plugin.rawId, { channel: props.channel }),
    ])
    detail.value = pluginDetail
    versions.value = versionList || []
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.visible, props.plugin.rawId, props.channel] as const,
  ([visible]) => {
    if (visible) void loadDetail()
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

function openRepository() {
  if (props.plugin.github_repo) openExternalUrl(props.plugin.github_repo)
}
</script>

<style scoped>
.market-plugin-detail { min-height: 140px; }
.market-plugin-detail__hero { display: flex; align-items: flex-start; gap: 16px; }
.market-plugin-detail__title { min-width: 0; flex: 1; }
.market-plugin-detail__name-row, .market-plugin-detail__meta, .market-plugin-detail__tags { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.market-plugin-detail__name-row h2 { margin: 0; font-size: 21px; word-break: break-word; }
.market-plugin-detail__title > p { margin: 7px 0; color: var(--el-text-color-regular); line-height: 1.55; }
.market-plugin-detail__meta { font-size: 13px; color: var(--el-text-color-secondary); gap: 12px; }
.market-plugin-detail__meta span { display: inline-flex; align-items: center; gap: 4px; }
.market-plugin-detail__tags { margin-top: 16px; }
.market-plugin-detail__section { margin-top: 22px; }
.market-plugin-detail__section h3 { margin: 0 0 8px; font-size: 15px; }
.market-plugin-detail__description { margin: 0; white-space: pre-wrap; color: var(--el-text-color-regular); line-height: 1.65; }
.market-plugin-detail__readme { max-height: 300px; margin: 0; overflow: auto; padding: 12px; border-radius: 8px; background: var(--el-fill-color-light); white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.6 var(--el-font-family); color: var(--el-text-color-regular); }
.market-plugin-detail__versions { display: grid; gap: 8px; }
.market-plugin-detail__version { padding: 10px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.market-plugin-detail__version > div { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.market-plugin-detail__version time { display: block; margin-top: 4px; color: var(--el-text-color-secondary); font-size: 12px; }
.market-plugin-detail__version p { margin: 7px 0 0; white-space: pre-wrap; color: var(--el-text-color-regular); line-height: 1.5; }
.market-plugin-detail__facts { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12px; color: var(--el-text-color-secondary); }
@media (max-width: 520px) { .market-plugin-detail__hero { gap: 12px; } .market-plugin-detail__hero :deep(.el-avatar) { width: 48px !important; height: 48px !important; } }
</style>
