import bindingErrors from '../model-binding-errors/en-US'

/**
 * Words of the Plugin API (model slots) page.
 * One module per language, all of one shape, so that a language pack carries
 * its own words only (see ../power).
 */

const messages = {
  bindingErrors,
  title: 'Plugin API', subtitle: 'Manage the models your plugins can use.',
  slots: 'Model slots', addSlot: 'Add model slot', editSlot: 'Edit model slot',
  bindingHint: 'Create a slot here, then assign it to a declared use in the plugin’s configuration.',
  noSlots: 'No model slots yet. Add one to connect your plugins to a model.',
  name: 'Slot name', protocol: 'API protocol', model: 'Model ID', endpoint: 'Base URL',
  source: 'Credential source', sourceManual: 'Manual configuration', sourceHost: 'Follow the main app API',
  sourceHint: 'A follow slot uses the main app’s API settings at call time; provider and key changes there apply automatically. Manual slots store their own endpoint and key.',
  hostApiLabel: 'Main app API', hostApi: { agent_advanced: 'Main app familiar model (advanced)', agent: 'Main app familiar model (general)', agent_small: 'Main app familiar model (small)' },
  hostHint: { agent_advanced: 'Follows the advanced familiar model on the main app’s API settings page (it is the general familiar model unless set separately). Meant for the hardest jobs; it should support tool calling and image input.', agent: 'Follows the familiar (task agent) model on the main app’s API settings page: the model familiars, keyboard-and-mouse control and the browser use. It is expected to support tool calling and image input.', agent_small: 'Follows the small familiar model on the main app’s API settings page (it is the recognition model unless set separately). Suited to light, frequent work; it should still support tool calling.' },
  hostManagedHint: 'Endpoint and credentials follow the main app API settings.',
  hostManagedKey: 'Managed by the main app', hostNotConfigured: 'Main app API not configured',
  hostUnsupported: 'The followed main app model is not an HTTP chat endpoint — plugin chat calls are not supported',
  endpointHint: "Include the API prefix required by your provider. OpenAI-compatible endpoints usually need /v1, for example https://api.example.com/v1; add it if missing. Do not include /chat/completions or /messages.",
  key: 'API key', keySet: 'Configured', keyEmpty: 'No key',
  keyPlaceholder: 'Enter a key, or leave empty for an endpoint without authentication',
  keyEditHint: "Saved keys are masked. Leave unchanged to keep, enter a complete new key to replace, or clear to remove. Copying returns masked text only.",
  keyUpdateRequired: 'The endpoint or protocol changed. Enter a replacement key or explicitly clear the saved key.',
  capabilities: 'Capabilities', capabilitiesHint: 'Enable only capabilities supported by this model. These determine which plugin uses can bind to the slot.',
  capability: { text: 'Text', image_input: 'Image input', tool_calling: 'Function tools', streaming: 'Streaming' },
  maxTokens: 'Default output token limit',
  timeout: 'Total timeout', seconds: '{count} s', timeoutHint: '1–300 seconds, including queue time and the fallback attempt.',
  fallback: 'Fallback slot', noFallback: 'None', fallbackHint: 'One retry on eligible upstream errors, within the same timeout; the primary model may use at most 2/3 of it, so a stalled request can still switch. Requires matching capabilities.',
  usedBy: 'Used by', notBound: 'No plugin bindings', deleteBlocked: 'Remove plugin bindings and fallback references before deleting.',
  deleteConfirm: 'Delete model slot “{name}”?', test: 'Test connection',
  unbind: 'Unbind', unbindConfirm: 'Remove the model binding for {plugin} / {usage}? This use will need a new binding before it can call a model.',
  testHint: 'Connection tests use the saved slot and send a short text request. The provider may charge for it; image, tool and streaming support are not tested.',
  testSuccess: 'Text request succeeded · {duration}', requiredFields: 'Enter a slot name, model ID and base URL.',
  usage: 'Recent usage', pluginFilter: 'Filter by plugin ID', allSlots: 'All model slots',
  requests: 'Requests', attempts: 'Upstream attempts', knownTokens: 'Known tokens (including partial)',
  partialUnknown: 'Partial / unknown usage', usageHint: 'Totals cover up to 1,000 retained requests; the table shows the latest 100 matching requests. Missing usage is unknown, not zero. Expand a row for each attempt.',
  noUsage: 'No model requests in this view.', noAttempt: 'No upstream attempt was started.',
  time: 'Time', consumer: 'Plugin / use', slot: 'Model slot', result: 'Result', duration: 'Duration',
  connectionTest: 'Connection test', tokenCounts: 'Input {input} · Output {output} · Total {total}',
  status: { success: 'Succeeded', error: 'Failed', timeout: 'Timed out', cancelled: 'Cancelled' },
  usageStatus: { reported: 'Reported usage', partial: 'Partial usage', unknown: 'Unknown usage' },
}

export type ModelApiMessages = typeof messages
export default messages
