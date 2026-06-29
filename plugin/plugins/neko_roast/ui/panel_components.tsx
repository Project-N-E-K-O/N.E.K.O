import {
  Alert,
  Card,
  Stack,
  StatusBadge,
  Text,
} from "@neko/plugin-ui"

type PanelTranslator = (key: string) => string

export function ModuleHealthBadge({ module, t }: { module: any; t: PanelTranslator }) {
  if (module && module.degraded) return <StatusBadge tone="danger" label={t("panel.modules.degraded")} />
  const on = !!(module && module.enabled)
  const reserved = !!(module && module.status && module.status.reserved)
  return (
    <StatusBadge
      tone={on ? "success" : (reserved ? "default" : "warning")}
      label={on ? t("panel.modules.online") : (reserved ? t("panel.modules.soon") : t("panel.modules.off"))}
    />
  )
}

export function ModuleRenderBoundary({
  title,
  render,
  t,
}: {
  title: any
  render: () => any
  t: PanelTranslator
}) {
  try {
    return render()
  } catch (err) {
    const msg = err && (err as any).message ? String((err as any).message) : ""
    return (
      <Card title={title}>
        <Stack gap={8}>
          <StatusBadge tone="danger" label={t("panel.modules.degraded")} />
          <Alert tone="danger">{t("panel.modules.renderError")}</Alert>
          {msg ? <Text>{msg}</Text> : null}
        </Stack>
      </Card>
    )
  }
}
