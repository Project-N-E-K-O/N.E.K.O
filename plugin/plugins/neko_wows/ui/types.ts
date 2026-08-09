export type Translate = (
  source: string,
  params?: Record<string, any>
) => string

export type ServiceState = {
  mode?: string
  reachable?: boolean
  ours?: boolean
  service_id?: string
  api_version?: string
  instance_id?: string
  source_status?: string
  error?: string
  pid?: number | null
  paused?: boolean
  detail?: string
  crash_count?: number
}

export type TransportState = {
  mode?: string
  epoch?: number
  ws_connects?: number
  ws_failures?: number
  rest_polls?: number
  rest_failures?: number
  frames_emitted?: number
  /** Wall-clock seconds, ready for `formatClock`. */
  last_frame_at?: number | null
  last_error?: string
  reconnect_delay?: number
}

export type CursorState = {
  epoch?: number
  instance_id?: string | null
  seq?: number
  accepted?: number
  dropped?: Record<string, number>
}

export type SnapshotState = {
  instance_id?: string
  seq?: number
  battle_id?: string | null
  status?: string
  legacy?: boolean
  api_version?: string
  transport?: string
  active?: boolean
  battle_type?: string | null
  game_mode?: string | null
  map_name?: string | null
  availability?: Record<string, string>
  unsupported?: string[]
  own_hp_ratio?: number | null
  allies_alive?: number | null
  enemies_alive?: number | null
  nearest_enemy_m?: number | null
}

export type TimelineEntry = {
  at?: number
  stage?: string
  outcome?: string
  seq?: number | null
  battle_id?: string | null
  event_id?: string
  reason?: string
  detail?: Record<string, any>
}

export type ArbiterState = {
  queued?: number
  paused?: boolean
  cooldowns?: number
  fired_once_per_battle?: string[]
  lanes?: Record<string, number>
  quiet_until?: number
  intrusion_mode?: string
}

export type DispatcherState = {
  paused?: boolean
  pause_reason?: string
  recent_failures?: number
  failure_limit?: number
  host_calls?: number
  delivered?: number
  suppressed?: number
  dry_run?: boolean
}

export type ShipCatalogState = {
  enabled?: boolean
  state?: string
  active_catalog_version?: string
  frozen_catalog_version?: string
  catalog_game_version?: string
  client_game_version?: string
  version_status?: string
  source_commit?: string
  schema_version?: number | null
  observed_objects?: number
  resolved_ship_types?: number
  unresolved_objects?: number
  pending_ship_types?: number
  submitted_ship_types?: number
  unresolved_reasons?: Record<string, number>
  last_error?: string
  official_tool?: {
    enabled?: boolean
    region?: string
    key_configured?: boolean
    cache_entries?: number
    cache_hits?: number
    cache_misses?: number
  }
}

export type ShotSummary = {
  shot_id?: string
  /** Wall-clock seconds, ready for `formatClock`. */
  captured_at?: number
  size_bytes?: number
}

export type ScreenshotState = {
  enabled?: boolean
  min_interval_seconds?: number
  retain_count?: number
  cooldown_remaining_seconds?: number
  recent?: ShotSummary[]
}

export type WowsConfigView = {
  dry_run?: boolean
  channel_mode?: string
  service_url?: string
  service_source_dir?: string
  game_dir?: string
  urgent_ttl_seconds?: number
  urgent_min_gap_seconds?: number
  normal_ttl_seconds?: number
  normal_min_gap_seconds?: number
  dialogue_intrusion_mode?: string
  user_chat_quiet_window_seconds?: number
  disabled_categories?: string[]
  disabled_lanes?: string[]
}

export type DocumentItem = {
  doc_id?: string
  title?: string
  size_bytes?: number
  chunk_count?: number
  indexed_chunks?: number
  imported_at?: number
  tags?: string[]
}

export type SearchHit = {
  doc_id?: string
  title?: string
  score?: number
  tag_hits?: number
  term_hits?: number
}

export type LastSearch = {
  query_text?: string
  tags_used?: string[]
  tag_candidates?: number
  term_candidates?: number
  best_term_hits?: number
  scored?: number
  gated?: boolean
  gate_reason?: string
  hits?: SearchHit[]
}

export type DocumentsState = {
  available?: boolean
  error?: string
  index_truncated?: boolean
  quotas?: {
    max_documents?: number
    max_total_bytes?: number
    max_file_bytes?: number
    index_chunk_cap?: number
    chunk_chars?: number
    chunk_overlap?: number
    min_term_hits?: number
    tag_weight?: number
  }
  stats?: {
    documents?: number
    total_bytes?: number
    chunks?: number
    indexed_chunks?: number
    postings?: number
    total_tokens?: number
  }
  items?: DocumentItem[]
  last_search?: LastSearch
}

export type PromptRevisionSummary = {
  revision_id?: string
  created_at?: number
  active?: boolean
  note?: string
  lengths?: { base?: number; urgent?: number; normal?: number }
}

export type PromptsState = {
  active_revision?: string
  is_builtin?: boolean
  sections?: { base?: string; urgent?: string; normal?: string }
  max_section_chars?: number
  revisions_kept?: number
  revisions?: PromptRevisionSummary[]
}

export type DashboardState = {
  running?: boolean
  runtime_now?: number
  config?: WowsConfigView
  reconnect_required?: boolean
  service?: ServiceState
  transport?: TransportState
  cursor?: CursorState
  snapshot?: SnapshotState
  counters?: { frames?: number; events?: number }
  arbiter?: ArbiterState
  dispatcher?: DispatcherState
  context_injected?: boolean
  screenshot?: ScreenshotState
  ship_catalog?: ShipCatalogState
  documents?: DocumentsState
  prompts?: PromptsState
  categories?: string[]
  lanes?: string[]
  timeline?: TimelineEntry[]
  mod_hint?: string
}
