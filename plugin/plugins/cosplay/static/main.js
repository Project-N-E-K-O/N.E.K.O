const API_BASE = "";

function getPluginId() {
  const m = window.location.pathname.match(/\/plugin\/([^/]+)\/ui/);
  return m ? m[1] : "galgame";
}

const state = {
  pluginId: getPluginId(),
  backend: null,
  uploads: { images: [], videos: [] },
  slideToken: 0,
  dialogueSlide: { active: false, cursor: 0, queue: [] },
  playback: {
    active: false,
    queue: [],
    cursor: 0,
    timer: null,
    token: 0,
    mode: "timer",
  },
  stageTextHidden: false,
  confirmCollapsed: false,
  liveSubtitleDurationMs: 2800,
  liveSubtitleTimer: null,
};

let toastTimer = null;

function getAuthToken() {
  return localStorage.getItem("auth_token") || "";
}

function authHeaders() {
  const token = getAuthToken();
  return {
    "Content-Type": "application/json",
    Authorization: token ? `Bearer ${token}` : "",
  };
}

function showToast(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = String(message || "");
  el.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3000);
}

async function callEntry(entryId, args = {}, options = {}) {
  let createResp;
  try {
    createResp = await fetch(`${API_BASE}/runs`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ plugin_id: state.pluginId, entry_id: entryId, args }),
    });
  } catch (err) {
    throw new Error("无法连接插件服务。请确认端口 48916 已启动且未被占用。\n" + String(err.message || err));
  }

  if (!createResp.ok) {
    throw new Error(`任务创建失败: HTTP ${createResp.status}。若持续失败，请检查 48916 端口占用。`);
  }

  const run = await createResp.json();
  const runId = String(run.run_id || "");
  if (!runId) throw new Error("未返回 run_id");

  const timeoutMs = Math.max(8000, Number(options.timeoutMs || 300000));
  const pollMs = Math.max(120, Number(options.pollIntervalMs || 420));
  const start = Date.now();

  while (Date.now() - start < timeoutMs) {
    const s = await fetch(`${API_BASE}/runs/${runId}`, { headers: authHeaders() });
    if (!s.ok) throw new Error(`状态查询失败: HTTP ${s.status}`);
    const sobj = await s.json();
    if (sobj.status !== "succeeded" && sobj.status !== "failed") {
      await sleep(pollMs);
      continue;
    }

    const exp = await fetch(`${API_BASE}/runs/${runId}/export`, { headers: authHeaders() });
    if (!exp.ok) throw new Error(`结果导出失败: HTTP ${exp.status}`);
    const eobj = await exp.json();
    const items = Array.isArray(eobj.items) ? eobj.items : [];

    for (const item of items) {
      let payload = null;
      if (item && item.type === "json" && (item.json != null || item.json_data != null)) {
        payload = item.json ?? item.json_data;
      } else if (item && item.type === "text" && typeof item.text === "string") {
        try {
          payload = JSON.parse(item.text);
        } catch (_e) {
          payload = null;
        }
      }
      if (!payload) continue;

      const p = payload.plugin_response || payload;
      const data = p.data || {};
      const success = p.success !== false;
      const errObj = p.error;
      const errText = typeof errObj === "string" ? errObj : (errObj && (errObj.message || errObj.error)) || "";
      return { success, data, error: String(errText || "") };
    }

    return { success: sobj.status === "succeeded", data: {}, error: sobj.status === "failed" ? "运行失败" : "" };
  }

  throw new Error("等待结果超时：请检查模型接口延迟或网络连通性。");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms || 0))));
}

function escapeHtml(v) {
  const s = String(v || "");
  return s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function applyModelConfig(cfg = {}) {
  document.getElementById("imageEnabled").checked = cfg.image?.enabled !== false;
  document.getElementById("imageApiUrl").value = cfg.image?.api_url || "";
  document.getElementById("imageModelId").value = cfg.image?.model_id || "";
  document.getElementById("imageApiKey").value = cfg.image?.api_key || "";
  document.getElementById("videoEnabled").checked = cfg.video?.enabled !== false;
  document.getElementById("videoApiUrl").value = cfg.video?.api_url || "";
  document.getElementById("videoModelId").value = cfg.video?.model_id || "";
  document.getElementById("videoApiKey").value = cfg.video?.api_key || "";
  document.getElementById("directorApiUrl").value = cfg.director?.api_url || "";
  document.getElementById("directorModelId").value = cfg.director?.model_id || "";
  document.getElementById("directorApiKey").value = cfg.director?.api_key || "";
  document.getElementById("ttsEnabled").checked = cfg.tts?.enabled !== false;
  document.getElementById("ttsApiUrl").value = cfg.tts?.api_url || "";
  document.getElementById("ttsModelId").value = cfg.tts?.model_id || "";
  document.getElementById("ttsApiKey").value = cfg.tts?.api_key || "";
}

function collectModelConfig() {
  return {
    image: {
      enabled: !!document.getElementById("imageEnabled")?.checked,
      api_url: document.getElementById("imageApiUrl").value.trim(),
      model_id: document.getElementById("imageModelId").value.trim(),
      api_key: document.getElementById("imageApiKey").value.trim(),
    },
    video: {
      enabled: !!document.getElementById("videoEnabled")?.checked,
      api_url: document.getElementById("videoApiUrl").value.trim(),
      model_id: document.getElementById("videoModelId").value.trim(),
      api_key: document.getElementById("videoApiKey").value.trim(),
    },
    director: {
      api_url: document.getElementById("directorApiUrl").value.trim(),
      model_id: document.getElementById("directorModelId").value.trim(),
      api_key: document.getElementById("directorApiKey").value.trim(),
    },
    tts: {
      enabled: !!document.getElementById("ttsEnabled")?.checked,
      api_url: document.getElementById("ttsApiUrl").value.trim(),
      model_id: document.getElementById("ttsModelId").value.trim(),
      api_key: document.getElementById("ttsApiKey").value.trim(),
    },
  };
}

function applyRoleConfig(cfg = {}) {
  document.getElementById("userRoleName").value = cfg.user_role_name || "";
  document.getElementById("aiRoleName").value = cfg.ai_role_name || "";
  document.getElementById("userAliases").value = Array.isArray(cfg.user_aliases) ? cfg.user_aliases.join(",") : "";
  document.getElementById("aiAliases").value = Array.isArray(cfg.ai_aliases) ? cfg.ai_aliases.join(",") : "";
  document.getElementById("userPersonaTags").value = cfg.user_persona_tags || "";
  document.getElementById("aiPersonaTags").value = cfg.ai_persona_tags || "";
}

function collectRoleConfig() {
  return {
    user_role_name: document.getElementById("userRoleName").value.trim(),
    ai_role_name: document.getElementById("aiRoleName").value.trim(),
    user_aliases: document.getElementById("userAliases").value.trim(),
    ai_aliases: document.getElementById("aiAliases").value.trim(),
    user_persona_tags: document.getElementById("userPersonaTags").value.trim(),
    ai_persona_tags: document.getElementById("aiPersonaTags").value.trim(),
  };
}

function setProgress(v) {
  const p = document.getElementById("prepareProgressBar");
  if (!p) return;
  const n = Math.max(0, Math.min(100, Number(v || 0)));
  p.style.width = `${n}%`;
}

function setStageMedia(media = {}) {
  const img = document.getElementById("stageImage");
  const video = document.getElementById("stageVideo");
  const cap = document.getElementById("stageCaption");
  const fx = document.getElementById("climaxFx");

  const kind = String(media.kind || "image");
  const url = String(media.url || "");
  const source = String(media.source || "none");
  const isClimax = !!media.is_climax;

  fx.classList.toggle("on", isClimax || kind === "video");

  if (!url) {
    img.style.display = "block";
    video.style.display = "none";
    video.pause();
    cap.textContent = "等待剧情素材加载";
    return;
  }

  if (kind === "video") {
    video.style.opacity = "0";
    video.style.display = "block";
    img.style.display = "none";
    video.src = url;
    video.play().then(() => {
      requestAnimationFrame(() => { video.style.opacity = "1"; });
    }).catch(() => {});
    cap.textContent = source === "user_upload" ? "用户覆盖视频播放中" : "高潮视频播放中";
    return;
  }

  img.style.opacity = "0";
  img.style.display = "block";
  video.style.display = "none";
  video.pause();
  img.src = url;
  requestAnimationFrame(() => { img.style.opacity = "1"; });
  img.onerror = () => {
    cap.textContent = "图片加载失败，建议检查素材路径或网络连通性";
  };
  cap.textContent = source === "user_upload" ? "用户覆盖图片展示中" : "剧情背景展示中";
}

function renderTimeline(nodes = [], currentIndex = 0) {
  const box = document.getElementById("timelineList");
  if (!box) return;
  if (!Array.isArray(nodes) || !nodes.length) {
    box.innerHTML = "<div class=\"status-box\">尚未生成剧情节点</div>";
    return;
  }

  box.innerHTML = nodes.map((n, i) => {
    const active = i === Number(currentIndex || 0) ? " style=\"outline:2px solid rgba(16,129,255,.42)\"" : "";
    const climax = n.is_climax ? " · 高潮" : "";
    return `
      <div class="timeline-item"${active}>
        <div class="meta">第 ${i + 1} 幕${climax} | 图:${escapeHtml(n.image_status || "pending")} | 视:${escapeHtml(n.video_status || "skipped")}</div>
        <div class="line"><strong>用户：</strong>${escapeHtml(n.user_line || "")}</div>
        <div class="line"><strong>AI：</strong>${escapeHtml(n.ai_line || "")}</div>
        <div class="line"><strong>图提示词：</strong>${escapeHtml(n.image_prompt || "")}</div>
        <div class="line"><strong>视提示词：</strong>${escapeHtml(n.video_prompt || "-")}</div>
        ${n.image_error ? `<div class="line error-text">图片错误：${escapeHtml(n.image_error)}</div>` : ""}
        ${n.video_error ? `<div class="line error-text">视频错误：${escapeHtml(n.video_error)}</div>` : ""}
      </div>
    `;
  }).join("");
}

function renderAudit(audit = []) {
  const box = document.getElementById("auditList");
  if (!box) return;
  if (!Array.isArray(audit) || !audit.length) {
    box.innerHTML = "<div class=\"status-box\">暂无线路核查记录</div>";
    return;
  }
  box.innerHTML = audit.map((a) => `
    <div class="audit-item">
      <div class="meta-title">第${escapeHtml(a.turn)}幕链路核查 | ${escapeHtml(a.time)}</div>
      <div class="line">导演→图片模型：${escapeHtml(a.director_to_image.status)} | 资产：${escapeHtml(a.director_to_image.asset || "-")}</div>
      <div class="line">导演→视频模型：${escapeHtml(a.director_to_video.status)} | 资产：${escapeHtml(a.director_to_video.asset || "-")}</div>
      ${a.director_to_image.error ? `<div class="line error-text">图片异常：${escapeHtml(a.director_to_image.error)}</div>` : ""}
      ${a.director_to_video.error ? `<div class="line error-text">视频异常：${escapeHtml(a.director_to_video.error)}</div>` : ""}
    </div>
  `).join("");
}

function renderLibrary(library = {}) {
  const box = document.getElementById("libraryList");
  if (!box) return;
  const items = Array.isArray(library.items) ? library.items : [];
  if (!items.length) {
    box.innerHTML = "<div class=\"status-box\">尚未生成可预览素材</div>";
    return;
  }

  box.innerHTML = items.map((item) => {
    const status = String(item.status || "ready");
    const sceneIndex = Number(item.scene_index || 0);
    const media = item.kind === "video"
      ? `<video src="${escapeHtml(item.url || "")}" controls preload="metadata"></video>`
      : `<img src="${escapeHtml(item.url || "")}" alt="${escapeHtml(item.title || "素材")}" />`;
    const failed = status === "failed" || status === "disabled";

    return `
      <div class="lib-card">
        <div class="meta-title">${escapeHtml(item.title || "素材")}</div>
        <div class="meta">状态：${escapeHtml(status)}${item.error ? ` | ${escapeHtml(item.error)}` : ""}</div>
        ${media}
        <div class="lib-actions">
          <button class="btn btn-ghost lib-use-btn" data-kind="${escapeHtml(item.kind)}" data-url="${escapeHtml(item.url || "")}" data-title="${escapeHtml(item.title || "")}">${escapeHtml(item.replace_label || "设为舞台")}</button>
          ${failed ? `<button class="btn btn-primary lib-replace-btn" data-kind="${escapeHtml(item.kind)}" data-scene-index="${sceneIndex}">上传替换本幕${item.kind === "video" ? "视频" : "图片"}</button>` : ""}
          <a class="btn btn-ghost" href="${escapeHtml(item.url || "")}" download="${escapeHtml(item.download_name || "asset")}">下载</a>
          <a class="btn btn-ghost" href="${escapeHtml(item.url || "")}" target="_blank" rel="noreferrer">打开</a>
        </div>
      </div>
    `;
  }).join("");

  box.querySelectorAll(".lib-use-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const kind = btn.getAttribute("data-kind") || "image";
      const url = btn.getAttribute("data-url") || "";
      const label = btn.getAttribute("data-title") || "";
      try {
        const resp = await callEntry("set_stage_override", { kind, url, label });
        if (!resp.success) throw new Error(resp.error || "替换失败");
        applyBackendState(resp.data.state || state.backend);
        showToast("已替换当前舞台素材");
      } catch (err) {
        showToast(String(err.message || err));
      }
    });
  });

  box.querySelectorAll(".lib-replace-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const kind = btn.getAttribute("data-kind") || "image";
      const sceneIndex = Number(btn.getAttribute("data-scene-index") || 0);
      try {
        const file = await pickSingleFile(kind);
        if (!file) return;
        const up = kind === "video"
          ? await uploadVideoByChunks(file)
          : await uploadImageDirect(file);
        if (!up.success) throw new Error(up.error || "上传失败");

        const rep = await callEntry("replace_node_asset", {
          node_index: sceneIndex,
          kind,
          url: up.data.url,
        });
        if (!rep.success) throw new Error(rep.error || "替换失败");

        applyBackendState(rep.data.state || state.backend);
        showToast(`第${sceneIndex + 1}幕${kind === "video" ? "视频" : "图片"}已替换`);
      } catch (err) {
        showToast(String(err.message || err));
      }
    });
  });
}

async function pickSingleFile(kind) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = kind === "video" ? "video/mp4,video/webm,video/quicktime" : "image/png,image/jpeg,image/webp,image/gif";
  return new Promise((resolve) => {
    input.addEventListener("change", () => {
      resolve(input.files?.[0] || null);
    }, { once: true });
    input.click();
  });
}

function renderStoragePaths(paths = {}) {
  const box = document.getElementById("storagePaths");
  if (!box) return;
  box.innerHTML = `
    <div>DATA 根目录：${escapeHtml(paths.data_root || "-")}</div>
    <div>生成图片路径：${escapeHtml(paths.generated_images || "-")}</div>
    <div>生成视频路径：${escapeHtml(paths.generated_videos || "-")}</div>
    <div>上传图片路径：${escapeHtml(paths.uploaded_images || "-")}</div>
    <div>上传视频路径：${escapeHtml(paths.uploaded_videos || "-")}</div>
  `;
}

function renderSubtitleHistory(history = [], roleProfiles = {}) {
  const box = document.getElementById("subtitleList");
  const countEl = document.getElementById("subtitleCount");
  if (!box || !countEl) return;
  const rows = Array.isArray(history) ? history : [];
  countEl.textContent = `${rows.length} 条`;
  if (!rows.length) {
    box.innerHTML = "<div class=\"subtitle-empty\">等待剧情字幕...</div>";
    return;
  }
  box.innerHTML = rows.map((item) => {
    const roleName = String(item.role_name || "角色");
    const profile = roleProfiles?.[roleName] || {};
    const color = profile.subtitle_color || "#e9f0ff";
    const glow = profile.subtitle_glow || "rgba(194,211,255,0.42)";
    return `
      <div class="subtitle-item" style="--role-color:${escapeHtml(color)};--role-glow:${escapeHtml(glow)}">
        <span class="role">${escapeHtml(roleName)}</span>
        <span class="text">${escapeHtml(String(item.text || ""))}</span>
      </div>
    `;
  }).join("");
  box.scrollTop = box.scrollHeight;
  showLiveSubtitle(rows[rows.length - 1], roleProfiles);
}

function showLiveSubtitle(item, roleProfiles = {}) {
  const panel = document.getElementById("liveSubtitle");
  const roleEl = document.getElementById("liveSubtitleRole");
  const textEl = document.getElementById("liveSubtitleText");
  if (!panel || !roleEl || !textEl || !item) return;
  const roleName = String(item.role_name || "角色");
  const profile = roleProfiles?.[roleName] || {};
  roleEl.textContent = roleName;
  roleEl.style.color = profile.subtitle_color || "#f0e8ff";
  roleEl.style.textShadow = `0 0 10px ${profile.subtitle_glow || "rgba(193,200,255,0.45)"}`;
  textEl.textContent = String(item.text || "");
  panel.classList.add("show");
  if (state.liveSubtitleTimer) clearTimeout(state.liveSubtitleTimer);
  const duration = Math.max(500, Math.min(20000, Number(state.liveSubtitleDurationMs || 2800)));
  state.liveSubtitleTimer = setTimeout(() => panel.classList.remove("show"), duration);
}

function renderRoleProfilesEditor(profiles = {}) {
  const box = document.getElementById("roleProfilesList");
  if (!box) return;
  const entries = Object.entries(profiles || {});
  if (!entries.length) {
    box.innerHTML = "<div class=\"status-box\">暂无角色配置，可点击“新增角色配置”。</div>";
    return;
  }
  box.innerHTML = entries.map(([roleName, cfg]) => `
    <div class="role-profile-item" data-role="${escapeHtml(roleName)}">
      <input class="rp-name" value="${escapeHtml(roleName)}" placeholder="角色名" />
      <select class="rp-voice">
        <option value="">默认音色</option>
        <option value="longxiaochun_v2" ${cfg.tts_voice === "longxiaochun_v2" ? "selected" : ""}>女声-温柔</option>
        <option value="longxiaotong_v2" ${cfg.tts_voice === "longxiaotong_v2" ? "selected" : ""}>女声-清亮</option>
        <option value="longxiaocheng_v2" ${cfg.tts_voice === "longxiaocheng_v2" ? "selected" : ""}>男声-沉稳</option>
        <option value="custom">${cfg.tts_voice && !["longxiaochun_v2","longxiaotong_v2","longxiaocheng_v2"].includes(cfg.tts_voice) ? "自定义已填" : "自定义"}</option>
      </select>
      <input class="rp-custom-voice" value="${escapeHtml(cfg.tts_voice || "")}" placeholder="自定义音色ID" />
      <input class="rp-color" type="color" value="${escapeHtml(cfg.subtitle_color || "#ffd8ef")}" />
      <input class="rp-size" type="range" min="0.8" max="1.6" step="0.05" value="${Number(cfg.font_scale || 1)}" />
      <button class="btn btn-ghost rp-remove">删除</button>
    </div>
  `).join("");

  box.querySelectorAll(".rp-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".role-profile-item");
      if (row) row.remove();
      syncRoleProfilesJsonFromEditor();
    });
  });
  box.querySelectorAll("input,select").forEach((el) => {
    el.addEventListener("change", syncRoleProfilesJsonFromEditor);
    el.addEventListener("input", syncRoleProfilesJsonFromEditor);
  });
}

function collectRoleProfilesFromEditor() {
  const rows = document.querySelectorAll(".role-profile-item");
  const out = {};
  rows.forEach((row) => {
    const role = row.querySelector(".rp-name")?.value.trim();
    if (!role) return;
    const selectedVoice = row.querySelector(".rp-voice")?.value || "";
    const customVoice = row.querySelector(".rp-custom-voice")?.value.trim() || "";
    const voice = selectedVoice && selectedVoice !== "custom" ? selectedVoice : customVoice;
    const color = row.querySelector(".rp-color")?.value || "#ffd8ef";
    const scale = Number(row.querySelector(".rp-size")?.value || 1);
    out[role] = {
      tts_voice: voice,
      subtitle_color: color,
      subtitle_glow: "rgba(255,183,222,0.45)",
      font_scale: scale,
    };
  });
  return out;
}

function syncRoleProfilesJsonFromEditor() {
  const obj = collectRoleProfilesFromEditor();
  const jsonEl = document.getElementById("roleProfilesInput");
  if (jsonEl) jsonEl.value = JSON.stringify(obj, null, 2);
}

function applyBackendState(next = {}) {
  state.backend = next || {};
  const prepared = !!next.prepared;
  const total = Number(next.total_turns || 0);
  const turn = Number(next.current_turn || 0);
  const finished = !!next.finished;
  const status = next.status || {};

  document.getElementById("progressChip").textContent = prepared
    ? (finished ? `剧情完成 (${total}/${total})` : `第 ${turn}/${total} 幕`)
    : "剧情未开始";

  document.getElementById("currentUserLine").value = String(next.current_user_line || "");
  document.getElementById("currentRoleChip").textContent = `当前角色：${String(next.current_role_name || "-")}`;
  document.getElementById("prepareSummary").textContent = String(status.message || next.last_error || "等待解析剧本");
  setProgress(Number(status.progress || 0));
  if (document.getElementById("backgroundMode")) {
    document.getElementById("backgroundMode").value = String(next.background_mode || "semi_auto");
  }
  if (document.getElementById("sceneSplitMode")) {
    document.getElementById("sceneSplitMode").value = String(next.scene_split_mode || "strict_copy");
  }
  if (document.getElementById("maxScenes")) {
    document.getElementById("maxScenes").value = String(Number(next.max_scenes || 0));
  }
  if (document.getElementById("exactScenes")) {
    document.getElementById("exactScenes").value = String(Number(next.exact_scenes || 0));
  }

  renderTimeline(next.nodes || [], Number(next.current_index || 0));
  renderAudit(next.control_audit || []);
  renderLibrary(next.library || {});
  renderStoragePaths(next.storage_paths || {});
  // 按秒自动播放用户素材时，不要用后端 stage_media 覆盖当前舞台，否则确认台词后
  // 会闪成剧情节点图/占位背景（与幻灯片脱节）。对话切图模式仍走后端舞台刷新。
  const skipBackendStageMedia =
    state.playback.active && state.playback.mode === "timer";
  if (!skipBackendStageMedia) {
    setStageMedia(next.stage_media || {});
  } else {
    const cur = state.playback.queue[state.playback.cursor];
    if (cur && cur.url) {
      applyPlaybackItem(cur);
    }
  }
  renderSubtitleHistory(next.dialogue_history || [], next.role_profiles || {});

  if (next.model_config) applyModelConfig(next.model_config);
  if (next.role_config) applyRoleConfig(next.role_config);
  if (next.role_profiles) {
    document.getElementById("roleProfilesInput").value = JSON.stringify(next.role_profiles, null, 2);
    renderRoleProfilesEditor(next.role_profiles);
  }
}

function collectUploadRows() {
  const rows = document.querySelectorAll(".upload-item");
  const result = [];
  rows.forEach((row) => {
    const id = row.getAttribute("data-id") || "";
    const kind = row.getAttribute("data-kind") || "image";
    const delay = Math.max(0, Number((row.querySelector(".delay-input")?.value || 0)));
    const duration = Math.max(0.1, Number((row.querySelector(".duration-input")?.value || 2)));
    const item = (kind === "video" ? state.uploads.videos : state.uploads.images).find((x) => x.id === id);
    if (!item) return;
    item.delay = delay;
    item.duration = duration;
    result.push({ ...item, kind });
  });
  return result;
}

function getPlaybackQueue() {
  const list = collectUploadRows().filter((x) => !!x.url);
  if (!list.length) return [];
  const requested = Math.max(1, Number(document.getElementById("slideStartIndex")?.value || 1));
  const start = (Math.floor(requested) - 1) % list.length;
  return [...list.slice(start), ...list.slice(0, start)];
}

function applySlideSecondsToAllUploads() {
  const sec = Math.max(0.1, Number(document.getElementById("slideSeconds")?.value || 2));
  state.uploads.images.forEach((item) => { item.duration = sec; });
  state.uploads.videos.forEach((item) => { item.duration = sec; });
  if (state.playback.active && Array.isArray(state.playback.queue) && state.playback.queue.length) {
    state.playback.queue.forEach((q) => {
      const kind = q.kind || "image";
      const src = kind === "video" ? state.uploads.videos : state.uploads.images;
      const found = src.find((x) => x.id === q.id);
      q.duration = found ? found.duration : sec;
    });
    if (state.playback.mode === "timer") {
      scheduleTimerPlayback();
    }
  }
  renderUploadList();
  showToast(`已将全部素材的「几秒后切换」设为 ${sec} 秒，可在列表中单独微调`);
}

function renderUploadList() {
  const box = document.getElementById("uploadList");
  if (!box) return;
  const all = [
    ...state.uploads.images.map((x) => ({ ...x, kind: "image" })),
    ...state.uploads.videos.map((x) => ({ ...x, kind: "video" })),
  ];
  if (!all.length) {
    box.innerHTML = "<div class=\"status-box\">暂未上传素材</div>";
    return;
  }

  box.innerHTML = all.map((item) => `
    <div class="upload-item" draggable="true" data-id="${escapeHtml(item.id)}" data-kind="${escapeHtml(item.kind)}">
      <div class="name">${escapeHtml(item.name)}</div>
      <div class="badge">${item.kind === "video" ? "视频" : "图片"}</div>
      ${item.kind === "video" ? `<video src="${escapeHtml(item.url)}" controls preload="metadata"></video>` : `<img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}" />`}
      <input class="delay-input" type="number" min="0" step="0.1" value="${Number(item.delay || 0)}" title="几秒后显示" />
      <input class="duration-input" type="number" min="0.1" step="0.1" value="${Number(item.duration || 2)}" title="几秒后切换" />
      <div class="upload-order-actions">
        <button class="btn btn-ghost upload-up-btn" title="上移">上移</button>
        <button class="btn btn-ghost upload-down-btn" title="下移">下移</button>
      </div>
      <button class="btn btn-ghost upload-use-btn">作为当前舞台</button>
    </div>
  `).join("");

  box.querySelectorAll(".upload-use-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest(".upload-item");
      const id = row?.getAttribute("data-id") || "";
      const kind = row?.getAttribute("data-kind") || "image";
      const item = (kind === "video" ? state.uploads.videos : state.uploads.images).find((x) => x.id === id);
      if (!item) return;
      try {
        const resp = await callEntry("set_stage_override", { kind, url: item.url, label: item.name });
        if (!resp.success) throw new Error(resp.error || "设置失败");
        applyBackendState(resp.data.state || state.backend);
        showToast("已切换用户上传素材到舞台");
      } catch (err) {
        showToast(String(err.message || err));
      }
    });
  });

  box.querySelectorAll(".upload-up-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".upload-item");
      if (!row || !row.previousElementSibling) return;
      row.parentElement?.insertBefore(row, row.previousElementSibling);
    });
  });
  box.querySelectorAll(".upload-down-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest(".upload-item");
      if (!row || !row.nextElementSibling) return;
      row.parentElement?.insertBefore(row.nextElementSibling, row);
    });
  });

  let draggingRow = null;
  box.querySelectorAll(".upload-item").forEach((row) => {
    row.addEventListener("dragstart", () => {
      draggingRow = row;
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      draggingRow = null;
      box.querySelectorAll(".upload-item").forEach((item) => item.classList.remove("drop-target"));
    });
    row.addEventListener("dragover", (event) => {
      event.preventDefault();
      if (!draggingRow || draggingRow === row) return;
      row.classList.add("drop-target");
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
    row.addEventListener("drop", (event) => {
      event.preventDefault();
      row.classList.remove("drop-target");
      if (!draggingRow || draggingRow === row) return;
      const rect = row.getBoundingClientRect();
      const placeAfter = event.clientY > rect.top + rect.height / 2;
      if (placeAfter) {
        row.parentElement?.insertBefore(draggingRow, row.nextElementSibling);
      } else {
        row.parentElement?.insertBefore(draggingRow, row);
      }
    });
  });
}

async function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
}

async function uploadFiles(files, kind) {
  let okCount = 0;
  for (const file of files) {
    try {
      const resp = kind === "video"
        ? await uploadVideoByChunks(file)
        : await uploadImageDirect(file);
      if (!resp.success) throw new Error(resp.error || "上传失败");
      const item = {
        id: `${kind}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
        name: file.name,
        url: resp.data.url,
        delay: 0,
        duration: kind === "video" ? 6 : 2,
      };
      if (kind === "video") state.uploads.videos.push(item);
      else state.uploads.images.push(item);
      okCount += 1;
    } catch (err) {
      showToast(`文件 ${file.name} 上传失败：${String(err.message || err)}`);
    }
  }
  renderUploadList();
  if (okCount > 0) showToast(`上传成功 ${okCount} 个${kind === "video" ? "视频" : "图片"}`);
}

async function uploadImageDirect(file) {
  const dataUrl = await readFileAsDataUrl(file);
  return callEntry("upload_stage_asset", {
    kind: "image",
    filename: file.name,
    data_url: dataUrl,
  }, { timeoutMs: 240000 });
}

async function uploadVideoByChunks(file) {
  const mime = String(file.type || "").toLowerCase();
  const begin = await callEntry("begin_chunked_video_upload", {
    filename: file.name,
    mime,
    total_size: Number(file.size || 0),
  }, { timeoutMs: 180000 });
  if (!begin.success) return begin;
  const uploadId = String(begin.data.upload_id || "");
  if (!uploadId) throw new Error("未获取到视频上传会话");

  const chunkSize = 2 * 1024 * 1024;
  let offset = 0;
  while (offset < file.size) {
    const chunk = file.slice(offset, Math.min(file.size, offset + chunkSize));
    const b64 = await blobToBase64(chunk);
    const append = await callEntry("append_chunked_video_upload", {
      upload_id: uploadId,
      chunk_b64: b64,
    }, { timeoutMs: 180000 });
    if (!append.success) throw new Error(append.error || "视频分片上传失败");
    offset += chunk.size;
  }

  const done = await callEntry("finish_chunked_video_upload", { upload_id: uploadId }, { timeoutMs: 180000 });
  if (!done.success) throw new Error(done.error || "视频上传收尾失败");
  return done;
}

async function blobToBase64(blob) {
  const arr = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < arr.length; i += step) {
    const part = arr.subarray(i, i + step);
    binary += String.fromCharCode(...part);
  }
  return btoa(binary);
}

function clearPlaybackTimer() {
  if (!state.playback.timer) return;
  clearTimeout(state.playback.timer);
  state.playback.timer = null;
}

function applyPlaybackItem(item) {
  if (!item || !item.url) return;
  setStageMedia({ kind: item.kind || "image", url: item.url, source: "user_upload", is_climax: false });
}

function scheduleTimerPlayback() {
  if (!state.playback.active || state.playback.mode !== "timer" || !state.playback.queue.length) return;
  clearPlaybackTimer();
  const current = state.playback.queue[state.playback.cursor];
  if (!current) return;
  const defaultSeconds = Math.max(0.1, Number(document.getElementById("slideSeconds")?.value || 2));
  const videoGap = Math.max(0, Number(document.getElementById("videoGapSeconds")?.value || 0));
  const staySec = Math.max(0.1, Number(current.duration || defaultSeconds));
  const gapSec = current.kind === "video" ? videoGap : Math.max(0, Number(current.delay || 0));
  const waitMs = (staySec + gapSec) * 1000;
  const token = state.playback.token;
  state.playback.timer = setTimeout(() => {
    if (!state.playback.active || token !== state.playback.token) return;
    advancePlayback(true);
  }, waitMs);
}

function advancePlayback(fromTimer = false) {
  if (!state.playback.active || !state.playback.queue.length) return;
  state.playback.cursor = (state.playback.cursor + 1) % state.playback.queue.length;
  const next = state.playback.queue[state.playback.cursor];
  applyPlaybackItem(next);
  if (state.playback.mode === "timer") {
    // 手动推进也要重置自动计时，确保自动与手动一致。
    scheduleTimerPlayback();
  } else if (!fromTimer) {
    showToast(`已切换到第 ${state.playback.cursor + 1} 个素材`);
  }
}

function retreatPlayback() {
  if (!state.playback.active || !state.playback.queue.length) return;
  state.playback.cursor = (state.playback.cursor - 1 + state.playback.queue.length) % state.playback.queue.length;
  const next = state.playback.queue[state.playback.cursor];
  applyPlaybackItem(next);
  if (state.playback.mode === "timer") {
    scheduleTimerPlayback();
  } else {
    showToast(`已切换到第 ${state.playback.cursor + 1} 个素材`);
  }
}

function startPlayback(mode) {
  const queue = getPlaybackQueue();
  if (!queue.length) {
    showToast("请先上传至少一个图片或视频素材");
    return;
  }
  clearPlaybackTimer();
  state.playback.active = true;
  state.playback.queue = queue;
  state.playback.cursor = 0;
  state.playback.mode = mode;
  state.playback.token += 1;
  state.dialogueSlide.active = mode === "dialogue";
  state.dialogueSlide.cursor = 0;
  state.dialogueSlide.queue = queue;

  applyPlaybackItem(queue[0]);
  if (mode === "timer") {
    scheduleTimerPlayback();
    showToast("已开始自动混合播放（图片/视频）");
  } else {
    showToast("已启用对话推进切换素材模式");
  }
}

function stopSlideshow() {
  state.slideToken += 1;
  clearPlaybackTimer();
  state.playback.active = false;
  state.playback.queue = [];
  state.playback.cursor = 0;
  state.dialogueSlide.active = false;
  state.dialogueSlide.cursor = 0;
  state.dialogueSlide.queue = [];
  showToast("幻灯片已停止");
}

function advanceDialogueSlideshow() {
  if (!state.playback.active) return;
  advancePlayback(false);
}

async function playSlideshow() {
  const mode = document.getElementById("slideMode")?.value || "timer";
  startPlayback(mode);
}

async function refreshState() {
  const resp = await callEntry("get_state", {});
  if (!resp.success) throw new Error(resp.error || "状态获取失败");
  applyBackendState(resp.data);
}

function initConfirmBoxInteractions() {
  const box = document.getElementById("confirmBox");
  const dragHandle = document.getElementById("confirmDragHandle");
  const resizeHandle = document.getElementById("confirmResizeHandle");
  const stage = document.getElementById("stageScreen");
  const liveSubtitle = document.getElementById("liveSubtitle");
  const liveSubtitleResize = document.getElementById("liveSubtitleResize");
  if (!box || !dragHandle || !resizeHandle || !stage) return;

  let dragState = null;
  let resizeState = null;
  let subtitleDrag = null;
  let subtitleResize = null;

  dragHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    const rect = box.getBoundingClientRect();
    dragState = {
      id: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    dragHandle.setPointerCapture(event.pointerId);
  });

  dragHandle.addEventListener("pointermove", (event) => {
    if (!dragState || dragState.id !== event.pointerId) return;
    const stageRect = stage.getBoundingClientRect();
    const width = box.offsetWidth;
    const height = box.offsetHeight;
    const left = Math.max(0, Math.min(event.clientX - stageRect.left - dragState.offsetX, stageRect.width - width));
    const top = Math.max(0, Math.min(event.clientY - stageRect.top - dragState.offsetY, stageRect.height - height));
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.style.bottom = "auto";
  });

  dragHandle.addEventListener("pointerup", (event) => {
    if (!dragState || dragState.id !== event.pointerId) return;
    dragHandle.releasePointerCapture(event.pointerId);
    dragState = null;
  });

  resizeHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    resizeState = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      width: box.offsetWidth,
      height: box.offsetHeight,
    };
    resizeHandle.setPointerCapture(event.pointerId);
  });

  resizeHandle.addEventListener("pointermove", (event) => {
    if (!resizeState || resizeState.id !== event.pointerId) return;
    const stageRect = stage.getBoundingClientRect();
    const nextWidth = Math.max(320, Math.min(resizeState.width + (event.clientX - resizeState.startX), stageRect.width - 16));
    const nextHeight = Math.max(150, Math.min(resizeState.height + (event.clientY - resizeState.startY), stageRect.height - 16));
    box.style.width = `${nextWidth}px`;
    box.style.minHeight = `${nextHeight}px`;
  });

  resizeHandle.addEventListener("pointerup", (event) => {
    if (!resizeState || resizeState.id !== event.pointerId) return;
    resizeHandle.releasePointerCapture(event.pointerId);
    resizeState = null;
  });

  if (liveSubtitle && liveSubtitleResize) {
    liveSubtitle.addEventListener("pointerdown", (event) => {
      if (event.target === liveSubtitleResize) return;
      const rect = liveSubtitle.getBoundingClientRect();
      subtitleDrag = {
        id: event.pointerId,
        offsetX: event.clientX - rect.left,
        offsetY: event.clientY - rect.top,
      };
      liveSubtitle.setPointerCapture(event.pointerId);
    });
    liveSubtitle.addEventListener("pointermove", (event) => {
      if (!subtitleDrag || subtitleDrag.id !== event.pointerId) return;
      const stageRect = stage.getBoundingClientRect();
      const left = Math.max(0, Math.min(event.clientX - stageRect.left - subtitleDrag.offsetX, stageRect.width - liveSubtitle.offsetWidth));
      const top = Math.max(0, Math.min(event.clientY - stageRect.top - subtitleDrag.offsetY, stageRect.height - liveSubtitle.offsetHeight));
      liveSubtitle.style.left = `${left}px`;
      liveSubtitle.style.top = `${top}px`;
      liveSubtitle.style.bottom = "auto";
      liveSubtitleResize.style.left = `${left + liveSubtitle.offsetWidth - 14}px`;
      liveSubtitleResize.style.top = `${top + liveSubtitle.offsetHeight - 14}px`;
      liveSubtitleResize.style.right = "auto";
      liveSubtitleResize.style.bottom = "auto";
    });
    liveSubtitle.addEventListener("pointerup", (event) => {
      if (!subtitleDrag || subtitleDrag.id !== event.pointerId) return;
      liveSubtitle.releasePointerCapture(event.pointerId);
      subtitleDrag = null;
    });

    liveSubtitleResize.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      subtitleResize = {
        id: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        width: liveSubtitle.offsetWidth,
        height: liveSubtitle.offsetHeight,
      };
      liveSubtitleResize.setPointerCapture(event.pointerId);
    });
    liveSubtitleResize.addEventListener("pointermove", (event) => {
      if (!subtitleResize || subtitleResize.id !== event.pointerId) return;
      const nextWidth = Math.max(340, subtitleResize.width + (event.clientX - subtitleResize.startX));
      const nextHeight = Math.max(60, subtitleResize.height + (event.clientY - subtitleResize.startY));
      liveSubtitle.style.width = `${nextWidth}px`;
      liveSubtitle.style.minHeight = `${nextHeight}px`;
      const rect = liveSubtitle.getBoundingClientRect();
      const stageRect = stage.getBoundingClientRect();
      liveSubtitleResize.style.left = `${rect.left - stageRect.left + nextWidth - 14}px`;
      liveSubtitleResize.style.top = `${rect.top - stageRect.top + nextHeight - 14}px`;
      liveSubtitleResize.style.right = "auto";
      liveSubtitleResize.style.bottom = "auto";
    });
    liveSubtitleResize.addEventListener("pointerup", (event) => {
      if (!subtitleResize || subtitleResize.id !== event.pointerId) return;
      liveSubtitleResize.releasePointerCapture(event.pointerId);
      subtitleResize = null;
    });
  }
}

function bindEvents() {
  document.getElementById("saveConfigBtn")?.addEventListener("click", async () => {
    try {
      const resp = await callEntry("save_model_config", { config: collectModelConfig() });
      if (!resp.success) throw new Error(resp.error || "保存配置失败");
      showToast("模型配置已保存");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("testModelConnBtn")?.addEventListener("click", async () => {
    try {
      const target = document.getElementById("testTargetSelect")?.value || "image";
      const resp = await callEntry("test_model_connection", { target }, { timeoutMs: 60000 });
      if (!resp.success) throw new Error(resp.error || "连接失败");
      showToast(`${target} ${resp.data.status || "连接正常"}`);
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("saveRoleBtn")?.addEventListener("click", async () => {
    try {
      const resp = await callEntry("save_role_config", { config: collectRoleConfig() });
      if (!resp.success) throw new Error(resp.error || "保存角色配置失败");
      showToast("角色配置已保存并实时生效");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("saveRoleProfilesBtn")?.addEventListener("click", async () => {
    try {
      const visualParsed = collectRoleProfilesFromEditor();
      const raw = document.getElementById("roleProfilesInput")?.value.trim() || "{}";
      const parsed = Object.keys(visualParsed).length ? visualParsed : JSON.parse(raw);
      const resp = await callEntry("save_role_profiles", { profiles: parsed });
      if (!resp.success) throw new Error(resp.error || "保存角色样式失败");
      applyBackendState(resp.data.state || state.backend);
      showToast("角色音色与字幕样式已保存");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("addRoleProfileBtn")?.addEventListener("click", () => {
    const current = collectRoleProfilesFromEditor();
    let idx = 1;
    while (current[`新角色${idx}`]) idx += 1;
    current[`新角色${idx}`] = {
      tts_voice: "",
      subtitle_color: "#d8e8ff",
      subtitle_glow: "rgba(178,206,255,0.48)",
      font_scale: 1,
    };
    renderRoleProfilesEditor(current);
    syncRoleProfilesJsonFromEditor();
  });

  document.getElementById("applyRoleTemplateBtn")?.addEventListener("click", () => {
    const template = {
      男主: { tts_voice: "longxiaocheng_v2", subtitle_color: "#d8e8ff", subtitle_glow: "rgba(178,206,255,0.48)", font_scale: 1.05 },
      女主: { tts_voice: "longxiaochun_v2", subtitle_color: "#ffd8ef", subtitle_glow: "rgba(255,183,222,0.52)", font_scale: 1.08 },
      路人: { tts_voice: "", subtitle_color: "#ebe0ff", subtitle_glow: "rgba(206,184,255,0.48)", font_scale: 1.0 },
    };
    renderRoleProfilesEditor(template);
    syncRoleProfilesJsonFromEditor();
  });

  document.getElementById("saveBgModeBtn")?.addEventListener("click", async () => {
    try {
      const mode = document.getElementById("backgroundMode")?.value || "semi_auto";
      const resp = await callEntry("set_background_mode", { mode });
      if (!resp.success) throw new Error(resp.error || "保存背景模式失败");
      applyBackendState(resp.data.state || state.backend);
      showToast("背景模式已保存");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("prepareBtn")?.addEventListener("click", async () => {
    const script = document.getElementById("scriptInput")?.value.trim() || "";
    if (!script) {
      showToast("请先输入剧情剧本");
      return;
    }
    const sceneSplitMode = document.getElementById("sceneSplitMode")?.value || "strict_copy";
    const maxScenes = Math.max(0, Number(document.getElementById("maxScenes")?.value || 0));
    const exactScenes = Math.max(0, Number(document.getElementById("exactScenes")?.value || 0));
    setProgress(8);
    document.getElementById("prepareSummary").textContent = "正在解析剧本...";
    try {
      const resp = await callEntry("prepare_script", {
        script_text: script,
        scene_split_mode: sceneSplitMode,
        max_scenes: maxScenes,
        exact_scenes: exactScenes,
      }, { timeoutMs: 900000, pollIntervalMs: 600 });
      if (!resp.success) throw new Error(resp.error || "预生成失败");
      applyBackendState(resp.data.state || {});
      showToast("预生成完成，素材可在“生成素材库”中查看");
    } catch (err) {
      setProgress(0);
      const msg = String(err.message || err);
      document.getElementById("prepareSummary").textContent = msg;
      showToast(msg);
    }
  });

  document.getElementById("confirmBtn")?.addEventListener("click", async () => {
    const spoken = document.getElementById("currentUserLine")?.value.trim() || "";
    if (!spoken) {
      showToast("当前无可确认台词");
      return;
    }
    try {
      const resp = await callEntry("confirm_current_line", { spoken_line: spoken });
      if (!resp.success) throw new Error(resp.error || "推进失败");
      applyBackendState(resp.data.state || {});
      const slideMode = document.getElementById("slideMode")?.value || "timer";
      if (slideMode === "dialogue") {
        advanceDialogueSlideshow();
      }
      showToast("已推送台词给主对话模型并推进剧情");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("refreshBtn")?.addEventListener("click", async () => {
    try {
      await refreshState();
      showToast("状态已刷新");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("loadStateBtn")?.addEventListener("click", async () => {
    try {
      await refreshState();
      showToast("已重新读取状态");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("clearOverrideBtn")?.addEventListener("click", async () => {
    stopSlideshow();
    try {
      const resp = await callEntry("clear_stage_override", {});
      if (!resp.success) throw new Error(resp.error || "恢复失败");
      applyBackendState(resp.data.state || {});
      showToast("已恢复 AI 自动舞台");
    } catch (err) {
      showToast(String(err.message || err));
    }
  });

  document.getElementById("startSlideshowBtn")?.addEventListener("click", () => {
    playSlideshow().catch((err) => showToast(String(err.message || err)));
  });

  document.getElementById("applySlideSecondsToAllBtn")?.addEventListener("click", () => {
    applySlideSecondsToAllUploads();
  });

  document.getElementById("stopSlideshowBtn")?.addEventListener("click", () => {
    stopSlideshow();
  });

  document.getElementById("stagePrevBtn")?.addEventListener("click", () => {
    retreatPlayback();
  });
  document.getElementById("stageNextBtn")?.addEventListener("click", () => {
    advancePlayback(false);
  });

  document.getElementById("imageUpload")?.addEventListener("change", async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    await uploadFiles(files, "image");
  });

  document.getElementById("videoUpload")?.addEventListener("change", async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = "";
    if (!files.length) return;
    await uploadFiles(files, "video");
  });

  document.getElementById("fullscreenToggleBtn")?.addEventListener("click", async () => {
    const root = document.documentElement;
    const inFs = !!document.fullscreenElement;
    try {
      if (!inFs && root.requestFullscreen) {
        await root.requestFullscreen();
        document.body.classList.add("fullscreen");
        document.getElementById("fullscreenToggleBtn").textContent = "窗口模式";
      } else if (inFs && document.exitFullscreen) {
        await document.exitFullscreen();
        document.body.classList.remove("fullscreen");
        document.getElementById("fullscreenToggleBtn").textContent = "全屏模式";
      }
    } catch (err) {
      showToast("全屏切换失败：" + String(err.message || err));
    }
  });

  document.getElementById("confirmCollapseBtn")?.addEventListener("click", () => {
    const box = document.getElementById("confirmBox");
    if (!box) return;
    state.confirmCollapsed = !state.confirmCollapsed;
    box.classList.toggle("collapsed", state.confirmCollapsed);
    document.getElementById("confirmCollapseBtn").textContent = state.confirmCollapsed ? "展开" : "收起";
  });

  document.getElementById("stageFloatToggleBtn")?.addEventListener("click", () => {
    const panel = document.getElementById("stageFloatPanel");
    if (!panel) return;
    const nextCollapsed = !panel.classList.contains("collapsed");
    panel.classList.toggle("collapsed", nextCollapsed);
    document.getElementById("stageFloatToggleBtn").textContent = nextCollapsed ? "展开快捷菜单" : "收起快捷菜单";
  });

  document.getElementById("applySubtitleSettingsBtn")?.addEventListener("click", () => {
    const sec = Number(document.getElementById("liveSubtitleDurationSec")?.value || 2.8);
    if (!Number.isFinite(sec)) return;
    state.liveSubtitleDurationMs = Math.max(500, Math.min(20000, sec * 1000));
    showToast(`字幕时长已设置为 ${Math.round(state.liveSubtitleDurationMs / 100) / 10} 秒`);
  });
  document.getElementById("liveSubtitleDurationSec")?.addEventListener("change", () => {
    const sec = Number(document.getElementById("liveSubtitleDurationSec")?.value || 2.8);
    if (!Number.isFinite(sec)) return;
    state.liveSubtitleDurationMs = Math.max(500, Math.min(20000, sec * 1000));
  });

  document.getElementById("textToggleBtn")?.addEventListener("click", () => {
    state.stageTextHidden = !state.stageTextHidden;
    document.body.classList.toggle("hide-stage-text", state.stageTextHidden);
    document.getElementById("textToggleBtn").textContent = state.stageTextHidden ? "显示舞台文字" : "隐藏舞台文字";
  });

  document.getElementById("quickShowTextBtn")?.addEventListener("click", () => {
    state.stageTextHidden = false;
    document.body.classList.remove("hide-stage-text");
    document.getElementById("textToggleBtn").textContent = "隐藏舞台文字";
  });
  document.getElementById("quickHideTextBtn")?.addEventListener("click", () => {
    state.stageTextHidden = true;
    document.body.classList.add("hide-stage-text");
    document.getElementById("textToggleBtn").textContent = "显示舞台文字";
  });
  document.getElementById("quickEnterFullscreenBtn")?.addEventListener("click", async () => {
    if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
      await document.documentElement.requestFullscreen();
    }
  });
  document.getElementById("quickExitFullscreenBtn")?.addEventListener("click", async () => {
    if (document.fullscreenElement && document.exitFullscreen) {
      await document.exitFullscreen();
    }
  });

  document.addEventListener("fullscreenchange", () => {
    const inFs = !!document.fullscreenElement;
    document.body.classList.toggle("fullscreen", inFs);
    document.getElementById("fullscreenToggleBtn").textContent = inFs ? "窗口模式" : "全屏模式";
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      retreatPlayback();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      advancePlayback(false);
    }
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  initConfirmBoxInteractions();
  bindEvents();
  try {
    await refreshState();
  } catch (err) {
    showToast(String(err.message || err));
  }
  renderUploadList();
  renderRoleProfilesEditor({});
  const subtitleDurationInput = document.getElementById("liveSubtitleDurationSec");
  if (subtitleDurationInput) subtitleDurationInput.value = String(state.liveSubtitleDurationMs / 1000);
});
