export type RoastConfig = {
  live_platform?: string
  live_room_ref?: string
  live_room_id?: number
  live_enabled?: boolean
  avatar_roast_enabled?: boolean
  avatar_analysis_enabled?: boolean
  danmaku_response_enabled?: boolean
  live_support_events_enabled?: boolean
  warmup_hosting_enabled?: boolean
  idle_hosting_enabled?: boolean
  active_engagement_enabled?: boolean
  developer_tools_enabled?: boolean
  live_mode?: string
  activity_level?: string
  roast_strength?: string
  roast_once_per_uid?: boolean
  rate_limit_seconds?: number
  queue_limit?: number
  safety_auto_stop_enabled?: boolean
  dry_run?: boolean
  viewer_store_dir?: string
  stream_theme?: string
  stream_goal?: string
  stream_columns?: string
  stream_avoid_topics?: string
}

export type DashboardState = {
  config?: RoastConfig
  live_connection?: Record<string, any>
  store_enabled?: boolean
  viewer_store?: Record<string, any>
  safety?: Record<string, any>
  live_status?: Record<string, any>
  live_state?: Record<string, any>
  live_director_status?: Record<string, any>
  solo_test_readiness?: Record<string, any>
  modules?: Array<Record<string, any>>
  recent_profiles?: Array<Record<string, any>>
  recent_results?: Array<Record<string, any>>
  live_session?: Record<string, any>
  recent_sandbox_results?: Array<Record<string, any>>
  recent_audit?: Array<Record<string, any>>
  speech_explanation?: Record<string, any>
  live_explain?: Record<string, any>
  idle_hosting_status?: Record<string, any>
  active_engagement_status?: Record<string, any>
  health_rows?: Array<Record<string, any>>
}

export const configDefaults = {
  live_platform: "bilibili",
  live_room_ref: "",
  live_room_id: "0",
  douyin_cookie: "",
  douyin_uid: "",
  douyin_nickname: "",
  live_enabled: false,
  avatar_roast_enabled: true,
  avatar_analysis_enabled: true,
  danmaku_response_enabled: true,
  live_support_events_enabled: true,
  warmup_hosting_enabled: true,
  idle_hosting_enabled: true,
  active_engagement_enabled: true,
  developer_tools_enabled: false,
  live_mode: "co_stream",
  activity_level: "standard",
  roast_strength: "normal",
  roast_once_per_uid: true,
  rate_limit_seconds: "20",
  queue_limit: "5",
  safety_auto_stop_enabled: true,
  dry_run: false,
  viewer_store_dir: "",
  stream_theme: "",
  stream_goal: "",
  stream_columns: "",
  stream_avoid_topics: "",
}

export const sandboxDefaults = {
  target: "",
  uid: "",
  nickname: "",
  avatar_url: "",
  danmaku_text: "",
}

export const presetViewer = {
  uid: "9000000000000001",
  nickname: "Demo viewer",
  danmaku_text: "First time here, can you roast my avatar?",
}
