<template>
  <div class="runs-page">
    <el-row :gutter="20">
      <el-col :span="10">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ $t('runs.title') }}</span>
              <div class="actions">
                <el-button :icon="Refresh" size="small" @click="handleRefresh" :loading="loading" />
              </div>
            </div>
          </template>

          <div v-if="!connected" class="hint">
            <el-alert type="warning" :closable="false">
              <template #title>
                <span>{{ $t('runs.wsDisconnected') }}</span>
              </template>
            </el-alert>
          </div>

          <el-table
            :data="runs"
            size="small"
            style="width: 100%"
            highlight-current-row
            @current-change="handleSelect"
            :row-key="(row: any) => row.run_id"
          >
            <el-table-column prop="status" :label="$t('runs.status')" width="110">
              <template #default="scope">
                <el-tag size="small" :type="statusTagType(scope.row.status)">
                  {{ scope.row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="plugin_id" :label="$t('runs.pluginId')" width="120" />
            <el-table-column prop="entry_id" :label="$t('runs.entryId')" />
            <el-table-column :label="$t('runs.updatedAt')" width="160">
              <template #default="scope">
                {{ formatTs(scope.row.updated_at) }}
              </template>
            </el-table-column>
          </el-table>

          <div v-if="runs.length === 0" class="empty">
            <el-empty :description="$t('runs.noRuns')" />
          </div>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>{{ $t('runs.detail') }}</span>
              <div class="actions">
                <el-button
                  v-if="selectedRun"
                  size="small"
                  :icon="Refresh"
                  @click="handleRefreshSelected"
                  :loading="loadingSelected"
                >
                  {{ $t('common.refresh') }}
                </el-button>
                <el-button
                  v-if="selectedRun && canCancelSelected"
                  size="small"
                  type="danger"
                  @click="handleCancel"
                >
                  {{ $t('runs.cancel') }}
                </el-button>
              </div>
            </div>
          </template>

          <div v-if="!selectedRun" class="empty">
            <el-empty :description="$t('runs.selectRun')" />
          </div>

          <div v-else class="detail">
            <el-descriptions :column="2" size="small" border>
              <el-descriptions-item :label="$t('runs.runId')">{{ selectedRun.run_id }}</el-descriptions-item>
              <el-descriptions-item :label="$t('runs.status')">{{ selectedRun.status }}</el-descriptions-item>
              <el-descriptions-item :label="$t('runs.pluginId')">{{ selectedRun.plugin_id }}</el-descriptions-item>
              <el-descriptions-item :label="$t('runs.entryId')">{{ selectedRun.entry_id }}</el-descriptions-item>
              <el-descriptions-item :label="$t('runs.stage')">{{ selectedRun.stage || '-' }}</el-descriptions-item>
              <el-descriptions-item :label="$t('runs.message')">{{ selectedRun.message || '-' }}</el-descriptions-item>
              <el-descriptions-item :label="$t('runs.progress')" :span="2">
                <el-progress
                  :percentage="Math.round(((selectedRun.progress ?? 0) as number) * 100)"
                  :stroke-width="14"
                />
              </el-descriptions-item>
              <el-descriptions-item :label="$t('runs.error')" :span="2">
                <pre class="error-text">{{ formatError(selectedRun.error) }}</pre>
              </el-descriptions-item>
            </el-descriptions>

            <div class="export-section">
              <div class="export-header">
                <span>{{ $t('runs.export') }}</span>
              </div>
              <el-table :data="selectedExports" size="small" style="width: 100%" :row-key="(row: any) => row.export_item_id">
                <el-table-column prop="type" :label="$t('runs.exportType')" width="120" />
                <el-table-column :label="$t('runs.exportContent')">
                  <template #default="scope">
                    <div v-if="scope.row.type === 'text'" class="export-text">{{ scope.row.text }}</div>
                    <div v-else-if="scope.row.type === 'url'"><a :href="scope.row.url" target="_blank">{{ scope.row.url }}</a></div>
                    <div v-else-if="scope.row.type === 'binary_url'"><a :href="scope.row.binary_url" target="_blank">{{ scope.row.binary_url }}</a></div>
                    <div v-else-if="scope.row.type === 'json'" class="export-json">
                      <el-collapse>
                        <el-collapse-item :title="scope.row.label || scope.row.description || 'JSON'">
                          <pre class="json-block">{{ formatJson(scope.row.json ?? scope.row.json_data) }}</pre>
                        </el-collapse-item>
                      </el-collapse>
                    </div>
                    <div v-else class="export-text">{{ scope.row.binary }}</div>
                  </template>
                </el-table-column>
                <el-table-column :label="$t('runs.createdAt')" width="160">
                  <template #default="scope">{{ formatTs(scope.row.created_at) }}</template>
                </el-table-column>
              </el-table>

              <div v-if="selectedExports.length === 0" class="empty">
                <el-empty :description="$t('runs.noExport')" />
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import dayjs from 'dayjs'
import { useRunsStore } from '@/stores/runs'

const runsStore = useRunsStore()
const { t } = useI18n()

const loading = ref(false)
const loadingSelected = ref(false)
const lastSelectedRunId = ref<string | null>(null)

const connected = computed(() => runsStore.connected)
const runs = computed(() => runsStore.runs)
const selectedRun = computed(() => runsStore.selectedRun)
const selectedExports = computed(() => runsStore.selectedExports)

const canCancelSelected = computed(() => {
  const r: any = selectedRun.value
  if (!r) return false
  const st = String(r.status || '')
  return st === 'queued' || st === 'running' || st === 'cancel_requested'
})

function formatTs(ts: any): string {
  const n = Number(ts)
  if (!Number.isFinite(n) || n <= 0) return '-'
  return dayjs(n * 1000).format('YYYY-MM-DD HH:mm:ss')
}

function formatError(err: any): string {
  if (!err) return '-'
  try {
    if (typeof err === 'string') return err
    if (typeof err === 'object') return JSON.stringify(err, null, 2)
    return String(err)
  } catch (_) {
    return String(err)
  }
}

function formatJson(json: any): string {
  return JSON.stringify(json, null, 2)
}

function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'succeeded') return 'success'
  if (status === 'running' || status === 'queued' || status === 'cancel_requested') return 'warning'
  if (status === 'failed' || status === 'timeout') return 'danger'
  return 'info'
}

async function handleRefresh() {
  loading.value = true
  try {
    await runsStore.refreshRuns()
  } catch (e: any) {
    ElMessage.error(String(e?.message || e || 'Failed to refresh runs'))
  } finally {
    loading.value = false
  }
}

async function handleRefreshSelected() {
  if (!selectedRun.value) return
  loadingSelected.value = true
  try {
    await Promise.all([
      runsStore.loadRun(selectedRun.value.run_id),
      runsStore.loadExports(selectedRun.value.run_id)
    ])
  } catch (e: any) {
    ElMessage.error(String(e?.message || e || 'Failed to refresh run'))
  } finally {
    loadingSelected.value = false
  }
}

async function handleCancel() {
  if (!selectedRun.value) return
  try {
    await ElMessageBox.confirm(
      String(t('runs.cancelConfirmMessage', { runId: selectedRun.value.run_id })),
      String(t('runs.cancelConfirmTitle')),
      {
        type: 'warning'
      }
    )
  } catch (_) {
    return
  }

  try {
    await runsStore.cancelRun(selectedRun.value.run_id)
    await handleRefreshSelected()
    ElMessage.success(String(t('runs.cancelSuccess')))
  } catch (e: any) {
    ElMessage.error(String(e?.message || e || 'Failed to cancel run'))
  }
}

async function handleSelect(row: any) {
  if (!row || !row.run_id) return
  const rid = String(row.run_id)
  if (!rid) return
  if (loadingSelected.value) return
  if (lastSelectedRunId.value === rid) return
  lastSelectedRunId.value = rid
  runsStore.selectRun(rid)
  await handleRefreshSelected()
}

onMounted(async () => {
  await handleRefresh()
})

onUnmounted(() => {
  runsStore.disconnect()
})
</script>

<style scoped>
.runs-page {
  padding: 0;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.actions {
  display: flex;
  gap: 8px;
}

.hint {
  margin-bottom: 12px;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.export-section {
  margin-top: 10px;
}

.export-header {
  font-weight: 600;
  margin-bottom: 8px;
}

.export-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.export-json :deep(.el-collapse-item__header) {
  font-size: 12px;
  height: 28px;
  line-height: 28px;
}

.json-block {
  margin: 0;
  padding: 8px;
  font-size: 12px;
  background: var(--el-fill-color-lighter);
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
}

.error-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.empty {
  margin-top: 10px;
}
</style>
