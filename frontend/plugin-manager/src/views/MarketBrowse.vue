<template>
  <div class="plugin-workbench market-workbench">
    <section class="plugin-workbench__main">
      <el-card class="plugin-list-card">
        <template #header>
          <div class="workbench-header">
            <div class="workbench-header__copy">
              <h2 class="workbench-header__title">
                <el-icon><ShoppingCart /></el-icon>
                {{ t('market.title') }}
              </h2>
              <p class="workbench-header__subtitle" v-if="marketAvailable">
                {{ t('market.subtitle') }}
              </p>
            </div>
            <div class="header-actions">
              <el-input
                v-model="searchQuery"
                :placeholder="t('market.searchPlaceholder')"
                clearable
                class="market-search"
                @keyup.enter="handleSearch"
                @clear="handleSearch"
              >
                <template #prefix>
                  <el-icon><Search /></el-icon>
                </template>
              </el-input>
              <button
                class="header-btn header-btn--primary"
                :disabled="loading"
                @click="handleRefresh"
              >
                <el-icon><Refresh /></el-icon>
                <span>{{ t('common.refresh') }}</span>
              </button>
            </div>
          </div>
        </template>

        <!-- 未配置 Market -->
        <EmptyState
          v-if="!marketAvailable && !loading"
          :description="t('market.notConfigured')"
        >
          <template #description>
            <p>{{ t('market.notConfigured') }}</p>
            <p class="market-empty__hint">
              {{ t('market.configHint') }}
            </p>
          </template>
        </EmptyState>

        <!-- 加载中 -->
        <LoadingSpinner
          v-else-if="loading && plugins.length === 0"
          :loading="true"
          :text="t('common.loading')"
        />

        <!-- 无结果 -->
        <EmptyState
          v-else-if="plugins.length === 0"
          :description="t('market.noResults')"
        />

        <!-- 插件网格 -->
        <template v-else>
          <div class="plugin-grid-section">
            <div class="plugin-grid">
              <div
                v-for="plugin in plugins"
                :key="plugin.id"
                class="plugin-item"
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
          </div>

          <!-- 分页 -->
          <div v-if="totalPages > 1" class="market-pagination">
            <el-pagination
              v-model:current-page="currentPage"
              :page-size="pageSize"
              :total="totalCount"
              layout="prev, pager, next, total"
              @current-change="handlePageChange"
            />
          </div>
        </template>
      </el-card>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ShoppingCart, Search, Refresh } from '@element-plus/icons-vue'
import MarketPluginCard from '@/components/plugin/MarketPluginCard.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import {
  fetchMarketPlugins,
  isMarketAvailable,
  type MarketPlugin,
} from '@/api/market'
import { usePluginStore } from '@/stores/plugin'

const { t } = useI18n()
const pluginStore = usePluginStore()

const loading = ref(false)
const marketAvailable = ref(false)
const plugins = ref<MarketPlugin[]>([])
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = 12
const totalCount = ref(0)
const installingId = ref<string | number | null>(null)

const installedIds = computed(() => {
  const ids = new Set<string>()
  for (const p of pluginStore.pluginsWithStatus) {
    ids.add(String(p.id))
  }
  return ids
})

const totalPages = computed(() => Math.ceil(totalCount.value / pageSize))

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
  if (plugin.github_repo) {
    window.open(plugin.github_repo, '_blank')
  }
}

async function handleInstall(plugin: MarketPlugin) {
  const downloadUrl = plugin.download_url || plugin.github_repo
  if (!downloadUrl) {
    ElMessage.warning(t('market.noDownloadUrl'))
    return
  }

  installingId.value = plugin.id

  try {
    const token = localStorage.getItem('neko_bridge_token') || ''
    const res = await fetch(`/market/install?token=${token}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package_url: downloadUrl,
        package_sha256: '0'.repeat(64), // TODO: 从 version 获取真实 hash
        plugin_id: String(plugin.id),
        version: plugin.version,
        on_conflict: 'rename',
      }),
    })

    if (res.ok) {
      ElMessage.success(t('market.installSuccess', { name: plugin.name }))
    } else if (res.status === 403) {
      ElMessage.warning(t('market.pairRequired'))
    } else {
      const err = await res.json().catch(() => ({}))
      ElMessage.error(err.detail || t('market.installFailed'))
    }
  } catch {
    window.open(downloadUrl, '_blank')
  } finally {
    installingId.value = null
  }
}

onMounted(async () => {
  marketAvailable.value = await isMarketAvailable()
  if (marketAvailable.value) {
    loadPlugins()
  }
  if (pluginStore.pluginsWithStatus.length === 0) {
    pluginStore.fetchPlugins()
  }
})
</script>

<style scoped>
.market-workbench {
  --plugin-entry-radius: 16px;
  --radius-card: 16px;
  --radius-panel: 14px;
  --radius-control: 10px;
}

.plugin-workbench__main {
  width: 100%;
}

.plugin-list-card {
  border-radius: var(--radius-card);
}

.workbench-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.workbench-header__title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.workbench-header__subtitle {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.market-search {
  width: 240px;
}

.header-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--radius-control);
  background: var(--el-bg-color);
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.header-btn:hover:not(:disabled) {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

.header-btn--primary {
  background: var(--el-color-primary);
  border-color: var(--el-color-primary);
  color: #fff;
}

.header-btn--primary:hover:not(:disabled) {
  background: var(--el-color-primary-light-3);
  color: #fff;
}

.header-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.plugin-grid-section {
  margin-top: 8px;
}

.plugin-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.plugin-item {
  min-width: 0;
}

.market-pagination {
  display: flex;
  justify-content: center;
  padding-top: 24px;
}

.market-empty__hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 8px;
}
</style>
