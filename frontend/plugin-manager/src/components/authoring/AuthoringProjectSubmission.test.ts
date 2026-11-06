// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, markRaw, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import AuthoringProjectSubmission from './AuthoringProjectSubmission.vue'
import { authoringMessages } from '@/i18n/authoring'
import {
  MarketSubmissionError,
  type MarketSubmission,
  type RepositoryPreview,
  type SubmissionPanelState,
} from '@/api/marketSubmissions'

const market = vi.hoisted(() => ({
  listMarketSubmissions: vi.fn(),
  previewSubmissionRepository: vi.fn(),
  createSubmissionDraft: vi.fn(),
  updateSubmissionDraft: vi.fn(),
  submitSubmission: vi.fn(),
  withdrawSubmission: vi.fn(),
  listMarketZones: vi.fn(),
  openExternalUrl: vi.fn(),
  authenticated: true,
}))
vi.mock('@/api/marketSubmissions', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/marketSubmissions')>()),
  listMarketZones: market.listMarketZones,
  listMarketSubmissions: market.listMarketSubmissions,
  previewSubmissionRepository: market.previewSubmissionRepository,
  createSubmissionDraft: market.createSubmissionDraft,
  updateSubmissionDraft: market.updateSubmissionDraft,
  submitSubmission: market.submitSubmission,
  withdrawSubmission: market.withdrawSubmission,
}))
vi.mock('@/utils/openExternal', () => ({ openExternalUrl: market.openExternalUrl }))
// The select popper does not open in happy-dom, so the zone select becomes a text input.
const SelectStub = defineComponent({
  props: { modelValue: String, disabled: Boolean, placeholder: String },
  emits: ['update:modelValue'],
  setup(props, { emit, slots }) {
    return () =>
      h('div', { 'data-placeholder': props.placeholder }, [
        h('input', {
          disabled: props.disabled,
          value: props.modelValue,
          onChange: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value),
        }),
        slots.default?.(),
      ])
  },
})
const OptionStub = defineComponent({
  props: { label: String, value: String },
  setup(props) {
    return () => h('span', { 'data-option': props.value }, props.label)
  },
})
vi.mock('@/composables/useMarketAuth', async () => {
  const { computed, ref } = await import('vue')
  return {
    useMarketAuth: () => {
      const marketAuth = ref<{ authenticated: boolean; market_web_url?: string }>({
        authenticated: false,
      })
      return {
        marketAuth,
        marketAuthBusy: ref(false),
        marketAuthDisplayName: computed(() => 'Author'),
        marketAuthStateMessageKey: computed(() => ''),
        loadMarketAuthStatus: async () => {
          marketAuth.value = {
            authenticated: market.authenticated,
            market_web_url: 'https://market.example',
          }
        },
        startMarketLogin: vi.fn(),
        stopMarketAuthPolling: vi.fn(),
      }
    },
  }
})

const en = authoringMessages['en-US']
const NativeMutationObserver = globalThis.MutationObserver
let cleanup: (() => void) | undefined
let states: SubmissionPanelState[] = []

const snapshot = {
  repo_url: 'https://github.com/owner/repo',
  repo_owner: 'owner',
  repo_name: 'repo',
  plugin_name: 'Repo',
  plugin_slug: 'repo_plugin',
  zone_slug: 'game',
  tags: ['工具'],
  submitted_ref: 'main',
  resolved_commit: 'old',
}
const item = (overrides: Partial<MarketSubmission> = {}): MarketSubmission => ({
  id: 5,
  status: 'draft',
  decision: null,
  terminal_kind: null,
  plugin_id: null,
  current_snapshot: snapshot,
  review_progress: null,
  ...overrides,
})
const page = (items: MarketSubmission[]) => ({
  items,
  total: items.length,
  page: 1,
  page_size: 100,
  total_pages: 1,
})
const preview = (overrides: Partial<RepositoryPreview> = {}): RepositoryPreview => ({
  identity_status: 'matched',
  workflow_status: 'available',
  plugin_id: 'repo_plugin',
  plugin_name: 'Repo plugin',
  description: 'Does things',
  resolved_commit: 'new',
  submitted_ref: 'main',
  warnings: [],
  ...overrides,
})

async function flush() {
  for (let i = 0; i < 10; i++) {
    await Promise.resolve()
    await nextTick()
  }
}

async function settle() {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve, 20))
    await flush()
  }
}

async function mount() {
  const container = document.createElement('div')
  document.body.append(container)
  const app = createApp({
    render: () =>
      h(AuthoringProjectSubmission, {
        githubRepository: 'owner/repo',
        onState: (state: SubmissionPanelState) => states.push(state),
      }),
  })
  app.use(ElementPlus)
  app.component('el-select', SelectStub)
  app.component('el-option', OptionStub)
  app.use(
    createI18n({
      legacy: false,
      locale: 'en-US',
      messages: { 'en-US': { authoring: en, common: { cancel: 'Cancel' } } },
    })
  )
  app.mount(container)
  cleanup = () => {
    app.unmount()
    container.remove()
  }
  await flush()
  return container
}

async function click(text: string, { last = false } = {}) {
  const found = [...document.querySelectorAll('button')].filter(
    (element) => element.textContent?.trim() === text
  )
  const button = last ? found.at(-1) : found[0]
  expect(button, `Missing button: ${text}`).toBeTruthy()
  button!.click()
  await flush()
}

const hasButton = (text: string) =>
  [...document.querySelectorAll('button')].some((element) => element.textContent?.trim() === text)

const button = (text: string) =>
  [...document.querySelectorAll('button')].find((element) => element.textContent?.trim() === text)

async function setSelect(placeholder: string, value: string) {
  const input = document.querySelector(`[data-placeholder="${placeholder}"] input`) as HTMLInputElement | null
  expect(input, `Missing select: ${placeholder}`).toBeTruthy()
  input!.value = value
  input!.dispatchEvent(new Event('change'))
  await flush()
}

const tagChip = (label: string) =>
  [...document.querySelectorAll('.el-check-tag')].find((element) => element.textContent?.trim() === label) as
    | HTMLElement
    | undefined

async function clickTag(label: string) {
  const chip = tagChip(label)
  expect(chip, `Missing tag: ${label}`).toBeTruthy()
  chip!.click()
  await flush()
}

async function chooseMetadata(zone = 'game', tagLabels: string[] = [en.submissionTagTool]) {
  await setSelect(en.submissionZonePlaceholder, zone)
  for (const label of tagLabels) await clickTag(label)
}

beforeEach(() => {
  vi.resetAllMocks()
  states = []
  market.authenticated = true
  vi.stubGlobal(
    'MutationObserver',
    class extends NativeMutationObserver {
      constructor(callback: MutationCallback) {
        super(callback)
        markRaw(this)
      }
    }
  )
  market.listMarketSubmissions.mockResolvedValue(page([]))
  market.previewSubmissionRepository.mockResolvedValue(preview())
  market.listMarketZones.mockResolvedValue([
    { id: 'game', name: 'Game' },
    { id: 'tools', name: 'Tools' },
  ])
})

afterEach(() => {
  cleanup?.()
  cleanup = undefined
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

describe('Market submission panel', () => {
  it('reports no phase while signed out and reads nothing', async () => {
    market.authenticated = false
    await mount()
    expect(document.body.textContent).toContain(en.submissionLoginRequired)
    expect(market.listMarketSubmissions).not.toHaveBeenCalled()
    expect(states.at(-1)).toEqual({ phase: null, marketPluginId: null })
  })

  it('reports an approved submission with its Market plugin id', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ status: 'closed', decision: 'approved', plugin_id: 7 })])
    )
    await mount()
    expect(states.at(-1)).toEqual({ phase: 'approved', marketPluginId: 7 })
    expect(document.body.textContent).toContain('Published to the Market as plugin 7.')
  })

  it('ignores submissions for a similarly named repository', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ current_snapshot: { ...snapshot, repo_url: 'https://github.com/owner/repo-extra' } })])
    )
    await mount()
    expect(document.body.textContent).toContain(en.submissionNone)
    expect(states.at(-1)?.phase).toBeNull()
  })

  it('creates a draft from the preview, with the chosen zone and tags', async () => {
    market.previewSubmissionRepository.mockResolvedValue(preview({ plugin_name: 'x'.repeat(150) }))
    market.createSubmissionDraft.mockResolvedValue(item())
    await mount()
    expect(document.body.textContent).toContain('Game')
    await chooseMetadata('tools', [en.submissionTagTool, en.submissionTagGame])
    await click(en.submissionCreateDraft)
    expect(market.createSubmissionDraft).toHaveBeenCalledExactlyOnceWith({
      repo_url: 'https://github.com/owner/repo',
      plugin_name: 'x'.repeat(100),
      plugin_slug: 'repo_plugin',
      zone_slug: 'tools',
      tags: ['工具', '游戏'],
      description: 'Does things',
      short_description: undefined,
      license_name: undefined,
      submitted_ref: 'main',
      resolved_commit: 'new',
    })
    expect(states.at(-1)?.phase).toBe('draft')
    ;([...document.querySelectorAll('a')].find((a) => a.textContent === en.submissionOpenOnMarket) as HTMLElement).click()
    expect(market.openExternalUrl).toHaveBeenCalledWith(
      'https://market.example/#/developer/submissions?submission=5'
    )
  })

  it('keeps create-draft disabled until a zone and a tag are chosen', async () => {
    await mount()
    expect(document.body.textContent).toContain(en.submissionMetadataRequired)
    expect(button(en.submissionCreateDraft)?.disabled).toBe(true)
    await setSelect(en.submissionZonePlaceholder, 'game')
    expect(button(en.submissionCreateDraft)?.disabled).toBe(true)
    await clickTag(en.submissionTagTool)
    expect(button(en.submissionCreateDraft)?.disabled).toBe(false)
    expect(document.body.textContent).not.toContain(en.submissionMetadataRequired)
  })

  it('offers the Market\'s standard tags and stops at five', async () => {
    await mount()
    expect(document.querySelectorAll('.el-check-tag')).toHaveLength(32)
    for (const label of [
      en.submissionTagGame,
      en.submissionTagTool,
      en.submissionTagAi,
      en.submissionTagMusic,
      en.submissionTagData,
      en.submissionTagWeather,
    ]) {
      await clickTag(label)
    }
    expect(document.querySelectorAll('.el-check-tag.is-checked')).toHaveLength(5)
    expect(tagChip(en.submissionTagWeather)?.classList.contains('is-checked')).toBe(false)
    expect(document.body.textContent).toContain('5/5 selected')
    await clickTag(en.submissionTagGame)
    expect(document.querySelectorAll('.el-check-tag.is-checked')).toHaveLength(4)
  })

  it('warns when the Market zones cannot be read', async () => {
    market.listMarketZones.mockRejectedValue(new MarketSubmissionError('market_catalog_unavailable', 502))
    await mount()
    expect(document.body.textContent).toContain(en.submissionZonesLoadFailed)
  })

  it('shows a draft\'s zone and tags and saves a change to them', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item()]))
    market.updateSubmissionDraft.mockResolvedValue(
      item({ current_snapshot: { ...snapshot, zone_slug: 'tools', tags: ['工具', '游戏'] } })
    )
    await mount()
    const zone = document.querySelector(`[data-placeholder="${en.submissionZonePlaceholder}"] input`) as HTMLInputElement
    expect(zone.value).toBe('game')
    expect(tagChip(en.submissionTagTool)?.classList.contains('is-checked')).toBe(true)
    expect(hasButton(en.submissionSaveMetadata)).toBe(false)
    await chooseMetadata('tools', [en.submissionTagGame])
    await click(en.submissionSaveMetadata)
    await settle()
    expect(market.updateSubmissionDraft).toHaveBeenCalledExactlyOnceWith(5, {
      zone_slug: 'tools',
      tags: ['工具', '游戏'],
    })
    expect(hasButton(en.submissionSaveMetadata)).toBe(false)
  })

  it('keeps a non-standard tag a draft already has', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ current_snapshot: { ...snapshot, tags: ['legacy'] } })])
    )
    await mount()
    expect(tagChip('legacy')?.classList.contains('is-checked')).toBe(true)
  })

  it('blocks submitting a new plugin\'s draft that has no zone or tags', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([item({ current_snapshot: { ...snapshot, zone_slug: null, tags: [] } })])
    )
    await mount()
    expect(button(en.submissionSubmitReview)?.disabled).toBe(true)
  })

  it('refuses a draft when the Market finds no plugin ID, and shows its warnings', async () => {
    market.previewSubmissionRepository.mockResolvedValue(
      preview({ plugin_id: null, warnings: ['plugin.toml is missing'], workflow_status: 'missing' })
    )
    await mount()
    await chooseMetadata()
    await click(en.submissionCreateDraft)
    expect(market.createSubmissionDraft).not.toHaveBeenCalled()
    const text = document.body.textContent
    expect(text).toContain(en.submissionNoManifest)
    expect(text).toContain('plugin.toml is missing')
    expect(text).toContain(en.submissionWorkflowMissing)
  })

  it('selects the open submission the Market names on a conflict', async () => {
    market.createSubmissionDraft.mockRejectedValue(
      new MarketSubmissionError('open_submission_exists', 409, 'exists', { submission_id: 9 })
    )
    await mount()
    await chooseMetadata()
    market.listMarketSubmissions.mockResolvedValue(page([item({ id: 4 }), item({ id: 9, status: 'submitted' })]))
    await click(en.submissionCreateDraft)
    expect(document.body.textContent).toContain(en.submissionOpenExists)
    expect(states.at(-1)?.phase).toBe('submitted')
  })

  it.each([
    [new MarketSubmissionError('market_submission_failed', 400, 'repository is private'), `${en.submissionCreateFailed} repository is private`],
    [
      new MarketSubmissionError('plugin_slug_conflict', 409, 'taken', { plugin_slug: 'repo_plugin' }),
      'The plugin ID repo_plugin is already used by another Market plugin.',
    ],
    [new MarketSubmissionError('market_email_unverified', 409, 'x'), en.marketEmailUnverified],
    [new MarketSubmissionError('market_auth_pending', 409), en.marketAuthPending],
  ])('explains a failed draft: %s', async (error, message) => {
    market.createSubmissionDraft.mockRejectedValue(error)
    await mount()
    await chooseMetadata()
    await click(en.submissionCreateDraft)
    expect(document.body.textContent).toContain(message)
  })

  it('points the draft at the current head before submitting it', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item()]))
    market.updateSubmissionDraft.mockResolvedValue(item({ current_snapshot: { ...snapshot, resolved_commit: 'new' } }))
    market.submitSubmission.mockResolvedValue(item({ status: 'submitted' }))
    await mount()
    await click(en.submissionSubmitReview)
    await settle()
    await click(en.submissionSubmitReview, { last: true })
    await settle()
    expect(market.previewSubmissionRepository).toHaveBeenCalledWith({
      repo_url: 'https://github.com/owner/repo',
      submitted_ref: 'main',
    })
    expect(market.updateSubmissionDraft).toHaveBeenCalledWith(5, {
      submitted_ref: 'main',
      resolved_commit: 'new',
    })
    expect(market.updateSubmissionDraft.mock.invocationCallOrder[0]).toBeLessThan(
      market.submitSubmission.mock.invocationCallOrder[0]!
    )
    expect(states.at(-1)?.phase).toBe('submitted')
  })

  it('withdraws only after confirmation, then offers a new draft', async () => {
    market.listMarketSubmissions.mockResolvedValue(page([item({ status: 'in_review' })]))
    market.withdrawSubmission.mockResolvedValue(
      item({ status: 'closed', decision: 'canceled', terminal_kind: 'author_withdrawn' })
    )
    await mount()
    expect(hasButton(en.submissionCreateDraft)).toBe(false)
    await click(en.submissionWithdraw)
    await settle()
    expect(market.withdrawSubmission).not.toHaveBeenCalled()
    await click(en.submissionWithdraw, { last: true })
    await settle()
    expect(market.withdrawSubmission).toHaveBeenCalledWith(5)
    expect(document.body.textContent).toContain(en.submissionStateWithdrawn)
    expect(hasButton(en.submissionCreateDraft)).toBe(true)
  })

  it('shows what the Market asks of the author', async () => {
    market.listMarketSubmissions.mockResolvedValue(
      page([
        item({
          status: 'in_review',
          review_progress: {
            phase: 'author_action',
            next_actor: 'author',
            title: 'Plugin ID conflict',
            description: 'Rename the plugin and resubmit.',
            diagnostic_code: 'slug_conflict',
          },
        }),
      ])
    )
    await mount()
    const text = document.body.textContent
    expect(text).toContain(en.submissionStateAuthorAction)
    expect(text).toContain('Plugin ID conflict')
    expect(text).toContain('Rename the plugin and resubmit.')
    expect(states.at(-1)?.phase).toBe('author_action')
  })
})
