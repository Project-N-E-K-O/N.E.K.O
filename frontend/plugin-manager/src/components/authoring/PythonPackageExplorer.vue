<template>
  <section class="authoring-details-panel" data-testid="python-package-explorer">
    <h3>{{ t('pythonPackages.installed') }} ({{ packages.length }})</h3>
    <div v-if="graph.nodes.length" class="package-explorer">
      <aside class="package-explorer-tree" :aria-label="t('pythonPackages.dependencyTree')">
        <el-input
          v-model="search"
          :placeholder="t('pythonPackages.search')"
          :aria-label="t('pythonPackages.search')"
          clearable
        />
        <el-tree
          :key="treeKey"
          :data="searchResults?.items ?? rootItems"
          :props="treeProps"
          node-key="key"
          :lazy="!search.trim()"
          :load="loadChildren"
          :default-expanded-keys="[...expandedKeys]"
          :default-expand-all="!!search.trim()"
          :expand-on-click-node="false"
          :current-node-key="selectedKey"
          highlight-current
          :empty-text="t('pythonPackages.empty')"
          @node-click="selectPackage"
          @node-expand="rememberExpanded"
          @node-collapse="rememberCollapsed"
        >
          <template #default="{ data }">
            <span
              class="package-tree-row"
              :class="{ 'package-tree-row--selected': data.id === selectedId }"
              :data-package-id="data.id"
              :title="data.label"
            >
              <span class="package-tree-name">{{ data.label }}</span>
              <span v-if="data.cycle" class="package-tree-note">{{
                t('pythonPackages.cycleReference')
              }}</span>
              <span v-else-if="data.status === 'missing'" class="package-tree-warning">{{
                t('pythonPackages.missing')
              }}</span>
              <span v-else-if="data.status === 'version_mismatch'" class="package-tree-warning">{{
                t('pythonPackages.incompatible')
              }}</span>
              <span v-else class="package-tree-version">{{ nodes.get(data.id)?.version }}</span>
              <el-tag
                v-if="(nodes.get(data.id)?.required_by.length ?? 0) > 1"
                size="small"
                type="info"
                >{{ t('pythonPackages.sharedDependency') }}</el-tag
              >
            </span>
          </template>
        </el-tree>
        <p v-if="searchResults?.truncated" class="authoring-small authoring-muted">
          {{ t('pythonPackages.searchLimited') }}
        </p>
      </aside>
      <article
        class="package-explorer-details"
        aria-live="polite"
        data-testid="python-package-info"
      >
        <template v-if="selected">
          <header class="package-info-heading">
            <h4>{{ selected.name }}</h4>
            <el-tag :type="selected.version ? 'info' : 'warning'">{{
              selected.version || t('pythonPackages.missing')
            }}</el-tag>
          </header>
          <p v-if="installedPackage?.summary" class="package-info-summary">
            {{ installedPackage.summary }}
          </p>
          <el-descriptions :column="1" border>
            <el-descriptions-item :label="t('pythonPackages.packageKind')">{{
              t(selected.direct ? 'pythonPackages.direct' : 'pythonPackages.other')
            }}</el-descriptions-item>
            <el-descriptions-item
              v-if="selected.declarations.length"
              :label="t('pythonPackages.versionConstraint')"
              ><code v-for="declaration in selected.declarations" :key="declaration">{{
                declaration
              }}</code></el-descriptions-item
            >
            <el-descriptions-item
              v-if="installedPackage?.requires_python"
              :label="t('pythonPackages.requiresPython')"
              ><code>{{ installedPackage.requires_python }}</code></el-descriptions-item
            >
          </el-descriptions>
          <h5>{{ t('pythonPackages.requiredBy') }}</h5>
          <div class="package-info-tags">
            <el-tag v-if="selected.direct" size="small">{{
              t('pythonPackages.projectUses')
            }}</el-tag>
            <el-tag v-for="id in selected.required_by" :key="id" size="small" type="info">{{
              nodes.get(id)?.name || id
            }}</el-tag>
            <span v-if="!selected.direct && !selected.required_by.length" class="authoring-muted">{{
              t('pythonPackages.noKnownParent')
            }}</span>
          </div>
          <h5>{{ t('pythonPackages.packageNeeds') }} ({{ selected.dependencies.length }})</h5>
          <ul v-if="selected.dependencies.length" class="package-info-dependencies">
            <li v-for="edge in selected.dependencies" :key="edge.id">
              <div>
                <strong>{{ nodes.get(edge.id)?.name || edge.id }}</strong>
                <span class="authoring-muted">{{ nodes.get(edge.id)?.version }}</span>
                <el-tag v-if="edge.status !== 'satisfied'" size="small" type="warning">{{
                  t(
                    edge.status === 'missing'
                      ? 'pythonPackages.missing'
                      : 'pythonPackages.incompatible'
                  )
                }}</el-tag>
              </div>
              <code v-for="requirement in edge.requirements" :key="requirement">{{
                requirement
              }}</code>
            </li>
          </ul>
          <p v-else class="authoring-muted">{{ t('pythonPackages.noDependencies') }}</p>
        </template>
        <p v-else class="authoring-muted">{{ t('pythonPackages.selectPackage') }}</p>
      </article>
    </div>
    <p v-else class="authoring-muted">{{ t('pythonPackages.empty') }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InstalledPythonPackage, PythonDependencyGraph } from '@/api/projectPythonPackages'

interface TreeItem {
  key: string
  id: string
  label: string
  path: string[]
  leaf: boolean
  cycle: boolean
  status: string
  children?: TreeItem[]
}
const props = defineProps<{ graph: PythonDependencyGraph; packages: InstalledPythonPackage[] }>()
const { t } = useI18n()
const search = ref('')
const selectedId = ref('')
const selectedKey = ref('')
const expandedKeys = ref(new Set<string>())
const treeProps = { label: 'label', isLeaf: 'leaf' }
const nodes = computed(() => new Map(props.graph.nodes.map((node) => [node.id, node])))
const selected = computed(() => nodes.value.get(selectedId.value))
const installedPackage = computed(() =>
  props.packages.find((pkg) => packageId(pkg.name) === selectedId.value)
)
const signature = computed(() => JSON.stringify(props.graph))
const treeKey = computed(() => `${signature.value}:${search.value.trim().toLowerCase()}`)
const matches = computed(() => {
  const query = search.value.trim().toLowerCase()
  return new Set(
    props.graph.nodes
      .filter((node) => !query || node.name.toLowerCase().includes(query))
      .map((node) => node.id)
  )
})
const visibleIds = computed(() => {
  if (!search.value.trim()) return new Set(nodes.value.keys())
  const visible = new Set(matches.value)
  const pending = [...visible]
  while (pending.length)
    for (const parent of nodes.value.get(pending.pop()!)?.required_by ?? []) {
      if (!visible.has(parent)) {
        visible.add(parent)
        pending.push(parent)
      }
    }
  return visible
})
function packageId(name: string) {
  return name.toLowerCase().replace(/[-_.]+/g, '-')
}
function item(id: string, path: string[], status = 'satisfied'): TreeItem {
  const cycle = path.includes(id)
  const next = [...path, id]
  const node = nodes.value.get(id)!
  const children = node.dependencies.filter((edge) => visibleIds.value.has(edge.id))
  return {
    key: next.join('/'),
    id,
    label: node.name,
    path: next,
    leaf: cycle || !children.length,
    cycle,
    status: path.length ? status : node.status,
  }
}
const rootItems = computed(() =>
  [...props.graph.roots, ...props.graph.other_roots]
    .filter((id) => visibleIds.value.has(id))
    .map((id) => item(id, []))
)
// Shared dependencies can produce exponentially many paths during automatic expansion.
const MAX_SEARCH_ITEMS = 500
const searchResults = computed(() => {
  if (!search.value.trim()) return null
  let remaining = MAX_SEARCH_ITEMS
  let truncated = false
  function branch(id: string, path: string[], status?: string): TreeItem | null {
    if (!remaining) {
      truncated = true
      return null
    }
    remaining -= 1
    const result = item(id, path, status)
    result.children = []
    if (!result.leaf) {
      for (const edge of nodes.value.get(id)!.dependencies) {
        if (!visibleIds.value.has(edge.id)) continue
        const child = branch(edge.id, result.path, edge.status)
        if (!child) break
        result.children.push(child)
      }
    }
    return result
  }
  const items: TreeItem[] = []
  for (const root of rootItems.value) {
    const result = branch(root.id, [])
    if (!result) break
    items.push(result)
  }
  return { items, truncated }
})
function loadChildren(
  node: { level: number; data: TreeItem },
  resolve: (children: TreeItem[]) => void
) {
  if (node.level === 0) {
    resolve(rootItems.value)
    return
  }
  const data = node.data
  if (data.cycle) {
    resolve([])
    return
  }
  resolve(
    (nodes.value.get(data.id)?.dependencies ?? [])
      .filter((edge) => visibleIds.value.has(edge.id))
      .map((edge) => item(edge.id, data.path, edge.status))
  )
}
function selectPackage(data: TreeItem) {
  selectedId.value = data.id
  selectedKey.value = data.key
}
function rememberExpanded(data: TreeItem) {
  if (!search.value.trim()) expandedKeys.value.add(data.key)
}
function rememberCollapsed(data: TreeItem) {
  if (!search.value.trim()) expandedKeys.value.delete(data.key)
}
watch(
  signature,
  () => {
    if (!nodes.value.has(selectedId.value)) {
      selectedId.value = props.graph.roots[0] ?? props.graph.other_roots[0] ?? ''
      selectedKey.value = selectedId.value
    }
    if (!expandedKeys.value.size)
      expandedKeys.value = new Set([...props.graph.roots, ...props.graph.other_roots])
  },
  { immediate: true }
)
watch(search, () => {
  if (search.value.trim()) {
    selectedId.value = [...matches.value][0] ?? ''
    selectedKey.value = ''
  }
})
</script>

<style scoped>
.package-explorer {
  display: grid;
  grid-template-columns: minmax(240px, 36%) minmax(0, 1fr);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 12px;
  overflow: hidden;
}
.package-explorer-tree {
  min-width: 0;
  padding: 14px;
  border-right: 1px solid var(--el-border-color-lighter);
}
.package-explorer-tree :deep(.el-tree) {
  margin-top: 12px;
  max-height: 430px;
  overflow: auto;
  background: transparent;
}
.package-explorer-tree :deep(.el-tree-node__content) {
  height: 36px;
  border-radius: 6px;
}
.package-tree-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  width: 100%;
  padding-right: 8px;
  font-size: 13px;
}
.package-tree-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.package-tree-row--selected .package-tree-name {
  color: var(--el-color-primary);
  font-weight: 600;
}
.package-tree-version,
.package-tree-note {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
}
.package-tree-warning {
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
  white-space: nowrap;
}
.package-explorer-details {
  padding: 18px;
  min-width: 0;
}
.package-info-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.package-info-heading h4 {
  margin: 0;
  font-size: 19px;
}
.package-info-summary {
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
.package-explorer-details h5 {
  margin: 20px 0 10px;
  font-size: 14px;
}
.package-info-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.package-info-dependencies {
  padding: 0;
  margin: 0;
  list-style: none;
}
.package-info-dependencies li {
  padding: 10px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.package-info-dependencies li > div {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.package-info-dependencies code {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow-wrap: anywhere;
}
@media (max-width: 700px) {
  .package-explorer {
    grid-template-columns: minmax(0, 1fr);
  }
  .package-explorer-tree {
    border-right: 0;
    border-bottom: 1px solid var(--el-border-color-lighter);
  }
  .package-explorer-tree :deep(.el-tree) {
    max-height: 260px;
  }
}
</style>
