<template>
  <div class="package-manager">
    <el-row :gutter="20">
      <el-col :span="24">
        <el-card class="hero-card">
          <div class="hero">
            <div>
              <div class="hero-kicker">neko-plugin-cli</div>
              <h2 class="hero-title">包管理</h2>
              <p class="hero-subtitle">
                在前端统一完成插件打包、包检查、校验、解包和整合包分析。
              </p>
            </div>
            <div class="hero-side">
              <el-statistic title="本地插件" :value="localPlugins.length" />
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="20" class="top-row">
      <el-col :span="8">
        <el-card class="plugins-card">
          <template #header>
            <div class="card-header">
              <span>本地插件</span>
              <el-button text :loading="pluginsLoading" @click="fetchLocalPlugins">刷新</el-button>
            </div>
          </template>

          <el-empty v-if="!pluginsLoading && localPlugins.length === 0" description="未发现可打包插件" />

          <div v-else class="plugin-list">
            <button
              v-for="plugin in localPlugins"
              :key="plugin"
              class="plugin-chip"
              :class="{ 'plugin-chip--active': selectedPlugin === plugin }"
              @click="selectPlugin(plugin)"
            >
              {{ plugin }}
            </button>
          </div>
        </el-card>
      </el-col>

      <el-col :span="16">
        <el-card class="result-card">
          <template #header>
            <div class="card-header">
              <span>最近结果</span>
              <el-tag v-if="resultKind" size="small" type="info">{{ resultKind }}</el-tag>
            </div>
          </template>

          <el-empty v-if="!resultText" description="执行操作后会在这里显示结果摘要" />
          <pre v-else class="result-block">{{ resultText }}</pre>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="tabs-card">
      <el-tabs v-model="activeTab" stretch>
        <el-tab-pane label="打包" name="pack">
          <div class="panel-grid">
            <div class="panel-form">
              <el-form label-position="top">
                <el-form-item label="插件">
                  <el-select v-model="packForm.plugin" placeholder="选择插件" clearable filterable>
                    <el-option
                      v-for="plugin in localPlugins"
                      :key="plugin"
                      :label="plugin"
                      :value="plugin"
                    />
                  </el-select>
                </el-form-item>

                <el-form-item label="打包全部">
                  <el-switch v-model="packForm.pack_all" />
                </el-form-item>

                <el-form-item label="输出目录">
                  <el-input v-model="packForm.target_dir" placeholder="默认使用 neko-plugin-cli/target" />
                </el-form-item>

                <el-form-item label="保留 staging">
                  <el-switch v-model="packForm.keep_staging" />
                </el-form-item>

                <div class="action-row">
                  <el-button type="primary" :loading="packing" @click="handlePack">
                    执行打包
                  </el-button>
                </div>
              </el-form>
            </div>

            <div class="panel-side">
              <el-alert
                type="info"
                :closable="false"
                title="适合快速导出 .neko-plugin 包"
                description="如果勾选“打包全部”，会遍历本地插件目录；否则只处理当前选中的插件。"
              />
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="检查 / 校验" name="inspect">
          <div class="panel-grid">
            <div class="panel-form">
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
            </div>

            <div class="panel-side">
              <el-descriptions
                v-if="inspectResult"
                :column="1"
                border
                class="inspect-summary"
              >
                <el-descriptions-item label="包 ID">{{ inspectResult.package_id }}</el-descriptions-item>
                <el-descriptions-item label="类型">{{ inspectResult.package_type }}</el-descriptions-item>
                <el-descriptions-item label="版本">{{ inspectResult.version || '-' }}</el-descriptions-item>
                <el-descriptions-item label="Hash 已校验">
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
              <el-alert
                v-else
                type="info"
                :closable="false"
                title="包检查会读取 manifest、metadata 和 payload 信息"
              />
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="解包" name="unpack">
          <div class="panel-grid">
            <div class="panel-form">
              <el-form label-position="top">
                <el-form-item label="包路径">
                  <el-input v-model="unpackForm.package" placeholder="例如 qq_auto_reply.neko-plugin" />
                </el-form-item>

                <el-form-item label="插件目录">
                  <el-input v-model="unpackForm.plugins_root" placeholder="默认写入 plugin/plugins" />
                </el-form-item>

                <el-form-item label="Profiles 目录">
                  <el-input v-model="unpackForm.profiles_root" placeholder="默认写入 plugin/.neko-package-profiles" />
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
            </div>

            <div class="panel-side">
              <el-alert
                type="warning"
                :closable="false"
                title="解包会写入插件目录"
                description="建议先用“检查 / 校验”确认包内容，再决定是否写入正式运行目录。"
              />
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="整合包分析" name="analyze">
          <div class="panel-grid">
            <div class="panel-form">
              <el-form label-position="top">
                <el-form-item label="插件列表">
                  <el-select
                    v-model="analyzeForm.plugins"
                    multiple
                    filterable
                    placeholder="选择多个插件"
                  >
                    <el-option
                      v-for="plugin in localPlugins"
                      :key="plugin"
                      :label="plugin"
                      :value="plugin"
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
            </div>

            <div class="panel-side">
              <el-alert
                type="success"
                :closable="false"
                title="分析会检查 SDK overlap 和共同依赖"
              />
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
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

const activeTab = ref('pack')
const localPlugins = ref<string[]>([])
const selectedPlugin = ref('')
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
  pack_all: false,
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

function setResult(kind: string, payload: unknown) {
  resultKind.value = kind
  resultText.value = JSON.stringify(payload, null, 2)
}

function selectPlugin(plugin: string) {
  selectedPlugin.value = plugin
  packForm.value.plugin = plugin
}

async function fetchLocalPlugins() {
  pluginsLoading.value = true
  try {
    const response = await getPluginCliPlugins()
    localPlugins.value = response.plugins
    const firstPlugin = response.plugins[0]
    if (!selectedPlugin.value && firstPlugin) {
      selectPlugin(firstPlugin)
    }
  } catch (error) {
    console.error('Failed to fetch local plugin list:', error)
  } finally {
    pluginsLoading.value = false
  }
}

async function handlePack() {
  packing.value = true
  inspectResult.value = null
  try {
    const payload: PluginCliPackRequest = {
      plugin: packForm.value.pack_all ? undefined : packForm.value.plugin,
      pack_all: !!packForm.value.pack_all,
      target_dir: packForm.value.target_dir || undefined,
      keep_staging: !!packForm.value.keep_staging,
    }
    const response = await packPluginCli(payload)
    setResult('pack', response)
    ElMessage.success(`打包完成，成功 ${response.packed_count} 个`)
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
    if (response.ok) {
      ElMessage.success('包校验通过')
    } else {
      ElMessage.warning('包未通过校验')
    }
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

onMounted(() => {
  fetchLocalPlugins()
})
</script>

<style scoped>
.package-manager {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.top-row {
  margin-top: 20px;
}

.hero-card,
.plugins-card,
.result-card,
.tabs-card {
  border-radius: 18px;
}

.hero {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
  padding: 8px 4px;
}

.hero-kicker {
  font-size: 12px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--el-color-primary);
  margin-bottom: 10px;
}

.hero-title {
  margin: 0;
  font-size: 28px;
  line-height: 1.1;
}

.hero-subtitle {
  margin: 10px 0 0;
  max-width: 720px;
  color: var(--el-text-color-secondary);
}

.hero-side {
  min-width: 140px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.plugin-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.plugin-chip {
  border: 1px solid var(--el-border-color);
  background: var(--el-bg-color);
  color: var(--el-text-color-primary);
  padding: 10px 14px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.plugin-chip:hover,
.plugin-chip--active {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
  background: color-mix(in srgb, var(--el-color-primary) 10%, transparent);
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

.panel-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
  gap: 20px;
}

.panel-form,
.panel-side {
  min-width: 0;
}

.action-row {
  display: flex;
  gap: 12px;
  margin-top: 6px;
}

.inspect-summary {
  width: 100%;
}

@media (max-width: 1100px) {
  .panel-grid {
    grid-template-columns: 1fr;
  }

  .hero {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
