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
                <el-radio-button label="list">列表</el-radio-button>
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
              <div
                v-for="plugin in filteredPurePlugins"
                :key="plugin.id"
                class="plugin-select-item"
                :class="{
                  'plugin-select-item--list': layoutMode === 'list',
                  'plugin-select-card--active': isSelected(plugin.id),
                }"
                @click="togglePlugin(plugin.id)"
              >
                <template v-if="layoutMode === 'list'">
                  <div class="plugin-list-row">
                    <el-checkbox
                      :model-value="isSelected(plugin.id)"
                      @click.stop
                      @change="togglePlugin(plugin.id)"
                    />
                    <span class="plugin-list-row__name">{{ plugin.name }}</span>
                  </div>
                </template>
                <template v-else>
                  <div class="plugin-select-item__checkbox">
                    <el-checkbox
                      :model-value="isSelected(plugin.id)"
                      @click.stop
                      @change="togglePlugin(plugin.id)"
                    />
                  </div>
                  <PluginCard :plugin="plugin" :is-selected="isSelected(plugin.id)" />
                </template>
              </div>
            </div>
          </template>

          <template v-if="filteredAdapters.length > 0">
            <div class="section-header section-header--adapter">
              <span class="section-title">适配器 ({{ filteredAdapters.length }})</span>
            </div>
            <div class="plugin-selector-grid" :class="layoutClass">
              <div
                v-for="plugin in filteredAdapters"
                :key="plugin.id"
                class="plugin-select-item"
                :class="{
                  'plugin-select-item--list': layoutMode === 'list',
                  'plugin-select-card--active': isSelected(plugin.id),
                }"
                @click="togglePlugin(plugin.id)"
              >
                <template v-if="layoutMode === 'list'">
                  <div class="plugin-list-row">
                    <el-checkbox
                      :model-value="isSelected(plugin.id)"
                      @click.stop
                      @change="togglePlugin(plugin.id)"
                    />
                    <span class="plugin-list-row__name">{{ plugin.name }}</span>
                  </div>
                </template>
                <template v-else>
                  <div class="plugin-select-item__checkbox">
                    <el-checkbox
                      :model-value="isSelected(plugin.id)"
                      @click.stop
                      @change="togglePlugin(plugin.id)"
                    />
                  </div>
                  <PluginCard :plugin="plugin" :is-selected="isSelected(plugin.id)" />
                </template>
              </div>
            </div>
          </template>

          <template v-if="filteredExtensions.length > 0">
            <div class="section-header section-header--ext">
              <span class="section-title">扩展 ({{ filteredExtensions.length }})</span>
            </div>
            <div class="plugin-selector-grid" :class="layoutClass">
              <div
                v-for="plugin in filteredExtensions"
                :key="plugin.id"
                class="plugin-select-item"
                :class="{
                  'plugin-select-item--list': layoutMode === 'list',
                  'plugin-select-card--active': isSelected(plugin.id),
                }"
                @click="togglePlugin(plugin.id)"
              >
                <template v-if="layoutMode === 'list'">
                  <div class="plugin-list-row">
                    <el-checkbox
                      :model-value="isSelected(plugin.id)"
                      @click.stop
                      @change="togglePlugin(plugin.id)"
                    />
                    <span class="plugin-list-row__name">{{ plugin.name }}</span>
                  </div>
                </template>
                <template v-else>
                  <div class="plugin-select-item__checkbox">
                    <el-checkbox
                      :model-value="isSelected(plugin.id)"
                      @click.stop
                      @change="togglePlugin(plugin.id)"
                    />
                  </div>
                  <PluginCard :plugin="plugin" :is-selected="isSelected(plugin.id)" />
                </template>
              </div>
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
                    <el-radio-button label="bundle">打包整合包</el-radio-button>
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

                <template v-if="packMode === 'bundle'">
                  <el-form-item label="整合包 ID">
                    <el-input v-model="packForm.bundle_id" placeholder="默认按插件 ID 自动生成" />
                  </el-form-item>

                  <el-form-item label="整合包名称">
                    <el-input v-model="packForm.package_name" placeholder="默认自动生成" />
                  </el-form-item>

                  <el-form-item label="整合包描述">
                    <el-input
                      v-model="packForm.package_description"
                      type="textarea"
                      :rows="2"
                      placeholder="可选"
                    />
                  </el-form-item>

                  <el-form-item label="整合包版本">
                    <el-input v-model="packForm.version" placeholder="默认 0.1.0" />
                  </el-form-item>
                </template>

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
              <span>本地包</span>
              <div class="package-header-actions">
                <el-tag size="small" type="info">{{ localPackages.length }}</el-tag>
                <el-button text :loading="packagesLoading" @click="refreshPackageSources">刷新</el-button>
              </div>
            </div>
          </template>

          <div v-if="targetDir" class="package-list-meta">
            <span class="package-list-meta__label">目录</span>
            <span class="package-list-meta__value">{{ targetDir }}</span>
          </div>

          <el-empty v-if="!packagesLoading && localPackages.length === 0" description="target 中还没有本地包" />

          <div v-else class="package-list">
            <button
              v-for="pkg in localPackages"
              :key="pkg.path"
              type="button"
              class="package-list-item"
              :class="{ 'package-list-item--active': packageRef.package === pkg.path || packageRef.package === pkg.name }"
              @click="selectPackage(pkg)"
            >
              <div class="package-list-item__main">
                <div class="package-list-item__name">{{ pkg.name }}</div>
                <div class="package-list-item__meta">
                  <span>{{ formatPackageSize(pkg.size_bytes) }}</span>
                  <span>{{ formatPackageTime(pkg.modified_at) }}</span>
                </div>
              </div>

              <div class="package-list-item__actions">
                <el-button text @click.stop="inspectSelectedPackage(pkg)">检查</el-button>
                <el-button text @click.stop="verifySelectedPackage(pkg)">校验</el-button>
                <el-button text @click.stop="prepareUnpackPackage(pkg)">解包</el-button>
              </div>
            </button>
          </div>
        </el-card>

        <el-card class="result-card">
          <template #header>
            <div class="card-header">
              <span>执行结果</span>
              <el-tag v-if="resultKind" size="small" type="info">{{ resultKind }}</el-tag>
            </div>
          </template>

          <div v-if="summaryMetrics.length > 0" class="summary-grid">
            <div
              v-for="metric in summaryMetrics"
              :key="metric.label"
              class="summary-metric"
            >
              <div class="summary-metric__label">{{ metric.label }}</div>
              <div class="summary-metric__value">{{ metric.value }}</div>
            </div>
          </div>

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

          <div v-if="summaryHighlights.length > 0" class="summary-section">
            <div
              v-for="item in summaryHighlights"
              :key="`${item.label}-${item.value}`"
              class="summary-row"
            >
              <span class="summary-row__label">{{ item.label }}</span>
              <span class="summary-row__value">{{ item.value }}</span>
            </div>
          </div>

          <div v-if="summaryListItems.length > 0" class="summary-section">
            <div class="summary-section__title">明细</div>
            <div class="summary-chip-list">
              <el-tag
                v-for="item in summaryListItems"
                :key="item"
                effect="plain"
                class="summary-chip"
              >
                {{ item }}
              </el-tag>
            </div>
          </div>

          <div v-if="summaryWarnings.length > 0" class="summary-section">
            <div class="summary-section__title">注意</div>
            <div class="summary-warning-list">
              <div
                v-for="warning in summaryWarnings"
                :key="warning"
                class="summary-warning"
              >
                {{ warning }}
              </div>
            </div>
          </div>

          <el-empty v-if="!resultText" description="执行操作后会在这里显示结果" />
          <el-collapse v-else class="result-raw">
            <el-collapse-item title="原始结果 JSON" name="raw">
              <pre class="result-block">{{ resultText }}</pre>
            </el-collapse-item>
          </el-collapse>
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
  getPluginCliPackages,
  getPluginCliPlugins,
  inspectPluginPackage,
  packPluginCli,
  unpackPluginPackage,
  verifyPluginPackage,
  type PluginCliAnalyzeResponse,
  type PluginCliInspectResponse,
  type PluginCliLocalPackageItem,
  type PluginCliPackRequest,
  type PluginCliUnpackRequest,
} from '@/api/pluginCli'
import { usePluginStore } from '@/stores/plugin'
import PluginCard from '@/components/plugin/PluginCard.vue'
import type { PluginMeta } from '@/types/api'

type LayoutMode = 'list' | 'single' | 'double' | 'compact'
type PackMode = 'selected' | 'single' | 'bundle' | 'all'
type PluginGroupType = 'plugin' | 'adapter' | 'extension'

type SelectablePlugin = PluginMeta & {
  type: PluginGroupType
  enabled?: boolean
  autoStart?: boolean
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
const packagesLoading = ref(false)
const localPackages = ref<PluginCliLocalPackageItem[]>([])
const targetDir = ref('')

const packing = ref(false)
const inspecting = ref(false)
const verifying = ref(false)
const unpacking = ref(false)
const analyzing = ref(false)

const resultKind = ref('')
const resultText = ref('')
const resultData = ref<Record<string, any> | null>(null)
const inspectResult = ref<PluginCliInspectResponse | null>(null)

const packForm = ref<PluginCliPackRequest>({
  plugin: '',
  plugins: [],
  target_dir: '',
  keep_staging: false,
  bundle_id: '',
  package_name: '',
  package_description: '',
  version: '',
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
        host_plugin_id: plugin.host_plugin_id,
        entries: plugin.entries || [],
        runtime_enabled: plugin.runtime_enabled,
        runtime_auto_start: plugin.runtime_auto_start,
        enabled: plugin.enabled,
        autoStart: plugin.autoStart,
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
        entries: [],
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
  if (packMode.value === 'bundle') {
    return selectedPluginIds.value
  }
  if (packMode.value === 'single') {
    return packForm.value.plugin ? [packForm.value.plugin] : []
  }
  return selectedPluginIds.value
})

const primaryPackResult = computed<Record<string, any> | null>(() => {
  const data = resultData.value
  if (!data || resultKind.value !== 'pack') return null
  const packed = Array.isArray(data.packed) ? data.packed : []
  if (packed.length === 1) {
    return packed[0] as Record<string, any>
  }
  return null
})

const summaryMetrics = computed(() => {
  const data = resultData.value
  if (!data) return []

  if (resultKind.value === 'pack') {
    const primaryPacked = primaryPackResult.value
    return [
      {
        label: '类型',
        value: primaryPacked?.package_type === 'bundle' ? '整合包' : '插件包',
      },
      { label: '成功', value: String(data.packed_count ?? 0) },
      { label: '失败', value: String(data.failed_count ?? 0) },
      {
        label: primaryPacked?.package_type === 'bundle' ? '包含插件' : '状态',
        value: primaryPacked?.package_type === 'bundle'
          ? String(primaryPacked?.plugin_ids?.length ?? 0)
          : data.ok ? '完成' : '部分失败',
      },
    ]
  }

  if (resultKind.value === 'inspect' || resultKind.value === 'verify') {
    return [
      { label: '插件数', value: String(data.plugin_count ?? 0) },
      { label: 'Profiles', value: String(data.profile_count ?? 0) },
      { label: 'Hash', value: formatHashStatus(data.payload_hash_verified) },
    ]
  }

  if (resultKind.value === 'unpack') {
    return [
      { label: '已处理插件', value: String(data.unpacked_plugin_count ?? 0) },
      { label: '冲突策略', value: String(data.conflict_strategy ?? '-') },
      { label: 'Hash', value: formatHashStatus(data.payload_hash_verified) },
    ]
  }

  if (resultKind.value === 'analyze') {
    return [
      { label: '插件数', value: String(data.plugin_count ?? 0) },
      { label: '共同依赖', value: String(data.common_dependencies?.length ?? 0) },
      { label: '共享依赖', value: String(data.shared_dependencies?.length ?? 0) },
    ]
  }

  return []
})

const summaryHighlights = computed(() => {
  const data = resultData.value
  if (!data) return []

  if (resultKind.value === 'pack') {
    const primaryPacked = primaryPackResult.value
    const firstPacked = data.packed?.[0]
    const latestPacked = data.packed?.[data.packed?.length - 1]
    if (primaryPacked?.package_type === 'bundle') {
      return [
        primaryPacked?.plugin_id ? { label: '整合包 ID', value: primaryPacked.plugin_id } : null,
        primaryPacked?.package_name ? { label: '整合包名称', value: primaryPacked.package_name } : null,
        primaryPacked?.version ? { label: '整合包版本', value: primaryPacked.version } : null,
        latestPacked?.package_path ? { label: '输出路径', value: latestPacked.package_path } : null,
      ].filter(Boolean) as Array<{ label: string; value: string }>
    }
    return [
      firstPacked?.plugin_id ? { label: '首个插件', value: firstPacked.plugin_id } : null,
      latestPacked?.package_path ? { label: '最新包路径', value: latestPacked.package_path } : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  if (resultKind.value === 'inspect' || resultKind.value === 'verify') {
    return [
      data.package_id ? { label: '包 ID', value: data.package_id } : null,
      data.package_type ? { label: '包类型', value: data.package_type } : null,
      data.version ? { label: '版本', value: data.version } : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  if (resultKind.value === 'unpack') {
    return [
      data.package_id ? { label: '包 ID', value: data.package_id } : null,
      data.plugins_root ? { label: '插件目录', value: data.plugins_root } : null,
      data.profile_dir ? { label: 'Profiles 目录', value: data.profile_dir } : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  if (resultKind.value === 'analyze') {
    const sdkSupported = data.sdk_supported_analysis
    const sdkRecommended = data.sdk_recommended_analysis
    return [
      sdkSupported?.current_sdk_version
        ? {
            label: '当前 SDK 支持',
            value: sdkSupported.current_sdk_supported_by_all ? `${sdkSupported.current_sdk_version} 全部支持` : `${sdkSupported.current_sdk_version} 存在不兼容`,
          }
        : null,
      sdkRecommended?.matching_versions?.length
        ? { label: '推荐交集', value: sdkRecommended.matching_versions.join(', ') }
        : null,
    ].filter(Boolean) as Array<{ label: string; value: string }>
  }

  return []
})

const summaryListItems = computed(() => {
  const data = resultData.value
  if (!data) return []

  if (resultKind.value === 'pack') {
    const primaryPacked = primaryPackResult.value
    if (primaryPacked?.package_type === 'bundle') {
      return (primaryPacked.plugin_ids ?? []).map((pluginId: string) => `plugin:${pluginId}`)
    }
    return (data.packed ?? []).map((item: Record<string, any>) => `${item.plugin_id} -> ${item.package_path}`)
  }

  if (resultKind.value === 'inspect' || resultKind.value === 'verify') {
    return [
      ...(data.plugins ?? []).map((item: Record<string, any>) => item.plugin_id),
      ...(data.profile_names ?? []).map((name: string) => `profile:${name}`),
    ]
  }

  if (resultKind.value === 'unpack') {
    return (data.unpacked_plugins ?? []).map((item: Record<string, any>) => {
      const suffix = item.renamed ? ' (renamed)' : ''
      return `${item.target_plugin_id}${suffix}`
    })
  }

  if (resultKind.value === 'analyze') {
    return (data.common_dependencies ?? []).map((item: Record<string, any>) => `${item.name} (${item.plugin_count})`)
  }

  return []
})

const summaryWarnings = computed(() => {
  const data = resultData.value
  if (!data) return []

  if (resultKind.value === 'pack') {
    const warnings = (data.failed ?? []).map((item: Record<string, any>) => `${item.plugin}: ${item.error}`)
    const primaryPacked = primaryPackResult.value
    if (primaryPacked?.package_type === 'bundle' && (primaryPacked.plugin_ids?.length ?? 0) < 2) {
      warnings.push('整合包通常应至少包含两个插件')
    }
    return warnings
  }

  if (resultKind.value === 'verify' && data.ok === false) {
    return ['包未通过 hash 校验，请不要直接导入运行环境']
  }

  if (resultKind.value === 'inspect' && data.payload_hash_verified === false) {
    return ['当前包 hash 校验失败，内容可能已被修改']
  }

  if (resultKind.value === 'analyze') {
    const warnings: string[] = []
    if (data.sdk_supported_analysis && data.sdk_supported_analysis.current_sdk_supported_by_all === false) {
      warnings.push('当前 SDK 版本不被所有插件共同支持')
    }
    if ((data.shared_dependencies?.length ?? 0) > 0) {
      warnings.push(`检测到 ${data.shared_dependencies.length} 个共享依赖，整合时需要重点检查版本约束`)
    }
    return warnings
  }

  return []
})

function normalizePluginType(type?: string): PluginGroupType {
  if (type === 'adapter') return 'adapter'
  if (type === 'extension') return 'extension'
  return 'plugin'
}

function setResult(kind: string, payload: unknown) {
  resultKind.value = kind
  resultData.value = payload && typeof payload === 'object' ? (payload as Record<string, any>) : null
  resultText.value = JSON.stringify(payload, null, 2)
}

function formatHashStatus(value: boolean | null | undefined): string {
  if (value === true) return '通过'
  if (value === false) return '失败'
  return '未校验'
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

async function refreshPackageSources() {
  packagesLoading.value = true
  try {
    const response = await getPluginCliPackages()
    localPackages.value = response.packages
    targetDir.value = response.target_dir
  } catch (error) {
    console.error('Failed to refresh package sources:', error)
  } finally {
    packagesLoading.value = false
  }
}

function applyPackageRef(packageValue: string) {
  packageRef.value.package = packageValue
  unpackForm.value.package = packageValue
}

function selectPackage(pkg: PluginCliLocalPackageItem) {
  applyPackageRef(pkg.path)
}

function focusPackageResult(packageValue: string) {
  applyPackageRef(packageValue)
  activeTab.value = 'inspect'
}

function formatPackageSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatPackageTime(raw: string): string {
  const date = new Date(raw)
  if (Number.isNaN(date.getTime())) return raw
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

async function inspectSelectedPackage(pkg: PluginCliLocalPackageItem) {
  selectPackage(pkg)
  activeTab.value = 'inspect'
  await handleInspect()
}

async function verifySelectedPackage(pkg: PluginCliLocalPackageItem) {
  selectPackage(pkg)
  activeTab.value = 'inspect'
  await handleVerify()
}

function prepareUnpackPackage(pkg: PluginCliLocalPackageItem) {
  selectPackage(pkg)
  activeTab.value = 'unpack'
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
    if (packMode.value === 'bundle') {
      if (targets.length < 2) {
        ElMessage.warning('整合包至少需要选择两个插件')
        return
      }
      const response = await packPluginCli({
        plugins: targets,
        bundle: true,
        bundle_id: packForm.value.bundle_id?.trim() || undefined,
        package_name: packForm.value.package_name?.trim() || undefined,
        package_description: packForm.value.package_description?.trim() || undefined,
        version: packForm.value.version?.trim() || undefined,
        target_dir: packForm.value.target_dir || undefined,
        keep_staging: !!packForm.value.keep_staging,
      })
      setResult('pack', response)
      await refreshPackageSources()
      const latestPacked = response.packed[response.packed.length - 1]
      if (latestPacked?.package_path) {
        focusPackageResult(latestPacked.package_path)
      }
      ElMessage.success('整合包打包完成')
      return
    }

    if (packMode.value === 'all') {
      const response = await packPluginCli({
        pack_all: true,
        target_dir: packForm.value.target_dir || undefined,
        keep_staging: !!packForm.value.keep_staging,
      })
      setResult('pack', response)
      await refreshPackageSources()
      const latestPacked = response.packed[response.packed.length - 1]
      if (latestPacked?.package_path) {
        focusPackageResult(latestPacked.package_path)
      }
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
    await refreshPackageSources()
    const latestPacked = packed[packed.length - 1] as { package_path?: string } | undefined
    if (latestPacked?.package_path) {
      focusPackageResult(latestPacked.package_path)
    }
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
    await refreshPluginSources()
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
    packForm.value.plugins = [...pluginIds]
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
  refreshPackageSources()
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

.package-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
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

.plugin-selector-grid--list {
  grid-template-columns: 1fr;
  gap: 8px;
}

.plugin-selector-grid--double {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.plugin-selector-grid--compact {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.plugin-select-item {
  position: relative;
  cursor: pointer;
}

.plugin-select-item--list {
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-bg-color);
  transition: all 0.2s ease;
}

.plugin-select-item:hover {
  transform: translateY(-2px);
}

.plugin-select-item__checkbox {
  position: absolute;
  top: 14px;
  right: 16px;
  z-index: 2;
  padding: 4px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--el-bg-color) 88%, transparent);
  backdrop-filter: blur(6px);
}

.plugin-select-item :deep(.plugin-card) {
  height: 100%;
}

.plugin-select-item :deep(.plugin-card-header) {
  padding-right: 26px;
}

.plugin-list-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 12px;
}

.plugin-list-row__name {
  font-size: 14px;
  color: var(--el-text-color-primary);
  line-height: 1.4;
  word-break: break-all;
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

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-metric {
  border: 1px solid var(--el-border-color-light);
  border-radius: 12px;
  padding: 12px 14px;
  background: var(--el-fill-color-lighter);
}

.summary-metric__label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.summary-metric__value {
  margin-top: 6px;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.summary-section {
  margin-bottom: 16px;
}

.summary-section__title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.summary-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}

.summary-row:last-child {
  border-bottom: none;
}

.summary-row__label {
  flex-shrink: 0;
  width: 88px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.summary-row__value {
  min-width: 0;
  font-size: 13px;
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.summary-chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.summary-chip {
  max-width: 100%;
}

.summary-warning-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.summary-warning {
  border-left: 3px solid var(--el-color-warning);
  background: color-mix(in srgb, var(--el-color-warning) 10%, var(--el-bg-color));
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.result-raw {
  margin-top: 16px;
}

.result-block {
  margin: 0;
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

.package-list-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  word-break: break-all;
}

.package-list-meta__label {
  flex-shrink: 0;
}

.package-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.package-list-item {
  width: 100%;
  border: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  text-align: left;
  cursor: pointer;
  transition: all 0.2s ease;
}

.package-list-item:hover {
  transform: translateY(-1px);
  box-shadow: var(--el-box-shadow-light);
}

.package-list-item--active {
  border-color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 6%, var(--el-bg-color));
}

.package-list-item__main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.package-list-item__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.package-list-item__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.package-list-item__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
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

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .package-list-item {
    flex-direction: column;
    align-items: flex-start;
  }

  .package-list-item__actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}
</style>
