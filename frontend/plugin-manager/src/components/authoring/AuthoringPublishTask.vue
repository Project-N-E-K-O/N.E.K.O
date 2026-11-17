<template>
  <section class="authoring-publish-task">
    <h4>
      <el-tag size="small" :type="TASK_TAG_TYPES[task.status]">
        {{ t(task.status === 'succeeded' && task.market_status &&
          !['published', 'already_published', 'approved'].includes(task.market_status)
          ? 'authoring.publishTaskMarketAccepted' : TASK_LABELS[task.status]) }}
      </el-tag>
      <span v-if="finishedAt" class="authoring-muted authoring-small">{{
        t('authoring.publishTaskLastRun', { time: finishedAt })
      }}</span>
      <span v-if="task.message" class="authoring-small">{{ task.message }}</span>
      <a
        v-if="task.status === 'succeeded' && task.release_url"
        class="authoring-small"
        href="#"
        @click.prevent="openExternalUrl(task.release_url)"
        >{{ task.release_url }}</a
      >
      <el-button
        v-if="active"
        class="authoring-publish-task__cancel"
        size="small"
        :icon="CircleClose"
        :loading="canceling"
        @click="emit('cancel')"
      >
        {{ t('authoring.publishCancelTask') }}
      </el-button>
    </h4>
    <!-- Only a failure watched here opens the log; an earlier run stays collapsed. -->
    <details v-if="task.log.length" :open="task.status === 'failed' && watched">
      <summary>{{ t('authoring.publishTaskLog') }}</summary>
      <pre>{{ task.log.join('\n') }}</pre>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CircleClose } from '@element-plus/icons-vue'
import { publishTaskActive, type PublishTask, type PublishTaskStatus } from '@/api/pluginProjects'
import { openExternalUrl } from '@/utils/openExternal'
import { formatRunTimestamp } from '@/utils/runDisplay'

const TASK_TAG_TYPES: Record<PublishTaskStatus, 'success' | 'danger' | 'warning' | 'info'> = {
  queued: 'info',
  running: 'warning',
  succeeded: 'success',
  failed: 'danger',
  canceled: 'info',
}
const TASK_LABELS: Record<PublishTaskStatus, string> = {
  queued: 'authoring.publishTaskQueued',
  running: 'authoring.publishTaskRunning',
  succeeded: 'authoring.publishTaskSucceeded',
  failed: 'authoring.publishTaskFailed',
  canceled: 'authoring.publishTaskCanceled',
}

/** The latest publish task, shown inside the step its mode belongs to. */
const props = defineProps<{
  task: PublishTask
  /** True once the panel saw the task active, so its outcome is news, not history. */
  watched: boolean
  canceling: boolean
}>()
const emit = defineEmits<{ cancel: [] }>()
const { t } = useI18n()
const active = computed(() => publishTaskActive(props.task))
const finishedAt = computed(() =>
  props.task.finished_at ? formatRunTimestamp(props.task.finished_at, true) : ''
)
</script>

<style scoped>
.authoring-publish-task {
  margin-top: var(--spacing-sm);
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-panel);
  background: var(--surface-card);
}
.authoring-publish-task h4 {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-sm);
  margin: 0;
}
.authoring-publish-task__cancel {
  margin-left: auto;
}
.authoring-publish-task details pre {
  margin: var(--spacing-xs) 0 0;
  max-height: 240px;
  overflow: auto;
  padding: var(--spacing-sm);
  border-radius: var(--radius-control);
  background: var(--surface-inset);
  color: var(--el-text-color-regular);
  font:
    12px/1.7 ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
