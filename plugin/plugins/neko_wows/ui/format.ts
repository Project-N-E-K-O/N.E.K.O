import type { Tone } from "@neko/plugin-ui"

import type { Translate } from "./types"

export function formatClock(at?: number | null, locale?: string): string {
  if (!at) return "—"
  const date = new Date(at * 1000)
  if (Number.isNaN(date.getTime())) return "—"
  return date.toLocaleTimeString(locale || undefined)
}

export function formatPercent(
  ratio: number | null | undefined,
  t: Translate
): string {
  if (ratio === null || ratio === undefined) return t("common.unknown")
  return `${Math.round(ratio * 100)}%`
}

export function formatMetres(
  value: number | null | undefined,
  t: Translate
): string {
  if (value === null || value === undefined) return t("common.unknown")
  return `${value} m`
}

export function formatCount(
  value: number | null | undefined,
  t: Translate
): string {
  if (value === null || value === undefined) return t("common.unknown")
  return String(value)
}

export function serviceModeLabel(mode: string | undefined, t: Translate): string {
  switch (mode) {
    case "external":
      return t("format.service.external")
    case "managed":
      return t("format.service.managed")
    case "conflict":
      return t("format.service.conflict")
    case "disabled":
      return t("format.service.disabled")
    default:
      return t("format.service.offline")
  }
}

export function serviceModeTone(mode?: string): Tone {
  if (mode === "external" || mode === "managed") return "success"
  if (mode === "conflict") return "danger"
  return "warning"
}

export function sourceStatusLabel(
  status: string | undefined,
  t: Translate
): string {
  switch (status) {
    case "live":
      return t("format.source.live")
    case "stale":
      return t("format.source.stale")
    case "ended":
      return t("format.source.ended")
    case "waiting":
      return t("format.source.waiting")
    default:
      return t("common.unknown")
  }
}

export function sourceStatusTone(status?: string): Tone {
  if (status === "live") return "success"
  if (status === "stale") return "danger"
  if (status === "ended") return "info"
  return "warning"
}

export function intrusionModeLabel(
  mode: string | undefined,
  t: Translate
): string {
  switch (mode) {
    case "no_interrupt":
      return t("preferences.intrusion.noInterrupt")
    case "allow_interrupt":
      return t("preferences.intrusion.allow")
    case "critical_only":
      return t("preferences.intrusion.criticalOnly")
    default:
      return mode || "—"
  }
}

export function availabilityLabel(
  value: string | undefined,
  t: Translate
): string {
  switch (value) {
    case "available":
      return t("format.availability.available")
    case "stale":
      return t("format.availability.stale")
    case "unsupported":
      return t("format.availability.unsupported")
    default:
      return t("format.availability.unknown")
  }
}

export function availabilityTone(value?: string): Tone {
  if (value === "available") return "success"
  if (value === "stale") return "warning"
  if (value === "unsupported") return "default"
  return "info"
}

export function outcomeLabel(
  stage: string | undefined,
  outcome: string | undefined,
  t: Translate
): string {
  const key = outcome ? `format.outcome.${stage || "other"}.${outcome}` : ""
  if (!key) return "—"
  const translated = t(key)
  return translated === key ? outcome || "—" : translated
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

export function stageLabel(stage: string | undefined, t: Translate): string {
  if (!stage) return "—"
  const key = `format.stage.${stage}`
  const translated = t(key)
  return translated === key ? stage : translated
}

export function categoryLabel(
  category: string | undefined,
  t: Translate
): string {
  if (!category) return "—"
  const key = `format.category.${category}`
  const translated = t(key)
  return translated === key ? category : translated
}
