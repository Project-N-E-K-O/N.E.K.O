<template>
  <div class="authoring-page" data-testid="authoring-workbench">
    <el-card>
      <template #header>
        <div class="authoring-header">
          <div class="authoring-header__title">
            <el-button v-if="requestedProject" :icon="ArrowLeft" @click="goHome">{{
              t('authoring.back')
            }}</el-button>
            <h2 :title="headerTitle">{{ headerTitle }}</h2>
            <template v-if="current">
              <AuthoringProjectStatus :project="current" />
              <el-tag v-if="current.plugin_type" size="small" type="info">{{
                t(current.plugin_type === 'adapter' ? 'plugins.typeAdapter' : 'plugins.typePlugin')
              }}</el-tag>
            </template>
          </div>
          <div class="authoring-header__actions">
            <el-button :icon="Refresh" :loading="loading" @click="refresh">{{
              t('authoring.refresh')
            }}</el-button>
            <template v-if="current">
              <el-button
                :icon="FolderOpened"
                :disabled="busy || current.status === 'missing'"
                @click="openFolder(current)"
                >{{ t('authoring.openFolder') }}</el-button
              >
              <el-button
                v-if="current.loaded"
                :icon="Document"
                :disabled="busy"
                @click="openLogs(current)"
                >{{ t('authoring.viewLogs') }}</el-button
              >
              <el-button
                v-if="current.loaded"
                type="primary"
                :icon="Position"
                :disabled="busy"
                @click="openPluginPage(current)"
                >{{ t('authoring.openPluginPage') }}</el-button
              >
              <el-button
                type="danger"
                text
                :icon="Delete"
                :disabled="busy"
                @click="confirmRemove(current)"
                >{{ t('authoring.removeProject') }}</el-button
              >
            </template>
            <template v-else-if="!requestedProject">
              <el-button :icon="Reading" @click="helpVisible = true">{{
                t('authoring.help')
              }}</el-button>
              <el-button :icon="FolderAdd" :disabled="busy" @click="addExisting">{{
                t('authoring.addExisting')
              }}</el-button>
              <el-button type="primary" :icon="Plus" @click="createVisible = true">{{
                t('authoring.newProject')
              }}</el-button>
            </template>
          </div>
        </div>
      </template>
      <el-alert
        v-if="loadError"
        :title="errorMessage(loadError, 'loadFailed')"
        type="error"
        :closable="false"
        show-icon
        class="authoring-create-notice"
      />
      <LoadingSpinner v-if="!loaded && !loadError" :loading="true" />
      <!-- The tab shell is a reserved extension point for future project
           sections; the merged overview is its only pane for now. -->
      <el-tabs v-else-if="current" v-model="activeTab" class="authoring-tabs">
        <el-tab-pane :label="t('authoring.tabOverview')" name="overview">
          <AuthoringProjectDetails
            :key="current.project_id"
            :project="current"
            @updated="onProjectUpdated"
          />
        </el-tab-pane>
        <el-tab-pane :label="t('authoring.publishTab')" name="publish" lazy>
          <AuthoringProjectPublish
            ref="publishPanel"
            :key="current.project_id"
            :project-id="current.project_id"
            :plugin-name="current.name ?? ''"
            :plugin-description="current.description ?? ''"
            :source-dir="current.source_dir"
          />
        </el-tab-pane>
      </el-tabs>
      <EmptyState
        v-else-if="requestedProject"
        :description="loaded ? t('authoring.projectNotFound') : ''"
        :icon="FolderOpened"
      />
      <AuthoringProjectList
        v-else-if="loaded"
        ref="projectList"
        :projects="projects"
        @open="openProject"
        @create="createVisible = true"
      />
    </el-card>
    <CreatePluginDialog v-model:visible="createVisible" @created="onCreated" />
    <AuthoringHelpDrawer v-model:visible="helpVisible" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft,
  Delete,
  Document,
  FolderAdd,
  FolderOpened,
  Plus,
  Position,
  Reading,
  Refresh,
} from '@element-plus/icons-vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import AuthoringProjectList from '@/components/authoring/AuthoringProjectList.vue'
import AuthoringProjectDetails from '@/components/authoring/AuthoringProjectDetails.vue'
import AuthoringProjectPublish from '@/components/authoring/AuthoringProjectPublish.vue'
import AuthoringProjectStatus from '@/components/authoring/AuthoringProjectStatus.vue'
import AuthoringHelpDrawer from '@/components/authoring/AuthoringHelpDrawer.vue'
import CreatePluginDialog from '@/components/authoring/CreatePluginDialog.vue'
import {
  projectTitle,
  useAuthoringProjects,
  useProjectErrorMessage,
} from '@/composables/useAuthoringProjects'
import {
  openProjectDirectory,
  pickProjectDirectory,
  projectErrorCode,
  type AuthoringProject,
  type CreatedPluginProject,
} from '@/api/pluginProjects'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const errorMessage = useProjectErrorMessage()
const { projects, loading, loaded, loadError, refresh, register, remove } = useAuthoringProjects()
const requestedProject = computed(() =>
  typeof route.query.project === 'string' ? route.query.project : undefined
)
const current = computed(() =>
  projects.value.find((project) => project.project_id === requestedProject.value)
)
const headerTitle = computed(() =>
  current.value ? projectTitle(current.value) : t('authoring.title')
)
const activeTab = ref('overview')
const createVisible = ref(false)
const helpVisible = ref(false)
const busy = ref(false)
const projectList = ref<InstanceType<typeof AuthoringProjectList>>()
const publishPanel = ref<InstanceType<typeof AuthoringProjectPublish>>()
onMounted(refresh)
// The lazy publish pane stays mounted across tab switches; coming back to it
// re-reads the status (a commit or push may have happened meanwhile). The first
// visit mounts the pane, which reads on its own.
watch(activeTab, (tab) => {
  if (tab === 'publish') void publishPanel.value?.reload()
})
// Another project opens on its overview, not on the tab the last one was left on.
watch(requestedProject, () => {
  activeTab.value = 'overview'
})

function openProject(projectId: string) {
  void router.push({ path: '/workbench', query: { project: projectId } })
}
function goHome() {
  void router.push({ path: '/workbench' })
}
function onProjectUpdated(updated: AuthoringProject) {
  projects.value = projects.value.map((project) =>
    project.project_id === updated.project_id ? updated : project
  )
}
async function onCreated(project: CreatedPluginProject) {
  ElMessage.success(t('authoring.creationComplete'))
  await refresh()
  openProject(project.project_id)
}

async function action(
  run: () => Promise<void>,
  describe: (error: unknown) => string = (error) => errorMessage(error, 'actionFailed')
) {
  if (busy.value) return
  busy.value = true
  try {
    await run()
  } catch (error) {
    ElMessage.error(describe(error))
  } finally {
    busy.value = false
  }
}

function addExisting() {
  return action(async () => {
    const selected = await pickProjectDirectory('', t('authoring.addExistingTitle'))
    if (!selected) return
    const project = await register(selected)
    projectList.value?.resetFilters()
    ElMessage.success(t('authoring.projectAdded', { name: projectTitle(project) }))
  }, (error) => {
    const code = projectErrorCode(error)
    return code === 'DIRECTORY_PICKER_UNAVAILABLE' || code === 'DIRECTORY_PICKER_FAILED'
      ? t('authoring.projectPickerUnavailable')
      : errorMessage(error, 'actionFailed')
  })
}

function openFolder(project: AuthoringProject) {
  return action(
    () => openProjectDirectory(project.source_dir),
    (error) => errorMessage(error, 'openFolderFailed')
  )
}

function openPluginPage(project: AuthoringProject) {
  if (project.plugin_id) void router.push(`/plugins/${encodeURIComponent(project.plugin_id)}`)
}

function openLogs(project: AuthoringProject) {
  if (project.plugin_id) void router.push(`/logs/${encodeURIComponent(project.plugin_id)}`)
}

async function confirmRemove(project: AuthoringProject) {
  try {
    await ElMessageBox.confirm(
      t('authoring.removeConfirm', { name: projectTitle(project), path: project.source_dir }),
      t('authoring.removeProject'),
      {
        type: 'warning',
        confirmButtonText: t('authoring.removeProject'),
        cancelButtonText: t('common.cancel'),
      }
    )
  } catch {
    return
  }
  await action(async () => {
    await remove(project.project_id)
    goHome()
    ElMessage.success(t('authoring.projectRemoved'))
  })
}
</script>

<style src="@/assets/styles/authoring.css"></style>
