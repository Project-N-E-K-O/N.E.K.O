import {
  Alert,
  Button,
  Card,
  DataTable,
  Grid,
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

export function ToggleSwitch(props: {
  checked: boolean
  label?: any
  disabled?: boolean
  tone?: string
  onChange: (value: boolean) => void
}) {
  const checked = !!props.checked
  const disabled = !!props.disabled
  // Use host theme variables so dark mode follows the shell.
  const onColor = props.tone === "success" ? "var(--success)" : "var(--primary)"
  const onGlow = props.tone === "success" ? "0 0 0 2px rgba(103, 194, 58, 0.18)" : "0 0 0 2px rgba(64, 158, 255, 0.18)"
  const trackColor = disabled ? "var(--border)" : checked ? onColor : "var(--muted)"
  const labelColor = disabled ? "var(--muted)" : "var(--text)"

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked ? "true" : "false"}
      disabled={disabled}
      onClick={() => {
        if (!disabled) {
          props.onChange(!checked)
        }
      }}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "8px",
        minHeight: "32px",
        padding: "0",
        border: "0",
        background: "transparent",
        color: labelColor,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.68 : 1,
      }}
    >
      <span
        aria-hidden="true"
        style={{
          position: "relative",
          width: "42px",
          height: "24px",
          borderRadius: "999px",
          background: trackColor,
          transition: "background 160ms ease",
          boxShadow: checked ? onGlow : "inset 0 0 0 1px rgba(148, 163, 184, 0.45)",
          flex: "0 0 auto",
        }}
      >
        <span
          style={{
            position: "absolute",
            top: "2px",
            left: "2px",
            width: "20px",
            height: "20px",
            borderRadius: "50%",
            background: "#ffffff",
            transform: checked ? "translateX(18px)" : "translateX(0)",
            transition: "transform 160ms ease",
            boxShadow: "0 1px 3px rgba(17, 24, 39, 0.32)",
          }}
        />
      </span>
      {props.label ? <span>{props.label}</span> : null}
    </button>
  )
}

export function AvatarPreview(props: { src?: string; alt: any }) {
  if (!props.src) {
    return (
      <div
        style={{
          width: "72px",
          height: "72px",
          borderRadius: "8px",
          border: "1px solid var(--border)",
          background: "var(--surface)",
        }}
      />
    )
  }

  return (
    <img
      src={props.src}
      alt={props.alt}
      style={{
        width: "72px",
        height: "72px",
        borderRadius: "8px",
        objectFit: "cover",
        border: "1px solid var(--border)",
        background: "var(--surface)",
      }}
    />
  )
}

export function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

export function AuthCard({
  t,
  loginState,
  loginLoggedIn,
  loginName,
  loginUid,
  disabled = false,
  onLogin,
  onLoginCheck,
  onLogout,
}: {
  t: PanelTranslator
  loginState: any
  loginLoggedIn: boolean
  loginName: string
  loginUid: string
  disabled?: boolean
  onLogin: () => void
  onLoginCheck: () => void
  onLogout: () => void
}) {
  return (
    <Card title={t("panel.auth.title")}>
      <Stack>
        <Text>
          {loginLoggedIn
            ? t("panel.auth.loggedIn") + (loginName ? ": " + loginName : "") + (loginUid ? " (UID " + loginUid + ")" : "")
            : t("panel.auth.loggedOut")}
        </Text>
        {loginLoggedIn ? (
          <Grid cols={2}>
            <Button tone="info" disabled={disabled} onClick={onLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            <Button tone="danger" disabled={disabled} onClick={onLogout}>{t("panel.actions.biliLogout")}</Button>
          </Grid>
        ) : (
          <Stack>
            <Grid cols={2}>
              <Button tone="info" disabled={disabled} onClick={onLogin}>{t("panel.actions.biliLogin")}</Button>
              <Button tone="success" disabled={disabled} onClick={onLoginCheck}>{t("panel.actions.biliLoginCheck")}</Button>
            </Grid>
            {loginState?.qrcode_image ? (
              <Stack>
                {/* hosted-ui strips data: URLs from img src, so the QR code uses a CSS background image. */}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={onLogin}
                  aria-label={t("panel.auth.refreshHint")}
                  title={t("panel.auth.refreshHint")}
                  style={{
                    width: "180px",
                    height: "180px",
                    boxSizing: "border-box",
                    padding: "8px",
                    borderRadius: "8px",
                    border: "none",
                    cursor: disabled ? "not-allowed" : "pointer",
                    backgroundColor: "#ffffff",
                    backgroundImage: `url("${loginState.qrcode_image}")`,
                    backgroundRepeat: "no-repeat",
                    backgroundPosition: "center",
                    backgroundSize: "contain",
                    backgroundOrigin: "content-box",
                  }}
                />
                <Text>{t("panel.auth.scanHint")}</Text>
                <Text>{t("panel.auth.refreshHint")}</Text>
              </Stack>
            ) : null}
            {loginState?.message ? <Text>{loginState.message}</Text> : null}
          </Stack>
        )}
      </Stack>
    </Card>
  )
}

export function StatusBadgeRow({
  items,
  t,
}: {
  items: Array<{ key: string; tone?: "success" | "warning" | "danger" | "default" }>
  t: PanelTranslator
}) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
      {items.map((item) => (
        <span key={item.key}>
          <StatusBadge tone={item.tone || "default"} label={t(item.key)} />
        </span>
      ))}
    </div>
  )
}

export function ModuleOverviewCard({ modules, t }: { modules: Array<Record<string, any>>; t: PanelTranslator }) {
  return (
    <Card title={t("panel.tabs.modules")}>
      {modules.length ? (
        <DataTable
          data={modules.map((item: any, index: number) => ({ ...item, id: item.id || String(index) }))}
          rowKey="id"
          columns={[
            { key: "title", label: t("panel.modules.name"), render: (row: any) => row.title || row.id || "-" },
            { key: "status", label: t("panel.modules.status"), render: (row: any) => <ModuleHealthBadge module={row} t={t} /> },
            { key: "id", label: "ID", render: (row: any) => row.id || "-" },
          ]}
        />
      ) : (
        <Text>{t("panel.modules.empty")}</Text>
      )}
    </Card>
  )
}

export function ComingSoonSection({ title, desc, t }: { title: any; desc: any; t: PanelTranslator }) {
  return (
    <Stack>
      <div style={{ opacity: 0.7 }}>
        <Card>
          <Stack gap={10}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
              <span style={{ color: "var(--text)", fontSize: "15px", fontWeight: 720 }}>{title}</span>
              <StatusBadge tone="info" label={t("panel.modules.soon")} />
            </div>
            <Text>{desc}</Text>
          </Stack>
        </Card>
      </div>
    </Stack>
  )
}
