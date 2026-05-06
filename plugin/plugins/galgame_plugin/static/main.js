const PLUGIN_ID = 'galgame_plugin';
const RUNS_URL = '/runs';
const UI_API_BASE = `/plugin/${PLUGIN_ID}/ui-api`;
// rapidocr / dxcam install URLs gone — both are bundled main-program deps
// (see pyproject.toml [dependency-groups] galgame). Only textractor +
// tesseract still need the runtime-install UI.
const TESSERACT_INSTALL_URL = `${UI_API_BASE}/tesseract/install`;
const TEXTRACTOR_INSTALL_URL = `${UI_API_BASE}/textractor/install`;
const INSTALL_TERMINAL_STATUSES = new Set(['completed', 'failed', 'canceled']);
// Only tesseract retains an install tab; rapidocr / dxcam status is now
// shown as plain banners (always visible, no install action).
const OCR_INSTALL_TABS = ['tesseract'];
const FLASH_AUTO_HIDE_MS = 4000;
const SETTINGS_AUTOSAVE_DELAY_MS = 700;
const PLUGIN_RUN_TIMEOUT_MS = 120000;
const PLUGIN_RUN_LIGHT_TIMEOUT_MS = 30000;
const PLUGIN_RUN_INITIAL_POLL_MS = 250;
const PLUGIN_RUN_MAX_POLL_MS = 2000;
const CL_ZOOM_KEY = 'galgame_current_line_zoom';
const CL_COLLAPSED_KEY = 'galgame_current_line_collapsed';
const CL_ZOOM_MIN = 12;
const CL_ZOOM_MAX = 36;
const CL_ZOOM_STEP = 2;
const CL_ZOOM_DEFAULT = 18;
const CL_COLLAPSED_DEFAULT = false;
const PIPELINE_ZOOM_KEY = 'galgame_pipeline_zoom';
const PIPELINE_ZOOM_MIN = 10;
const PIPELINE_ZOOM_MAX = 20;
const PIPELINE_ZOOM_STEP = 1;
const PIPELINE_ZOOM_DEFAULT = 13;
const PIPELINE_COLLAPSED_KEY = 'galgame_pipeline_collapsed';
const OCR_WINDOW_COLLAPSED_KEY = 'galgame_ocr_window_collapsed';

function uiT(key, fallback) {
  return window.I18n && typeof window.I18n.t === 'function'
    ? window.I18n.t(key, fallback)
    : (fallback || key);
}

function uiTf(key, fallback, values = {}) {
  const template = uiT(key, fallback);
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match
  ));
}

function uiDynamicT(prefix, key, fallback) {
  const normalized = String(key || '').trim();
  if (!normalized) {
    return fallback || '';
  }
  return uiT(`${prefix}.${normalized}`, fallback || normalized);
}

function getInstallUIConfig() {
  return {
    tesseract: {
      kind: 'tesseract',
      label: 'Tesseract',
      url: TESSERACT_INSTALL_URL,
      storageKey: `${PLUGIN_ID}:tesseract_install_task_id`,
      domPrefix: 'tesseract',
      actionText: uiT('ui.install.tesseract.action', '一键安装 Tesseract'),
      retryText: uiT('ui.install.tesseract.retry', '重试安装 Tesseract'),
      runningText: uiT('ui.install.running', '后台安装中...'),
      queuedFlash: uiT('ui.install.tesseract.queued', '已创建后台安装任务，接下来会通过 HTTPS 下载 Tesseract 和语言包，并通过 SSE 推送实时进度。'),
      successFlash: uiT('ui.install.tesseract.success', 'Tesseract 安装完成'),
      failureFlash: uiT('ui.install.tesseract.failure', 'Tesseract 安装失败'),
    },
    textractor: {
      kind: 'textractor',
      label: 'Textractor',
      url: TEXTRACTOR_INSTALL_URL,
      storageKey: `${PLUGIN_ID}:textractor_install_task_id`,
      domPrefix: 'textractor',
      actionText: uiT('ui.install.textractor.action', '一键安装 Textractor'),
      retryText: uiT('ui.install.textractor.retry', '重试安装 Textractor'),
      runningText: uiT('ui.install.running', '后台安装中...'),
      queuedFlash: uiT('ui.install.textractor.queued', '已创建后台安装任务，接下来会通过 HTTPS 下载 Textractor，并通过 SSE 推送实时进度。'),
      successFlash: uiT('ui.install.textractor.success', 'Textractor 安装完成'),
      failureFlash: uiT('ui.install.textractor.failure', 'Textractor 安装失败'),
    },
  };
}

function createInstallRuntimeState() {
  return {
    state: null,
    inProgress: false,
    currentTaskId: '',
    eventSource: null,
    reconnectTimer: null,
    handledTerminalKey: '',
  };
}

const installRuntime = {
  rapidocr: createInstallRuntimeState(),
  dxcam: createInstallRuntimeState(),
  tesseract: createInstallRuntimeState(),
  textractor: createInstallRuntimeState(),
};

function readCurrentLineZoom() {
  const raw = parseInt(localStorage.getItem(CL_ZOOM_KEY) || '', 10);
  return Number.isFinite(raw) ? raw : CL_ZOOM_DEFAULT;
}

function readCurrentLineCollapsed() {
  const raw = localStorage.getItem(CL_COLLAPSED_KEY);
  if (raw === '1') { return true; }
  if (raw === '0') { return false; }
  return CL_COLLAPSED_DEFAULT;
}

function applyCurrentLineZoom(px) {
  const clamped = Math.max(CL_ZOOM_MIN, Math.min(CL_ZOOM_MAX, px));
  document.documentElement.style.setProperty('--galgame-line-font-size', `${clamped}px`);
  localStorage.setItem(CL_ZOOM_KEY, String(clamped));
  return clamped;
}

function readPipelineZoom() {
  const raw = parseInt(localStorage.getItem(PIPELINE_ZOOM_KEY) || '', 10);
  return Number.isFinite(raw) ? raw : PIPELINE_ZOOM_DEFAULT;
}

function applyPipelineZoom(px) {
  const clamped = Math.max(PIPELINE_ZOOM_MIN, Math.min(PIPELINE_ZOOM_MAX, px));
  document.documentElement.style.setProperty('--ocr-pipeline-font-size', `${clamped}px`);
  localStorage.setItem(PIPELINE_ZOOM_KEY, String(clamped));
  return clamped;
}

function readPipelineCollapsed() {
  return localStorage.getItem(PIPELINE_COLLAPSED_KEY) === '1';
}

function applyPipelineCollapsed(on) {
  const collapsed = Boolean(on);
  const panel = document.getElementById('ocrPipelinePanel');
  const steps = document.getElementById('ocrPipelineSteps');
  if (panel) {
    panel.classList.toggle('collapsed', collapsed);
  }
  if (steps) {
    steps.hidden = collapsed;
    steps.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
  }
  const button = document.getElementById('ocrPipelineCollapseToggle');
  if (button) {
    button.textContent = collapsed
      ? uiT('ui.button.expand', '展开')
      : uiT('ui.button.collapse', '隐藏');
    button.title = collapsed
      ? uiT('ui.ocr.pipeline.expand_title', '展开 OCR 链路步骤')
      : uiT('ui.ocr.pipeline.collapse_title', '隐藏 OCR 链路步骤');
    button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
  localStorage.setItem(PIPELINE_COLLAPSED_KEY, collapsed ? '1' : '0');
}

function applyCurrentLineCollapsed(on) {
  const collapsed = Boolean(on);
  const node = document.getElementById('currentLineOverview');
  if (node) {
    node.classList.toggle('collapsed', collapsed);
  }
  ['currentLineOverviewHint', 'currentLineEffectiveText', 'observedLinesCollapse', 'currentLineOverviewGrid'].forEach((id) => {
    const contentNode = document.getElementById(id);
    if (contentNode) {
      contentNode.classList.toggle('current-line-collapsed-hidden', collapsed);
      contentNode.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
    }
  });
  const button = document.getElementById('currentLineCollapseToggle');
  if (button) {
    button.textContent = collapsed
      ? uiT('ui.button.expand', '展开')
      : uiT('ui.button.collapse', '隐藏');
    button.title = collapsed
      ? uiT('ui.current_line.expand_title', '展开当前台词内容')
      : uiT('ui.current_line.collapse_title', '隐藏当前台词内容');
    button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
  localStorage.setItem(CL_COLLAPSED_KEY, collapsed ? '1' : '0');
}

function initCurrentLineUiPrefs() {
  applyCurrentLineZoom(readCurrentLineZoom());
  applyCurrentLineCollapsed(readCurrentLineCollapsed());

  document.getElementById('currentLineZoomIn')?.addEventListener('click', () => {
    applyCurrentLineZoom(readCurrentLineZoom() + CL_ZOOM_STEP);
  });
  document.getElementById('currentLineZoomOut')?.addEventListener('click', () => {
    applyCurrentLineZoom(readCurrentLineZoom() - CL_ZOOM_STEP);
  });
  document.getElementById('currentLineZoomReset')?.addEventListener('click', () => {
    applyCurrentLineZoom(CL_ZOOM_DEFAULT);
  });
  document.getElementById('currentLineCollapseToggle')?.addEventListener('click', () => {
    applyCurrentLineCollapsed(!readCurrentLineCollapsed());
  });
}

function initPipelineZoom() {
  applyPipelineZoom(readPipelineZoom());

  document.getElementById('ocrPipelineZoomIn')?.addEventListener('click', () => {
    applyPipelineZoom(readPipelineZoom() + PIPELINE_ZOOM_STEP);
  });
  document.getElementById('ocrPipelineZoomOut')?.addEventListener('click', () => {
    applyPipelineZoom(readPipelineZoom() - PIPELINE_ZOOM_STEP);
  });
  document.getElementById('ocrPipelineZoomReset')?.addEventListener('click', () => {
    applyPipelineZoom(PIPELINE_ZOOM_DEFAULT);
  });
}

function initPipelineCollapse() {
  applyPipelineCollapsed(readPipelineCollapsed());

  document.getElementById('ocrPipelineCollapseToggle')?.addEventListener('click', () => {
    applyPipelineCollapsed(!readPipelineCollapsed());
  });
}

function readOcrWindowCollapsed() {
  return localStorage.getItem(OCR_WINDOW_COLLAPSED_KEY) === '1';
}

function applyOcrWindowCollapsed(on) {
  const collapsed = Boolean(on);
  const panel = document.getElementById('ocrWindowPanel');
  if (panel) {
    panel.classList.toggle('collapsed', collapsed);
  }

  document.querySelectorAll('#ocrWindowPanel .ocr-window-collapsible').forEach((node) => {
    node.hidden = collapsed;
    node.setAttribute('aria-hidden', collapsed ? 'true' : 'false');
  });

  const button = document.getElementById('ocrWindowCollapseToggle');
  if (button) {
    button.textContent = collapsed
      ? uiT('ui.button.expand', '展开')
      : uiT('ui.button.collapse', '隐藏');
    button.title = collapsed
      ? uiT('ui.ocr.window.expand_title', '展开窗口选择内容')
      : uiT('ui.ocr.window.collapse_title', '隐藏窗口选择内容');
    button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }

  localStorage.setItem(OCR_WINDOW_COLLAPSED_KEY, collapsed ? '1' : '0');
}

function initOcrWindowCollapse() {
  applyOcrWindowCollapsed(readOcrWindowCollapsed());

  document.getElementById('ocrWindowCollapseToggle')?.addEventListener('click', () => {
    applyOcrWindowCollapsed(!readOcrWindowCollapsed());
  });
}

const DEFAULT_CAPTURE_PROFILE = {
  left_inset_ratio: 0.05,
  right_inset_ratio: 0.05,
  top_ratio: 0.62,
  bottom_inset_ratio: 0.08,
};
const AIHONG_PROCESS_NAMES = new Set(['thelamentinggeese.exe']);
const OCR_PROFILE_STAGE_LABELS_ZH = {
  default: '通用区域',
  dialogue_stage: '对白区',
  menu_stage: '菜单区',
  title_stage: '标题/主菜单',
  save_load_stage: '存读档',
  config_stage: '设置',
  transition_stage: '转场',
  gallery_stage: '回想/鉴赏',
  minigame_stage: '小游戏',
  game_over_stage: 'Game Over',
};
const OCR_PROFILE_STAGE_I18N_KEYS = {
  default: 'ui.stage.default',
  dialogue_stage: 'ui.stage.dialogue',
  menu_stage: 'ui.stage.menu',
  title_stage: 'ui.stage.title',
  save_load_stage: 'ui.stage.save_load',
  config_stage: 'ui.stage.config',
  transition_stage: 'ui.stage.transition',
  gallery_stage: 'ui.stage.gallery',
  minigame_stage: 'ui.stage.minigame',
  game_over_stage: 'ui.stage.game_over',
};
const OCR_CAPTURE_SAVE_SCOPE_LABELS_ZH = {
  window_bucket: '当前窗口分辨率',
  process_fallback: '进程通用回退',
};
const OCR_CAPTURE_SAVE_SCOPE_I18N_KEYS = {
  window_bucket: 'ui.capture_profile.save_scope.window_bucket',
  process_fallback: 'ui.capture_profile.save_scope.process_fallback',
};
const OCR_CAPTURE_MATCH_SOURCE_LABELS_ZH = {
  bucket_exact: '当前窗口精确命中',
  bucket_aspect_nearest: '相近宽高比回退',
  process_fallback: '进程通用回退',
  builtin_preset: '内建预设',
  config_default: '插件默认配置',
};
const OCR_CAPTURE_MATCH_SOURCE_I18N_KEYS = {
  bucket_exact: 'ui.capture_profile.match_source.bucket_exact',
  bucket_aspect_nearest: 'ui.capture_profile.match_source.bucket_aspect_nearest',
  process_fallback: 'ui.capture_profile.match_source.process_fallback',
  builtin_preset: 'ui.capture_profile.match_source.builtin_preset',
  config_default: 'ui.capture_profile.match_source.config_default',
};
const AIHONG_CAPTURE_PRESETS = {
  dialogue_stage: {
    left_inset_ratio: 0.05,
    right_inset_ratio: 0.24,
    top_ratio: 0.73,
    bottom_inset_ratio: 0.10,
  },
  menu_stage: {
    left_inset_ratio: 0.20,
    right_inset_ratio: 0.20,
    top_ratio: 0.40,
    bottom_inset_ratio: 0.34,
  },
};

const AUTO_REFRESH_IDLE_INTERVAL_MS = 5000;
const AUTO_REFRESH_ACTIVE_INTERVAL_MS = 1000;
const AUTO_REFRESH_URGENT_INTERVAL_MS = 500;
const AUTO_REFRESH_INTERVAL_MS = AUTO_REFRESH_ACTIVE_INTERVAL_MS;
const FOCUS_PAUSE_REFRESH_INTERVAL_MS = 1000;
const ERROR_REFRESH_INTERVAL_MS = 10000;
const OCR_WINDOW_REFRESH_TTL_MS = 3000;
const MEMORY_PROCESS_REFRESH_TTL_MS = 3000;
const FIELD_LABELS_ZH = {
  connection_state: '连接状态',
  active_data_source: '当前数据源',
  reader_mode: '文本读取模式',
  mode: '模式',
  push_notifications: '推送通知',
  advance_speed: '推进速度',
  bound_game_id: '绑定游戏 ID',
  active_session_id: '当前会话 ID',
  last_seq: '最新序号',
  stream_reset_pending: '等待重置流',
  available_game_ids: '可用游戏 ID',
  performance_cpu_percent: '插件 CPU',
  performance_memory_mb: '插件内存',
  performance_memory_percent: '插件内存占比',
  performance_thread_count: '插件线程数',
  performance_process: '插件进程',
  performance_detail: '性能指标状态',
  ocr_reader_enabled: 'OCR Reader 已启用',
  ocr_reader_status: 'OCR Reader 状态',
  ocr_reader_detail: 'OCR Reader 详情',
  ocr_reader_target: 'OCR Reader 目标',
  ocr_poll_interval_seconds: 'OCR 识别间隔',
  ocr_trigger_mode: 'OCR 触发方式',
  ocr_trigger_mode_effective: 'OCR 实际触发方式',
  ocr_reader_fast_loop_enabled: 'OCR Fast Loop 已启用',
  ocr_fast_loop_running: 'OCR Fast Loop 运行中',
  ocr_fast_loop_inflight_seconds: 'OCR Fast Loop 本轮耗时',
  ocr_fast_loop_last_duration_seconds: 'OCR Fast Loop 上轮耗时',
  ocr_fast_loop_iteration_count: 'OCR Fast Loop 轮次',
  ocr_poll_latency_sample_count: 'OCR poll 样本数',
  ocr_poll_duration_p50_seconds: 'OCR poll P50',
  ocr_poll_duration_p95_seconds: 'OCR poll P95',
  bridge_poll_latency_sample_count: 'Bridge poll 样本数',
  bridge_poll_duration_p50_seconds: 'Bridge poll P50',
  bridge_poll_duration_p95_seconds: 'Bridge poll P95',
  ocr_auto_degrade_reason: 'OCR 自动降级原因',
  ocr_auto_degrade_at: 'OCR 自动降级时间',
  ocr_auto_degrade_count: 'OCR 自动降级次数',
  candidate_age_seconds: '候选台词等待',
  stable_confirm_wait_seconds: '稳定确认等待',
  ocr_reader_allowed: 'OCR Reader 允许轮询',
  ocr_reader_allowed_block_reason: 'OCR Reader 禁用原因',
  target_window_visible: '目标窗口可见',
  target_window_minimized: '目标窗口最小化',
  ocr_window_capture_eligible: 'OCR 窗口可采集',
  ocr_window_capture_available: 'OCR 窗口采集可用',
  ocr_window_capture_block_reason: 'OCR 窗口采集阻塞原因',
  input_target_foreground: '输入目标在前台',
  input_target_block_reason: '输入目标阻塞原因',
  ocr_reader_manager_available: 'OCR Reader 管理器可用',
  ocr_runtime_status: 'OCR Runtime 状态',
  ocr_tick_allowed: 'OCR 本轮执行',
  ocr_tick_gate_allowed: 'OCR 轮询门控',
  ocr_tick_skipped_reason: 'OCR 执行跳过原因',
  ocr_tick_entered: 'OCR Tick 已进入',
  ocr_tick_lock_acquired: 'OCR Tick 锁已获取',
  ocr_tick_block_reason: 'OCR 本轮未执行原因',
  ocr_emit_block_reason: 'OCR 未写入原因',
  ocr_fast_loop_delegated: 'OCR Fast Loop 接管',
  ocr_waiting_for_advance: 'OCR 等待游戏推进',
  ocr_waiting_for_advance_reason: 'OCR 等待推进原因',
  pending_ocr_advance_capture: '等待推进采集',
  pending_manual_foreground_ocr_capture: '等待前台推进采集',
  pending_ocr_delay_remaining: '推进采集延迟剩余',
  pending_ocr_advance_capture_age_seconds: '推进采集等待时长',
  pending_ocr_advance_reason: '推进采集来源',
  pending_ocr_advance_clear_reason: '推进采集清理原因',
  ocr_bootstrap_capture_needed: 'OCR 启动采集需要',
  after_advance_screen_refresh_tick_needed: '推进后画面刷新需要',
  companion_after_advance_ocr_refresh_tick_needed: '陪伴模式推进后 OCR 刷新需要',
  foreground_refresh_attempted: '前台状态已刷新',
  foreground_refresh_skipped_reason: '前台刷新跳过原因',
  ocr_last_tick_decision_at: 'OCR 最近轮询决策',
  display_source_not_ocr_reason: '非 OCR 展示原因',
  ocr_backend_selection: 'OCR 后端选择',
  ocr_capture_backend_selection: '截图后端选择',
  ocr_background_state: 'OCR 后台状态',
  ocr_background_message: 'OCR 后台说明',
  ocr_background_polling: 'OCR 后台轮询',
  ocr_foreground_resume_pending: 'OCR 等待前台恢复',
  ocr_capture_backend_blocked: 'OCR 截图后端受阻',
  ocr_screen_awareness_latency_mode: '画面感知延迟模式',
  ocr_screen_awareness_min_interval_seconds: '画面感知最小间隔',
  ocr_backend_kind: 'OCR 后端类型',
  ocr_backend_detail: 'OCR 后端详情',
  rapidocr_enabled: 'RapidOCR 已启用',
  rapidocr_installed: 'RapidOCR 已安装',
  rapidocr_detail: 'RapidOCR 详情',
  dxcam_installed: 'DXcam 已安装',
  dxcam_detail: 'DXcam 详情',
  memory_reader_enabled: 'Memory Reader 已启用',
  memory_reader_status: 'Memory Reader 状态',
  memory_reader_detail: 'Memory Reader 详情',
  memory_reader_process: 'Memory Reader 进程',
  tesseract_installed: 'Tesseract 已安装',
  tesseract_detail: 'Tesseract 详情',
  tesseract_missing_languages: 'Tesseract 缺失语言',
  textractor_installed: 'Textractor 已安装',
  textractor_detail: 'Textractor 详情',
  last_error: '最近错误',
  status: '状态',
  detail: '详情',
  process_name: '进程名',
  pid: '进程 ID',
  window_title: '窗口标题',
  width: '窗口宽度',
  height: '窗口高度',
  aspect_ratio: '窗口宽高比',
  game_id: '游戏 ID',
  session_id: '会话 ID',
  last_event_ts: '最近事件时间',
  last_text_seq: '最近 Memory 文本序号',
  last_text_ts: '最近 Memory 文本时间',
  last_text_recent: 'Memory 文本近期有效',
  last_text_age_seconds: 'Memory 文本停更秒数',
  capture_stage: '截图阶段',
  capture_profile: '截图配置',
  capture_profile_match_source: '截图配置来源',
  capture_profile_bucket_key: '截图配置桶',
  capture_backend_kind: '截图后端',
  capture_backend_detail: '截图后端详情',
  last_capture_image_hash: '最近截图 Hash',
  consecutive_same_capture_frames: '连续相同截图',
  stale_capture_backend: '截图源未更新',
  last_rejected_ocr_text: '最近拒绝 OCR 文本',
  last_rejected_ocr_reason: '最近拒绝原因',
  last_rejected_ocr_at: '最近拒绝时间',
  last_rejected_capture_backend: '拒绝截图后端',
  screen_awareness_last_skip_reason: '画面感知跳过原因',
  screen_awareness_last_region_count: '画面感知区域数',
  screen_awareness_last_capture_duration_seconds: '画面感知截图耗时',
  screen_awareness_last_ocr_duration_seconds: '画面感知 OCR 耗时',
  backend_kind: '后端类型',
  backend_detail: '后端详情',
  backend_path: '后端路径',
  backend_model: '后端模型',
  tesseract_path: 'Tesseract 路径',
  languages: '语言',
  takeover_reason: '接管原因',
  target_selection_mode: '目标选择模式',
  target_selection_detail: '目标选择详情',
  effective_window_key: '生效窗口键',
  effective_window_title: '生效窗口标题',
  effective_process_name: '生效进程名',
  foreground_refresh_at: '前台刷新时间',
  foreground_refresh_detail: '前台刷新详情',
  foreground_hwnd: '当前前台 hwnd',
  target_hwnd: '目标 hwnd',
  last_poll_started_at: '最近 OCR poll 开始',
  last_poll_completed_at: '最近 OCR poll 完成',
  last_poll_duration_seconds: '最近 OCR poll 耗时',
  last_poll_emitted_event: '最近 OCR poll 产生事件',
  last_tick_skipped: '最近 tick 被跳过',
  last_tick_skip_reason: 'tick 跳过原因',
  pending_visual_scene_count: '待提交场景变化',
  last_auto_recalibrate_attempts: '自动校准 OCR 次数',
  last_auto_recalibrate_duration_seconds: '自动校准耗时',
  last_auto_recalibrate_limited: '自动校准达到限制',
  last_auto_recalibrate_error: '自动校准错误',
  last_capture_total_duration_seconds: '最近 OCR 总耗时',
  last_capture_frame_duration_seconds: '截图耗时',
  last_capture_background_duration_seconds: '背景 Hash 耗时',
  last_capture_image_hash_duration_seconds: '截图 Hash 耗时',
  last_ocr_extract_duration_seconds: 'OCR 推理耗时',
  last_backend_plan_duration_seconds: '后端选择耗时',
  last_window_scan_duration_seconds: '窗口扫描耗时',
  last_capture_background_hash_skipped: '已跳过背景 Hash',
  candidate_count: '候选窗口数',
  excluded_candidate_count: '排除窗口数',
  last_exclude_reason: '最近排除原因',
  speaker: '说话人',
  text: '文本',
  scene_id: '场景 ID',
  line_id: '台词 ID',
  route_id: '路线 ID',
  is_menu_open: '菜单是否打开',
  snapshot_ts: '快照时间',
  stale: '是否过期',
  result: '结果',
  agent_user_status: 'Agent 用户状态',
  agent_pause_kind: 'Agent 暂停类型',
  agent_pause_message: 'Agent 暂停说明',
  agent_can_resume_by_button: '可用按钮恢复',
  agent_can_resume_by_focus: '可由窗口聚焦恢复',
  inbound_queue_size: '入站队列',
  outbound_queue_size: '出站队列',
  last_interruption: '最近打断',
  last_outbound_message: '最近出站消息',
  recent_pushes: '最近推送数',
  activity: '活动',
  reason: '原因',
  input_source: '输入源',
  scene_stage: '场景阶段',
  push_policy: '推送策略',
  actionable: '可操作',
  standby_requested: '已请求待机',
  memory_counts: '记忆计数',
};

const CONNECTION_STATE_LABELS_ZH = {
  active: '运行中',
  idle: '空闲',
  stale: '已过期',
};

const MODE_LABELS_ZH = {
  silent: '静默模式',
  companion: '伴读模式',
  choice_advisor: '自动推进模式',
};
const ADVANCE_SPEED_LABELS_ZH = {
  slow: '慢',
  medium: '中等',
  fast: '快速',
};

const AGENT_USER_STATUS_LABELS_ZH = {
  running: '运行中',
  read_only: '只读伴读',
  paused_by_user: '用户待机',
  paused_window_not_foreground: '游戏窗口未前台',
  screen_safety_pause: '安全暂停',
  ocr_unavailable: 'OCR 不可用',
  waiting_choice: '等待/处理选项',
  acting: '正在操作',
  error: '错误',
};

const DATA_SOURCE_LABELS_ZH = {
  bridge_sdk: 'Bridge SDK',
  ocr_reader: 'OCR 读取',
  memory_reader: '内存读取',
};

const READER_MODE_LABELS_ZH = {
  auto: '自动（内存有新文本优先，停更后 OCR 接管）',
  memory_reader: '内存读取',
  ocr_reader: 'OCR',
};

const ACTION_LABELS_ZH = {
  refresh_all: '刷新全部',
  debug_details: '查看调试详情',
  refresh_ocr_windows: '刷新窗口',
  select_ocr_window: '选择游戏窗口',
  focus_game: '切回游戏窗口',
  capture_backend: '切换截图方式',
  recalibrate_ocr: '重新截图校准',
  line_details: '查看识别详情',
  choice_advisor: '切换到自动推进模式',
  install_rapidocr: '查看 RapidOCR 启用提示',
  refresh_status: '刷新状态',
  start_recognition: '开始自动识别',
};

function mapLabel(i18nKeys, labels, key, fallback = '') {
  const normalized = String(key || '').trim();
  const fallbackText = labels[normalized] || fallback || normalized;
  const i18nKey = i18nKeys[normalized];
  return i18nKey ? uiT(i18nKey, fallbackText) : fallbackText;
}

function fieldLabel(key) {
  return uiDynamicT('ui.field', key, FIELD_LABELS_ZH[key] || key);
}

function agentUserStatusLabel(key, fallback = '') {
  return uiDynamicT('ui.agent_status', key, AGENT_USER_STATUS_LABELS_ZH[key] || fallback || key);
}

function ocrProfileStageLabel(key, fallback = '') {
  return mapLabel(OCR_PROFILE_STAGE_I18N_KEYS, OCR_PROFILE_STAGE_LABELS_ZH, key, fallback);
}

function ocrCaptureMatchSourceLabel(key, fallback = '') {
  return mapLabel(OCR_CAPTURE_MATCH_SOURCE_I18N_KEYS, OCR_CAPTURE_MATCH_SOURCE_LABELS_ZH, key, fallback);
}

function ocrCaptureSaveScopeLabel(key, fallback = '') {
  return mapLabel(OCR_CAPTURE_SAVE_SCOPE_I18N_KEYS, OCR_CAPTURE_SAVE_SCOPE_LABELS_ZH, key, fallback);
}

function connectionStateLabel(key, fallback = '') {
  return uiDynamicT('ui.connection_state', key, CONNECTION_STATE_LABELS_ZH[key] || fallback || key);
}

function modeLabel(key, fallback = '') {
  return uiDynamicT('ui.mode_label', key, MODE_LABELS_ZH[key] || fallback || key);
}

function advanceSpeedLabel(key, fallback = '') {
  return uiDynamicT('ui.speed_label', key, ADVANCE_SPEED_LABELS_ZH[key] || fallback || key);
}

function dataSourceLabel(key, fallback = '') {
  return uiDynamicT('ui.data_source', key, DATA_SOURCE_LABELS_ZH[key] || fallback || key);
}

function readerModeLabel(key, fallback = '') {
  return uiDynamicT('ui.reader_mode', key, READER_MODE_LABELS_ZH[key] || fallback || key);
}

function primaryActionLabel(id, fallback = '') {
  return uiDynamicT('ui.action', id, ACTION_LABELS_ZH[id] || fallback || id);
}

function diagnosisAction(id, fallback = '') {
  return { id, label: primaryActionLabel(id, fallback || id) };
}

let latestAgentReply = uiT('ui.agent.no_interaction', '暂无交互');
let latestAgentStatus = null;
let latestStatus = null;
let latestSnapshotData = null;
let latestMemoryProcessSnapshot = null;
let latestOcrWindowSnapshot = null;
let refreshInFlight = null;
let memoryProcessRefreshInFlight = null;
let ocrWindowRefreshInFlight = null;
let lastMemoryProcessRefreshAt = 0;
let lastOcrWindowRefreshAt = 0;
let emptyOcrWindowFocusForceRefreshDone = false;
let autoRefreshTimer = null;
let autoRefreshIntervalMs = AUTO_REFRESH_INTERVAL_MS;
let activeInstallTab = 'tesseract';
let settingsDirty = false;
let settingsSaveInFlight = false;
let pendingModeSelection = '';
let modeSaveRequestId = 0;
let settingsAutosaveTimer = null;
let flashTimer = null;
let flashToken = 0;
let ocrScreenTemplatesUndoValue = '';
let ocrRegionSnapshot = null;
let ocrRegionSelection = null;
let ocrRegionDragStart = null;

const SETTINGS_CONTROL_IDS = new Set([
  'modeSelect',
  'pushToggle',
  'advanceSpeedSelect',
  'readerModeSelect',
  'ocrPollIntervalInput',
  'ocrTriggerModeSelect',
  'llmVisionToggle',
  'fastLoopToggle',
  'llmVisionMaxImagePxInput',
]);

const latestInsights = {
  suggestKey: '',
  suggestPayload: null,
};

function getInstallConfig(kind) {
  const config = getInstallUIConfig()[kind];
  if (!config) {
    throw new Error(`unsupported install kind: ${kind}`);
  }
  return config;
}

function getInstallState(kind) {
  return installRuntime[kind];
}

function getInstallNodes(kind) {
  const prefix = getInstallConfig(kind).domPrefix;
  return {
    card: document.getElementById(`${prefix}InstallState`),
    statusText: document.getElementById(`${prefix}InstallStatusText`),
    percentText: document.getElementById(`${prefix}InstallPercent`),
    messageText: document.getElementById(`${prefix}InstallMessage`),
    detailText: document.getElementById(`${prefix}InstallDetail`),
    progressBar: document.getElementById(`${prefix}InstallBar`),
    button: document.getElementById(`${prefix}InstallBtn`),
  };
}

function configureUseButton(id, { active = false, disabled = false, text = '', title = '' } = {}) {
  const button = document.getElementById(id);
  if (!button) {
    return;
  }
  if (text) {
    button.textContent = text;
  }
  button.disabled = Boolean(disabled || active);
  button.classList.toggle('active', Boolean(active));
  button.title = title || '';
}

async function createRun(entryId, args = {}) {
  const createResp = await fetch(RUNS_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      plugin_id: PLUGIN_ID,
      entry_id: entryId,
      args,
    }),
  });

  if (!createResp.ok) {
    throw new Error(uiTf('ui.error.run_create_failed', '创建任务失败: HTTP {status}', { status: createResp.status }));
  }

  const createData = await createResp.json();
  const runId = createData.run_id || createData.id;
  if (!runId) {
    throw new Error(uiT('ui.error.run_id_missing', '未获取到 run_id'));
  }
  return runId;
}

async function exportRunResult(runId) {
  const exportResp = await fetch(`${RUNS_URL}/${runId}/export`);
  if (!exportResp.ok) {
    return {};
  }
  const exportData = await exportResp.json();
  const items = exportData.items || [];
  const resultItem = items.find((item) => item.type === 'json' && item.json) || items[0];
  const pluginResponse = resultItem ? (resultItem.json || {}) : {};
  if (pluginResponse.success === false || pluginResponse.error) {
    throw new Error(pluginResponse.error?.message || pluginResponse.message || uiT('ui.error.plugin_call_failed', '插件调用失败'));
  }
  return pluginResponse.data || {};
}

async function callPlugin(entryId, args = {}, { timeoutMs } = {}) {
  const runId = await createRun(entryId, args);
  const deadline = Date.now() + (timeoutMs ?? PLUGIN_RUN_TIMEOUT_MS);
  let pollDelay = PLUGIN_RUN_INITIAL_POLL_MS;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, pollDelay));
    pollDelay = Math.min(Math.round(pollDelay * 1.5), PLUGIN_RUN_MAX_POLL_MS);
    const pollResp = await fetch(`${RUNS_URL}/${runId}`);
    if (!pollResp.ok) {
      if ([404, 405, 501].includes(pollResp.status)) {
        console.error('[galgame] callPlugin permanent error', pollResp.status, entryId);
        throw new Error(`Plugin call failed with ${pollResp.status}`);
      }
      continue;
    }

    const runRecord = await pollResp.json();
    if (runRecord.status === 'succeeded') {
      return await exportRunResult(runId);
    }

    if (['failed', 'canceled', 'timeout'].includes(runRecord.status)) {
      throw new Error(runRecord.error?.message || runRecord.message || runRecord.status);
    }
  }

  throw new Error(uiT('ui.error.plugin_call_timeout', '插件调用超时'));
}

async function safeCall(entryId, args = {}, fallback = {}) {
  try {
    return await callPlugin(entryId, args);
  } catch (error) {
    return {
      ...fallback,
      degraded: true,
      diagnostic: error instanceof Error ? error.message : String(error),
    };
  }
}

function escapeHtml(text) {
  if (text == null) {
    return '';
  }
  const div = document.createElement('div');
  div.textContent = String(text);
  return div.innerHTML;
}

function setFlash(message, type = 'info') {
  const node = document.getElementById('flashMessage');
  flashToken += 1;
  const token = flashToken;
  if (flashTimer) {
    clearTimeout(flashTimer);
    flashTimer = null;
  }
  node.hidden = !message;
  node.textContent = message || '';
  node.className = `flash-message ${type}`;
  if (message && ['success', 'info'].includes(type)) {
    flashTimer = window.setTimeout(() => {
      if (flashToken !== token) {
        return;
      }
      node.hidden = true;
      node.textContent = '';
      flashTimer = null;
    }, FLASH_AUTO_HIDE_MS);
  }
}

function isPluginNotStartedError(error) {
  const message = error instanceof Error ? error.message : String(error || '');
  return message.includes("Plugin 'galgame_plugin' is registered but not running");
}

function updateSettingsDirtyHint(message = '') {
  const hint = document.getElementById('settingsDirtyHint');
  if (!hint) {
    return;
  }
  if (settingsSaveInFlight) {
    hint.hidden = false;
    hint.textContent = message || uiT('ui.settings.saving_hint', '正在保存...');
    return;
  }
  hint.hidden = !settingsDirty;
  hint.textContent = message || uiT('ui.settings.dirty', '有未保存设置');
}

function shouldOfferRapidOcrInstall(status = {}) {
  const rapidocr = status.rapidocr || {};
  return Boolean(status.rapidocr_enabled) && rapidocr.installed !== true;
}

function withRapidOcrInstallAction(diagnosis, status = {}) {
  if (!diagnosis || !shouldOfferRapidOcrInstall(status)) {
    return diagnosis;
  }
  const actions = Array.isArray(diagnosis.actions) ? diagnosis.actions : [];
  if (actions.some((action) => action && action.id === 'install_rapidocr')) {
    return diagnosis;
  }
  return {
    ...diagnosis,
    actions: [
      diagnosisAction('install_rapidocr'),
      ...actions,
    ],
  };
}

function textValue(value) {
  return String(value == null ? '' : value).trim();
}

function statusBoolValue(primary, fallback = undefined) {
  if (typeof primary === 'boolean') {
    return primary;
  }
  if (typeof fallback === 'boolean') {
    return fallback;
  }
  return Boolean(primary || fallback);
}

function lineText(line = {}) {
  return textValue(line && typeof line === 'object' ? line.text : '');
}

function lineStability(line = {}) {
  return textValue(line && typeof line === 'object' ? line.stability : '').toLowerCase();
}

function isStableLine(line = {}) {
  if (!line || typeof line !== 'object') {
    return false;
  }
  const stability = lineStability(line);
  if (stability) {
    return stability === 'stable';
  }
  const source = textValue(line.source).toLowerCase();
  return source === 'stable' || source === 'history';
}

function compactLineText(value) {
  return textValue(value).replace(/\s+/g, '');
}

function getCurrentLineTexts(status = {}) {
  const runtime = status.ocr_reader_runtime || {};
  const effectiveLine = (
    status.effective_current_line && typeof status.effective_current_line === 'object'
      ? status.effective_current_line
      : {}
  );
  const observedLine = (
    runtime.last_observed_line && typeof runtime.last_observed_line === 'object'
      ? runtime.last_observed_line
      : {}
  );
  const stableText = lineText(runtime.last_stable_line)
    || (isStableLine(effectiveLine) ? lineText(effectiveLine) : '');
  const effectiveText = lineText(effectiveLine);
  const observedText = lineText(observedLine)
    || (!isStableLine(effectiveLine) ? effectiveText : '');
  const rawText = textValue(runtime.last_raw_ocr_text);
  return {
    rawText,
    observedText,
    stableText,
    effectiveText,
  };
}

function formatStableBlockReason(reason) {
  const mapping = {
    waiting_for_repeat: uiT('ui.reason.stable.waiting_for_repeat', '刚读到新文字，正在确认是不是同一句台词'),
    duplicate_stable_text: uiT('ui.reason.stable.duplicate_stable_text', '这句台词已经显示过'),
    duplicate_raw_text: uiT('ui.reason.stable.duplicate_raw_text', '识别结果和上一轮相同，暂不重复写入'),
    duplicate_observed_text: uiT('ui.reason.stable.duplicate_observed_text', '候选台词和上一轮相同，正在等待变化'),
    duplicate_candidate_text: uiT('ui.reason.stable.duplicate_candidate_text', '候选台词和上一轮相同，正在等待变化'),
    empty_text: uiT('ui.reason.stable.empty_text', '文字识别暂时没有读到有效文本'),
    no_text: uiT('ui.reason.stable.no_text', '文字识别暂时没有读到有效文本'),
    no_valid_text: uiT('ui.reason.stable.no_valid_text', '文字识别暂时没有读到有效文本'),
    low_confidence: uiT('ui.reason.stable.low_confidence', '文字识别置信度较低，暂不写入台词'),
    overlay_text: uiT('ui.reason.stable.overlay_text', '识别结果像系统界面文字，暂不写入台词'),
    game_overlay_text: uiT('ui.reason.stable.game_overlay_text', '识别结果像游戏菜单或系统界面，暂不写入台词'),
    waiting_for_change: uiT('ui.reason.stable.waiting_for_change', '画面文字没有变化，正在等待新台词'),
    waiting_for_new_text: uiT('ui.reason.stable.waiting_for_new_text', '正在等待新台词出现'),
    capture_failed: uiT('ui.reason.stable.capture_failed', '截图或识别失败，暂时不能确认台词'),
  };
  const normalized = textValue(reason);
  return uiDynamicT('ui.reason.stable', normalized, mapping[normalized] || normalized);
}

function formatOcrTickBlockReason(reason) {
  const mapping = {
    ocr_reader_unavailable: uiT('ui.reason.tick.ocr_reader_unavailable', 'OCR Reader 尚不可用'),
    ocr_reader_not_allowed: uiT('ui.reason.tick.ocr_reader_not_allowed', '当前读取模式不允许 OCR'),
    reader_mode_memory_only: uiT('ui.reason.tick.reader_mode_memory_only', '当前为仅内存读取模式'),
    memory_reader_recent_text: uiT('ui.reason.tick.memory_reader_recent_text', '内存读取已有近期文本，暂不轮询 OCR'),
    memory_reader_default_unavailable: uiT('ui.reason.tick.memory_reader_default_unavailable', '默认内存读取目标尚不可用，未主动切到 OCR'),
    waiting_pending_advance_delay: uiT('ui.reason.tick.waiting_pending_advance_delay', '等待推进后的延迟采集窗口'),
    trigger_mode_after_advance_waiting_for_input: uiT('ui.reason.tick.trigger_mode_after_advance_waiting_for_input', '点击对白后识别模式正在等待游戏推进'),
    trigger_mode_after_advance_waiting_for_refresh: uiT('ui.reason.tick.trigger_mode_after_advance_waiting_for_refresh', '点击对白后识别模式正在等待刷新条件'),
    tick_gate_closed: uiT('ui.reason.tick.tick_gate_closed', 'OCR 轮询门控未打开'),
    plugin_config_missing: uiT('ui.reason.tick.plugin_config_missing', '插件配置尚未就绪'),
    ocr_reader_manager_missing: uiT('ui.reason.tick.ocr_reader_manager_missing', 'OCR Reader 管理器尚未就绪'),
    ocr_fast_loop_started: uiT('ui.reason.tick.ocr_fast_loop_started', 'OCR Fast Loop 已接管本轮采集'),
    ocr_tick_lock_busy: uiT('ui.reason.tick.ocr_tick_lock_busy', '上一轮 OCR tick 仍在执行'),
    tick_gate_timeout: uiT('ui.reason.tick.tick_gate_timeout', '推进采集等待门控超时，已清理'),
    trigger_mode_not_after_advance: uiT('ui.reason.tick.trigger_mode_not_after_advance', '当前不是点击对白后识别模式'),
    refresh_method_missing: uiT('ui.reason.tick.refresh_method_missing', '前台刷新方法不可用'),
    refresh_failed: uiT('ui.reason.tick.refresh_failed', '前台刷新失败'),
    target_missing: uiT('ui.reason.tick.target_missing', '目标游戏窗口不存在'),
    target_minimized: uiT('ui.reason.tick.target_minimized', '目标游戏窗口已最小化'),
    target_not_visible: uiT('ui.reason.tick.target_not_visible', '目标游戏窗口不可见'),
    target_not_foreground: uiT('ui.reason.tick.target_not_foreground', '目标游戏窗口不是前台焦点'),
    capture_failed: uiT('ui.reason.tick.capture_failed', '截图或文字识别失败'),
    stale_capture_backend: uiT('ui.reason.tick.stale_capture_backend', '截图画面没有更新'),
  };
  const normalized = textValue(reason);
  return uiDynamicT('ui.reason.tick', normalized, mapping[normalized] || normalized);
}

function formatOcrEmitBlockReason(reason) {
  const mapping = {
    capture_failed: uiT('ui.reason.emit.capture_failed', '截图或文字识别失败'),
    stale_capture_backend: uiT('ui.reason.emit.stale_capture_backend', '截图画面没有更新'),
    screen_classification_skipped_dialogue: uiT('ui.reason.emit.screen_classification_skipped_dialogue', '画面被判定为非对白界面'),
    no_dialogue_text: uiT('ui.reason.emit.no_dialogue_text', '没有可写入的对白文本'),
    ...{
      waiting_for_repeat: formatStableBlockReason('waiting_for_repeat'),
      duplicate_stable_text: formatStableBlockReason('duplicate_stable_text'),
      duplicate_raw_text: formatStableBlockReason('duplicate_raw_text'),
      duplicate_observed_text: formatStableBlockReason('duplicate_observed_text'),
      duplicate_candidate_text: formatStableBlockReason('duplicate_candidate_text'),
      empty_text: formatStableBlockReason('empty_text'),
      no_text: formatStableBlockReason('no_text'),
      no_valid_text: formatStableBlockReason('no_valid_text'),
      low_confidence: formatStableBlockReason('low_confidence'),
      overlay_text: formatStableBlockReason('overlay_text'),
      game_overlay_text: formatStableBlockReason('game_overlay_text'),
      waiting_for_change: formatStableBlockReason('waiting_for_change'),
      waiting_for_new_text: formatStableBlockReason('waiting_for_new_text'),
    },
  };
  const normalized = textValue(reason);
  return uiDynamicT('ui.reason.emit', normalized, mapping[normalized] || formatStableBlockReason(normalized));
}

function formatOcrBackgroundState(state) {
  const mapping = {
    background_polling: uiT('ui.ocr.background_state.background_polling', '后台轮询中'),
    foreground_resume_pending: uiT('ui.ocr.background_state.foreground_resume_pending', '等待前台恢复'),
    visible_background_readable: uiT('ui.ocr.background_state.visible_background_readable', '可见后台可读'),
    capture_backend_blocked: uiT('ui.ocr.background_state.capture_backend_blocked', '截图后端受阻'),
    target_unavailable: uiT('ui.ocr.background_state.target_unavailable', '目标窗口不可用'),
    foreground_active: uiT('ui.ocr.background_state.foreground_active', '前台可采集'),
    idle: uiT('ui.ocr.background_state.idle', '未激活'),
  };
  const normalized = textValue(state);
  return uiDynamicT('ui.ocr.background_state', normalized, mapping[normalized] || normalized);
}

function normalizePrimaryDiagnosis(diagnosis) {
  if (!diagnosis || typeof diagnosis !== 'object') {
    return null;
  }
  const severity = textValue(diagnosis.severity);
  const title = textValue(diagnosis.title);
  const body = textValue(diagnosis.message || diagnosis.body);
  if (!title && !body) {
    return null;
  }
  const actions = Array.isArray(diagnosis.actions)
    ? diagnosis.actions.map((action) => {
      if (!action || typeof action !== 'object') {
        return null;
      }
      const id = textValue(action.id);
      if (!id) {
        return null;
      }
      return {
        id,
        label: textValue(action.label || action.title || id) || id,
      };
    }).filter(Boolean)
    : [];
  return {
    severity: ['ok', 'info', 'warning', 'error'].includes(severity) ? severity : 'info',
    title: title || uiT('ui.diag.default_title', '运行诊断'),
    body,
    actions,
  };
}

function buildPrimaryDiagnosis(status = {}) {
  const backendDiagnosis = normalizePrimaryDiagnosis(status.primary_diagnosis);
  if (backendDiagnosis) {
    return withRapidOcrInstallAction(backendDiagnosis, status);
  }
  const diagnose = (diagnosis) => withRapidOcrInstallAction(diagnosis, status);

  const runtime = status.ocr_reader_runtime || {};
  const detail = textValue(runtime.target_selection_detail);
  const contextState = textValue(status.ocr_context_state || runtime.ocr_context_state);
  const lastExcludeReason = textValue(runtime.last_exclude_reason);
  const lastCaptureError = textValue(runtime.last_capture_error);
  const lastRejectedReason = textValue(runtime.last_rejected_ocr_reason);
  const lastError = textValue(status.last_error && status.last_error.message);
  const agentPauseKind = textValue(status.agent_pause_kind);
  const agentUserStatus = textValue(status.agent_user_status);
  const backgroundStatus = status.ocr_background_status || {};
  const intervalBackgroundBlocked = (
    textValue(backgroundStatus.state || status.ocr_background_state) === 'capture_backend_blocked'
    && textValue(backgroundStatus.trigger_mode || status.ocr_reader_trigger_mode) === 'interval'
    && runtime.target_is_foreground === false
  );
  const { observedText, stableText, effectiveText } = getCurrentLineTexts(status);
  const observedKey = compactLineText(observedText);
  const stableKey = compactLineText(stableText);
  const hasEffectiveWindow = Boolean(textValue(runtime.effective_window_key));
  const candidateCount = Number(runtime.candidate_count || 0);
  const tickBlockReason = textValue(status.ocr_tick_block_reason || runtime.ocr_tick_block_reason);
  const emitBlockReason = textValue(status.ocr_emit_block_reason || runtime.ocr_emit_block_reason);
  const connectionState = textValue(status.connection_state);
  const hasOcrRuntimeSignal = Boolean(
    status.ocr_reader_enabled
    || runtime.status
    || runtime.detail
    || contextState
    || detail
    || Object.prototype.hasOwnProperty.call(runtime, 'candidate_count')
  );

  if (connectionState === 'plugin_not_started') {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.plugin_not_started.title', '插件尚未启动'),
      body: uiT('ui.diag.plugin_not_started.body', '请在插件管理页面点击"启动"按钮，启动完成后数据会自动刷新。'),
      actions: [
        diagnosisAction('refresh_all'),
      ],
    });
  }

  if (lastError) {
    return diagnose({
      severity: 'error',
      title: uiT('ui.diag.plugin_error.title', '插件运行出错'),
      body: uiTf('ui.diag.plugin_error.body', '{error}。可以先刷新状态；如果仍然出现，请查看调试详情。', { error: lastError }),
      actions: [
        diagnosisAction('refresh_all'),
        diagnosisAction('debug_details'),
      ],
    });
  }

  if (detail === 'memory_reader_window_minimized' || lastExcludeReason === 'excluded_minimized_window') {
    return diagnose({
      severity: 'warning',
      title: uiT('ui.diag.window_minimized.title', '游戏窗口最小化了'),
      body: uiT('ui.diag.window_minimized.body', '检测到游戏，但窗口最小化，文字识别不能截图。请恢复游戏窗口后继续。'),
      actions: [
        diagnosisAction('refresh_ocr_windows'),
        diagnosisAction('select_ocr_window'),
      ],
    });
  }

  if (contextState === 'capture_failed' || lastCaptureError) {
    const captureFailure = lastCaptureError || uiT('ui.diag.capture_failed.default_error', '截图或识别后端返回错误，新台词不会更新。');
    return diagnose({
      severity: 'error',
      title: uiT('ui.diag.capture_failed.title', '截图或文字识别失败'),
      body: intervalBackgroundBlocked
        ? uiTf('ui.diag.capture_failed.interval_body', '{error}当前为定时 OCR 后台读取，请确认窗口可见且未最小化；如果仍失败，切换截图方式或重新选择 OCR 窗口。', { error: captureFailure })
        : captureFailure,
      actions: intervalBackgroundBlocked
        ? [
          diagnosisAction('focus_game'),
          diagnosisAction('capture_backend'),
          diagnosisAction('select_ocr_window'),
          diagnosisAction('debug_details'),
        ]
        : [
          diagnosisAction('recalibrate_ocr'),
          diagnosisAction('capture_backend'),
          diagnosisAction('debug_details'),
        ],
    });
  }

  if (runtime.stale_capture_backend) {
    return diagnose({
      severity: 'warning',
      title: uiT('ui.diag.stale_capture.title', '截图画面没有更新'),
      body: intervalBackgroundBlocked
        ? uiT('ui.diag.stale_capture.interval_body', '定时 OCR 后台读取时截图画面没有更新。请确认游戏窗口可见且未最小化；如果仍停在旧画面，请切换截图方式或重新选择 OCR 窗口。')
        : uiT('ui.diag.stale_capture.body', '当前截图源可能停在旧画面。请切回游戏窗口，或切换截图方式后再试。'),
      actions: intervalBackgroundBlocked
        ? [
          diagnosisAction('focus_game'),
          diagnosisAction('capture_backend'),
          diagnosisAction('select_ocr_window'),
        ]
        : [
          diagnosisAction('focus_game'),
          diagnosisAction('capture_backend'),
          diagnosisAction('refresh_ocr_windows'),
        ],
    });
  }

  if (tickBlockReason === 'trigger_mode_after_advance_waiting_for_input') {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.waiting_advance.title', 'OCR 正在等待游戏推进'),
      body: uiT('ui.diag.waiting_advance.body', '当前为点击对白后识别模式，上一轮已经完成；点击、滚轮向下或按推进键后才会重新采集。'),
      actions: [
        diagnosisAction('focus_game'),
        diagnosisAction('debug_details'),
      ],
    });
  }

  if (tickBlockReason === 'memory_reader_recent_text') {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.memory_priority.title', '内存读取正在优先提供文本'),
      body: uiT('ui.diag.memory_priority.body', '自动模式检测到内存读取仍有近期文本，因此暂时不主动轮询 OCR。'),
      actions: [
        diagnosisAction('debug_details'),
      ],
    });
  }

  if (tickBlockReason) {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.ocr_tick_blocked.title', 'OCR 轮询暂未执行'),
      body: formatOcrTickBlockReason(tickBlockReason),
      actions: [
        diagnosisAction('refresh_all'),
        diagnosisAction('debug_details'),
      ],
    });
  }

  if (emitBlockReason === 'screen_classification_skipped_dialogue') {
    return diagnose({
      severity: 'warning',
      title: uiT('ui.diag.non_dialogue.title', 'OCR 画面被判定为非对白界面'),
      body: uiT('ui.diag.non_dialogue.body', '截图和识别已执行，但屏幕分类判断当前画面不适合写入对白。'),
      actions: [
        diagnosisAction('recalibrate_ocr'),
        diagnosisAction('debug_details'),
      ],
    });
  }

  if (['duplicate_stable_text', 'waiting_for_repeat', 'no_dialogue_text'].includes(emitBlockReason)) {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.no_new_line.title', 'OCR 已执行但没有新台词'),
      body: formatOcrEmitBlockReason(emitBlockReason),
      actions: [
        diagnosisAction('line_details'),
        diagnosisAction('debug_details'),
      ],
    });
  }

  if (
    detail === 'no_eligible_window'
    || (!hasEffectiveWindow && candidateCount === 0 && hasOcrRuntimeSignal)
  ) {
    return diagnose({
      severity: 'warning',
      title: uiT('ui.diag.no_window.title', '没找到能识别的游戏窗口'),
      body: uiT('ui.diag.no_window.body', '游戏可能未启动、被最小化，或当前窗口不是游戏。请确认游戏窗口可见后刷新。'),
      actions: [
        diagnosisAction('refresh_ocr_windows'),
        diagnosisAction('select_ocr_window'),
      ],
    });
  }

  if (detail === 'foreground_window_needs_manual_confirmation' || detail === 'auto_detect_needs_manual_fallback') {
    return diagnose({
      severity: 'warning',
      title: uiT('ui.diag.manual_window.title', '需要手动选择游戏窗口'),
      body: uiT('ui.diag.manual_window.body', '自动检测不够确定。手动选择一次可以避免识别到插件页面或其他窗口。'),
      actions: [
        diagnosisAction('select_ocr_window'),
        diagnosisAction('refresh_ocr_windows'),
      ],
    });
  }

  if (observedKey && observedKey !== stableKey) {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.observed_new.title', '刚读到新文字'),
      body: uiT('ui.diag.observed_new.body', '文字识别已经看到候选台词，正在确认这是不是同一句台词。'),
      actions: [
        diagnosisAction('line_details'),
      ],
    });
  }

  if (agentPauseKind === 'window_not_foreground' || agentUserStatus === 'paused_window_not_foreground') {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.not_foreground.title', '游戏不在前台'),
      body: uiT('ui.diag.not_foreground.body', '自动推进已暂停。切回游戏窗口后会继续，伴读信息仍会刷新。'),
      actions: [
        diagnosisAction('focus_game'),
      ],
    });
  }

  if (agentPauseKind === 'read_only' || agentUserStatus === 'read_only') {
    return diagnose({
      severity: 'info',
      title: uiT('ui.diag.read_only.title', '当前是伴读模式'),
      body: uiT('ui.diag.read_only.body', '会显示台词和建议，但不会自动点击。需要自动推进时请切换模式。'),
      actions: [
        diagnosisAction('choice_advisor'),
      ],
    });
  }

  if (effectiveText || stableText) {
    const target = formatOcrTargetForUser(status);
    return diagnose({
      severity: 'ok',
      title: uiT('ui.diag.recognizing.title', '正在识别台词'),
      body: target
        ? uiTf('ui.diag.recognizing.body_with_target', '当前目标：{target}。已读到台词，页面会持续刷新。', { target })
        : uiT('ui.diag.recognizing.body', '已读到台词，页面会持续刷新。'),
      actions: [
        diagnosisAction('refresh_all'),
      ],
    });
  }

  return diagnose({
    severity: 'info',
    title: uiT('ui.diag.waiting_game.title', '等待游戏状态'),
    body: status.summary || uiT('ui.diag.waiting_game.body', '暂时没有足够信息判断当前卡点。请先打开游戏，或刷新窗口列表。'),
    actions: [
      diagnosisAction('refresh_all'),
      diagnosisAction('select_ocr_window'),
    ],
  });
}

function renderPrimaryDiagnosis(status = {}) {
  const node = document.getElementById('primaryDiagnosisPanel');
  if (!node) {
    return;
  }
  const diagnosis = buildPrimaryDiagnosis(status);
  const kicker = document.getElementById('primaryDiagnosisKicker');
  const title = document.getElementById('primaryDiagnosisTitle');
  const body = document.getElementById('primaryDiagnosisBody');
  const actions = document.getElementById('primaryDiagnosisActions');
  node.className = `primary-diagnosis ${diagnosis.severity || 'info'}`;
  kicker.textContent = uiT('ui.diag.kicker', '运行诊断');
  title.textContent = diagnosis.title;
  body.textContent = diagnosis.body;
  actions.innerHTML = (diagnosis.actions || []).map((action, index) => `
    <button class="${index === 0 ? 'primary' : 'secondary'}" data-primary-action="${escapeHtml(action.id)}">
      ${escapeHtml(primaryActionLabel(action.id, action.label))}
    </button>
  `).join('');
}

function buildFirstRunSteps(status = {}) {
  const runtime = status.ocr_reader_runtime || {};
  const memoryRuntime = status.memory_reader_runtime || {};
  const snapshotWindows = latestOcrWindowSnapshot && Array.isArray(latestOcrWindowSnapshot.windows)
    ? latestOcrWindowSnapshot.windows
    : [];
  const availableGameIds = Array.isArray(status.available_game_ids) ? status.available_game_ids : [];
  const detail = textValue(runtime.target_selection_detail);
  const lastExcludeReason = textValue(runtime.last_exclude_reason);
  const hasGame = Boolean(
    textValue(status.active_session_id)
    || availableGameIds.length
    || Number(runtime.pid || 0)
    || textValue(runtime.process_name)
    || textValue(runtime.window_title)
    || Number(memoryRuntime.pid || 0)
    || textValue(memoryRuntime.process_name)
  );
  const hasWindow = Boolean(
    textValue(runtime.effective_window_key)
    || Number(runtime.candidate_count || 0) > 0
    || snapshotWindows.length > 0
  );
  const hasConfirmedWindow = Boolean(
    textValue(runtime.effective_window_key)
    && detail !== 'no_eligible_window'
    && detail !== 'memory_reader_window_minimized'
    && lastExcludeReason !== 'excluded_minimized_window'
  );
  const { observedText, stableText, effectiveText } = getCurrentLineTexts(status);
  const hasLine = Boolean(effectiveText || stableText || observedText);

  return [
    {
      done: hasGame,
      title: uiT('ui.first_run.start_game.title', '启动或恢复游戏'),
      body: hasGame
        ? uiT('ui.first_run.start_game.done', '已发现游戏状态。')
        : uiT('ui.first_run.start_game.pending', '打开游戏，并停在有文字的画面。'),
    },
    {
      done: hasWindow,
      title: uiT('ui.first_run.refresh_window.title', '刷新窗口'),
      body: hasWindow
        ? uiT('ui.first_run.refresh_window.done', '已找到可检查的窗口。')
        : uiT('ui.first_run.refresh_window.pending', '回到插件页，点击“刷新窗口”。'),
    },
    {
      done: hasConfirmedWindow,
      title: uiT('ui.first_run.select_window.title', '选择游戏窗口'),
      body: hasConfirmedWindow
        ? uiT('ui.first_run.select_window.done', '已确认识别窗口。')
        : uiT('ui.first_run.select_window.pending', '如果没有自动选中，请手动选择游戏窗口。'),
    },
    {
      done: hasLine,
      title: uiT('ui.first_run.recognize.title', '开始识别'),
      body: hasLine
        ? uiT('ui.first_run.recognize.done', '已读到台词。')
        : uiT('ui.first_run.recognize.pending', '开始自动识别，或在游戏中推进到下一句台词。'),
    },
  ];
}

function renderFirstRunGuide(status = {}) {
  const node = document.getElementById('firstRunGuide');
  const stepsNode = document.getElementById('firstRunSteps');
  if (!node || !stepsNode) {
    return;
  }
  const steps = buildFirstRunSteps(status);
  const allDone = steps.every((step) => step.done);
  const readyToStart = steps.slice(0, 3).every((step) => step.done);
  const advancedSettings = document.getElementById('advancedSettings');
  const advancedOpen = Boolean(advancedSettings && advancedSettings.classList.contains('open'));
  const completedOrReady = allDone || readyToStart;
  const shouldHide = advancedOpen || completedOrReady;
  node.hidden = shouldHide;
  if (completedOrReady) {
    stepsNode.replaceChildren();
    document.body.classList.remove('onboarding-active');
    const onboardingView = document.getElementById('onboardingView');
    if (onboardingView) { onboardingView.hidden = true; }
    return;
  }
  if (advancedOpen) {
    return;
  }
  const firstIncompleteIndex = steps.findIndex((step) => !step.done);
  stepsNode.innerHTML = steps.map((step, index) => {
    const stateClass = step.done ? 'done' : (index === firstIncompleteIndex ? 'active' : 'pending');
    const marker = step.done ? uiT('ui.first_run.done_marker', '完成') : String(index + 1);
    return `
      <article class="first-run-step ${stateClass}">
        <span class="first-run-step-marker">${escapeHtml(marker)}</span>
        <div>
          <h3>${escapeHtml(step.title)}</h3>
          <p>${escapeHtml(step.body)}</p>
        </div>
      </article>
    `;
  }).join('');

  const onboardingSteps = document.getElementById('onboardingSteps');
  const onboardingActions = document.getElementById('onboardingActions');
  if (onboardingSteps) {
    onboardingSteps.innerHTML = stepsNode.innerHTML;
  }
  if (onboardingActions) {
    const firstIncomplete = steps[firstIncompleteIndex];
    const actions = [];
    if (firstIncompleteIndex === 1) {
      actions.push(`<button class="primary" data-first-run-action="install_rapidocr">${escapeHtml(primaryActionLabel('install_rapidocr'))}</button>`);
    }
    if (!steps[2].done) {
      actions.push(`<button class="primary" data-first-run-action="select_ocr_window">${escapeHtml(primaryActionLabel('select_ocr_window'))}</button>`);
      actions.push(`<button class="secondary" data-first-run-action="refresh_ocr_windows">${escapeHtml(primaryActionLabel('refresh_ocr_windows'))}</button>`);
    }
    if (!steps[3].done && steps[2].done) {
      actions.push(`<button class="primary" data-first-run-action="choice_advisor">${escapeHtml(primaryActionLabel('start_recognition'))}</button>`);
      actions.push(`<button class="secondary" data-first-run-action="refresh_all">${escapeHtml(primaryActionLabel('refresh_status'))}</button>`);
    }
    onboardingActions.innerHTML = actions.join('');
  }
}

function renderCurrentLineOverview(status = {}) {
  const node = document.getElementById('currentLineOverview');
  if (!node) {
    return;
  }
  const runtime = status.ocr_reader_runtime || {};
  const title = document.getElementById('currentLineOverviewTitle');
  const statusChip = document.getElementById('currentLineOverviewStatus');
  const hint = document.getElementById('currentLineOverviewHint');
  const grid = document.getElementById('currentLineOverviewGrid');
  const { rawText, observedText, stableText } = getCurrentLineTexts(status);
  const displayStable = stableText;
  const observedKey = compactLineText(observedText);
  const stableKey = compactLineText(displayStable);
  const hasMismatch = Boolean(observedKey && observedKey !== stableKey);
  const blockReason = formatStableBlockReason(runtime.stable_ocr_block_reason);
  const repeatCount = Number(runtime.stable_ocr_repeat_count || 0);

  node.classList.toggle('waiting', !rawText && !observedText && !displayStable);
  statusChip.className = 'status-chip';
  if (hasMismatch) {
    title.textContent = uiT('ui.current_line.new_text_title', '刚读到新文字');
    statusChip.textContent = uiT('ui.current_line.confirming_status', '确认中');
    statusChip.classList.add('warning');
    hint.textContent = blockReason || (repeatCount
      ? uiTf('ui.current_line.confirming_repeat_hint', '正在确认这是不是同一句台词，已连续看到 {count} 次。', { count: repeatCount })
      : uiT('ui.current_line.confirming_hint', '正在确认这是不是同一句台词。'));
  } else if (displayStable) {
    title.textContent = uiT('ui.current_line.confirmed_title', '已确认当前台词');
    statusChip.textContent = uiT('ui.current_line.confirmed_status', '已确认');
    statusChip.classList.add('active');
    hint.textContent = uiT('ui.current_line.confirmed_hint', '这句台词已经进入正式上下文，后续建议会以它为基础更新。');
  } else if (rawText || observedText) {
    title.textContent = uiT('ui.current_line.filtering_title', '正在筛选识别结果');
    statusChip.textContent = uiT('ui.current_line.filtering_status', '筛选中');
    statusChip.classList.add('warning');
    hint.textContent = blockReason || uiT('ui.current_line.filtering_hint', '文字识别已有结果，但还没有写入正式台词。');
  } else {
    title.textContent = uiT('ui.current_line.waiting_result', '等待识别结果');
    statusChip.textContent = uiT('ui.current_line.waiting_refresh', '等待刷新');
    hint.textContent = buildOcrMissingLineDiagnostic(status);
  }

  const rows = [
    {
      label: uiT('ui.current_line.raw_ocr_label', '最新 OCR 原文'),
      value: rawText,
      empty: uiT('ui.current_line.no_raw_ocr', '还没有 OCR 原文'),
    },
    {
      label: uiT('ui.current_line.observed_label', '刚读到的候选台词'),
      value: observedText,
      empty: uiT('ui.current_line.no_observed', '还没有候选台词'),
    },
    {
      label: uiT('ui.current_line.stable_label', '已确认台词'),
      value: displayStable,
      empty: uiT('ui.current_line.no_stable', '还没有已确认台词'),
    },
  ];

  grid.innerHTML = rows.map((row) => `
    <article class="current-line-item${row.value ? '' : ' empty'}">
      <p class="list-kicker">${escapeHtml(row.label)}</p>
      <p>${escapeHtml(row.value || row.empty)}</p>
    </article>
  `).join('');

  const effectiveEl = document.getElementById('currentLineEffectiveText');
  const observedCollapse = document.getElementById('observedLinesCollapse');
  const observedContent = document.getElementById('observedLinesContent');

  if (effectiveEl) {
    const effectiveText = displayStable || observedText || rawText;
    if (effectiveText) {
      effectiveEl.textContent = effectiveText;
      effectiveEl.hidden = false;
    } else {
      effectiveEl.hidden = true;
    }
  }

  if (observedCollapse && observedContent) {
    const hasObserved = observedText && observedText !== displayStable;
    const hasRaw = rawText && rawText !== observedText && rawText !== displayStable;
    const observedLines = [];
    if (hasObserved) { observedLines.push(observedText); }
    if (hasRaw) { observedLines.push(rawText); }

    if (observedLines.length > 0 && displayStable) {
      observedCollapse.hidden = false;
      observedContent.innerHTML = observedLines.map((text) =>
        `<div class="observed-line-item">${escapeHtml(text)}</div>`
      ).join('');
    } else {
      observedCollapse.hidden = true;
    }
  }
}

function pipelineStateLabel(state) {
  const mapping = {
    ok: uiT('ui.pipeline_state.ok', '正常'),
    info: uiT('ui.pipeline_state.info', '等待'),
    warning: uiT('ui.pipeline_state.warning', '注意'),
    error: uiT('ui.pipeline_state.error', '异常'),
  };
  return uiDynamicT('ui.pipeline_state', state, mapping[state] || mapping.info);
}

function buildOcrPipelineSteps(status = {}) {
  const runtime = status.ocr_reader_runtime || {};
  const rapidocr = status.rapidocr || {};
  const tesseract = status.tesseract || {};
  const detail = textValue(runtime.target_selection_detail);
  const contextState = textValue(status.ocr_context_state || runtime.ocr_context_state);
  const lastExcludeReason = textValue(runtime.last_exclude_reason);
  const lastCaptureError = textValue(runtime.last_capture_error);
  const lastRejectedReason = textValue(runtime.last_rejected_ocr_reason);
  const { rawText, observedText, stableText } = getCurrentLineTexts(status);
  const displayStable = stableText;
  const observedKey = compactLineText(observedText);
  const stableKey = compactLineText(displayStable);
  const hasObservedMismatch = Boolean(observedKey && observedKey !== stableKey);
  const blockReason = formatStableBlockReason(runtime.stable_ocr_block_reason);
  const tickBlockReason = textValue(status.ocr_tick_block_reason || runtime.ocr_tick_block_reason);
  const emitBlockReason = textValue(status.ocr_emit_block_reason || runtime.ocr_emit_block_reason);
  const formattedTickBlockReason = formatOcrTickBlockReason(tickBlockReason);
  const formattedEmitBlockReason = formatOcrEmitBlockReason(emitBlockReason);
  const captureBackend = textValue(runtime.capture_backend_kind || status.ocr_capture_backend_selection || 'auto');
  const ocrBackend = textValue(runtime.backend_kind || status.ocr_backend_selection || 'auto');

  let windowStep = {
    key: 'window',
    state: 'info',
    title: uiT('ui.pipeline.window.waiting_title', '等待游戏窗口'),
    body: uiT('ui.pipeline.window.waiting_body', '等待目标窗口进入可识别状态。'),
    meta: detail ? formatOcrWindowSelectionDetail(detail) : '',
  };
  if (detail === 'memory_reader_window_minimized' || lastExcludeReason === 'excluded_minimized_window') {
    windowStep = {
      key: 'window',
      state: 'warning',
      title: uiT('ui.pipeline.window.minimized_title', '游戏窗口最小化'),
      body: uiT('ui.pipeline.window.needs_attention_body', '窗口阶段需要处理。'),
      meta: formatOcrWindowReason(lastExcludeReason || 'excluded_minimized_window'),
    };
  } else if (textValue(runtime.effective_window_key)) {
    windowStep = {
      key: 'window',
      state: 'ok',
      title: uiT('ui.pipeline.window.confirmed_title', '已确认游戏窗口'),
      body: uiT('ui.pipeline.window.ok_body', '窗口阶段正常。'),
      meta: runtime.target_is_foreground
        ? uiT('ui.pipeline.window.foreground_meta', '前台窗口')
        : uiT('ui.pipeline.window.not_foreground_meta', '非前台窗口'),
    };
  } else if (Number(runtime.candidate_count || 0) > 0) {
    windowStep = {
      key: 'window',
      state: 'info',
      title: uiT('ui.pipeline.window.candidate_title', '发现候选窗口'),
      body: uiT('ui.pipeline.window.waiting_confirm_body', '窗口阶段等待确认。'),
      meta: uiT('ui.pipeline.window.manual_if_needed_meta', '需要时可手动选择'),
    };
  } else if (detail === 'no_eligible_window' || Object.prototype.hasOwnProperty.call(runtime, 'candidate_count')) {
    windowStep = {
      key: 'window',
      state: 'warning',
      title: uiT('ui.pipeline.window.none_title', '没有可识别窗口'),
      body: uiT('ui.pipeline.window.needs_attention_body', '窗口阶段需要处理。'),
      meta: formatOcrWindowSelectionDetail(detail),
    };
  }

  let captureStep = {
    key: 'capture',
    state: 'info',
    title: uiT('ui.pipeline.capture.waiting_title', '等待截图'),
    body: uiT('ui.pipeline.capture.waiting_body', '截图阶段等待窗口确认。'),
    meta: captureBackend,
  };
  if (contextState === 'capture_failed' || lastCaptureError) {
    captureStep = {
      key: 'capture',
      state: 'error',
      title: uiT('ui.pipeline.capture.failed_title', '截图失败'),
      body: uiT('ui.pipeline.stage_error_body', '阶段异常，处理入口在运行诊断。'),
      meta: captureBackend,
    };
  } else if (runtime.stale_capture_backend) {
    captureStep = {
      key: 'capture',
      state: 'warning',
      title: uiT('ui.pipeline.capture.stale_title', '截图画面未更新'),
      body: uiT('ui.pipeline.capture.needs_attention_body', '截图阶段需要处理。'),
      meta: `${captureBackend}${runtime.consecutive_same_capture_frames ? ` | ${uiTf('ui.pipeline.capture.same_frames_meta', '连续 {count} 帧相同', { count: runtime.consecutive_same_capture_frames })}` : ''}`,
    };
  } else if (runtime.last_capture_completed_at || runtime.last_capture_image_hash || runtime.capture_backend_kind) {
    captureStep = {
      key: 'capture',
      state: 'ok',
      title: uiT('ui.pipeline.capture.available_title', '截图后端可用'),
      body: uiT('ui.pipeline.capture.ok_body', '截图阶段正常。'),
      meta: captureBackend,
    };
  }

  let ocrStep = {
    key: 'ocr',
    state: 'info',
    title: uiT('ui.pipeline.ocr.waiting_title', '等待文字识别'),
    body: uiT('ui.pipeline.ocr.waiting_body', '识别阶段等待截图输入。'),
    meta: ocrBackend,
  };
  if (!status.ocr_reader_enabled) {
    ocrStep = {
      key: 'ocr',
      state: 'warning',
      title: uiT('ui.pipeline.ocr.disabled_title', 'OCR Reader 未启用'),
      body: uiT('ui.pipeline.ocr.disabled_body', '识别阶段未启用。'),
      meta: ocrBackend,
    };
  } else if (tickBlockReason) {
    ocrStep = {
      key: 'ocr',
      state: ['ocr_reader_unavailable', 'ocr_reader_not_allowed', 'reader_mode_memory_only'].includes(tickBlockReason)
        ? 'warning'
        : 'info',
      title: uiT('ui.pipeline.ocr.tick_blocked_title', 'OCR 轮询暂未执行'),
      body: formattedTickBlockReason || uiT('ui.pipeline.ocr.tick_waiting_body', '轮询阶段等待条件满足。'),
      meta: ocrBackend,
    };
  } else if (runtime.backend_detail === 'backend_unavailable' || runtime.detail === 'backend_unavailable') {
    ocrStep = {
      key: 'ocr',
      state: 'error',
      title: uiT('ui.pipeline.ocr.backend_unavailable_title', 'OCR 后端不可用'),
      body: uiT('ui.pipeline.ocr.error_body', '识别阶段异常，处理入口在运行诊断。'),
      meta: ocrBackend,
    };
  } else if (runtime.detail === 'self_ui_guard_blocked' || lastRejectedReason) {
    ocrStep = {
      key: 'ocr',
      state: 'warning',
      title: uiT('ui.pipeline.ocr.rejected_title', 'OCR 已拒绝非游戏文本'),
      body: uiT('ui.pipeline.ocr.no_write_body', '识别阶段已执行但没有写入新台词。'),
      meta: lastRejectedReason || ocrBackend,
    };
  } else if (emitBlockReason) {
    ocrStep = {
      key: 'ocr',
      state: emitBlockReason === 'screen_classification_skipped_dialogue' ? 'warning' : 'info',
      title: uiT('ui.pipeline.ocr.executed_title', 'OCR 已执行'),
      body: formattedEmitBlockReason || uiT('ui.pipeline.ocr.executed_no_write_body', '识别已执行但没有写入新台词。'),
      meta: ocrBackend,
    };
  } else if (runtime.backend_kind || rawText || observedText || displayStable) {
    ocrStep = {
      key: 'ocr',
      state: 'ok',
      title: uiT('ui.pipeline.ocr.available_title', '文字识别可用'),
      body: uiT('ui.pipeline.ocr.ok_body', '识别阶段正常。'),
      meta: ocrBackend,
    };
  } else if (!rapidocr.installed && !tesseract.installed) {
    ocrStep = {
      key: 'ocr',
      state: 'warning',
      title: uiT('ui.pipeline.ocr.missing_components_title', 'OCR 组件可能缺失'),
      body: uiT('ui.pipeline.ocr.needs_attention_body', '识别阶段需要处理。'),
      meta: ocrBackend,
    };
  }

  const observedStep = observedText
    ? {
      key: 'observed',
      state: hasObservedMismatch ? 'warning' : 'ok',
      title: hasObservedMismatch
        ? uiT('ui.pipeline.observed.confirming_title', '候选台词确认中')
        : uiT('ui.pipeline.observed.read_title', '已读到候选台词'),
      body: hasObservedMismatch
        ? uiT('ui.pipeline.observed.confirming_body', '候选台词等待稳定确认。')
        : uiT('ui.pipeline.observed.ok_body', '候选台词读取正常。'),
      meta: hasObservedMismatch
        ? (formattedEmitBlockReason || blockReason || uiT('ui.pipeline.observed.waiting_stable_meta', '等待稳定确认'))
        : uiT('ui.pipeline.observed.same_as_stable_meta', '候选与已确认台词一致'),
    }
    : {
      key: 'observed',
      state: rawText ? 'info' : 'warning',
      title: rawText
        ? uiT('ui.pipeline.observed.filtering_raw_title', '正在筛选 OCR 原文')
        : uiT('ui.pipeline.observed.no_candidate_title', '还没有候选台词'),
      body: rawText
        ? uiT('ui.pipeline.observed.filtering_body', '候选阶段正在筛选。')
        : uiT('ui.pipeline.observed.waiting_text_body', '候选阶段等待有效文字。'),
      meta: formattedEmitBlockReason || blockReason || formattedTickBlockReason || '',
    };

  const stableStep = displayStable
    ? {
      key: 'stable',
      state: 'ok',
      title: uiT('ui.pipeline.stable.confirmed_title', '已确认台词'),
      body: uiT('ui.pipeline.stable.ok_body', '确认阶段正常。'),
      meta: status.effective_current_line?.source || runtime.last_stable_line?.source || '',
    }
    : {
      key: 'stable',
      state: observedText ? 'warning' : 'info',
      title: observedText
        ? uiT('ui.pipeline.stable.waiting_title', '等待稳定确认')
        : uiT('ui.pipeline.stable.no_stable_title', '还没有已确认台词'),
      body: observedText
        ? uiT('ui.pipeline.stable.waiting_body', '确认阶段等待稳定。')
        : uiT('ui.pipeline.stable.waiting_candidate_body', '确认阶段等待候选台词。'),
      meta: formattedEmitBlockReason || blockReason || formattedTickBlockReason || '',
    };

  let agentStep = {
    key: 'agent',
    state: 'info',
    title: uiT('ui.pipeline.agent.waiting_title', '等待 Agent 状态'),
    body: uiT('ui.pipeline.agent.waiting_body', 'Agent 会根据模式决定是否自动推进。'),
    meta: status.mode || '',
  };
  if (status.agent_user_status === 'error') {
    agentStep = {
      key: 'agent',
      state: 'error',
      title: uiT('ui.pipeline.agent.error_title', 'Agent 异常'),
      body: status.agent_reason || status.agent_diagnostic || uiT('ui.pipeline.agent.error_body', 'Agent 返回错误状态。'),
      meta: status.agent_pause_kind || '',
    };
  } else if (status.agent_pause_kind === 'window_not_foreground' || status.agent_user_status === 'paused_window_not_foreground') {
    agentStep = {
      key: 'agent',
      state: 'warning',
      title: uiT('ui.pipeline.agent.not_foreground_title', '游戏不在前台'),
      body: status.agent_pause_message || uiT('ui.pipeline.agent.not_foreground_body', '自动推进已暂停，切回游戏后继续。'),
      meta: agentUserStatusLabel(status.agent_user_status, status.agent_user_status || ''),
    };
  } else if (status.agent_pause_kind === 'screen_safety' || status.agent_user_status === 'screen_safety_pause') {
    agentStep = {
      key: 'agent',
      state: 'warning',
      title: uiT('ui.pipeline.agent.safety_pause_title', '安全暂停'),
      body: status.agent_pause_message || uiT('ui.pipeline.agent.safety_pause_body', '当前画面不适合自动推进。'),
      meta: agentUserStatusLabel(status.agent_user_status, status.agent_user_status || ''),
    };
  } else if (status.agent_pause_kind === 'read_only' || status.agent_user_status === 'read_only') {
    agentStep = {
      key: 'agent',
      state: 'info',
      title: uiT('ui.pipeline.agent.read_only_title', '伴读模式'),
      body: uiT('ui.pipeline.agent.read_only_body', '会显示台词和建议，但不会自动点击。'),
      meta: agentUserStatusLabel(status.agent_user_status, ''),
    };
  } else if (status.agent_user_status || status.agent_status) {
    agentStep = {
      key: 'agent',
      state: 'ok',
      title: uiT('ui.pipeline.agent.ok_title', 'Agent 状态正常'),
      body: status.agent_activity || status.agent_reason || uiT('ui.pipeline.agent.ok_body', '按当前模式运行。'),
      meta: agentUserStatusLabel(status.agent_user_status, status.agent_user_status || status.agent_status || ''),
    };
  }

  return [windowStep, captureStep, ocrStep, observedStep, stableStep, agentStep];
}

function renderOcrPipelinePanel(status = {}) {
  const node = document.getElementById('ocrPipelinePanel');
  const stepsNode = document.getElementById('ocrPipelineSteps');
  const summaryNode = document.getElementById('ocrPipelineSummary');
  if (!node || !stepsNode || !summaryNode) {
    return;
  }
  const steps = buildOcrPipelineSteps(status);
  const worstState = steps.some((step) => step.state === 'error')
    ? 'error'
    : steps.some((step) => step.state === 'warning')
      ? 'warning'
      : steps.every((step) => step.state === 'ok')
        ? 'ok'
        : 'info';
  summaryNode.className = `status-chip ${worstState === 'ok' ? 'active' : worstState}`;
  summaryNode.textContent = worstState === 'ok'
    ? uiT('ui.ocr.pipeline.summary_ok', '链路正常')
    : worstState === 'error'
      ? uiT('ui.ocr.pipeline.summary_error', '链路异常')
      : worstState === 'warning'
        ? uiT('ui.ocr.pipeline.summary_warning', '需要处理')
        : uiT('ui.ocr.pipeline.summary_waiting', '等待状态');
  stepsNode.innerHTML = steps.map((step) => `
    <article class="ocr-pipeline-step ${escapeHtml(step.state)}">
      <span class="ocr-pipeline-dot">${escapeHtml(pipelineStateLabel(step.state))}</span>
      <div>
        <p class="list-kicker">${escapeHtml(step.key)}</p>
        <h3>${escapeHtml(step.title)}</h3>
        <p>${escapeHtml(step.body || '')}</p>
        ${step.meta ? `<p class="result-note">${escapeHtml(step.meta)}</p>` : ''}
      </div>
    </article>
  `).join('');
}

function installTaskDisplayState(kind) {
  const state = getInstallState(kind);
  const task = state && state.state ? state.state : {};
  if (state?.inProgress) {
    return {
      state: 'running',
      labelText: uiT('ui.install.summary.installing', '安装中'),
      needsAttention: true,
    };
  }
  if (task.status === 'failed') {
    return {
      state: 'failed',
      labelText: uiT('ui.install.summary.failed', '安装失败'),
      needsAttention: true,
    };
  }
  if (task.status === 'completed') {
    return {
      state: 'installed',
      labelText: uiT('ui.install.summary.installed', '已安装'),
      needsAttention: false,
    };
  }
  return null;
}

function dependencySummaryItem(kind, status = {}) {
  const taskState = installTaskDisplayState(kind);
  if (taskState) {
    return {
      kind,
      label: getInstallConfig(kind).label,
      ...taskState,
    };
  }

  // rapidocr / dxcam used to have install-summary branches here; both are now
  // bundled main-program deps, so they're not in OCR_INSTALL_TABS and this
  // function is never called with those kinds. Their status is shown
  // exclusively via the always-visible rapidocrPrompt / dxcamPrompt banners.

  if (kind === 'tesseract') {
    const tesseract = status.tesseract || {};
    const missingLanguages = Array.isArray(tesseract.missing_languages) ? tesseract.missing_languages : [];
    if (tesseract.installed && !missingLanguages.length) {
      return { kind, label: 'Tesseract', state: 'installed', labelText: uiT('ui.install.summary.installed', '已安装'), needsAttention: false };
    }
    if (tesseract.installed && missingLanguages.length) {
      return {
        kind,
        label: 'Tesseract',
        state: 'warning',
        labelText: uiTf('ui.install.summary.missing_languages', '缺少语言包 {languages}', { languages: missingLanguages.join(', ') }),
        needsAttention: true,
      };
    }
    return { kind, label: 'Tesseract', state: 'missing', labelText: uiT('ui.install.summary.missing', '未安装'), needsAttention: true };
  }

  const textractor = status.textractor || {};
  return textractor.installed
    ? { kind, label: 'Textractor', state: 'installed', labelText: uiT('ui.install.summary.installed', '已安装'), needsAttention: false }
    : { kind, label: 'Textractor', state: 'optional', labelText: uiT('ui.install.summary.missing_optional', '未安装（可选）'), needsAttention: false };
}

function renderInstallCompactSummary(status = {}) {
  const summary = document.getElementById('installCompactSummary');
  if (!summary) {
    return;
  }
  const ocrItems = OCR_INSTALL_TABS.map((kind) => dependencySummaryItem(kind, status));
  const memoryItems = ['textractor'].map((kind) => dependencySummaryItem(kind, status));
  const renderGroup = (label, items) => `
    <span class="install-summary-group">
      <span class="install-summary-label">${escapeHtml(label)}</span>
      ${items.map((item) => `
        <span class="install-summary-chip ${escapeHtml(item.state || 'neutral')}">
          ${escapeHtml(item.label)} ${escapeHtml(item.labelText || item.label || '')}
        </span>
      `).join('')}
    </span>
  `;
  summary.innerHTML = [
    renderGroup('OCR', ocrItems),
    renderGroup(uiT('ui.install.summary.memory_group', '内存'), memoryItems),
  ].join('');
}

function formatOcrTargetForUser(status = {}) {
  const runtime = status.ocr_reader_runtime || {};
  const processName = runtime.process_name || runtime.effective_process_name || '';
  const title = runtime.window_title || runtime.effective_window_title || '';
  const pid = Number(runtime.pid || 0);
  const parts = [];
  if (processName) {
    parts.push(processName);
  }
  if (title) {
    parts.push(title);
  }
  if (pid) {
    parts.push(`pid ${pid}`);
  }
  return parts.join(' / ');
}

function syncAgentResumeButton(status = {}) {
  const button = document.getElementById('standbyOffBtn');
  const userStatus = status.agent_user_status || '';
  const pauseKind = status.agent_pause_kind || '';
  button.disabled = false;
  if (status.agent_can_resume_by_button || userStatus === 'paused_by_user') {
    button.textContent = uiT('ui.button.standby_off', '恢复活跃');
    button.dataset.resumeAction = 'standby';
  } else if (pauseKind === 'window_not_foreground' || userStatus === 'paused_window_not_foreground') {
    button.textContent = uiT('ui.agent.resume.focus_game', '请切回游戏窗口');
    button.dataset.resumeAction = 'focus';
  } else if (pauseKind === 'read_only' || userStatus === 'read_only') {
    button.textContent = uiT('ui.agent.resume.read_only', '只读模式');
    button.dataset.resumeAction = 'read_only';
  } else {
    button.textContent = uiT('ui.button.standby_off', '恢复活跃');
    button.dataset.resumeAction = 'noop';
  }
}

function isSettingsControlElement(element) {
  return Boolean(element && SETTINGS_CONTROL_IDS.has(element.id || ''));
}

function shouldPreserveSettingsControls() {
  return settingsDirty || settingsSaveInFlight || isSettingsControlElement(document.activeElement);
}

function syncSettingsValue(id, value) {
  if (shouldPreserveSettingsControls()) {
    return;
  }
  const node = document.getElementById(id);
  if (node) {
    node.value = value;
  }
}

function syncSettingsChecked(id, checked) {
  if (shouldPreserveSettingsControls()) {
    return;
  }
  const node = document.getElementById(id);
  if (node) {
    node.checked = Boolean(checked);
  }
}

function renderAgentUserNotice(status = {}) {
  const node = document.getElementById('agentUserNotice');
  const title = document.getElementById('agentUserNoticeTitle');
  const body = document.getElementById('agentUserNoticeBody');
  const target = document.getElementById('agentUserNoticeTarget');
  const userStatus = status.agent_user_status || '';
  const pauseKind = status.agent_pause_kind || 'none';
  const label = agentUserStatusLabel(userStatus, userStatus || uiT('ui.agent.waiting_status', '等待状态'));
  const targetText = formatOcrTargetForUser(status);
  const mode = status.mode || '';
  const waitingInAutoMode = userStatus === 'read_only' && mode === 'choice_advisor';
  const displayLabel = waitingInAutoMode ? uiT('ui.agent.waiting_actionable_status', '等待可操作状态') : label;
  const displayPauseMessage = waitingInAutoMode && !status.agent_pause_message
    ? uiT('ui.agent.notice.waiting_actionable', '自动推进已开启，正在等待游戏会话、OCR 台词或目标窗口进入可操作状态。')
    : status.agent_pause_message;

  node.hidden = false;
  title.textContent = displayLabel;
  body.textContent = displayPauseMessage
    || (userStatus === 'read_only' && status.mode === 'companion'
      ? uiT('ui.agent.notice.companion_no_advance', '游戏窗口已在前台，但伴读模式不会自动推进。需要自动推进时请切到自动推进模式。')
      : '')
    || (userStatus === 'running' && status.mode === 'choice_advisor'
      ? uiT('ui.agent.notice.auto_advance_running', '游戏窗口已在前台，Agent 会按自动推进模式继续。OCR 会在后台持续刷新。')
      : '')
    || (userStatus === 'running'
      ? uiT('ui.agent.notice.running', 'Agent 正在按当前模式运行。OCR 会在后台持续刷新。')
      : uiT('ui.agent.notice.default', 'Agent 状态会随游戏窗口、OCR 和模式设置自动更新。'));
  target.textContent = targetText ? uiTf('ui.agent.notice.target_window', '目标窗口：{target}', { target: targetText }) : '';

  node.className = 'agent-user-notice neutral';
  if (
    pauseKind === 'window_not_foreground'
    || pauseKind === 'screen_safety'
    || userStatus === 'paused_window_not_foreground'
    || userStatus === 'screen_safety_pause'
  ) {
    node.classList.add('warning');
  } else if (pauseKind === 'ocr_unavailable' || userStatus === 'ocr_unavailable' || userStatus === 'error') {
    node.classList.add('error');
  } else if (pauseKind === 'read_only' || pauseKind === 'user' || userStatus === 'read_only' || userStatus === 'paused_by_user') {
    node.classList.add('read-only');
  }
}

function buildOcrMissingLineDiagnostic(status = {}) {
  const runtime = status.ocr_reader_runtime || {};
  const rapidocr = status.rapidocr || {};
  const parts = [
    status.ocr_capture_diagnostic_required
      ? uiT('ui.ocr.missing_line.capture_target_abnormal', 'OCR 截图区/窗口目标可能异常')
      : uiT('ui.ocr.missing_line.no_available_line', 'OCR 尚未读到可用台词'),
  ];
  if (status.ocr_capture_diagnostic) {
    parts.push(status.ocr_capture_diagnostic);
  }
  if (status.agent_reason) {
    parts.push(`agent_reason=${status.agent_reason}`);
  }
  if (status.agent_diagnostic) {
    parts.push(status.agent_diagnostic);
  }
  if (runtime.status) {
    parts.push(`status=${runtime.status}`);
  }
  if (runtime.detail) {
    parts.push(`detail=${runtime.detail}`);
  }
  if (runtime.ocr_context_state) {
    parts.push(`context_state=${runtime.ocr_context_state}`);
  }
  const tickBlockReason = textValue(status.ocr_tick_block_reason || runtime.ocr_tick_block_reason);
  if (tickBlockReason) {
    parts.push(`tick_block=${formatOcrTickBlockReason(tickBlockReason)}`);
  }
  const emitBlockReason = textValue(status.ocr_emit_block_reason || runtime.ocr_emit_block_reason);
  if (emitBlockReason) {
    parts.push(`emit_block=${formatOcrEmitBlockReason(emitBlockReason)}`);
  }
  const readerAllowedBlockReason = textValue(
    status.ocr_reader_allowed_block_reason || runtime.ocr_reader_allowed_block_reason,
  );
  if (readerAllowedBlockReason) {
    parts.push(`reader_block=${formatOcrTickBlockReason(readerAllowedBlockReason)}`);
  }
  const displaySourceNotOcrReason = textValue(
    status.display_source_not_ocr_reason || runtime.display_source_not_ocr_reason,
  );
  if (displaySourceNotOcrReason) {
    parts.push(`display_source=${displaySourceNotOcrReason}`);
  }
  if (runtime.backend_kind) {
    parts.push(`backend=${runtime.backend_kind}`);
  }
  if (runtime.backend_detail) {
    parts.push(`backend_detail=${runtime.backend_detail}`);
  }
  if (typeof status.bridge_poll_running === 'boolean') {
    parts.push(`bridge_poll_running=${status.bridge_poll_running}`);
  }
  if (typeof status.bridge_poll_inflight_seconds === 'number') {
    parts.push(`bridge_poll_inflight=${status.bridge_poll_inflight_seconds.toFixed(1)}s`);
  }
  if (typeof status.last_bridge_poll_duration_seconds === 'number') {
    parts.push(`last_poll_duration=${status.last_bridge_poll_duration_seconds.toFixed(1)}s`);
  }
  if (typeof status.pending_ocr_advance_captures === 'number' && status.pending_ocr_advance_captures > 0) {
    parts.push(`pending_ocr=${status.pending_ocr_advance_captures}`);
  }
  if (status.last_ocr_advance_capture_reason) {
    parts.push(`ocr_reason=${status.last_ocr_advance_capture_reason}`);
  }
  if (status.last_error?.message) {
    parts.push(`last_error=${status.last_error.message}`);
  }
  if (rapidocr.detail && rapidocr.detail !== 'installed') {
    parts.push(`rapidocr=${rapidocr.detail}`);
  }
  if (runtime.capture_stage) {
    parts.push(`stage=${runtime.capture_stage}`);
  }
  if (runtime.capture_profile) {
    parts.push(`capture=${formatCaptureProfile(runtime.capture_profile)}`);
  }
  if (runtime.consecutive_no_text_polls) {
    parts.push(`no_text_polls=${runtime.consecutive_no_text_polls}`);
  }
  if (runtime.last_observed_at) {
    parts.push(`last_observed_at=${runtime.last_observed_at}`);
  }
  if (runtime.last_capture_error) {
    parts.push(`last_capture_error=${runtime.last_capture_error}`);
  }
  if (runtime.last_raw_ocr_text) {
    parts.push(`last_raw=${String(runtime.last_raw_ocr_text).slice(0, 80)}`);
  }
  if (runtime.last_rejected_ocr_reason) {
    parts.push(`rejected=${runtime.last_rejected_ocr_reason}`);
  }
  if (runtime.last_rejected_ocr_text) {
    parts.push(`last_rejected=${String(runtime.last_rejected_ocr_text).slice(0, 80)}`);
  }
  return parts.join(' | ');
}

function normalizeLineText(value = '') {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function formatOcrTriggerMode(value = '') {
  return value === 'after_advance'
    ? uiT('ui.settings.trigger_after_advance', '点击对白后识别')
    : uiT('ui.settings.trigger_interval', '按间隔识别');
}

function formatFixedNumber(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : '0.0';
}

function isLikelyGameDialogueLine(item = {}) {
  if (!item || item.is_diagnostic) {
    return false;
  }
  const text = normalizeLineText(item.text || '');
  if (!text || text.length < 2 || text.length > 220) {
    return false;
  }
  const lowered = text.toLowerCase();
  const blockedTokens = [
    'agent',
    'capture_failed',
    'context_state=',
    'dxcam:',
    'galgame_',
    'gateway_unavailable',
    'http://',
    'https://',
    'last_error=',
    'ocr_context_unavailable',
    'plugin/',
    'plugin\\',
    'powershell',
    'status=',
    'stability',
    '当前快照',
    '场景 id',
    '场景id',
    '会话 id',
    '会话id',
    '游戏 id',
    '游戏id',
    '菜单是否打开',
    '台词 id',
    '台词id',
    '路线 id',
    '路线id',
    '快照时间',
    '是否过期',
    '退出全屏',
    '收起',
    '全屏',
    'ocr 诊断',
    'recent raw ocr',
    '最近 raw ocr',
  ];
  if (blockedTokens.some((token) => lowered.includes(token))) {
    return false;
  }
  if (text.startsWith('{') || text.startsWith('[') || (text.includes('{') && text.includes('}'))) {
    return false;
  }
  const hasDialoguePunctuation = /[。！？!?…]|——|「|」|『|』|“|”/.test(text);
  const hasWeakDialoguePunctuation = /[，,、：:]/.test(text);
  const hasSpeaker = Boolean(String(item.speaker || '').trim());
  if (hasSpeaker || hasDialoguePunctuation) {
    return true;
  }
  return hasWeakDialoguePunctuation && text.replace(/\s+/g, '').length >= 8;
}

function lineKey(item = {}) {
  const text = normalizeLineText(item.text || '');
  if (text) {
    return [
      item.scene_id || '',
      item.speaker || '',
      text,
    ].join('::');
  }
  return String(item.line_id || '').trim();
}

function mergedHistoryLines(history = {}) {
  const merged = new Map();
  (history.observed_lines || []).forEach((item) => {
    if (!isLikelyGameDialogueLine(item)) {
      return;
    }
    merged.set(lineKey(item), { ...item, stability: item.stability || 'tentative' });
  });
  (history.stable_lines || []).forEach((item) => {
    if (!isLikelyGameDialogueLine(item)) {
      return;
    }
    merged.set(lineKey(item), { ...item, stability: item.stability || 'stable' });
  });
  return Array.from(merged.values());
}

function maxScrollTop(node) {
  if (!node) {
    return 0;
  }
  return Math.max(0, Number(node.scrollHeight || 0) - Number(node.clientHeight || 0));
}

function setScrollPosition(node, top, left = 0) {
  if (!node) {
    return;
  }
  node.scrollTop = Math.min(Math.max(0, Number(top || 0)), maxScrollTop(node));
  if ('scrollLeft' in node) {
    node.scrollLeft = Math.max(0, Number(left || 0));
  }
}

function restoreScrollPosition(node, top, left = 0) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      setScrollPosition(node, top, left);
    });
  });
}

function captureRefreshScrollState(root = document) {
  const entries = [];
  const seen = new Set();
  const addNode = (node) => {
    if (!node || seen.has(node)) {
      return;
    }
    seen.add(node);
    entries.push({
      node,
      top: Number(node.scrollTop || 0),
      left: Number(node.scrollLeft || 0),
    });
  };
  addNode(document.scrollingElement || document.documentElement || document.body);
  root.querySelectorAll?.('.scroll-region, .reply-text-scroll').forEach(addNode);
  if (root.classList?.contains('panel-fullscreen')) {
    addNode(root);
  }
  return entries;
}

function restoreRefreshScrollState(entries) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      (entries || []).forEach((entry) => {
        setScrollPosition(entry.node, entry.top, entry.left);
      });
    });
  });
}

function renderPreservingScroll(node, render) {
  if (!node) {
    return;
  }
  const previousTop = Number(node.scrollTop || 0);
  const previousLeft = Number(node.scrollLeft || 0);
  render();
  restoreScrollPosition(node, previousTop, previousLeft);
}

function isScrollableNode(node) {
  return Boolean(node && node.scrollHeight > node.clientHeight + 1);
}

function canScrollNode(node, deltaY) {
  if (!isScrollableNode(node)) {
    return false;
  }
  if (deltaY < 0) {
    return node.scrollTop > 0;
  }
  if (deltaY > 0) {
    return node.scrollTop + node.clientHeight < node.scrollHeight - 1;
  }
  return true;
}

function eventElement(eventTarget) {
  if (eventTarget instanceof Element) {
    return eventTarget;
  }
  return eventTarget?.parentElement || null;
}

function fullscreenWheelTarget(eventTarget, deltaY) {
  const element = eventElement(eventTarget);
  const panel = element?.closest?.('.panel-fullscreen');
  if (!panel) {
    return null;
  }
  let node = element;
  while (node && node !== panel.parentElement) {
    if (
      node.matches?.('.scroll-region, .reply-text-scroll, .module-body, .list-card, .panel-fullscreen')
      && canScrollNode(node, deltaY)
    ) {
      return node;
    }
    if (node === panel) {
      break;
    }
    node = node.parentElement;
  }
  const nested = Array.from(panel.querySelectorAll('.scroll-region, .reply-text-scroll, .module-body, .list-card'))
    .find((candidate) => canScrollNode(candidate, deltaY));
  return nested || (canScrollNode(panel, deltaY) ? panel : null);
}

function exitPanelFullscreen() {
  document.querySelectorAll('.panel-fullscreen').forEach((panel) => {
    panel.classList.remove('panel-fullscreen');
    const button = panel.querySelector('.panel-fullscreen-toggle');
    if (button) {
      button.textContent = uiT('ui.button.fullscreen', '全屏');
      button.setAttribute('aria-label', uiT('ui.button.fullscreen', '全屏'));
    }
  });
  document.body.classList.remove('panel-fullscreen-active');
}

function togglePanelFullscreen(panel) {
  if (!panel) {
    return;
  }
  const isActive = panel.classList.contains('panel-fullscreen');
  exitPanelFullscreen();
  if (isActive) {
    return;
  }
  panel.open = true;
  panel.classList.add('panel-fullscreen');
  document.body.classList.add('panel-fullscreen-active');
  const button = panel.querySelector('.panel-fullscreen-toggle');
  if (button) {
    button.textContent = uiT('ui.button.exit_fullscreen', '退出全屏');
    button.setAttribute('aria-label', uiT('ui.button.exit_fullscreen', '退出全屏'));
  }
}

function initializePanelFullscreenControls() {
  document.querySelectorAll('.dashboard-module, .settings-module').forEach((panel) => {
    const summary = panel.querySelector(':scope > summary');
    if (!summary || summary.querySelector('.panel-fullscreen-toggle')) {
      return;
    }
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'panel-fullscreen-toggle';
    button.textContent = uiT('ui.button.fullscreen', '全屏');
    button.setAttribute('aria-label', uiT('ui.button.fullscreen', '全屏'));
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      togglePanelFullscreen(panel);
    });
    summary.appendChild(button);
  });
}

function latestHistoryLine(history = {}) {
  const lines = mergedHistoryLines(history);
  return lines.length ? lines[lines.length - 1] : null;
}

function effectiveCurrentLine(snapshot = {}, history = {}, status = {}) {
  const state = snapshot.snapshot || {};
  if (state.line_id && state.text) {
    return { ...state, source: 'snapshot', stability: state.stability || '' };
  }
  if (snapshot.effective_current_line?.line_id && snapshot.effective_current_line?.text) {
    return { ...snapshot.effective_current_line };
  }
  if (status.effective_current_line?.line_id && status.effective_current_line?.text) {
    return { ...status.effective_current_line };
  }
  return latestHistoryLine(history) || {};
}

function buildSuggestFallback(sceneId = '', diagnostic = 'no visible choices') {
  return {
    degraded: true,
    scene_id: sceneId,
    choices: [],
    diagnostic,
  };
}

function renderGrid(nodeId, rows) {
  const container = document.getElementById(nodeId);
  renderPreservingScroll(container, () => {
    container.innerHTML = renderDataRows(rows);
  });
}

function renderDataRows(rows) {
  return rows.map((row) => `
    <div class="data-row">
      <dt>${escapeHtml(fieldLabel(row.label))}</dt>
      <dd>${escapeHtml(row.value)}</dd>
    </div>
  `).join('');
}

function renderStatusGrid(rows, debugRows) {
  const container = document.getElementById('statusGrid');
  const debugWasOpen = Boolean(container?.querySelector('.status-debug-panel')?.open);
  renderPreservingScroll(container, () => {
    container.innerHTML = `
      <dl class="data-grid status-grid-primary">
        ${renderDataRows(rows)}
      </dl>
      <details class="status-debug-panel"${debugWasOpen ? ' open' : ''}>
        <summary>
          <span>${escapeHtml(uiT('ui.status.debug_title', '高级调试'))}</span>
          <small>${escapeHtml(uiTf('ui.status.debug_count', '{count} 项内部状态', { count: debugRows.length }))}</small>
        </summary>
        <dl class="data-grid">
          ${renderDataRows(debugRows)}
        </dl>
      </details>
    `;
  });
}

function renderStackList(nodeId, items, formatter) {
  const node = document.getElementById(nodeId);
  if (!items.length) {
    renderPreservingScroll(node, () => {
      node.className = 'stack-list scroll-region empty-state';
      node.textContent = uiT('ui.empty.no_data', '暂无数据');
    });
    return;
  }
  renderPreservingScroll(node, () => {
    node.className = 'stack-list scroll-region';
    node.innerHTML = items.map(formatter).join('');
  });
}

function isInstallTaskTerminal(state) {
  return Boolean(state) && INSTALL_TERMINAL_STATUSES.has(String(state.status || ''));
}

function shouldRestoreInstallTaskState(state) {
  return Boolean(state) && !isInstallTaskTerminal(state);
}

function installStatusPriority(status) {
  const normalized = String(status || '').trim();
  if (normalized === 'completed') {
    return 3;
  }
  if (normalized === 'failed') {
    return 2;
  }
  if (normalized === 'canceled') {
    return 1;
  }
  return 0;
}

function selectPreferredInstallState(primary, secondary) {
  if (!primary) {
    return secondary;
  }
  if (!secondary) {
    return primary;
  }

  const primaryTerminal = isInstallTaskTerminal(primary);
  const secondaryTerminal = isInstallTaskTerminal(secondary);
  if (primaryTerminal !== secondaryTerminal) {
    return primaryTerminal ? secondary : primary;
  }

  const primaryUpdated = Number(primary.updated_at || primary.started_at || 0);
  const secondaryUpdated = Number(secondary.updated_at || secondary.started_at || 0);
  if (primaryUpdated !== secondaryUpdated) {
    return primaryUpdated >= secondaryUpdated ? primary : secondary;
  }

  const primaryPriority = installStatusPriority(primary.status);
  const secondaryPriority = installStatusPriority(secondary.status);
  if (primaryPriority !== secondaryPriority) {
    return primaryPriority >= secondaryPriority ? primary : secondary;
  }

  return primary;
}

function persistInstallTaskId(kind, taskId) {
  if (!taskId) {
    return;
  }
  try {
    localStorage.setItem(getInstallConfig(kind).storageKey, taskId);
  } catch (_) {
    // Ignore storage failures in embedded browsers.
  }
}

function readPersistedInstallTaskId(kind) {
  try {
    return localStorage.getItem(getInstallConfig(kind).storageKey) || '';
  } catch (_) {
    return '';
  }
}

function clearPersistedInstallTaskId(kind) {
  try {
    localStorage.removeItem(getInstallConfig(kind).storageKey);
  } catch (_) {
    // Ignore storage failures in embedded browsers.
  }
}

function clearInstallReconnectTimer(kind) {
  const state = getInstallState(kind);
  if (state.reconnectTimer) {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
  }
}

function closeInstallStream(kind) {
  const state = getInstallState(kind);
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const digits = size >= 100 || unitIndex === 0 ? 0 : 1;
  return `${size.toFixed(digits)} ${units[unitIndex]}`;
}

function formatInstallPhase(phase) {
  const normalized = String(phase || '').trim();
  const mapping = {
    queued: uiT('ui.install.phase.queued', '排队中'),
    metadata: uiT('ui.install.phase.metadata', '获取安装信息'),
    downloading: uiT('ui.install.phase.downloading', 'HTTPS 下载中'),
    installing: uiT('ui.install.phase.installing', '安装器执行中'),
    extracting: uiT('ui.install.phase.extracting', '解压安装中'),
    languages: uiT('ui.install.phase.languages', '下载语言包中'),
    verifying: uiT('ui.install.phase.verifying', '校验安装中'),
    completed: uiT('ui.install.phase.completed', '安装完成'),
    failed: uiT('ui.install.phase.failed', '安装失败'),
    canceled: uiT('ui.install.phase.canceled', '已取消'),
  };
  return uiDynamicT('ui.install.phase', normalized, mapping[normalized] || normalized || uiT('ui.install.phase.waiting', '等待中'));
}

function formatCaptureProfile(profile) {
  const source = profile || {};
  const rows = [
    ['left', source.left_inset_ratio],
    ['right', source.right_inset_ratio],
    ['top', source.top_ratio],
    ['bottom', source.bottom_inset_ratio],
  ].filter(([, value]) => typeof value === 'number' && Number.isFinite(value));
  if (!rows.length) {
    return '';
  }
  return rows.map(([label, value]) => `${label}=${Number(value).toFixed(2)}`).join(' | ');
}

function normalizeProcessName(value) {
  return String(value || '').trim().toLowerCase();
}

function normalizeCaptureProfileSaveScope(value) {
  const normalized = String(value || '').trim();
  return normalized === 'window_bucket' ? 'window_bucket' : 'process_fallback';
}

function normalizeCaptureProfileBucketKey(value) {
  return String(value || '').trim().toLowerCase();
}

function buildCaptureProfileBucketKey(width, height) {
  const normalizedWidth = Math.max(0, Number(width || 0));
  const normalizedHeight = Math.max(0, Number(height || 0));
  if (!normalizedWidth || !normalizedHeight) {
    return '';
  }
  return `${Math.round(normalizedWidth)}x${Math.round(normalizedHeight)}`;
}

function isRatioProfileValue(value) {
  return Boolean(value)
    && typeof value === 'object'
    && ['left_inset_ratio', 'right_inset_ratio', 'top_ratio', 'bottom_inset_ratio']
      .every((key) => typeof value[key] === 'number' && Number.isFinite(value[key]));
}

function isAihongProcessName(value) {
  return AIHONG_PROCESS_NAMES.has(normalizeProcessName(value));
}

function findStoredCaptureProfileEntry(status, processName) {
  const profiles = status?.ocr_capture_profiles || {};
  const direct = profiles[processName];
  if (direct) {
    return direct;
  }
  const normalizedProcessName = normalizeProcessName(processName);
  return Object.entries(profiles).find(([name]) => normalizeProcessName(name) === normalizedProcessName)?.[1] || null;
}

function resolveStoredFallbackCaptureProfile(entry, stage) {
  if (!entry || typeof entry !== 'object') {
    return null;
  }
  if (isRatioProfileValue(entry)) {
    return entry;
  }
  const stageEntry = entry[stage];
  if (isRatioProfileValue(stageEntry)) {
    return stageEntry;
  }
  const defaultEntry = entry.default;
  if (isRatioProfileValue(defaultEntry)) {
    return defaultEntry;
  }
  return null;
}

function resolveStoredBucketCaptureProfile(entry, stage, bucketKey) {
  if (!entry || typeof entry !== 'object' || !bucketKey) {
    return null;
  }
  const buckets = entry.__window_buckets__;
  if (!buckets || typeof buckets !== 'object') {
    return null;
  }
  const normalizedBucketKey = normalizeCaptureProfileBucketKey(bucketKey);
  const directBucket = buckets[normalizedBucketKey];
  const bucketEntry = directBucket
    || Object.entries(buckets).find(([key]) => normalizeCaptureProfileBucketKey(key) === normalizedBucketKey)?.[1]
    || null;
  if (!bucketEntry || typeof bucketEntry !== 'object') {
    return null;
  }
  const bucketStages = bucketEntry.stages;
  if (!bucketStages || typeof bucketStages !== 'object') {
    return null;
  }
  const stageEntry = bucketStages[stage];
  if (isRatioProfileValue(stageEntry)) {
    return stageEntry;
  }
  const defaultEntry = bucketStages.default;
  if (isRatioProfileValue(defaultEntry)) {
    return defaultEntry;
  }
  return null;
}

function resolveRuntimeDefaultSaveScope(status, processName) {
  const runtime = status?.ocr_reader_runtime || {};
  return normalizeProcessName(processName)
    && normalizeProcessName(processName) === normalizeProcessName(runtime.process_name)
    && Number(runtime.width || 0) > 0
    && Number(runtime.height || 0) > 0
    ? 'window_bucket'
    : 'process_fallback';
}

function resolveEditableCaptureProfile(status, processName, stage, saveScope) {
  const runtime = status?.ocr_reader_runtime || {};
  const entry = findStoredCaptureProfileEntry(status, processName);
  const normalizedScope = normalizeCaptureProfileSaveScope(saveScope);
  const runtimeProcessMatches = normalizeProcessName(processName)
    && normalizeProcessName(processName) === normalizeProcessName(runtime.process_name);
  const runtimeBucketKey = normalizeCaptureProfileBucketKey(
    runtime.capture_profile_bucket_key || buildCaptureProfileBucketKey(runtime.width, runtime.height),
  );

  if (normalizedScope === 'window_bucket') {
    const storedBucketProfile = resolveStoredBucketCaptureProfile(entry, stage, runtimeBucketKey);
    if (storedBucketProfile) {
      return storedBucketProfile;
    }
    if (runtimeProcessMatches && runtime.capture_profile && runtimeBucketKey) {
      return runtime.capture_profile;
    }
  } else {
    const storedFallbackProfile = resolveStoredFallbackCaptureProfile(entry, stage);
    if (storedFallbackProfile) {
      return storedFallbackProfile;
    }
    if (
      runtimeProcessMatches
      && runtime.capture_profile
      && !['bucket_exact', 'bucket_aspect_nearest'].includes(String(runtime.capture_profile_match_source || ''))
    ) {
      return runtime.capture_profile;
    }
  }
  if (isAihongProcessName(processName) && AIHONG_CAPTURE_PRESETS[stage]) {
    return AIHONG_CAPTURE_PRESETS[stage];
  }
  return DEFAULT_CAPTURE_PROFILE;
}

function setInputValueIfIdle(node, value) {
  if (!node) {
    return;
  }
  if (document.activeElement === node) {
    return;
  }
  node.value = value;
}

function formatScreenTemplatesForInput(templates) {
  const value = Array.isArray(templates) ? templates : [];
  return JSON.stringify(value, null, 2);
}

function renderOcrScreenTemplates(status) {
  const input = document.getElementById('ocrScreenTemplatesInput');
  if (!input || document.activeElement === input) {
    return;
  }
  input.value = formatScreenTemplatesForInput(status.ocr_screen_templates || []);
}

function setOcrScreenTemplatesUndoAvailable(available) {
  const button = document.getElementById('ocrScreenTemplatesUndoBtn');
  if (button) {
    button.disabled = !available;
  }
}

function profileValueForInputs(runtimeProfile) {
  const merged = {
    ...DEFAULT_CAPTURE_PROFILE,
    ...(runtimeProfile || {}),
  };
  return {
    left: Number(merged.left_inset_ratio).toFixed(2),
    right: Number(merged.right_inset_ratio).toFixed(2),
    top: Number(merged.top_ratio).toFixed(2),
    bottom: Number(merged.bottom_inset_ratio).toFixed(2),
  };
}

function renderInstallTaskState(kind) {
  const state = getInstallState(kind).state;
  const { card, statusText, percentText, messageText, detailText, progressBar, button } = getInstallNodes(kind);
  const { label } = getInstallConfig(kind);

  if (!state) {
    card.hidden = true;
    card.style.display = '';
    button.hidden = false;
    button.disabled = false;
    statusText.textContent = uiTf('ui.install.task.waiting', '等待 {label} 安装任务', { label });
    percentText.textContent = '0%';
    messageText.textContent = '';
    detailText.textContent = '';
    progressBar.style.width = '0%';
    return;
  }

  const progress = Math.max(0, Math.min(1, Number(state.progress || 0)));
  const percent = Math.round(progress * 100);
  const details = [];
  if (state.total_bytes) {
    details.push(`${formatBytes(state.downloaded_bytes)} / ${formatBytes(state.total_bytes)}`);
  } else if (state.downloaded_bytes) {
    details.push(formatBytes(state.downloaded_bytes));
  }
  if (state.resume_from) {
    details.push(uiTf('ui.install.task.resume_from', '续传自 {size}', { size: formatBytes(state.resume_from) }));
  }
  if (state.asset_name) {
    details.push(state.asset_name);
  }
  if (state.task_id) {
    details.push(`task ${state.task_id}`);
  }

  card.hidden = false;
  card.style.display = '';
  statusText.textContent = `${formatInstallPhase(state.phase)} · ${state.status || ''}`;
  percentText.textContent = `${percent}%`;
  messageText.textContent = state.message || '';
  detailText.textContent = details.join(' · ');
  progressBar.style.width = `${percent}%`;
  if (state.status === 'completed') {
    button.hidden = true;
    button.disabled = true;
  } else if (state.status === 'failed') {
    button.hidden = false;
    button.disabled = false;
    button.textContent = getInstallConfig(kind).retryText;
  }
}

function renderPluginUnavailable(error) {
  latestStatus = null;
  pendingModeSelection = '';
  updateModeSwitchControl('', { ready: false });
  const pluginNotStarted = uiT('ui.diag.plugin_not_started.title', '插件尚未启动');
  const message = error instanceof Error ? error.message : String(error || pluginNotStarted);
  document.getElementById('summaryText').textContent = pluginNotStarted;
  renderPrimaryDiagnosis({
    connection_state: 'plugin_not_started',
    last_error: null,
    primary_diagnosis: {
      severity: 'info',
      title: pluginNotStarted,
      message: uiT('ui.diag.plugin_not_started.body', '请在插件管理页面点击"启动"按钮，启动完成后数据会自动刷新。'),
      actions: [
        diagnosisAction('refresh_all'),
      ],
    },
    summary: pluginNotStarted,
  });
  renderFirstRunGuide({});
  renderCurrentLineOverview({});
  renderOcrPipelinePanel({});
  renderInstallCompactSummary({});
  renderStatusGrid([
    { label: 'connection_state', value: 'plugin_not_started' },
    { label: 'status', value: pluginNotStarted },
    { label: 'last_error', value: message },
  ], []);
  renderGrid('ocrRuntimeGrid', [
    { label: 'status', value: pluginNotStarted },
  ]);
  renderGrid('snapshotGrid', [
    { label: 'status', value: pluginNotStarted },
  ]);

  // Static label table — rapidocr/dxcam are no longer in `getInstallUIConfig`
  // (no install machinery for bundled deps), so calling `getInstallConfig`
  // for them would throw and break this whole fallback render. Touch banner
  // text directly via static labels; only tesseract/textractor still have
  // install button/card DOM to hide.
  const PROMPT_LABELS = {
    rapidocr: 'RapidOCR',
    dxcam: 'DXcam',
    tesseract: 'Tesseract',
    textractor: 'Textractor',
  };
  for (const kind of ['rapidocr', 'dxcam', 'tesseract', 'textractor']) {
    const banner = document.getElementById(`${kind}Prompt`);
    if (!banner) {
      continue;
    }
    const kicker = document.getElementById(`${kind}PromptKicker`);
    const title = document.getElementById(`${kind}PromptTitle`);
    const body = document.getElementById(`${kind}PromptBody`);
    const path = document.getElementById(`${kind}PathText`);
    // Banners default to `hidden` in HTML and used to be unhidden by
    // switchInstallTab. Post-refactor switchInstallTab only iterates
    // OCR_INSTALL_TABS = ['tesseract'] so rapidocr/dxcam stay hidden in
    // the plugin-unavailable failure path — explicitly clear it here so
    // the degraded-state hint is actually visible to the user.
    banner.hidden = false;
    banner.className = `install-banner install-banner-${kind} neutral`;
    if (kicker) kicker.textContent = PROMPT_LABELS[kind];
    if (title) title.textContent = pluginNotStarted;
    if (body) body.textContent = uiT('ui.install.plugin_unavailable_body', '当前无法读取插件运行状态。请先启动或重载 galgame_plugin，启动完成后这里会显示安装和运行时状态。');
    if (path) path.textContent = message;
    // Install button/card only exist for tesseract + textractor (bundled
    // rapidocr/dxcam don't have install task DOM anymore).
    if (kind === 'tesseract' || kind === 'textractor') {
      const { button, card } = getInstallNodes(kind);
      if (card) {
        card.hidden = true;
        card.style.display = 'none';
      }
      if (button) {
        button.hidden = true;
        button.disabled = true;
      }
    }
  }
}

function applyInstallTaskState(kind, state, { allowRefresh = true } = {}) {
  if (!state) {
    return;
  }
  const installState = getInstallState(kind);
  installState.state = state;
  installState.currentTaskId = state.task_id || state.run_id || installState.currentTaskId;
  if (installState.currentTaskId) {
    if (isInstallTaskTerminal(state)) {
      clearPersistedInstallTaskId(kind);
    } else {
      persistInstallTaskId(kind, installState.currentTaskId);
    }
  }
  installState.inProgress = !isInstallTaskTerminal(state);

  if (latestStatus) {
    renderStatus(latestStatus);
  } else {
    renderInstallTaskState(kind);
  }

  if (!isInstallTaskTerminal(state)) {
    return;
  }

  closeInstallStream(kind);
  clearInstallReconnectTimer(kind);
  const terminalKey = `${installState.currentTaskId}:${state.status || ''}:${state.updated_at || ''}`;
  if (installState.handledTerminalKey === terminalKey) {
    return;
  }
  installState.handledTerminalKey = terminalKey;

  const config = getInstallConfig(kind);
  if (state.status === 'completed') {
    setFlash(state.message || config.successFlash, 'success');
  } else {
    setFlash(state.error || state.message || config.failureFlash, 'error');
  }

  if (allowRefresh) {
    refreshAll({ preserveFlash: true, forceInsights: true }).catch((error) => {
      setFlash(error instanceof Error ? error.message : String(error), 'error');
    });
  }
}

async function fetchInstallTaskState(kind, taskId) {
  const response = await fetch(`${getInstallConfig(kind).url}/${encodeURIComponent(taskId)}`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(uiTf('ui.error.install_state_read_failed', '读取 {label} 安装状态失败: HTTP {status}', {
      label: getInstallConfig(kind).label,
      status: response.status,
    }));
  }
  return await response.json();
}

async function fetchLatestInstallTaskState(kind) {
  const response = await fetch(`${getInstallConfig(kind).url}/latest`);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(uiTf('ui.error.latest_install_state_read_failed', '读取最近 {label} 安装状态失败: HTTP {status}', {
      label: getInstallConfig(kind).label,
      status: response.status,
    }));
  }
  return await response.json();
}

function scheduleInstallReconnect(kind, taskId) {
  const state = getInstallState(kind);
  clearInstallReconnectTimer(kind);
  state.reconnectTimer = setTimeout(async () => {
    try {
      const recovered = await fetchInstallTaskState(kind, taskId);
      if (recovered) {
        applyInstallTaskState(kind, recovered, { allowRefresh: false });
        if (!isInstallTaskTerminal(recovered)) {
          connectInstallStream(kind, taskId);
        }
        return;
      }
    } catch (_) {
      // Keep retrying until we observe a terminal state or the server becomes reachable again.
    }

    if (!state.state || !isInstallTaskTerminal(state.state)) {
      scheduleInstallReconnect(kind, taskId);
    }
  }, 1500);
}

function connectInstallStream(kind, taskId) {
  const state = getInstallState(kind);
  closeInstallStream(kind);
  clearInstallReconnectTimer(kind);
  const stream = new EventSource(`${getInstallConfig(kind).url}/${encodeURIComponent(taskId)}/stream`);
  state.eventSource = stream;

  stream.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      applyInstallTaskState(kind, payload);
    } catch (error) {
      setFlash(error instanceof Error ? error.message : String(error), 'error');
    }
  };

  stream.onerror = async () => {
    if (state.eventSource !== stream) {
      return;
    }
    stream.close();
    state.eventSource = null;
    if (state.state && isInstallTaskTerminal(state.state)) {
      return;
    }
    try {
      const recovered = await fetchInstallTaskState(kind, taskId);
      if (recovered) {
        applyInstallTaskState(kind, recovered, { allowRefresh: false });
      }
    } catch (_) {
      // Ignore transient recovery failures and retry shortly.
    }
    scheduleInstallReconnect(kind, taskId);
  };
}

async function restoreInstallState(kind) {
  const persistedTaskId = readPersistedInstallTaskId(kind);
  let persistedState = null;
  let latestState = null;

  if (persistedTaskId) {
    try {
      persistedState = await fetchInstallTaskState(kind, persistedTaskId);
      if (!persistedState) {
        clearPersistedInstallTaskId(kind);
      } else if (!shouldRestoreInstallTaskState(persistedState)) {
        clearPersistedInstallTaskId(kind);
        persistedState = null;
      }
    } catch (_) {
      persistedState = null;
    }
  }

  try {
    latestState = await fetchLatestInstallTaskState(kind);
    if (!shouldRestoreInstallTaskState(latestState)) {
      latestState = null;
    }
  } catch (_) {
    latestState = null;
  }

  const restoredState = selectPreferredInstallState(persistedState, latestState);
  if (!restoredState) {
    return;
  }

  applyInstallTaskState(kind, restoredState, { allowRefresh: false });
  const restoredTaskId = restoredState.task_id || restoredState.run_id || '';
  if (restoredTaskId && !isInstallTaskTerminal(restoredState)) {
    connectInstallStream(kind, restoredTaskId);
  }
}

async function restoreTextractorInstallState() {
  await restoreInstallState('textractor');
}

// restoreRapidOcrInstallState / restoreDxcamInstallState removed — both
// kinds no longer have install machinery (bundled into main program).

async function restoreTesseractInstallState() {
  await restoreInstallState('tesseract');
}

function updateModeSwitchControl(currentMode, { ready = true } = {}) {
  const modeSwitchEl = document.getElementById('modeSwitch');
  if (!modeSwitchEl) {
    return;
  }
  document.querySelectorAll('#modeSwitch .mode-btn').forEach((btn) => {
    btn.classList.toggle('active', ready && btn.dataset.mode === currentMode);
    btn.disabled = !ready;
    btn.setAttribute('aria-disabled', ready ? 'false' : 'true');
  });
  modeSwitchEl.dataset.active = ready ? currentMode : '';
  modeSwitchEl.dataset.ready = ready ? 'true' : 'false';
  if (!ready) {
    modeSwitchEl.style.removeProperty('--indicator-left');
    modeSwitchEl.style.removeProperty('--indicator-width');
    return;
  }
  const activeBtn = modeSwitchEl.querySelector('.mode-btn.active');
  if (activeBtn) {
    const sr = modeSwitchEl.getBoundingClientRect();
    const br = activeBtn.getBoundingClientRect();
    modeSwitchEl.style.setProperty('--indicator-left', `${br.left - sr.left}px`);
    modeSwitchEl.style.setProperty('--indicator-width', `${br.width}px`);
  }
}

function updateSummaryMode(currentMode) {
  const summaryNode = document.getElementById('summaryText');
  if (!summaryNode) {
    return;
  }
  if (latestStatus) {
    summaryNode.textContent = buildStatusSummaryText({
      ...latestStatus,
      mode: currentMode,
    });
    return;
  }
  summaryNode.textContent = uiTf('ui.summary.mode_part', '模式：{mode}', {
    mode: modeLabel(currentMode, currentMode),
  });
}

function clearPendingModeSelection(mode) {
  if (!pendingModeSelection || (mode && pendingModeSelection !== mode)) {
    return;
  }
  pendingModeSelection = '';
  if (latestStatus) {
    renderStatus(latestStatus);
  }
}

function renderStatus(status) {
  latestStatus = status;
  const statusMode = status.mode || 'companion';
  if (pendingModeSelection && statusMode === pendingModeSelection) {
    pendingModeSelection = '';
  }
  const currentMode = pendingModeSelection || statusMode;
  syncSettingsValue('modeSelect', currentMode);
  syncSettingsChecked('pushToggle', Boolean(status.push_notifications));
  syncSettingsValue('advanceSpeedSelect', status.advance_speed || 'medium');
  document.getElementById('summaryText').textContent = buildStatusSummaryText({
    ...status,
    mode: currentMode,
  });
  updateModeSwitchControl(currentMode);
  const currentSpeed = status.advance_speed || 'medium';
  document.querySelectorAll('#speedSwitch .speed-btn').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.speed === currentSpeed);
  });
  document.querySelector('.hero').classList.toggle('mode-auto', currentMode === 'choice_advisor');
  syncSettingsValue('readerModeSelect', status.reader_mode || 'auto');
  const ocrPollIntervalInput = document.getElementById('ocrPollIntervalInput');
  if (ocrPollIntervalInput && !shouldPreserveSettingsControls()) {
    const interval = Number(status.ocr_reader_poll_interval_seconds || 0.5);
    ocrPollIntervalInput.value = Number.isFinite(interval) ? interval.toFixed(1) : '0.5';
  }
  syncSettingsValue('ocrTriggerModeSelect', status.ocr_reader_trigger_mode || 'interval');
  syncSettingsChecked('llmVisionToggle', Boolean(status.llm_vision_enabled));
  syncSettingsChecked('fastLoopToggle', Boolean(status.ocr_fast_loop_enabled));
  const llmVisionMaxInput = document.getElementById('llmVisionMaxImagePxInput');
  if (llmVisionMaxInput && !shouldPreserveSettingsControls()) {
    llmVisionMaxInput.value = String(Number(status.llm_vision_max_image_px || 768));
  }

  const memoryReaderRuntime = status.memory_reader_runtime || {};
  const ocrRuntime = status.ocr_reader_runtime || {};
  const rapidocr = status.rapidocr || {};
  const dxcam = status.dxcam || {};
  const textractor = status.textractor || {};
  const tesseract = status.tesseract || {};
  const performance = status.performance || {};

  const memoryReaderProcess = memoryReaderRuntime.process_name
    ? `${memoryReaderRuntime.process_name} (${memoryReaderRuntime.pid || 0})`
    : '';
  const ocrTarget = ocrRuntime.process_name
    ? `${ocrRuntime.process_name} (${ocrRuntime.pid || 0})`
    : '';
  const missingLanguages = (tesseract.missing_languages || []).join(', ');
  const performanceProcess = performance.process_name
    ? `${performance.process_name} (${performance.pid || 0})`
    : String(performance.pid || '');
  const ocrTickAllowed = statusBoolValue(status.ocr_tick_allowed, ocrRuntime.ocr_tick_allowed);
  const ocrReaderAllowed = statusBoolValue(status.ocr_reader_allowed, ocrRuntime.ocr_reader_allowed);
  const ocrWaitingForAdvance = statusBoolValue(
    status.ocr_waiting_for_advance,
    ocrRuntime.ocr_waiting_for_advance,
  );
  const ocrTickBlockReason = textValue(status.ocr_tick_block_reason || ocrRuntime.ocr_tick_block_reason);
  const ocrEmitBlockReason = textValue(status.ocr_emit_block_reason || ocrRuntime.ocr_emit_block_reason);
  const ocrReaderAllowedBlockReason = textValue(
    status.ocr_reader_allowed_block_reason || ocrRuntime.ocr_reader_allowed_block_reason,
  );
  const ocrTriggerModeEffective = textValue(
    status.ocr_trigger_mode_effective || ocrRuntime.ocr_trigger_mode_effective || status.ocr_reader_trigger_mode,
  );
  const ocrWaitingForAdvanceReason = textValue(
    status.ocr_waiting_for_advance_reason || ocrRuntime.ocr_waiting_for_advance_reason,
  );
  const ocrTickGateAllowed = statusBoolValue(status.ocr_tick_gate_allowed, ocrRuntime.ocr_tick_gate_allowed);
  const ocrReaderManagerAvailable = statusBoolValue(
    status.ocr_reader_manager_available,
    ocrRuntime.ocr_reader_manager_available,
  );
  const ocrTickSkippedReason = textValue(status.ocr_tick_skipped_reason || ocrRuntime.ocr_tick_skipped_reason);
  const pendingOcrAdvanceCapture = statusBoolValue(
    status.pending_ocr_advance_capture,
    ocrRuntime.pending_ocr_advance_capture,
  );
  const pendingManualForegroundOcrCapture = statusBoolValue(
    status.pending_manual_foreground_ocr_capture,
    ocrRuntime.pending_manual_foreground_ocr_capture,
  );
  const pendingOcrDelayRemaining = status.pending_ocr_delay_remaining ?? ocrRuntime.pending_ocr_delay_remaining;
  const pendingOcrAdvanceAgeSeconds = (
    status.pending_ocr_advance_capture_age_seconds
    ?? ocrRuntime.pending_ocr_advance_capture_age_seconds
  );
  const pendingOcrAdvanceReason = textValue(
    status.pending_ocr_advance_reason || ocrRuntime.pending_ocr_advance_reason,
  );
  const pendingOcrAdvanceClearReason = textValue(
    status.pending_ocr_advance_clear_reason || ocrRuntime.pending_ocr_advance_clear_reason,
  );
  const foregroundRefreshAttempted = statusBoolValue(
    status.foreground_refresh_attempted,
    ocrRuntime.foreground_refresh_attempted,
  );
  const foregroundRefreshSkippedReason = textValue(
    status.foreground_refresh_skipped_reason || ocrRuntime.foreground_refresh_skipped_reason,
  );
  const ocrLastTickDecisionAt = textValue(
    status.ocr_last_tick_decision_at || ocrRuntime.ocr_last_tick_decision_at,
  );
  const ocrRuntimeStatus = textValue(status.ocr_runtime_status || ocrRuntime.ocr_runtime_status);
  const displaySourceNotOcrReason = textValue(
    status.display_source_not_ocr_reason || ocrRuntime.display_source_not_ocr_reason,
  );
  const ocrBackgroundStatus = status.ocr_background_status || {};
  const ocrBackgroundState = textValue(status.ocr_background_state || ocrBackgroundStatus.state);
  const ocrBackgroundMessage = textValue(status.ocr_background_message || ocrBackgroundStatus.message);

  renderPrimaryDiagnosis(status);
  renderFirstRunGuide(status);
  renderCurrentLineOverview(status);
  renderOcrPipelinePanel(status);
  renderInstallCompactSummary(status);

  const diagnosisEl = document.getElementById('primaryDiagnosisPanel');
  if (diagnosisEl) {
    diagnosisEl.classList.toggle('compact', status.primary_diagnosis_level === 'ok');
  }

  const suggestSection = document.getElementById('suggestPanelSection');
  const suggestPanel = document.getElementById('suggestPanel');
  if (suggestSection && suggestPanel) {
    const noDataText = uiT('ui.empty.no_data', '暂无数据');
    const hasContent = suggestPanel.textContent.trim() && suggestPanel.textContent.trim() !== noDataText;
    suggestSection.hidden = !hasContent;
  }

  const userStatusRows = [
    { label: 'connection_state', value: status.connection_state || '' },
    { label: 'active_data_source', value: status.active_data_source || '' },
    { label: 'reader_mode', value: readerModeLabel(status.reader_mode, status.reader_mode || 'auto') },
    { label: 'mode', value: status.mode || '' },
    {
      label: 'agent_user_status',
      value: agentUserStatusLabel(status.agent_user_status, status.agent_user_status || ''),
    },
    { label: 'agent_pause_message', value: status.agent_pause_message || '' },
    { label: 'push_notifications', value: String(Boolean(status.push_notifications)) },
    { label: 'advance_speed', value: advanceSpeedLabel(status.advance_speed, status.advance_speed || 'medium') },
    { label: 'bound_game_id', value: status.bound_game_id || uiT('ui.common.auto_value', '(auto)') },
    { label: 'available_game_ids', value: (status.available_game_ids || []).join(', ') || uiT('ui.common.none_value', '(none)') },
    { label: 'performance_cpu_percent', value: `${formatFixedNumber(performance.cpu_percent, 1)}%` },
    { label: 'performance_memory_mb', value: `${formatFixedNumber(performance.memory_mb, 1)} MB` },
    { label: 'ocr_reader_enabled', value: String(Boolean(status.ocr_reader_enabled)) },
    { label: 'ocr_poll_interval_seconds', value: String(status.ocr_reader_poll_interval_seconds || '') },
    { label: 'ocr_trigger_mode', value: formatOcrTriggerMode(status.ocr_reader_trigger_mode || 'interval') },
    { label: 'ocr_trigger_mode_effective', value: formatOcrTriggerMode(ocrTriggerModeEffective || 'interval') },
    { label: 'ocr_background_state', value: formatOcrBackgroundState(ocrBackgroundState) },
    { label: 'ocr_background_message', value: ocrBackgroundMessage },
    { label: 'ocr_reader_allowed', value: String(ocrReaderAllowed) },
    { label: 'ocr_reader_allowed_block_reason', value: formatOcrTickBlockReason(ocrReaderAllowedBlockReason) },
    { label: 'ocr_reader_manager_available', value: String(ocrReaderManagerAvailable) },
    { label: 'ocr_runtime_status', value: ocrRuntimeStatus },
    { label: 'ocr_tick_gate_allowed', value: String(ocrTickGateAllowed) },
    { label: 'ocr_tick_allowed', value: String(ocrTickAllowed) },
    { label: 'ocr_tick_block_reason', value: formatOcrTickBlockReason(ocrTickBlockReason) },
    { label: 'ocr_tick_skipped_reason', value: formatOcrTickBlockReason(ocrTickSkippedReason) },
    { label: 'ocr_emit_block_reason', value: formatOcrEmitBlockReason(ocrEmitBlockReason) },
    { label: 'ocr_waiting_for_advance', value: String(ocrWaitingForAdvance) },
    { label: 'ocr_waiting_for_advance_reason', value: formatOcrTickBlockReason(ocrWaitingForAdvanceReason) },
    { label: 'pending_ocr_advance_capture', value: String(pendingOcrAdvanceCapture) },
    { label: 'pending_manual_foreground_ocr_capture', value: String(pendingManualForegroundOcrCapture) },
    { label: 'pending_ocr_delay_remaining', value: formatOcrRuntimeSeconds(pendingOcrDelayRemaining) },
    { label: 'pending_ocr_advance_capture_age_seconds', value: formatOcrRuntimeSeconds(pendingOcrAdvanceAgeSeconds) },
    { label: 'pending_ocr_advance_reason', value: pendingOcrAdvanceReason },
    { label: 'pending_ocr_advance_clear_reason', value: formatOcrTickBlockReason(pendingOcrAdvanceClearReason) },
    { label: 'foreground_refresh_attempted', value: String(foregroundRefreshAttempted) },
    { label: 'foreground_refresh_skipped_reason', value: formatOcrTickBlockReason(foregroundRefreshSkippedReason) },
    { label: 'ocr_last_tick_decision_at', value: ocrLastTickDecisionAt },
    { label: 'display_source_not_ocr_reason', value: displaySourceNotOcrReason },
    { label: 'ocr_reader_status', value: ocrRuntime.status || '' },
    { label: 'ocr_reader_detail', value: ocrRuntime.detail || '' },
    { label: 'ocr_context_state', value: ocrRuntime.ocr_context_state || '' },
    { label: 'screen_type', value: status.screen_type || '' },
    { label: 'screen_confidence', value: formatFixedNumber(status.screen_confidence, 2) },
    { label: 'screen_ui_elements', value: String((status.screen_ui_elements || []).length || 0) },
    { label: 'target_is_foreground', value: String(Boolean(ocrRuntime.target_is_foreground)) },
    { label: 'target_window_visible', value: String(Boolean(status.target_window_visible ?? ocrRuntime.target_window_visible)) },
    { label: 'target_window_minimized', value: String(Boolean(status.target_window_minimized ?? ocrRuntime.target_window_minimized)) },
    { label: 'ocr_window_capture_eligible', value: String(Boolean(status.ocr_window_capture_eligible ?? ocrRuntime.ocr_window_capture_eligible)) },
    { label: 'ocr_window_capture_available', value: String(Boolean(status.ocr_window_capture_available ?? ocrRuntime.ocr_window_capture_available)) },
    { label: 'ocr_window_capture_block_reason', value: formatOcrTickBlockReason(status.ocr_window_capture_block_reason || ocrRuntime.ocr_window_capture_block_reason) },
    { label: 'input_target_foreground', value: String(Boolean(status.input_target_foreground ?? ocrRuntime.input_target_foreground)) },
    { label: 'input_target_block_reason', value: formatOcrTickBlockReason(status.input_target_block_reason || ocrRuntime.input_target_block_reason) },
    { label: 'effective_current_line', value: status.effective_current_line?.text || '' },
    { label: 'ocr_reader_target', value: ocrTarget || '' },
    { label: 'ocr_backend_selection', value: status.ocr_backend_selection || 'auto' },
    { label: 'ocr_capture_backend_selection', value: status.ocr_capture_backend_selection || 'auto' },
    { label: 'ocr_reader_fast_loop_enabled', value: String(Boolean(status.ocr_reader_fast_loop_enabled)) },
    { label: 'rapidocr_installed', value: String(Boolean(rapidocr.installed)) },
    { label: 'dxcam_installed', value: String(Boolean(dxcam.installed)) },
    { label: 'memory_reader_enabled', value: String(Boolean(status.memory_reader_enabled)) },
    { label: 'memory_reader_status', value: memoryReaderRuntime.status || '' },
    { label: 'memory_reader_process', value: memoryReaderProcess || '' },
    { label: 'memory_reader_engine', value: memoryReaderRuntime.engine || '' },
    { label: 'tesseract_installed', value: String(Boolean(tesseract.installed)) },
    { label: 'tesseract_missing_languages', value: missingLanguages || '(none)' },
    { label: 'textractor_installed', value: String(Boolean(textractor.installed)) },
    { label: 'last_error', value: status.last_error?.message || '' },
  ];
  const debugStatusRows = [
    { label: 'agent_pause_kind', value: status.agent_pause_kind || '' },
    { label: 'agent_can_resume_by_button', value: String(Boolean(status.agent_can_resume_by_button)) },
    { label: 'agent_can_resume_by_focus', value: String(Boolean(status.agent_can_resume_by_focus)) },
    { label: 'agent_status', value: status.agent_status || '' },
    { label: 'agent_activity', value: status.agent_activity || '' },
    { label: 'agent_reason', value: status.agent_reason || '' },
    { label: 'agent_diagnostic', value: status.agent_diagnostic || '' },
    { label: 'inbound_queue_size', value: String(status.agent_inbound_queue_size || 0) },
    { label: 'outbound_queue_size', value: String(status.agent_outbound_queue_size || 0) },
    { label: 'active_session_id', value: status.active_session_id || '' },
    { label: 'last_seq', value: String(status.last_seq || 0) },
    { label: 'stream_reset_pending', value: String(Boolean(status.stream_reset_pending)) },
    { label: 'performance_memory_percent', value: `${formatFixedNumber(performance.memory_percent, 2)}%` },
    { label: 'performance_thread_count', value: String(performance.thread_count || 0) },
    { label: 'performance_process', value: performanceProcess || '' },
    { label: 'performance_detail', value: performance.detail || '' },
    { label: 'pending_ocr_advance_captures', value: String(status.pending_ocr_advance_captures || 0) },
    {
      label: 'pending_ocr_advance_capture_age_seconds',
      value: formatFixedNumber(pendingOcrAdvanceAgeSeconds, 1),
    },
    { label: 'last_ocr_advance_capture_reason', value: status.last_ocr_advance_capture_reason || '' },
    { label: 'ocr_background_polling', value: String(Boolean(status.ocr_background_polling || ocrBackgroundStatus.background_polling)) },
    { label: 'ocr_foreground_resume_pending', value: String(Boolean(status.ocr_foreground_resume_pending || ocrBackgroundStatus.foreground_resume_pending)) },
    { label: 'ocr_capture_backend_blocked', value: String(Boolean(status.ocr_capture_backend_blocked || ocrBackgroundStatus.capture_backend_blocked)) },
    { label: 'ocr_capture_diagnostic_required', value: String(Boolean(status.ocr_capture_diagnostic_required)) },
    { label: 'ocr_capture_diagnostic', value: status.ocr_capture_diagnostic || '' },
    { label: 'ocr_last_tick_decision_at', value: ocrLastTickDecisionAt },
    { label: 'ocr_fast_loop_running', value: String(Boolean(status.ocr_fast_loop_running)) },
    { label: 'ocr_fast_loop_last_duration_seconds', value: formatFixedNumber(status.ocr_fast_loop_last_duration_seconds, 2) },
    { label: 'ocr_fast_loop_iteration_count', value: String(status.ocr_fast_loop_iteration_count || 0) },
    { label: 'ocr_poll_latency_sample_count', value: String(status.ocr_poll_latency_sample_count || 0) },
    { label: 'ocr_poll_duration_p50_seconds', value: formatFixedNumber(status.ocr_poll_duration_p50_seconds, 2) },
    { label: 'ocr_poll_duration_p95_seconds', value: formatFixedNumber(status.ocr_poll_duration_p95_seconds, 2) },
    { label: 'bridge_poll_latency_sample_count', value: String(status.bridge_poll_latency_sample_count || 0) },
    { label: 'bridge_poll_duration_p50_seconds', value: formatFixedNumber(status.bridge_poll_duration_p50_seconds, 2) },
    { label: 'bridge_poll_duration_p95_seconds', value: formatFixedNumber(status.bridge_poll_duration_p95_seconds, 2) },
    { label: 'ocr_auto_degrade_count', value: String(status.ocr_auto_degrade_count || 0) },
    { label: 'ocr_auto_degrade_reason', value: status.ocr_auto_degrade_reason || '' },
    { label: 'candidate_age_seconds', value: formatFixedNumber(status.candidate_age_seconds, 1) },
    { label: 'stable_confirm_wait_seconds', value: formatFixedNumber(status.stable_confirm_wait_seconds, 1) },
    { label: 'ocr_backend_kind', value: ocrRuntime.backend_kind || '' },
    { label: 'ocr_backend_detail', value: ocrRuntime.backend_detail || '' },
    { label: 'screen_text_sources', value: (status.screen_debug?.sources || []).join(', ') || '' },
    { label: 'screen_classify_reason', value: status.screen_debug?.reason || '' },
    { label: 'screen_classify_layout', value: JSON.stringify(status.screen_debug?.layout || {}) },
    { label: 'screen_classify_keywords', value: JSON.stringify(status.screen_debug?.keyword_hits || {}) },
    { label: 'llm_vision_enabled', value: String(Boolean(status.llm_vision_enabled)) },
    { label: 'llm_vision_max_image_px', value: String(status.llm_vision_max_image_px || 0) },
    { label: 'ocr_screen_template_count', value: String(status.ocr_screen_template_count || 0) },
    { label: 'vision_snapshot_available', value: String(Boolean(ocrRuntime.vision_snapshot_available)) },
    { label: 'vision_snapshot_captured_at', value: ocrRuntime.vision_snapshot_captured_at || '' },
    { label: 'vision_snapshot_byte_size', value: String(ocrRuntime.vision_snapshot_byte_size || 0) },
    { label: 'ocr_screen_awareness_full_frame_ocr', value: String(Boolean(status.ocr_screen_awareness_full_frame_ocr)) },
    { label: 'ocr_screen_awareness_multi_region_ocr', value: String(Boolean(status.ocr_screen_awareness_multi_region_ocr)) },
    { label: 'ocr_screen_awareness_visual_rules', value: String(Boolean(status.ocr_screen_awareness_visual_rules)) },
    { label: 'ocr_screen_awareness_latency_mode', value: status.ocr_screen_awareness_latency_mode || '' },
    {
      label: 'ocr_screen_awareness_min_interval_seconds',
      value: formatFixedNumber(status.ocr_screen_awareness_min_interval_seconds, 1),
    },
    { label: 'rapidocr_enabled', value: String(Boolean(status.rapidocr_enabled)) },
    { label: 'rapidocr_detail', value: rapidocr.detail || '' },
    { label: 'dxcam_detail', value: dxcam.detail || '' },
    { label: 'memory_reader_detail', value: memoryReaderRuntime.detail || '' },
    { label: 'memory_reader_target_selection_detail', value: memoryReaderRuntime.target_selection_detail || '' },
    { label: 'memory_reader_detection_reason', value: memoryReaderRuntime.detection_reason || '' },
    { label: 'memory_reader_hook_code_count', value: String(memoryReaderRuntime.hook_code_count || 0) },
    { label: 'memory_reader_hook_code_detail', value: memoryReaderRuntime.hook_code_detail || '' },
    { label: 'last_text_seq', value: String(memoryReaderRuntime.last_text_seq || 0) },
    { label: 'last_text_ts', value: memoryReaderRuntime.last_text_ts || '' },
    { label: 'last_text_recent', value: String(Boolean(memoryReaderRuntime.last_text_recent)) },
    { label: 'last_text_age_seconds', value: formatFixedNumber(memoryReaderRuntime.last_text_age_seconds, 1) },
    { label: 'tesseract_detail', value: tesseract.detail || '' },
    { label: 'textractor_detail', value: textractor.detail || '' },
  ];
  renderStatusGrid(userStatusRows, debugStatusRows);

  renderOcrRuntime(status);
  renderAgentUserNotice(status);
  syncAgentResumeButton(status);
  syncAutoRefreshIntervalForStatus(status);
  renderRapidOcr(status);
  renderDxcam(status);
  renderTesseract(status);
  renderTextractor(status);
  renderMemoryReaderTargetStatus(status);
  renderOcrWindowTargetStatus(status);
  renderOcrScreenTemplates(status);
  renderOcrProfile(status);
  renderGameBinding(status);
}

function renderGameBinding(status) {
  const currentNode = document.getElementById('currentBoundGameId');
  const detailNode = document.getElementById('currentBoundGameDetail');
  const listNode = document.getElementById('availableGameIds');
  if (!currentNode || !listNode) {
    return;
  }

  const boundGameId = String(status.bound_game_id || '').trim();
  const gameIds = Array.isArray(status.available_game_ids) ? status.available_game_ids : [];
  const boundDescription = describeGameBindingId(boundGameId);
  currentNode.textContent = boundGameId
    ? uiTf('ui.game.binding.fixed_title', '已固定：{title}', { title: boundDescription.title })
    : uiT('ui.game.binding.auto_title', '自动选择游戏窗口');
  if (detailNode) {
    detailNode.textContent = boundGameId
      ? uiTf('ui.game.binding.fixed_detail', '{detail}。点“恢复自动”后，插件会重新按当前可用目标选择。', { detail: boundDescription.detail })
      : uiT('ui.game.binding.auto_detail', '插件会优先选择当前可用目标。需要固定目标时，点击下面的候选项。');
  }

  if (!gameIds.length) {
    listNode.className = 'binding-chip-row empty-inline';
    listNode.textContent = uiT('ui.game.binding.no_games', '未发现可绑定游戏。请确认 Bridge/OCR/Memory Reader 已连接到游戏窗口。');
    return;
  }

  const normalizedGameIds = gameIds.map((gameId) => String(gameId || '').trim()).filter(Boolean);
  if (!normalizedGameIds.length) {
    listNode.className = 'binding-chip-row empty-inline';
    listNode.textContent = uiT('ui.game.binding.empty_ids', '可用游戏 ID 为空。');
    return;
  }

  listNode.className = 'binding-chip-row';
  listNode.replaceChildren(
    ...normalizedGameIds
      .map((normalized) => {
        const active = normalized === boundGameId;
        const description = describeGameBindingId(normalized);
        const button = document.createElement('button');
        button.className = `binding-chip${active ? ' active' : ''}`;
        button.dataset.gameId = normalized;
        button.disabled = active;
        const title = document.createElement('span');
        title.className = 'binding-chip-title';
        title.textContent = active
          ? uiTf('ui.game.binding.current_title', '当前：{title}', { title: description.title })
          : description.title;
        const detail = document.createElement('span');
        detail.className = 'binding-chip-detail';
        detail.textContent = description.detail;
        button.replaceChildren(title, detail);
        return button;
      }),
  );

  listNode.querySelectorAll('[data-game-id]').forEach((button) => {
    button.addEventListener('click', () => {
      const gameId = button.getAttribute('data-game-id') || '';
      withButtonPending(button, uiT('ui.pending.binding', '绑定中...'), () => bindGame(gameId)).catch((error) => { console.error('[galgame] async action failed', error); });
    });
  });
}

function describeGameBindingId(gameId) {
  const normalized = String(gameId || '').trim();
  if (!normalized) {
    return {
      title: uiT('ui.game.binding.auto_title', '自动选择游戏窗口'),
      detail: uiT('ui.game.binding.auto_short_detail', '插件会优先选择当前可用目标'),
    };
  }
  const [prefix, ...rest] = normalized.split('-');
  const suffix = rest.join('-') || normalized;
  if (prefix === 'mem') {
    return {
      title: uiT('ui.game.binding.memory_target', '内存读取目标'),
      detail: `ID ${suffix}`,
    };
  }
  if (prefix === 'ocr') {
    return {
      title: uiT('ui.game.binding.ocr_target', 'OCR 窗口目标'),
      detail: `ID ${suffix}`,
    };
  }
  return {
    title: uiT('ui.game.binding.game_target', '游戏目标'),
    detail: normalized,
  };
}

function formatConnectionStateZh(value) {
  const normalized = String(value || '').trim();
  return connectionStateLabel(normalized, normalized || uiT('ui.common.unknown', '未知'));
}

function formatModeZh(value) {
  const normalized = String(value || '').trim();
  return modeLabel(normalized, normalized || uiT('ui.common.unknown_mode', '未知模式'));
}

function formatDataSourceZh(value) {
  const normalized = String(value || '').trim();
  return dataSourceLabel(normalized, normalized || uiT('ui.common.unknown_source', '未知来源'));
}

function buildStatusSummaryText(status) {
  if (!status || typeof status !== 'object') {
    return uiT('ui.summary.none', '无摘要');
  }

  const source = String(status.active_data_source || '').trim();
  const sessionId = String(status.active_session_id || '').trim();
  const boundGameId = String(status.bound_game_id || '').trim();
  const connectionState = formatConnectionStateZh(status.connection_state);
  const mode = formatModeZh(status.mode);
  const lastSeq = String(status.last_seq || 0);
  const warningMessage = typeof status.last_error?.message === 'string'
    ? status.last_error.message.trim()
    : '';

  let prefix = '';
  if (source === 'ocr_reader' && sessionId) {
    prefix = uiT('ui.summary.connected_ocr', '已通过 OCR 读取连接（降级模式）');
  } else if (source === 'memory_reader' && sessionId) {
    prefix = uiT('ui.summary.connected_memory', '已通过内存读取连接（降级模式）');
  } else if (source === 'bridge_sdk' && sessionId) {
    prefix = uiT('ui.summary.connected_bridge', '已通过 Bridge SDK 连接');
  } else if (status.connection_state === 'stale') {
    prefix = uiT('ui.summary.stale_snapshot', '当前桥接快照已过期');
  } else if (status.connection_state === 'active') {
    prefix = uiT('ui.summary.bridge_active', '当前桥接链路运行中');
  } else {
    prefix = uiTf('ui.summary.current_source', '当前数据源：{source}', { source: formatDataSourceZh(source) });
  }

  const parts = [
    uiTf('ui.summary.state_part', '状态：{state}', { state: connectionState }),
    uiTf('ui.summary.mode_part', '模式：{mode}', { mode }),
  ];

  if (boundGameId) {
    parts.push(uiTf('ui.summary.bound_part', '绑定：{gameId}', { gameId: boundGameId }));
  }
  if (sessionId) {
    parts.push(uiTf('ui.summary.session_part', '会话：{sessionId}', { sessionId }));
  }
  parts.push(uiTf('ui.summary.seq_part', '最新序号：{seq}', { seq: lastSeq }));

  if (warningMessage) {
    parts.push(uiTf('ui.summary.warning_part', '告警：{warning}', { warning: warningMessage }));
  }
  if (status.ocr_capture_diagnostic_required) {
    parts.push(uiTf('ui.summary.ocr_diagnostic_part', 'OCR诊断：{diagnostic}', {
      diagnostic: status.ocr_context_state || ocrRuntimeState(status) || uiT('ui.ocr.missing_line.capture_target_abnormal', '截图区/窗口目标可能异常'),
    }));
  }
  if (status.agent_diagnostic_required || status.agent_reason) {
    const agentText = status.agent_diagnostic || status.agent_reason || status.agent_status || '';
    if (agentText) {
      parts.push(uiTf('ui.summary.agent_part', 'Agent：{agent}', { agent: agentText }));
    }
  }

  return `${prefix}｜${parts.join('｜')}`;
}

function ocrRuntimeState(status) {
  const runtime = status?.ocr_reader_runtime || {};
  return readOcrRuntimeValue(runtime, 'ocr', 'context_state', 'ocr_context_state') || runtime.detail || '';
}

function ocrRuntimeGroup(runtime, groupName) {
  const group = runtime?.[groupName];
  return group && typeof group === 'object' ? group : {};
}

function readOcrRuntimeValue(runtime, groupName, groupKey, legacyKey = groupKey) {
  const group = ocrRuntimeGroup(runtime, groupName);
  const groupedValue = group[groupKey];
  if (groupedValue !== undefined && groupedValue !== null && groupedValue !== '') {
    return groupedValue;
  }
  const legacyValue = runtime?.[legacyKey];
  return legacyValue !== undefined && legacyValue !== null ? legacyValue : '';
}

function formatOcrRuntimeSeconds(value) {
  if (value === undefined || value === null || value === '') {
    return '';
  }
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue.toFixed(2) : '';
}

function renderOcrRuntime(status) {
  const runtime = status.ocr_reader_runtime || {};
  const backgroundStatus = status.ocr_background_status || {};
  const fromWindow = (key, legacyKey = key) => readOcrRuntimeValue(runtime, 'window', key, legacyKey);
  const fromCapture = (key, legacyKey = key) => readOcrRuntimeValue(runtime, 'capture', key, legacyKey);
  const fromOcr = (key, legacyKey = key) => readOcrRuntimeValue(runtime, 'ocr', key, legacyKey);
  const fromTiming = (key, legacyKey = key) => readOcrRuntimeValue(runtime, 'timing', key, legacyKey);
  const fromAdvance = (key, legacyKey = key) => readOcrRuntimeValue(runtime, 'advance', key, legacyKey);
  const fromProfile = (key, legacyKey = key) => readOcrRuntimeValue(runtime, 'profile', key, legacyKey);
  const windowTitle = fromWindow('title', 'window_title');
  const captureStage = fromCapture('stage', 'capture_stage');
  const captureProfile = fromCapture('profile', 'capture_profile');
  const captureProfileMatchSource = fromCapture('profile_match_source', 'capture_profile_match_source');
  const lastCaptureStage = fromCapture('last_stage', 'last_capture_stage');
  const lastCaptureProfile = fromCapture('last_profile', 'last_capture_profile');
  renderGrid('ocrRuntimeGrid', [
    { label: 'status', value: runtime.status || '' },
    { label: 'detail', value: runtime.detail || '' },
    { label: 'process_name', value: fromWindow('process_name') || '' },
    { label: 'pid', value: String(fromWindow('pid') || 0) },
    { label: 'window_title', value: windowTitle || '' },
    { label: 'width', value: String(fromWindow('width') || 0) },
    { label: 'height', value: String(fromWindow('height') || 0) },
    { label: 'aspect_ratio', value: fromWindow('aspect_ratio') ? Number(fromWindow('aspect_ratio')).toFixed(4) : '' },
    { label: 'game_id', value: runtime.game_id || '' },
    { label: 'session_id', value: runtime.session_id || '' },
    { label: 'last_seq', value: String(runtime.last_seq || 0) },
    { label: 'last_event_ts', value: runtime.last_event_ts || '' },
    {
      label: 'ocr_background_state',
      value: formatOcrBackgroundState(status.ocr_background_state || backgroundStatus.state),
    },
    { label: 'ocr_background_message', value: status.ocr_background_message || backgroundStatus.message || '' },
    { label: 'ocr_background_polling', value: String(Boolean(status.ocr_background_polling || backgroundStatus.background_polling)) },
    { label: 'ocr_foreground_resume_pending', value: String(Boolean(status.ocr_foreground_resume_pending || backgroundStatus.foreground_resume_pending)) },
    { label: 'ocr_capture_backend_blocked', value: String(Boolean(status.ocr_capture_backend_blocked || backgroundStatus.capture_backend_blocked)) },
    { label: 'capture_stage', value: ocrProfileStageLabel(captureStage, captureStage || uiT('ui.stage.default', '通用区域')) },
    { label: 'capture_profile', value: formatCaptureProfile(captureProfile) || '(default)' },
    {
      label: 'capture_profile_match_source',
      value: ocrCaptureMatchSourceLabel(captureProfileMatchSource, captureProfileMatchSource || ''),
    },
    { label: 'capture_profile_bucket_key', value: fromCapture('profile_bucket_key', 'capture_profile_bucket_key') || '' },
    {
      label: 'recommended_capture_profile_stage',
      value: ocrProfileStageLabel(fromProfile('recommended_capture_profile_stage'))
        || fromProfile('recommended_capture_profile_stage')
        || '',
    },
    {
      label: 'recommended_capture_profile',
      value: formatCaptureProfile(fromProfile('recommended_capture_profile')) || '',
    },
    {
      label: 'recommended_capture_profile_process_name',
      value: fromProfile('recommended_capture_profile_process_name') || '',
    },
    {
      label: 'recommended_capture_profile_save_scope',
      value: fromProfile('recommended_capture_profile_save_scope') || '',
    },
    { label: 'recommended_capture_profile_reason', value: fromProfile('recommended_capture_profile_reason') || '' },
    {
      label: 'recommended_capture_profile_confidence',
      value: formatFixedNumber(fromProfile('recommended_capture_profile_confidence'), 2),
    },
    {
      label: 'recommended_capture_profile_manual_present',
      value: String(Boolean(fromProfile('recommended_capture_profile_manual_present'))),
    },
    {
      label: 'capture_profile_auto_apply_enabled',
      value: String(Boolean(fromCapture('capture_profile_auto_apply_enabled'))),
    },
    {
      label: 'capture_profile_pending_rollback',
      value: String(Boolean(fromCapture('capture_profile_pending_rollback'))),
    },
    {
      label: 'capture_profile_last_rollback_reason',
      value: fromCapture('capture_profile_last_rollback_reason') || '',
    },
    { label: 'consecutive_no_text_polls', value: String(fromOcr('consecutive_no_text_polls') || 0) },
    { label: 'last_observed_at', value: fromOcr('last_observed_at') || '' },
    { label: 'last_capture_stage', value: ocrProfileStageLabel(lastCaptureStage, lastCaptureStage || '') },
    { label: 'last_capture_profile', value: formatCaptureProfile(lastCaptureProfile) || '' },
    { label: 'ocr_context_state', value: fromOcr('context_state', 'ocr_context_state') || '' },
    { label: 'ocr_tick_gate_allowed', value: String(Boolean(runtime.ocr_tick_gate_allowed)) },
    { label: 'ocr_tick_allowed', value: String(Boolean(runtime.ocr_tick_allowed)) },
    { label: 'ocr_tick_block_reason', value: formatOcrTickBlockReason(runtime.ocr_tick_block_reason) },
    { label: 'ocr_tick_skipped_reason', value: formatOcrTickBlockReason(runtime.ocr_tick_skipped_reason) },
    { label: 'ocr_tick_entered', value: String(Boolean(runtime.ocr_tick_entered)) },
    { label: 'ocr_tick_lock_acquired', value: String(Boolean(runtime.ocr_tick_lock_acquired)) },
    { label: 'ocr_emit_block_reason', value: formatOcrEmitBlockReason(runtime.ocr_emit_block_reason) },
    { label: 'ocr_reader_allowed', value: String(Boolean(runtime.ocr_reader_allowed)) },
    { label: 'ocr_reader_allowed_block_reason', value: formatOcrTickBlockReason(runtime.ocr_reader_allowed_block_reason) },
    { label: 'target_window_visible', value: String(Boolean(fromWindow('target_window_visible'))) },
    { label: 'target_window_minimized', value: String(Boolean(fromWindow('target_window_minimized'))) },
    { label: 'ocr_window_capture_eligible', value: String(Boolean(fromWindow('ocr_window_capture_eligible'))) },
    { label: 'ocr_window_capture_available', value: String(Boolean(fromWindow('ocr_window_capture_available'))) },
    { label: 'ocr_window_capture_block_reason', value: formatOcrTickBlockReason(fromWindow('ocr_window_capture_block_reason')) },
    { label: 'input_target_foreground', value: String(Boolean(fromWindow('input_target_foreground'))) },
    { label: 'input_target_block_reason', value: formatOcrTickBlockReason(fromWindow('input_target_block_reason')) },
    { label: 'ocr_trigger_mode_effective', value: formatOcrTriggerMode(runtime.ocr_trigger_mode_effective || 'interval') },
    { label: 'ocr_waiting_for_advance', value: String(Boolean(runtime.ocr_waiting_for_advance)) },
    { label: 'ocr_waiting_for_advance_reason', value: formatOcrTickBlockReason(runtime.ocr_waiting_for_advance_reason) },
    { label: 'pending_manual_foreground_ocr_capture', value: String(Boolean(runtime.pending_manual_foreground_ocr_capture)) },
    { label: 'pending_ocr_delay_remaining', value: formatOcrRuntimeSeconds(runtime.pending_ocr_delay_remaining) },
    { label: 'pending_ocr_advance_reason', value: runtime.pending_ocr_advance_reason || '' },
    { label: 'pending_ocr_advance_clear_reason', value: formatOcrTickBlockReason(runtime.pending_ocr_advance_clear_reason) },
    { label: 'foreground_refresh_attempted', value: String(Boolean(runtime.foreground_refresh_attempted)) },
    { label: 'foreground_refresh_skipped_reason', value: formatOcrTickBlockReason(runtime.foreground_refresh_skipped_reason) },
    { label: 'ocr_last_tick_decision_at', value: runtime.ocr_last_tick_decision_at || '' },
    { label: 'ocr_fast_loop_delegated', value: String(Boolean(runtime.ocr_fast_loop_delegated)) },
    { label: 'ocr_fast_loop_running', value: String(Boolean(status.ocr_fast_loop_running)) },
    {
      label: 'ocr_fast_loop_inflight_seconds',
      value: formatOcrRuntimeSeconds(status.ocr_fast_loop_inflight_seconds),
    },
    {
      label: 'ocr_fast_loop_last_duration_seconds',
      value: formatOcrRuntimeSeconds(status.ocr_fast_loop_last_duration_seconds),
    },
    { label: 'ocr_fast_loop_iteration_count', value: String(status.ocr_fast_loop_iteration_count || 0) },
    { label: 'ocr_poll_duration_p50_seconds', value: formatOcrRuntimeSeconds(status.ocr_poll_duration_p50_seconds) },
    { label: 'ocr_poll_duration_p95_seconds', value: formatOcrRuntimeSeconds(status.ocr_poll_duration_p95_seconds) },
    { label: 'bridge_poll_duration_p50_seconds', value: formatOcrRuntimeSeconds(status.bridge_poll_duration_p50_seconds) },
    { label: 'bridge_poll_duration_p95_seconds', value: formatOcrRuntimeSeconds(status.bridge_poll_duration_p95_seconds) },
    { label: 'ocr_auto_degrade_reason', value: status.ocr_auto_degrade_reason || '' },
    { label: 'ocr_auto_degrade_at', value: status.ocr_auto_degrade_at || '' },
    { label: 'candidate_age_seconds', value: formatOcrRuntimeSeconds(status.candidate_age_seconds) },
    { label: 'stable_confirm_wait_seconds', value: formatOcrRuntimeSeconds(status.stable_confirm_wait_seconds) },
    { label: 'display_source_not_ocr_reason', value: runtime.display_source_not_ocr_reason || '' },
    { label: 'last_capture_attempt_at', value: fromOcr('last_capture_attempt_at') || '' },
    { label: 'last_capture_completed_at', value: fromOcr('last_capture_completed_at') || '' },
    { label: 'last_capture_error', value: fromOcr('last_capture_error') || '' },
    { label: 'capture_backend_kind', value: fromCapture('backend_kind', 'capture_backend_kind') || '' },
    { label: 'capture_backend_detail', value: fromCapture('backend_detail', 'capture_backend_detail') || '' },
    { label: 'last_capture_image_hash', value: fromCapture('last_image_hash', 'last_capture_image_hash') || '' },
    { label: 'vision_snapshot_available', value: String(Boolean(fromCapture('vision_snapshot_available'))) },
    { label: 'vision_snapshot_captured_at', value: fromCapture('vision_snapshot_captured_at') || '' },
    { label: 'vision_snapshot_expires_at', value: fromCapture('vision_snapshot_expires_at') || '' },
    { label: 'vision_snapshot_source', value: fromCapture('vision_snapshot_source') || '' },
    { label: 'vision_snapshot_size', value: `${fromCapture('vision_snapshot_width') || 0}x${fromCapture('vision_snapshot_height') || 0}` },
    { label: 'vision_snapshot_byte_size', value: String(fromCapture('vision_snapshot_byte_size') || 0) },
    {
      label: 'screen_awareness_sample_count',
      value: String(fromCapture('screen_awareness_sample_count') || 0),
    },
    {
      label: 'screen_awareness_model_detail',
      value: fromCapture('screen_awareness_model_detail') || '',
    },
    {
      label: 'screen_awareness_model_last_stage',
      value: fromCapture('screen_awareness_model_last_stage') || '',
    },
    {
      label: 'screen_awareness_model_last_confidence',
      value: formatFixedNumber(fromCapture('screen_awareness_model_last_confidence'), 2),
    },
    { label: 'consecutive_same_capture_frames', value: String(fromCapture('consecutive_same_frames', 'consecutive_same_capture_frames') || 0) },
    { label: 'stale_capture_backend', value: String(Boolean(fromCapture('stale_backend', 'stale_capture_backend'))) },
    { label: 'last_raw_ocr_text', value: fromOcr('last_raw_text', 'last_raw_ocr_text') || '' },
    { label: 'last_rejected_ocr_text', value: fromOcr('last_rejected_text', 'last_rejected_ocr_text') || '' },
    { label: 'last_rejected_ocr_reason', value: fromOcr('last_rejected_reason', 'last_rejected_ocr_reason') || '' },
    { label: 'last_rejected_ocr_at', value: fromOcr('last_rejected_at', 'last_rejected_ocr_at') || '' },
    { label: 'last_rejected_capture_backend', value: fromOcr('last_rejected_capture_backend') || '' },
    { label: 'screen_awareness_last_skip_reason', value: fromCapture('screen_awareness_last_skip_reason') || '' },
    {
      label: 'screen_awareness_last_region_count',
      value: String(fromCapture('screen_awareness_last_region_count') || 0),
    },
    {
      label: 'screen_awareness_last_capture_duration_seconds',
      value: formatOcrRuntimeSeconds(fromCapture('screen_awareness_last_capture_duration_seconds')),
    },
    {
      label: 'screen_awareness_last_ocr_duration_seconds',
      value: formatOcrRuntimeSeconds(fromCapture('screen_awareness_last_ocr_duration_seconds')),
    },
    { label: 'last_observed_line', value: fromOcr('last_observed_line')?.text || '' },
    { label: 'last_stable_line', value: fromOcr('last_stable_line')?.text || '' },
    { label: 'ocr_capture_diagnostic_required', value: String(Boolean(fromCapture('diagnostic_required', 'ocr_capture_diagnostic_required'))) },
    { label: 'backend_kind', value: fromOcr('backend_kind') || '' },
    { label: 'backend_detail', value: fromOcr('backend_detail') || '' },
    { label: 'backend_path', value: fromOcr('backend_path') || '' },
    { label: 'backend_model', value: fromOcr('backend_model') || '' },
    { label: 'tesseract_path', value: fromOcr('tesseract_path') || '' },
    { label: 'languages', value: fromOcr('languages') || '' },
    { label: 'takeover_reason', value: runtime.takeover_reason || '' },
    { label: 'target_selection_mode', value: fromWindow('selection_mode', 'target_selection_mode') || '' },
    { label: 'target_selection_detail', value: fromWindow('selection_detail', 'target_selection_detail') || '' },
    { label: 'effective_window_key', value: fromWindow('effective_window_key') || '' },
    { label: 'effective_window_title', value: fromWindow('effective_window_title') || '' },
    { label: 'effective_process_name', value: fromWindow('effective_process_name') || '' },
    { label: 'target_is_foreground', value: String(Boolean(fromWindow('target_is_foreground'))) },
    { label: 'foreground_refresh_at', value: fromWindow('foreground_refresh_at') || '' },
    { label: 'foreground_refresh_detail', value: fromWindow('foreground_refresh_detail') || '' },
    { label: 'foreground_hwnd', value: String(fromWindow('foreground_hwnd') || 0) },
    { label: 'target_hwnd', value: String(fromWindow('target_hwnd') || 0) },
    { label: 'foreground_advance_monitor_running', value: String(Boolean(fromAdvance('foreground_monitor_running', 'foreground_advance_monitor_running'))) },
    { label: 'foreground_advance_last_seq', value: String(fromAdvance('foreground_last_seq', 'foreground_advance_last_seq') || 0) },
    { label: 'foreground_advance_consumed_seq', value: String(fromAdvance('foreground_consumed_seq', 'foreground_advance_consumed_seq') || 0) },
    { label: 'foreground_advance_last_kind', value: fromAdvance('foreground_last_kind', 'foreground_advance_last_kind') || '' },
    { label: 'foreground_advance_last_delta', value: String(fromAdvance('foreground_last_delta', 'foreground_advance_last_delta') || 0) },
    { label: 'foreground_advance_last_matched', value: String(Boolean(fromAdvance('foreground_last_matched', 'foreground_advance_last_matched'))) },
    { label: 'foreground_advance_last_match_reason', value: fromAdvance('foreground_last_match_reason', 'foreground_advance_last_match_reason') || '' },
    { label: 'last_poll_started_at', value: fromTiming('last_poll_started_at') || '' },
    { label: 'last_poll_completed_at', value: fromTiming('last_poll_completed_at') || '' },
    {
      label: 'last_poll_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_poll_duration_seconds')),
    },
    {
      label: 'last_capture_total_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_capture_total_duration_seconds')),
    },
    {
      label: 'last_capture_frame_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_capture_frame_duration_seconds')),
    },
    {
      label: 'last_capture_background_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_capture_background_duration_seconds')),
    },
    {
      label: 'last_capture_image_hash_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_capture_image_hash_duration_seconds')),
    },
    {
      label: 'last_ocr_extract_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_ocr_extract_duration_seconds')),
    },
    {
      label: 'last_backend_plan_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_backend_plan_duration_seconds')),
    },
    {
      label: 'last_window_scan_duration_seconds',
      value: formatOcrRuntimeSeconds(fromTiming('last_window_scan_duration_seconds')),
    },
    { label: 'last_capture_background_hash_skipped', value: String(Boolean(fromAdvance('last_background_hash_skipped', 'last_capture_background_hash_skipped'))) },
    { label: 'last_poll_emitted_event', value: String(Boolean(fromAdvance('last_poll_emitted_event'))) },
    { label: 'last_tick_skipped', value: String(Boolean(fromAdvance('last_tick_skipped'))) },
    { label: 'last_tick_skip_reason', value: fromAdvance('last_tick_skip_reason') || '' },
    { label: 'pending_visual_scene_count', value: String(fromAdvance('pending_visual_scene_count') || 0) },
    { label: 'last_auto_recalibrate_attempts', value: String(fromAdvance('last_auto_recalibrate_attempts') || 0) },
    {
      label: 'last_auto_recalibrate_duration_seconds',
      value: formatOcrRuntimeSeconds(fromAdvance('last_auto_recalibrate_duration_seconds')),
    },
    { label: 'last_auto_recalibrate_limited', value: String(Boolean(fromAdvance('last_auto_recalibrate_limited'))) },
    { label: 'last_auto_recalibrate_error', value: fromAdvance('last_auto_recalibrate_error') || '' },
    { label: 'candidate_count', value: String(fromWindow('candidate_count') || 0) },
    { label: 'excluded_candidate_count', value: String(fromWindow('excluded_candidate_count') || 0) },
    { label: 'last_exclude_reason', value: fromWindow('last_exclude_reason') || '' },
  ]);
}

function formatMemoryReaderSelectionDetail(detail) {
  const mapping = {
    auto_candidate_scan: uiT('ui.memory.detail.auto_candidate_scan', '正在自动检测游戏进程'),
    manual_target_active: uiT('ui.memory.detail.manual_target_active', '手动锁定已启用'),
    manual_target_exact: uiT('ui.memory.detail.manual_target_exact', '命中手动锁定进程'),
    manual_target_rebound: uiT('ui.memory.detail.manual_target_rebound', '已按 exe 路径和进程名重新绑定'),
    manual_target_unavailable: uiT('ui.memory.detail.manual_target_unavailable', '手动进程不可用，请重新选择'),
    manual_pid_unimplemented: uiT('ui.memory.detail.manual_pid_unimplemented', 'auto_detect=false 但尚未锁定手动目标'),
    no_detected_game_process: uiT('ui.memory.detail.no_detected_game_process', '未检测到可用游戏进程'),
    detected_kirikiri_xp3: uiT('ui.memory.detail.detected_kirikiri_xp3', '通过 xp3 资源识别为 KiriKiri'),
    detected_kirikiri_common_xp3: uiT('ui.memory.detail.detected_kirikiri_common_xp3', '通过常见 xp3 资源识别为 KiriKiri'),
    detected_kirikiri_startup_tjs: uiT('ui.memory.detail.detected_kirikiri_startup_tjs', '通过 startup.tjs 识别为 KiriKiri'),
    detected_kirikiri_module: uiT('ui.memory.detail.detected_kirikiri_module', '通过 krkr.dll 识别为 KiriKiri'),
    detected_kirikiri_process_name: uiT('ui.memory.detail.detected_kirikiri_process_name', '通过进程名识别为 KiriKiri'),
    detected_kirikiri_preset_senren_banka: uiT('ui.memory.detail.detected_kirikiri_preset_senren_banka', '通过千恋万花内建签名识别为 KiriKiri'),
    detected_unity_module: uiT('ui.memory.detail.detected_unity_module', '通过 Unity 模块识别'),
    detected_unity_name_or_cmdline: uiT('ui.memory.detail.detected_unity_name_or_cmdline', '通过 Unity 进程信息识别'),
    detected_renpy_module: uiT('ui.memory.detail.detected_renpy_module', '通过 RenPy 模块识别'),
    detected_renpy_cmdline: uiT('ui.memory.detail.detected_renpy_cmdline', '通过 RenPy 命令行识别'),
    unknown_engine: uiT('ui.memory.detail.unknown_engine', '未知引擎'),
  };
  return uiDynamicT('ui.memory.detail', detail, mapping[detail] || detail || '');
}

function renderMemoryReaderTargetStatus(status) {
  const runtime = status.memory_reader_runtime || {};
  const snapshot = latestMemoryProcessSnapshot || {};
  const target = status.memory_reader_target || snapshot.manual_target || {};
  const modeText = document.getElementById('memoryReaderTargetModeText');
  const hint = document.getElementById('memoryReaderRuntimeHint');
  const autoButton = document.getElementById('memoryProcessAutoBtn');
  const mode = runtime.target_selection_mode || target.mode || snapshot.target_selection_mode || 'auto';
  const processName = runtime.process_name || target.process_name || '';
  const pid = runtime.pid || target.pid || '';
  const engine = runtime.engine || target.engine || target.detected_engine || '';
  const detail = formatMemoryReaderSelectionDetail(runtime.target_selection_detail || runtime.detection_reason || '');
  if (modeText) {
    modeText.textContent = mode === 'manual'
      ? `${uiT('ui.window.current_mode', '当前模式')}: manual${target.process_name ? ` | ${uiT('ui.window.locked', '锁定')} ${target.process_name}` : ''}`
      : `${uiT('ui.window.current_mode', '当前模式')}: ${uiT('ui.window.auto_detect_first', '自动检测优先')}`;
  }
  if (hint) {
    const parts = [
      processName ? `${uiT('ui.window.current_target', '当前目标')}: ${processName}${pid ? ` (${pid})` : ''}` : '',
      engine ? `engine=${engine}` : '',
      detail,
      runtime.hook_code_detail ? `hook=${runtime.hook_code_detail} (${runtime.hook_code_count || 0})` : '',
    ].filter(Boolean);
    hint.textContent = parts.join(' | ') || uiT('ui.memory.auto_hint', 'Memory Reader 会优先检测 RenPy / Unity / KiriKiri；自动失败时可手动选择进程。');
  }
  if (autoButton) {
    autoButton.disabled = mode !== 'manual';
  }
  renderLockedMemoryProcess(status);
}

function renderLockedMemoryProcess(status) {
  const runtime = (status || {}).memory_reader_runtime || {};
  const snapshot = latestMemoryProcessSnapshot || {};
  const target = (status || {}).memory_reader_target || snapshot.manual_target || {};
  const card = document.getElementById('memoryLockedProcessCard');
  if (!card) {
    return;
  }
  const mode = runtime.target_selection_mode || target.mode || snapshot.target_selection_mode || 'auto';
  const processName = target.process_name || runtime.process_name || '';
  const pid = target.pid || runtime.pid || '';
  const exePath = target.exe_path || runtime.exe_path || '';
  const engine = target.engine || target.detected_engine || runtime.engine || '';
  const detail = formatMemoryReaderSelectionDetail(runtime.target_selection_detail || target.detection_reason || runtime.detection_reason || '');
  if (mode === 'manual' && (processName || exePath || pid)) {
    card.innerHTML = `
      <div class="locked-window-info">
        <p class="list-kicker">${escapeHtml(processName || uiT('ui.window.unknown_process', '未知进程'))}${pid ? ` · PID ${escapeHtml(pid)}` : ''}</p>
        <h3>${escapeHtml(engine || 'unknown')}</h3>
        <p class="result-note mono">${escapeHtml(exePath || target.process_key || '')}</p>
        <div class="window-candidate-meta" style="margin-top:8px;">
          <span class="status-chip active">${escapeHtml(uiT('ui.window.manual_locked', '手动锁定'))}</span>
          ${detail ? `<span class="status-chip">${escapeHtml(detail)}</span>` : ''}
        </div>
      </div>
    `;
  } else if (runtime.process_name) {
    card.innerHTML = `
      <div class="locked-window-info">
        <p class="list-kicker">${escapeHtml(runtime.process_name)}${runtime.pid ? ` · PID ${escapeHtml(runtime.pid)}` : ''}</p>
        <h3>${escapeHtml(runtime.engine || 'unknown')}</h3>
        <p class="result-note">${escapeHtml(detail || uiT('ui.memory.auto_detected_target', '自动检测到 Memory Reader 目标'))}</p>
        <div class="window-candidate-meta" style="margin-top:8px;">
          <span class="status-chip active">${escapeHtml(uiT('ui.window.auto_detected', '自动检测'))}</span>
        </div>
      </div>
    `;
  } else {
    card.innerHTML = `<div class="locked-window-empty">${escapeHtml(uiT('ui.memory.no_locked_target', '尚未确认 Memory Reader 目标进程。自动识别失败时，请点击“选择进程”手动锁定。'))}</div>`;
  }
}

function renderMemoryProcessListToNode(node, processes) {
  renderPreservingScroll(node, () => {
    if (!processes.length) {
      node.className = 'stack-list scroll-region empty-state window-candidate-list';
      node.textContent = uiT('ui.modal.no_process', '暂无候选进程');
    } else {
      node.className = 'stack-list scroll-region window-candidate-list';
      node.innerHTML = processes.map((item) => {
        const chips = [
          item.is_attached ? `<span class="status-chip active">${escapeHtml(uiT('ui.window.attached', '当前附着'))}</span>` : '',
          item.is_manual_target ? `<span class="status-chip active">${escapeHtml(uiT('ui.window.manual_locked', '手动锁定'))}</span>` : '',
          item.engine || item.detected_engine ? `<span class="status-chip">${escapeHtml(item.engine || item.detected_engine)}</span>` : '',
        ].filter(Boolean).join('');
        return `
          <article class="list-card compact">
            <div class="window-candidate-header">
              <div class="window-candidate-summary">
                <p class="list-kicker">${escapeHtml(item.process_name || uiT('ui.window.unknown_process', '未知进程'))} · pid ${escapeHtml(item.pid || 0)}</p>
                <h3>${escapeHtml(formatMemoryReaderSelectionDetail(item.detection_reason || '') || item.detection_reason || 'unknown_engine')}</h3>
              </div>
              <button class="secondary" data-memory-process-key="${escapeHtml(item.process_key || '')}">${escapeHtml(uiT('ui.window.lock_process', '锁定此进程'))}</button>
            </div>
            <p class="result-note mono">${escapeHtml(item.exe_path || item.process_key || '')}</p>
            <div class="window-candidate-actions">
              <div class="window-candidate-meta">${chips}</div>
            </div>
          </article>
        `;
      }).join('');
    }
  });
  node.querySelectorAll('[data-memory-process-key]').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.getAttribute('data-memory-process-key') || '';
      withButtonPending(button, uiT('ui.pending.locking', '锁定中...'), () => setMemoryProcessTarget(key)).catch((error) => { console.error('[galgame] async action failed', error); });
    });
  });
}

function renderMemoryProcessTargetSnapshot(snapshot, status = latestStatus) {
  latestMemoryProcessSnapshot = snapshot;
  const modal = document.getElementById('memoryProcessModal');
  if (modal && !modal.hidden) {
    const modalList = document.getElementById('memoryProcessList');
    renderMemoryProcessListToNode(modalList, snapshot.processes || []);
  }
  renderMemoryReaderTargetStatus(status || { memory_reader_runtime: {} });
}

function formatOcrWindowReason(reason) {
  const mapping = {
    excluded_self_window: uiT('ui.ocr.window.reason.excluded_self_window', '已排除 N.E.K.O 自身窗口'),
    excluded_overlay_window: uiT('ui.ocr.window.reason.excluded_overlay_window', '已排除 overlay / launcher / helper'),
    excluded_helper_window: uiT('ui.ocr.window.reason.excluded_helper_window', '已排除系统或宿主辅助窗口'),
    excluded_small_or_hidden_window: uiT('ui.ocr.window.reason.excluded_small_or_hidden_window', '已排除过小或不可用窗口'),
    excluded_minimized_window: uiT('ui.ocr.window.reason.excluded_minimized_window', '游戏窗口已最小化，OCR 不能截图，请恢复窗口'),
    excluded_non_game_process: uiT('ui.ocr.window.reason.excluded_non_game_process', '非游戏进程，已忽略'),
  };
  return uiDynamicT('ui.ocr.window.reason', reason, mapping[reason] || reason || 'unknown');
}

function formatOcrWindowSelectionDetail(detail) {
  const mapping = {
    auto_candidate_scan: uiT('ui.ocr.window.detail.auto_candidate_scan', '正在自动检测游戏窗口'),
    manual_target_active: uiT('ui.ocr.window.detail.manual_target_active', '手动锁定已启用'),
    manual_target_exact: uiT('ui.ocr.window.detail.manual_target_exact', '命中手动锁定窗口'),
    manual_target_rebound: uiT('ui.ocr.window.detail.manual_target_rebound', '已按签名重新绑定手动窗口'),
    manual_target_unavailable_fallback_to_auto: uiT('ui.ocr.window.detail.manual_target_unavailable_fallback_to_auto', '手动窗口不可用，请重新锁定窗口'),
    waiting_for_manual_window_target: uiT('ui.ocr.window.detail.waiting_for_manual_window_target', '自动检测失败，请手动锁定 OCR 目标窗口'),
    auto_detect_needs_manual_fallback: uiT('ui.ocr.window.detail.auto_detect_needs_manual_fallback', '未找到可信自动目标，请手动选择游戏窗口'),
    foreground_window_needs_manual_confirmation: uiT('ui.ocr.window.detail.foreground_window_needs_manual_confirmation', '当前前台窗口不像游戏，请手动选择游戏窗口'),
    no_eligible_window: uiT('ui.ocr.window.detail.no_eligible_window', '当前没有可用游戏窗口'),
    memory_reader_window_minimized: uiT('ui.ocr.window.detail.memory_reader_window_minimized', '游戏窗口已最小化，OCR 不能截图，请恢复窗口'),
    memory_reader_minimized_overridden_by_foreground: uiT('ui.ocr.window.detail.memory_reader_minimized_overridden_by_foreground', '窗口标记为最小化但实际在前台，已自动恢复'),
    memory_reader_pid: uiT('ui.ocr.window.detail.memory_reader_pid', '优先沿用 Memory Reader 命中的 PID'),
    memory_reader_process: uiT('ui.ocr.window.detail.memory_reader_process', '优先沿用 Memory Reader 命中的进程'),
    manual_target_overridden_by_memory_reader: uiT('ui.ocr.window.detail.manual_target_overridden_by_memory_reader', 'Memory Reader 目标与 OCR 手动目标不一致，已优先使用 Memory Reader'),
    manual_target_overridden_by_memory_reader_pid: uiT('ui.ocr.window.detail.manual_target_overridden_by_memory_reader_pid', 'OCR 手动目标已过期，已按 Memory Reader PID 切回当前游戏'),
    manual_target_overridden_by_memory_reader_process: uiT('ui.ocr.window.detail.manual_target_overridden_by_memory_reader_process', 'OCR 手动目标已过期，已按 Memory Reader 进程切回当前游戏'),
    manual_target_overridden_by_memory_reader_unavailable: uiT('ui.ocr.window.detail.manual_target_overridden_by_memory_reader_unavailable', 'Memory Reader 已切到其他游戏，OCR 暂不继续读旧窗口'),
    memory_reader_target_unavailable: uiT('ui.ocr.window.detail.memory_reader_target_unavailable', 'Memory Reader 目标窗口不可用，OCR 暂停以避免读错窗口'),
    attached_hwnd: uiT('ui.ocr.window.detail.attached_hwnd', '优先复用当前已附着窗口'),
    attached_pid: uiT('ui.ocr.window.detail.attached_pid', '优先复用当前已附着进程'),
    foreground_window: uiT('ui.ocr.window.detail.foreground_window', '优先使用当前前台候选窗口'),
    scored_candidate: uiT('ui.ocr.window.detail.scored_candidate', '按候选排序选择窗口'),
  };
  return uiDynamicT('ui.ocr.window.detail', detail, mapping[detail] || detail || '');
}

function renderOcrWindowTargetStatus(status) {
  const runtime = status.ocr_reader_runtime || {};
  const snapshot = latestOcrWindowSnapshot || {};
  const modeText = document.getElementById('ocrWindowTargetModeText');
  const hint = document.getElementById('ocrWindowRuntimeHint');
  const autoButton = document.getElementById('ocrWindowAutoBtn');
  const mode = runtime.target_selection_mode || snapshot.target_selection_mode || 'auto';
  const manualTarget = runtime.manual_target || snapshot.manual_target || {};
  const effectiveTitle = runtime.effective_window_title || runtime.window_title || '';
  const effectiveProcess = runtime.effective_process_name || runtime.process_name || '';
  const detail = formatOcrWindowSelectionDetail(runtime.target_selection_detail || '');
  let captureHint = '';
  if (runtime.capture_backend_kind === 'dxcam') {
    captureHint = uiT('ui.ocr.window.capture_dxcam', '使用 DXcam 截图后端');
  } else if (runtime.capture_backend_detail === 'dxcam_unavailable_fallback') {
    captureHint = uiT('ui.ocr.window.capture_dxcam_unavailable', '未安装 DXcam，正在使用兼容截图；可安装 dxcam 降低遮挡或旧帧影响');
  } else if (runtime.capture_backend_detail === 'dxcam_failed_fallback') {
    captureHint = uiT('ui.ocr.window.capture_dxcam_failed', 'DXcam 截图失败，已自动切到兼容截图');
  } else if (runtime.capture_backend_kind) {
    captureHint = `${uiT('ui.ocr.window.capture_compat_prefix', '使用')} ${runtime.capture_backend_kind} ${uiT('ui.ocr.window.capture_compat_suffix', '兼容截图')}`;
  }
  if (runtime.stale_capture_backend) {
    captureHint = uiT('ui.ocr.window.capture_stale', '截图源没有更新，请切回游戏窗口或切换 DXcam 截图后端');
  }
  const hintParts = [
    effectiveProcess ? `${uiT('ui.window.current_target', '当前目标')}: ${effectiveProcess}${runtime.pid ? ` (${runtime.pid})` : ''}` : '',
    effectiveTitle ? `${uiT('ui.window.window_label', '窗口')}: ${effectiveTitle}` : '',
    captureHint,
    detail,
    runtime.last_exclude_reason ? `${uiT('ui.ocr.window.last_excluded', '最近排除')}: ${formatOcrWindowReason(runtime.last_exclude_reason)}` : '',
  ].filter(Boolean);

  modeText.textContent = mode === 'manual'
    ? `${uiT('ui.window.current_mode', '当前模式')}: manual${manualTarget.process_name ? ` | ${uiT('ui.window.locked', '锁定')} ${manualTarget.process_name}` : ''}`
    : `${uiT('ui.window.current_mode', '当前模式')}: ${uiT('ui.window.auto_detect_first', '自动检测优先')}`;
  hint.textContent = hintParts.join(' | ') || uiT('ui.ocr.window.auto_hint', '插件会先尝试当前前台/已绑定游戏窗口；无法可信识别时，请手动选择游戏窗口。');
  autoButton.disabled = mode !== 'manual';
}

function renderLockedWindow(status) {
  const runtime = (status || {}).ocr_reader_runtime || {};
  const snapshot = latestOcrWindowSnapshot || {};
  const card = document.getElementById('ocrLockedWindowCard');
  const mode = runtime.target_selection_mode || snapshot.target_selection_mode || 'auto';
  const manualTarget = runtime.manual_target || snapshot.manual_target || {};
  const effectiveTitle = runtime.effective_window_title || runtime.window_title || '';
  const effectiveProcess = runtime.effective_process_name || runtime.process_name || '';

  if (mode === 'manual' && (manualTarget.process_name || effectiveProcess)) {
    const processName = manualTarget.process_name || effectiveProcess;
    const title = manualTarget.title || effectiveTitle;
    const pid = manualTarget.pid || runtime.pid || '';
    const windowKey = manualTarget.window_key || '';
    card.innerHTML = `
      <div class="locked-window-info">
        <p class="list-kicker">${escapeHtml(processName)}${pid ? ` · PID ${escapeHtml(pid)}` : ''}</p>
        <h3>${escapeHtml(title || uiT('ui.window.untitled', '未命名窗口'))}</h3>
        <p class="result-note mono">${escapeHtml(windowKey)}</p>
        <div class="window-candidate-meta" style="margin-top:8px;">
          <span class="status-chip active">${escapeHtml(uiT('ui.window.manual_locked', '手动锁定'))}</span>
        </div>
      </div>
    `;
  } else if (effectiveProcess || effectiveTitle) {
    const detail = formatOcrWindowSelectionDetail(runtime.target_selection_detail || '');
    card.innerHTML = `
      <div class="locked-window-info">
        <p class="list-kicker">${escapeHtml(effectiveProcess || uiT('ui.window.auto_detect_target', '自动检测目标'))}${runtime.pid ? ` · PID ${escapeHtml(runtime.pid)}` : ''}</p>
        <h3>${escapeHtml(effectiveTitle || uiT('ui.window.untitled', '未命名窗口'))}</h3>
        <p class="result-note">${escapeHtml(detail || uiT('ui.ocr.window.auto_detected_target', '自动检测到可信游戏窗口'))}</p>
        <div class="window-candidate-meta" style="margin-top:8px;">
          <span class="status-chip active">${escapeHtml(uiT('ui.window.auto_detected', '自动检测'))}</span>
          ${runtime.target_is_foreground ? `<span class="status-chip active">${escapeHtml(uiT('ui.window.foreground', '前台窗口'))}</span>` : `<span class="status-chip warning">${escapeHtml(uiT('ui.window.not_foreground', '非前台'))}</span>`}
        </div>
      </div>
    `;
  } else {
    card.innerHTML = `<div class="locked-window-empty">${escapeHtml(uiT('ui.ocr.window.no_locked_target', '尚未确认 OCR 目标窗口。插件会优先尝试前台/已绑定游戏窗口；如果仍没有读到台词，请点击“选择识别窗口”手动锁定。'))}</div>`;
  }
}

function renderOcrWindowListToNode(node, windows) {
  renderPreservingScroll(node, () => {
    if (!windows.length) {
      node.className = 'stack-list scroll-region empty-state window-candidate-list';
      node.textContent = uiT('ui.ocr.window.no_available_window', '暂无可用游戏窗口');
    } else {
      node.className = 'stack-list scroll-region window-candidate-list';
      node.innerHTML = windows.map((item) => {
        const chips = [
          item.is_attached ? `<span class="status-chip active">${escapeHtml(uiT('ui.window.attached', '当前附着'))}</span>` : '',
          item.is_foreground ? `<span class="status-chip">${escapeHtml(uiT('ui.window.foreground', '前台窗口'))}</span>` : '',
          item.is_manual_target ? `<span class="status-chip active">${escapeHtml(uiT('ui.window.manual_locked', '手动锁定'))}</span>` : '',
        ].filter(Boolean).join('');
        return `
          <article class="list-card compact">
            <div class="window-candidate-header">
              <div class="window-candidate-summary">
                <p class="list-kicker">${escapeHtml(item.process_name || uiT('ui.window.unknown_process', '未知进程'))} · pid ${escapeHtml(item.pid || 0)}</p>
                <h3>${escapeHtml(item.title || uiT('ui.window.untitled', '未命名窗口'))}</h3>
              </div>
              <button class="secondary" data-window-key="${escapeHtml(item.window_key || '')}">${escapeHtml(uiT('ui.window.lock_window', '锁定此窗口'))}</button>
            </div>
            <p class="result-note mono">${escapeHtml(item.window_key || '')}</p>
            <div class="window-candidate-actions">
              <div class="window-candidate-meta">${chips}</div>
            </div>
          </article>
        `;
      }).join('');
    }
  });
  node.querySelectorAll('[data-window-key]').forEach((button) => {
    button.addEventListener('click', () => {
      const key = button.getAttribute('data-window-key') || '';
      withButtonPending(button, uiT('ui.pending.locking', '锁定中...'), () => setOcrWindowTarget(key)).catch((error) => { console.error('[galgame] async action failed', error); });
    });
  });
}

function renderOcrWindowTargetSnapshot(snapshot, status = latestStatus) {
  latestOcrWindowSnapshot = snapshot;
  const runtime = (status || {}).ocr_reader_runtime || {};
  const excludedNode = document.getElementById('ocrExcludedWindowList');
  const windows = snapshot.windows || [];
  const excludedWindows = snapshot.excluded_windows || [];
  emptyOcrWindowFocusForceRefreshDone = windows.length === 0
    ? emptyOcrWindowFocusForceRefreshDone
    : false;

  const modal = document.getElementById('ocrWindowModal');
  if (modal && !modal.hidden) {
    const modalList = document.getElementById('ocrWindowList');
    renderOcrWindowListToNode(modalList, windows);
  }

  renderPreservingScroll(excludedNode, () => {
    if (!excludedWindows.length) {
      excludedNode.className = 'stack-list scroll-region empty-state window-candidate-list';
      excludedNode.textContent = uiT('ui.ocr.window.no_excluded', '暂无排除窗口');
    } else {
      excludedNode.className = 'stack-list scroll-region window-candidate-list';
      excludedNode.innerHTML = excludedWindows.map((item) => `
        <article class="list-card compact">
          <p class="list-kicker">${escapeHtml(item.process_name || uiT('ui.window.unknown_process', '未知进程'))} · ${escapeHtml(formatOcrWindowReason(item.exclude_reason || ''))}</p>
          <h3>${escapeHtml(item.title || uiT('ui.window.untitled', '未命名窗口'))}</h3>
          <p class="result-note mono">${escapeHtml(item.window_key || '')}</p>
        </article>
      `).join('');
    }
  });

  renderOcrWindowTargetStatus(status || { ocr_reader_runtime: runtime });
  renderLockedWindow(status || { ocr_reader_runtime: runtime });
}

function renderRapidOcr(status) {
  const rapidocr = status.rapidocr || {};
  const runtime = status.ocr_reader_runtime || {};
  const banner = document.getElementById('rapidocrPrompt');
  const kicker = document.getElementById('rapidocrPromptKicker');
  const title = document.getElementById('rapidocrPromptTitle');
  const body = document.getElementById('rapidocrPromptBody');
  const path = document.getElementById('rapidocrPathText');
  const installed = Boolean(rapidocr.installed);
  const selectedBackend = status.ocr_backend_selection || 'auto';
  const usingRapidOcr = runtime.backend_kind === 'rapidocr';
  const usingFallback = runtime.backend_kind === 'tesseract';
  configureUseButton('rapidocrUseBtn', {
    active: selectedBackend === 'rapidocr',
    disabled: !installed,
    text: selectedBackend === 'rapidocr'
      ? uiT('ui.install.rapidocr.using', '正在使用 RapidOCR')
      : uiT('ui.install.rapidocr.use', '使用 RapidOCR'),
    title: installed
      ? uiT('ui.install.rapidocr.force_title', '强制 OCR Reader 使用 RapidOCR')
      : uiT('ui.install.rapidocr.unavailable_title', 'RapidOCR 未启用 — 请使用打包版本，或源码运行 `uv sync --group galgame`'),
  });
  configureUseButton('ocrBackendAutoBtn', {
    active: selectedBackend === 'auto',
    text: selectedBackend === 'auto'
      ? uiT('ui.install.ocr_auto.using', 'OCR 自动选择中')
      : uiT('ui.install.ocr_auto', 'OCR 自动'),
    title: uiT('ui.install.ocr_auto.title', '按 RapidOCR 优先、Tesseract 兜底自动选择 OCR 后端'),
  });

  // rapidocrPrompt is no longer tab-controlled — show it whenever we render.
  banner.hidden = false;
  banner.className = 'install-banner install-banner-rapidocr';

  if (!rapidocr.install_supported) {
    banner.classList.add('neutral');
    kicker.textContent = uiT('ui.install.rapidocr.kicker', 'OCR 主后端');
    title.textContent = uiT('ui.install.rapidocr.unsupported_title', '当前平台暂不支持 RapidOCR');
    body.textContent = uiT('ui.install.rapidocr.unsupported_body', 'RapidOCR 主后端目前只支持 Windows。');
    path.textContent = '';
    return;
  }

  if (installed) {
    banner.classList.add(usingRapidOcr ? 'success' : 'neutral');
    kicker.textContent = usingRapidOcr
      ? uiT('ui.install.rapidocr.kicker_active', 'OCR 主后端已接管')
      : uiT('ui.install.rapidocr.kicker_ready', 'OCR 主后端已就绪');
    title.textContent = usingRapidOcr
      ? uiT('ui.install.rapidocr.active_title', 'RapidOCR 已接管当前 OCR Reader')
      : uiT('ui.install.rapidocr.ready_title', 'RapidOCR 已就绪，等待作为 OCR 主后端工作');
    body.textContent = usingRapidOcr
      ? `${uiT('ui.install.backend_label', '后端')}: ${runtime.backend_kind || 'rapidocr'}\n${uiT('ui.install.model_label', '模型')}: ${runtime.backend_model || rapidocr.selected_model || ''}`
      : usingFallback
        ? `${uiT('ui.install.rapidocr.fallback_body', 'RapidOCR 已就绪，但本帧 OCR 回退到了 Tesseract。原因')}: ${runtime.backend_detail || rapidocr.detail || uiT('ui.status.unknown', '未知')}。`
        : uiT('ui.install.rapidocr.ready_body', 'RapidOCR 已就绪。无 SDK 且无有效内存文本时，它会优先于 Tesseract 作为 OCR Reader 的主后端。');
    path.textContent = [
      rapidocr.detected_path ? `${uiT('ui.install.detected_path', '检测路径')}: ${rapidocr.detected_path}` : '',
      rapidocr.model_cache_dir ? `${uiT('ui.install.model_dir', '模型目录')}: ${rapidocr.model_cache_dir}` : '',
    ].filter(Boolean).join('\n');
  } else if (rapidocr.detail === 'broken_runtime') {
    banner.classList.add('warning');
    kicker.textContent = uiT('ui.install.rapidocr.kicker_broken', 'OCR 主后端异常');
    title.textContent = uiT('ui.install.rapidocr.broken_title', 'RapidOCR 运行时已损坏或导入失败');
    body.textContent = uiT('ui.install.rapidocr.broken_body', 'bundled rapidocr 包导入失败。请重建插件 venv（`uv sync --group galgame`）或重装打包版本。');
    path.textContent = rapidocr.detected_path
      ? `${uiT('ui.install.detected_path', '检测路径')}: ${rapidocr.detected_path}`
      : '';
  } else {
    // Bundled-but-missing: source install without `--group galgame`, or non-Windows.
    banner.classList.add('warning');
    kicker.textContent = uiT('ui.install.rapidocr.kicker_not_ready', 'OCR 主后端未就绪');
    title.textContent = uiT('ui.install.rapidocr.not_ready_title', '未检测到 RapidOCR');
    body.textContent = uiT(
      'ui.install.rapidocr.bundled_hint',
      'RapidOCR 现在随主程序打包。如果你跑的是打包版本，请重新下载安装包；如果是源码运行，请执行 `uv sync --group galgame` 后重启。',
    );
    path.textContent = '';
  }
}

function renderDxcam(status) {
  const dxcam = status.dxcam || {};
  const runtime = status.ocr_reader_runtime || {};
  const banner = document.getElementById('dxcamPrompt');
  const kicker = document.getElementById('dxcamPromptKicker');
  const title = document.getElementById('dxcamPromptTitle');
  const body = document.getElementById('dxcamPromptBody');
  const path = document.getElementById('dxcamPathText');
  const installed = Boolean(dxcam.installed);
  const selectedCaptureBackend = status.ocr_capture_backend_selection || 'auto';
  const usingDxcam = runtime.capture_backend_kind === 'dxcam';
  const captureBackendText = runtime.capture_backend_kind || (
    selectedCaptureBackend === 'dxcam'
      ? uiT('ui.install.dxcam.selected_waiting', 'DXcam 已选择，等待下一次 OCR 截图确认')
      : uiT('ui.status.unknown', '未知')
  );
  configureUseButton('smartCaptureUseBtn', {
    active: selectedCaptureBackend === 'smart',
    text: selectedCaptureBackend === 'smart'
      ? uiT('ui.install.smart.using', '正在使用 Smart')
      : uiT('ui.install.smart.use', '使用 Smart'),
    title: uiT('ui.install.smart.title', '前台优先 DXcam，后台只尝试 PrintWindow，避免读到被遮挡的可见屏幕像素'),
  });
  configureUseButton('dxcamUseBtn', {
    active: selectedCaptureBackend === 'dxcam',
    disabled: !installed,
    text: selectedCaptureBackend === 'dxcam'
      ? uiT('ui.install.dxcam.using', '正在使用 DXcam')
      : uiT('ui.install.dxcam.use', '使用 DXcam'),
    title: installed
      ? uiT('ui.install.dxcam.force_title', '强制截图后端使用 DXcam')
      : uiT('ui.install.dxcam.unavailable_title', 'DXcam 不可用 — 仅 Windows 平台支持，且需要打包版本或源码 uv sync'),
  });
  configureUseButton('captureBackendAutoBtn', {
    active: selectedCaptureBackend === 'auto',
    text: selectedCaptureBackend === 'auto'
      ? uiT('ui.install.capture_auto.using', '截图自动选择中')
      : uiT('ui.install.capture_auto', '截图自动'),
    title: uiT('ui.install.capture_auto.title', '默认链：DXcam → MSS → PyAutoGUI 自动选择截图后端'),
  });
  configureUseButton('mssUseBtn', {
    active: selectedCaptureBackend === 'mss' || selectedCaptureBackend === 'imagegrab',
    text: (selectedCaptureBackend === 'mss' || selectedCaptureBackend === 'imagegrab')
      ? uiT('ui.install.mss.using', '正在使用 MSS')
      : uiT('ui.install.mss.use', '使用 MSS'),
    title: uiT('ui.install.mss.title', '使用 MSS 跨平台截图后端，游戏窗口需要可见'),
  });
  configureUseButton('pyautoguiUseBtn', {
    active: selectedCaptureBackend === 'pyautogui',
    text: selectedCaptureBackend === 'pyautogui'
      ? uiT('ui.install.pyautogui.using', '正在使用 PyAutoGUI')
      : uiT('ui.install.pyautogui.use', '使用 PyAutoGUI'),
    title: uiT('ui.install.pyautogui.title', '使用 PyAutoGUI 截图后端，跨平台兜底（macOS/Linux 上较慢）'),
  });
  configureUseButton('printwindowUseBtn', {
    active: selectedCaptureBackend === 'printwindow',
    text: selectedCaptureBackend === 'printwindow'
      ? uiT('ui.install.printwindow.using', '正在使用 PrintWindow')
      : uiT('ui.install.printwindow.use', '使用 PrintWindow'),
    title: uiT('ui.install.printwindow.title', '使用 Win32 PrintWindow，主要用于截被遮挡窗口；DirectX/Unity 游戏可能旧帧或黑屏'),
  });

  // dxcamPrompt is no longer tab-controlled — show it whenever we render.
  banner.hidden = false;
  banner.className = 'install-banner install-banner-dxcam';

  if (!dxcam.install_supported) {
    banner.classList.add('neutral');
    kicker.textContent = uiT('ui.install.dxcam.kicker', '截图依赖');
    title.textContent = uiT('ui.install.dxcam.unsupported_title', '当前平台不支持 DXcam');
    body.textContent = uiT('ui.install.dxcam.unsupported_body', 'DXcam 仅用于 Windows 桌面捕获。当前平台会自动使用 mss / pyautogui 等跨平台后端。');
    path.textContent = '';
    return;
  }

  if (installed) {
    banner.classList.add(usingDxcam ? 'success' : 'neutral');
    kicker.textContent = usingDxcam
      ? uiT('ui.install.dxcam.kicker_active', '截图依赖已接管')
      : uiT('ui.install.dxcam.kicker_ready', '截图依赖已就绪');
    title.textContent = usingDxcam
      ? uiT('ui.install.dxcam.active_title', 'DXcam 正在作为截图后端工作')
      : selectedCaptureBackend === 'dxcam'
        ? uiT('ui.install.dxcam.selected_waiting', 'DXcam 已选择，等待下一次 OCR 截图确认')
        : uiT('ui.install.dxcam.ready_title', 'DXcam 已就绪，等待 OCR Reader 自动使用');
    body.textContent = usingDxcam
      ? uiT('ui.install.dxcam.active_body', '当前截图后端使用 DXcam。它仍要求游戏窗口前台可见，不做后台捕获或绕过。')
      : `${uiT('ui.install.dxcam.ready_body_prefix', 'DXcam 已就绪。当前截图后端')}: ${captureBackendText}。`;
    path.textContent = dxcam.detected_path ? `${uiT('ui.install.detected_path', '检测路径')}: ${dxcam.detected_path}` : '';
  } else {
    // Bundled-but-missing: source install without `--group galgame`, or non-Windows.
    banner.classList.add('warning');
    kicker.textContent = uiT('ui.install.dxcam.kicker_not_ready', '截图依赖未就绪');
    title.textContent = uiT('ui.install.dxcam.not_ready_title', '未检测到 DXcam');
    body.textContent = uiT(
      'ui.install.dxcam.bundled_hint',
      'DXcam 现在随主程序打包（仅 Windows）。如果你跑的是打包版本，请重新下载安装包；如果是源码运行，请执行 `uv sync --group galgame` 后重启。截图链会自动 fallback 到 MSS / PyAutoGUI。',
    );
    path.textContent = '';
  }
}

function renderTesseract(status) {
  const tesseract = status.tesseract || {};
  const runtime = status.ocr_reader_runtime || {};
  const banner = document.getElementById('tesseractPrompt');
  const kicker = document.getElementById('tesseractPromptKicker');
  const title = document.getElementById('tesseractPromptTitle');
  const body = document.getElementById('tesseractPromptBody');
  const path = document.getElementById('tesseractPathText');
  const button = document.getElementById('tesseractInstallBtn');
  const installState = getInstallState('tesseract').state;
  const installable = Boolean(tesseract.install_supported) && Boolean(tesseract.can_install);
  const installed = Boolean(tesseract.installed) || (installState && installState.status === 'completed');
  const missingLanguages = tesseract.missing_languages || [];
  const selectedBackend = status.ocr_backend_selection || 'auto';
  configureUseButton('tesseractUseBtn', {
    active: selectedBackend === 'tesseract',
    disabled: !installed,
    text: selectedBackend === 'tesseract'
      ? uiT('ui.install.tesseract.using', '正在使用 Tesseract')
      : uiT('ui.install.tesseract.use', '使用 Tesseract'),
    title: installed
      ? uiT('ui.install.tesseract.force_title', '强制 OCR Reader 使用 Tesseract')
      : uiT('ui.install.tesseract.install_first_title', '请先安装 Tesseract 和所需语言包'),
  });

  banner.className = 'install-banner install-banner-tesseract';
  button.hidden = !installable;
  button.disabled = getInstallState('tesseract').inProgress;
  button.textContent = getInstallState('tesseract').inProgress
    ? getInstallConfig('tesseract').runningText
    : getInstallConfig('tesseract').actionText;

  if (!tesseract.install_supported) {
    banner.classList.add('neutral');
    kicker.textContent = uiT('ui.install.tesseract.kicker', 'OCR 兼容兜底');
    title.textContent = uiT('ui.install.tesseract.unsupported_title', '当前平台暂不支持自动安装 Tesseract');
    body.textContent = uiT('ui.install.tesseract.unsupported_body', 'Tesseract 目前只保留为 OCR Reader 的兼容兜底，本地自动安装也只在 Windows 上提供。');
    path.textContent = '';
    button.hidden = true;
    renderInstallTaskState('tesseract');
    return;
  }

  if (installed) {
    banner.classList.add('success');
    kicker.textContent = uiT('ui.install.tesseract.kicker', 'OCR 兼容兜底');
    title.textContent = runtime.backend_kind === 'tesseract'
      ? uiT('ui.install.tesseract.active_title', 'Tesseract 正在作为兼容兜底工作')
      : uiT('ui.install.tesseract.ready_title', 'Tesseract 已就绪，等待必要时回退');
    body.textContent = runtime.backend_kind === 'tesseract'
      ? `${uiT('ui.install.tesseract.active_body_prefix', '当前 OCR Reader 使用 Tesseract。原因')}: ${runtime.backend_detail || runtime.detail || uiT('ui.install.compat_fallback', '兼容兜底')}.`
      : uiT('ui.install.tesseract.ready_body', '本地 Tesseract 与默认语言包 chi_sim+jpn+eng 已齐全。它会在 RapidOCR 缺失、损坏或运行时异常时接管 OCR。');
    path.textContent = tesseract.detected_path
      ? `${uiT('ui.install.detected_path', '检测路径')}: ${tesseract.detected_path}`
      : '';
    button.hidden = true;
  } else if (tesseract.detail === 'missing_languages') {
    banner.classList.add('warning');
    kicker.textContent = uiT('ui.install.tesseract.kicker_missing_languages', '兜底语言缺失');
    title.textContent = uiT('ui.install.tesseract.missing_languages_title', '已检测到 Tesseract，但兼容兜底语言包不完整');
    body.textContent = `${uiT('ui.install.tesseract.missing_languages_body_prefix', '当前缺少')} ${(missingLanguages || []).join(', ') || uiT('ui.install.language_pack', '语言包')}。${uiT('ui.install.tesseract.missing_languages_body_suffix', '安装流程会按默认语言 chi_sim+jpn+eng 补齐兼容兜底所需文件。')}`;
    path.textContent = tesseract.tessdata_dir
      ? `${uiT('ui.install.tessdata_dir', 'tessdata 目录')}: ${tesseract.tessdata_dir}`
      : '';
  } else {
    banner.classList.add('warning');
    kicker.textContent = uiT('ui.install.tesseract.kicker_not_ready', '兼容兜底未就绪');
    title.textContent = uiT('ui.install.tesseract.not_ready_title', '未检测到 Tesseract，兼容兜底尚未就绪');
    body.textContent = uiT('ui.install.tesseract.not_ready_body', '这不会阻止 RapidOCR 作为主后端工作，但当 RapidOCR 缺失或运行异常时，将无法自动回退到本地 Tesseract。');
    path.textContent = tesseract.expected_executable_path
      ? `${uiT('ui.install.expected_path', '预期安装位置')}: ${tesseract.expected_executable_path}`
      : '';
  }

  if (installState && !isInstallTaskTerminal(installState)) {
    banner.className = 'install-banner install-banner-tesseract neutral';
    kicker.textContent = uiT('ui.install.tesseract.install_kicker', 'Tesseract 安装');
    title.textContent = uiT('ui.install.tesseract.installing_title', 'Tesseract 正在后台安装');
    body.textContent = uiT('ui.install.tesseract.installing_body', '安装器和语言包下载都通过 HTTPS 进行，当前页面会通过 SSE 接收实时进度；即使刷新页面，也会尝试恢复最近的安装状态。');
    button.hidden = false;
    button.disabled = true;
    button.textContent = getInstallConfig('tesseract').runningText;
  } else if (installState && installState.status === 'failed' && installable) {
    banner.className = 'install-banner install-banner-tesseract neutral';
    kicker.textContent = uiT('ui.install.tesseract.install_kicker', 'Tesseract 安装');
    title.textContent = uiT('ui.install.tesseract.failed_title', 'Tesseract 安装失败，可直接重试');
    body.textContent = installState.error || installState.message || uiT('ui.install.task_failed_retry', '后台安装任务失败，你可以再次点击按钮重试。');
    button.hidden = false;
    button.disabled = false;
    button.textContent = getInstallConfig('tesseract').retryText;
  } else if (installState && installState.status === 'completed' && !installed) {
    banner.className = 'install-banner install-banner-tesseract neutral';
    kicker.textContent = uiT('ui.install.tesseract.install_kicker', 'Tesseract 安装');
    title.textContent = uiT('ui.install.tesseract.completed_refresh_title', 'Tesseract 安装已完成，正在刷新 OCR 状态');
    body.textContent = installState.message || uiT('ui.install.task_done_refreshing', '安装任务已结束，正在等待插件状态刷新。');
  }

  if (installed) {
    getInstallNodes('tesseract').card.hidden = true;
  } else {
    renderInstallTaskState('tesseract');
  }
}

function renderTextractor(status) {
  const textractor = status.textractor || {};
  const runtime = status.memory_reader_runtime || {};
  const banner = document.getElementById('textractorPrompt');
  const kicker = document.getElementById('textractorPromptKicker');
  const title = document.getElementById('textractorPromptTitle');
  const body = document.getElementById('textractorPromptBody');
  const path = document.getElementById('textractorPathText');
  const button = document.getElementById('textractorInstallBtn');
  const installState = getInstallState('textractor').state;
  const installable = Boolean(textractor.install_supported) && Boolean(textractor.can_install);
  const installed = Boolean(textractor.installed) || (installState && installState.status === 'completed');
  const runtimeBlocked = runtime.detail === 'invalid_textractor_path';

  banner.className = 'install-banner install-banner-textractor';
  button.hidden = !installable;
  button.disabled = getInstallState('textractor').inProgress;
  button.textContent = getInstallState('textractor').inProgress
    ? getInstallConfig('textractor').runningText
    : getInstallConfig('textractor').actionText;

  if (!textractor.install_supported) {
    banner.classList.add('neutral');
    kicker.textContent = uiT('ui.install.textractor.kicker', '实验性兜底');
    title.textContent = uiT('ui.install.textractor.unsupported_title', '当前平台无需 Textractor 自动安装');
    body.textContent = uiT('ui.install.textractor.unsupported_body', 'Textractor 读内存兜底仅在 Windows 上启用，而且当前优先级已经低于 OCR Reader。');
    path.textContent = '';
    button.hidden = true;
    renderInstallTaskState('textractor');
    return;
  }

  if (installed) {
    banner.classList.add('success');
    kicker.textContent = uiT('ui.install.textractor.kicker', '实验性兜底');
    title.textContent = runtimeBlocked
      ? uiT('ui.install.textractor.ready_blocked_title', 'Textractor 已安装，等待 Memory Reader 手动/实验性接管')
      : uiT('ui.install.textractor.ready_title', 'Textractor 已就绪，但仅作为实验性兜底');
    body.textContent = runtimeBlocked
      ? uiT('ui.install.textractor.ready_blocked_body', '当前已检测到 TextractorCLI.exe。它仍保留在链路中，但优先级固定低于 OCR Reader，不再是当前首发主验收线。')
      : uiT('ui.install.textractor.ready_body', 'TextractorCLI.exe 已检测到');
    path.textContent = textractor.detected_path ? `${uiT('ui.install.detected_path', '检测路径')}: ${textractor.detected_path}` : '';
    button.hidden = true;
  } else {
    banner.classList.add('neutral');
    kicker.textContent = uiT('ui.install.textractor.kicker', '实验性兜底');
    title.textContent = runtimeBlocked
      ? uiT('ui.install.textractor.missing_blocked_title', '未检测到 Textractor，Memory Reader 实验性兜底暂不可用')
      : uiT('ui.install.textractor.missing_title', '尚未检测到 Textractor');
    body.textContent = runtimeBlocked
      ? uiT('ui.install.textractor.missing_blocked_body', '如果你后续仍想继续尝试 Memory Reader，可以在这里补装 Textractor；但当前正式主兜底仍然是 OCR Reader。')
      : uiT('ui.install.textractor.missing_body', 'Textractor 仅影响实验性 Memory Reader 链路，不影响当前 Bridge SDK > OCR Reader 的正式运行顺序。');
    path.textContent = textractor.expected_executable_path
      ? `${uiT('ui.install.expected_path', '预期安装位置')}: ${textractor.expected_executable_path}`
      : '';
  }

  if (installState && !isInstallTaskTerminal(installState)) {
    banner.className = 'install-banner install-banner-textractor neutral';
    kicker.textContent = uiT('ui.install.textractor.install_kicker', 'Textractor 安装');
    title.textContent = uiT('ui.install.textractor.installing_title', 'Textractor 正在后台安装');
    body.textContent = uiT('ui.install.textractor.installing_body', '下载通过 HTTPS 进行，页面会通过 SSE 接收实时进度。Textractor 完成后只会补强实验性 Memory Reader 路径。');
    button.hidden = false;
    button.disabled = true;
    button.textContent = getInstallConfig('textractor').runningText;
  } else if (installState && installState.status === 'failed' && installable) {
    banner.className = 'install-banner install-banner-textractor neutral';
    kicker.textContent = uiT('ui.install.textractor.install_kicker', 'Textractor 安装');
    title.textContent = uiT('ui.install.textractor.failed_title', 'Textractor 安装失败，可直接重试');
    body.textContent = installState.error || installState.message || uiT('ui.install.task_failed_retry', '后台安装任务失败，你可以再次点击按钮重试。');
    button.hidden = false;
    button.disabled = false;
    button.textContent = getInstallConfig('textractor').retryText;
  } else if (installState && installState.status === 'completed' && !installed) {
    banner.className = 'install-banner install-banner-textractor neutral';
    kicker.textContent = uiT('ui.install.textractor.install_kicker', 'Textractor 安装');
    title.textContent = uiT('ui.install.textractor.completed_refresh_title', 'Textractor 安装已完成，正在刷新插件状态');
    body.textContent = installState.message || uiT('ui.install.task_done_refreshing', '安装任务已结束，正在等待插件状态刷新。');
  }

  if (installed) {
    getInstallNodes('textractor').card.hidden = true;
  } else {
    renderInstallTaskState('textractor');
  }
}

function renderOcrProfile(status) {
  const runtime = status.ocr_reader_runtime || {};
  const processInput = document.getElementById('ocrProfileProcessInput');
  const stageSelect = document.getElementById('ocrProfileStageSelect');
  const saveScopeSelect = document.getElementById('ocrProfileSaveScopeSelect');
  const leftInput = document.getElementById('ocrProfileLeftInput');
  const rightInput = document.getElementById('ocrProfileRightInput');
  const topInput = document.getElementById('ocrProfileTopInput');
  const bottomInput = document.getElementById('ocrProfileBottomInput');
  const hint = document.getElementById('ocrProfileRuntimeHint');
  const currentProcessName = processInput.value.trim() || runtime.process_name || '';
  const currentStage = stageSelect.value || 'default';
  const defaultSaveScope = resolveRuntimeDefaultSaveScope(status, currentProcessName);
  if (!saveScopeSelect.value || (saveScopeSelect.value === 'window_bucket' && defaultSaveScope === 'process_fallback' && !runtime.width)) {
    saveScopeSelect.value = defaultSaveScope;
  }
  const currentSaveScope = normalizeCaptureProfileSaveScope(saveScopeSelect.value || defaultSaveScope);
  const profileValues = profileValueForInputs(
    resolveEditableCaptureProfile(status, currentProcessName, currentStage, currentSaveScope),
  );
  const autoRecalibrateButton = document.getElementById('ocrProfileAutoRecalibrateBtn');
  const applyRecommendedButton = document.getElementById('ocrProfileApplyRecommendedBtn');
  const rollbackButton = document.getElementById('ocrProfileRollbackBtn');
  const autoApplyInput = document.getElementById('ocrProfileAutoApplyRecommendedInput');
  let autoRecalibrateReason = '';
  if (!Boolean(runtime.enabled)) {
    autoRecalibrateReason = uiT('ui.ocr_profile.auto_recalibrate.disabled_reader', 'OCR Reader 未启用');
  } else if (runtime.detail === 'unsupported_platform') {
    autoRecalibrateReason = uiT('ui.ocr_profile.auto_recalibrate.unsupported_platform', '当前平台不是 Windows');
  } else if (runtime.detail === 'capture_backend_unavailable') {
    autoRecalibrateReason = uiT('ui.ocr_profile.auto_recalibrate.capture_backend_unavailable', '当前截图后端不可用');
  } else if (!runtime.process_name || !Number(runtime.width || 0) || !Number(runtime.height || 0)) {
    autoRecalibrateReason = uiT('ui.ocr_profile.auto_recalibrate.no_target_window', '当前没有已附着的 OCR 目标窗口');
  }
  const recommendedProfile = runtime.recommended_capture_profile
    || runtime.profile?.recommended_capture_profile
    || {};
  const recommendedStage = runtime.recommended_capture_profile_stage
    || runtime.profile?.recommended_capture_profile_stage
    || '';
  const recommendedReason = runtime.recommended_capture_profile_reason
    || runtime.profile?.recommended_capture_profile_reason
    || '';
  const recommendedConfidence = runtime.recommended_capture_profile_confidence
    || runtime.profile?.recommended_capture_profile_confidence
    || 0;
  const recommendedManualPresent = Boolean(
    runtime.recommended_capture_profile_manual_present
    || runtime.profile?.recommended_capture_profile_manual_present,
  );
  const recommendedHint = recommendedProfile && Object.keys(recommendedProfile).length
    ? uiTf(
      'ui.ocr_profile.recommended_hint',
      '建议校准: {stage} {profile} ({reason}, confidence={confidence}, manual={manual})',
      {
        stage: ocrProfileStageLabel(recommendedStage, recommendedStage || uiT('ui.stage.dialogue', '对白区')),
        profile: formatCaptureProfile(recommendedProfile),
        reason: recommendedReason || 'auto',
        confidence: formatFixedNumber(recommendedConfidence, 2),
        manual: recommendedManualPresent ? uiT('ui.common.yes_code', 'yes') : uiT('ui.common.no_code', 'no'),
      },
    )
    : '';
  const rollbackReason = runtime.capture_profile_last_rollback_reason || '';
  const pendingRollback = Boolean(runtime.capture_profile_pending_rollback);

  if (runtime.process_name) {
    hint.textContent = [
      uiTf('ui.ocr_profile.current_target', '当前 OCR 目标: {process} ({pid})', {
        process: runtime.process_name,
        pid: runtime.pid || 0,
      }),
      runtime.window_title ? uiTf('ui.ocr_profile.window_title', '窗口: {title}', { title: runtime.window_title }) : '',
      runtime.width && runtime.height ? uiTf('ui.ocr_profile.window_size', '尺寸: {width}x{height}', {
        width: runtime.width,
        height: runtime.height,
      }) : '',
      runtime.capture_stage
        ? uiTf('ui.ocr_profile.runtime_stage', '运行阶段: {stage}', {
          stage: ocrProfileStageLabel(runtime.capture_stage, runtime.capture_stage),
        })
        : '',
      runtime.capture_profile_match_source
        ? uiTf('ui.ocr_profile.match_source', '命中来源: {source}', {
          source: ocrCaptureMatchSourceLabel(runtime.capture_profile_match_source, runtime.capture_profile_match_source),
        })
        : '',
      runtime.capture_profile_bucket_key ? uiTf('ui.ocr_profile.bucket', '命中桶: {bucket}', { bucket: runtime.capture_profile_bucket_key }) : '',
      runtime.detail ? uiTf('ui.ocr_profile.status', '状态: {status}', { status: runtime.detail }) : '',
      runtime.takeover_reason ? uiTf('ui.ocr_profile.takeover_reason', '接管原因: {reason}', { reason: runtime.takeover_reason }) : '',
      recommendedHint,
      pendingRollback ? uiTf('ui.ocr_profile.rollback_observing', '推荐回滚观察中: {count}/2', {
        count: runtime.capture_profile_rollback_failure_count || 0,
      }) : '',
      rollbackReason ? uiTf('ui.ocr_profile.rollback_status', '推荐回滚状态: {reason}', { reason: rollbackReason }) : '',
      uiTf('ui.ocr_profile.auto_recalibrate_status', '自动重校准: {status}', {
        status: autoRecalibrateReason || uiT('ui.ocr_profile.available', '可用'),
      }),
      isAihongProcessName(currentProcessName || runtime.process_name)
        ? uiT('ui.ocr_profile.aihong_stage_hint', '哀鸿支持按对白区 / 菜单区分别保存')
        : '',
    ].filter(Boolean).join(' | ');
  } else {
    hint.textContent = isAihongProcessName(currentProcessName)
      ? uiT('ui.ocr_profile.no_target_aihong_hint', '当前还没有附着的 OCR 目标进程。你可以先手动填写 TheLamentingGeese.exe，并分别预存哀鸿的对白区 / 菜单区截图范围。自动重校准需要先附着到真实游戏窗口。')
      : uiT('ui.ocr_profile.no_target_hint', '当前还没有附着的 OCR 目标进程。你也可以先手动填写 process_name，把截图校准预先存起来。自动重校准需要先附着到真实游戏窗口。');
  }

  if (!processInput.value || document.activeElement !== processInput) {
    setInputValueIfIdle(processInput, runtime.process_name || processInput.value);
  }
  if (!saveScopeSelect.value) {
    saveScopeSelect.value = defaultSaveScope;
  }
  setInputValueIfIdle(leftInput, profileValues.left);
  setInputValueIfIdle(rightInput, profileValues.right);
  setInputValueIfIdle(topInput, profileValues.top);
  setInputValueIfIdle(bottomInput, profileValues.bottom);
  autoRecalibrateButton.disabled = Boolean(autoRecalibrateReason);
  autoRecalibrateButton.title = autoRecalibrateReason || uiT('ui.ocr_profile.auto_recalibrate.title', '使用当前附着窗口自动重校准对白区');
  if (applyRecommendedButton) {
    const applyBlocked = !recommendedProfile
      || !Object.keys(recommendedProfile).length
      || recommendedManualPresent;
    applyRecommendedButton.disabled = applyBlocked;
    applyRecommendedButton.title = recommendedManualPresent
      ? uiT('ui.ocr_profile.apply_recommended.manual_present_title', '已有手动 profile，推荐不会自动覆盖；可直接手动保存校准。')
      : (applyBlocked
        ? uiT('ui.ocr_profile.apply_recommended.blocked_title', '当前没有可应用的推荐 profile')
        : uiT('ui.ocr_profile.apply_recommended.title', '应用当前推荐 profile，并保留自动回滚点'));
  }
  if (rollbackButton) {
    rollbackButton.disabled = !pendingRollback;
    rollbackButton.title = pendingRollback
      ? uiT('ui.ocr_profile.rollback.title', '回滚最近一次推荐 profile 应用')
      : uiT('ui.ocr_profile.rollback.blocked_title', '当前没有待回滚的推荐 profile');
  }
  if (autoApplyInput && document.activeElement !== autoApplyInput) {
    autoApplyInput.checked = Boolean(runtime.capture_profile_auto_apply_enabled);
  }
}

function renderSnapshot(snapshot) {
  const state = snapshot.snapshot || {};
  renderGrid('snapshotGrid', [
    { label: 'game_id', value: snapshot.game_id || '' },
    { label: 'session_id', value: snapshot.session_id || '' },
    { label: 'speaker', value: state.speaker || '' },
    { label: 'text', value: state.text || '' },
    { label: 'stability', value: state.stability || '' },
    { label: 'scene_id', value: state.scene_id || '' },
    { label: 'line_id', value: state.line_id || '' },
    { label: 'route_id', value: state.route_id || '' },
    { label: 'is_menu_open', value: String(Boolean(state.is_menu_open)) },
    { label: 'snapshot_ts', value: snapshot.snapshot_ts || '' },
    { label: 'stale', value: String(Boolean(snapshot.stale)) },
  ]);
}

function renderHistory(history) {
  const mergedLines = mergedHistoryLines(history);
  const runtime = latestStatus?.ocr_reader_runtime || {};
  const fallbackItems = mergedLines.length ? mergedLines : [{
    speaker: 'OCR',
    scene_id: runtime.ocr_context_state || runtime.detail || '',
    stability: 'diagnostic',
    line_id: runtime.last_poll_completed_at || runtime.last_capture_completed_at || '',
    text: runtime.last_raw_ocr_text
      ? uiTf('ui.history.last_raw_ocr', '最近 raw OCR：{text}', { text: runtime.last_raw_ocr_text })
      : buildOcrMissingLineDiagnostic(latestStatus || {}),
    is_diagnostic: true,
  }];
  renderStackList('linesList', fallbackItems, (item) => `
    <article class="list-card">
      <p class="list-kicker">${escapeHtml(item.is_diagnostic ? uiT('ui.history.ocr_diagnostic', 'OCR 诊断') : (item.speaker || uiT('ui.history.narrator', '旁白')))} · ${escapeHtml(item.scene_id || '')} · ${escapeHtml(item.stability || '')}</p>
      <h3>${escapeHtml(item.is_diagnostic ? uiT('ui.history.no_stable_line', '未写入稳定台词') : (item.line_id || ''))}</h3>
      <p>${escapeHtml(item.text || '')}</p>
    </article>
  `);

  renderStackList('choicesList', history.choices || [], (item) => `
    <article class="list-card">
      <p class="list-kicker">${escapeHtml(item.action || '')} · #${escapeHtml(item.index || 0)}</p>
      <h3>${escapeHtml(item.choice_id || '')}</h3>
      <p>${escapeHtml(item.text || '')}</p>
    </article>
  `);

  renderStackList('eventsList', history.events || [], (item) => `
    <article class="list-card compact">
      <p class="list-kicker">seq ${escapeHtml(item.seq || 0)} · ${escapeHtml(item.type || '')}</p>
      <h3>${escapeHtml(item.line_id || item.scene_id || '')}</h3>
      <p>${escapeHtml(JSON.stringify(item.payload || {}))}</p>
    </article>
  `);
}

function formatInsightMeta(payload) {
  const inputSource = payload.input_source || (latestStatus && latestStatus.active_data_source) || 'unknown';
  const semantic = payload.semantic_granularity
    || (payload.semantic_degraded ? 'weaker_than_bridge_sdk' : 'bridge_sdk_level');
  const fallback = payload.fallback_used ? uiT('ui.common.yes', '是') : uiT('ui.common.no', '否');
  return uiTf('ui.suggest.meta', '输入源={inputSource} | degraded={degraded} | 语义粒度={semantic} | 使用回退={fallback}', {
    inputSource,
    degraded: Boolean(payload.degraded),
    semantic,
    fallback,
  });
}

function renderAgentStatus(payload) {
  latestAgentStatus = payload || latestAgentStatus;
  const replyNode = document.getElementById('agentReplyText');
  renderPreservingScroll(replyNode, () => {
    replyNode.textContent = latestAgentReply;
  });
  const memoryCounts = payload.memory_counts || {};
  const summaryDebug = payload.debug?.summary || {};
  const summaryTaskDebug = summaryDebug.task || {};
  const summaryRows = [
    { label: 'scene_summary_line_interval', value: payload.scene_summary_line_interval || '' },
    { label: 'scene_summary_lines_since_push', value: payload.scene_summary_lines_since_push || 0 },
    { label: 'scene_summary_lines_until_push', value: payload.scene_summary_lines_until_push || 0 },
    { label: 'pending_summary_task_count', value: summaryDebug.pending_summary_task_count || summaryTaskDebug.pending_count || 0 },
    { label: 'pending_summary_tasks', value: formatDebugValue(summaryDebug.pending_summary_tasks || summaryTaskDebug.pending || []) },
    { label: 'last_delivered_summary_key', value: summaryDebug.last_delivered_summary_key || summaryTaskDebug.last_delivered_summary_key || '' },
    { label: 'last_delivered_summary_seq', value: summaryDebug.last_delivered_summary_seq || summaryTaskDebug.last_delivered_summary_seq || 0 },
    { label: 'last_delivered_summary_scene_id', value: summaryDebug.last_delivered_summary_scene_id || summaryTaskDebug.last_delivered_summary_scene_id || '' },
    { label: 'last_session_transition_type', value: payload.last_session_transition_type || '' },
    { label: 'last_session_transition_reason', value: payload.last_session_transition_reason || '' },
    { label: 'last_session_transition_fields', value: formatDebugValue(payload.last_session_transition_fields || {}) },
    { label: 'summary_thresholds', value: formatDebugValue(summaryDebug.thresholds || {}) },
    { label: 'summary_scene_states', value: formatDebugValue(summaryDebug.scene_states || []) },
    { label: 'summary_last_scheduled', value: formatDebugValue(summaryDebug.last_scheduled || {}) },
    { label: 'summary_last_drop', value: formatDebugValue(summaryDebug.last_drop || {}) },
    { label: 'summary_last_skip', value: formatDebugValue(summaryDebug.last_skip || {}) },
    { label: 'summary_last_task_cancelled', value: formatDebugValue(summaryDebug.last_task_cancelled || summaryTaskDebug.last_cancelled || {}) },
    { label: 'summary_last_task_exception', value: formatDebugValue(summaryDebug.last_task_exception || summaryTaskDebug.last_exception || {}) },
    { label: 'summary_last_task_returned_false', value: formatDebugValue(summaryDebug.last_task_returned_false || summaryTaskDebug.last_returned_false || {}) },
    { label: 'summary_last_task_restored_schedule', value: formatDebugValue(summaryDebug.last_task_restored_schedule || summaryTaskDebug.last_restored_schedule || {}) },
    { label: 'summary_last_retry_reason', value: summaryDebug.last_retry_reason || '' },
    { label: 'summary_peek_session_transition', value: formatDebugValue(summaryDebug.peek_session_transition || {}) },
  ];
  renderAgentStatusGrid([
    {
      label: 'agent_user_status',
      value: agentUserStatusLabel(payload.agent_user_status, payload.agent_user_status || ''),
    },
    { label: 'status', value: payload.status || 'standby' },
    { label: 'activity', value: payload.activity || 'idle' },
    { label: 'reason', value: payload.reason || '' },
    { label: 'diagnostic', value: payload.debug?.ocr_capture_diagnostic || payload.error || '' },
    { label: 'input_source', value: payload.input_source || (latestStatus && latestStatus.active_data_source) || 'unknown' },
    { label: 'scene_stage', value: payload.scene_stage || 'unknown' },
    { label: 'scene_id', value: payload.scene_id || '' },
    { label: 'line_id', value: payload.line_id || '' },
    { label: 'push_policy', value: payload.push_policy || 'disabled' },
    { label: 'actionable', value: String(Boolean(payload.actionable)) },
    { label: 'standby_requested', value: String(Boolean(payload.standby_requested)) },
    {
      label: 'memory_counts',
      value: `scene=${memoryCounts.scene_memory || 0} choice=${memoryCounts.choice_memory || 0} failure=${memoryCounts.failure_memory || 0}`,
    },
    { label: 'inbound_queue_size', value: String(payload.inbound_queue_size || 0) },
    { label: 'outbound_queue_size', value: String(payload.outbound_queue_size || 0) },
    {
      label: 'last_interruption',
      value: payload.last_interruption?.interrupted_message_id || '',
    },
    {
      label: 'last_outbound_message',
      value: payload.last_outbound_message?.content || '',
    },
    { label: 'result', value: payload.result || '' },
    { label: 'recent_pushes', value: String((payload.recent_pushes || []).length) },
    { label: 'bridge_tick_auto_running', value: String(Boolean(latestStatus?.bridge_tick_auto_running)) },
    { label: 'last_agent_tick_age_seconds', value: String(latestStatus?.last_agent_tick_age_seconds || 0) },
    { label: 'bridge_tick_last_error', value: latestStatus?.bridge_tick_last_error || '' },
  ], summaryRows);

  renderStackList('pushesList', payload.recent_pushes || [], (item) => `
    <article class="list-card compact">
      <p class="list-kicker">${escapeHtml(item.kind || '')} | ${escapeHtml(formatPushStatus(item))} | ${escapeHtml(item.ts || '')}</p>
      <h3>${escapeHtml(item.scene_id || '')}</h3>
      <p>${escapeHtml(item.content || '')}</p>
      ${item.error ? `<p>${escapeHtml(item.error)}</p>` : ''}
    </article>
  `);
}

function renderAgentStatusGrid(rows, summaryRows) {
  const container = document.getElementById('agentStatusGrid');
  const debugWasOpen = Boolean(container?.querySelector('.status-debug-panel')?.open);
  renderPreservingScroll(container, () => {
    container.innerHTML = `
      ${renderDataRows(rows)}
      <details class="status-debug-panel"${debugWasOpen ? ' open' : ''}>
        <summary>
          <span>${escapeHtml(uiT('ui.agent.summary_debug', 'Summary 调试'))}</span>
          <small>${escapeHtml(uiTf('ui.agent.summary_debug_count', '{count} 项内部状态', { count: summaryRows.length }))}</small>
        </summary>
        <dl class="data-grid">
          ${renderDataRows(summaryRows)}
        </dl>
      </details>
    `;
  });
}

function formatDebugValue(value) {
  if (value == null || value === '') {
    return '';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch (error) {
    return String(value);
  }
}

function formatPushStatus(item = {}) {
  const parts = [item.status || ''];
  parts.push(item.delivered ? 'delivered' : 'not_delivered');
  if (item.suppressed) {
    parts.push('suppressed');
  }
  if (Number(item.retry_count || 0) > 0) {
    parts.push(`retry=${Number(item.retry_count || 0)}`);
  }
  if (item.error) {
    parts.push('error');
  }
  return parts.filter(Boolean).join(' | ');
}

function renderSuggest(payload) {
  const node = document.getElementById('suggestPanel');
  const choices = payload.choices || [];
  renderPreservingScroll(node, () => {
    node.className = 'result-panel scroll-region';
    node.innerHTML = `
      <p class="list-kicker">${escapeHtml(payload.scene_id || '')} | ${escapeHtml(formatInsightMeta(payload))}</p>
      <div class="stack-list">
        ${choices.length ? choices.map((item) => `
          <article class="list-card compact">
            <p class="list-kicker">rank ${escapeHtml(item.rank || 0)} | ${escapeHtml(item.choice_id || '')}</p>
            <h3>${escapeHtml(item.text || '')}</h3>
            <p>${escapeHtml(item.reason || '')}</p>
          </article>
        `).join('') : `<div class="empty-inline">${escapeHtml(payload.diagnostic || 'No suggestion yet')}</div>`}
      </div>
    `;
  });
  const mirror = document.getElementById('suggestPanelMirror');
  if (mirror && node) {
    mirror.innerHTML = node.innerHTML;
    mirror.className = node.className;
  }
}

async function refreshInsights(snapshot, { force = false, history = {}, status = {} } = {}) {
  const mode = String(status.mode || '').trim();
  if (mode === 'silent') {
    const diagnostic = uiT('ui.suggest.silent_diagnostic', '静默模式：不自动生成建议。');
    const suggest = buildSuggestFallback('', diagnostic);
    latestInsights.suggestKey = 'silent';
    latestInsights.suggestPayload = suggest;
    const scrollState = captureRefreshScrollState();
    renderSuggest(suggest);
    restoreRefreshScrollState(scrollState);
    return;
  }

  const state = snapshot.snapshot || {};
  const fallbackLine = effectiveCurrentLine(snapshot, history, status);
  const currentSceneId = state.scene_id || fallbackLine.scene_id || '';
  const choices = Array.isArray(state.choices) ? state.choices : [];
  const visibleChoiceMenu = Boolean(state.is_menu_open) && choices.length > 0;
  const hasChoices = mode === 'choice_advisor' && visibleChoiceMenu;
  const suggestKey = hasChoices
    ? `${currentSceneId}::${choices.map((item) => `${item.choice_id || ''}:${item.text || ''}`).join('|')}`
    : `${currentSceneId}::no-choices`;

  const suggestPromise = hasChoices
    ? (force || latestInsights.suggestKey !== suggestKey || !latestInsights.suggestPayload)
      ? safeCall(
        'galgame_suggest_choice',
        {},
        buildSuggestFallback(currentSceneId),
      )
      : Promise.resolve(latestInsights.suggestPayload)
    : Promise.resolve(buildSuggestFallback(
      currentSceneId,
      visibleChoiceMenu ? uiT('ui.suggest.companion_diagnostic', '伴读模式：不自动生成选项建议。') : 'no visible choices',
    ));

  const suggest = await suggestPromise;

  latestInsights.suggestKey = suggestKey;
  latestInsights.suggestPayload = suggest;

  const scrollState = captureRefreshScrollState();
  renderSuggest(suggest);
  restoreRefreshScrollState(scrollState);
}

function renderInsightsPending(message = uiT('ui.suggest.pending', '选项建议正在后台刷新...')) {
  const scrollState = captureRefreshScrollState();
  renderSuggest(buildSuggestFallback('', message));
  restoreRefreshScrollState(scrollState);
}

function runBackgroundTask(label, task) {
  Promise.resolve()
    .then(task)
    .catch((error) => {
      console.warn(`[galgame_plugin ui] ${label} failed`, error);
    });
}

function stopAutoRefresh() {
  if (autoRefreshTimer !== null) {
    window.clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

function desiredAutoRefreshInterval(status = latestStatus) {
  if (!status || status.connection_state === 'error' || status.agent_user_status === 'error') {
    return ERROR_REFRESH_INTERVAL_MS;
  }
  if (
    status.agent_user_status === 'paused_window_not_foreground'
    || status.agent_pause_kind === 'window_not_foreground'
  ) {
    return FOCUS_PAUSE_REFRESH_INTERVAL_MS;
  }
  const runtime = status.ocr_reader_runtime || {};
  const { observedText, stableText, effectiveText } = getCurrentLineTexts(status);
  const runtimeState = textValue(runtime.status);
  const contextState = textValue(status.ocr_context_state || runtime.ocr_context_state);
  if (
    status.bridge_poll_running === true
    || status.ocr_fast_loop_running === true
    || ['starting', 'active', 'running'].includes(runtimeState)
    || ['capture_pending', 'observed'].includes(contextState)
    || runtime.stable_ocr_block_reason === 'waiting_for_repeat'
    || Number(status.pending_ocr_advance_captures || 0) > 0
  ) {
    return AUTO_REFRESH_URGENT_INTERVAL_MS;
  }
  if (
    status.connection_state === 'active'
    || runtimeState === 'active'
    || runtimeState === 'running'
    || observedText
    || stableText
    || effectiveText
  ) {
    return AUTO_REFRESH_ACTIVE_INTERVAL_MS;
  }
  return AUTO_REFRESH_IDLE_INTERVAL_MS;
}

function startAutoRefresh(intervalMs = AUTO_REFRESH_INTERVAL_MS) {
  stopAutoRefresh();
  autoRefreshIntervalMs = intervalMs;
  autoRefreshTimer = window.setInterval(() => {
    if (document.hidden) {
      return;
    }
    refreshAll({ preserveFlash: true, silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
  }, intervalMs);
}

function syncAutoRefreshIntervalForStatus(status = latestStatus) {
  const desired = desiredAutoRefreshInterval(status);
  if (autoRefreshTimer !== null && desired !== autoRefreshIntervalMs) {
    startAutoRefresh(desired);
  }
}

function isOcrWindowModalOpen() {
  const modal = document.getElementById('ocrWindowModal');
  return Boolean(modal && !modal.hidden);
}

function isMemoryProcessModalOpen() {
  const modal = document.getElementById('memoryProcessModal');
  return Boolean(modal && !modal.hidden);
}

function shouldRefreshMemoryProcessesForStatus(status) {
  const runtime = status?.memory_reader_runtime || {};
  const detail = String(runtime.target_selection_detail || runtime.detail || '');
  return (
    detail === 'no_detected_game_process'
    || detail === 'manual_target_unavailable'
    || detail === 'manual_pid_unimplemented'
    || isMemoryProcessModalOpen()
  );
}

function shouldRefreshOcrWindowsForStatus(status) {
  const runtime = status?.ocr_reader_runtime || {};
  const detail = String(runtime.target_selection_detail || '');
  const context = String(status?.ocr_context_state || runtime.ocr_context_state || '');
  return (
    context === 'waiting_for_valid_window'
    || detail === 'no_eligible_window'
    || detail === 'foreground_window_needs_manual_confirmation'
    || detail === 'auto_detect_needs_manual_fallback'
    || detail === 'memory_reader_window_minimized'
    || Number(runtime.candidate_count || 0) === 0
    || isOcrWindowModalOpen()
  );
}

function refreshMemoryProcessTargetsIfNeeded({
  reason = '',
  force = false,
  silent = true,
} = {}) {
  if (memoryProcessRefreshInFlight) {
    return memoryProcessRefreshInFlight;
  }

  const now = Date.now();
  if (!force && now - lastMemoryProcessRefreshAt < MEMORY_PROCESS_REFRESH_TTL_MS) {
    return Promise.resolve(false);
  }

  memoryProcessRefreshInFlight = refreshMemoryProcesses({
    includeUnknown: true,
    silent,
  }).then((refreshed) => {
    if (refreshed) {
      lastMemoryProcessRefreshAt = Date.now();
    }
    return Boolean(refreshed);
  }).catch((error) => {
    console.warn(`[galgame_plugin ui] refresh Memory Reader processes for ${reason || 'unknown'} failed`, error);
    if (!silent) {
      setFlash(error instanceof Error ? error.message : String(error), 'error');
    }
    return false;
  }).finally(() => {
    memoryProcessRefreshInFlight = null;
  });
  return memoryProcessRefreshInFlight;
}

function refreshOcrWindowTargetsIfNeeded({
  reason = '',
  force = false,
  silent = true,
} = {}) {
  if (ocrWindowRefreshInFlight) {
    return ocrWindowRefreshInFlight;
  }

  const now = Date.now();
  if (!force && now - lastOcrWindowRefreshAt < OCR_WINDOW_REFRESH_TTL_MS) {
    return Promise.resolve(false);
  }

  ocrWindowRefreshInFlight = refreshOcrWindowTargets({
    includeExcluded: true,
    silent,
    force,
  }).then((refreshed) => {
    if (refreshed) {
      lastOcrWindowRefreshAt = Date.now();
    }
    return Boolean(refreshed);
  }).catch((error) => {
    console.warn(`[galgame_plugin ui] refresh OCR window targets for ${reason || 'unknown'} failed`, error);
    if (!silent) {
      setFlash(error instanceof Error ? error.message : String(error), 'error');
    }
    return false;
  }).finally(() => {
    ocrWindowRefreshInFlight = null;
  });
  return ocrWindowRefreshInFlight;
}

function refreshOcrWindowsOnPageFocus() {
  if (document.hidden) {
    return;
  }
  refreshOcrWindowTargetsIfNeeded({
    reason: 'page_focus',
    silent: true,
  }).catch((error) => { console.error('[galgame] async action failed', error); });
}

async function refreshAll(options = {}) {
  const {
    preserveFlash = false,
    silent = false,
    forceInsights = false,
    insightMode = 'background',
    showInsightPending = false,
    forceRefresh = false,
  } = options;
  if (refreshInFlight) {
    if (!forceRefresh) {
      return refreshInFlight;
    }
    try {
      await refreshInFlight;
    } catch (error) {
      console.warn('[galgame_plugin ui] ignored stale refresh before forced refresh', error);
    }
  }

  refreshInFlight = (async () => {
    if (!preserveFlash && !silent) {
      setFlash('', 'info');
    }
    try {
      const [status, snapshot, history] = await Promise.all([
        callPlugin('galgame_get_status', {}, { timeoutMs: PLUGIN_RUN_LIGHT_TIMEOUT_MS }),
        callPlugin('galgame_get_snapshot', {}, { timeoutMs: PLUGIN_RUN_LIGHT_TIMEOUT_MS }),
        callPlugin('galgame_get_history', { limit: 20, include_events: true }, { timeoutMs: PLUGIN_RUN_LIGHT_TIMEOUT_MS }),
      ]);
      const scrollState = captureRefreshScrollState();
      const agentStatus = status.agent || buildAgentStatusFromStatus(status);
      latestSnapshotData = snapshot;
      renderStatus(status);
      renderSnapshot(snapshot);
      renderHistory(history);
      renderAgentStatus(agentStatus);
      restoreRefreshScrollState(scrollState);
      if (shouldRefreshMemoryProcessesForStatus(status)) {
        runBackgroundTask('refresh Memory Reader processes after status', () => (
          refreshMemoryProcessTargetsIfNeeded({
            reason: 'status_needs_memory_process_refresh',
            silent: true,
          })
        ));
      }
      if (shouldRefreshOcrWindowsForStatus(status)) {
        const snapshotWindows = latestOcrWindowSnapshot && Array.isArray(latestOcrWindowSnapshot.windows)
          ? latestOcrWindowSnapshot.windows
          : [];
        const forceEmptyFocusedRefresh = Boolean(
          !emptyOcrWindowFocusForceRefreshDone
          && !document.hidden
          && document.hasFocus()
          && snapshotWindows.length === 0
          && Number((status.ocr_reader_runtime || {}).candidate_count || 0) === 0
        );
        if (forceEmptyFocusedRefresh) {
          emptyOcrWindowFocusForceRefreshDone = true;
        }
        runBackgroundTask('refresh OCR window targets after status', () => (
          refreshOcrWindowTargetsIfNeeded({
            reason: 'status_needs_window_refresh',
            force: forceEmptyFocusedRefresh,
            silent: true,
          })
        ));
      }
      if (showInsightPending && !latestInsights.suggestPayload) {
        renderInsightsPending();
      }
      if (insightMode !== 'none') {
        const insightRefresh = refreshInsights(snapshot, { force: forceInsights, history, status });
        if (insightMode === 'blocking') {
          await insightRefresh;
        } else {
          insightRefresh.catch((error) => {
            console.warn('[galgame_plugin ui] background insight refresh failed', error);
          });
        }
      }
      return true;
    } catch (error) {
      renderPluginUnavailable(error);
      if (silent) {
        console.warn('[galgame_plugin ui] refresh failed', error);
        return false;
      }
      if (isPluginNotStartedError(error)) {
        setFlash(uiT('ui.flash.plugin_not_started', '插件尚未启动。请在插件管理页面点击"启动"按钮。'), 'info');
      } else {
        setFlash(error instanceof Error ? error.message : String(error), 'error');
      }
      return false;
    }
  })();

  try {
    return await refreshInFlight;
  } finally {
    refreshInFlight = null;
  }
}

function buildAgentStatusFromStatus(status = {}) {
  return {
    action: 'peek_status',
    result: status.agent_error || '',
    status: status.agent_status || 'standby',
    agent_user_status: status.agent_user_status || '',
    activity: status.agent_activity || '',
    reason: status.agent_reason || '',
    error: status.agent_error || '',
    inbound_queue_size: status.agent_inbound_queue_size || 0,
    outbound_queue_size: status.agent_outbound_queue_size || 0,
    last_interruption: status.agent_last_interruption || {},
    last_outbound_message: status.agent_last_outbound_message || {},
    debug: {
      ocr_capture_diagnostic: status.agent_diagnostic || status.ocr_capture_diagnostic || '',
    },
    recent_pushes: latestAgentStatus?.recent_pushes || [],
  };
}

async function withButtonPending(buttonOrId, pendingText, fn) {
  const button = typeof buttonOrId === 'string'
    ? document.getElementById(buttonOrId)
    : buttonOrId;
  if (!button) {
    return fn();
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = pendingText;
  try {
    return await fn();
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function startInstall(kind, force = false) {
  const config = getInstallConfig(kind);

  navigateToInstallPanel(kind);

  const state = getInstallState(kind);
  const { button } = getInstallNodes(kind);
  state.inProgress = true;
  state.state = {
    kind,
    task_id: '',
    status: 'queued',
    phase: 'queued',
    message: uiTf('ui.install.task.creating', '正在创建 {label} 后台安装任务...', { label: config.label }),
    progress: 0.01,
    updated_at: Date.now() / 1000,
  };
  closeInstallStream(kind);
  clearInstallReconnectTimer(kind);
  button.disabled = true;
  button.textContent = uiT('ui.pending.installing', '准备安装...');
  if (latestStatus) {
    renderStatus(latestStatus);
  } else {
    renderInstallTaskState(kind);
  }
  setFlash(config.queuedFlash, 'info');

  try {
    const response = await fetch(config.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force }),
    });
    if (!response.ok) {
      throw new Error(uiTf('ui.error.install_task_create_failed', '创建 {label} 安装任务失败: HTTP {status}', {
        label: config.label,
        status: response.status,
      }));
    }

    const payload = await response.json();
    const taskId = payload.task_id || payload.run_id;
    if (!taskId) {
      throw new Error(uiTf('ui.error.install_task_id_missing', '未获取到 {label} 安装 task_id', { label: config.label }));
    }

    state.currentTaskId = taskId;
    persistInstallTaskId(kind, taskId);
    if (payload.state) {
      applyInstallTaskState(kind, payload.state, { allowRefresh: false });
    }
    connectInstallStream(kind, taskId);

    const initialState = await fetchInstallTaskState(kind, taskId);
    if (initialState) {
      applyInstallTaskState(kind, initialState, { allowRefresh: false });
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    state.inProgress = false;
    state.state = {
      kind,
      task_id: state.currentTaskId || '',
      status: 'failed',
      phase: 'failed',
      message,
      error: message,
      progress: 1,
      updated_at: Date.now() / 1000,
    };
    if (latestStatus) {
      renderStatus(latestStatus);
    } else {
      renderInstallTaskState(kind);
    }
    setFlash(message, 'error');
  }
}

async function installTextractor(force = false) {
  await startInstall('textractor', force);
}

// installRapidOcr / installDxcam removed — both kinds no longer have install
// machinery (bundled into main program; HTTP routes deleted in PR #1188).

async function installTesseract(force = false) {
  await startInstall('tesseract', force);
}

async function setOcrBackendSelection({ backendSelection = null, captureBackend = null } = {}) {
  const args = {};
  if (backendSelection) {
    args.backend_selection = backendSelection;
  }
  if (captureBackend) {
    args.capture_backend = captureBackend;
  }
  const label = backendSelection
    ? uiTf('ui.ocr.backend_selection_label', 'OCR 后端切换为 {backend}', { backend: backendSelection })
    : uiTf('ui.ocr.capture_backend_selection_label', '截图后端切换为 {backend}', { backend: captureBackend });
  try {
    setFlash(uiTf('ui.flash.saving_named_setting', '正在{label}...', { label }), 'info');
    await callPlugin('galgame_set_ocr_backend', args);
    setFlash(uiTf('ui.flash.named_setting_saved', '{label} 已保存', { label }), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

function clearSettingsAutosaveTimer() {
  if (settingsAutosaveTimer) {
    clearTimeout(settingsAutosaveTimer);
    settingsAutosaveTimer = null;
  }
}

function scheduleSettingsAutosave() {
  clearSettingsAutosaveTimer();
  settingsAutosaveTimer = window.setTimeout(() => {
    settingsAutosaveTimer = null;
    if (!settingsDirty || settingsSaveInFlight) {
      return;
    }
    saveMode({ auto: true }).catch((error) => {
      setFlash(error instanceof Error ? error.message : String(error), 'error');
    });
  }, SETTINGS_AUTOSAVE_DELAY_MS);
}

async function saveMode({ auto = false } = {}) {
  let modeCommitted = false;
  clearSettingsAutosaveTimer();
  const mode = document.getElementById('modeSelect').value;
  const pushNotifications = document.getElementById('pushToggle').checked;
  const advanceSpeed = document.getElementById('advanceSpeedSelect').value || 'medium';
  const readerMode = document.getElementById('readerModeSelect')?.value || 'auto';
  const ocrPollIntervalRaw = document.getElementById('ocrPollIntervalInput')?.value || '';
  const ocrPollInterval = Number(ocrPollIntervalRaw || 0.5);
  const ocrTriggerMode = document.getElementById('ocrTriggerModeSelect')?.value || 'interval';
  const visionEnabled = Boolean(document.getElementById('llmVisionToggle')?.checked);
  const fastLoopEnabled = Boolean(document.getElementById('fastLoopToggle')?.checked);
  const visionMaxRaw = document.getElementById('llmVisionMaxImagePxInput')?.value || '';
  const visionMaxImagePx = Number(visionMaxRaw || 768);
  if (!['auto', 'memory_reader', 'ocr_reader'].includes(readerMode)) {
    setFlash(uiT('ui.flash.invalid_reader_mode', '文本读取模式无效。'), 'error');
    clearPendingModeSelection(mode);
    return;
  }
  if (!Number.isFinite(ocrPollInterval) || ocrPollInterval < 0.5 || ocrPollInterval > 10) {
    setFlash(uiT('ui.flash.invalid_ocr_interval', 'OCR/DXcam 识别间隔必须在 0.5 到 10 秒之间。'), 'error');
    clearPendingModeSelection(mode);
    return;
  }
  if (!['interval', 'after_advance'].includes(ocrTriggerMode)) {
    setFlash(uiT('ui.flash.invalid_ocr_trigger', 'OCR 触发方式无效。'), 'error');
    clearPendingModeSelection(mode);
    return;
  }
  if (!Number.isFinite(visionMaxImagePx) || visionMaxImagePx < 64 || visionMaxImagePx > 2048) {
    setFlash(uiT('ui.flash.invalid_vision_max', 'Vision 最大边长必须在 64 到 2048 之间。'), 'error');
    clearPendingModeSelection(mode);
    return;
  }
  const requestId = ++modeSaveRequestId;
  try {
    if (mode && (!latestStatus || latestStatus.mode !== mode)) {
      pendingModeSelection = mode;
      updateModeSwitchControl(mode);
      updateSummaryMode(mode);
    }
    settingsSaveInFlight = true;
    updateSettingsDirtyHint(auto ? uiT('ui.pending.auto_saving', '正在自动保存...') : uiT('ui.pending.saving', '保存中...'));
    setFlash(auto ? uiT('ui.flash.auto_saving_settings', '正在自动保存设置...') : uiT('ui.flash.saving_settings', '正在保存设置...'), 'info');
    await callPlugin('galgame_set_mode', {
      mode,
      push_notifications: pushNotifications,
      advance_speed: advanceSpeed,
      reader_mode: readerMode,
    });
    modeCommitted = true;
    await callPlugin('galgame_set_ocr_timing', {
      poll_interval_seconds: ocrPollInterval,
      trigger_mode: ocrTriggerMode,
      fast_loop_enabled: fastLoopEnabled,
    });
    await callPlugin('galgame_set_llm_vision', {
      vision_enabled: visionEnabled,
      vision_max_image_px: Math.round(visionMaxImagePx),
    });
    if (requestId !== modeSaveRequestId) {
      return;
    }
    setFlash(auto ? uiT('ui.flash.settings_auto_saved', '设置已自动保存') : uiT('ui.flash.settings_saved', '设置已保存'), 'success');
    settingsDirty = false;
    settingsSaveInFlight = false;
    updateSettingsDirtyHint();
    await refreshAll({ preserveFlash: true, forceInsights: true, forceRefresh: true });
  } catch (error) {
    if (requestId !== modeSaveRequestId) {
      console.error('[galgame] stale saveMode error suppressed', error);
      return;
    }
    if (modeCommitted) {
      try {
        await refreshAll({ preserveFlash: true, forceRefresh: true });
      } catch (refreshError) {
        console.error('[galgame] mode save reconcile refresh failed', refreshError);
      }
    } else {
      clearPendingModeSelection(mode);
    }
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  } finally {
    if (requestId === modeSaveRequestId) {
      settingsSaveInFlight = false;
      updateSettingsDirtyHint();
    }
  }
}

async function saveOcrScreenTemplates() {
  const input = document.getElementById('ocrScreenTemplatesInput');
  if (!input) {
    return;
  }
  try {
    const templates = readOcrScreenTemplatesInput();
    setFlash(uiT('ui.flash.screen_templates_saving', '正在保存屏幕模板...'), 'info');
    const payload = await callPlugin('galgame_set_ocr_screen_templates', {
      screen_templates: templates,
    });
    input.value = formatScreenTemplatesForInput(payload.screen_templates || []);
    setFlash(payload.summary || uiT('ui.flash.screen_templates_saved', '屏幕模板已保存'), 'success');
    await refreshAll({ preserveFlash: true, forceRefresh: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

function readOcrScreenTemplatesInput() {
  const input = document.getElementById('ocrScreenTemplatesInput');
  let templates;
  try {
    templates = JSON.parse(input?.value || '[]');
  } catch (error) {
    throw new Error(error instanceof Error
      ? `${uiT('ui.error.template_json_parse_failed', '模板 JSON 解析失败')}：${error.message}`
      : uiT('ui.error.template_json_parse_failed_sentence', '模板 JSON 解析失败。'));
  }
  if (!Array.isArray(templates)) {
    throw new Error(uiT('ui.error.template_json_must_be_array', '模板 JSON 必须是数组。'));
  }
  return templates;
}

async function buildOcrScreenTemplateDraft() {
  const input = document.getElementById('ocrScreenTemplatesInput');
  if (!input) {
    return;
  }
  try {
    const templates = readOcrScreenTemplatesInput();
    const payload = await callPlugin('galgame_build_ocr_screen_template_draft', {});
    if (!payload.template || typeof payload.template !== 'object') {
      throw new Error(uiT('ui.error.no_template_draft_returned', '插件未返回可用模板草稿。'));
    }
    ocrScreenTemplatesUndoValue = input.value;
    setOcrScreenTemplatesUndoAvailable(true);
    templates.push(payload.template);
    input.value = formatScreenTemplatesForInput(templates);
    setFlash(payload.summary || uiT('ui.flash.screen_template_draft_created', '已生成屏幕模板草稿，可编辑后保存。'), 'success');
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function validateOcrScreenTemplates() {
  try {
    const templates = readOcrScreenTemplatesInput();
    const payload = await callPlugin('galgame_validate_ocr_screen_templates', {
      screen_templates: templates,
    });
    const classification = payload.classification || {};
    const screenType = classification.screen_type || 'default';
    const confidence = Number(classification.screen_confidence || 0).toFixed(2);
    setFlash(payload.summary || `${uiT('ui.flash.template_validation_result', '模板验证结果')}: ${screenType} (${confidence})`, 'success');
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

function undoOcrScreenTemplateDraft() {
  const input = document.getElementById('ocrScreenTemplatesInput');
  if (!input || !ocrScreenTemplatesUndoValue) {
    return;
  }
  input.value = ocrScreenTemplatesUndoValue;
  ocrScreenTemplatesUndoValue = '';
  setOcrScreenTemplatesUndoAvailable(false);
  setFlash(uiT('ui.flash.screen_template_draft_undone', '已撤销上一次模板草稿。'), 'info');
}

function getOcrRegionNodes() {
  return {
    editor: document.getElementById('ocrRegionEditor'),
    image: document.getElementById('ocrRegionImage'),
    box: document.getElementById('ocrRegionSelectionBox'),
    empty: document.getElementById('ocrRegionEmpty'),
    hint: document.getElementById('ocrRegionHint'),
    stage: document.getElementById('ocrRegionStageSelect'),
  };
}

function clampRegionValue(value) {
  return Math.max(0, Math.min(1, Number(value) || 0));
}

function normalizeOcrRegion(region) {
  const left = clampRegionValue(Math.min(region.left, region.right));
  const right = clampRegionValue(Math.max(region.left, region.right));
  const top = clampRegionValue(Math.min(region.top, region.bottom));
  const bottom = clampRegionValue(Math.max(region.top, region.bottom));
  return { left, top, right, bottom };
}

function ocrRegionFromPointer(event) {
  const { image } = getOcrRegionNodes();
  if (!image || image.hidden) {
    return null;
  }
  const imageRect = image.getBoundingClientRect();
  if (!imageRect.width || !imageRect.height) {
    return null;
  }
  return {
    left: clampRegionValue((event.clientX - imageRect.left) / imageRect.width),
    top: clampRegionValue((event.clientY - imageRect.top) / imageRect.height),
  };
}

function renderOcrRegionSelection() {
  const { editor, image, box, hint } = getOcrRegionNodes();
  if (!editor || !image || !box) {
    return;
  }
  if (!ocrRegionSelection) {
    box.hidden = true;
    if (hint) {
      hint.textContent = ocrRegionSnapshot
        ? uiT('ui.screen_templates.drag_region_hint', '在截图上拖拽框选区域。')
        : uiT('ui.screen_templates.region_hint', '加载最近 OCR 屏幕感知截图后，拖拽框选区域。');
    }
    return;
  }
  const region = normalizeOcrRegion(ocrRegionSelection);
  const editorRect = editor.getBoundingClientRect();
  const imageRect = image.getBoundingClientRect();
  const leftPx = imageRect.left - editorRect.left + region.left * imageRect.width;
  const topPx = imageRect.top - editorRect.top + region.top * imageRect.height;
  const widthPx = Math.max(1, (region.right - region.left) * imageRect.width);
  const heightPx = Math.max(1, (region.bottom - region.top) * imageRect.height);
  box.hidden = false;
  box.style.left = `${leftPx}px`;
  box.style.top = `${topPx}px`;
  box.style.width = `${widthPx}px`;
  box.style.height = `${heightPx}px`;
  if (hint) {
    hint.textContent = `${uiT('ui.screen_templates.region_label', '区域')}: left=${region.left.toFixed(3)}, top=${region.top.toFixed(3)}, right=${region.right.toFixed(3)}, bottom=${region.bottom.toFixed(3)}`;
  }
}

async function loadOcrRegionSnapshot() {
  const { image, empty, hint } = getOcrRegionNodes();
  try {
    const payload = await callPlugin('galgame_get_ocr_screen_awareness_snapshot', {});
    const snapshot = payload.snapshot || {};
    const imageBase64 = snapshot.vision_image_base64 || '';
    if (!imageBase64) {
      throw new Error(uiT('ui.error.no_ocr_snapshot', '当前没有可用截图。请先开启 Vision，并等待 OCR 完成一次全画面感知。'));
    }
    ocrRegionSnapshot = snapshot;
    ocrRegionSelection = null;
    if (image) {
      image.hidden = false;
      image.src = imageBase64;
      image.onload = () => renderOcrRegionSelection();
    }
    if (empty) {
      empty.hidden = true;
    }
    if (hint) {
      hint.textContent = payload.summary || uiT('ui.screen_templates.snapshot_loaded_hint', '截图已加载，可拖拽框选区域。');
    }
    renderOcrRegionSelection();
    setFlash(payload.summary || uiT('ui.flash.ocr_snapshot_loaded', 'OCR 屏幕感知截图已加载'), 'success');
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function buildOcrRegionTemplateDraft() {
  if (!ocrRegionSelection) {
    setFlash(uiT('ui.flash.select_region_first', '请先在截图上框选区域。'), 'error');
    return;
  }
  const input = document.getElementById('ocrScreenTemplatesInput');
  const { stage } = getOcrRegionNodes();
  if (!input) {
    return;
  }
  try {
    const templates = readOcrScreenTemplatesInput();
    const selectedStage = stage?.value || 'dialogue_stage';
    const payload = await callPlugin('galgame_build_ocr_screen_template_draft', {
      stage: selectedStage,
      region: {
        role: selectedStage,
        ...normalizeOcrRegion(ocrRegionSelection),
      },
    });
    ocrScreenTemplatesUndoValue = input.value;
    setOcrScreenTemplatesUndoAvailable(true);
    templates.push(payload.template);
    input.value = formatScreenTemplatesForInput(templates);
    setFlash(payload.summary || uiT('ui.flash.region_template_draft_created', '已根据框选区域生成模板草稿。'), 'success');
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

function applyOcrRegionToCaptureProfileForm() {
  if (!ocrRegionSelection) {
    setFlash(uiT('ui.flash.select_region_first', '请先在截图上框选区域。'), 'error');
    return;
  }
  const region = normalizeOcrRegion(ocrRegionSelection);
  const { stage } = getOcrRegionNodes();
  const profileStage = document.getElementById('ocrProfileStageSelect');
  const leftInput = document.getElementById('ocrProfileLeftInput');
  const rightInput = document.getElementById('ocrProfileRightInput');
  const topInput = document.getElementById('ocrProfileTopInput');
  const bottomInput = document.getElementById('ocrProfileBottomInput');
  if (profileStage && stage?.value) {
    profileStage.value = stage.value;
  }
  if (leftInput) {
    leftInput.value = region.left.toFixed(2);
  }
  if (rightInput) {
    rightInput.value = Math.max(0, 1 - region.right).toFixed(2);
  }
  if (topInput) {
    topInput.value = region.top.toFixed(2);
  }
  if (bottomInput) {
    bottomInput.value = Math.max(0, 1 - region.bottom).toFixed(2);
  }
  setFlash(uiT('ui.flash.region_applied_to_profile', '已将框选区域套用到 OCR 截图校准表单，确认后可保存。'), 'success');
}

function startOcrRegionDrag(event) {
  if (event.button !== undefined && event.button !== 0) {
    return;
  }
  const point = ocrRegionFromPointer(event);
  if (!point) {
    return;
  }
  ocrRegionDragStart = point;
  ocrRegionSelection = {
    left: point.left,
    top: point.top,
    right: point.left,
    bottom: point.top,
  };
  event.currentTarget.setPointerCapture?.(event.pointerId);
  renderOcrRegionSelection();
}

function moveOcrRegionDrag(event) {
  if (!ocrRegionDragStart) {
    return;
  }
  const point = ocrRegionFromPointer(event);
  if (!point) {
    return;
  }
  ocrRegionSelection = normalizeOcrRegion({
    left: ocrRegionDragStart.left,
    top: ocrRegionDragStart.top,
    right: point.left,
    bottom: point.top,
  });
  renderOcrRegionSelection();
}

function finishOcrRegionDrag(event) {
  if (!ocrRegionDragStart) {
    return;
  }
  event.currentTarget.releasePointerCapture?.(event.pointerId);
  ocrRegionDragStart = null;
  const region = ocrRegionSelection ? normalizeOcrRegion(ocrRegionSelection) : null;
  if (!region || (region.right - region.left) * (region.bottom - region.top) < 0.0025) {
    ocrRegionSelection = null;
  } else {
    ocrRegionSelection = region;
  }
  renderOcrRegionSelection();
}

async function bindGame(gameId = '') {
  const normalized = String(gameId || '').trim();
  try {
    setFlash(normalized
      ? uiTf('ui.flash.binding_game', '正在绑定 {gameId}...', { gameId: normalized })
      : uiT('ui.flash.restoring_auto_select', '正在恢复自动选择...'), 'info');
    await callPlugin('galgame_bind_game', { game_id: normalized });
    setFlash(normalized
      ? uiTf('ui.flash.game_bound', '已绑定 {gameId}', { gameId: normalized })
      : uiT('ui.flash.auto_select_restored', '已恢复自动选择'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function setStandby(standby) {
  try {
    setFlash(standby ? uiT('ui.flash.entering_standby', '正在进入待机...') : uiT('ui.flash.resuming_active', '正在恢复活跃...'), 'info');
    const payload = await callPlugin('galgame_agent_command', {
      action: 'set_standby',
      standby,
    });
    latestAgentReply = payload.result || latestAgentReply;
    setFlash(standby ? uiT('ui.flash.standby_enabled', '已切换到待机') : uiT('ui.flash.active_resumed', '已恢复活跃'), 'success');
    refreshAll({ preserveFlash: true, forceInsights: true }).catch((error) => {
      console.warn('[galgame_plugin ui] refresh after standby change failed', error);
    });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function resumeAgentFromButton() {
  const action = document.getElementById('standbyOffBtn').dataset.resumeAction || 'noop';
  if (action === 'focus') {
    setFlash(uiT('ui.flash.resume_focus_pause', '当前是窗口失焦暂停。请切回游戏窗口，Agent 会自动继续；恢复活跃只解除手动待机。'), 'info');
    refreshAll({ preserveFlash: true, silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
    return;
  }
  if (action === 'read_only') {
    setFlash(uiT('ui.flash.resume_read_only_mode', '当前为伴读/静默模式，不会自动点击。需要自动推进时请切到“自动推进”。'), 'info');
    return;
  }
  if (action === 'noop') {
    setFlash(uiT('ui.flash.no_manual_standby', 'Agent 当前没有手动待机。'), 'info');
    return;
  }
  await setStandby(false);
}

async function askAgent(action) {
  const prompt = document.getElementById('agentPromptInput').value.trim();
  if (!prompt) {
    setFlash(uiT('ui.flash.agent_prompt_required', '请输入要发送给 Agent 的文本'), 'error');
    return;
  }

  try {
    setFlash(action === 'query_context' ? uiT('ui.flash.querying_context', '正在查询上下文...') : uiT('ui.flash.sending_agent', '正在发送给 Agent...'), 'info');
    const payload = await callPlugin(
      'galgame_agent_command',
      action === 'query_context'
        ? { action, context_query: prompt }
        : { action, message: prompt },
    );
    latestAgentReply = payload.result || uiT('ui.agent.no_reply', 'Agent 未返回文本');
    setFlash(uiT('ui.flash.agent_responded', 'Agent 已响应'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

function readProfileNumber(id, label) {
  const raw = document.getElementById(id).value.trim();
  const value = Number(raw);
  if (!raw) {
    throw new Error(uiTf('ui.error.field_required', '{field} 不能为空', { field: label }));
  }
  if (!Number.isFinite(value)) {
    throw new Error(uiTf('ui.error.field_must_be_number', '{field} 必须是数字', { field: label }));
  }
  if (value < 0 || value >= 1) {
    throw new Error(uiTf('ui.error.field_range_0_099', '{field} 必须在 0.00 到 0.99 之间', { field: label }));
  }
  return value;
}

async function saveOcrCaptureProfile() {
  try {
    const processName = document.getElementById('ocrProfileProcessInput').value.trim();
    const stage = document.getElementById('ocrProfileStageSelect').value || 'default';
    const saveScope = normalizeCaptureProfileSaveScope(
      document.getElementById('ocrProfileSaveScopeSelect').value,
    );
    const leftInsetRatio = readProfileNumber('ocrProfileLeftInput', 'left_inset_ratio');
    const rightInsetRatio = readProfileNumber('ocrProfileRightInput', 'right_inset_ratio');
    const topRatio = readProfileNumber('ocrProfileTopInput', 'top_ratio');
    const bottomInsetRatio = readProfileNumber('ocrProfileBottomInput', 'bottom_inset_ratio');
    const payload = await callPlugin('galgame_set_ocr_capture_profile', {
      process_name: processName,
      stage,
      save_scope: saveScope,
      left_inset_ratio: leftInsetRatio,
      right_inset_ratio: rightInsetRatio,
      top_ratio: topRatio,
      bottom_inset_ratio: bottomInsetRatio,
      clear: false,
    });
    setFlash(payload.summary || uiT('ui.flash.ocr_profile_saved', 'OCR 截图校准已保存'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function clearOcrCaptureProfile() {
  try {
    const processName = document.getElementById('ocrProfileProcessInput').value.trim();
    const stage = document.getElementById('ocrProfileStageSelect').value || 'default';
    const saveScope = normalizeCaptureProfileSaveScope(
      document.getElementById('ocrProfileSaveScopeSelect').value,
    );
    const payload = await callPlugin('galgame_set_ocr_capture_profile', {
      process_name: processName,
      stage,
      save_scope: saveScope,
      clear: true,
    });
    setFlash(payload.summary || uiT('ui.flash.ocr_profile_cleared', 'OCR 截图校准已清空'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function autoRecalibrateOcrDialogueProfile() {
  try {
    const payload = await callPlugin('galgame_auto_recalibrate_ocr_dialogue_profile', {});
    const sampleText = String(payload.sample_text || '').trim();
    const summary = payload.summary || uiT('ui.flash.ocr_dialogue_auto_recalibrated', 'OCR 对白区已自动重校准');
    setFlash(sampleText ? `${summary} | ${sampleText}` : summary, 'success');
    const saveScopeSelect = document.getElementById('ocrProfileSaveScopeSelect');
    saveScopeSelect.value = 'window_bucket';
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function applyRecommendedOcrCaptureProfile() {
  try {
    const autoApplyInput = document.getElementById('ocrProfileAutoApplyRecommendedInput');
    const payload = await callPlugin('galgame_apply_recommended_ocr_capture_profile', {
      confirm: true,
      enable_auto_apply: Boolean(autoApplyInput?.checked),
      allow_manual_override: false,
    });
    setFlash(payload.summary || uiT('ui.flash.ocr_recommended_profile_applied', 'OCR 推荐截图校准已应用'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function rollbackOcrCaptureProfileRecommendation() {
  try {
    const payload = await callPlugin('galgame_rollback_ocr_capture_profile', {
      confirm: true,
    });
    setFlash(payload.summary || uiT('ui.flash.ocr_recommended_profile_rolled_back', 'OCR 推荐截图校准已回滚'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function refreshMemoryProcesses({ includeUnknown = true, silent = false } = {}) {
  try {
    const payload = await callPlugin('galgame_list_memory_reader_processes', {
      include_unknown: Boolean(includeUnknown),
    });
    renderMemoryProcessTargetSnapshot(payload, latestStatus);
    return true;
  } catch (error) {
    if (silent) {
      console.warn('[galgame_plugin ui] refresh Memory Reader processes failed', error);
      return false;
    }
    setFlash(error instanceof Error ? error.message : String(error), 'error');
    return false;
  }
}

async function openMemoryProcessModal() {
  const modal = document.getElementById('memoryProcessModal');
  const modalList = document.getElementById('memoryProcessList');
  modal.hidden = false;
  renderPreservingScroll(modalList, () => {
    modalList.className = 'stack-list scroll-region empty-state window-candidate-list';
    modalList.textContent = uiT('ui.memory.loading_processes', '正在加载可用进程...');
  });
  const refreshed = await refreshMemoryProcessTargetsIfNeeded({
    reason: 'open_memory_process_modal',
    force: true,
    silent: false,
  });
  if (!refreshed && !latestMemoryProcessSnapshot) {
    setFlash(uiT('ui.flash.memory_process_refresh_failed', 'Memory Reader 进程列表刷新失败，请稍后重试。'), 'warning');
  }
  const snapshot = latestMemoryProcessSnapshot || {};
  renderMemoryProcessListToNode(modalList, snapshot.processes || []);
}

function closeMemoryProcessModal() {
  const modal = document.getElementById('memoryProcessModal');
  modal.hidden = true;
}

async function setMemoryProcessTarget(processKey) {
  try {
    setFlash(uiT('ui.flash.locking_memory_process', '正在锁定 Memory Reader 进程...'), 'info');
    const payload = await callPlugin('galgame_set_memory_reader_target', {
      process_key: processKey,
      clear: false,
    });
    const target = payload.process_target || {};
    setFlash(uiTf('ui.flash.memory_process_locked', '已锁定 Memory Reader 进程：{process}', {
      process: target.process_name || processKey || uiT('ui.memory.target_process', '目标进程'),
    }), 'success');
    closeMemoryProcessModal();
    refreshAll({ preserveFlash: true, forceInsights: true }).catch((error) => {
      console.warn('[galgame_plugin ui] refresh after Memory Reader process lock failed', error);
    });
    refreshMemoryProcessTargetsIfNeeded({
      reason: 'lock_memory_process',
      force: true,
      silent: true,
    }).catch((error) => { console.error('[galgame] async action failed', error); });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function clearMemoryProcessTarget() {
  try {
    setFlash(uiT('ui.flash.clearing_memory_process_lock', '正在清除 Memory Reader 手动进程锁定...'), 'info');
    await callPlugin('galgame_set_memory_reader_target', { clear: true });
    setFlash(uiT('ui.flash.memory_process_auto_restored', 'Memory Reader 已恢复自动进程检测'), 'success');
    await refreshAll({ preserveFlash: true, forceInsights: true });
    refreshMemoryProcessTargetsIfNeeded({
      reason: 'clear_memory_process',
      force: true,
      silent: true,
    }).catch((error) => { console.error('[galgame] async action failed', error); });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function refreshOcrWindowTargets({ includeExcluded = true, silent = false, force = false } = {}) {
  try {
    const payload = await callPlugin('galgame_list_ocr_windows', {
      include_excluded: Boolean(includeExcluded),
      force: Boolean(force),
    });
    renderOcrWindowTargetSnapshot(payload, latestStatus);
    return true;
  } catch (error) {
    if (silent) {
      console.warn('[galgame_plugin ui] refresh OCR window targets failed', error);
      return false;
    }
    setFlash(error instanceof Error ? error.message : String(error), 'error');
    return false;
  }
}

async function openOcrWindowModal() {
  const modal = document.getElementById('ocrWindowModal');
  const modalList = document.getElementById('ocrWindowList');
  modal.hidden = false;
  renderPreservingScroll(modalList, () => {
    modalList.className = 'stack-list scroll-region empty-state window-candidate-list';
    modalList.textContent = uiT('ui.ocr.window.loading_windows', '正在加载可用游戏窗口...');
  });
  const refreshed = await refreshOcrWindowTargetsIfNeeded({
    reason: 'open_window_modal',
    force: true,
    silent: false,
  });
  if (!refreshed && !latestOcrWindowSnapshot) {
    setFlash(uiT('ui.flash.ocr_window_refresh_failed', 'OCR 窗口列表刷新失败，请稍后重试。'), 'warning');
  }
  const snapshot = latestOcrWindowSnapshot || {};
  renderOcrWindowListToNode(modalList, snapshot.windows || []);
}

function closeOcrWindowModal() {
  const modal = document.getElementById('ocrWindowModal');
  modal.hidden = true;
}

async function setOcrWindowTarget(windowKey) {
  try {
    setFlash(uiT('ui.flash.locking_ocr_window', '正在锁定 OCR 识别窗口...'), 'info');
    const payload = await callPlugin('galgame_set_ocr_window_target', {
      window_key: windowKey,
      clear: false,
    });
    const target = payload.window_target || {};
    const targetName = target.process_name || target.normalized_title || uiT('ui.ocr.window.target_window', '目标窗口');
    setFlash(uiTf('ui.flash.ocr_window_locked', '已锁定 OCR 识别窗口：{target}。后台正在刷新识别状态。', {
      target: targetName,
    }), 'success');
    closeOcrWindowModal();
    refreshAll({ preserveFlash: true, forceInsights: true }).catch((error) => {
      console.warn('[galgame_plugin ui] refresh after OCR window lock failed', error);
    });
    refreshOcrWindowTargetsIfNeeded({
      reason: 'lock_window_target',
      force: true,
      silent: true,
    }).catch((error) => { console.error('[galgame] async action failed', error); });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function clearOcrWindowTarget() {
  try {
    setFlash(uiT('ui.flash.clearing_ocr_window', '正在清除 OCR 目标窗口...'), 'info');
    const payload = await callPlugin('galgame_set_ocr_window_target', {
      clear: true,
    });
    setFlash(payload.summary || uiT('ui.flash.ocr_window_cleared', '已清除 OCR 目标窗口。插件会重新尝试自动检测；识别不到时再手动选择。'), 'success');
    refreshAll({ preserveFlash: true, forceInsights: true }).catch((error) => {
      console.warn('[galgame_plugin ui] refresh after OCR target clear failed', error);
    });
    refreshOcrWindowTargetsIfNeeded({
      reason: 'clear_window_target',
      force: true,
      silent: true,
    }).catch((error) => { console.error('[galgame] async action failed', error); });
  } catch (error) {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  }
}

async function clearOcrWindowTargetWithFeedback() {
  await withButtonPending('ocrWindowAutoBtn', uiT('ui.pending.clearing', '清除中...'), clearOcrWindowTarget);
}

function expandAndScrollTo(elementId) {
  const node = document.getElementById(elementId);
  if (!node) {
    return;
  }
  const details = node.closest('details');
  if (details) {
    details.open = true;
  }
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function revealDebugDetails() {
  expandAndScrollTo('ocrRuntimeGrid');
}

function revealLineDetails() {
  expandAndScrollTo('currentLineOverview');
}

function revealCaptureBackendSettings() {
  // navigateToInstallPanel expands the Advanced Settings container + the
  // dependencies <details>, otherwise expandAndScrollTo would scroll past
  // a still-collapsed parent and the user sees nothing. The 'dxcam' kind
  // skips the install-tab switch (not in OCR_INSTALL_TABS post-bundling)
  // but the expansion side-effects still apply.
  // `scrollToSection: false` so navigateToInstallPanel's queued
  // installSection.scrollIntoView doesn't snap the viewport back to the
  // top of installSection on the next frame and hide dxcamPrompt.
  navigateToInstallPanel('dxcam', { scrollToSection: false });
  expandAndScrollTo('dxcamPrompt');
}

async function refreshStatusAndWindowsFromAction() {
  setFlash(uiT('ui.flash.refreshing_status_and_windows', '正在刷新状态和窗口列表...'), 'info');
  const loaded = await refreshAll({ preserveFlash: true, forceInsights: true, showInsightPending: true });
  const windowsLoaded = loaded
    ? await refreshOcrWindowTargetsIfNeeded({
      reason: 'primary_diagnosis',
      force: true,
      silent: false,
    })
    : false;
  setFlash(
    loaded
      ? (windowsLoaded
        ? uiT('ui.flash.status_and_windows_refreshed', '状态和窗口列表已刷新。')
        : uiT('ui.flash.status_refreshed_windows_failed', '状态已刷新；窗口列表刷新失败，请稍后重试。'))
      : uiT('ui.flash.status_refresh_failed', '状态刷新失败，请稍后重试。'),
    loaded && windowsLoaded ? 'success' : 'warning',
  );
}

async function switchToChoiceAdvisorMode() {
  const modeSelect = document.getElementById('modeSelect');
  if (modeSelect) {
    modeSelect.value = 'choice_advisor';
  }
  await saveMode();
}

async function handleDiagnosisAction(action) {
  switch (action) {
    case 'refresh_all':
      await refreshStatusAndWindowsFromAction();
      break;
    case 'refresh_ocr_windows':
      await refreshStatusAndWindowsFromAction();
      break;
    case 'select_ocr_window':
      await openOcrWindowModal();
      break;
    case 'debug_details':
      revealDebugDetails();
      setFlash(uiT('ui.flash.debug_details_expanded', '已展开 OCR 运行时调试详情。'), 'info');
      break;
    case 'line_details':
      revealLineDetails();
      setFlash(uiT('ui.flash.line_details_revealed', '已定位到当前台词识别详情。'), 'info');
      break;
    case 'recalibrate_ocr':
      await autoRecalibrateOcrDialogueProfile();
      break;
    case 'capture_backend':
      revealCaptureBackendSettings();
      setFlash(uiT('ui.flash.capture_backend_settings_revealed', '已定位到截图方式设置。可以切换 DXcam、MSS、PyAutoGUI 或 PrintWindow。'), 'info');
      break;
    case 'install_rapidocr':
      // The action ID is kept for backward compat with diagnosis emitters
      // (`withRapidOcrInstallAction`, onboarding button). Behavior changed:
      // RapidOCR is bundled now, no in-app install. navigateToInstallPanel
      // expands the Advanced Settings + dependencies containers so the
      // banner is actually visible after the scroll; bundled_hint copy on
      // the banner explains what to do (reinstall packaged build /
      // `uv sync --group galgame`). `scrollToSection: false` keeps
      // navigateToInstallPanel from snapping back to installSection top
      // on the next frame and hiding rapidocrPrompt.
      navigateToInstallPanel('rapidocr', { scrollToSection: false });
      expandAndScrollTo('rapidocrPrompt');
      setFlash(uiT('ui.flash.rapidocr_hint_revealed', '已定位到 RapidOCR 状态横幅。请按横幅说明操作（重装打包版 / uv sync --group galgame）。'), 'info');
      break;
    case 'install_textractor':
      // Textractor is a desktop binary (GitHub releases), not a Python
      // wheel — it still has a real runtime install flow via the
      // `galgame_install_textractor` SDK action. Trigger it directly so
      // the "Memory Reader 缺 Textractor" diagnosis is actionable.
      await installTextractor(false);
      break;
    case 'choice_advisor':
      await switchToChoiceAdvisorMode();
      break;
    case 'focus_game':
      setFlash(uiT('ui.flash.focus_game_window', '请切回游戏窗口。窗口回到前台后，插件会在下一轮刷新中继续识别。'), 'info');
      break;
    default:
      setFlash(uiT('ui.flash.action_unavailable', '这个操作暂时不可用。'), 'warning');
      break;
  }
}

function switchInstallTab(tab) {
  activeInstallTab = OCR_INSTALL_TABS.includes(tab) ? tab : 'tesseract';
  document.querySelectorAll('.install-tab').forEach((btn) => {
    if (btn.dataset.installTab === activeInstallTab) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });
  OCR_INSTALL_TABS.forEach((kind) => {
    const banner = document.getElementById(`${kind}Prompt`);
    if (banner) {
      banner.hidden = kind !== activeInstallTab;
    }
  });
  const memoryBanner = document.getElementById('textractorPrompt');
  if (memoryBanner) {
    memoryBanner.hidden = false;
  }
}

function navigateToInstallPanel(kind, { scrollToSection = true } = {}) {
  // `scrollToSection: false` callers (revealCaptureBackendSettings,
  // case 'install_rapidocr') only want the parent-container expansion
  // side effects — they do their own fine-grained scroll afterwards via
  // expandAndScrollTo(banner). Without this opt-out, the queued
  // installSection.scrollIntoView in the next frame snaps the viewport
  // back to the top of installSection, hiding the banner the caller
  // just scrolled to.
  const advancedSettings = document.getElementById('advancedSettings');
  const advancedToggleBtn = document.getElementById('advancedToggleBtn');
  const dependencyModule = document.getElementById('dependencyModule');
  const installSection = document.getElementById('installSection');

  try {
    localStorage.setItem('galgame_skip_onboarding', '1');
  } catch (error) {
    console.warn('[galgame_plugin ui] persist onboarding skip failed', error);
  }
  document.body.classList.remove('onboarding-active');
  const onboardingView = document.getElementById('onboardingView');
  if (onboardingView) {
    onboardingView.hidden = true;
  }

  if (advancedSettings && !advancedSettings.classList.contains('open')) {
    advancedSettings.classList.add('open');
    if (advancedToggleBtn) {
      advancedToggleBtn.textContent = uiT('ui.advanced.collapse_settings', '收起高级设置');
    }
    const firstRunGuide = document.getElementById('firstRunGuide');
    if (firstRunGuide) {
      firstRunGuide.hidden = true;
    }
  }

  if (dependencyModule && !dependencyModule.open) {
    dependencyModule.open = true;
  }

  if (OCR_INSTALL_TABS.includes(kind)) {
    switchInstallTab(kind);
  }

  if (scrollToSection && installSection) {
    requestAnimationFrame(() => {
      installSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }
}

async function initialize() {
  initializePanelFullscreenControls();
  initCurrentLineUiPrefs();
  initPipelineZoom();
  initPipelineCollapse();
  initOcrWindowCollapse();
  updateSettingsDirtyHint();
  switchInstallTab(activeInstallTab);

  const skipOnboarding = localStorage.getItem('galgame_skip_onboarding') === '1';
  if (!skipOnboarding) {
    document.body.classList.add('onboarding-active');
    const onboardingView = document.getElementById('onboardingView');
    if (onboardingView) { onboardingView.hidden = false; }
  }

  try {
    localStorage.removeItem(`${PLUGIN_ID}:last_ui_state:v1`);
  } catch (error) {
    console.warn('[galgame_plugin ui] clear cached state failed', error);
  }
  renderInsightsPending(uiT('ui.suggest.initial_pending', '等待首轮状态刷新；选项建议会在后台更新。'));
    setFlash(uiT('ui.flash.loading_status', '正在加载插件状态...'), 'info');
  const loaded = await refreshAll({ forceInsights: false, showInsightPending: true });
  if (loaded) {
    setFlash(uiT('ui.flash.loaded_status', '插件状态已加载；窗口列表、依赖状态和选项建议正在后台更新。'), 'success');
  }
  runBackgroundTask('refresh Memory Reader processes', () => (
    refreshMemoryProcessTargetsIfNeeded({
      reason: 'initialize',
      force: true,
      silent: true,
    })
  ));
  runBackgroundTask('refresh OCR window targets', () => (
    refreshOcrWindowTargetsIfNeeded({
      reason: 'initialize',
      force: true,
      silent: true,
    })
  ));
  runBackgroundTask('restore install states', () => Promise.all([
    // restoreRapidOcrInstallState / restoreDxcamInstallState removed (bundled).
    restoreTesseractInstallState(),
    restoreTextractorInstallState(),
  ]));
  startAutoRefresh();
}

document.getElementById('refreshBtn').addEventListener('click', async () => {
  await withButtonPending('refreshBtn', uiT('ui.pending.refreshing', '刷新中...'), async () => {
    setFlash(uiT('ui.flash.refreshing_plugin_status', '正在刷新插件状态...'), 'info');
    const loaded = await refreshAll({ forceInsights: true, showInsightPending: true });
    if (loaded) {
      refreshMemoryProcessTargetsIfNeeded({
        reason: 'manual_refresh',
        force: true,
        silent: true,
      }).catch((error) => { console.error('[galgame] async action failed', error); });
    }
    const windowsLoaded = loaded
      ? await refreshOcrWindowTargetsIfNeeded({
        reason: 'manual_refresh',
        force: true,
        silent: true,
      })
      : false;
    if (loaded) {
      setFlash(
        windowsLoaded
          ? uiT('ui.flash.status_windows_refreshed_insights_pending', '状态和窗口列表已刷新；选项建议在后台更新。')
          : uiT('ui.flash.status_refreshed_windows_failed', '状态已刷新；窗口列表刷新失败，请稍后重试。'),
        windowsLoaded ? 'success' : 'warning',
      );
    }
  });
});
document.getElementById('primaryDiagnosisPanel').addEventListener('click', (event) => {
  const target = eventElement(event.target);
  const button = target ? target.closest('[data-primary-action]') : null;
  if (!button) {
    return;
  }
  const action = button.getAttribute('data-primary-action') || '';
  withButtonPending(button, uiT('ui.pending.processing', '处理中...'), () => handleDiagnosisAction(action)).catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});
document.getElementById('firstRunGuide').addEventListener('click', (event) => {
  const target = eventElement(event.target);
  const button = target ? target.closest('[data-first-run-action]') : null;
  if (!button) {
    return;
  }
  const action = button.getAttribute('data-first-run-action') || '';
  withButtonPending(button, uiT('ui.pending.processing', '处理中...'), () => handleDiagnosisAction(action)).catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});
document.getElementById('saveModeBtn').addEventListener('click', () => {
  withButtonPending('saveModeBtn', uiT('ui.pending.saving', '保存中...'), saveMode).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrScreenTemplatesSaveBtn').addEventListener('click', () => {
  withButtonPending('ocrScreenTemplatesSaveBtn', uiT('ui.pending.saving', '保存中...'), saveOcrScreenTemplates).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrScreenTemplatesDraftBtn').addEventListener('click', () => {
  withButtonPending('ocrScreenTemplatesDraftBtn', uiT('ui.pending.generating', '生成中...'), buildOcrScreenTemplateDraft).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrScreenTemplatesValidateBtn').addEventListener('click', () => {
  withButtonPending('ocrScreenTemplatesValidateBtn', uiT('ui.pending.validating', '验证中...'), validateOcrScreenTemplates).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrScreenTemplatesUndoBtn').addEventListener('click', undoOcrScreenTemplateDraft);
document.getElementById('ocrRegionSnapshotBtn').addEventListener('click', () => {
  withButtonPending('ocrRegionSnapshotBtn', uiT('ui.pending.loading', '加载中...'), loadOcrRegionSnapshot).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrRegionTemplateBtn').addEventListener('click', () => {
  withButtonPending('ocrRegionTemplateBtn', uiT('ui.pending.generating', '生成中...'), buildOcrRegionTemplateDraft).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrRegionCaptureBtn').addEventListener('click', applyOcrRegionToCaptureProfileForm);
document.getElementById('ocrRegionEditor').addEventListener('pointerdown', startOcrRegionDrag);
document.getElementById('ocrRegionEditor').addEventListener('pointermove', moveOcrRegionDrag);
document.getElementById('ocrRegionEditor').addEventListener('pointerup', finishOcrRegionDrag);
document.getElementById('ocrRegionEditor').addEventListener('pointercancel', finishOcrRegionDrag);
SETTINGS_CONTROL_IDS.forEach((id) => {
  const node = document.getElementById(id);
  if (!node) {
    return;
  }
  const markDirty = () => {
    if (!settingsSaveInFlight) {
      settingsDirty = true;
      updateSettingsDirtyHint();
      if (node.tagName === 'SELECT') {
        scheduleSettingsAutosave();
      }
    }
  };
  node.addEventListener('input', markDirty);
  node.addEventListener('change', markDirty);
});
document.getElementById('clearBindBtn').addEventListener('click', async () => {
  await withButtonPending('clearBindBtn', uiT('ui.pending.restoring', '恢复中...'), () => bindGame(''));
});
document.getElementById('standbyOnBtn').addEventListener('click', () => {
  withButtonPending('standbyOnBtn', uiT('ui.pending.switching', '切换中...'), () => setStandby(true)).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('standbyOffBtn').addEventListener('click', () => {
  withButtonPending('standbyOffBtn', uiT('ui.pending.processing', '处理中...'), resumeAgentFromButton).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('queryContextBtn').addEventListener('click', () => {
  withButtonPending('queryContextBtn', uiT('ui.pending.querying', '查询中...'), () => askAgent('query_context')).catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('sendMessageBtn').addEventListener('click', () => {
  withButtonPending('sendMessageBtn', uiT('ui.pending.sending', '发送中...'), () => askAgent('send_message')).catch((error) => { console.error('[galgame] async action failed', error); });
});
// rapidocrInstallBtn / dxcamInstallBtn event listeners removed — buttons gone from HTML.
document.getElementById('tesseractInstallBtn').addEventListener('click', () => installTesseract(false));
document.getElementById('textractorInstallBtn').addEventListener('click', () => installTextractor(false));
document.getElementById('rapidocrUseBtn').addEventListener('click', () => setOcrBackendSelection({ backendSelection: 'rapidocr' }));
document.getElementById('ocrBackendAutoBtn').addEventListener('click', () => setOcrBackendSelection({ backendSelection: 'auto' }));
document.getElementById('tesseractUseBtn').addEventListener('click', () => setOcrBackendSelection({ backendSelection: 'tesseract' }));
document.getElementById('smartCaptureUseBtn').addEventListener('click', () => setOcrBackendSelection({ captureBackend: 'smart' }));
document.getElementById('dxcamUseBtn').addEventListener('click', () => setOcrBackendSelection({ captureBackend: 'dxcam' }));
document.getElementById('captureBackendAutoBtn').addEventListener('click', () => setOcrBackendSelection({ captureBackend: 'auto' }));
document.getElementById('mssUseBtn').addEventListener('click', () => setOcrBackendSelection({ captureBackend: 'mss' }));
document.getElementById('pyautoguiUseBtn').addEventListener('click', () => setOcrBackendSelection({ captureBackend: 'pyautogui' }));
document.getElementById('printwindowUseBtn').addEventListener('click', () => setOcrBackendSelection({ captureBackend: 'printwindow' }));
document.getElementById('memoryProcessRefreshBtn').addEventListener('click', () => {
  refreshMemoryProcessTargetsIfNeeded({
    reason: 'memory_process_refresh_button',
    force: true,
    silent: false,
  }).then((refreshed) => {
    if (!refreshed) {
      setFlash(uiT('ui.flash.memory_process_refresh_failed', 'Memory Reader 进程列表刷新失败，请稍后重试。'), 'warning');
    }
  }).catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});
document.getElementById('memoryProcessAutoBtn').addEventListener('click', () => {
  clearMemoryProcessTarget().catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('memoryProcessSelectBtn').addEventListener('click', () => {
  openMemoryProcessModal().catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});
document.getElementById('memoryProcessModalClose').addEventListener('click', closeMemoryProcessModal);
document.querySelector('#memoryProcessModal .modal-overlay').addEventListener('click', closeMemoryProcessModal);
document.getElementById('ocrWindowRefreshBtn').addEventListener('click', () => {
  refreshOcrWindowTargetsIfNeeded({
    reason: 'window_list_refresh_button',
    force: true,
    silent: false,
  }).then((refreshed) => {
    if (!refreshed) {
      setFlash(uiT('ui.flash.ocr_window_refresh_failed', 'OCR 窗口列表刷新失败，请稍后重试。'), 'warning');
    }
  }).catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});
document.getElementById('ocrWindowAutoBtn').addEventListener('click', () => {
  clearOcrWindowTargetWithFeedback().catch((error) => { console.error('[galgame] async action failed', error); });
});
document.getElementById('ocrWindowSelectBtn').addEventListener('click', () => {
  openOcrWindowModal().catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});
document.getElementById('ocrWindowModalClose').addEventListener('click', closeOcrWindowModal);
document.querySelector('#ocrWindowModal .modal-overlay').addEventListener('click', closeOcrWindowModal);
document.addEventListener('pointerdown', (event) => {
  if (!document.body.classList.contains('panel-fullscreen-active')) {
    return;
  }
  const panel = document.querySelector('.panel-fullscreen');
  const target = eventElement(event.target);
  if (panel && target && !panel.contains(target)) {
    exitPanelFullscreen();
  }
});
document.addEventListener('wheel', (event) => {
  if (!document.body.classList.contains('panel-fullscreen-active') || event.ctrlKey) {
    return;
  }
  const target = fullscreenWheelTarget(event.target, event.deltaY);
  if (!target) {
    return;
  }
  target.scrollTop += event.deltaY;
  event.preventDefault();
}, { passive: false });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    exitPanelFullscreen();
    closeMemoryProcessModal();
    closeOcrWindowModal();
  }
});
document.getElementById('ocrProfileSaveBtn').addEventListener('click', saveOcrCaptureProfile);
document.getElementById('ocrProfileClearBtn').addEventListener('click', clearOcrCaptureProfile);
document.getElementById('ocrProfileAutoRecalibrateBtn').addEventListener('click', autoRecalibrateOcrDialogueProfile);
document.getElementById('ocrProfileApplyRecommendedBtn').addEventListener('click', applyRecommendedOcrCaptureProfile);
document.getElementById('ocrProfileRollbackBtn').addEventListener('click', rollbackOcrCaptureProfileRecommendation);
document.getElementById('ocrProfileStageSelect').addEventListener('change', () => {
  if (latestStatus) {
    renderOcrProfile(latestStatus);
  }
});
document.getElementById('ocrProfileSaveScopeSelect').addEventListener('change', () => {
  if (latestStatus) {
    renderOcrProfile(latestStatus);
  }
});
document.getElementById('ocrProfileProcessInput').addEventListener('blur', () => {
  if (latestStatus) {
    renderOcrProfile(latestStatus);
  }
});

document.querySelectorAll('.install-tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.installTab;
    if (tab) {
      switchInstallTab(tab);
    }
  });
});

document.querySelectorAll('#modeSwitch .mode-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const modeSwitchEl = document.getElementById('modeSwitch');
    if (!modeSwitchEl || modeSwitchEl.dataset.ready !== 'true') {
      return;
    }
    const mode = btn.dataset.mode;
    const select = document.getElementById('modeSelect');
    if (select && mode) {
      select.value = mode;
      select.dispatchEvent(new Event('change'));
      pendingModeSelection = mode;
      updateModeSwitchControl(mode);
      updateSummaryMode(mode);
      saveMode().catch((error) => { console.error('[galgame] async action failed', error); });
    }
  });
});

document.querySelectorAll('#speedSwitch .speed-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const speed = btn.dataset.speed;
    const select = document.getElementById('advanceSpeedSelect');
    if (select && speed) {
      select.value = speed;
      select.dispatchEvent(new Event('change'));
      saveMode().catch((error) => { console.error('[galgame] async action failed', error); });
    }
  });
});

document.querySelectorAll('[data-skip-onboarding]').forEach((btn) => {
  btn.addEventListener('click', () => {
    localStorage.setItem('galgame_skip_onboarding', '1');
    document.body.classList.remove('onboarding-active');
  });
});

document.getElementById('onboardingView').addEventListener('click', (event) => {
  const target = eventElement(event.target);
  const button = target ? target.closest('[data-first-run-action]') : null;
  if (!button) {
    return;
  }
  const action = button.getAttribute('data-first-run-action') || '';
  withButtonPending(button, uiT('ui.pending.processing', '处理中...'), () => handleDiagnosisAction(action)).catch((error) => {
    setFlash(error instanceof Error ? error.message : String(error), 'error');
  });
});

const advancedToggleBtn = document.getElementById('advancedToggleBtn');
if (advancedToggleBtn) {
  advancedToggleBtn.addEventListener('click', () => {
    const el = document.getElementById('advancedSettings');
    if (el) {
      el.classList.toggle('open');
      advancedToggleBtn.textContent = el.classList.contains('open')
        ? uiT('ui.advanced.collapse_settings', '收起高级设置')
        : uiT('ui.advanced.settings', '高级设置');
      if (el.classList.contains('open')) {
        const firstRunGuide = document.getElementById('firstRunGuide');
        if (firstRunGuide) {
          firstRunGuide.hidden = true;
        }
      }
    }
  });
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    refreshAll({ preserveFlash: true, silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
    refreshMemoryProcessTargetsIfNeeded({ reason: 'page_focus', silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
    refreshOcrWindowsOnPageFocus();
  }
});

window.addEventListener('focus', () => {
  refreshAll({ preserveFlash: true, silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
  refreshMemoryProcessTargetsIfNeeded({ reason: 'page_focus', silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
  refreshOcrWindowsOnPageFocus();
});

window.addEventListener('i18n-ready', () => {
  if (window.I18n && typeof window.I18n.lang === 'function' && window.I18n.lang() !== 'zh-CN') {
    refreshAll({ preserveFlash: true, silent: true }).catch((error) => { console.error('[galgame] async action failed', error); });
  }
});

initialize();
