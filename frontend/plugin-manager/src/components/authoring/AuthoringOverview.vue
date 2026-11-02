<template>
  <div class="authoring-section-stack">
    <section class="authoring-next-step">
      <span class="authoring-feature-icon"
        ><el-icon :size="24"><Guide /></el-icon
      ></span>
      <div>
        <span class="authoring-eyebrow">{{ t('authoring.nextStep') }}</span>
        <h2>
          {{
            t(
              `authoring.${nextTab === 'checks' ? 'checkAction' : nextTab === 'delivery' ? 'delivery' : 'runtime'}`
            )
          }}
        </h2>
        <p>{{ t('authoring.nextBody') }}</p>
      </div>
      <el-button type="primary" plain @click="emit('navigate', nextTab)"
        >{{ t('authoring.continue') }} <el-icon><ArrowRight /></el-icon
      ></el-button>
    </section>
    <div class="authoring-overview-grid">
      <AuthoringProjectDetails :project="project" />
      <section class="authoring-panel">
        <h3 class="authoring-panel-title">
          <el-icon><Connection /></el-icon>{{ t('authoring.continue') }}
        </h3>
        <button
          v-for="(item, index) in steps"
          :key="item.label"
          class="authoring-workflow-step"
          @click="emit('navigate', item.tab)"
        >
          <span class="authoring-step-number" :class="{ complete: item.complete }"
            ><el-icon v-if="item.complete"><Check /></el-icon
            ><template v-else>{{ index + 1 }}</template></span
          >
          <span
            ><strong>{{ t(`authoring.${item.label}`) }}</strong
            ><small>{{ item.detail }}</small></span
          ><el-icon><ArrowRight /></el-icon>
        </button>
      </section>
    </div>
    <section class="authoring-panel">
      <h3 class="authoring-panel-title">
        <el-icon><Clock /></el-icon>{{ t('authoring.activity') }}
      </h3>
      <div class="authoring-activity-list">
        <div
          v-for="(item, index) in project.activity.slice(0, 5)"
          :key="`${item.at}-${index}`"
          class="authoring-activity"
        >
          <span class="authoring-activity-dot" /><span>{{
            t(
              `authoring.${item.kind === 'stopped' ? 'stoppedEvent' : item.kind === 'published' ? 'publishedEvent' : item.kind}`
            )
          }}</span
          ><time>{{ formatTime(item.at) }}</time>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowRight, Check, Clock, Connection, Guide } from '@element-plus/icons-vue'
import AuthoringProjectDetails from './AuthoringProjectDetails.vue'
import type { AuthoringProject, AuthoringTab } from '@/types/authoring'

const props = defineProps<{ project: AuthoringProject }>()
const emit = defineEmits<{ navigate: [tab: AuthoringTab] }>()
const { t, locale } = useI18n()
const nextTab = computed<AuthoringTab>(() =>
  props.project.check === 'issues' || props.project.check === 'stale'
    ? 'checks'
    : props.project.check === 'passed'
      ? 'delivery'
      : 'runtime'
)
const steps = computed<
  Array<{ tab: AuthoringTab; label: string; detail: string; complete: boolean }>
>(() => [
  {
    tab: 'runtime',
    label: 'runtime',
    detail: t(
      `authoring.${props.project.run.status === 'succeeded' ? 'runSuccess' : props.project.runtime}`
    ),
    complete: props.project.run.status === 'succeeded',
  },
  {
    tab: 'checks',
    label: 'checks',
    detail: t(`authoring.${props.project.check}`),
    complete: props.project.check === 'passed',
  },
  {
    tab: 'delivery',
    label: 'build',
    detail: props.project.artifact?.name || t('authoring.noArtifact'),
    complete: props.project.artifact?.revision === props.project.revision,
  },
  {
    tab: 'delivery',
    label: 'publish',
    detail: t(`authoring.${props.project.release.phase === 4 ? 'published' : 'notPublished'}`),
    complete: props.project.release.phase === 4,
  },
])
function formatTime(at: number) {
  return new Intl.DateTimeFormat(locale.value, { hour: '2-digit', minute: '2-digit' }).format(at)
}
</script>
