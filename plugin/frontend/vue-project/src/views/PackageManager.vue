<template>
  <div class="package-manager">
    <div class="main-grid">
      <el-card class="selector-card">
        <template #header>
          <div class="card-header card-header--stack">
            <div class="selector-topline">
              <span>本地插件</span>
              <div class="selector-topline__meta">
                <el-tag size="small" type="primary">{{ selectablePlugins.length }}</el-tag>
                <el-tag size="small" type="info">已选 {{ selectedPluginIds.length }}</el-tag>
              </div>
            </div>

            <el-input
              v-model="pluginFilter"
              clearable
              placeholder="搜索插件名称、ID、描述"
            />

            <div class="type-filter-bar">
              <el-checkbox-group v-model="selectedTypes" class="type-filter-group">
                <el-checkbox-button label="plugin">插件 ({{ pluginCount }})</el-checkbox-button>
                <el-checkbox-button label="adapter">适配器 ({{ adapterCount }})</el-checkbox-button>
                <el-checkbox-button label="extension">扩展 ({{ extensionCount }})</el-checkbox-button>
              </el-checkbox-group>
            </div>

            <div class="selector-toolbar">
              <el-radio-group v-model="layoutMode" size="small">
                <el-radio-button label="single">单排</el-radio-button>
                <el-radio-button label="double">双排</el-radio-button>
                <el-radio-button label="compact">小矩阵</el-radio-button>
              </el-radio-group>

              <div class="selector-actions">
                <el-button text @click="selectAllVisible">全选</el-button>
                <el-button text @click="clearSelection">清空</el-button>
                <el-button :loading="pluginsLoading" text @click="refreshPluginSources">刷新</el-button>
              </div>
            </div>
          </div>
        </template>

        <el-empty
          v-if="!pluginsLoading && filteredPlugins.length === 0"
          description="没有匹配的本地插件"
        />

        <div v-else class="selector-sections">
          <template v-if="filteredPurePlugins.length > 0">
            <div class="section-header">
              <span class="section-title">插件 ({{ filteredPurePlugins.length }})</span>
            </div>
            <div class="plugin-selector-grid" :class="layoutClass">
              <article
                v-for="plugin in filteredPurePlugins"
                :key="plugin.id"
                class="plugin-select-card"
                :class="{ 'plugin-select-card--active': isSelected(plugin.id) }"
                @click="togglePlugin(plugin.id)"
              >
                <div class="plugin-select-card__header">
                  <div class="plugin-select-card__title-group">
                    <div class="plugin-select-card__badges">
                      <el-tag size="small" effect="plain" :type="pluginTypeTagType(plugin.type)">
                        {{ plugin.type || 'plugin' }}
                      </el-tag>
                      <el-tag
                        v-if="plugin.status"
                        size="small"
                        effect="plain"
                        :type="pluginStatusTagType(plugin.status)"
                      >
                        {{ plugin.status }}
                      </el-tag>
                    </div>
                    <h3 class="plugin-select-card__title">{{ plugin.name }}</h3>
                    <div class="plugin-select-card__subtitle">{{ plugin.id }}</div>
                  </div>
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    @click.stop
                    @change="togglePlugin(plugin.id)"
                  />
                </div>

                <p class="plugin-select-card__description">
                  {{ plugin.description || '暂无描述' }}
                </p>

                <div class="plugin-select-card__meta">
                  <el-tag size="small" type="info">v{{ plugin.version || '0.0.0' }}</el-tag>
                  <span class="plugin-select-card__meta-text">入口 {{ plugin.entryCount }}</span>
                </div>
              </article>
            </div>
          </template>

          <template v-if="filteredAdapters.length > 0">
            <div class="section-header section-header--adapter">
              <span class="section-title">适配器 ({{ filteredAdapters.length }})</span>
            </div>
            <div class="plugin-selector-grid" :class="layoutClass">
              <article
                v-for="plugin in filteredAdapters"
                :key="plugin.id"
                class="plugin-select-card"
                :class="{ 'plugin-select-card--active': isSelected(plugin.id) }"
                @click="togglePlugin(plugin.id)"
              >
                <div class="plugin-select-card__header">
                  <div class="plugin-select-card__title-group">
                    <div class="plugin-select-card__badges">
                      <el-tag size="small" effect="plain" :type="pluginTypeTagType(plugin.type)">
                        {{ plugin.type || 'adapter' }}
                      </el-tag>
                      <el-tag
                        v-if="plugin.status"
                        size="small"
                        effect="plain"
                        :type="pluginStatusTagType(plugin.status)"
                      >
                        {{ plugin.status }}
                      </el-tag>
                    </div>
                    <h3 class="plugin-select-card__title">{{ plugin.name }}</h3>
                    <div class="plugin-select-card__subtitle">{{ plugin.id }}</div>
                  </div>
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    @click.stop
                    @change="togglePlugin(plugin.id)"
                  />
                </div>

                <p class="plugin-select-card__description">
                  {{ plugin.description || '暂无描述' }}
                </p>

                <div class="plugin-select-card__meta">
                  <el-tag size="small" type="info">v{{ plugin.version || '0.0.0' }}</el-tag>
                  <span class="plugin-select-card__meta-text">入口 {{ plugin.entryCount }}</span>
                </div>
              </article>
            </div>
          </template>

          <template v-if="filteredExtensions.length > 0">
            <div class="section-header section-header--ext">
              <span class="section-title">扩展 ({{ filteredExtensions.length }})</span>
            </div>
            <div class="plugin-selector-grid" :class="layoutClass">
              <article
                v-for="plugin in filteredExtensions"
                :key="plugin.id"
                class="plugin-select-card"
                :class="{ 'plugin-select-card--active': isSelected(plugin.id) }"
                @click="togglePlugin(plugin.id)"
              >
                <div class="plugin-select-card__header">
                  <div class="plugin-select-card__title-group">
                    <div class="plugin-select-card__badges">
                      <el-tag size="small" effect="plain" :type="pluginTypeTagType(plugin.type)">
                        {{ plugin.type || 'extension' }}
                      </el-tag>
                      <el-tag
                        v-if="plugin.status"
                        size="small"
                        effect="plain"
                        :type="pluginStatusTagType(plugin.status)"
                      >
                        {{ plugin.status }}
                      </el-tag>
                    </div>
                    <h3 class="plugin-select-card__title">{{ plugin.name }}</h3>
                    <div class="plugin-select-card__subtitle">{{ plugin.id }}</div>
                  </div>
                  <el-checkbox
                    :model-value="isSelected(plugin.id)"
                    @click.stop
                    @change="togglePlugin(plugin.id)"
                  />
                </div>

                <p class="plugin-select-card__description">
                  {{ plugin.description || '暂无描述' }}
                </p>

                <div class="plugin-select-card__meta">
                  <el-tag size="small" type="info">v{{ plugin.version || '0.0.0' }}</el-tag>
                  <span v-if="plugin.hostPluginId" class="plugin-select-card__meta-text">
                    宿主 {{ plugin.hostPluginId }}
                  </span>
                </div>
              </article>
            </div>
          </template>
        </div>
      </el-card>

      <div class="content-stack">
        <el-card class="operations-card">
          <template #header>
            <div class="card-header">
              <span>包管理</span>
              <el-tag size="small" type="info">目标 {{ resolvedPackTargets.length }}</el-tag>
            </div>
          </template>

          <el-tabs v-model="activeTab" stretch>
            <el-tab-pane label="打包" name="pack">
              <el-form label-position="top">
                <el-form-item label="打包模式">
                  <el-radio-group v-model="packMode">
                    <el-radio-button label="selected">打包选中插件</el-radio-button>
                    <el-radio-button label="single">打包单个插件</el-radio-button>
                    <el-radio-button label="all">打包全部插件</el-radio-button>
                  </el-radio-group>
                </el-form-item>

                <el-form-item v-if="packMode === 'single'" label="插件">
                  <el-select v-model="packForm.plugin" placeholder="选择插件" clearable filterable>
                    <el-option
                      v-for="plugin in selectablePlugins"
                      :key="plugin.id"
                      :label="plugin.name"
                      :value="plugin.id"
                    />
                  </el-select>
                </el-form-item>

                <el-form-item label="输出目录">
                  <el-input v-model="packForm.target_dir" placeholder="默认使用 neko-plugin-cli/target" />
                </el-form-item>

                <el-form-item label="保留 staging">
                  <el-switch v-model="packForm.keep_staging" />
                </el-form-item>

                <div class="hint-row">
                  <el-tag type="info" effect="plain">
                    当前会处理 {{ resolvedPackTargets.length }} 个插件
                  </el-tag>
                </div>

                <div class="action-row">
                  <el-button type="primary" :loading="packing" @click="handlePack">
                    执行打包
                  </el-button>
                </div>
              </el-form>
            </el-tab-pane>

            <el-tab-pane label="检查 / 校验" name="inspect">
              <el-form label-position="top">
                <el-form-item label="包路径或 target 中的包名">
                  <el-input v-model="packageRef.package" placeholder="例如 qq_auto_reply.neko-plugin" />
                </el-form-item>

                <div class="action-row">
                  <el-button :loading="inspecting" @click="handleInspect">检查包</el-button>
                  <el-button type="success" plain :loading="verifying" @click="handleVerify">
                    校验包
                  </el-button>
                </div>
              </el-form>
            </el-tab-pane>

            <el-tab-pane label="解包" name="unpack">
              <el-form label-position="top">
                <el-form-item label="包路径">
                  <el-input v-model="unpackForm.package" placeholder="例如 qq_auto_reply.neko-plugin" />
                </el-form-item>

                <el-form-item label="插件目录">
                  <el-input v-model="unpackForm.plugins_root" placeholder="默认写入 plugin/plugins" />
                </el-form-item>

                <el-form-item label="Profiles 目录">
                  <el-input
                    v-model="unpackForm.profiles_root"
                    placeholder="默认写入 plugin/.neko-package-profiles"
                  />
                </el-form-item>

                <el-form-item label="冲突策略">
                  <el-radio-group v-model="unpackForm.on_conflict">
                    <el-radio-button label="rename">rename</el-radio-button>
                    <el-radio-button label="fail">fail</el-radio-button>
                  </el-radio-group>
                </el-form-item>

                <div class="action-row">
                  <el-button type="warning" :loading="unpacking" @click="handleUnpack">
                    执行解包
                  </el-button>
                </div>
              </el-form>
            </el-tab-pane>

            <el-tab-pane label="整合包分析" name="analyze">
              <el-form label-position="top">
                <el-form-item label="插件列表">
                  <el-select
                    v-model="analyzeForm.plugins"
                    multiple
                    filterable
                    placeholder="选择多个插件"
                  >
                    <el-option
                      v-for="plugin in selectablePlugins"
                      :key="plugin.id"
                      :label="plugin.name"
                      :value="plugin.id"
                    />
                  </el-select>
                </el-form-item>

                <el-form-item label="当前 SDK 版本">
                  <el-input v-model="analyzeForm.current_sdk_version" placeholder="例如 0.1.0" />
                </el-form-item>

                <div class="action-row">
                  <el-button type="primary" plain :loading="analyzing" @click="handleAnalyze">
                    执行分析
                  </el-button>
                </div>
              </el-form>
            </el-tab-pane>
          </el-tabs>
        </el-card>

        <el-card class="result-card">
          <template #header>
            <div class="card-header">
              <span>执行结果</span>
              <el-tag v-if="resultKind" size="small" type="info">{{ resultKind }}</el-tag>
            </div>
          </template>

          <div v-if="inspectResult" class="inspect-panel">
            <el-descriptions :column="2" border class="inspect-summary">
              <el-descriptions-item label="包 ID">{{ inspectResult.package_id }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ inspectResult.package_type }}</el-descriptions-item>
              <el-descriptions-item label="版本">{{ inspectResult.version || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Schema">{{ inspectResult.schema_version || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Hash 校验">
                <el-tag
                  :type="inspectResult.payload_hash_verified === true ? 'success' : inspectResult.payload_hash_verified === false ? 'danger' : 'info'"
                >
                  {{ inspectResult.payload_hash_verified === null ? '未校验' : inspectResult.payload_hash_verified ? '通过' : '失败' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="Profiles">
                {{ inspectResult.profile_names.join(', ') || '-' }}
              </el-descriptions-item>
            </el-descriptions>
          </div>

          <el-empty v-if="!resultText" description="执行操作后会在这里显示结果" />
          <pre v-else class="result-block">{{ resultText }}</pre>
        </el-card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  analyzePluginBundle,
  getPluginCliPlugins,
  inspectPluginPackage,
  packPluginCli,
  unpackPluginPackage,
  verifyPluginPackage,
  type PluginCliAnalyzeResponse,
  type PluginCliInspectResponse,
  type PluginCliPackRequest,
  type PluginCliUnpackRequest,
} from '@/api/pluginCli'
import { usePluginStore } from '@/stores/plugin'

type LayoutMode = 'single' | 'double' | 'compact'
type PackMode = 'selected' | 'single' | 'all'
type PluginGroupType = 'plugin' | 'adapter' | 'extension'

interface SelectablePlugin {
  id: string
  name: string
  description: string
  version: string
  type: PluginGroupType
  status?: string
  hostPluginId?: string
  entryCount: number
}

const pluginStore = usePluginStore()

const activeTab = ref('pack')
const layoutMode = ref<LayoutMode>('double')
const packMode = ref<PackMode>('selected')
const pluginFilter = ref('')
const localPluginIds = ref<string[]>([])
const selectedPluginIds = ref<string[]>([])
const selectedTypes = ref<PluginGroupType[]>(['plugin', 'adapter', 'extension'])
const pluginsLoading = ref(false)

const packing = ref(false)
const inspecting = ref(false)
const verifying = ref(false)
const unpacking = ref(false)
const analyzing = ref(false)

const resultKind = ref('')
const resultText = ref('')
const inspectResult = ref<PluginCliInspectResponse | null>(null)

const packForm = ref<PluginCliPackRequest>({
  plugin: '',
  target_dir: '',
  keep_staging: false,
})

const packageRef = ref({ package: '' })

const unpackForm = ref<PluginCliUnpackRequest>({
  package: '',
  plugins_root: '',
  profiles_root: '',
  on_conflict: 'rename',
})

const analyzeForm = ref({
  plugins: [] as string[],
  current_sdk_version: '',
})

const selectablePlugins = computed<SelectablePlugin[]>(() => {
  const metaById = new Map(
    pluginStore.pluginsWithStatus.map((plugin) => [
      plugin.id,
      {
        id: plugin.id,
        name: plugin.name || plugin.id,
        description: plugin.description || '',
        version: plugin.version || '0.0.0',
        type: normalizePluginType(plugin.type),
        status: plugin.status,
        hostPluginId: plugin.host_plugin_id,
        entryCount: plugin.entries?.length || 0,
      } satisfies SelectablePlugin,
    ])
  )

  return localPluginIds.value.map((pluginId) => {
    return (
      metaById.get(pluginId) ?? {
        id: pluginId,
        name: pluginId,
        description: '',
        version: '0.0.0',
        type: 'plugin',
        entryCount: 0,
      }
    )
  })
})

const filteredPlugins = computed(() => {
  const keyword = pluginFilter.value.trim().toLowerCase()
  return selectablePlugins.value.filter((plugin) => {
    if (!selectedTypes.value.includes(plugin.type)) {
      return false
    }
    if (!keyword) {
      return true
    }
    return (
      plugin.id.toLowerCase().includes(keyword) ||
      plugin.name.toLowerCase().includes(keyword) ||
      plugin.description.toLowerCase().includes(keyword) ||
      plugin.type.toLowerCase().includes(keyword)
    )
  })
})

const pluginCount = computed(() => selectablePlugins.value.filter((plugin) => plugin.type === 'plugin').length)
const adapterCount = computed(() => selectablePlugins.value.filter((plugin) => plugin.type === 'adapter').length)
const extensionCount = computed(() => selectablePlugins.value.filter((plugin) => plugin.type === 'extension').length)

const filteredPurePlugins = computed(() => filteredPlugins.value.filter((plugin) => plugin.type === 'plugin'))
const filteredAdapters = computed(() => filteredPlugins.value.filter((plugin) => plugin.type === 'adapter'))
const filteredExtensions = computed(() => filteredPlugins.value.filter((plugin) => plugin.type === 'extension'))

const layoutClass = computed(() => `plugin-selector-grid--${layoutMode.value}`)

const resolvedPackTargets = computed(() => {
  if (packMode.value === 'all') {
    return selectablePlugins.value.map((plugin) => plugin.id)
  }
  if (packMode.value === 'single') {
    return packForm.value.plugin ? [packForm.value.plugin] : []
  }
  return selectedPluginIds.value
})

function normalizePluginType(type?: string): PluginGroupType {
  if (type === 'adapter') return 'adapter'
  if (type === 'extension') return 'extension'
  return 'plugin'
}

function setResult(kind: string, payload: unknown) {
  resultKind.value = kind
  resultText.value = JSON.stringify(payload, null, 2)
}

function pluginTypeTagType(type?: string): 'primary' | 'success' | 'warning' {
  if (type === 'adapter') return 'success'
  if (type === 'extension') return 'warning'
  return 'primary'
}

function pluginStatusTagType(status?: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'running' || status === 'injected') return 'success'
  if (status === 'load_failed' || status === 'crashed') return 'danger'
  if (status === 'pending') return 'warning'
  return 'info'
}

function isSelected(pluginId: string): boolean {
  return selectedPluginIds.value.includes(pluginId)
}

function togglePlugin(pluginId: string) {
  if (isSelected(pluginId)) {
    selectedPluginIds.value = selectedPluginIds.value.filter((item) => item !== pluginId)
    return
  }
  selectedPluginIds.value = [...selectedPluginIds.value, pluginId]
}

function selectAllVisible() {
  selectedPluginIds.value = Array.from(
    new Set([...selectedPluginIds.value, ...filteredPlugins.value.map((plugin) => plugin.id)])
  )
}

function clearSelection() {
  selectedPluginIds.value = []
}

async function refreshPluginSources() {
  pluginsLoading.value = true
  try {
    await pluginStore.fetchPlugins()
    const response = await getPluginCliPlugins()
    localPluginIds.value = response.plugins
    if (selectedPluginIds.value.length === 0) {
      selectedPluginIds.value = response.plugins.slice(0, 1)
    } else {
      selectedPluginIds.value = selectedPluginIds.value.filter((pluginId) => response.plugins.includes(pluginId))
    }
  } catch (error) {
    console.error('Failed to refresh plugin sources:', error)
  } finally {
    pluginsLoading.value = false
  }
}

async function handlePack() {
  const targets = resolvedPackTargets.value
  if (targets.length === 0) {
    ElMessage.warning('请先选择要打包的插件')
    return
  }

  packing.value = true
  inspectResult.value = null

  try {
    if (packMode.value === 'all') {
      const response = await packPluginCli({
        pack_all: true,
        target_dir: packForm.value.target_dir || undefined,
        keep_staging: !!packForm.value.keep_staging,
      })
      setResult('pack', response)
      ElMessage.success(`打包完成，成功 ${response.packed_count} 个`)
      return
    }

    const packed: unknown[] = []
    const failed: Array<{ plugin: string; error: string }> = []

    for (const pluginId of targets) {
      try {
        const response = await packPluginCli({
          plugin: pluginId,
          pack_all: false,
          target_dir: packForm.value.target_dir || undefined,
          keep_staging: !!packForm.value.keep_staging,
        })
        packed.push(...response.packed)
        failed.push(...response.failed)
      } catch (error) {
        failed.push({ plugin: pluginId, error: error instanceof Error ? error.message : String(error) })
      }
    }

    const summary = {
      packed,
      packed_count: packed.length,
      failed,
      failed_count: failed.length,
      ok: failed.length === 0,
    }
    setResult('pack', summary)
    ElMessage.success(`打包完成，成功 ${packed.length} 个`)
  } finally {
    packing.value = false
  }
}

async function handleInspect() {
  if (!packageRef.value.package.trim()) {
    ElMessage.warning('请先输入包路径')
    return
  }
  inspecting.value = true
  try {
    const response = await inspectPluginPackage({ package: packageRef.value.package.trim() })
    inspectResult.value = response
    setResult('inspect', response)
    ElMessage.success('包检查完成')
  } finally {
    inspecting.value = false
  }
}

async function handleVerify() {
  if (!packageRef.value.package.trim()) {
    ElMessage.warning('请先输入包路径')
    return
  }
  verifying.value = true
  try {
    const response = await verifyPluginPackage({ package: packageRef.value.package.trim() })
    inspectResult.value = response
    setResult('verify', response)
    ElMessage[response.ok ? 'success' : 'warning'](response.ok ? '包校验通过' : '包未通过校验')
  } finally {
    verifying.value = false
  }
}

async function handleUnpack() {
  if (!unpackForm.value.package?.trim()) {
    ElMessage.warning('请先输入包路径')
    return
  }
  unpacking.value = true
  inspectResult.value = null
  try {
    const response = await unpackPluginPackage({
      package: unpackForm.value.package.trim(),
      plugins_root: unpackForm.value.plugins_root?.trim() || undefined,
      profiles_root: unpackForm.value.profiles_root?.trim() || undefined,
      on_conflict: unpackForm.value.on_conflict || 'rename',
    })
    setResult('unpack', response)
    ElMessage.success(`解包完成，处理了 ${response.unpacked_plugin_count} 个插件`)
  } finally {
    unpacking.value = false
  }
}

async function handleAnalyze() {
  if (analyzeForm.value.plugins.length === 0) {
    ElMessage.warning('请至少选择一个插件')
    return
  }
  analyzing.value = true
  inspectResult.value = null
  try {
    const response: PluginCliAnalyzeResponse = await analyzePluginBundle({
      plugins: analyzeForm.value.plugins,
      current_sdk_version: analyzeForm.value.current_sdk_version.trim() || undefined,
    })
    setResult('analyze', response)
    ElMessage.success('分析完成')
  } finally {
    analyzing.value = false
  }
}

watch(
  selectedPluginIds,
  (pluginIds) => {
    if (packMode.value !== 'single') {
      packForm.value.plugin = pluginIds[0] || ''
    }
    analyzeForm.value.plugins = [...pluginIds]
  },
  { immediate: true }
)

watch(packMode, (mode) => {
  if (mode === 'single') {
    packForm.value.plugin = selectedPluginIds.value[0] || ''
  }
})

onMounted(() => {
  refreshPluginSources()
})
</script>

<style scoped>
.package-manager {
  display: flex;
  flex-direction: column;
}

.main-grid {
  display: grid;
  grid-template-columns: 440px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.selector-card,
.operations-card,
.result-card {
  border-radius: 18px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.card-header--stack {
  flex-direction: column;
  align-items: stretch;
}

.selector-topline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.selector-topline__meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.selector-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.selector-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.type-filter-bar {
  padding: 2px 0;
}

.type-filter-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.type-filter-group .el-checkbox-button__inner {
  display: flex;
  align-items: center;
  gap: 4px;
}

.selector-sections {
  max-height: 820px;
  overflow: auto;
  padding-right: 4px;
}

.section-header {
  margin-bottom: 12px;
}

.section-header--adapter,
.section-header--ext {
  margin-top: 24px;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.plugin-selector-grid {
  display: grid;
  gap: 12px;
}

.plugin-selector-grid--single {
  grid-template-columns: 1fr;
}

.plugin-selector-grid--double {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.plugin-selector-grid--compact {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.plugin-select-card {
  cursor: pointer;
  transition: all 0.25s ease;
  border: 1px solid var(--el-border-color-light);
  border-radius: 14px;
  background: var(--el-bg-color);
  padding: 14px;
  min-height: 176px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.plugin-select-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--el-box-shadow);
}

.plugin-select-card--active {
  border-color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
}

.plugin-select-card__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.plugin-select-card__title-group {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.plugin-select-card__badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.plugin-select-card__title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.plugin-select-card__subtitle {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  word-break: break-all;
}

.plugin-select-card__description {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 14px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 63px;
}

.plugin-select-card__meta {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.plugin-select-card__meta-text {
  color: var(--el-text-color-secondary);
}

.action-row {
  display: flex;
  gap: 12px;
  margin-top: 6px;
}

.hint-row {
  margin: 6px 0 4px;
}

.inspect-summary {
  width: 100%;
  margin-bottom: 16px;
}

.result-block {
  margin: 16px 0 0;
  padding: 14px;
  border-radius: 14px;
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
  overflow: auto;
  max-height: 360px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.55;
}

@media (max-width: 1380px) {
  .main-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 980px) {
  .plugin-selector-grid--double,
  .plugin-selector-grid--compact {
    grid-template-columns: 1fr;
  }
}
</style>
