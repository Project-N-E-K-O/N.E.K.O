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
          :title="t('market.openInBrowser', '在浏览器打开')"
          @click="openMarketExternal"
        >
          <el-icon><Link /></el-icon>
        </button>
        <el-button text circle @click="$emit('close')">
          <el-icon><Close /></el-icon>
        </el-button>
      </div>
    </div>

    <div class="market-panel__toolbar">
      <el-input
        v-model="searchQuery"
        :placeholder="t('market.searchPlaceholder')"
        clearable
        class="market-panel__search"
        @keyup.enter="handleSearch"
        @clear="handleSearch"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <button
        class="market-panel__btn"
        :disabled="loading"
        @click="handleRefresh"
      >
        <el-icon :class="{ 'is-spinning': loading }"><Refresh /></el-icon>
        <span>{{ t('common.refresh') }}</span>
      </button>
    </div>

    <div class="market-panel__content">
      <EmptyState
        v-if="!marketAvailable && !loading"
        :description="t('market.notConfigured')"
      >
        <template #description>
          <p>{{ t('market.notConfigured') }}</p>
          <p class="market-panel__empty-hint">
            {{ t('market.configHint') }}
          </p>
        </template>
      </EmptyState>

      <LoadingSpinner
        v-else-if="loading && plugins.length === 0"
        :loading="true"
        :text="t('common.loading')"
      />

      <EmptyState
        v-else-if="plugins.length === 0"
        :description="t('market.noResults')"
      />

      <template v-else>
        <div class="market-panel__grid">
          <div
            v-for="plugin in plugins"
            :key="plugin.id"
            class="market-panel__grid-item"
          >
            <MarketPluginCard
              :plugin="plugin"
              :installed="installedIds.has(String(plugin.id))"
              :installing="installingId === plugin.id"
              @click="handlePluginClick(plugin)"
              @install="handleInstall(plugin)"
            />
          </div>
        </div>

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
import { ref, computed, onMounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ShoppingCart, Search, Refresh, Close, Link } from '@element-plus/icons-vue'
import MarketPluginCard from '@/components/plugin/MarketPluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import {
  fetchMarketPlugins,
  fetchMarketPluginVersions,
  getMarketUrl,
  isMarketAvailable,
  type MarketPlugin,
} from '@/api/market'
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

defineEmits<{
  close: []
}>()

const { t } = useI18n()
const pluginStore = usePluginStore()

const loading = ref(false)
const marketAvailable = ref(false)
const marketBaseUrl = ref<string | null>(null)
const plugins = ref<MarketPlugin[]>([])
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = props.embedded ? 8 : 12
const totalCount = ref(0)
const installingId = ref<string | number | null>(null)
const bridgeToken = ref('')

const installedIds = computed(() => {
  const ids = new Set<string>()
  for (const p of pluginStore.pluginsWithStatus) ids.add(String(p.id))
  return ids
})

const totalPages = computed(() => Math.ceil(totalCount.value / pageSize))

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

async function loadPlugins() {
  loading.value = true
  try {
    const result = await fetchMarketPlugins({
      page: currentPage.value,
      page_size: pageSize,
      search: searchQuery.value || undefined,
    })
    if (result) {
      plugins.value = result.items
      totalCount.value = result.total
      marketAvailable.value = true
    } else {
      marketAvailable.value = false
    }
  } catch {
    marketAvailable.value = false
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  loadPlugins()
}

function handleRefresh() {
  loadPlugins()
}

function handlePageChange(page: number) {
  currentPage.value = page
  loadPlugins()
}

function handlePluginClick(plugin: MarketPlugin) {
  if (marketBaseUrl.value) {
    window.open(`${marketBaseUrl.value}/plugin/${plugin.id}`, '_blank')
  } else if (plugin.github_repo) {
    window.open(plugin.github_repo, '_blank')
  }
}

function openMarketExternal() {
  if (marketBaseUrl.value) window.open(marketBaseUrl.value, '_blank')
}

interface ResolvedInstallPayload {
  package_url: string
  package_sha256: string | null
  payload_hash: string | null
  version: string
}

async function resolveInstallPayload(plugin: MarketPlugin): Promise<ResolvedInstallPayload | null> {
  const fallbackUrl = plugin.download_url || plugin.github_repo || ''
  let packageUrl = fallbackUrl
  let packageSha256: string | null = null
  let payloadHash: string | null = null
  let version = plugin.version

  try {
    const versions = await fetchMarketPluginVersions(plugin.id)
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
          pluginStore.fetchPlugins().catch(() => {})
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

async function handleInstall(plugin: MarketPlugin) {
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
        plugin_id: String(plugin.id),
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

// 首次挂载时初始化；当 `active` 从 false 变 true 且尚未加载时也初始化。
onMounted(() => {
  if (props.active !== false) initialize()
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
  border-radius: var(--radius-card, 16px);
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
  border-radius: var(--radius-control, 10px);
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
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.market-panel__search {
  flex: 1 1 220px;
  min-width: 180px;
}

.market-panel__btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--radius-control, 10px);
  background: var(--el-bg-color);
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.market-panel__btn:hover:not(:disabled) {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

.market-panel__btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.market-panel__content {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.market-panel__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}

.market-panel--embedded .market-panel__grid {
  grid-template-columns: 1fr;
}

.market-panel__grid-item {
  min-width: 0;
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

.is-spinning {
  animation: market-panel-spin 1s linear infinite;
}

@keyframes market-panel-spin {
  to { transform: rotate(360deg); }
}
</style>
