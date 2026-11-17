<template>
  <ul v-if="messages.length" class="market-messages">
    <li v-for="message in messages" :key="message.id" class="market-message">
      <el-avatar :size="30" :src="avatarFor(message)">
        {{ authorName(message).slice(0, 1) }}
      </el-avatar>
      <div class="market-message__content">
        <div class="market-message__meta">
          <strong>{{ authorName(message) }}</strong>
          <time :datetime="message.created_at">{{ formatTime(message.created_at) }}</time>
        </div>
        <div v-if="message.reply_to" class="market-message__context">
          {{ t('market.detailCommentReplyTo', { name: authorName(message.reply_to) }) }}
        </div>
        <p>{{ messageText(message) }}</p>
        <el-button
          v-if="canWrite && !message.is_hidden"
          text
          size="small"
          :disabled="sending"
          @click="replyTo = message"
        >
          {{ t('marketFeedback.reply') }}
        </el-button>
      </div>
    </li>
  </ul>
  <el-empty v-else :description="emptyText" :image-size="72" />
  <el-button v-if="hasMore" :loading="loadingMore" @click="emit('more')">
    {{ t('marketFeedback.loadMore') }}
  </el-button>
  <form v-if="canWrite" class="market-message-form" @submit.prevent="submit">
    <div v-if="replyTo" class="market-message__meta">
      <span>{{ t('market.detailCommentReplyTo', { name: authorName(replyTo) }) }}</span>
      <el-button text size="small" :disabled="sending" @click="replyTo = null">{{
        t('common.cancel')
      }}</el-button>
    </div>
    <label>
      {{ t('marketFeedback.message') }}
      <el-input v-model="body" type="textarea" :rows="3" :maxlength="10000" :disabled="sending" />
    </label>
    <el-alert v-if="sendError" :title="t(sendError)" type="error" :closable="false" show-icon />
    <el-button native-type="submit" type="primary" :disabled="!body.trim()" :loading="sending">
      {{ t('marketFeedback.send') }}
    </el-button>
  </form>
  <el-button v-else-if="!session" :loading="marketAuthBusy" @click="startMarketLogin()">
    {{ t('market.login') }}
  </el-button>
  <p v-else-if="requiresEmailVerification">{{ t('marketFeedback.verifyEmail') }}</p>
</template>

<script lang="ts">
import type { MarketPluginComment } from '@/api/market'

interface UnsentDraft {
  body: string
  // Kept whole: the target may sit on a page the reloaded thread has not loaded.
  replyTo: MarketPluginComment | null
  // Set when the reload raced a send of this text; resolves true if it may have posted.
  posted?: Promise<boolean>
}

// Unsent drafts survive the remount that follows a session reload. Keyed by
// thread, never by session: the text was not posted under any account.
const unsentDrafts = new Map<string, UnsentDraft>()
</script>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { fetchCommunityAuthorAvatars } from '@/api/marketCommunityProfiles'
import {
  MarketConversationError,
  isSentAfterAccountChange,
  marketConversationSessionChanged,
  type MarketMessageDraft,
} from '@/api/marketConversations'
import { MARKET_AUTH_CHANGED_EVENT } from '@/api/marketBridge'
import { useMarketAuth } from '@/composables/useMarketAuth'

const props = defineProps<{
  messages: MarketPluginComment[]
  emptyText: string
  session: string | null
  canWrite: boolean
  requiresEmailVerification?: boolean
  hasMore: boolean
  loadingMore: boolean
  send: (draft: MarketMessageDraft) => Promise<void>
  draftKey: string
}>()
const emit = defineEmits<{ more: []; 'session-updated': [] }>()
const { t, locale } = useI18n()
const { marketAuthBusy, startMarketLogin } = useMarketAuth()
const body = ref('')
const replyTo = ref<MarketPluginComment | null>(null)
const sending = ref(false)
const sendError = ref('')
const avatars = ref<Record<string, string>>({})
let lastDraft: MarketMessageDraft | undefined
let inFlight: Promise<boolean> | undefined
let alive = true

async function restoreDraft(draft: UnsentDraft) {
  // Wait for a racing send: restoring text it already posted would invite a duplicate.
  if (draft.posted && (await draft.posted)) return
  if (!alive) {
    unsentDrafts.set(props.draftKey, { body: draft.body, replyTo: draft.replyTo })
    return
  }
  if (body.value.trim()) return
  body.value = draft.body
  replyTo.value = draft.replyTo
}

// A read-only remount (signed out, guest view) leaves the draft for a later writable one.
const stashed = props.canWrite ? unsentDrafts.get(props.draftKey) : undefined
if (stashed) {
  unsentDrafts.delete(props.draftKey)
  void restoreDraft(stashed)
}

/** Ask the parent to reload; the reload remounts this form, so keep what was typed. */
function reloadKeepingDraft() {
  if (body.value.trim()) {
    unsentDrafts.set(props.draftKey, {
      body: body.value,
      replyTo: replyTo.value,
      posted: sending.value ? inFlight : undefined,
    })
  }
  emit('session-updated')
}

/** Only a 4xx proves the Market did not take the write; a lost response might have. */
function mayHavePosted(error: unknown): boolean {
  return !(error instanceof MarketConversationError && error.status >= 400 && error.status < 500)
}

function onAccountChange() {
  reloadKeepingDraft()
}
onMounted(() => window.addEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange))
onBeforeUnmount(() => {
  alive = false
  window.removeEventListener(MARKET_AUTH_CHANGED_EVENT, onAccountChange)
})

const queried = new Set<string>()
watch(
  () => props.messages,
  async (messages, _, onCleanup) => {
    const actors = new Map(
      messages.flatMap((message) => {
        const id = message.author.community_profile_id
        return !message.is_hidden && id && !queried.has(id) ? [[id, message.author] as const] : []
      })
    )
    if (!actors.size) return
    const request = new AbortController()
    onCleanup(() => request.abort())
    const entries = [...actors.values()]
    for (let offset = 0; offset < entries.length && !request.signal.aborted; offset += 100) {
      const batch = entries.slice(offset, offset + 100)
      const values = await fetchCommunityAuthorAvatars(batch, request.signal)
      if (request.signal.aborted) return
      for (const actor of batch) {
        if (actor.community_profile_id) queried.add(actor.community_profile_id)
      }
      Object.assign(avatars.value, values)
    }
  },
  { immediate: true }
)

function authorName(entry: Pick<MarketPluginComment, 'author'>): string {
  return entry.author.display_name || entry.author.username || t('market.unknownAuthor')
}

function messageText(message: MarketPluginComment): string {
  if (message.is_hidden) return t('market.detailCommentHidden')
  return message.body || t('market.detailCommentEmpty')
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value)
}

function avatarFor(message: MarketPluginComment): string {
  if (message.is_hidden) return ''
  const id = message.author.community_profile_id
  return (id && avatars.value[id]) || message.author.avatar_url || ''
}

async function submit() {
  const text = body.value.trim()
  const replyId = replyTo.value?.id ?? null
  if (!text || sending.value || !props.canWrite) return
  if (!lastDraft || lastDraft.body !== text || lastDraft.reply_to_id !== replyId) {
    lastDraft = { body: text, reply_to_id: replyId, client_message_id: crypto.randomUUID() }
  }
  let settle: (posted: boolean) => void = () => {}
  inFlight = new Promise((resolve) => (settle = resolve))
  sending.value = true
  sendError.value = ''
  try {
    await props.send(lastDraft)
    settle(true)
    body.value = ''
    replyTo.value = null
    lastDraft = undefined
  } catch (error) {
    settle(isSentAfterAccountChange(error) || mayHavePosted(error))
    if (isSentAfterAccountChange(error)) {
      // Already posted: drop the draft so the new account is not nudged into a resend.
      body.value = ''
      replyTo.value = null
      lastDraft = undefined
      // A toast outlives this component, which is re-keyed by the reload below.
      ElMessage.warning(t('marketFeedback.sentAccountChanged'))
      if (alive) emit('session-updated')
    } else if (marketConversationSessionChanged(error)) {
      // Nothing was posted. The inline error would vanish with the remount,
      // so say it in a toast and carry the draft over.
      ElMessage.error(t('marketFeedback.notSentSessionChanged'))
      // If an account-change reload already replaced this form, its stash
      // carries this send's outcome and the new form restores the text.
      if (alive) reloadKeepingDraft()
    } else {
      sendError.value =
        error instanceof MarketConversationError && error.code === 'market_email_unverified'
          ? 'marketFeedback.verifyEmail'
          : 'marketFeedback.sendFailed'
    }
  } finally {
    sending.value = false
  }
}
</script>

<style scoped>
.market-messages {
  display: grid;
  gap: 12px;
  list-style: none;
  padding: 0;
}
.market-message {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr);
  gap: 10px;
}
.market-message__content {
  min-width: 0;
}
.market-message__content p {
  margin: 6px 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.market-message__meta {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.market-message__meta time,
.market-message__context {
  font-size: 0.85rem;
  color: var(--el-text-color-secondary);
}
.market-message-form {
  display: grid;
  gap: 8px;
  justify-items: start;
  margin-top: 16px;
}
.market-message-form label {
  display: grid;
  gap: 4px;
  width: 100%;
}
</style>
