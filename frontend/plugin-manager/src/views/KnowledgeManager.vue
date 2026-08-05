<template>
  <div class="knowledge-manager">
    <header class="page-heading">
      <div>
        <h1>{{ t('knowledge.title') }}</h1>
        <p>{{ t('knowledge.subtitle') }}</p>
      </div>
      <el-button :loading="loading" @click="refreshAll">{{ t('common.refresh') }}</el-button>
    </header>

    <div class="market-entry">
      <el-alert :title="t('knowledge.marketConnected')" type="info" :closable="false" show-icon />
      <el-tag v-if="marketAuth.authenticated" type="success" effect="plain">
        {{ t('market.accountConnected', { name: marketAuthDisplayName }) }}
      </el-tag>
      <span v-else class="market-login-hint">{{ t('knowledge.loginRequired') }}</span>
      <el-button
        type="primary"
        plain
        :disabled="!marketAuth.authenticated"
        :loading="marketOpening"
        @click="openKnowledgeMarket"
      >
        {{ t('knowledge.openMarket') }}
      </el-button>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane :label="t('knowledge.overview')" name="overview">
        <div class="collection-grid" v-loading="loading">
          <el-card v-for="item in collections" :key="item.collection_id" shadow="never">
            <template #header>
              <div class="card-heading">
                <strong>{{ item.name }}</strong>
                <el-tag :type="item.status === 'ready' ? 'success' : 'danger'">
                  {{ item.status === 'ready' ? t('knowledge.ready') : t('knowledge.degraded') }}
                </el-tag>
              </div>
            </template>
            <dl>
              <div><dt>{{ t('knowledge.entries') }}</dt><dd>{{ item.entries ?? 0 }}</dd></div>
              <div><dt>{{ t('knowledge.disabled') }}</dt><dd>{{ item.disabled_entries ?? 0 }}</dd></div>
              <div><dt>{{ t('knowledge.packs') }}</dt><dd>{{ item.packs ?? 0 }}</dd></div>
            </dl>
            <div class="switch-row">
              <span>{{ t('knowledge.autoContext') }}</span>
              <el-switch
                :model-value="item.auto_context"
                :disabled="item.status !== 'ready'"
                @change="setCollectionAuto(item, Boolean($event))"
              />
            </div>
            <div class="source-list">
              <el-tag v-for="source in item.sources || []" :key="source.tag" size="small" effect="plain">
                {{ source.tag }} · {{ source.entries }}
              </el-tag>
            </div>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.catalog')" name="catalog">
        <div class="toolbar">
          <el-select v-model="selectedCollection" @change="loadEntries(true)">
            <el-option v-for="item in collections" :key="item.collection_id" :label="item.name" :value="item.collection_id" />
          </el-select>
          <el-input v-model="query" clearable :placeholder="t('knowledge.searchPlaceholder')" @keyup.enter="loadEntries(true)" />
          <el-button type="primary" @click="loadEntries(true)">{{ t('common.search') }}</el-button>
        </div>
        <el-table :data="entries" v-loading="entriesLoading" :row-key="knowledgeEntryRowKey">
          <el-table-column prop="title" :label="t('knowledge.term')" min-width="180" />
          <el-table-column prop="summary" :label="t('knowledge.summary')" min-width="320" show-overflow-tooltip />
          <el-table-column :label="t('knowledge.source')" width="170">
            <template #default="scope">{{ scope.row.source?.name }}</template>
          </el-table-column>
          <el-table-column :label="t('knowledge.actions')" width="190">
            <template #default="scope">
              <el-button link type="primary" @click="openEntry(scope.row)">{{ t('knowledge.details') }}</el-button>
              <el-button link :type="scope.row.disabled ? 'success' : 'danger'" @click="toggleEntry(scope.row)">
                {{ scope.row.disabled ? t('knowledge.restore') : t('knowledge.disable') }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pager">
          <el-button :disabled="offset === 0" @click="previousPage">{{ t('knowledge.previous') }}</el-button>
          <span>{{ offset + 1 }}–{{ offset + entries.length }}</span>
          <el-button :disabled="!hasMore" @click="nextPage">{{ t('knowledge.next') }}</el-button>
        </div>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.packs')" name="packs">
        <div class="toolbar">
          <el-select v-model="selectedCollection" @change="loadPacks">
            <el-option v-for="item in collections" :key="item.collection_id" :label="item.name" :value="item.collection_id" />
          </el-select>
          <input ref="fileInput" type="file" accept="application/json,.json" hidden @change="importSelectedPack" />
          <el-button type="primary" @click="fileInput?.click()">{{ t('knowledge.importPack') }}</el-button>
        </div>
        <el-table :data="packs" v-loading="packsLoading">
          <el-table-column prop="pack_id" :label="t('knowledge.packId')" min-width="180" />
          <el-table-column prop="entries" :label="t('knowledge.entries')" width="100" />
          <el-table-column :label="t('knowledge.subscription')" min-width="200">
            <template #default="scope">
              {{ scope.row.subscription ? `${scope.row.subscription.provider} · ${scope.row.subscription.version}` : t('knowledge.localImport') }}
            </template>
          </el-table-column>
          <el-table-column :label="t('knowledge.autoContext')" width="130">
            <template #default="scope">
              <el-switch :model-value="scope.row.auto_context === true" @change="setPackAuto(scope.row, Boolean($event))" />
            </template>
          </el-table-column>
          <el-table-column :label="t('knowledge.actions')" width="110">
            <template #default="scope">
              <el-button link type="danger" @click="removePack(scope.row)">{{ t('common.delete') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <el-tab-pane :label="t('knowledge.diagnostics')" name="diagnostics">
        <el-table :data="diagnostics" v-loading="diagnosticsLoading">
          <el-table-column prop="timestamp" :label="t('knowledge.time')" width="210" />
          <el-table-column prop="collection_id" :label="t('knowledge.collection')" width="130" />
          <el-table-column prop="match_mode" :label="t('knowledge.matchMode')" width="140" />
          <el-table-column prop="result" :label="t('knowledge.result')" width="130" />
          <el-table-column prop="error_type" :label="t('knowledge.errorType')" min-width="150" />
          <el-table-column :label="t('knowledge.delivered')" width="100">
            <template #default="scope">
              <el-tag :type="scope.row.card_delivered ? 'success' : 'info'">
                {{ scope.row.card_delivered ? t('knowledge.yes') : t('knowledge.no') }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="drawerOpen" :title="selectedEntry?.title || ''" size="520px">
      <template v-if="selectedEntry">
        <h3>{{ t('knowledge.summary') }}</h3><p>{{ selectedEntry.summary }}</p>
        <h3>{{ t('knowledge.terms') }}</h3><pre>{{ JSON.stringify(selectedEntry.terms, null, 2) }}</pre>
        <h3>{{ t('knowledge.tags') }}</h3><p>{{ selectedEntry.tags.join(' · ') }}</p>
        <h3>{{ t('knowledge.content') }}</h3><pre>{{ selectedEntry.content }}</pre>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { knowledgeApi, type KnowledgeCollection, type KnowledgeEntrySummary } from '@/api/knowledge'
import { getMarketUrl } from '@/api/market'
import { useMarketAuth } from '@/composables/useMarketAuth'
import { openExternalUrl } from '@/utils/openExternal'

const { t } = useI18n()
const {
  marketAuth,
  marketAuthDisplayName,
  loadMarketAuthStatus,
} = useMarketAuth()
const activeTab = ref('overview')
const loading = ref(false)
const collections = ref<KnowledgeCollection[]>([])
const selectedCollection = ref('meme')
const query = ref('')
const entries = ref<KnowledgeEntrySummary[]>([])
const entriesLoading = ref(false)
const offset = ref(0)
const pageSize = 50
const hasMore = ref(false)
const drawerOpen = ref(false)
const selectedEntry = ref<KnowledgeEntrySummary | null>(null)
const packs = ref<any[]>([])
const packsLoading = ref(false)
const diagnostics = ref<any[]>([])
const diagnosticsLoading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const marketOpening = ref(false)
let latestEntriesRequest = 0
let latestEntryRequest = 0
let latestPacksRequest = 0
const packsCollection = ref('')

function knowledgeEntryRowKey(row: KnowledgeEntrySummary): string {
  return JSON.stringify([row.collection_id, row.source?.tag || '', row.title])
}

async function openKnowledgeMarket() {
  if (!marketAuth.value.authenticated) {
    ElMessage.warning(t('knowledge.loginRequired'))
    return
  }
  marketOpening.value = true
  try {
    const base = await getMarketUrl()
    if (!base) throw new Error(t('knowledge.marketUnavailable'))
    const response = await fetch('/market/pair-code', { method: 'POST' })
    if (!response.ok) throw new Error(t('knowledge.marketPairFailed'))
    const pairing = await response.json()
    const code = String(pairing.one_time_code || '')
    const port = Number(pairing.port)
    if (!code || !Number.isInteger(port)) {
      throw new Error(t('knowledge.marketPairFailed'))
    }
    const query = new URLSearchParams({
      neko_pair: code,
      neko_port: String(port),
    })
    openExternalUrl(`${base.replace(/\/+$/, '')}/#/knowledge?${query}`)
  } catch (error) {
    ElMessage.error(
      error instanceof Error ? error.message : t('knowledge.marketPairFailed'),
    )
  } finally {
    marketOpening.value = false
  }
}

async function refreshAll() {
  loading.value = true
  try {
    const response = await knowledgeApi.collections()
    collections.value = response.collections || []
    if (!collections.value.some((item) => item.collection_id === selectedCollection.value)) {
      selectedCollection.value = collections.value[0]?.collection_id || ''
    }
  } catch {
    ElMessage.error(t('knowledge.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function setCollectionAuto(item: KnowledgeCollection, enabled: boolean) {
  try {
    await knowledgeApi.setCollectionAutoContext({ collection: item.collection_id, enabled })
    item.auto_context = enabled
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

async function loadEntries(reset = false) {
  const collection = selectedCollection.value
  const requestId = ++latestEntriesRequest
  if (!collection) {
    entries.value = []
    hasMore.value = false
    entriesLoading.value = false
    return
  }
  if (reset) offset.value = 0
  entriesLoading.value = true
  try {
    const response = await knowledgeApi.entries({ collection, query: query.value, limit: pageSize, offset: offset.value })
    if (requestId !== latestEntriesRequest || collection !== selectedCollection.value) return
    entries.value = response.items || []
    hasMore.value = Boolean(response.has_more)
  } catch {
    if (requestId === latestEntriesRequest) ElMessage.error(t('knowledge.loadFailed'))
  } finally {
    if (requestId === latestEntriesRequest) entriesLoading.value = false
  }
}

async function openEntry(row: KnowledgeEntrySummary) {
  const requestId = ++latestEntryRequest
  const collection = row.collection_id
  try {
    const response = await knowledgeApi.entry({ collection, source: row.source.tag, title: row.title })
    if (requestId !== latestEntryRequest || collection !== selectedCollection.value) return
    selectedEntry.value = response.entry || null
    drawerOpen.value = Boolean(selectedEntry.value)
  } catch {
    if (requestId !== latestEntryRequest || collection !== selectedCollection.value) return
    selectedEntry.value = null
    drawerOpen.value = false
    ElMessage.error(t('knowledge.loadFailed'))
  }
}

async function toggleEntry(row: KnowledgeEntrySummary) {
  try {
    await knowledgeApi.setEntryDisabled({ collection: row.collection_id, source: row.source.tag, title: row.title, disabled: !row.disabled })
    row.disabled = !row.disabled
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

function previousPage() { offset.value = Math.max(0, offset.value - pageSize); loadEntries() }
function nextPage() { offset.value += pageSize; loadEntries() }

async function loadPacks() {
  const collection = selectedCollection.value
  const requestId = ++latestPacksRequest
  if (!collection) {
    packs.value = []
    packsLoading.value = false
    return
  }
  packs.value = []
  packsLoading.value = true
  try {
    const response = await knowledgeApi.packs(collection)
    if (requestId !== latestPacksRequest || collection !== selectedCollection.value) return
    packs.value = response.packs || []
    packsCollection.value = collection
  } catch {
    if (requestId === latestPacksRequest) ElMessage.error(t('knowledge.loadFailed'))
  } finally {
    if (requestId === latestPacksRequest) packsLoading.value = false
  }
}

async function importSelectedPack(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const pack = JSON.parse(await file.text())
    await knowledgeApi.importPack(pack)
    ElMessage.success(t('knowledge.importSuccess'))
    await Promise.all([refreshAll(), loadPacks()])
  } catch { ElMessage.error(t('knowledge.invalidPack')) }
}

async function setPackAuto(row: any, enabled: boolean) {
  const collection = packsCollection.value || selectedCollection.value
  try {
    await knowledgeApi.setPackAutoContext({ collection, pack_id: row.pack_id, enabled })
    row.auto_context = enabled
  } catch { ElMessage.error(t('knowledge.operationFailed')) }
}

async function removePack(row: any) {
  const collection = packsCollection.value || selectedCollection.value
  try {
    await ElMessageBox.confirm(t('knowledge.removeConfirm', { name: row.pack_id }), t('common.warning'), { type: 'warning' })
    await knowledgeApi.removePack({ collection, pack_id: row.pack_id })
    await Promise.all([refreshAll(), loadPacks()])
  } catch (error: any) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(t('knowledge.operationFailed'))
  }
}

async function loadDiagnostics() {
  diagnosticsLoading.value = true
  try { diagnostics.value = (await knowledgeApi.diagnostics()).items || [] }
  catch { ElMessage.error(t('knowledge.loadFailed')) }
  finally { diagnosticsLoading.value = false }
}

watch(activeTab, (tab) => {
  if (tab === 'catalog') loadEntries(true)
  if (tab === 'packs') loadPacks()
  if (tab === 'diagnostics') loadDiagnostics()
})

watch(selectedCollection, () => {
  if (activeTab.value === 'catalog') void loadEntries(true)
  if (activeTab.value === 'packs') void loadPacks()
})

onMounted(() => {
  void Promise.all([refreshAll(), loadMarketAuthStatus()])
})
</script>

<style scoped>
.knowledge-manager { padding: 24px; display: flex; flex-direction: column; gap: 18px; }
.market-entry { display: flex; align-items: center; gap: 12px; }
.market-entry .el-alert { flex: 1; }
.market-login-hint { color: var(--el-text-color-secondary); font-size: 13px; }
.page-heading, .card-heading, .switch-row, .toolbar, .pager { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.page-heading h1 { margin: 0 0 6px; }
.page-heading p { margin: 0; color: var(--el-text-color-secondary); }
.collection-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 16px; }
dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
dl div { padding: 10px; border-radius: 8px; background: var(--el-fill-color-light); }
dt { color: var(--el-text-color-secondary); font-size: 12px; } dd { margin: 4px 0 0; font-size: 20px; font-weight: 700; }
.switch-row { margin-top: 14px; }
.source-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
.toolbar { justify-content: flex-start; margin-bottom: 14px; }
.toolbar .el-select { width: 210px; } .toolbar .el-input { max-width: 480px; }
.pager { justify-content: flex-end; margin-top: 14px; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 12px; border-radius: 8px; background: var(--el-fill-color-light); }
</style>
