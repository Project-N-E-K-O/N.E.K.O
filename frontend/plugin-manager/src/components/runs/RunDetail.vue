<template>
  <div class="run-detail-content" data-yui-guide-id="runs-detail-summary">
    <div class="run-summary">
      <div class="run-summary__identity">
        <h2>{{ run.entry_id }}</h2>
        <p>{{ run.plugin_id }}</p>
      </div>
      <RunStatusBadge :status="run.status" />
    </div>
    <div v-if="detail.error" class="detail-feedback" role="alert">
      <span>{{ t(detail.error) }}</span
      ><el-button size="small" @click="$emit('retry')">{{ t('runs.retry') }}</el-button>
    </div>
    <div v-if="!detail.loaded && !detail.error" class="detail-loading" role="status">
      <span>{{ t('runs.loadingDetail') }}</span
      ><i></i><i></i><i></i>
    </div>
    <dl v-if="detail.loaded" class="run-facts">
      <div>
        <dt>{{ t('runs.startedAt') }}</dt>
        <dd>{{ formatRunTimestamp(run.started_at) }}</dd>
      </div>
      <div>
        <dt>{{ t(active ? 'runs.elapsed' : 'runs.duration') }}</dt>
        <dd>{{ duration }}</dd>
      </div>
      <div v-if="run.finished_at">
        <dt>{{ t('runs.finishedAt') }}</dt>
        <dd>{{ formatRunTimestamp(run.finished_at) }}</dd>
      </div>
      <div v-if="run.stage">
        <dt>{{ t('runs.stage') }}</dt>
        <dd>{{ run.stage }}</dd>
      </div>
    </dl>
    <details v-if="detail.loaded" class="run-metadata">
      <summary>{{ t('runs.moreDetails') }}</summary>
      <dl>
        <div>
          <dt>{{ t('runs.runId') }}</dt>
          <dd class="run-id">{{ run.run_id }}</dd>
        </div>
        <div>
          <dt>{{ t('runs.createdAt') }}</dt>
          <dd>{{ formatRunTimestamp(run.created_at) }}</dd>
        </div>
        <div v-if="run.message">
          <dt>{{ t('runs.message') }}</dt>
          <dd>{{ run.message }}</dd>
        </div>
      </dl>
    </details>
    <div v-if="detail.loaded && active" class="run-execution">
      <p v-if="run.status === 'cancel_requested'">{{ t('runs.cancelSuccess') }}</p>
      <div v-else-if="progress !== null" class="run-progress">
        <span>{{ t('runs.progress') }}</span
        ><strong>{{ progress }}%</strong>
        <el-progress :percentage="progress" :show-text="false" :stroke-width="5" />
      </div>
      <p v-else>
        {{ t(run.status === 'queued' ? 'runs.waitingForStart' : 'runs.progressUnknown') }}
      </p>
    </div>
    <div v-if="cancelUncertain" class="detail-feedback" role="alert">
      <span>{{ t('runs.operationOutcomeUnknown') }}</span>
    </div>
    <section
      v-if="detail.loaded && (run.error || ['failed', 'timeout'].includes(run.status))"
      class="run-error"
      role="status"
    >
      <h3>{{ t(runStatusKey(run.status)) }}</h3>
      <p v-if="errorMessage">{{ errorMessage }}</p>
      <details v-if="run.error">
        <summary>{{ t('runs.errorDetails') }}</summary>
        <pre>{{ formattedError }}</pre>
      </details>
    </section>
    <RunOutput :items="outputs" :resource="output" @retry="$emit('retry-output')" />
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDocumentVisibility, useIntervalFn } from '@vueuse/core'
import type { RunRecord, ExportItem } from '@/api/runs'
import type { RunResourceState } from '@/composables/useRunWorkbench'
import {
  formatRunTimestamp,
  formatRunDuration,
  runDurationSeconds,
  runProgress,
  runStatusKey,
} from '@/utils/runDisplay'
import RunStatusBadge from './RunStatusBadge.vue'
import RunOutput from './RunOutput.vue'
const props = defineProps<{
  run: RunRecord
  outputs: ExportItem[]
  detail: RunResourceState
  output: RunResourceState
  cancelUncertain: boolean
  visible?: boolean
}>()
defineEmits<{ retry: []; 'retry-output': [] }>()
const { t, locale } = useI18n()
const now = ref(Date.now() / 1000)
const active = computed(() => ['running', 'queued', 'cancel_requested'].includes(props.run.status))
const visibility = useDocumentVisibility()
const { pause, resume } = useIntervalFn(
  () => {
    now.value = Date.now() / 1000
  },
  1000,
  { immediate: false }
)
watch(
  () => [props.run.run_id, props.run.status, props.run.started_at, props.visible, visibility.value],
  () => {
    pause()
    now.value = Date.now() / 1000
    if (
      active.value &&
      props.run.started_at &&
      props.visible !== false &&
      visibility.value === 'visible'
    )
      resume()
  },
  { immediate: true }
)
const duration = computed(() =>
  formatRunDuration(runDurationSeconds(props.run, now.value), locale.value)
)
const progress = computed(() => runProgress(props.run.progress))
const errorMessage = computed(() =>
  typeof props.run.error === 'object' ? props.run.error?.message : props.run.error
)
const formattedError = computed(() =>
  typeof props.run.error === 'string' ? props.run.error : JSON.stringify(props.run.error, null, 2)
)
</script>
<style scoped>
.run-detail-content {
  min-width: 0;
}
.run-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}
.run-summary__identity {
  min-width: 0;
  flex: 1 1 12rem;
}
.run-summary h2 {
  margin: 0 0 5px;
  font-size: 1.125rem;
  line-height: 1.35;
  font-weight: 650;
  overflow-wrap: anywhere;
}
.run-summary p {
  color: var(--el-text-color-secondary);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
  margin: 0;
}
.run-summary p:last-child {
  margin-top: 6px;
  color: var(--el-text-color-regular);
  font-size: 0.8125rem;
}
.run-summary :deep(.run-status) {
  flex-shrink: 0;
  padding: 5px 9px;
  border-radius: var(--run-radius);
  background: var(--surface-inset);
}
.run-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(132px, 1fr));
  gap: 12px 16px;
  padding: 16px 0 10px;
  margin: 0;
}
.run-facts > div {
  min-width: 0;
}
.run-facts dt,
.run-metadata dt {
  color: var(--el-text-color-secondary);
  font-size: 0.75rem;
  margin-bottom: 5px;
}
.run-facts dd,
.run-metadata dd {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 0.8125rem;
  overflow-wrap: anywhere;
  font-variant-numeric: tabular-nums;
}
.run-id {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.75rem;
  user-select: text;
}
.run-metadata {
  font-size: 0.75rem;
  color: var(--el-text-color-secondary);
}
.run-metadata summary {
  cursor: pointer;
  width: fit-content;
  padding: 4px 0;
}
.run-metadata dl {
  display: grid;
  gap: 10px;
  margin: 10px 0 0;
}
.run-metadata dl > div {
  min-width: 0;
}
.run-metadata summary:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
  border-radius: var(--run-radius);
}
.run-execution {
  margin-top: 16px;
  color: var(--el-text-color-regular);
  font-size: 0.8125rem;
}
.run-progress {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
}
.run-progress :deep(.el-progress) {
  grid-column: 1/-1;
}
.run-progress strong {
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.run-error {
  margin-top: 16px;
  padding: 16px;
  border-inline-start: 2px solid var(--el-color-danger);
  border-radius: var(--run-radius);
  background: color-mix(in srgb, var(--el-color-danger) 6%, var(--surface-workspace));
}
.run-error h3 {
  margin: 0 0 7px;
  color: var(--el-color-danger);
  font-size: 0.875rem;
}
.run-error p {
  margin: 0;
  color: var(--el-text-color-regular);
  font-size: 0.8125rem;
  overflow-wrap: anywhere;
}
.run-error details {
  margin-top: 12px;
  color: var(--el-text-color-secondary);
  font-size: 0.75rem;
}
.run-error summary {
  cursor: pointer;
}
.run-error pre {
  margin: 10px 0 0;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.detail-feedback {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 20px;
  color: var(--el-color-warning);
  font-size: 0.8125rem;
}
.detail-loading {
  padding: 24px 0;
  display: grid;
  gap: 16px;
  color: var(--el-text-color-secondary);
  font-size: 0.8125rem;
}
.detail-loading i {
  width: 80%;
  height: 12px;
  background: var(--surface-border);
  border-radius: var(--run-radius);
}
.detail-loading i:last-child {
  width: 60%;
}
@container run-workbench (max-width: 380px) {
  .run-facts {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .run-facts > div {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 5px 12px;
  }
  .run-facts dt {
    margin-bottom: 0;
  }
  .run-facts dd {
    text-align: end;
  }
}
</style>
