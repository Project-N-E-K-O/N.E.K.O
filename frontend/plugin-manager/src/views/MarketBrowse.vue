<template>
  <div class="market-browse">
    <el-card class="market-card">
      <template #header>
        <div class="market-header">
          <div class="market-header__title">
            <el-icon><ShoppingCart /></el-icon>
            <span>{{ $t('market.title') || '获取新插件' }}</span>
          </div>
          <div class="market-header__actions">
            <el-input
              v-model="searchQuery"
              :placeholder="$t('market.searchPlaceholder') || '搜索插件...'"
              clearable
              class="market-search"
              @keyup.enter="handleSearch"
              @clear="handleSearch"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-button :loading="loading" @click="handleRefresh">
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
        </div>
      </template>

      <!-- 未配置 Market -->
      <div v-if="!marketAvailable && !loading" class="market-empty">
        <el-empty :description="$t('market.notConfigured') || '插件市场未配置'">
          <template #image>
            <el-icon :size="64" color="#909399"><ShoppingCart /></el-icon>
          </template>
          <p class="market-empty__hint">
            {{ $t('market.configHint') || '请在环境变量中设置 NEKO_MARKET_URL 以启用插件市场' }}
          </p>
        </el-empty>
      </div>

      <!-- 加载中 -->
      <div v-else-if="loading && plugins.length === 0" class="market-loading">
        <el-skeleton :rows="5" animated />
      </div>

      <!-- 插件列表 -->
      <div v-else class="market-grid">
        <div
          v-for="plugin in plugins"
          :key="plugin.id"
          class="market-plugin-card"
          @click="handlePluginClick(plugin)"
        >
          <div class="market-plugin-card__header">
            <span class="market-plugin-card__name">{{ plugin.name }}</span>
            <el-tag v-if="plugin.is_recommended" type="warning" size="small">推荐</el-tag>
          </div>
          <p class="market-plugin-card__desc">{{ plugin.description }}</p>
          <div class="market-plugin-card__meta">
            <span class="meta-item">
              <el-icon><User /></el-icon>
              {{ plugin.author?.name || '未知' }}
            </span>
            <span class="meta-item">
              <el-icon><Download /></el-icon>
              {{ plugin.downloads || 0 }}
            </span>
            <span class="meta-item meta-item--version">v{{ plugin.version }}</span>
          </div>
          <div class="market-plugin-card__actions">
            <el-button
              type="primary"
              size="small"
              :loading="installingId === plugin.id"
              :disabled="installedIds.has(String(plugin.id))"
              @click.stop="handleInstall(plugin)"
            >
              {{ installedIds.has(String(plugin.id)) ? '已安装' : '安装' }}
            </el-button>
          </div>
        </div>

        <!-- 无结果 -->
        <div v-if="!loading && plugins.length === 0" class="market-empty">
          <el-empty :description="$t('market.noResults') || '没有找到插件'" />
        </div>
      </div>

      <!-- 分页 -->
      <div v-if="totalPages > 1" class="market-pagination">
        <el-pagination
          v-model:current-page="currentPage"
          :page-size="pageSize"
          :total="totalCount"
          layout="prev, pager, next"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ShoppingCart, Search, Refresh, User, Download } from '@element-plus/icons-vue'
import {
  fetchMarketPlugins,
  isMarketAvailable,
  type MarketPlugin,
} from '@/api/market'

const router = useRouter()

const loading = ref(false)
const marketAvailable = ref(false)
const plugins = ref<MarketPlugin[]>([])
const searchQuery = ref('')
const currentPage = ref(1)
const pageSize = 12
const totalCount = ref(0)
const installingId = ref<string | number | null>(null)
const installedIds = ref<Set<string>>(new Set())

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
  // 可以跳转到详情或打开弹窗
  // 暂时用外部链接
  if (plugin.github_repo) {
    window.open(plugin.github_repo, '_blank')
  }
}

async function handleInstall(plugin: MarketPlugin) {
  const downloadUrl = plugin.download_url || plugin.github_repo
  if (!downloadUrl) {
    ElMessage.warning('该插件没有可用的下载地址')
    return
  }

  installingId.value = plugin.id

  try {
    // 调用本地 /market/install 端点
    const res = await fetch('/market/install?' + new URLSearchParams({ token: getBridgeToken() }), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package_url: downloadUrl,
        package_sha256: '0'.repeat(64), // TODO: 从 version 数据获取
        plugin_id: String(plugin.id),
        version: plugin.version,
        on_conflict: 'rename',
      }),
    })

    if (res.ok) {
      const data = await res.json()
      ElMessage.success(`安装任务已创建: ${plugin.name}`)
      installedIds.value.add(String(plugin.id))
      // TODO: 轮询进度
    } else if (res.status === 403) {
      ElMessage.warning('需要先配对 Bridge Token')
    } else {
      const err = await res.json().catch(() => ({}))
      ElMessage.error(err.detail || '安装失败')
    }
  } catch {
    // Fallback: 打开下载链接
    window.open(downloadUrl, '_blank')
  } finally {
    installingId.value = null
  }
}

function getBridgeToken(): string {
  // 从 localStorage 或 bridge.json 获取
  return localStorage.getItem('neko_bridge_token') || ''
}

onMounted(async () => {
  marketAvailable.value = await isMarketAvailable()
  if (marketAvailable.value) {
    loadPlugins()
  }
})
</script>

<style scoped>
.market-browse {
  padding: 0;
}

.market-card {
  border-radius: 16px;
  border: 1px solid var(--el-border-color-lighter);
}

.market-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
}

.market-header__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
}

.market-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.market-search {
  width: 240px;
}

.market-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.market-plugin-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.market-plugin-card:hover {
  border-color: var(--el-color-primary-light-5);
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.market-plugin-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.market-plugin-card__name {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.market-plugin-card__desc {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 0;
  flex: 1;
}

.market-plugin-card__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 3px;
}

.meta-item--version {
  margin-left: auto;
  font-family: monospace;
  color: var(--el-text-color-placeholder);
}

.market-plugin-card__actions {
  display: flex;
  justify-content: flex-end;
  padding-top: 4px;
}

.market-pagination {
  display: flex;
  justify-content: center;
  padding-top: 20px;
}

.market-empty {
  padding: 40px 0;
  text-align: center;
}

.market-empty__hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  margin-top: 8px;
}

.market-loading {
  padding: 20px 0;
}
</style>
