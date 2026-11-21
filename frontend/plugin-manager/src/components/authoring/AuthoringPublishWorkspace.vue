<template>
  <section class="authoring-workspace" :aria-label="t('authoring.workspaceTitle')">
    <h4>{{ t('authoring.workspaceTitle') }}</h4>
    <p v-if="!workspace" class="authoring-muted authoring-small">{{ t('authoring.workspaceNoRepo') }}</p>
    <template v-else>
      <dl class="authoring-workspace__facts">
        <div>
          <dt>{{ t('authoring.workspaceBranch') }}</dt>
          <dd>
            <code v-if="repository.branch">{{ repository.branch }}</code>
            <span v-else class="authoring-muted">{{ t('authoring.workspaceNoBranch') }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('authoring.workspaceUpstream') }}</dt>
          <dd>
            <code v-if="repository.upstream">{{ repository.upstream }}</code>
            <span v-else class="authoring-muted">{{ t('authoring.workspaceNoUpstream') }}</span>
          </dd>
        </div>
        <div>
          <dt>{{ t('authoring.workspaceSync') }}</dt>
          <dd>{{ syncText }}</dd>
        </div>
        <div>
          <dt>{{ t('authoring.workspaceOrigin') }}</dt>
          <dd>
            <code v-if="repository.origin_url">{{ repository.origin_url }}</code>
            <span v-else class="authoring-muted">{{ t('authoring.workspaceNoOrigin') }}</span>
          </dd>
        </div>
      </dl>

      <h5>
        {{ t('authoring.workspaceChanges') }}
        <el-tag size="small" :type="workspace.changes_total ? 'warning' : 'success'">
          {{ workspace.changes_total }}
        </el-tag>
      </h5>
      <p v-if="!workspace.changes_total" class="authoring-muted authoring-small">
        {{ t('authoring.workspaceClean') }}
      </p>
      <template v-else>
        <p class="authoring-small">{{ t('authoring.workspaceChangesHint') }}</p>
        <ul class="authoring-workspace__changes">
          <li v-for="change in workspace.changes" :key="change.path">
            <el-tag size="small" :type="CHANGE_TAGS[change.kind]" effect="plain">
              {{ t(CHANGE_LABELS[change.kind]) }}
            </el-tag>
            <code>{{ change.path }}</code>
          </li>
        </ul>
        <p v-if="workspace.changes_total > workspace.changes.length" class="authoring-muted authoring-small">
          {{ t('authoring.workspaceChangesMore', { n: workspace.changes_total - workspace.changes.length }) }}
        </p>
      </template>

      <h5>{{ t('authoring.workspaceCommits') }}</h5>
      <p v-if="!workspace.commits.length" class="authoring-muted authoring-small">
        {{ t('authoring.workspaceNoCommits') }}
      </p>
      <ol v-else class="authoring-workspace__commits">
        <li v-for="commit in workspace.commits" :key="commit.sha">
          <code class="authoring-workspace__sha" :title="commit.sha">{{ commit.sha.slice(0, 7) }}</code>
          <span class="authoring-workspace__subject">{{ commit.subject }}</span>
          <el-tag v-if="commit.sha === repository.head" size="small" effect="plain">HEAD</el-tag>
          <el-tag v-if="repository.tag_at_head && commit.sha === repository.head" size="small" type="success" effect="plain">
            {{ repository.tag }}
          </el-tag>
          <el-tag v-if="commit.pushed === false" size="small" type="warning" effect="plain">
            {{ t('authoring.workspaceNotPushed') }}
          </el-tag>
          <span class="authoring-workspace__meta authoring-muted authoring-small">
            {{ commit.author }} · {{ formatRunTimestamp(commit.timestamp, true) }}
          </span>
        </li>
      </ol>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type {
  PublishRepositoryState,
  WorkspaceChangeKind,
  WorkspaceLog,
} from '@/api/pluginProjects'
import { formatRunTimestamp } from '@/utils/runDisplay'

const CHANGE_LABELS: Record<WorkspaceChangeKind, string> = {
  modified: 'authoring.workspaceChangeModified',
  added: 'authoring.workspaceChangeAdded',
  deleted: 'authoring.workspaceChangeDeleted',
  renamed: 'authoring.workspaceChangeRenamed',
  untracked: 'authoring.workspaceChangeUntracked',
  conflicted: 'authoring.workspaceChangeConflicted',
}
const CHANGE_TAGS: Record<WorkspaceChangeKind, 'warning' | 'success' | 'danger' | 'info'> = {
  modified: 'warning',
  added: 'success',
  deleted: 'danger',
  renamed: 'info',
  untracked: 'info',
  conflicted: 'danger',
}

/** Page 1: what is on this computer that GitHub does not have yet. */
const props = defineProps<{ repository: PublishRepositoryState; workspace: WorkspaceLog | null }>()
const { t } = useI18n()
const syncText = computed(() => {
  const log = props.workspace
  if (!log || log.ahead === null || log.behind === null) return t('authoring.workspaceSyncUnknown')
  if (!log.ahead && !log.behind) return t('authoring.workspaceSyncLevel')
  const parts: string[] = []
  if (log.ahead) parts.push(t('authoring.workspaceAhead', { n: log.ahead }))
  if (log.behind) parts.push(t('authoring.workspaceBehind', { n: log.behind }))
  return parts.join(t('authoring.workspaceListSeparator'))
})
</script>

<style scoped>
.authoring-workspace {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-xs);
  padding: var(--spacing-sm) var(--spacing-md);
  border: 1px solid var(--surface-border);
  border-radius: var(--radius-control);
  background: var(--surface-card);
}
.authoring-workspace h4,
.authoring-workspace h5 {
  display: flex;
  align-items: center;
  gap: var(--spacing-xs);
  margin: var(--spacing-xs) 0 0;
}
.authoring-workspace h4 {
  font-size: 14px;
}
.authoring-workspace h5 {
  font-size: 13px;
}
.authoring-workspace p {
  margin: 0;
}
.authoring-workspace__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--spacing-xs) var(--spacing-md);
  margin: 0;
}
.authoring-workspace__facts dt {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.authoring-workspace__facts dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.authoring-workspace__changes,
.authoring-workspace__commits {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 260px;
  margin: 0;
  padding: 0;
  overflow: auto;
  list-style: none;
}
.authoring-workspace__changes li,
.authoring-workspace__commits li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--spacing-xs);
  min-width: 0;
}
.authoring-workspace__changes code {
  overflow-wrap: anywhere;
}
.authoring-workspace__sha {
  color: var(--el-color-primary);
}
.authoring-workspace__subject {
  min-width: 0;
  overflow-wrap: anywhere;
}
.authoring-workspace__meta {
  margin-left: auto;
}
</style>
>