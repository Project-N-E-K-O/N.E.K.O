<template>
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
</template>

<script setup lang="ts">
import type { PluginCliInspectResponse } from '@/api/pluginCli'

defineProps<{
  resultKind: string
  resultText: string
  inspectResult: PluginCliInspectResponse | null
  summaryMetrics: Array<{ label: string; value: string }>
  summaryHighlights: Array<{ label: string; value: string }>
  summaryListItems: string[]
  summaryWarnings: string[]
}>()
</script>

<style scoped>
.result-card {
  border-radius: 18px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
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

@media (max-width: 980px) {
  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
