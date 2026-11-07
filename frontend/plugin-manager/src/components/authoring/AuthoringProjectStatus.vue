<template>
  <el-tag v-if="problem" size="small" :type="problem.type" effect="light">{{
    t(`authoring.${problem.label}`)
  }}</el-tag>
  <StatusIndicator v-else :status="project.running ? 'running' : 'stopped'" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import type { AuthoringProject } from '@/api/pluginProjects'

const props = defineProps<{ project: AuthoringProject }>()
const { t } = useI18n()
// A loaded project uses the same running/stopped indicator as every plugin,
// unless the runtime reports a problem with it.
const RUNTIME_PROBLEMS: Partial<
  Record<NonNullable<AuthoringProject['problem']>, { type: 'danger' | 'warning'; label: string }>
> = {
  load_failed: { type: 'danger', label: 'statusLoadFailed' },
  reload_failed: { type: 'danger', label: 'statusReloadFailed' },
  startup_degraded: { type: 'warning', label: 'statusStartupDegraded' },
}
const problem = computed(() => {
  if (props.project.status === 'missing') return { type: 'danger', label: 'statusMissing' } as const
  if (props.project.status === 'invalid')
    return { type: 'warning', label: 'statusInvalid' } as const
  if (!props.project.loaded) {
    // The plugin system being off is no fault of the project.
    return props.project.problem === 'runtime_inactive'
      ? ({ type: 'info', label: 'statusRuntimeInactive' } as const)
      : ({ type: 'warning', label: 'notLoaded' } as const)
  }
  return props.project.problem ? (RUNTIME_PROBLEMS[props.project.problem] ?? null) : null
})
</script>
