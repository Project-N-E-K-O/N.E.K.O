<template>
  <el-dialog
    :model-value="visible"
    width="min(1180px, 92vw)"
    top="6vh"
    destroy-on-close
    append-to-body
    class="package-result-dialog"
    @update:model-value="$emit('update:visible', $event)"
  >
    <template #header>
      <div class="dialog-header">
        <div>
          <div class="dialog-title">包管理执行记录</div>
          <div class="dialog-subtitle">保留最近 {{ resultHistory.length }} 条执行结果</div>
        </div>
      </div>
    </template>

    <div v-if="resultHistory.length === 0" class="dialog-empty">
      <el-empty description="执行包管理操作后，这里会显示记录" />
    </div>

    <div v-else class="dialog-layout">
      <aside class="history-pane">
        <button
          v-for="record in resultHistory"
          :key="record.id"
          type="button"
          class="history-item"
          :class="{ 'history-item--active': record.id === activeResultId }"
          @click="$emit('select', record.id)"
        >
          <div class="history-item__top">
            <el-tag size="small" effect="dark" type="info">{{ record.kind }}</el-tag>
            <span class="history-item__time">{{ record.createdAt }}</span>
          </div>
          <div class="history-item__summary">
            {{ record.summaryHighlights[0]?.value || record.summaryWarnings[0] || '查看详情' }}
          </div>
        </button>
      </aside>

      <section class="detail-pane">
        <template v-if="activeResultRecord">
          <div class="detail-header">
            <div>
              <div class="detail-title">结果详情</div>
              <div class="detail-meta">{{ activeResultRecord.createdAt }}</div>
            </div>
            <el-tag type="primary" effect="plain">{{ activeResultRecord.kind }}</el-tag>
          </div>

          <div v-if="activeResultRecord.summaryMetrics.length > 0" class="summary-grid">
            <div
              v-for="metric in activeResultRecord.summaryMetrics"
              :key="metric.label"
              class="summary-metric"
            >
              <div class="summary-metric__label">{{ metric.label }}</div>
              <div class="summary-metric__value">{{ metric.value }}</div>
            </div>
          </div>

          <div v-if="activeResultRecord.inspectResult" class="inspect-panel">
            <el-descriptions :column="2" border class="inspect-summary">
              <el-descriptions-item label="包 ID">{{ activeResultRecord.inspectResult.package_id }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ activeResultRecord.inspectResult.package_type }}</el-descriptions-item>
              <el-descriptions-item label="版本">{{ activeResultRecord.inspectResult.version || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Schema">{{ activeResultRecord.inspectResult.schema_version || '-' }}</el-descriptions-item>
              <el-descriptions-item label="Hash 校验">
                <el-tag
                  :type="activeResultRecord.inspectResult.payload_hash_verified === true ? 'success' : activeResultRecord.inspectResult.payload_hash_verified === false ? 'danger' : 'info'"
                >
                  {{ activeResultRecord.inspectResult.payload_hash_verified === null ? '未校验' : activeResultRecord.inspectResult.payload_hash_verified ? '通过' : '失败' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="Profiles">
                {{ activeResultRecord.inspectResult.profile_names.join(', ') || '-' }}
              </el-descriptions-item>
            </el-descriptions>
          </div>

          <div v-if="activeResultRecord.summaryHighlights.length > 0" class="summary-section">
            <div
              v-for="item in activeResultRecord.summaryHighlights"
              :key="`${item.label}-${item.value}`"
              class="summary-row"
            >
              <span class="summary-row__label">{{ item.label }}</span>
              <span class="summary-row__value">{{ item.value }}</span>
            </div>
          </div>

          <div v-if="activeResultRecord.summaryListItems.length > 0" class="summary-section">
            <div class="summary-section__title">明细</div>
            <div class="summary-chip-list">
              <el-tag
                v-for="item in activeResultRecord.summaryListItems"
                :key="item"
                effect="plain"
                class="summary-chip"
              >
                {{ item }}
              </el-tag>
            </div>
          </div>

          <div v-if="activeResultRecord.summaryWarnings.length > 0" class="summary-section">
            <div class="summary-section__title">注意</div>
            <div class="summary-warning-list">
              <div
                v-for="warning in activeResultRecord.summaryWarnings"
                :key="warning"
                class="summary-warning"
              >
                {{ warning }}
              </div>
            </div>
          </div>

          <div class="summary-section">
            <div class="summary-section__title">原始结果 JSON</div>
            <pre class="result-block">{{ activeResultRecord.resultText }}</pre>
          </div>
        </template>
      </section>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import type { PackageResultRecord } from '@/composables/usePackageManager'

defineProps<{
  visible: boolean
  resultHistory: PackageResultRecord[]
  activeResultId: string
  activeResultRecord: PackageResultRecord | null
}>()

defineEmits<{
  'update:visible': [value: boolean]
  select: [recordId: string]
}>()
</script>

<style scoped>
.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.dialog-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.dialog-subtitle {
  margin-top: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.dialog-empty {
  padding: 24px 0 8px;
}

.dialog-layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 20px;
  min-height: 60vh;
}

.history-pane {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 10px;
  max-height: 72vh;
  overflow: auto;
}

.history-item {
  width: 100%;
  border: 1px solid var(--el-border-color-light);
  border-radius: 16px;
  padding: 14px;
  background: linear-gradient(180deg, var(--el-fill-color-blank) 0%, var(--el-fill-color-lighter) 100%);
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
}

.history-item:hover {
  border-color: var(--el-color-primary-light-5);
  transform: translateY(-1px);
}

.history-item--active {
  border-color: var(--el-color-primary);
  box-shadow: 0 14px 30px color-mix(in srgb, var(--el-color-primary) 12%, transparent);
}

.history-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.history-item__time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.history-item__summary {
  margin-top: 10px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-primary);
  word-break: break-word;
}

.detail-pane {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 22px;
  padding: 20px;
  background: linear-gradient(180deg, color-mix(in srgb, var(--el-color-primary) 4%, var(--el-bg-color)) 0%, var(--el-bg-color) 42%);
  max-height: 72vh;
  overflow: auto;
}

.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.detail-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--el-text-color-primary);
}

.detail-meta {
  margin-top: 6px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.inspect-summary {
  width: 100%;
  margin-bottom: 16px;
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
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

@media (max-width: 980px) {
  .dialog-layout {
    grid-template-columns: 1fr;
  }

  .history-pane,
  .detail-pane {
    max-height: none;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
