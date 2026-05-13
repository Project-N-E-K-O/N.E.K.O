<template>
  <div
    class="market-panel"
    :class="{ 'market-panel--embedded': embedded }"
    data-yui-guide-id="market-panel-root"
  >
    <div
      v-if="embedded"
      class="market-panel__heading"
      data-yui-guide-id="market-panel-heading"
    >
      <div class="market-panel__heading-copy">
        <div class="market-panel__heading-title">
          <el-icon><ShoppingCart /></el-icon>
          <span>{{ t('market.title') }}</span>
        </div>
        <span class="market-panel__heading-hint">{{ t('market.subtitle') }}</span>
      </div>
      <div class="market-panel__heading-actions">
        <button
          v-if="marketBaseUrl"
          class="market-panel__icon-btn"
          :title="t('market.openInBrowser')"
          @click="openMarketExternal"
        >
          <el-icon><Link /></el-icon>
        </button>
        <el-button text circle @click="$emit('close')">
          <el-icon><Close /></el-icon>
        </el-button>
      </div>
    </div>

    <WorkbenchFilterBar
      v-model:filter-text="filterText"
      v-model:use-regex="useRegex"
      v-model:filter-mode="filterMode"
      :regex-error="regexError"
      :rule-groups="filterRuleGroups"
      :placeholder="t('market.searchPlaceholder')"
      :rules-trigger-label="t('market.filterRules')"
      :rules-title="t('market.filterRulesTitle')"
      :rules-hint="t('market.filterRulesHint')"
      :whitelist-label="t('plugins.filterWhitelist')"
      :blacklist-label="t('plugins.filterBlacklist')"
      :invalid-regex-label="t('plugins.invalidRegex')"
    />

    <WorkbenchToolbar class="market-panel__toolbar">
      <WorkbenchGroupFilter
        v-model:selected-ids="selectedGroupIds"
        :choices="groupChoices"
        :counts="groupCounts"
        selection-mode="single"
      />
      <div class="market-panel__toolbar-right">
        <el-select
          v-model="sortBy"
          size="small"
          class="market-panel__sort"
          @change="onSortChange"
        >
          <el-option
            v-for="opt in sortOptions"
            :key="opt.value"
            :value="opt.value"
            :label="opt.label"
          />
        </el-select>
        <WorkbenchLayoutSwitcher
          v-model:layout-mode="layoutMode"
          :choices="layoutChoices"
        />
      </div>
    </WorkbenchToolbar>

    <div class="market-panel__content">
      <EmptyState
        v-if="!marketAvailable && !loading"
        :description="t('market.notConfigured')"
      >
        <template #description>
          <p>{{ t('market.notConfigured') }}</p>
          <p class="market-panel__empty-hint">{{ t('market.configHint') }}</p>
        </template>
      </EmptyState>

      <LoadingSpinner
        v-else-if="loading && plugins.length === 0"
        :loading="true"
        :text="t('common.loading')"
      />

      <EmptyState
        v-else-if="filteredItems.length === 0"
        :description="t('market.noResults')"
      />

      <template v-else>
        <GridSection
          :title="activeGroupLabel"
          :items="filteredItems"
          :layout-mode="layoutMode"
          :multi-select-enabled="false"
          :selected-ids="[]"
          variant="default"
          guide-prefix="market-panel"
        >
          <template #item="{ item }">
            <MarketPluginCard
              :plugin="item"
              :installed="isInstalled(item)"
              :installing="installingId === item.id"
              @click="handlePluginClick(item)"
              @install="handleInstall(item)"
            />
          </template>
        </GridSection>

        <div v-if="totalPages > 1" class="market-panel__pagination">
          <el-pagination
            v-model:current-page="currentPage"
            :page-size="pageSize"
            :total="totalCount"
            :small="embedded"
            layout="prev, pager, next, total"
            @current-change="handlePageChange"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ShoppingCart, Close, Link } from '@element-plus/icons-vue'
import MarketPluginCard from '@/components/plugin/MarketPluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import WorkbenchFilterBar from '@/components/common/WorkbenchFilterBar.vue'
import WorkbenchGroupFilter from '@/components/common/WorkbenchGroupFilter.vue'
import WorkbenchLayoutSwitcher from '@/components/common/WorkbenchLayoutSwitcher.vue'
import WorkbenchToolbar from '@/components/common/WorkbenchToolbar.vue'
import GridSection from '@/components/common/GridSection.vue'
import {
  fetchMarketPlugins,
  fetchMarketPluginVersions,
  getMarketUrl,
  isMarketAvailable,
  type MarketPlugin,
  type FetchMarketPluginsParams,
} from '@/api/market'
import { useMarketWorkbench, type MarketWorkbenchItem } from '@/composables/useMarketWorkbench'
import type {
  FilterRuleGroupDescriptor,
  GroupChoiceDescriptor,
  LayoutChoiceDescriptor,
} from '@/composables/workbenchDescriptors'
import { usePluginStore } from '@/stores/plugin'

interface Props {
  embedded?: boolean
  /** 外部触发的打开事件，用于切换可见时重新校验状态 */
  active?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  embedded: false,
  active: true,
})

defineEmits<{ close: [] }>()

const { t } = useI18n()
const pluginStore = usePluginStore()

const loading = ref(false)
const marketAvailable = ref(false)
const marketBaseUrl = ref<string | null>(null)
const plugins = ref<MarketPlugin[]>([])
const currentPage = ref(1)
const pageSize = props.embedded ? 8 : 12
const totalCount = ref(0)
const installingId = ref<string | null>(null)
const bridgeToken = ref('')
const sortBy = ref<'created_at' | 'download_count' | 'rating_average' | 'name'>('created_at')
const sortOrder = ref<'asc' | 'desc'>('desc')

// ─── 本地插件对比：用 slug/id/name 三路兜底 ────────────────────────
const localPluginKeys = computed(() => {
  const keys = new Set<string>()
  for (const p of pluginStore.pluginsWithStatus) {
    const id = String(p.id || '').toLowerCase()
    const name = String(p.name || '').toLowerCase()
    if (id) keys.add(id)
    if (name) keys.add(name)
  }
  return keys
})

function isInstalled(plugin: { slug?: string; name?: string; id: string | number }): boolean {
  const candidates = [plugin.slug, plugin.name, String(plugin.id)]
    .filter(Boolean)
    .map((v) => String(v).toLowerCase())
  return candidates.some((c) => localPluginKeys.value.has(c))
}

// ─── 工作台：过滤 + 分组 + 布局 ───────────────────────────────────
const {
  filterText,
  useRegex,
  filterMode,
  selectedGroupIds,
  layoutMode,
  regexError,
  groupCounts,
  filteredItems,
} = useMarketWorkbench(plugins, { isInstalled })

const activeGroupId = computed(() => selectedGroupIds.value[0] || 'all')
const activeGroupLabel = computed(() =>
  activeGroupId.value === 'recommended'
    ? t('market.recommended')
    : t('market.allPlugins'),
)

// ─── UI 描述符 ────────────────────────────────────────────────────
const groupChoices = computed<GroupChoiceDescriptor[]>(() => [
  { id: 'recommended', label: t('market.recommended') },
  { id: 'all', label: t('market.allPlugins') },
])

const layoutChoices = computed<LayoutChoiceDescriptor[]>(() => [
  { value: 'list', label: t('plugins.layoutList') },
  { value: 'single', label: t('plugins.layoutSingle') },
  { value: 'double', label: t('plugins.layoutDouble') },
  { value: 'compact', label: t('plugins.layoutCompact') },
])

const sortOptions = computed(() => [
  { value: 'created_at', label: t('market.sortNewest') },
  { value: 'download_count', label: t('market.sortMostDownloads') },
  { value: 'rating_average', label: t('market.sortTopRated') },
  { value: 'name', label: t('market.sortName') },
])

const filterRuleGroups = computed<FilterRuleGroupDescriptor[]>(() => [
  {
    key: 'state',
    title: t('market.filterGroups.state'),
    rules: [
      { token: 'is:recommended', label: t('market.filterLabels.recommended') },
      { token: 'is:installed', label: t('market.filterLabels.installed') },
      { token: 'is:uninstalled', label: t('market.filterLabels.uninstalled') },
    ],
  },
  {
    key: 'zone',
    title: t('market.filterGroups.zone'),
    rules: [
      { token: 'zone:game', label: t('market.zones.game') },
      { token: 'zone:companion', label: t('market.zones.companion') },
      { token: 'zone:function', label: t('market.zones.function') },
      { token: 'zone:entertainment', label: t('market.zones.entertainment') },
      { token: 'zone:tool', label: t('market.zones.tool') },
    ],
  },
  {
    key: 'meta',
    title: t('market.filterGroups.meta'),
    rules: [
      { token: 'tag:', label: t('market.filterLabels.tag') },
      { token: 'author:', label: t('market.filterLabels.author') },
      { token: 'name:', label: t('market.filterLabels.name') },
      { token: 'v:>=', label: t('market.filterLabels.versionGte') },
      { token: 'has:repo', label: t('market.filterLabels.hasRepo') },
      { token: 'has:tags', label: t('market.filterLabels.hasTags') },
    ],
  },
])

const totalPages = computed(() => Math.ceil(totalCount.value / pageSize))

// ─── 后端查询：提取纯关键词，qualifier 和 regex 留给前端 ────────
/** 从用户输入里抽取可以直传给后端 q= 的"裸 term"。 */
function extractServerQuery(input: string): string {
  if (!input.trim()) return ''
  if (useRegex.value) return ''
  const tokens = input.match(/"[^"]+"|\S+/g) || []
  const terms = tokens
    .map((raw) => {
      const negated = raw.startsWith('-')
      const body = negated ? raw.slice(1) : raw
      const unquoted = body.replace(/^"(.*)"$/, '$1').trim()
      if (!unquoted || unquoted.includes(':')) return ''
      if (negated) return ''
      return unquoted
    })
    .filter(Boolean)
  return terms.join(' ').trim()
}

async function ensureBridgeToken(): Promise<string> {
  if (bridgeToken.value) return bridgeToken.value
  try {
    const res = await fetch('/market/bridge-token')
    if (res.ok) {
      const data = await res.json()
      if (data.bridge_token) {
        bridgeToken.value = data.bridge_token
        localStorage.setItem('neko_bridge_token', data.bridge_token)
      }
    }
  } catch {
    // 静默降级
  }
  if (!bridgeToken.value) {
    bridgeToken.value = localStorage.getItem('neko_bridge_token') || ''
  }
  return bridgeToken.value
}

let loadSeq = 0

async function loadPlugins() {
  if (!marketAvailable.value) return
  const mySeq = ++loadSeq
  loading.value = true
  try {
    const params: FetchMarketPluginsParams = {
      page: currentPage.value,
      page_size: pageSize,
      sort_by: sortBy.value,
      sort_order: sortOrder.value,
    }
    const q = extractServerQuery(filterText.value)
    if (q) params.search = q
    if (activeGroupId.value === 'recommended') params.featured_only = true

    const result = await fetchMarketPlugins(params)
    // 只接受最新一次请求的返回值，避免乱序覆盖
    if (mySeq !== loadSeq) return
    if (result) {
      plugins.value = result.items
      totalCount.value = result.total
      marketAvailable.value = true
    } else {
      marketAvailable.value = false
    }
  } catch {
    if (mySeq === loadSeq) marketAvailable.value = false
  } finally {
    if (mySeq === loadSeq) loading.value = false
  }
}

// ─── 交互：分页、搜索 debounce、排序、分组切换 ────────────────────

let searchDebounceTimer: number | null = null

watch(filterText, () => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = window.setTimeout(() => {
    currentPage.value = 1
    loadPlugins()
  }, 400)
})

watch(useRegex, () => {
  currentPage.value = 1
  loadPlugins()
})

watch(activeGroupId, () => {
  currentPage.value = 1
  loadPlugins()
})

function onSortChange() {
  // name 字段默认升序，其他字段默认降序
  sortOrder.value = sortBy.value === 'name' ? 'asc' : 'desc'
  currentPage.value = 1
  loadPlugins()
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadPlugins()
}

function handlePluginClick(plugin: MarketWorkbenchItem): void {
  if (marketBaseUrl.value) {
    const path = plugin.slug ? `/plugin/${plugin.slug}` : `/plugin/${plugin.rawId}`
    window.open(`${marketBaseUrl.value}${path}`, '_blank')
  } else if (plugin.github_repo) {
    window.open(plugin.github_repo, '_blank')
  }
}

function openMarketExternal() {
  if (marketBaseUrl.value) window.open(marketBaseUrl.value, '_blank')
}

// ─── 安装流程（与之前一致，换成新的 MarketPlugin id 类型） ───────

interface ResolvedInstallPayload {
  package_url: string
  package_sha256: string | null
  payload_hash: string | null
  version: string
}

async function resolveInstallPayload(
  plugin: MarketWorkbenchItem,
): Promise<ResolvedInstallPayload | null> {
  const fallbackUrl = plugin.download_url || plugin.github_repo || ''
  let packageUrl = fallbackUrl
  let packageSha256: string | null = null
  let payloadHash: string | null = null
  let version = plugin.version

  try {
    const versions = await fetchMarketPluginVersions(plugin.rawId)
    if (versions && versions.length > 0) {
      const matched = versions.find((v) => v.version === plugin.version) ?? versions[0]
      if (matched) {
        packageUrl = matched.package_url || matched.download_url || fallbackUrl
        packageSha256 = matched.package_sha256 || null
        payloadHash = matched.payload_hash || null
        version = matched.version || version
      }
    }
  } catch {
    // 静默降级
  }

  if (!packageUrl) return null
  return { package_url: packageUrl, package_sha256: packageSha256, payload_hash: payloadHash, version }
}

async function pollInstallTask(taskId: string, pluginName: string): Promise<boolean> {
  const token = await ensureBridgeToken()
  const deadline = Date.now() + 3 * 60 * 1000
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`/market/tasks/${taskId}?token=${encodeURIComponent(token)}`)
      if (res.ok) {
        const task = await res.json()
        if (task.status === 'completed') {
          ElMessage.success(t('market.installSuccess', { name: pluginName }))
          // 安装后同步本地注册表，让 isInstalled 立即生效
          pluginStore.syncRegistryAndFetch().catch(() => {})
          return true
        }
        if (task.status === 'failed') {
          ElMessage.error(task.error || task.message || t('market.installFailed'))
          return false
        }
      }
    } catch {
      // 继续轮询
    }
    await new Promise((r) => setTimeout(r, 1000))
  }
  ElMessage.warning(t('market.installFailed'))
  return false
}

async function handleInstall(plugin: MarketWorkbenchItem) {
  const payload = await resolveInstallPayload(plugin)
  if (!payload) {
    ElMessage.warning(t('market.noDownloadUrl'))
    return
  }

  installingId.value = plugin.id

  try {
    const token = await ensureBridgeToken()
    const res = await fetch(`/market/install?token=${encodeURIComponent(token)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package_url: payload.package_url,
        package_sha256: payload.package_sha256,
        payload_hash: payload.payload_hash,
        plugin_id: String(plugin.rawId),
        version: payload.version,
        on_conflict: 'rename',
      }),
    })

    if (res.ok) {
      const data = await res.json()
      if (data.task_id) {
        await pollInstallTask(data.task_id, plugin.name)
      } else {
        ElMessage.success(t('market.installSuccess', { name: plugin.name }))
      }
    } else if (res.status === 403) {
      ElMessage.warning(t('market.pairRequired'))
    } else {
      const err = await res.json().catch(() => ({}))
      ElMessage.error(err.detail || t('market.installFailed'))
    }
  } catch {
    window.open(payload.package_url, '_blank')
  } finally {
    installingId.value = null
  }
}

async function initialize() {
  marketAvailable.value = await isMarketAvailable()
  marketBaseUrl.value = await getMarketUrl()
  await ensureBridgeToken()
  if (marketAvailable.value) {
    await loadPlugins()
  }
  if (pluginStore.pluginsWithStatus.length === 0) {
    pluginStore.fetchPlugins().catch(() => {})
  }
}

onMounted(() => {
  if (props.active !== false) initialize()
})

onBeforeUnmount(() => {
  if (searchDebounceTimer) {
    clearTimeout(searchDebounceTimer)
    searchDebounceTimer = null
  }
  // 让在途 loadPlugins 的 mySeq 全部失效
  loadSeq++
})

watch(
  () => props.active,
  (active) => {
    if (active && plugins.value.length === 0 && !loading.value) {
      initialize()
    }
  },
)
</script>

<style scoped>
.market-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  width: 100%;
  min-width: 0;
}

.market-panel--embedded {
  height: 100%;
  padding: 18px 18px 24px;
  background: var(--el-bg-color);
  border-radius: 16px;
  border: 1px solid var(--el-border-color-lighter);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.04);
}

.market-panel__heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding-bottom: 4px;
}

.market-panel__heading-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.market-panel__heading-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.market-panel__heading-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.market-panel__heading-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.market-panel__icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}

.market-panel__icon-btn:hover {
  background: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
  color: var(--el-color-primary);
}

.market-panel__toolbar {
  margin-top: 0;
}

.market-panel__toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.market-panel__sort {
  width: 140px;
}

.market-panel__content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.market-panel__pagination {
  display: flex;
  justify-content: center;
  padding-top: 8px;
}

.market-panel__empty-hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 8px;
}
</style>
