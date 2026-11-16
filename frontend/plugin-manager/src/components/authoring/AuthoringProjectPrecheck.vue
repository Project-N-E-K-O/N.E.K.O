<template>
  <div class="authoring-precheck">
    <el-alert v-if="requestError" :title="requestError" type="error" :closable="false" show-icon />
    <el-alert
      v-else-if="report"
      :title="t(report.passed ? 'authoring.precheckPassed' : 'authoring.precheckFailed')"
      :type="report.passed ? 'success' : 'error'"
      :closable="false"
      show-icon
    />
    <div class="authoring-precheck-actions">
      <el-button type="primary" :icon="VideoPlay" :loading="running" @click="run">
        {{ t(running ? 'authoring.precheckRunning' : 'authoring.precheckRun') }}
      </el-button>
      <span class="authoring-muted authoring-small">
        {{ t('authoring.precheckCiNote') }}
      </span>
    </div>
    <section v-if="report" class="authoring-precheck-env">
      <h4>{{ t('authoring.precheckEnv') }}</h4>
      <ul v-if="report.environment_issues.length">
        <li v-for="([severity, message], index) in report.environment_issues" :key="index">
          <el-tag size="small" :type="severity === 'error' ? 'danger' : 'warning'">{{
            t(severity === 'error' ? 'authoring.precheckSeverityError' : 'authoring.precheckSeverityWarning')
          }}</el-tag>
          <span>{{ message }}</span>
        </li>
      </ul>
      <p v-else class="authoring-muted authoring-small">{{ t('authoring.precheckEnvClean') }}</p>
    </section>
    <details
      v-for="stage in stages"
      :key="stage.name"
      class="authoring-precheck-stage"
      :open="stage.state === 'failed'"
    >
      <summary>
        <el-tag size="small" :type="stageTagType(stage.state)">{{ stageLabel(stage) }}</el-tag>
        <span>{{ t(stageTitleKey(stage.name)) }}</span>
      </summary>
      <pre v-if="stage.output">{{ stage.output }}</pre>
    </details>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useLocalizedError } from '@/composables/useLocalizedError'
import { VideoPlay } from '@element-plus/icons-vue'
import {
  precheckPluginProject,
  type PrecheckReport,
  type PrecheckStage,
} from '@/api/pluginProjects'
import { useProjectErrorMessage } from '@/composables/useAuthoringProjects'

type StageName = PrecheckStage['name']
type StageState = 'passed' | 'failed' | 'skipped'

const STAGE_ORDER: StageName[] = ['lint', 'sync', 'release-check']

const props = defineProps<{ projectId: string }>()
const { t } = useI18n()
const describeError = useProjectErrorMessage()
const running = ref(false)
const report = ref<PrecheckReport | null>(null)
const requestFailure = useLocalizedError(describeError, 'precheckFailedToRun')
const requestError = requestFailure.message

interface StageView {
  name: StageName
  state: StageState
  output: string
}

const stages = computed<StageView[]>(() => {
  if (!report.value) return []
  const found = new Map(report.value.stages.map((stage) => [stage.name, stage]))
  let blocked = false
  return STAGE_ORDER.map((name) => {
    const stage = found.get(name)
    if (!stage) {
      // The CLI short-circuits after the first failing gate.
      const state: StageState = blocked ? 'skipped' : 'passed'
      return { name, state, output: '' }
    }
    blocked = blocked || !stage.passed
    return { name, state: (stage.passed ? 'passed' : 'failed') as StageState, output: stage.output }
  })
})

function stageTitleKey(name: StageName) {
  return name === 'lint'
    ? 'authoring.precheckStageLint'
    : name === 'sync'
      ? 'authoring.precheckStageSync'
      : 'authoring.precheckStageReleaseCheck'
}

function stageLabel(stage: StageView) {
  if (stage.state === 'passed') return t('authoring.precheckStagePassed')
  return stage.state === 'failed' ? t('authoring.precheckStageFailed') : t('authoring.precheckSkipped')
}

function stageTagType(state: StageState) {
  return state === 'passed' ? 'success' : state === 'failed' ? 'danger' : 'info'
}

async function run() {
  if (running.value) return
  running.value = true
  requestFailure.clear()
  try {
    report.value = await precheckPluginProject(props.projectId)
  } catch (error) {
    report.value = null
    requestFailure.set(error)
  } finally {
    running.value = false
  }
}
</script>
