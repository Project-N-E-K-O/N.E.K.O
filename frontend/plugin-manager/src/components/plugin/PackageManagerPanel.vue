<template>
  <div class="package-manager" :class="{ 'package-manager--embedded': embedded }">
    <div class="toolbar-row" :class="{ 'toolbar-row--embedded': embedded }">
      <div v-if="embedded" class="embedded-heading">
        <div class="embedded-heading__copy">
          <span class="embedded-heading__title">包管理</span>
          <span class="embedded-heading__hint">复用左侧插件列表的筛选、多选和分类结果</span>
        </div>
        <div class="embedded-heading__actions">
          <el-button class="history-button" plain @click="openResultDialog">
            执行记录
            <el-badge v-if="resultHistory.length > 0" :value="resultHistory.length" class="history-badge" />
          </el-button>
          <el-button text circle @click="$emit('close')">
            <el-icon><Close /></el-icon>
          </el-button>
        </div>
      </div>

      <el-button v-else class="history-button" plain @click="openResultDialog">
        执行记录
        <el-badge v-if="resultHistory.length > 0" :value="resultHistory.length" class="history-badge" />
      </el-button>
    </div>

    <div class="main-grid" :class="{ 'main-grid--embedded': embedded }">
      <PluginSelectorPanel
        v-if="!embedded"
        :loading="pluginsLoading"
        :total-count="selectablePlugins.length"
        :selected-count="selectedPluginIds.length"
        :plugin-filter="pluginFilter"
        :use-regex="useRegex"
        :filter-mode="filterMode"
        :regex-error="regexError"
        :selected-types="selectedTypes"
        :layout-mode="layoutMode"
        :plugin-count="pluginCount"
        :adapter-count="adapterCount"
        :extension-count="extensionCount"
        :filtered-pure-plugins="filteredPurePlugins"
        :filtered-adapters="filteredAdapters"
        :filtered-extensions="filteredExtensions"
        :selected-plugin-ids="selectedPluginIds"
        @refresh="refreshPluginSources"
        @select-all-visible="selectAllVisible"
        @clear-selection="clearSelection"
        @toggle-plugin="togglePlugin"
        @update:plugin-filter="pluginFilter = $event"
        @update:use-regex="useRegex = $event"
        @update:filter-mode="filterMode = $event"
        @update:selected-types="selectedTypes = $event"
        @update:layout-mode="layoutMode = $event"
      />

      <div class="content-stack">
        <div v-if="embedded" class="embedded-selection-summary">
          <el-tag size="small" type="primary">已选 {{ selectedPluginIds.length }}</el-tag>
          <el-tag size="small" type="info">可打包 {{ selectablePlugins.length }}</el-tag>
          <span class="embedded-selection-summary__text">
            打包和整合分析默认使用左侧当前可见范围与已选插件。
          </span>
        </div>

        <el-card class="operations-card">
          <template #header>
            <div class="card-header">
              <span>包管理</span>
              <el-tag size="small" type="info">目标 {{ resolvedPackTargets.length }}</el-tag>
            </div>
          </template>

          <el-tabs v-model="activeTab" stretch class="pkg-tabs">
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
                  <el-input v-model="unpackForm.plugins_root" placeholder="默认写入我的文档下的用户插件目录" />
                </el-form-item>

                <el-form-item label="Profiles 目录">
                  <el-input
                    v-model="unpackForm.profiles_root"
                    placeholder="默认写入我的文档下的 .neko-package-profiles 目录"
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

        <PackageArchiveListPanel
          :loading="packagesLoading"
          :total-count="localPackages.length"
          :target-dir="targetDir"
          :active-package="packageRef.package"
          :package-filter-type="packageFilterType"
          :packages="filteredLocalPackages"
          @refresh="refreshPackageSources"
          @select="selectPackage"
          @inspect="inspectSelectedPackage"
          @verify="verifySelectedPackage"
          @prepare-unpack="prepareUnpackPackage"
          @update:package-filter-type="packageFilterType = $event"
        />
      </div>
    </div>

    <PackageResultPanel
      v-model:visible="resultDialogVisible"
      :result-history="resultHistory"
      :active-result-id="activeResultId"
      :active-result-record="activeResultRecord"
      @select="setActiveResult"
    />
  </div>
</template>

<script setup lang="ts">
import { Close } from '@element-plus/icons-vue'
import PackageArchiveListPanel from '@/components/plugin/PackageArchiveListPanel.vue'
import PackageResultPanel from '@/components/plugin/PackageResultPanel.vue'
import PluginSelectorPanel from '@/components/plugin/PluginSelectorPanel.vue'
import { usePackageManager } from '@/composables/usePackageManager'

withDefaults(
  defineProps<{
    embedded?: boolean
  }>(),
  {
    embedded: false,
  },
)

defineEmits<{
  close: []
}>()

const {
  activeTab,
  layoutMode,
  packMode,
  pluginFilter,
  useRegex,
  filterMode,
  regexError,
  selectedTypes,
  pluginsLoading,
  packagesLoading,
  localPackages,
  targetDir,
  packageFilterType,
  packing,
  inspecting,
  verifying,
  unpacking,
  analyzing,
  resultDialogVisible,
  resultHistory,
  activeResultId,
  activeResultRecord,
  packForm,
  packageRef,
  unpackForm,
  analyzeForm,
  selectablePlugins,
  pluginCount,
  adapterCount,
  extensionCount,
  filteredPurePlugins,
  filteredAdapters,
  filteredExtensions,
  selectedPluginIds,
  resolvedPackTargets,
  filteredLocalPackages,
  setActiveResult,
  openResultDialog,
  togglePlugin,
  selectAllVisible,
  clearSelection,
  refreshPluginSources,
  refreshPackageSources,
  selectPackage,
  inspectSelectedPackage,
  verifySelectedPackage,
  prepareUnpackPackage,
  handlePack,
  handleInspect,
  handleVerify,
  handleUnpack,
  handleAnalyze,
} = usePackageManager()
</script>

<style scoped>
.package-manager {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.package-manager--embedded {
  gap: 14px;
}

.main-grid {
  display: grid;
  grid-template-columns: 440px minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.main-grid--embedded {
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.toolbar-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
}

.toolbar-row--embedded {
  justify-content: stretch;
}

.embedded-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 14px 16px;
  border-radius: 18px;
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--el-color-primary) 10%, white), color-mix(in srgb, var(--el-color-info) 9%, white));
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 12%, var(--el-border-color));
}

.embedded-heading__copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.embedded-heading__title {
  font-size: 15px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.embedded-heading__hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.embedded-heading__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.embedded-selection-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 14px;
  border-radius: 16px;
  background: color-mix(in srgb, var(--el-fill-color-light) 78%, white);
  border: 1px solid color-mix(in srgb, var(--el-color-info) 10%, var(--el-border-color));
}

.embedded-selection-summary__text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.history-button {
  position: relative;
  border-radius: 999px;
  padding-inline: 18px;
}

.history-badge {
  margin-left: 10px;
}

.operations-card {
  border-radius: 18px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.action-row {
  display: flex;
  gap: 12px;
  margin-top: 6px;
}

.hint-row {
  margin: 6px 0 4px;
}

/* ── Tab content transition ── */
.pkg-tabs :deep(.el-tabs__content) {
  overflow: visible;
}

.pkg-tabs :deep(.el-tab-pane) {
  animation: tab-enter 0.34s cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes tab-enter {
  from {
    opacity: 0;
    transform: scale(0.97) translateY(8px);
    filter: blur(4px);
  }
  to {
    opacity: 1;
    transform: scale(1) translateY(0);
    filter: blur(0);
  }
}

/* ── Tab nav bar polish ── */
.pkg-tabs :deep(.el-tabs__nav-wrap) {
  margin-bottom: 4px;
}

.pkg-tabs :deep(.el-tabs__item) {
  transition:
    color 0.24s ease,
    font-weight 0.24s ease;
}

.pkg-tabs :deep(.el-tabs__active-bar) {
  transition:
    transform 0.34s cubic-bezier(0.22, 1, 0.36, 1),
    width 0.34s cubic-bezier(0.22, 1, 0.36, 1);
}

@media (max-width: 1380px) {
  .main-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .embedded-heading {
    flex-direction: column;
    align-items: stretch;
  }

  .embedded-heading__actions {
    justify-content: space-between;
  }
}

@media (prefers-reduced-motion: reduce) {
  .pkg-tabs :deep(.el-tab-pane) {
    animation: none;
  }
}
</style>
