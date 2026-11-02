<template>
  <div class="authoring-workbench" data-testid="authoring-workbench">
    <header class="authoring-toolbar">
      <div class="authoring-toolbar-title">
        <el-icon :size="21"><EditPen /></el-icon>
        <h1>{{ t('authoring.title') }}</h1>
      </div>
      <div class="authoring-actions">
        <el-button
          :icon="Reading"
          @click="
            openHelp(
              current
                ? tab === 'delivery'
                  ? 'publish'
                  : tab === 'overview'
                    ? 'start'
                    : tab
                : 'start'
            )
          "
          >{{ t('authoring.help') }}</el-button
        >
        <el-button
          v-if="!current"
          type="primary"
          :icon="Plus"
          :disabled="busy"
          @click="openCreate('create')"
          >{{ t('authoring.newProject') }}</el-button
        >
      </div>
    </header>
    <div v-if="current" class="authoring-project-shell">
      <div class="authoring-project-header">
        <div class="authoring-project-breadcrumb">
          <el-button text :icon="ArrowLeft" @click="goHome">{{ t('authoring.back') }}</el-button
          ><span>/</span
          ><el-select
            :model-value="current.key"
            :aria-label="t('authoring.back')"
            @update:model-value="selectProject"
            ><el-option
              v-for="project in projects"
              :key="project.key"
              :value="project.key"
              :label="name(project)"
          /></el-select>
        </div>
        <div class="authoring-project-identity">
          <span class="authoring-project-icon authoring-project-icon--large"
            ><el-icon :size="29"><Box /></el-icon
          ></span>
          <div>
            <h2>
              {{ name(current) }} <span>v{{ current.version }}</span>
            </h2>
            <p>
              {{
                current.description ||
                (current.kind === 'demo' && current.sample
                  ? t(`authoring.${current.sample}Description`)
                  : '—')
              }}
            </p>
          </div>
          <el-button
            v-if="current.kind === 'demo'"
            type="primary"
            :loading="isOperation('check')"
            :disabled="busy && !isOperation('check')"
            @click="check(current.id)"
            >{{ t('authoring.checkAction') }}</el-button
          >
        </div>
        <div class="authoring-project-meta">
          <code>{{ current.source }}/</code>
          <div v-if="current.kind === 'demo'">
            <StatusIndicator :status="current.runtime" /><el-tag
              size="small"
              effect="plain"
              :type="
                current.check === 'passed'
                  ? 'success'
                  : current.check === 'issues' || current.check === 'stale'
                    ? 'warning'
                    : 'info'
              "
              >{{ t(`authoring.${current.check}`) }}</el-tag
            ><span class="authoring-small authoring-muted">{{
              t(`authoring.${current.release.phase === 4 ? 'published' : 'notPublished'}`)
            }}</span>
          </div>
        </div>
      </div>
      <div v-if="demoProject" ref="tabsElement" class="authoring-tabs">
        <el-tabs ref="tabsControl" :model-value="tab" @update:model-value="selectTab"
          ><el-tab-pane
            v-for="item in tabs"
            :key="item"
            :name="item"
            :label="t(`authoring.${item}`)"
        /></el-tabs>
      </div>
      <section v-if="demoProject" class="authoring-content" :aria-label="t(`authoring.${tab}`)">
        <MotionTransition preset="quiet" mode="out-in"
          ><div :key="`${current.id}:${tab}`">
            <AuthoringOverview
              v-if="tab === 'overview'"
              :project="demoProject"
              @navigate="selectTab"
            />
            <AuthoringRuntime
              v-else-if="tab === 'runtime'"
              :project="demoProject"
              :busy="busy"
              :executing="isOperation('run')"
              @lifecycle="(action) => runtime(current!.id, action)"
              @run="(input, fail) => run(current!.id, input, fail)"
            />
            <AuthoringChecks
              v-else-if="tab === 'checks'"
              :project="demoProject"
              :busy="busy"
              :checking="isOperation('check')"
              @check="check(current.id)"
              @change="markChanged(current.id)"
              @repair="markChanged(current.id, true)"
              @help="openHelp"
            />
            <AuthoringDelivery
              v-else
              :project="demoProject"
              :busy="busy"
              :building="isOperation('build')"
              :publishing="isOperation('publish')"
              @build="build(current.id)"
              @publish="publish(current.id)"
              @help="openHelp"
            /></div
        ></MotionTransition>
      </section>
      <section v-else class="authoring-content" :aria-label="t('authoring.projectDetails')">
        <AuthoringProjectDetails :project="current" />
      </section>
    </div>
    <div v-else class="authoring-home">
      <AuthoringProjectList
        ref="projectList"
        :projects="projects"
        @open="openProject"
        @create="openCreate('create')"
      />
    </div>
    <CreatePluginDialog
      v-model:visible="createVisible"
      :mode="createMode"
      :existing-ids="projects.map((project) => project.id)"
      @create="create"
      @created="rememberCreated"
    />
    <AuthoringHelpDrawer v-model:visible="helpVisible" v-model:topic="helpTopic" />
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { ElMessage, type TabsInstance } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Box, EditPen, Plus, Reading } from '@element-plus/icons-vue'
import MotionTransition from '@/motion/MotionTransition.vue'
import StatusIndicator from '@/components/common/StatusIndicator.vue'
import AuthoringProjectList from '@/components/authoring/AuthoringProjectList.vue'
import AuthoringProjectDetails from '@/components/authoring/AuthoringProjectDetails.vue'
import AuthoringHelpDrawer from '@/components/authoring/AuthoringHelpDrawer.vue'
import CreatePluginDialog from '@/components/authoring/CreatePluginDialog.vue'
import { usePluginAuthoring } from '@/composables/usePluginAuthoring'
import { useCreatedPluginProjects } from '@/composables/useCreatedPluginProjects'
import type { PluginProjectResult } from '@/api/pluginProjects'
import type {
  AuthoringOperation,
  AuthoringProjectItem,
  AuthoringProjectDraft,
  AuthoringTab,
  HelpTopic,
} from '@/types/authoring'

const { t, locale } = useI18n()
// Simulated project tabs are only reachable for development-only sample projects.
const AuthoringOverview = defineAsyncComponent(
  () => import('@/components/authoring/AuthoringOverview.vue')
)
const AuthoringRuntime = defineAsyncComponent(
  () => import('@/components/authoring/AuthoringRuntime.vue')
)
const AuthoringChecks = defineAsyncComponent(
  () => import('@/components/authoring/AuthoringChecks.vue')
)
const AuthoringDelivery = defineAsyncComponent(
  () => import('@/components/authoring/AuthoringDelivery.vue')
)
const router = useRouter()
const route = useRoute()
const {
  projects: demoProjects,
  pending,
  busy,
  check,
  runtime,
  run,
  markChanged,
  build,
  publish,
  addProject,
} = usePluginAuthoring()
const { projects: createdProjects, remember } = useCreatedPluginProjects()
const projects = computed<AuthoringProjectItem[]>(() => [
  ...createdProjects.value,
  ...demoProjects.value,
])
const tabs: AuthoringTab[] = ['overview', 'runtime', 'checks', 'delivery']
const current = computed(() =>
  projects.value.find((project) => project.key === route.query.project)
)
const demoProject = computed(() => (current.value?.kind === 'demo' ? current.value : undefined))
const tab = computed<AuthoringTab>(() =>
  tabs.includes(route.query.tab as AuthoringTab) ? (route.query.tab as AuthoringTab) : 'overview'
)
const tabsElement = ref<HTMLDivElement>()
const tabsControl = ref<TabsInstance>()
let tabScrollFrame = 0
// A direct link can select a tab before Element Plus has measured overflow.
// Reuse its scrolling API after layout, including sidebar and locale changes.
function revealActiveTab() {
  cancelAnimationFrame(tabScrollFrame)
  tabScrollFrame = requestAnimationFrame(() => {
    void tabsControl.value?.tabNavRef?.scrollToActiveTab()
  })
}
useResizeObserver(tabsElement, revealActiveTab)
watch([tab, locale, tabsControl], revealActiveTab, { flush: 'post' })
onBeforeUnmount(() => cancelAnimationFrame(tabScrollFrame))
const createVisible = ref(false)
const projectList = ref<InstanceType<typeof AuthoringProjectList>>()
const createMode = ref<'create' | 'import'>('create')
const helpVisible = ref(false)
const helpTopic = ref<HelpTopic>('start')
const name = (project: AuthoringProjectItem) =>
  project.name ||
  (project.kind === 'demo' && project.sample ? t(`authoring.${project.sample}`) : project.id)
function isOperation(operation: AuthoringOperation) {
  return pending.value?.projectId === current.value?.id && pending.value?.operation === operation
}
function openProject(key: string, section: AuthoringTab = 'overview') {
  void router.push({ path: '/development/workbench', query: { project: key, tab: section } })
}
function goHome() {
  void router.push({ path: '/development/workbench' })
}
function selectProject(key: string | number) {
  openProject(String(key), tab.value)
}
function selectTab(value: string | number) {
  if (current.value && tabs.includes(value as AuthoringTab))
    openProject(current.value.key, value as AuthoringTab)
}
function openCreate(mode: 'create' | 'import') {
  createMode.value = mode
  createVisible.value = true
}
function create(draft: AuthoringProjectDraft) {
  const id = addProject(draft)
  if (!id) return
  createVisible.value = false
  openProject(id)
}
function rememberCreated(result: PluginProjectResult, key: string) {
  try {
    remember(result, key)
  } catch {
    ElMessage.error(t('authoring.projectRecordSaveFailed'))
  }
  projectList.value?.resetFilters()
}
function openHelp(topic: HelpTopic) {
  helpTopic.value = topic
  helpVisible.value = true
}
</script>

<style src="@/assets/styles/authoring.css"></style>
