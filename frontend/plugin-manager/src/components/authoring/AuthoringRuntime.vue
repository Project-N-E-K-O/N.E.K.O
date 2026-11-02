<template>
  <div class="authoring-section-stack">
    <section class="authoring-panel authoring-runtime-bar">
      <div>
        <h2>{{ t('authoring.runtime') }}</h2>
        <p>{{ t('authoring.runtimeHint') }}</p>
      </div>
      <div class="authoring-actions">
        <StatusIndicator :status="project.runtime" /><el-button
          :disabled="busy"
          :type="project.runtime === 'running' ? 'default' : 'primary'"
          @click="emit('lifecycle', project.runtime === 'running' ? 'stop' : 'start')"
          >{{ t(`authoring.${project.runtime === 'running' ? 'stop' : 'start'}`) }}</el-button
        ><el-button :disabled="busy" @click="emit('lifecycle', 'reload')">{{
          t('authoring.reload')
        }}</el-button>
      </div>
    </section>
    <div class="authoring-runtime-grid">
      <section class="authoring-panel">
        <h3 class="authoring-panel-title">
          <el-icon><VideoPlay /></el-icon>{{ t('authoring.entry') }}
        </h3>
        <div class="authoring-entry-heading">
          <code>{{ entryId }}</code
          ><el-tag size="small" effect="plain">async</el-tag>
        </div>
        <form class="authoring-form" @submit.prevent="emit('run', input, fail)">
          <label for="authoring-test-input"
            >{{ t('authoring.input')
            }}<el-input id="authoring-test-input" v-model="input" placeholder="N.E.K.O" /></label
          ><el-checkbox v-model="fail">{{ t('authoring.simulateFailure') }}</el-checkbox
          ><el-button
            type="primary"
            native-type="submit"
            :loading="executing"
            :disabled="busy || project.runtime !== 'running'"
            >{{ t('authoring.trigger') }}</el-button
          >
        </form>
        <p v-if="project.runtime !== 'running'" class="authoring-small authoring-muted">
          {{ t('authoring.start') }} → {{ t('authoring.trigger') }}
        </p>
      </section>
      <section class="authoring-panel">
        <h3 class="authoring-panel-title">
          <el-icon><Document /></el-icon>{{ t('authoring.runtime') }} / Run
        </h3>
        <div
          v-if="project.run.status === 'idle' && !executing"
          class="authoring-centered authoring-result-empty"
        >
          <el-icon :size="32"><Monitor /></el-icon>
          <p>{{ t('authoring.runEmpty') }}</p>
        </div>
        <template v-else
          ><div class="authoring-run-heading" role="status">
            <el-tag
              :type="executing ? 'info' : project.run.status === 'failed' ? 'danger' : 'success'"
              >{{
                t(
                  `authoring.${executing ? 'working' : project.run.status === 'failed' ? 'runFailed' : 'runSuccess'}`
                )
              }}</el-tag
            ><code>{{ project.id }}:{{ entryId }}</code>
          </div>
          <pre class="authoring-output">{{ executing ? '…' : result }}</pre>
        </template>
      </section>
    </div>
    <section class="authoring-panel">
      <h3 class="authoring-panel-title">
        <el-icon><Tickets /></el-icon>{{ t('plugins.logs') }}
      </h3>
      <div class="authoring-log-list">
        <p
          v-for="(item, index) in project.activity
            .filter((event) => ['started', 'stopped', 'reloaded', 'executed'].includes(event.kind))
            .slice(0, 6)"
          :key="`${item.at}-${index}`"
        >
          <time>{{ time(item.at) }}</time
          ><span class="authoring-log-level">{{
            item.outcome === 'failed' ? 'ERROR' : 'INFO'
          }}</span
          ><span>{{ t(`authoring.${item.kind === 'stopped' ? 'stoppedEvent' : item.kind}`) }}</span>
        </p>
        <span
          v-if="
            !project.activity.some((event) =>
              ['started', 'stopped', 'reloaded', 'executed'].includes(event.kind)
            )
          "
          class="authoring-muted"
          >—</span
        >
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Document, Monitor, Tickets, VideoPlay } from '@element-plus/icons-vue'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import type { AuthoringProject } from '@/types/authoring'

const props = defineProps<{ project: AuthoringProject; busy: boolean; executing: boolean }>()
const emit = defineEmits<{
  lifecycle: [action: 'start' | 'stop' | 'reload']
  run: [input: string, fail: boolean]
}>()
const { t, locale } = useI18n()
const input = ref('N.E.K.O')
const fail = ref(false)
const entryId = computed(() =>
  props.project.sample === 'weather'
    ? 'lookup'
    : props.project.sample === 'memo'
      ? 'remind'
      : 'hello'
)
const result = computed(() =>
  JSON.stringify(
    {
      demo: true,
      status: props.project.run.status,
      input: props.project.run.input,
      ...(props.project.run.status === 'failed'
        ? { error: { code: 'DEMO_EXECUTION_FAILED', message: t('authoring.runFailed') } }
        : { result: { entry: entryId.value, message: t('authoring.runSuccess') } }),
    },
    null,
    2
  )
)
function time(at: number) {
  return new Intl.DateTimeFormat(locale.value, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(at)
}
</script>
