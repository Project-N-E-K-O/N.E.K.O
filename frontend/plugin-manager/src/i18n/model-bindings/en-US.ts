import bindingErrors from '../model-binding-errors/en-US'

/**
 * Words of the per-plugin model binding panel.
 * One module per language, all of one shape, so that a language pack carries
 * its own words only (see ../power).
 */

const messages = {
  bindingErrors,
  title: 'Model API bindings',
  manage: 'Manage slots and usage',
  description: 'Choose a saved model slot for each purpose declared by this plugin. Changes are saved immediately.',
  ready: 'All required model purposes are bound',
  notReady: 'Some required model purposes need a binding',
  readinessHint: 'This status only confirms required bindings. It does not check provider availability or control plugin startup.',
  boundSummary: '{bound} of {total} bound',
  collapse: 'Collapse',
  expand: 'Expand',
  required: 'Required',
  optional: 'Optional',
  unbound: 'Unbound',
  incompatible: 'Missing capabilities',
  noSlots: 'No model slots yet. Add a slot in Plugin API, then refresh here.',
  loadFailed: 'Could not load model bindings. Please retry.',
  saveFailed: 'Could not save this binding. The previous binding is still displayed.',
  capabilities: { text: 'Text', image_input: 'Image input', tool_calling: 'Tool calling', streaming: 'Streaming' },
  status: { bound: 'Bound', unbound: 'No slot selected', incompatible: 'The bound slot no longer meets this purpose’s requirements' },
}

export type ModelBindingsMessages = typeof messages
export default messages
