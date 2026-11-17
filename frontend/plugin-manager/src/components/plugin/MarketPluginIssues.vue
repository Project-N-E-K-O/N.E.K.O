<template>
  <div v-if="selected === null">
    <LoadingSpinner v-if="loading && !items.length" :loading="true" />
    <el-alert
      v-if="failed"
      :title="t('marketFeedback.loadFailed')"
      type="error"
      :closable="false"
      show-icon
    />
    <el-empty
      v-else-if="!loading && !items.length"
      :description="t('marketFeedback.noIssues')"
      :image-size="72"
    />
    <ul v-if="items.length" class="issue-list">
      <li v-for="issue in items" :key="issue.id">
        <button class="issue-link" type="button" @click="loadIssue(issue.id)">
          <strong>{{ issue.title }}</strong>
          <el-tag size="small" :type="issue.state === 'open' ? 'warning' : 'info'">
            {{
              t(issue.state === 'open' ? 'marketFeedback.issueOpen' : 'marketFeedback.issueClosed')
            }}
          </el-tag>
        </button>
      </li>
    </ul>
    <el-button v-if="next !== null" :loading="loading" @click="loadList(true)">
      {{ t('marketFeedback.loadMore') }}
    </el-button>
    <el-button v-else-if="failed" @click="loadList()">{{ t('common.refresh') }}</el-button>
  </div>
  <div v-else>
    <el-button text @click="goBack">{{ t('marketFeedback.backToIssues') }}</el-button>
    <LoadingSpinner v-if="loadingIssue && !detail" :loading="true" />
    <el-alert
      v-if="detailFailed"
      :title="t('marketFeedback.loadFailed')"
      type="error"
      :closable="false"
      show-icon
    />
    <el-button v-if="detailFailed && !detail" @click="loadIssue(selected)">{{
      t('common.refresh')
    }}</el-button>
    <template v-if="detail">
      <div class="issue-header">
        <h3>{{ detail.title }}</h3>
        <el-tag size="small" :type="detail.state === 'open' ? 'warning' : 'info'">
          {{
            t(detail.state === 'open' ? 'marketFeedback.issueOpen' : 'marketFeedback.issueClosed')
          }}
        </el-tag>
        <el-button
          v-if="detail.capabilities.can_close"
          :loading="changing"
          @click="transition('close')"
          >{{ t('marketFeedback.closeIssue') }}</el-button
        >
        <el-button
          v-if="detail.capabilities.can_reopen"
          :loading="changing"
          @click="transition('reopen')"
          >{{ t('marketFeedback.reopenIssue') }}</el-button
        >
      </div>
      <MarketConversationThread
        :key="`${detail.id}:${detailSession || 'guest'}`"
        :messages="detail.messages"
        :empty-text="t('marketFeedback.empty')"
        :session="detailSession"
        :can-write="!!detailSession && detail.capabilities.can_reply"
        :requires-email-verification="
          !!detailSession && detail.state === 'open' && !detail.capabilities.can_reply
        "
        :has-more="detail.next_message_cursor !== null"
        :loading-more="loadingIssue"
        :send="send"
        @more="loadMessages"
        @session-updated="loadIssue(detail.id)"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import LoadingSpinner from '@/components/common/LoadingSpinner.vue'
import MarketConversationThread from './MarketConversationThread.vue'
import {
  changeMarketIssueState,
  marketConversationSessionChanged,
  readMarketIssue,
  readMarketIssueMessages,
  readMarketIssues,
  sendMarketIssueReply,
  type MarketIssueDetail,
  type MarketIssueSummary,
  type MarketMessageDraft,
} from '@/api/marketConversations'

const props = defineProps<{ pluginId: number | string }>()
const emit = defineEmits<{ 'feedback-changed': [] }>()
const { t } = useI18n()
const items = ref<MarketIssueSummary[]>([])
const next = ref<number | null>(null)
const listSession = ref<string | null>(null)
const loading = ref(false)
const failed = ref(false)
const selected = ref<number | null>(null)
const detail = ref<MarketIssueDetail | null>(null)
const detailSession = ref<string | null>(null)
const loadingIssue = ref(false)
const detailFailed = ref(false)
const changing = ref(false)
let listRequest: AbortController | undefined
let detailRequest: AbortController | undefined

async function loadList(more = false) {
  if (more && (loading.value || next.value === null)) return
  listRequest?.abort()
  const request = new AbortController()
  listRequest = request
  loading.value = true
  failed.value = false
  if (!more) {
    items.value = []
    next.value = null
    listSession.value = null
  }
  try {
    const result = await readMarketIssues(
      props.pluginId,
      request.signal,
      next.value ?? undefined,
      listSession.value
    )
    if (request.signal.aborted) return
    if (more && listSession.value !== result.session) {
      void loadList()
      return
    }
    items.value = [...items.value, ...result.data.items.filter((issue) => !issue.is_hidden)]
    next.value = result.data.next_cursor
    listSession.value = result.session
  } catch (error) {
    if (request.signal.aborted) return
    if (more && marketConversationSessionChanged(error)) void loadList()
    else failed.value = true
  } finally {
    if (listRequest === request) loading.value = false
  }
}

async function loadIssue(issueId: number) {
  selected.value = issueId
  detail.value = null
  detailSession.value = null
  detailFailed.value = false
  loadingIssue.value = true
  detailRequest?.abort()
  const request = new AbortController()
  detailRequest = request
  try {
    const result = await readMarketIssue(props.pluginId, issueId, request.signal)
    if (request.signal.aborted) return
    if (result.data.is_hidden) {
      detailFailed.value = true
      return
    }
    detail.value = result.data
    detailSession.value = result.session
    if (listSession.value === result.session) {
      items.value = items.value.map((item) => (item.id === issueId ? result.data : item))
    }
  } catch {
    if (!request.signal.aborted) detailFailed.value = true
  } finally {
    if (detailRequest === request) loadingIssue.value = false
  }
}

function goBack() {
  detailRequest?.abort()
  selected.value = null
  if (listSession.value !== detailSession.value) void loadList()
}

async function loadMessages() {
  const source = detail.value
  const request = detailRequest
  if (!source || !request || source.next_message_cursor === null || loadingIssue.value) return
  loadingIssue.value = true
  detailFailed.value = false
  try {
    const result = await readMarketIssueMessages(
      props.pluginId,
      source.id,
      source.next_message_cursor,
      request.signal,
      detailSession.value
    )
    if (request.signal.aborted || !detail.value) return
    if (result.session !== detailSession.value) {
      void loadIssue(source.id)
      return
    }
    const messages = new Map(detail.value.messages.map((message) => [message.id, message]))
    for (const message of result.data.items) messages.set(message.id, message)
    detail.value = {
      ...detail.value,
      messages: [...messages.values()].sort((left, right) => left.id - right.id),
      next_message_cursor: result.data.next_cursor,
    }
  } catch (error) {
    if (request.signal.aborted) return
    if (marketConversationSessionChanged(error)) void loadIssue(source.id)
    else detailFailed.value = true
  } finally {
    if (detailRequest === request) loadingIssue.value = false
  }
}

async function send(draft: MarketMessageDraft) {
  const source = detail.value
  const session = detailSession.value
  const request = detailRequest
  if (!source?.capabilities.can_reply || !session || !request) return
  const message = await sendMarketIssueReply(props.pluginId, source.id, session, draft)
  if (request.signal.aborted || !detail.value) return
  if (!detail.value.messages.some((existing) => existing.id === message.id)) {
    detail.value = { ...detail.value, messages: [...detail.value.messages, message] }
  }
  emit('feedback-changed')
}

async function transition(action: 'close' | 'reopen') {
  const source = detail.value
  const session = detailSession.value
  const request = detailRequest
  if (!source || !session || !request || changing.value) return
  changing.value = true
  try {
    const updated = await changeMarketIssueState(props.pluginId, source.id, session, action)
    if (!request.signal.aborted) {
      detail.value = updated
      items.value = items.value.map((item) => (item.id === updated.id ? updated : item))
      emit('feedback-changed')
    }
  } catch (error) {
    if (!request.signal.aborted) {
      if (marketConversationSessionChanged(error)) void loadIssue(source.id)
      else ElMessage.error(t('marketFeedback.updateIssueFailed'))
    }
  } finally {
    changing.value = false
  }
}

onMounted(() => void loadList())
onBeforeUnmount(() => {
  listRequest?.abort()
  detailRequest?.abort()
})
</script>

<style scoped>
.issue-list {
  margin: 8px 0 16px;
  padding: 0;
  list-style: none;
}
.issue-list li {
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.issue-link {
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  width: 100%;
  text-align: start;
  padding: 12px 6px;
  border: 0;
  background: transparent;
  color: var(--el-text-color-primary);
}
.issue-link:hover {
  background: var(--el-fill-color-light);
}
.issue-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 16px;
}
.issue-header h3 {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
