import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Tip,
  Alert,
  StatCard,
  StatusBadge,
  DataTable,
  Button,
  Field,
  Input,
  Select,
  Switch,
  RefreshButton,
  useForm,
  useEffect,
  useToast,
  useConfirm,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type LifeKitConfig = {
  default_city?: string
  timezone?: string
  forecast_days?: number
  locale?: string
  force_locale?: boolean
  cache_ttl_seconds?: number
}

type SavedLocation = {
  id?: string
  label?: string
  city?: string
  address?: string
  lat?: number
  lon?: number
  country?: string
  is_default?: boolean
}

type LifeKitDashboardState = {
  config?: LifeKitConfig
  locations?: SavedLocation[]
  location_count?: number
  default_location?: SavedLocation | null
  store_enabled?: boolean
  locale?: string
}

const defaultConfigForm = {
  default_city: "",
  timezone: "Asia/Shanghai",
  forecast_days: "3",
  locale: "",
  force_locale: false,
}

const emptyLocationForm = {
  label: "",
  city: "",
  address: "",
  set_default: false,
}

type ConfigFormValues = typeof defaultConfigForm
type LocationFormValues = typeof emptyLocationForm

function actionById(actions: HostedAction[], id: string): HostedAction | undefined {
  return actions.find((action) => action.id === id || action.entry_id === id)
}

function formatCoords(location: SavedLocation): string {
  if (typeof location.lat !== "number" || typeof location.lon !== "number") return "-"
  return `${location.lat.toFixed(4)}, ${location.lon.toFixed(4)}`
}

function locationKey(location: SavedLocation): string {
  return String(location.id || location.label || location.city || "")
}

export default function LifeKitPanel(props: PluginSurfaceProps<LifeKitDashboardState>) {
  const { state, actions, t } = props
  const safeState = state || {}
  const locations = Array.isArray(safeState.locations) ? safeState.locations : []
  const config = safeState.config || {}
  const storeEnabled = safeState.store_enabled !== false
  const toast = useToast()
  const confirm = useConfirm()
  const configForm = useForm<ConfigFormValues>(defaultConfigForm)
  const locationForm = useForm<LocationFormValues>(emptyLocationForm)
  const updateConfigAction = actionById(actions || [], "update_config")
  const addLocationAction = actionById(actions || [], "add_location")
  const removeLocationAction = actionById(actions || [], "remove_location")
  const setDefaultAction = actionById(actions || [], "set_default_location")

  useEffect(() => {
    configForm.setValues({
      default_city: String(config.default_city || ""),
      timezone: String(config.timezone || "Asia/Shanghai"),
      forecast_days: String(config.forecast_days || 3),
      locale: String(config.locale || ""),
      force_locale: !!config.force_locale,
    })
  }, [config.default_city, config.timezone, config.forecast_days, config.locale, config.force_locale])

  async function saveConfig() {
    if (!updateConfigAction) {
      toast.error(t("panel.errors.actionUnavailable"))
      return
    }
    try {
      await props.api.call("update_config", {
        default_city: configForm.values.default_city.trim(),
        timezone: configForm.values.timezone.trim() || "Asia/Shanghai",
        forecast_days: Number(configForm.values.forecast_days) || 3,
        locale: configForm.values.locale,
        force_locale: !!configForm.values.force_locale,
      })
      await props.api.refresh()
      toast.success(t("panel.messages.configSaved"))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  async function addLocation() {
    if (!addLocationAction) {
      toast.error(t("panel.errors.actionUnavailable"))
      return
    }
    const label = locationForm.values.label.trim()
    const city = locationForm.values.city.trim()
    if (!label || !city) {
      toast.error(t("panel.errors.labelCityRequired"))
      return
    }
    await props.api.call("add_location", {
      label,
      city,
      address: locationForm.values.address.trim(),
      set_default: !!locationForm.values.set_default,
    })
    locationForm.reset(emptyLocationForm)
    await props.api.refresh()
    toast.success(t("panel.messages.locationAdded", { label, city }))
  }

  async function removeLocation(location: SavedLocation) {
    if (!removeLocationAction) {
      toast.error(t("panel.errors.actionUnavailable"))
      return
    }
    const key = locationKey(location)
    if (!key) return
    const ok = await confirm({
      title: t("panel.actions.remove"),
      message: t("actions.removeLocation.confirm"),
      tone: "danger",
      confirmLabel: t("panel.actions.remove"),
      cancelLabel: t("panel.actions.cancel"),
    })
    if (!ok) return
    await props.api.call("remove_location", { location_id: key })
    await props.api.refresh()
    toast.success(t("panel.messages.locationRemoved"))
  }

  async function setDefaultLocation(location: SavedLocation) {
    if (!setDefaultAction) {
      toast.error(t("panel.errors.actionUnavailable"))
      return
    }
    const key = locationKey(location)
    if (!key) return
    await props.api.call("set_default_location", { location_id: key })
    await props.api.refresh()
    toast.success(t("panel.messages.defaultSet"))
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Grid cols={4}>
        <StatCard label={t("panel.stats.savedLocations")} value={locations.length} />
        <StatCard label={t("panel.stats.defaultLocation")} value={safeState.default_location?.label || "-"} />
        <StatCard label={t("panel.stats.locale")} value={safeState.locale || "-"} />
        <StatCard
          label={t("panel.stats.store")}
          value={<StatusBadge tone={storeEnabled ? "success" : "warning"} label={storeEnabled ? t("panel.store.enabled") : t("panel.store.disabledShort")} />}
        />
      </Grid>

      {!storeEnabled ? (
        <Alert tone="warning">{t("panel.store.disabled")}</Alert>
      ) : null}

      <Grid cols={2}>
        <Card title={t("panel.config.title")}>
          <Stack>
            <Field label={t("panel.fields.defaultCity")} help={t("panel.fields.defaultCityHelp")}>
              <Input
                value={configForm.values.default_city}
                placeholder={t("panel.placeholders.defaultCity")}
                onChange={(value) => configForm.setField("default_city", value)}
              />
            </Field>
            <Field label={t("panel.fields.timezone")}>
              <Input
                value={configForm.values.timezone}
                placeholder="Asia/Shanghai"
                onChange={(value) => configForm.setField("timezone", value)}
              />
            </Field>
            <Field label={t("panel.fields.forecastDays")} help={t("panel.fields.forecastDaysHelp")}>
              <Select
                value={configForm.values.forecast_days}
                options={["1", "2", "3", "4", "5", "6", "7"]}
                onChange={(value) => configForm.setField("forecast_days", String(value))}
              />
            </Field>
            <Field label={t("panel.fields.locale")}>
              <Select
                value={configForm.values.locale}
                options={[
                  { value: "", label: t("panel.options.auto") },
                  { value: "zh-CN", label: t("panel.options.zhCN") },
                  { value: "zh-TW", label: t("panel.options.zhTW") },
                  { value: "en", label: t("panel.options.en") },
                ]}
                onChange={(value) => configForm.setField("locale", String(value))}
              />
            </Field>
            <Switch
              checked={configForm.values.force_locale}
              label={t("panel.fields.forceLocale")}
              onChange={(value) => configForm.setField("force_locale", value)}
            />
            <Button tone="success" disabled={!updateConfigAction} onClick={saveConfig}>
              {t("panel.actions.saveConfig")}
            </Button>
          </Stack>
        </Card>

        <Card title={t("panel.add.title")}>
          <Stack>
            <Field label={t("panel.fields.label")} required>
              <Input
                value={locationForm.values.label}
                placeholder={t("panel.placeholders.label")}
                onChange={(value) => locationForm.setField("label", value)}
              />
            </Field>
            <Field label={t("panel.fields.city")} required>
              <Input
                value={locationForm.values.city}
                placeholder={t("panel.placeholders.city")}
                onChange={(value) => locationForm.setField("city", value)}
              />
            </Field>
            <Field label={t("panel.fields.address")} help={t("panel.fields.addressHelp")}>
              <Input
                value={locationForm.values.address}
                placeholder={t("panel.placeholders.address")}
                onChange={(value) => locationForm.setField("address", value)}
              />
            </Field>
            <Switch
              checked={locationForm.values.set_default}
              label={t("panel.fields.setDefault")}
              onChange={(value) => locationForm.setField("set_default", value)}
            />
            <Button tone="primary" disabled={!storeEnabled || !addLocationAction} onClick={addLocation}>
              {t("panel.actions.addLocation")}
            </Button>
            <Tip>{t("panel.add.tip")}</Tip>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("panel.locations.title")}>
        <Stack>
          <RefreshButton label={t("panel.actions.refresh")} />
          {locations.length ? (
            <DataTable
              data={locations}
              rowKey="id"
              columns={[
                { key: "label", label: t("panel.columns.label") },
                { key: "city", label: t("panel.columns.city") },
                { key: "address", label: t("panel.columns.address"), render: (row) => row.address || "-" },
                { key: "coords", label: t("panel.columns.coords"), render: (row) => formatCoords(row) },
                {
                  key: "default",
                  label: t("panel.columns.default"),
                  render: (row) => row.is_default ? <StatusBadge tone="primary" label={t("panel.badges.default")} /> : "-",
                },
                {
                  key: "actions",
                  label: t("panel.columns.actions"),
                  render: (row) => (
                    <Stack>
                      {!row.is_default ? (
                        <Button tone="primary" disabled={!setDefaultAction} onClick={() => setDefaultLocation(row)}>
                          {t("panel.actions.setDefault")}
                        </Button>
                      ) : null}
                      <Button tone="danger" disabled={!removeLocationAction} onClick={() => removeLocation(row)}>
                        {t("panel.actions.remove")}
                      </Button>
                    </Stack>
                  ),
                },
              ]}
            />
          ) : (
            <Card title={t("panel.locations.empty.title")}>
              <Text>{t("panel.locations.empty.description")}</Text>
            </Card>
          )}
        </Stack>
      </Card>
    </Page>
  )
}
