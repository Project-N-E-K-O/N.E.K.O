import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Tip,
  Warning,
  Steps,
  Step,
  CodeBlock,
  StatusBadge,
  StatCard,
  KeyValue,
  Divider,
  Alert,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type LifeKitGuideState = {
  config?: {
    default_city?: string
    timezone?: string
    forecast_days?: number
    locale?: string
    force_locale?: boolean
  }
  locations?: Array<{ label?: string; city?: string; is_default?: boolean }>
  location_count?: number
  default_location?: { label?: string; city?: string } | null
  store_enabled?: boolean
  locale?: string
}

export default function LifeKitQuickstartGuide(props: PluginSurfaceProps<LifeKitGuideState>) {
  const { plugin, state, t } = props
  const safePlugin = plugin || {}
  const safeState = state || {}
  const config = safeState.config || {}
  const locations = Array.isArray(safeState.locations) ? safeState.locations : []
  const defaultLocation = safeState.default_location
  const storeEnabled = safeState.store_enabled !== false
  const forecastDays = config.forecast_days || 3
  const locale = safeState.locale || config.locale || t("quickstart.status.auto")
  const defaultCity = config.default_city || t("quickstart.status.autoLocation")
  const timezone = config.timezone || "Asia/Shanghai"
  const configExample = `[lifekit]
default_city = "Shanghai"
timezone = "Asia/Shanghai"
forecast_days = 3
locale = ""
force_locale = false`
  const locationExample = `label: Home
city: Shanghai
address: Pudong Lujiazui
set_default: true`
  const routeExample = `origin: Home
destination: Shanghai Hongqiao Railway Station
mode: transit`
  const unitExample = `value: 5
from_unit: km
to_unit: mile`

  return (
    <Page title={t("quickstart.title")} subtitle={t("quickstart.subtitle")}>
      <Grid cols={3}>
        <Card title={t("quickstart.cards.configure.title")}>
          <Stack>
            <StatusBadge tone="primary">{t("quickstart.badges.settings")}</StatusBadge>
            <Text>{t("quickstart.cards.configure.body")}</Text>
          </Stack>
        </Card>
        <Card title={t("quickstart.cards.locations.title")}>
          <Stack>
            <StatusBadge tone="success">{t("quickstart.badges.locations")}</StatusBadge>
            <Text>{t("quickstart.cards.locations.body")}</Text>
          </Stack>
        </Card>
        <Card title={t("quickstart.cards.tools.title")}>
          <Stack>
            <StatusBadge tone="info">{t("quickstart.badges.tools")}</StatusBadge>
            <Text>{t("quickstart.cards.tools.body")}</Text>
          </Stack>
        </Card>
      </Grid>

      <Grid cols={4}>
        <StatCard label={t("quickstart.stats.locations")} value={locations.length} />
        <StatCard label={t("quickstart.stats.defaultLocation")} value={defaultLocation?.label || "-"} />
        <StatCard label={t("quickstart.stats.forecastDays")} value={forecastDays} />
        <StatCard label={t("quickstart.stats.locale")} value={locale} />
      </Grid>

      <Alert tone={storeEnabled ? "success" : "warning"}>
        {storeEnabled
          ? t("quickstart.alert.storeEnabled")
          : t("quickstart.alert.storeDisabled")}
      </Alert>

      <Card title={t("quickstart.path.title")}>
        <Steps>
          <Step index="1" title={t("quickstart.path.openPanel.title")}>
            <Text>{t("quickstart.path.openPanel.body")}</Text>
          </Step>
          <Step index="2" title={t("quickstart.path.configure.title")}>
            <Text>{t("quickstart.path.configure.body")}</Text>
          </Step>
          <Step index="3" title={t("quickstart.path.addLocations.title")}>
            <Text>{t("quickstart.path.addLocations.body")}</Text>
          </Step>
          <Step index="4" title={t("quickstart.path.useTools.title")}>
            <Text>{t("quickstart.path.useTools.body")}</Text>
          </Step>
          <Step index="5" title={t("quickstart.path.review.title")}>
            <Text>{t("quickstart.path.review.body")}</Text>
          </Step>
        </Steps>
      </Card>

      <Grid cols={2}>
        <Card title={t("quickstart.examples.config")}>
          <CodeBlock>{configExample}</CodeBlock>
        </Card>
        <Card title={t("quickstart.examples.location")}>
          <CodeBlock>{locationExample}</CodeBlock>
        </Card>
      </Grid>

      <Grid cols={2}>
        <Card title={t("quickstart.tools.weather.title")}>
          <Stack>
            <Text>{t("quickstart.tools.weather.body")}</Text>
            <KeyValue
              items={[
                { key: "get_weather", label: "get_weather", value: t("quickstart.tools.weather.current") },
                { key: "hourly_forecast", label: "hourly_forecast", value: t("quickstart.tools.weather.hourly") },
                { key: "travel_advice", label: "travel_advice", value: t("quickstart.tools.weather.travel") },
                { key: "air_quality", label: "air_quality", value: t("quickstart.tools.weather.air") },
              ]}
            />
          </Stack>
        </Card>
        <Card title={t("quickstart.tools.places.title")}>
          <Stack>
            <Text>{t("quickstart.tools.places.body")}</Text>
            <KeyValue
              items={[
                { key: "search_nearby", label: "search_nearby", value: t("quickstart.tools.places.nearby") },
                { key: "trip_advice", label: "trip_advice", value: t("quickstart.tools.places.trip") },
                { key: "food_recommend", label: "food_recommend", value: t("quickstart.tools.places.food") },
              ]}
            />
          </Stack>
        </Card>
      </Grid>

      <Grid cols={2}>
        <Card title={t("quickstart.examples.route")}>
          <CodeBlock>{routeExample}</CodeBlock>
        </Card>
        <Card title={t("quickstart.examples.unit")}>
          <CodeBlock>{unitExample}</CodeBlock>
        </Card>
      </Grid>

      <Card title={t("quickstart.tools.everyday.title")}>
        <Grid cols={3}>
          <Card title={t("quickstart.tools.everyday.recipe.title")}>
            <Text>{t("quickstart.tools.everyday.recipe.body")}</Text>
          </Card>
          <Card title={t("quickstart.tools.everyday.calendar.title")}>
            <Text>{t("quickstart.tools.everyday.calendar.body")}</Text>
          </Card>
          <Card title={t("quickstart.tools.everyday.convert.title")}>
            <Text>{t("quickstart.tools.everyday.convert.body")}</Text>
          </Card>
        </Grid>
      </Card>

      <Card title={t("quickstart.current.title")}>
        <KeyValue
          items={[
            { key: "plugin", label: t("quickstart.current.plugin"), value: safePlugin.id || "lifekit" },
            { key: "default_city", label: t("quickstart.current.defaultCity"), value: defaultCity },
            { key: "timezone", label: t("quickstart.current.timezone"), value: timezone },
            { key: "default_location", label: t("quickstart.current.defaultLocation"), value: defaultLocation?.label || "-" },
          ]}
        />
      </Card>

      <Card title={t("quickstart.faq.title")}>
        <Stack>
          <Tip>{t("quickstart.faq.noLocation")}</Tip>
          <Tip>{t("quickstart.faq.routeKeys")}</Tip>
          <Tip>{t("quickstart.faq.locale")}</Tip>
        </Stack>
      </Card>

      <Card title={t("quickstart.next.title")}>
        <Text>{t("quickstart.next.body")}</Text>
        <Divider />
        <KeyValue
          items={[
            { key: "panel", label: t("quickstart.next.panel"), value: "ui/panel.tsx" },
            { key: "guide", label: t("quickstart.next.guide"), value: "docs/quickstart.tsx" },
            { key: "entries", label: t("quickstart.next.entries"), value: t("quickstart.next.entriesValue") },
          ]}
        />
      </Card>

      <Warning>{t("quickstart.warning.network")}</Warning>
    </Page>
  )
}
