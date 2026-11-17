<template>
  <el-card class="market-feedback">
    <template #header>
      <header class="feedback-header">
        <div class="feedback-heading">
          <el-button :icon="ArrowLeft" @click="router.push('/workbench')">
            {{ t('authoring.title') }}
          </el-button>
          <h2>{{ t('marketFeedback.title') }}</h2>
        </div>
        <el-button :icon="Refresh" :loading="loading" @click="refresh()">
          {{ t('common.refresh') }}
        </el-button>
      </header>
    </template>

    <div v-if="state === 'login'" class="feedback-state">
      <p>{{ t('marketFeedback.loginRequired') }}</p>
      <el-button type="primary" :loading="marketAuthBusy" @click="startMarketLogin()">
        {{ t('market.login') }}
      </el-button>
    </div>
    <el-alert
      v-else-if="state === 'pending'"
      :title="t('market.authVerificationPending')"
      type="info"
      :closable="false"
      show-icon
    />
    <el-alert
      v-else-if="state === 'error'"
      :title="t('marketFeedback.loadFailed')"
      type="error"
      :closable="false"
      show-icon
    />
    <template v-else>
      <div v-if="page" class="feedback-toolbar">
        <div class="feedback-counts">
          <span
            >{{ t('marketFeedback.unread') }} <strong>{{ page.unread_total }}</strong></span
          >
          <span
            >{{ t('marketFeedback.awaitingReply') }}
            <strong>{{ page.awaiting_reply_total }}</strong></span
          >
        </div>
        <el-button :loading="marking" @click="markRead('all')">
          {{ t('marketFeedback.markAllRead') }}
        </el-button>
      </div>
      <div v-if="state === 'ready'" class="feedback-filters">
        <el-select v-model="kind" :aria-label="t('marketFeedback.kind')">
          <el-option value="all" :label="t('marketFeedback.allKinds')" />
          <el-option value="comment" :label="t('marketFeedback.comment')" />
          <el-option value="issue" :label="t('marketFeedback.issue')" />
        </el-select>
        <el-select v-model="status" :aria-label="t('marketFeedback.status')">
          <el-option value="all" :label="t('marketFeedback.allStatuses')" />
          <el-option value="unread" :label="t('marketFeedback.unread')" />
          <el-option value="awaiting_reply" :label="t('marketFeedback.awaitingReply')" />
        </el-select>
      </div>
      <LoadingSpinner v-if="!page" :loading="loading" />
      <template v-else>
        <el-alert
          v-if="appendFailed"
          :title="t('marketFeedback.loadFailed')"
          type="error"
          :closable="false"
          show-icon
        />
        <el-empty v-if="!groups.length" :description="t('marketFeedback.empty')" />
        <section v-for="group in groups" :key="group.pluginId" class="feedback-group">
          <h3>
            {{ group.name }}
            <el-tag v-if="group.awaiting" size="small" type="warning" round>
              {{ t('marketFeedback.awaitingReply') }} {{ group.awaiting }}
            </el-tag>
            <el-tag v-if="!group.listed" size="small" type="info">
              {{ t('marketFeedback.unlisted') }}
            </el-tag>
          </h3>
          <ul class="feedback-items">
            <li
              v-for="item in group.items"
              :key="`${item.kind}:${item.thread_id}`"
              class="feedback-item"
            >
              <el-avatar :size="36" :src="avatarFor(item)">
                {{ authorName(item).slice(0, 1) }}
              </el-avatar>
              <div class="feedback-item-body">
                <div class="feedback-item-meta">
                  <strong>{{ item.issue_title || t('marketFeedback.comment') }}</strong>
                  <el-tag v-if="item.unread" size="small">{{ t('marketFeedback.unread') }}</el-tag>
                  <el-tag
                    v-if="item.awaiting_reply && item.plugin_listed"
                    size="small"
                    type="warning"
                  >
                    {{ t('marketFeedback.awaitingReply') }}
                  </el-tag>
                </div>
                <div class="feedback-item-time">
                  {{ authorName(item) }} ·
                  <time :datetime="item.created_at">{{ formatDate(item.created_at) }}</time>
                </div>
                <p>{{ item.body }}</p>
                <div class="feedback-actions">
                  <el-button
                    v-if="marketFeedbackLink(page.marketWebUrl, item)"
                    tag="a"
                    :href="marketFeedbackLink(page.marketWebUrl, item)"
                    target="_blank"
                    rel="noopener noreferrer"
                    size="small"
                    @click="
                      handleExternalLinkClick($event, marketFeedbackLink(page.marketWebUrl, item))
                    "
                    @auxclick.middle="
                      handleExternalLinkClick($event, marketFeedbackLink(page.marketWebUrl, item))
                    "
                    >{{ t('marketFeedback.open') }}</el-button
                  >
                  <el-button
                    v-if="item.unread"
                    size="small"
                    :disabled="marking"
                    @click="markRead(item)"
                  >
                    {{ t('marketFeedback.markRead') }}
                  </el-button>
                </div>
              </div>
            </li>
          </ul>
        </section>
        <el-button v-if="page.next_cursor" :loading="loading" @click="loadPage(true)">
          {{ t('marketFeedback.loadMore') }}
        </el-button>
      </template>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import { useMarketAuth } from '@/composables/useMarketAuth'
import { handleExternalLinkClick } from '@/utils/openExternal'
import { fetchCommunityAuthorAvatars } from '@/api/marketCommunityProfiles'
import { MARKET_AUTH_CHANGED_EVENT } from '@/api/marketBridge'
import {
  FeedbackRequestError,
  fetchMarketFeedback,
  marketFeedbackLink,
  markMarketFeedbackRead,
  type FeedbackItem,
  type FeedbackKind,
  type FeedbackPage,
  type FeedbackStatus,
} from '@/api/marketFeedback'

const router = useRouter()
const { t, locale } = useI18n()
const { marketAuth, marketAuthBusy, startMarketLogin, loadMarketAuthStatus } = useMarketAuth()
const page = ref<FeedbackPage | null>(null)
const state = ref<'loading' | 'ready' | 'login' | 'pending' | 'error'>('loading')
const kind = ref<FeedbackKind | 'all'>('all')
const status = ref<FeedbackStatus>('all')
const loading = ref(false)
const marking = ref(false)
const appendFailed = ref(false)
const avatars = ref<Record<string, string>>({})
let activePage: AbortController | undefined
let loadedPages = 0
let mounted = false

const groups = computed(() => {
  const counts = new Map(
    page.value?.by_plugin.map((row) => [row.plugin_id, row.awaiting_reply_total])
  )
  const byPlugin = new Map<
    number,
    {
      pluginId: number
      name: string
      listed: boolean
      awaiting: number
      items: FeedbackItem[]
    }
  >()
  for (const item of page.value?.items ?? []) {
    let group = byPlugin.get(item.plugin_id)
    if (!group) {
      group = {
        pluginId: item.plugin_id,
        name: item.plugin_name,
        listed: item.plugin_listed,
        awaiting: counts.get(item.plugin_id) ?? 0,
        items: [],
      }
      byPlugin.set(item.plugin_id, group)
    }
    group.items.push(item)
  }
  return [...byPlugin.values()]
})

function authorName(item: FeedbackItem): string {
  return item.author.display_name || item.author.username
}

function avatarFor(item: FeedbackItem): string {
  const id = item.author.community_profile_id
  return (id && avatars.value[id]) || item.author.avatar_url || ''
}

const dateFormat = computed(
  () => new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' })
)

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : dateFormat.value.format(date)
}

function hasChangedSession(error: unknown): boolean {
  return error instanceof FeedbackRequestError && error.code === 'market_feedback_session_changed'
}

function feedbackKey(item: FeedbackItem): string {
  return `${item.kind}:${item.thread_id}`
}

// The Market caps one feedback page at 100 items.
const MARKET_FEEDBACK_PAGE_MAX = 100

/**
 * Re-read the list from the top, as deep as the author had scrolled, under the
 * current filters. Re-reading rather than merging into the old pages drops
 * threads that no longer match (read under "unread", answered under "awaiting").
 */
async function readThrough(count: number, signal: AbortSignal): Promise<FeedbackPage> {
  const filters = { kind: kind.value, status: status.value }
  let result = await fetchMarketFeedback(
    { ...filters, limit: Math.min(MARKET_FEEDBACK_PAGE_MAX, count) },
    signal
  )
  const items = [...result.items]
  while (items.length < count && result.next_cursor) {
    result = await fetchMarketFeedback(
      {
        ...filters,
        cursor: result.next_cursor,
        limit: Math.min(MARKET_FEEDBACK_PAGE_MAX, count - items.length),
      },
      signal,
      result.session
    )
    items.push(...result.items)
  }
  return { ...result, items }
}

/**
 * ``quiet`` refreshes in place (window focus, after marking read): the list,
 * loaded pages and avatars stay on screen. A full load (filters, account
 * change, the refresh button) starts over from the first page.
 */
async function loadPage(append = false, quiet = false): Promise<void> {
  const previous = page.value
  if (append && (!previous?.next_cursor || loading.value)) return
  const keepLoaded = quiet && !append && !!previous && state.value === 'ready'
  activePage?.abort()
  const request = new AbortController()
  activePage = request
  appendFailed.value = false
  loading.value = true
  if (!append && !keepLoaded) {
    page.value = null
    avatars.value = {}
    if (state.value !== 'ready') state.value = 'loading'
  }
  // An emptied list has nothing to re-read in depth (and limit=0 is not a valid page).
  const deep = keepLoaded && !!previous?.items.length && loadedPages > 1
  try {
    const result =
      deep && previous
        ? await readThrough(previous.items.length, request.signal)
        : await fetchMarketFeedback(
            {
              kind: kind.value,
              status: status.value,
              cursor: append ? (previous?.next_cursor ?? undefined) : undefined,
            },
            request.signal,
            append ? previous?.session : undefined
          )
    if (request.signal.aborted) return
    if (append && previous?.session !== result.session) {
      void loadPage()
      return
    }
    if (keepLoaded && previous?.session !== result.session) avatars.value = {}
    const items = new Map<string, FeedbackItem>()
    for (const item of [...(append ? (previous?.items ?? []) : []), ...result.items]) {
      items.set(feedbackKey(item), item)
    }
    page.value = { ...result, items: [...items.values()] }
    if (append) loadedPages += 1
    else if (!deep) loadedPages = 1
    state.value = 'ready'
    void fetchCommunityAuthorAvatars(
      result.items.map((item) => item.author),
      request.signal
    ).then((result) => {
      if (!request.signal.aborted) Object.assign(avatars.value, result)
    })
  } catch (error) {
    if (request.signal.aborted) return
    if ((append || deep) && hasChangedSession(error)) {
      void loadPage()
      return
    }
    if (
      error instanceof FeedbackRequestError &&
      (error.status === 401 || error.code === 'market_auth_required')
    ) {
      page.value = null
      avatars.value = {}
      state.value = 'login'
    } else if (error instanceof FeedbackRequestError && error.code === 'market_auth_pending') {
      page.value = null
      avatars.value = {}
      state.value = 'pending'
    } else if (append) {
      appendFailed.value = true
    } else if (!keepLoaded) {
      // A failed quiet refresh falls through and keeps the list already on screen.
      state.value = 'error'
    }
  } finally {
    if (activePage === request) loading.value = false
  }
}

async function markRead(target: FeedbackItem | 'all'): Promise<void> {
  const session = page.value?.session
  if (!session || marking.value) return
  marking.value = true
  try {
    await markMarketFeedbackRead(target, session)
    const current = page.value
    if (mounted && current?.session === session) {
      // Apply the write locally first, so the list is right even if the quiet
      // refresh below fails: under "unread" a read item leaves the list.
      // A single mark only covers messages up to the one shown; a newer message
      // in the same thread (loaded by a refresh meanwhile) is still unread.
      const marked = (item: FeedbackItem) =>
        target === 'all' ||
        (feedbackKey(item) === feedbackKey(target) && item.message_id <= target.message_id)
      const newlyRead = current.items.filter((item) => marked(item) && item.unread).length
      page.value = {
        ...current,
        unread_total: target === 'all' ? 0 : Math.max(0, current.unread_total - newlyRead),
        items:
          status.value === 'unread'
            ? current.items.filter((item) => !marked(item))
            : current.items.map((item) => (marked(item) ? { ...item, unread: false } : item)),
      }
      await loadPage(false, true)
    } else if (mounted && !current) {
      await loadPage()
    }
  } catch (error) {
    if (!mounted) return
    if (
      hasChangedSession(error) ||
      (error instanceof FeedbackRequestError && error.status === 401)
    ) {
      await loadPage()
    } else {
      ElMessage.error(t('marketFeedback.markFailed'))
    }
  } finally {
    marking.value = false
  }
}

async function refresh(quiet = false) {
  if (state.value === 'pending') await loadMarketAuthStatus()
  if (mounted) await loadPage(false, quiet)
}

// Coming back from the Market in a browser must not throw away loaded pages.
function onFocus() {
  // Do not abort a "load more" (or any load) the author is waiting on.
  if (loading.value) return
  void refresh(true)
}

watch([kind, status], () => void loadPage())
watch(
  () => marketAuth.value.authenticated,
  (authenticated) => {
    if (authenticated) void loadPage()
  }
)
// A direct switch from one signed-in account to another keeps
// ``authenticated`` true, so the watch above cannot see it.
function onAccountChange() {
  if (mounted) void loadPage()
}
onMounted(() => {
  mounted = true
  window.addEventListener('focus', onFocus)
  window.addEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange)
  void loadPage()
})
onBeforeUnmount(() => {
  mounted = false
  activePage?.abort()
  window.removeEventListener('focus', onFocus)
  window.removeEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange)
})
</script>

<style scoped>
.feedback-header,
.feedback-heading,
.feedback-toolbar,
.feedback-counts,
.feedback-filters,
.feedback-item-meta,
.feedback-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.feedback-header,
.feedback-toolbar {
  justify-content: space-between;
}

.feedback-toolbar,
.feedback-filters {
  margin-bottom: 16px;
}

.feedback-filters > * {
  width: 180px;
}

.feedback-heading h2 {
  margin: 0;
}

.feedback-state {
  text-align: center;
  padding: 24px;
}

.feedback-group {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-card);
  margin-bottom: 16px;
}

.feedback-group h3 {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 0;
  padding: 14px 16px;
  background: var(--el-fill-color-lighter);
  border-radius: inherit;
}

.feedback-items {
  list-style: none;
  margin: 0;
  padding: 0;
}

.feedback-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
  padding: 14px 16px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.feedback-item-time {
  margin-top: 4px;
  color: var(--el-text-color-secondary);
  font-size: 0.875rem;
}

.feedback-item-body {
  overflow-wrap: anywhere;
}

.feedback-item-body p {
  margin: 8px 0;
  white-space: pre-wrap;
}

.feedback-actions :deep(.el-button) {
  margin: 0;
}
</style>
