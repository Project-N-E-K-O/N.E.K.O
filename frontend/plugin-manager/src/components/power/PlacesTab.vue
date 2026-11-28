<template>
  <div v-loading="loading && !overview && !places.length" class="storage-panel" data-testid="storage-panel">
    <el-alert v-if="error" :title="error" type="error" :closable="true" show-icon class="block" data-testid="storage-error" @close="error = ''" />
    <el-alert
      v-if="banner"
      :title="banner.text"
      :type="banner.type"
      :closable="true"
      show-icon
      class="block"
      data-testid="storage-banner"
      @close="banner = null"
    />

    <!-- Full Disk Space Management Panel (available on backends with /console/storage) -->
    <template v-if="overview">
      <!-- 1. Overview & Segmented Capacity Bar -->
      <section class="panel overview-card" data-testid="storage-overview">
        <div class="heading">
          <div>
            <h2>{{ t('power.runtime.storage.title') }}</h2>
            <p class="muted">{{ t('power.runtime.storage.subtitle') }}</p>
          </div>
          <div class="header-actions">
            <el-button
              type="primary"
              plain
              :disabled="cleaning || staleSummary.totalCount === 0"
              data-testid="clean-all-stale"
              @click="askCleanAllStale"
            >
              {{ staleSummary.totalCount > 0
                ? t('power.runtime.storage.cleanStaleWithSize', { count: staleSummary.totalCount, size: formatBytes(staleSummary.bytes) })
                : t('power.runtime.storage.cleanStaleEmpty') }}
            </el-button>
            <el-button :loading="loading" data-testid="storage-refresh" @click="reloadFresh">
              {{ t('power.runtime.storage.rescan') }}
            </el-button>
          </div>
        </div>

        <div class="kpi-grid">
          <div class="kpi-tile" data-testid="kpi-total">
            <span class="kpi-label">{{ t('power.runtime.storage.kpi.totalUsed') }}</span>
            <strong class="kpi-value">{{ formatMaybeBound(overview.total_bytes, overview.total_whole) }}</strong>
            <span class="kpi-sub">
              {{ diskShareText || t('power.runtime.storage.kpi.hostDirs', { count: overview.places.filter(p => p.exists).length }) }}
            </span>
          </div>

          <div class="kpi-tile" :class="{ 'kpi-tile--accent': overview.reclaimable_bytes > 0 }" data-testid="kpi-reclaimable">
            <span class="kpi-label">{{ t('power.runtime.storage.kpi.reclaimable') }}</span>
            <strong class="kpi-value">{{ formatBytes(overview.reclaimable_bytes) }}</strong>
            <span class="kpi-sub">
              {{ t('power.runtime.storage.kpi.reclaimableDetail', {
                worktrees: cleanableWorktrees.length,
                workDirs: cleanableWorkDirs.length,
              }) }}
            </span>
          </div>

          <div class="kpi-tile" :class="{ 'kpi-tile--warn': diskLow }" data-testid="kpi-disk">
            <span class="kpi-label">{{ t('power.runtime.storage.kpi.diskFree') }}</span>
            <strong class="kpi-value">{{ overview.disk ? formatBytes(overview.disk.free) : '—' }}</strong>
            <template v-if="overview.disk">
              <div class="disk-meter" aria-hidden="true">
                <div class="disk-meter__fill" :class="{ 'is-low': diskLow }" :style="{ width: `${diskUsedPercent}%` }" />
              </div>
              <span class="kpi-sub">
                {{ t('power.runtime.storage.kpi.diskOfTotal', { total: formatBytes(overview.disk.total), percent: diskUsedPercent }) }}
              </span>
            </template>
            <span v-else class="kpi-sub">{{ t('power.runtime.storage.kpi.diskUnknown') }}</span>
          </div>
        </div>

        <!-- Stacked segmented breakdown bar -->
        <div class="breakdown" data-testid="storage-breakdown">
          <div class="bar-track" role="img" :aria-label="t('power.runtime.storage.breakdownLabel')">
            <div
              v-for="seg in barSegments"
              :key="seg.key"
              class="bar-segment"
              :class="{ 'is-interactive': isJumpableCategory(seg.key) }"
              :style="{ flexGrow: seg.bytes, backgroundColor: categoryColor(seg.key) }"
              :title="`${categoryLabel(seg.key)}: ${formatMaybeBound(seg.bytes, seg.bytes_whole)}`"
              @click="jumpToCategory(seg.key)"
            />
            <div v-if="!barSegments.length" class="bar-empty" />
          </div>

          <div class="legend">
            <button
              v-for="cat in overview.categories"
              :key="cat.key"
              type="button"
              class="legend-chip"
              :class="{
                'is-clickable': isJumpableCategory(cat.key),
                'is-active': isJumpableCategory(cat.key) && section === cat.key,
              }"
              :data-category="cat.key"
              @click="jumpToCategory(cat.key)"
            >
              <span class="dot" :style="{ backgroundColor: categoryColor(cat.key) }" />
              <span class="legend-name">{{ categoryLabel(cat.key) }}</span>
              <span class="legend-size">{{ formatMaybeBound(cat.bytes, cat.bytes_whole) }}</span>
              <span v-if="!cat.cleanable && cat.key === 'space'" class="legend-tag">
                {{ t('power.runtime.storage.protected') }}
              </span>
            </button>
          </div>
          <p v-if="!overview.total_whole" class="muted scan-note">
            {{ t('power.runtime.storage.partialScanNote') }}
          </p>
        </div>
      </section>

      <!-- 2. Tabbed Cleanup Workspace -->
      <section class="panel workspace-card" data-testid="storage-workspace">
        <div class="workspace-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            class="tab-pill"
            :class="{ 'is-active': section === 'worktrees' }"
            :aria-selected="section === 'worktrees'"
            data-testid="storage-tab-worktrees"
            @click="section = 'worktrees'"
          >
            <span>{{ t('power.runtime.storage.tabs.worktrees') }}</span>
            <span class="pill-badge">{{ overview.worktrees.length }}</span>
          </button>
          <button
            type="button"
            role="tab"
            class="tab-pill"
            :class="{ 'is-active': section === 'work_dirs' }"
            :aria-selected="section === 'work_dirs'"
            data-testid="storage-tab-workdirs"
            @click="section = 'work_dirs'"
          >
            <span>{{ t('power.runtime.storage.tabs.workDirs') }}</span>
            <span class="pill-badge">{{ overview.work_dirs.length }}</span>
          </button>
          <button
            type="button"
            role="tab"
            class="tab-pill"
            :class="{ 'is-active': section === 'transcripts' }"
            :aria-selected="section === 'transcripts'"
            data-testid="storage-tab-transcripts"
            @click="section = 'transcripts'"
          >
            <span>{{ t('power.runtime.storage.tabs.transcripts') }}</span>
            <span class="pill-badge">{{ formatBytes(overview.transcripts.bytes) }}</span>
          </button>
        </div>

        <!-- Tab A: Worktrees -->
        <div v-if="section === 'worktrees'" class="tab-body" data-testid="section-worktrees">
          <p class="muted section-hint">{{ t('power.runtime.storage.worktrees.hint') }}</p>

          <div class="toolbar">
            <div class="filter-group" role="group">
              <button
                v-for="opt in wtFilterOptions"
                :key="opt.value"
                type="button"
                class="filter-chip"
                :class="{ 'is-active': wtFilter === opt.value }"
                :data-testid="`wt-filter-${opt.value}`"
                @click="wtFilter = opt.value"
              >
                {{ opt.label }} <span class="chip-count">{{ opt.count }}</span>
              </button>
            </div>

            <div class="toolbar-right">
              <el-select v-model="wtSort" size="small" class="sort-select" data-testid="wt-sort">
                <el-option value="size" :label="t('power.runtime.storage.sort.size')" />
                <el-option value="oldest" :label="t('power.runtime.storage.sort.oldest')" />
              </el-select>
            </div>
          </div>

          <div v-if="filteredWorktrees.length" class="bulk-bar">
            <el-checkbox
              :model-value="allVisibleWorktreesSelected"
              :indeterminate="someVisibleWorktreesSelected"
              :disabled="visibleCleanableWorktrees.length === 0"
              data-testid="wt-select-all"
              @change="toggleSelectAllWorktrees"
            >
              {{ t('power.runtime.storage.selectCleanable', { count: visibleCleanableWorktrees.length }) }}
            </el-checkbox>
            <div class="bulk-actions">
              <el-button
                v-if="staleWorktrees.length"
                size="small"
                plain
                :disabled="cleaning"
                data-testid="wt-clean-stale"
                @click="askCleanWorktrees(staleWorktrees)"
              >
                {{ t('power.runtime.storage.worktrees.cleanStale', { count: staleWorktrees.length }) }}
              </el-button>
              <el-button
                type="danger"
                size="small"
                plain
                :disabled="cleaning || selectedWorktreeItems.length === 0"
                data-testid="clean-selected-worktrees"
                @click="askCleanWorktrees(selectedWorktreeItems)"
              >
                {{ selectedWorktreeItems.length
                  ? t('power.runtime.storage.cleanSelectedWithSize', { count: selectedWorktreeItems.length, size: formatBytes(selectedWorktreeBytes) })
                  : t('power.runtime.storage.cleanSelected') }}
              </el-button>
            </div>
          </div>

          <div v-if="!filteredWorktrees.length" class="empty-state" data-testid="wt-empty">
            {{ overview.worktrees.length
              ? t('power.runtime.storage.noMatches')
              : t('power.runtime.storage.worktrees.empty') }}
          </div>

          <div v-else class="item-list">
            <div
              v-for="item in filteredWorktrees"
              :key="item.id"
              class="item-row"
              :class="{ 'item-row--muted': !item.can_clean }"
              :data-worktree="item.id"
            >
              <el-checkbox
                class="item-check"
                :model-value="selectedWorktrees.includes(item.id)"
                :disabled="!item.can_clean"
                @change="toggleWorktree(item.id, Boolean($event))"
              />
              <div class="item-main">
                <div class="item-head">
                  <el-tag size="small" :type="worktreeTagType(item.state)" effect="light">
                    {{ worktreeStateLabel(item.state) }}
                  </el-tag>
                  <el-tag v-if="item.project" size="small" effect="plain">{{ item.project }}</el-tag>
                  <el-tag v-if="item.stale" size="small" type="warning" effect="plain">
                    {{ item.state === 'discarding' ? t('power.runtime.storage.badges.retryPending') : t('power.runtime.storage.badges.stale') }}
                  </el-tag>
                  <span class="item-title">{{ worktreeTitle(item) }}</span>
                </div>
                <div class="item-meta">
                  <span v-if="worktreeReasonLabel(item)">{{ worktreeReasonLabel(item) }}</span>
                  <span v-if="item.conflicts.length" class="meta-warn">
                    {{ t(item.reason === 'skipped' ? 'power.trees.skippedFiles' : 'power.trees.conflicts', {
                      files: item.conflicts.slice(0, 3).join(', '),
                    }) }}
                    <template v-if="item.conflicts.length > 3">
                      {{ t('power.trees.conflictsMore', { count: item.conflicts.length - 3 }) }}
                    </template>
                  </span>
                  <span>{{ t('power.runtime.storage.updatedAt', { time: formatTime(item.updated_at) }) }}</span>
                </div>
                <div class="item-path">{{ item.workdir || item.path }}</div>
              </div>
              <div class="item-aside">
                <span class="item-size">
                  {{ item.exists ? formatMaybeBound(item.bytes, item.bytes_whole) : t('power.runtime.places.missing') }}
                </span>
                <div class="item-actions">
                  <el-button
                    v-if="item.can_land"
                    size="small"
                    type="primary"
                    plain
                    :loading="landingId === item.id"
                    :disabled="cleaning || landingId !== ''"
                    :data-testid="`worktree-land-${item.id}`"
                    @click="mergeWorktree(item)"
                  >
                    {{ t('power.trees.land') }}
                  </el-button>
                  <el-button
                    v-if="item.exists"
                    size="small"
                    :loading="opening === `worktree:${item.id}`"
                    :data-testid="`worktree-open-${item.id}`"
                    @click="openItem('worktree', item.id)"
                  >
                    {{ t('power.runtime.storage.open') }}
                  </el-button>
                  <el-button
                    v-if="item.can_clean"
                    size="small"
                    type="danger"
                    plain
                    :disabled="cleaning"
                    :data-testid="`worktree-clean-${item.id}`"
                    @click="askCleanWorktrees([item])"
                  >
                    {{ item.state === 'discarding' ? t('power.runtime.storage.retryClean') : t('power.runtime.storage.clean') }}
                  </el-button>
                  <span v-else class="in-use-label">{{ t('power.runtime.storage.badges.inUse') }}</span>
                </div>
              </div>
            </div>
          </div>

          <p v-if="overview.worktrees_more > 0" class="muted more-note">
            {{ t('power.runtime.storage.moreHidden', { count: overview.worktrees_more }) }}
          </p>
        </div>

        <!-- Tab B: Task Working Folders -->
        <div v-else-if="section === 'work_dirs'" class="tab-body" data-testid="section-workdirs">
          <p class="muted section-hint">{{ t('power.runtime.storage.workDirs.hint') }}</p>

          <div class="toolbar">
            <div class="filter-group" role="group">
              <button
                v-for="opt in wdFilterOptions"
                :key="opt.value"
                type="button"
                class="filter-chip"
                :class="{ 'is-active': wdFilter === opt.value }"
                :data-testid="`wd-filter-${opt.value}`"
                @click="wdFilter = opt.value"
              >
                {{ opt.label }} <span class="chip-count">{{ opt.count }}</span>
              </button>
            </div>

            <div class="toolbar-right">
              <el-select v-model="wdSort" size="small" class="sort-select" data-testid="wd-sort">
                <el-option value="size" :label="t('power.runtime.storage.sort.size')" />
                <el-option value="oldest" :label="t('power.runtime.storage.sort.oldest')" />
              </el-select>
            </div>
          </div>

          <div v-if="filteredWorkDirs.length" class="bulk-bar">
            <el-checkbox
              :model-value="allVisibleWorkDirsSelected"
              :indeterminate="someVisibleWorkDirsSelected"
              :disabled="visibleCleanableWorkDirs.length === 0"
              data-testid="wd-select-all"
              @change="toggleSelectAllWorkDirs"
            >
              {{ t('power.runtime.storage.selectCleanable', { count: visibleCleanableWorkDirs.length }) }}
            </el-checkbox>
            <div class="bulk-actions">
              <el-button
                v-if="emptyCleanableWorkDirs.length"
                size="small"
                plain
                :disabled="cleaning"
                data-testid="clean-empty-workdirs"
                @click="askCleanWorkDirs(emptyCleanableWorkDirs)"
              >
                {{ t('power.runtime.storage.workDirs.cleanEmpty', { count: emptyCleanableWorkDirs.length }) }}
              </el-button>
              <el-button
                v-if="staleWorkDirs.length"
                size="small"
                plain
                :disabled="cleaning"
                data-testid="wd-clean-stale"
                @click="askCleanWorkDirs(staleWorkDirs)"
              >
                {{ t('power.runtime.storage.workDirs.cleanStale', { count: staleWorkDirs.length }) }}
              </el-button>
              <el-button
                type="danger"
                size="small"
                plain
                :disabled="cleaning || selectedWorkDirItems.length === 0"
                data-testid="clean-selected-workdirs"
                @click="askCleanWorkDirs(selectedWorkDirItems)"
              >
                {{ selectedWorkDirItems.length
                  ? t('power.runtime.storage.cleanSelectedWithSize', { count: selectedWorkDirItems.length, size: formatBytes(selectedWorkDirBytes) })
                  : t('power.runtime.storage.cleanSelected') }}
              </el-button>
            </div>
          </div>

          <div v-if="!filteredWorkDirs.length" class="empty-state" data-testid="wd-empty">
            {{ overview.work_dirs.length
              ? t('power.runtime.storage.noMatches')
              : t('power.runtime.storage.workDirs.empty') }}
          </div>

          <div v-else class="item-list">
            <div
              v-for="item in filteredWorkDirs"
              :key="item.id"
              class="item-row"
              :class="{ 'item-row--muted': !item.can_clean }"
              :data-work-dir="item.id"
            >
              <el-checkbox
                class="item-check"
                :model-value="selectedWorkDirs.includes(item.id)"
                :disabled="!item.can_clean"
                @change="toggleWorkDir(item.id, Boolean($event))"
              />
              <div class="item-main">
                <div class="item-head">
                  <el-tag size="small" effect="plain">
                    {{ item.project === '_daily' ? t('power.home.loose') : item.project }}
                  </el-tag>
                  <el-tag v-if="item.scope === 'project'" size="small" type="info" effect="light">
                    {{ t('power.runtime.storage.badges.projectDefault') }}
                  </el-tag>
                  <el-tag v-if="item.referenced_by_project" size="small" type="warning" effect="plain">
                    {{ t('power.runtime.storage.badges.projectRef') }}
                  </el-tag>
                  <el-tag v-if="item.session_status === 'orphan'" size="small" type="danger" effect="light">
                    {{ t('power.runtime.storage.badges.orphan') }}
                  </el-tag>
                  <el-tag v-if="item.in_use" size="small" type="success" effect="light">
                    {{ t('power.runtime.storage.badges.inUse') }}
                  </el-tag>
                  <el-tag v-else-if="item.empty" size="small" type="info" effect="plain">
                    {{ t('power.runtime.storage.badges.emptyDir') }}
                  </el-tag>
                  <el-tag v-else-if="item.stale" size="small" type="warning" effect="plain">
                    {{ t('power.runtime.storage.badges.stale') }}
                  </el-tag>
                  <span class="item-title">{{ workDirTitle(item) }}</span>
                </div>
                <div class="item-meta">
                  <span>{{ t('power.runtime.storage.workDirs.fileCount', { count: item.file_count }) }}</span>
                  <span>{{ t('power.runtime.storage.lastActiveAt', { time: formatTime(item.last_active_at) }) }}</span>
                </div>
                <div class="item-path">{{ item.path }}</div>
              </div>
              <div class="item-aside">
                <span class="item-size">{{ formatMaybeBound(item.bytes, item.bytes_whole) }}</span>
                <div class="item-actions">
                  <el-button
                    size="small"
                    :loading="opening === `work_dir:${item.id}`"
                    :data-testid="`workdir-open-${item.id}`"
                    @click="openItem('work_dir', item.id)"
                  >
                    {{ t('power.runtime.storage.open') }}
                  </el-button>
                  <el-button
                    size="small"
                    type="danger"
                    plain
                    :disabled="!item.can_clean || cleaning"
                    :data-testid="`workdir-clean-${item.id}`"
                    @click="askCleanWorkDirs([item])"
                  >
                    {{ t('power.runtime.storage.clean') }}
                  </el-button>
                </div>
              </div>
            </div>
          </div>

          <p v-if="overview.work_dirs_more > 0" class="muted more-note">
            {{ t('power.runtime.storage.moreHidden', { count: overview.work_dirs_more }) }}
          </p>
        </div>

        <!-- Tab C: Transcripts & Tool Outputs -->
        <div v-else class="tab-body" data-testid="section-transcripts">
          <p class="muted section-hint">
            {{ t('power.runtime.storage.transcripts.hint', {
              sessions: overview.transcripts.sessions,
              size: formatMaybeBound(overview.transcripts.bytes, overview.transcripts.bytes_whole),
              days: overview.settings.transcript_kept_days,
            }) }}
          </p>

          <div class="transcript-grid">
            <div class="sub-card" data-testid="transcripts-expired-card">
              <div class="sub-card__head">
                <h3>{{ t('power.runtime.storage.transcripts.expiredTitle') }}</h3>
                <strong class="sub-card__size">{{ formatBytes(overview.transcripts.expired_bytes) }}</strong>
              </div>
              <p class="muted">
                {{ t('power.runtime.storage.transcripts.expiredHint', {
                  count: overview.transcripts.expired_count,
                  days: overview.settings.transcript_kept_days,
                }) }}
              </p>
              <el-button
                type="danger"
                plain
                size="small"
                :disabled="cleaning || overview.transcripts.expired_count === 0"
                data-testid="clean-expired-transcripts"
                @click="askCleanTranscripts('expired')"
              >
                {{ t('power.runtime.storage.transcripts.cleanExpired') }}
              </el-button>
            </div>

            <div class="sub-card" data-testid="transcripts-outputs-card">
              <div class="sub-card__head">
                <h3>{{ t('power.runtime.storage.transcripts.outputsTitle') }}</h3>
                <strong class="sub-card__size">{{ formatBytes(overview.transcripts.outputs_bytes) }}</strong>
              </div>
              <p class="muted">
                {{ t('power.runtime.storage.transcripts.outputsHint', { count: overview.transcripts.outputs_count }) }}
              </p>
              <el-button
                type="danger"
                plain
                size="small"
                :disabled="cleaning || overview.transcripts.outputs_count === 0"
                data-testid="clean-output-caches"
                @click="askCleanTranscripts('outputs')"
              >
                {{ t('power.runtime.storage.transcripts.cleanOutputs') }}
              </el-button>
            </div>
          </div>
        </div>
      </section>

      <!-- 3. Automatic Cleanup Retention Policy -->
      <section class="panel policy-card" data-testid="storage-policy">
        <div class="heading">
          <div>
            <h2>{{ t('power.runtime.storage.policy.title') }}</h2>
            <p class="muted">{{ t('power.runtime.storage.policy.subtitle') }}</p>
          </div>
          <div class="header-actions">
            <el-button
              size="small"
              :disabled="savingPolicy || policyIsDefault"
              data-testid="policy-reset"
              @click="resetPolicy"
            >
              {{ t('power.runtime.storage.policy.reset') }}
            </el-button>
            <el-button
              type="primary"
              size="small"
              :loading="savingPolicy"
              :disabled="!policyDirty"
              data-testid="policy-save"
              @click="savePolicy"
            >
              {{ t('power.runtime.storage.policy.save') }}
            </el-button>
          </div>
        </div>

        <div class="policy-rows">
          <div class="policy-row">
            <div class="policy-info">
              <div class="policy-title-line">
                <el-switch v-model="policyDraft.auto_clean_work_dirs" :disabled="savingPolicy" data-testid="policy-workdirs-enabled" />
                <strong>{{ t('power.runtime.storage.policy.workDirsTitle') }}</strong>
              </div>
              <p class="muted">{{ t('power.runtime.storage.policy.workDirsHint') }}</p>
            </div>
            <div class="policy-control">
              <span class="muted">{{ t('power.runtime.storage.policy.keepFor') }}</span>
              <el-input-number
                v-model="policyDraft.work_dir_kept_days"
                :min="overview.settings.min_days"
                :max="overview.settings.max_days"
                :step="1"
                :precision="0"
                size="small"
                :disabled="!policyDraft.auto_clean_work_dirs || savingPolicy"
                data-testid="policy-workdirs-days"
              />
              <span class="muted">{{ t('power.runtime.storage.policy.days') }}</span>
            </div>
          </div>

          <div class="policy-row">
            <div class="policy-info">
              <div class="policy-title-line">
                <el-switch v-model="policyDraft.auto_clean_worktrees" :disabled="savingPolicy" data-testid="policy-worktrees-enabled" />
                <strong>{{ t('power.runtime.storage.policy.worktreesTitle') }}</strong>
              </div>
              <p class="muted">{{ t('power.runtime.storage.policy.worktreesHint') }}</p>
            </div>
            <div class="policy-control">
              <span class="muted">{{ t('power.runtime.storage.policy.keepFor') }}</span>
              <el-input-number
                v-model="policyDraft.worktree_kept_days"
                :min="overview.settings.min_days"
                :max="overview.settings.max_days"
                :step="1"
                :precision="0"
                size="small"
                :disabled="!policyDraft.auto_clean_worktrees || savingPolicy"
                data-testid="policy-worktrees-days"
              />
              <span class="muted">{{ t('power.runtime.storage.policy.days') }}</span>
            </div>
          </div>
        </div>

        <p class="muted policy-footnote">
          {{ t('power.runtime.storage.policy.protectedNote', { days: overview.settings.transcript_kept_days }) }}
        </p>
        <p v-if="policyNote" class="policy-saved" role="status" data-testid="policy-note">{{ policyNote }}</p>
      </section>
    </template>

    <!-- 4. All Host Directories Table (always shown; also serves as fallback for older backends) -->
    <section class="panel" data-testid="places-list">
      <div class="heading">
        <h2>{{ t('power.runtime.places.title') }}</h2>
        <el-button v-if="!overview" :loading="loading" @click="reloadFresh">{{ t('common.refresh') }}</el-button>
      </div>
      <p class="muted">{{ t('power.runtime.places.hint') }}</p>
      <table class="rows">
        <thead>
          <tr>
            <th>{{ t('power.runtime.places.what') }}</th>
            <th>{{ t('power.runtime.places.where') }}</th>
            <th>{{ t('power.runtime.places.holds') }}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="place in places" :key="place.key" :data-place="place.key">
            <td>{{ placeLabel(place.key) }}</td>
            <td class="path">{{ place.path }}</td>
            <td class="muted">{{ holds(place) }}</td>
            <td class="act">
              <el-button v-if="place.exists" size="small" :loading="opening === `place:${place.key}`" @click="openHostPlace(place.key)">
                {{ t('power.runtime.places.open') }}
              </el-button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- Confirmation dialog for all destructive cleanup actions -->
    <el-dialog
      v-model="confirmOpen"
      :title="pending?.title ?? ''"
      width="520px"
      append-to-body
      data-testid="storage-confirm-dialog"
    >
      <template v-if="pending">
        <p class="confirm-desc">{{ pending.description }}</p>
        <div v-for="(warn, idx) in pending.warnings" :key="idx" class="confirm-warn" data-testid="storage-confirm-warn">
          {{ warn }}
        </div>
        <ul v-if="pending.items.length" class="confirm-list">
          <li v-for="entry in pending.items.slice(0, PREVIEW_LIMIT)" :key="entry.id">
            <div class="confirm-list__row">
              <strong>{{ entry.label }}</strong>
              <span v-if="entry.size" class="muted">{{ entry.size }}</span>
            </div>
            <div v-if="entry.path" class="confirm-list__path">{{ entry.path }}</div>
          </li>
        </ul>
        <p v-if="pending.items.length > PREVIEW_LIMIT" class="muted">
          {{ t('power.runtime.storage.confirm.andMore', { count: pending.items.length - PREVIEW_LIMIT }) }}
        </p>
      </template>
      <template #footer>
        <el-button :disabled="cleaning" @click="confirmOpen = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="danger"
          :loading="cleaning"
          data-testid="storage-confirm-submit"
          @click="runConfirmedClean"
        >
          {{ t('power.runtime.storage.confirm.proceed') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  cleanTranscripts,
  cleanWorkDirs,
  cleanWorktrees,
  getPlaces,
  getStorage,
  landWorktree,
  openPlace,
  openStorageItem,
  setStorageSettings,
} from '@/api/console'
import type {
  Landing,
  Place,
  StorageCategoryKey,
  StorageOpenKind,
  StorageOverview,
  StorageSettingsValues,
  StorageWorkDir,
  StorageWorktree,
} from '@/api/console'
import { describeConsoleError, formatBytes } from './powerFormat'

type SectionTab = 'worktrees' | 'work_dirs' | 'transcripts'
type WorktreeFilter = 'all' | 'cleanable' | 'stale' | 'active'
type WorkDirFilter = 'all' | 'cleanable' | 'stale' | 'empty' | 'in_use'
type SortKey = 'size' | 'oldest'

interface PendingClean {
  title: string
  description: string
  warnings: string[]
  items: Array<{ id: string; label: string; path?: string; size?: string }>
  wtIds: string[]
  wdIds: string[]
  transcriptMode: 'expired' | 'outputs' | null
}

const PREVIEW_LIMIT = 8
const LOW_DISK_BYTES = 5 * 1024 * 1024 * 1024

const CATEGORY_COLORS: Record<string, string> = {
  worktrees: '#f59e0b',
  work_dirs: '#40c5f1',
  transcripts: '#8b5cf6',
  space: '#10b981',
  dsh: '#6366f1',
  browser_profile: '#ec4899',
  other: '#94a3b8',
}

const { t, te } = useI18n()

const overview = ref<StorageOverview | null>(null)
const places = ref<Place[]>([])
const loading = ref(false)
const opening = ref('')
const landingId = ref('')
const cleaning = ref(false)
const savingPolicy = ref(false)
const error = ref('')
const banner = ref<{ type: 'success' | 'warning'; text: string } | null>(null)
const policyNote = ref('')

const section = ref<SectionTab>('worktrees')
const hasPickedInitialTab = ref(false)
const wtFilter = ref<WorktreeFilter>('all')
const wtSort = ref<SortKey>('size')
const wdFilter = ref<WorkDirFilter>('all')
const wdSort = ref<SortKey>('size')

const selectedWorktrees = ref<string[]>([])
const selectedWorkDirs = ref<string[]>([])

const confirmOpen = ref(false)
const pending = ref<PendingClean | null>(null)

const policyDraft = reactive<StorageSettingsValues>({
  auto_clean_work_dirs: true,
  work_dir_kept_days: 30,
  auto_clean_worktrees: true,
  worktree_kept_days: 14,
})

// -- Formatting helpers ------------------------------------------------------

function formatMaybeBound(bytes: number | null | undefined, whole: boolean): string {
  const size = formatBytes(bytes)
  if (!size) return '0 B'
  return whole ? size : t('power.runtime.places.atLeast', { size })
}

function holds(place: Place): string {
  if (!place.exists) return t('power.runtime.places.missing')
  return formatMaybeBound(place.bytes, place.bytes_whole)
}

function placeLabel(key: string): string {
  const i18nKey = `power.runtime.places.keys.${key}`
  return te(i18nKey) ? t(i18nKey) : key
}

function categoryColor(key: StorageCategoryKey): string {
  return CATEGORY_COLORS[key] ?? '#94a3b8'
}

function categoryLabel(key: StorageCategoryKey): string {
  const i18nKey = `power.runtime.storage.categories.${key}`
  return te(i18nKey) ? t(i18nKey) : key
}

function isJumpableCategory(key: StorageCategoryKey): key is SectionTab {
  return key === 'worktrees' || key === 'work_dirs' || key === 'transcripts'
}

function jumpToCategory(key: StorageCategoryKey) {
  if (isJumpableCategory(key)) section.value = key
}

function formatTime(ts: number | null | undefined): string {
  if (!ts || !Number.isFinite(ts)) return '—'
  const date = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function worktreeStateLabel(state: string): string {
  if (state === 'orphan') return t('power.runtime.storage.worktrees.states.orphan')
  if (state === 'discarding') return t('power.runtime.storage.worktrees.states.discarding')
  const key = `power.trees.states.${state}`
  return te(key) ? t(key) : state
}

function worktreeTagType(state: string): 'warning' | 'danger' | 'info' | 'primary' {
  if (state === 'unlanded' || state === 'orphan') return 'danger'
  if (state === 'held' || state === 'discarding') return 'warning'
  if (state === 'kept') return 'info'
  return 'primary'
}

function worktreeReasonLabel(item: StorageWorktree): string {
  if (!item.reason || item.reason === 'ask') return ''
  const key = `power.trees.reasons.${item.reason}`
  return te(key) ? t(key) : item.reason
}

function worktreeTitle(item: StorageWorktree): string {
  const parts = [item.session_name, item.title].filter(Boolean)
  if (parts.length) return parts.join(' · ')
  return item.kind === 'orphan' ? `${t('power.runtime.storage.worktrees.orphanPrefix')} ${item.id}` : item.id
}

function workDirTitle(item: StorageWorkDir): string {
  if (item.scope === 'project') {
    return item.title || t('power.runtime.storage.workDirs.projectDefaultName', { project: item.project })
  }
  const parts = [item.session_name, item.title].filter(Boolean)
  return parts.length ? parts.join(' · ') : (item.session_id || item.id)
}

// -- Derived metrics & filters ----------------------------------------------

const barSegments = computed(() => (overview.value?.categories ?? []).filter(cat => cat.bytes > 0))

const diskUsedPercent = computed(() => {
  const disk = overview.value?.disk
  if (!disk || disk.total <= 0) return 0
  return Math.min(100, Math.max(0, Math.round((disk.used / disk.total) * 100)))
})

const diskLow = computed(() => {
  const disk = overview.value?.disk
  if (!disk) return false
  return disk.free < LOW_DISK_BYTES || diskUsedPercent.value >= 90
})

const diskShareText = computed(() => {
  const data = overview.value
  if (!data?.disk || data.disk.total <= 0 || data.total_bytes <= 0) return ''
  const pct = (data.total_bytes / data.disk.total) * 100
  const formatted = pct < 0.1 ? '<0.1%' : `${pct.toFixed(1)}%`
  return t('power.runtime.storage.kpi.shareOfDisk', { percent: formatted })
})

const allWorktrees = computed(() => overview.value?.worktrees ?? [])
const allWorkDirs = computed(() => overview.value?.work_dirs ?? [])

const cleanableWorktrees = computed(() => allWorktrees.value.filter(w => w.can_clean))
const staleWorktrees = computed(() => allWorktrees.value.filter(w => w.stale && w.can_clean))
const activeWorktrees = computed(() => allWorktrees.value.filter(w => !w.can_clean))

const cleanableWorkDirs = computed(() => allWorkDirs.value.filter(d => d.can_clean))
const staleWorkDirs = computed(() => allWorkDirs.value.filter(d => d.stale && d.can_clean))
const emptyCleanableWorkDirs = computed(() => allWorkDirs.value.filter(d => d.empty && d.can_clean))
const inUseWorkDirs = computed(() => allWorkDirs.value.filter(d => d.in_use))

const staleSummary = computed(() => {
  const wtBytes = staleWorktrees.value.reduce((acc, item) => acc + (item.bytes || 0), 0)
  const wdBytes = staleWorkDirs.value.reduce((acc, item) => acc + (item.bytes || 0), 0)
  const expCount = overview.value?.transcripts.expired_count ?? 0
  const expBytes = overview.value?.transcripts.expired_bytes ?? 0
  return {
    wtIds: staleWorktrees.value.map(w => w.id),
    wdIds: staleWorkDirs.value.map(d => d.id),
    expiredTranscripts: expCount,
    totalCount: staleWorktrees.value.length + staleWorkDirs.value.length + expCount,
    bytes: wtBytes + wdBytes + expBytes,
  }
})

const wtFilterOptions = computed<Array<{ value: WorktreeFilter; label: string; count: number }>>(() => [
  { value: 'all', label: t('power.runtime.storage.filters.all'), count: allWorktrees.value.length },
  { value: 'cleanable', label: t('power.runtime.storage.filters.cleanable'), count: cleanableWorktrees.value.length },
  { value: 'stale', label: t('power.runtime.storage.filters.stale'), count: staleWorktrees.value.length },
  { value: 'active', label: t('power.runtime.storage.filters.active'), count: activeWorktrees.value.length },
])

const wdFilterOptions = computed<Array<{ value: WorkDirFilter; label: string; count: number }>>(() => [
  { value: 'all', label: t('power.runtime.storage.filters.all'), count: allWorkDirs.value.length },
  { value: 'cleanable', label: t('power.runtime.storage.filters.cleanable'), count: cleanableWorkDirs.value.length },
  { value: 'stale', label: t('power.runtime.storage.filters.stale'), count: staleWorkDirs.value.length },
  { value: 'empty', label: t('power.runtime.storage.filters.empty'), count: emptyCleanableWorkDirs.value.length },
  { value: 'in_use', label: t('power.runtime.storage.filters.inUse'), count: inUseWorkDirs.value.length },
])

const filteredWorktrees = computed(() => {
  const list = allWorktrees.value.filter(item => {
    if (wtFilter.value === 'cleanable') return item.can_clean
    if (wtFilter.value === 'stale') return item.stale && item.can_clean
    if (wtFilter.value === 'active') return !item.can_clean
    return true
  })
  return [...list].sort((a, b) => {
    if (wtSort.value === 'oldest') {
      return (a.updated_at ?? 0) - (b.updated_at ?? 0) || (b.bytes - a.bytes)
    }
    return (b.bytes - a.bytes) || ((a.updated_at ?? 0) - (b.updated_at ?? 0))
  })
})

const filteredWorkDirs = computed(() => {
  const list = allWorkDirs.value.filter(item => {
    if (wdFilter.value === 'cleanable') return item.can_clean
    if (wdFilter.value === 'stale') return item.stale && item.can_clean
    if (wdFilter.value === 'empty') return item.empty && item.can_clean
    if (wdFilter.value === 'in_use') return item.in_use
    return true
  })
  return [...list].sort((a, b) => {
    if (wdSort.value === 'oldest') {
      return (a.last_active_at ?? 0) - (b.last_active_at ?? 0) || (b.bytes - a.bytes)
    }
    return (b.bytes - a.bytes) || ((a.last_active_at ?? 0) - (b.last_active_at ?? 0))
  })
})

// -- Selection helpers -------------------------------------------------------

const visibleCleanableWorktrees = computed(() => filteredWorktrees.value.filter(w => w.can_clean))
const selectedWorktreeItems = computed(() => allWorktrees.value.filter(w => w.can_clean && selectedWorktrees.value.includes(w.id)))
const selectedWorktreeBytes = computed(() => selectedWorktreeItems.value.reduce((acc, w) => acc + (w.bytes || 0), 0))
const allVisibleWorktreesSelected = computed(() =>
  visibleCleanableWorktrees.value.length > 0 && visibleCleanableWorktrees.value.every(w => selectedWorktrees.value.includes(w.id)),
)
const someVisibleWorktreesSelected = computed(() =>
  visibleCleanableWorktrees.value.some(w => selectedWorktrees.value.includes(w.id)) && !allVisibleWorktreesSelected.value,
)

const visibleCleanableWorkDirs = computed(() => filteredWorkDirs.value.filter(d => d.can_clean))
const selectedWorkDirItems = computed(() => allWorkDirs.value.filter(d => d.can_clean && selectedWorkDirs.value.includes(d.id)))
const selectedWorkDirBytes = computed(() => selectedWorkDirItems.value.reduce((acc, d) => acc + (d.bytes || 0), 0))
const allVisibleWorkDirsSelected = computed(() =>
  visibleCleanableWorkDirs.value.length > 0 && visibleCleanableWorkDirs.value.every(d => selectedWorkDirs.value.includes(d.id)),
)
const someVisibleWorkDirsSelected = computed(() =>
  visibleCleanableWorkDirs.value.some(d => selectedWorkDirs.value.includes(d.id)) && !allVisibleWorkDirsSelected.value,
)

function toggleWorktree(id: string, checked: boolean) {
  if (checked) {
    if (!selectedWorktrees.value.includes(id)) selectedWorktrees.value = [...selectedWorktrees.value, id]
  } else {
    selectedWorktrees.value = selectedWorktrees.value.filter(x => x !== id)
  }
}

function toggleSelectAllWorktrees(checked: string | number | boolean) {
  const visibleIds = visibleCleanableWorktrees.value.map(w => w.id)
  if (checked === true) {
    selectedWorktrees.value = Array.from(new Set([...selectedWorktrees.value, ...visibleIds]))
  } else {
    const drop = new Set(visibleIds)
    selectedWorktrees.value = selectedWorktrees.value.filter(id => !drop.has(id))
  }
}

function toggleWorkDir(id: string, checked: boolean) {
  if (checked) {
    if (!selectedWorkDirs.value.includes(id)) selectedWorkDirs.value = [...selectedWorkDirs.value, id]
  } else {
    selectedWorkDirs.value = selectedWorkDirs.value.filter(x => x !== id)
  }
}

function toggleSelectAllWorkDirs(checked: string | number | boolean) {
  const visibleIds = visibleCleanableWorkDirs.value.map(d => d.id)
  if (checked === true) {
    selectedWorkDirs.value = Array.from(new Set([...selectedWorkDirs.value, ...visibleIds]))
  } else {
    const drop = new Set(visibleIds)
    selectedWorkDirs.value = selectedWorkDirs.value.filter(id => !drop.has(id))
  }
}

// -- Policy draft helpers ----------------------------------------------------

function syncPolicyDraft(settings: StorageSettingsValues) {
  policyDraft.auto_clean_work_dirs = settings.auto_clean_work_dirs
  policyDraft.work_dir_kept_days = settings.work_dir_kept_days
  policyDraft.auto_clean_worktrees = settings.auto_clean_worktrees
  policyDraft.worktree_kept_days = settings.worktree_kept_days
}

const policyDirty = computed(() => {
  const current = overview.value?.settings
  if (!current) return false
  return (
    policyDraft.auto_clean_work_dirs !== current.auto_clean_work_dirs ||
    policyDraft.work_dir_kept_days !== current.work_dir_kept_days ||
    policyDraft.auto_clean_worktrees !== current.auto_clean_worktrees ||
    policyDraft.worktree_kept_days !== current.worktree_kept_days
  )
})

const policyIsDefault = computed(() => {
  const defs = overview.value?.settings.defaults
  if (!defs) return true
  return (
    policyDraft.auto_clean_work_dirs === defs.auto_clean_work_dirs &&
    policyDraft.work_dir_kept_days === defs.work_dir_kept_days &&
    policyDraft.auto_clean_worktrees === defs.auto_clean_worktrees &&
    policyDraft.worktree_kept_days === defs.worktree_kept_days
  )
})

async function savePolicy() {
  const current = overview.value?.settings
  if (!current || savingPolicy.value || !policyDirty.value) return
  const changes: Partial<StorageSettingsValues> = {}
  if (policyDraft.auto_clean_work_dirs !== current.auto_clean_work_dirs) changes.auto_clean_work_dirs = policyDraft.auto_clean_work_dirs
  if (policyDraft.work_dir_kept_days !== current.work_dir_kept_days) changes.work_dir_kept_days = Math.round(policyDraft.work_dir_kept_days)
  if (policyDraft.auto_clean_worktrees !== current.auto_clean_worktrees) changes.auto_clean_worktrees = policyDraft.auto_clean_worktrees
  if (policyDraft.worktree_kept_days !== current.worktree_kept_days) changes.worktree_kept_days = Math.round(policyDraft.worktree_kept_days)
  savingPolicy.value = true
  policyNote.value = ''
  error.value = ''
  try {
    const res = await setStorageSettings(changes)
    if (overview.value && res?.settings) {
      overview.value = { ...overview.value, settings: res.settings }
      syncPolicyDraft(res.settings)
    }
    policyNote.value = t('power.runtime.storage.policy.saved')
    await load(true)
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    savingPolicy.value = false
  }
}

async function resetPolicy() {
  const defs = overview.value?.settings.defaults
  if (!defs || savingPolicy.value) return
  syncPolicyDraft(defs)
  if (policyDirty.value) {
    await savePolicy()
  }
}

// -- Loading & fallback ------------------------------------------------------

async function load(preserveBanner = false) {
  loading.value = true
  if (!preserveBanner) banner.value = null
  try {
    const data = await getStorage()
    if (data && Array.isArray(data.places)) {
      overview.value = data
      places.value = data.places
      syncPolicyDraft(data.settings)
      const validWt = new Set(data.worktrees.filter(w => w.can_clean).map(w => w.id))
      selectedWorktrees.value = selectedWorktrees.value.filter(id => validWt.has(id))
      const validWd = new Set(data.work_dirs.filter(d => d.can_clean).map(d => d.id))
      selectedWorkDirs.value = selectedWorkDirs.value.filter(id => validWd.has(id))
      if (!hasPickedInitialTab.value) {
        hasPickedInitialTab.value = true
        if (data.worktrees.length === 0 && data.work_dirs.length > 0) section.value = 'work_dirs'
      }
      error.value = ''
      return
    }
    // Older backend or partial mock: fall back to /console/places.
    places.value = (await getPlaces()).places
    overview.value = null
    error.value = ''
  } catch (failure) {
    const status = (failure as { response?: { status?: number } })?.response?.status
    if (status === 404) {
      try {
        places.value = (await getPlaces()).places
        overview.value = null
        error.value = ''
        return
      } catch (fallbackErr) {
        error.value = describeConsoleError(fallbackErr, t, te)
        return
      }
    }
    error.value = describeConsoleError(failure, t, te)
  } finally {
    loading.value = false
  }
}

function reloadFresh() {
  policyNote.value = ''
  void load(false)
}

// -- Open & merge actions ----------------------------------------------------

async function openHostPlace(key: string) {
  opening.value = `place:${key}`
  try {
    await openPlace(key)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    opening.value = ''
  }
}

async function openItem(kind: StorageOpenKind, id: string) {
  opening.value = `${kind}:${id}`
  try {
    await openStorageItem(kind, id)
    error.value = ''
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    opening.value = ''
  }
}

function describeLanding(result: Landing, state: string): { type: 'success' | 'warning'; text: string } {
  if (result.status === 'landed' || result.status === 'unchanged') {
    const parts = [
      result.status === 'landed'
        ? t('power.trees.landed', { count: result.files.length + result.more })
        : t('power.trees.unchanged'),
    ]
    if (result.outside?.length) parts.push(t('power.trees.outsideNote', { files: result.outside.join(', ') }))
    if (result.skipped?.length) parts.push(t('power.trees.skippedNote', { files: result.skipped.join(', ') }))
    return { type: result.skipped?.length ? 'warning' : 'success', text: parts.join(' ') }
  }
  if (result.status === 'conflict') {
    if (result.resolving) return { type: 'warning', text: t('power.trees.resolving') }
    return { type: 'warning', text: state === 'kept' ? t('power.trees.keptClashes') : t('power.trees.stillClashes') }
  }
  const kept = result.kept_copies?.length ? ` ${t('power.trees.keptCopies', { paths: result.kept_copies.join(', ') })}` : ''
  return { type: 'warning', text: `${t('power.trees.failed', { detail: result.detail || '—' })}${kept}` }
}

async function mergeWorktree(item: StorageWorktree) {
  if (landingId.value || cleaning.value) return
  landingId.value = item.id
  error.value = ''
  try {
    const result = await landWorktree(item.id)
    banner.value = describeLanding(result, item.state)
    await load(true)
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    landingId.value = ''
  }
}

// -- Destructive cleanup confirmation & execution ---------------------------

function askCleanWorktrees(items: StorageWorktree[]) {
  const targets = items.filter(w => w.can_clean)
  if (!targets.length) return
  const totalBytes = targets.reduce((acc, w) => acc + (w.bytes || 0), 0)
  const warnings: string[] = []
  if (targets.some(w => w.state === 'held' || w.state === 'unlanded' || w.state === 'kept')) {
    warnings.push(t('power.runtime.storage.confirm.worktreesUnmergedWarn'))
  }
  const skippedFiles = targets.filter(w => w.reason === 'skipped' && w.conflicts.length).flatMap(w => w.conflicts)
  if (skippedFiles.length) {
    warnings.push(t('power.trees.discardSkipped', { files: skippedFiles.slice(0, 5).join(', ') }))
  }
  pending.value = {
    title: t('power.runtime.storage.confirm.worktreesTitle', { count: targets.length }),
    description: t('power.runtime.storage.confirm.worktreesText', {
      count: targets.length,
      size: formatBytes(totalBytes),
    }),
    warnings,
    items: targets.map(w => ({
      id: w.id,
      label: worktreeTitle(w),
      path: w.workdir || w.path,
      size: w.exists ? formatMaybeBound(w.bytes, w.bytes_whole) : t('power.runtime.places.missing'),
    })),
    wtIds: targets.map(w => w.id),
    wdIds: [],
    transcriptMode: null,
  }
  confirmOpen.value = true
}

function askCleanWorkDirs(items: StorageWorkDir[]) {
  const targets = items.filter(d => d.can_clean)
  if (!targets.length) return
  const totalBytes = targets.reduce((acc, d) => acc + (d.bytes || 0), 0)
  const warnings: string[] = []
  if (targets.some(d => d.scope === 'project' || d.referenced_by_project)) {
    warnings.push(t('power.runtime.storage.confirm.projectWorkDirWarn'))
  }
  pending.value = {
    title: t('power.runtime.storage.confirm.workDirsTitle', { count: targets.length }),
    description: t('power.runtime.storage.confirm.workDirsText', {
      count: targets.length,
      size: formatBytes(totalBytes),
    }),
    warnings,
    items: targets.map(d => ({
      id: d.id,
      label: workDirTitle(d),
      path: d.path,
      size: formatMaybeBound(d.bytes, d.bytes_whole),
    })),
    wtIds: [],
    wdIds: targets.map(d => d.id),
    transcriptMode: null,
  }
  confirmOpen.value = true
}

function askCleanTranscripts(mode: 'expired' | 'outputs') {
  const tr = overview.value?.transcripts
  if (!tr) return
  const count = mode === 'expired' ? tr.expired_count : tr.outputs_count
  const bytes = mode === 'expired' ? tr.expired_bytes : tr.outputs_bytes
  if (count === 0) return
  pending.value = {
    title: t(mode === 'expired'
      ? 'power.runtime.storage.confirm.expiredTitle'
      : 'power.runtime.storage.confirm.outputsTitle'),
    description: t(mode === 'expired'
      ? 'power.runtime.storage.confirm.expiredText'
      : 'power.runtime.storage.confirm.outputsText', { count, size: formatBytes(bytes) }),
    warnings: [],
    items: [],
    wtIds: [],
    wdIds: [],
    transcriptMode: mode,
  }
  confirmOpen.value = true
}

function askCleanAllStale() {
  const s = staleSummary.value
  if (s.totalCount === 0) return
  const warnings: string[] = []
  if (staleWorktrees.value.some(w => w.state === 'held' || w.state === 'unlanded' || w.state === 'kept')) {
    warnings.push(t('power.runtime.storage.confirm.worktreesUnmergedWarn'))
  }
  const preview = [
    ...staleWorktrees.value.map(w => ({
      id: `wt:${w.id}`,
      label: `${t('power.runtime.storage.tabs.worktrees')} · ${worktreeTitle(w)}`,
      path: w.workdir || w.path,
      size: formatMaybeBound(w.bytes, w.bytes_whole),
    })),
    ...staleWorkDirs.value.map(d => ({
      id: `wd:${d.id}`,
      label: `${t('power.runtime.storage.tabs.workDirs')} · ${workDirTitle(d)}`,
      path: d.path,
      size: formatMaybeBound(d.bytes, d.bytes_whole),
    })),
  ]
  if (s.expiredTranscripts > 0) {
    preview.push({
      id: 'tr:expired',
      label: t('power.runtime.storage.transcripts.expiredSummaryItem', { count: s.expiredTranscripts }),
      path: '',
      size: formatBytes(overview.value?.transcripts.expired_bytes ?? 0),
    })
  }
  pending.value = {
    title: t('power.runtime.storage.confirm.staleAllTitle'),
    description: t('power.runtime.storage.confirm.staleAllText', {
      worktrees: s.wtIds.length,
      workDirs: s.wdIds.length,
      transcripts: s.expiredTranscripts,
      size: formatBytes(s.bytes),
    }),
    warnings,
    items: preview,
    wtIds: s.wtIds,
    wdIds: s.wdIds,
    transcriptMode: s.expiredTranscripts > 0 ? 'expired' : null,
  }
  confirmOpen.value = true
}

async function runConfirmedClean() {
  const job = pending.value
  if (!job || cleaning.value) return
  cleaning.value = true
  error.value = ''
  let cleanedCount = 0
  let deferredCount = 0
  let failedCount = 0
  let freedBytes = 0
  try {
    if (job.wtIds.length) {
      const res = await cleanWorktrees(job.wtIds)
      cleanedCount += res.cleaned.length
      deferredCount += res.deferred.length
      failedCount += res.failed.length
      freedBytes += res.freed_bytes
    }
    if (job.wdIds.length) {
      const res = await cleanWorkDirs(job.wdIds)
      cleanedCount += res.cleaned.length
      failedCount += res.failed.length
      freedBytes += res.freed_bytes
    }
    if (job.transcriptMode) {
      const res = await cleanTranscripts(job.transcriptMode)
      cleanedCount += res.cleaned
      failedCount += res.skipped
      freedBytes += res.freed_bytes
    }
    const parts: string[] = [
      t('power.runtime.storage.result.cleaned', { count: cleanedCount, size: formatBytes(freedBytes) }),
    ]
    if (deferredCount > 0) {
      parts.push(t('power.runtime.storage.result.deferred', { count: deferredCount }))
    }
    if (failedCount > 0) {
      parts.push(t('power.runtime.storage.result.failed', { count: failedCount }))
    }
    banner.value = {
      type: deferredCount > 0 || failedCount > 0 ? 'warning' : 'success',
      text: parts.join(' '),
    }
    confirmOpen.value = false
    pending.value = null
    await load(true)
  } catch (failure) {
    error.value = describeConsoleError(failure, t, te)
  } finally {
    cleaning.value = false
  }
}

onMounted(() => void load())
defineExpose({ load })
</script>

<style scoped>
.storage-panel { display: flex; flex-direction: column; gap: 16px; }
.panel { background: var(--el-bg-color); border: 1px solid var(--surface-border, var(--el-border-color-lighter)); border-radius: var(--radius-card, 12px); padding: 18px 20px; }
.heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.heading h2 { margin: 0; font-size: 16px; font-weight: 600; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.muted { color: var(--el-text-color-secondary); font-size: 13px; line-height: 1.6; margin: 4px 0 0; }
.block { margin-bottom: 0; }

/* KPI strip */
.kpi-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.kpi-tile { display: flex; flex-direction: column; gap: 4px; padding: 14px 16px; border-radius: 10px; border: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-lighter); }
.kpi-tile--accent { border-color: rgba(64, 197, 241, 0.45); background: rgba(64, 197, 241, 0.08); }
.kpi-tile--warn { border-color: rgba(245, 158, 11, 0.5); background: rgba(245, 158, 11, 0.08); }
.kpi-label { font-size: 12px; color: var(--el-text-color-secondary); }
.kpi-value { font-size: 22px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; color: var(--el-text-color-primary); }
.kpi-sub { font-size: 12px; color: var(--el-text-color-secondary); }
.disk-meter { height: 5px; border-radius: 999px; background: var(--el-border-color-lighter); overflow: hidden; margin: 2px 0; }
.disk-meter__fill { height: 100%; border-radius: 999px; background: var(--el-color-primary); transition: width 0.25s ease; }
.disk-meter__fill.is-low { background: var(--el-color-warning); }

/* Segmented capacity bar */
.breakdown { margin-top: 16px; }
.bar-track { display: flex; gap: 2px; height: 12px; border-radius: 999px; overflow: hidden; background: var(--el-fill-color-light); padding: 1px; }
.bar-segment { height: 100%; min-width: 6px; border-radius: 999px; transition: opacity 0.15s ease, transform 0.15s ease; }
.bar-segment.is-interactive { cursor: pointer; }
.bar-segment.is-interactive:hover { opacity: 0.85; }
.bar-empty { width: 100%; height: 100%; }
.legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.legend-chip { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 999px; border: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color); font: inherit; font-size: 12px; color: var(--el-text-color-regular); cursor: default; }
.legend-chip.is-clickable { cursor: pointer; }
.legend-chip.is-clickable:hover { border-color: var(--el-color-primary-light-5); color: var(--el-text-color-primary); }
.legend-chip.is-active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.legend-name { font-weight: 500; }
.legend-size { font-variant-numeric: tabular-nums; color: var(--el-text-color-secondary); }
.legend-tag { font-size: 11px; padding: 1px 6px; border-radius: 999px; background: var(--el-fill-color-light); color: var(--el-text-color-secondary); }
.scan-note { margin-top: 8px; font-size: 12px; }

/* Workspace tabs & toolbar */
.workspace-tabs { display: flex; gap: 8px; flex-wrap: wrap; border-bottom: 1px solid var(--el-border-color-lighter); padding-bottom: 12px; }
.tab-pill { display: inline-flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px; border: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-lighter); font: inherit; font-size: 13px; font-weight: 500; color: var(--el-text-color-regular); cursor: pointer; transition: all 0.15s ease; }
.tab-pill:hover { border-color: var(--el-color-primary-light-5); color: var(--el-text-color-primary); }
.tab-pill.is-active { background: var(--el-color-primary); border-color: var(--el-color-primary); color: #fff; }
.pill-badge { font-size: 11px; font-variant-numeric: tabular-nums; padding: 1px 7px; border-radius: 999px; background: rgba(0, 0, 0, 0.08); }
.tab-pill.is-active .pill-badge { background: rgba(255, 255, 255, 0.22); color: #fff; }

.tab-body { padding-top: 12px; }
.section-hint { margin: 0 0 12px; }

.toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.filter-group { display: flex; flex-wrap: wrap; gap: 6px; }
.filter-chip { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color); font: inherit; font-size: 12px; color: var(--el-text-color-regular); cursor: pointer; }
.filter-chip.is-active { border-color: var(--el-color-primary); background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-weight: 500; }
.chip-count { font-variant-numeric: tabular-nums; opacity: 0.75; }
.sort-select { width: 150px; }

.bulk-bar { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; padding: 8px 12px; border-radius: 8px; background: var(--el-fill-color-lighter); margin-bottom: 10px; }
.bulk-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* Item list */
.item-list { display: flex; flex-direction: column; gap: 8px; }
.item-row { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--el-border-color-lighter); background: var(--el-bg-color); transition: border-color 0.15s ease; }
.item-row:hover { border-color: var(--el-border-color); }
.item-row--muted { background: var(--el-fill-color-lighter); }
.item-check { margin-top: 2px; }
.item-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.item-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.item-title { font-size: 13px; font-weight: 600; color: var(--el-text-color-primary); overflow-wrap: anywhere; }
.item-meta { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: var(--el-text-color-secondary); }
.meta-warn { color: var(--el-color-warning-dark-2, var(--el-color-warning)); }
.item-path { font-family: var(--el-font-family-mono, Consolas, Menlo, monospace); font-size: 11px; color: var(--el-text-color-secondary); overflow-wrap: anywhere; }
.item-aside { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; flex-shrink: 0; }
.item-size { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.item-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.item-actions .el-button + .el-button { margin-left: 0; }
.in-use-label { font-size: 12px; color: var(--el-color-success); padding: 2px 8px; border-radius: 999px; background: var(--el-color-success-light-9); }

.empty-state { padding: 28px 16px; text-align: center; color: var(--el-text-color-secondary); font-size: 13px; border: 1px dashed var(--el-border-color); border-radius: 10px; }
.more-note { margin-top: 10px; font-size: 12px; }

/* Transcript cards */
.transcript-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.sub-card { padding: 14px 16px; border-radius: 10px; border: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-lighter); display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.sub-card__head { width: 100%; display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.sub-card__head h3 { margin: 0; font-size: 14px; font-weight: 600; }
.sub-card__size { font-size: 15px; font-variant-numeric: tabular-nums; }

/* Auto-cleanup policy */
.policy-rows { display: flex; flex-direction: column; gap: 12px; margin-top: 14px; }
.policy-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--el-border-color-lighter); background: var(--el-fill-color-lighter); }
.policy-info { flex: 1; min-width: 240px; }
.policy-title-line { display: flex; align-items: center; gap: 10px; font-size: 14px; }
.policy-control { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.policy-footnote { margin-top: 12px; font-size: 12px; }
.policy-saved { margin: 8px 0 0; font-size: 12px; color: var(--el-color-success); }

/* Places table */
.rows { width: 100%; border-collapse: collapse; margin-top: 8px; }
.rows th, .rows td { text-align: left; padding: 10px 8px; border-top: 1px solid var(--el-border-color-lighter); vertical-align: middle; }
.rows th { border-top: 0; font-weight: 500; font-size: 13px; color: var(--el-text-color-secondary); }
.path { font-family: var(--el-font-family-mono, monospace); font-size: 12px; overflow-wrap: anywhere; }
.act { text-align: right; white-space: nowrap; }

/* Confirm dialog */
.confirm-desc { margin: 0 0 10px; font-size: 14px; line-height: 1.6; }
.confirm-warn { margin: 0 0 10px; padding: 8px 12px; border-radius: 8px; background: var(--el-color-warning-light-9); border: 1px solid var(--el-color-warning-light-5); color: var(--el-color-warning-dark-2, var(--el-color-warning)); font-size: 13px; line-height: 1.5; }
.confirm-list { margin: 8px 0; padding: 0; list-style: none; max-height: 220px; overflow-y: auto; border: 1px solid var(--el-border-color-lighter); border-radius: 8px; }
.confirm-list li { padding: 8px 10px; border-top: 1px solid var(--el-border-color-lighter); }
.confirm-list li:first-child { border-top: 0; }
.confirm-list__row { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; }
.confirm-list__path { font-family: var(--el-font-family-mono, monospace); font-size: 11px; color: var(--el-text-color-secondary); overflow-wrap: anywhere; margin-top: 2px; }

@media (max-width: 760px) {
  .kpi-grid { grid-template-columns: 1fr; }
  .transcript-grid { grid-template-columns: 1fr; }
  .item-row { flex-direction: column; }
  .item-aside { width: 100%; flex-direction: row; align-items: center; justify-content: space-between; }
}
</style>
