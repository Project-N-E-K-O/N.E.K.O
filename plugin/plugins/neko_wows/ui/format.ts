import type { Tone } from "@neko/plugin-ui"

export function formatClock(at?: number | null): string {
  if (!at) return "—"
  const date = new Date(at * 1000)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleTimeString()
}

export function formatPercent(ratio?: number | null): string {
  if (ratio === null || ratio === undefined) return "未知"
  return `${Math.round(ratio * 100)}%`
}

export function formatMetres(value?: number | null): string {
  if (value === null || value === undefined) return "未知"
  return `${value} m`
}

export function formatCount(value?: number | null): string {
  if (value === null || value === undefined) return "未知"
  return String(value)
}

/** Service supervision mode, in the user's terms. */
export function serviceModeLabel(mode?: string): string {
  switch (mode) {
    case "external":
      return "外部服务（不由插件管理）"
    case "managed":
      return "插件已拉起"
    case "conflict":
      return "端口被其它服务占用"
    case "disabled":
      return "未启用自动拉起"
    default:
      return "未连接"
  }
}

export function serviceModeTone(mode?: string): Tone {
  if (mode === "external" || mode === "managed") return "success"
  if (mode === "conflict") return "danger"
  return "warning"
}

export function sourceStatusLabel(status?: string): string {
  switch (status) {
    case "live":
      return "对战中"
    case "stale":
      return "数据停更"
    case "ended":
      return "本局已结束"
    case "waiting":
      return "等待战局"
    default:
      return "未知"
  }
}

export function sourceStatusTone(status?: string): Tone {
  if (status === "live") return "success"
  if (status === "stale") return "danger"
  if (status === "ended") return "info"
  return "warning"
}

export function availabilityLabel(value?: string): string {
  switch (value) {
    case "available":
      return "可用"
    case "stale":
      return "过期"
    case "unsupported":
      return "服务未提供"
    default:
      return "本帧无数据"
  }
}

export function availabilityTone(value?: string): Tone {
  if (value === "available") return "success"
  if (value === "stale") return "warning"
  if (value === "unsupported") return "default"
  return "info"
}

/**
 * Pipeline outcomes, phrased so a suppressed call-out never reads like a bug.
 * The distinction that matters most: the plugin chose not to speak, versus the
 * plugin tried and the host declined.
 */
export function outcomeLabel(stage?: string, outcome?: string): string {
  if (stage === "delivery") {
    switch (outcome) {
      case "delivered":
        return "已投给猫娘"
      case "dry_run":
        return "dry-run 短路（未投）"
      case "expired":
        return "过期未投"
      case "paused":
        return "已暂停"
      case "failed":
        return "投递失败"
      case "output_enabled":
        return "已开启真实输出"
      default:
        return outcome || "—"
    }
  }
  if (stage === "arbiter") {
    switch (outcome) {
      case "chosen":
        return "选中"
      case "queued":
        return "入队"
      case "cooldown":
        return "冷却中"
      case "lane_gap":
        return "通道间隔未到"
      case "once_per_battle":
        return "本局已说过"
      case "coalesced":
        return "被新事件合并"
      case "preempted":
        return "被抢占"
      case "expired":
        return "TTL 到期"
      case "paused":
        return "输出已暂停"
      case "quiet_window":
        return "插件静默窗口（你在聊天）"
      default:
        return outcome || "—"
    }
  }
  if (stage === "detect") {
    switch (outcome) {
      case "events":
        return "产生事件"
      case "blocked":
        return "能力缺失，未评估"
      case "baseline":
        return "仅建立基线"
      case "identity_reset":
        return "战局切换，已重置"
      case "reset":
        return "重置"
      case "evaluated":
        return "无事件"
      default:
        return outcome || "—"
    }
  }
  if (stage === "frame") {
    switch (outcome) {
      case "dropped":
        return "重复/乱序，已丢弃"
      case "rejected":
        return "无法解析"
      case "error":
        return "链路异常"
      default:
        return outcome || "—"
    }
  }
  return outcome || "—"
}

export function outcomeTone(stage?: string, outcome?: string): Tone {
  if (outcome === "delivered" || outcome === "chosen" || outcome === "events") {
    return "success"
  }
  if (outcome === "failed" || outcome === "error" || outcome === "rejected") {
    return "danger"
  }
  if (outcome === "blocked" || outcome === "paused" || outcome === "expired") {
    return "warning"
  }
  return "info"
}

export function stageLabel(stage?: string): string {
  switch (stage) {
    case "frame":
      return "快照"
    case "detect":
      return "检测"
    case "arbiter":
      return "仲裁"
    case "delivery":
      return "投递"
    case "service":
      return "服务"
    case "documents":
      return "文档"
    case "prompts":
      return "提示词"
    default:
      return stage || "—"
  }
}

/** Broadcast categories double as coalesce keys; show the human name. */
export function categoryLabel(category?: string): string {
  switch (category) {
    case "wows_lifecycle":
      return "开局与终局"
    case "wows_summary":
      return "战后摘要"
    case "wows_survival":
      return "生存（沉没/血量/受伤）"
    case "wows_situation":
      return "局势（人数/孤立/建议）"
    case "wows_threat":
      return "威胁（逼近/多方向）"
    case "wows_geometry":
      return "几何（边界/露侧）"
    case "wows_targeting":
      return "目标与弹药"
    case "wows_progress":
      return "伤害里程碑"
    default:
      return category || "—"
  }
}
