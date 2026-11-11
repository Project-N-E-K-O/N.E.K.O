<template>
  <div class="authoring-publish-release">
    <div class="authoring-precheck-actions">
      <el-button
        v-for="(mode, index) in modes"
        :key="mode"
        :type="index === 0 ? 'primary' : 'default'"
        :icon="index === 0 ? Position : undefined"
        :disabled="taskActive"
        :data-mode="mode"
        @click="emit('run', mode)"
      >
        {{ t(MODE_LABELS[mode]) }}
      </el-button>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <AuthoringPublishTask
      v-if="task"
      :task="task"
      :watched="watched"
      :canceling="canceling"
      @cancel="emit('cancel')"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Position } from '@element-plus/icons-vue'
import AuthoringPublishTask from '@/components/authoring/AuthoringPublishTask.vue'
import { publishTaskActive, type PublishMode, type PublishTask } from '@/api/pluginProjects'

const MODE_LABELS: Record<PublishMode, string> = {
  github_and_market: 'authoring.publishRunGithubMarket',
  github: 'authoring.publishRunGithub',
  market_notify: 'authoring.publishRunMarket',
}

/**
 * The actions of the GitHub Release step or the Market version step, with the
 * latest task of those modes. The buttons are always clickable; the page
 * explains what is missing before it runs anything.
 */
const props = defineProps<{
  modes: PublishMode[]
  /** The latest task when it ran one of `modes`. */
  task: PublishTask | null
  /** Any task of the project, which blocks starting another. */
  anyTaskActive: boolean
  watched: boolean
  canceling: boolean
  error: string
}>()
const emit = defineEmits<{ run: [mode: PublishMode]; cancel: [] }>()
const { t } = useI18n()
const taskActive = computed(
  () => props.anyTaskActive || (!!props.task && publishTaskActive(props.task))
)
</script>

<style scoped>
.authoring-publish-release {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  margin-top: var(--spacing-sm);
}
</style>
