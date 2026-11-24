import type { Label } from './shared'

const labels = {
  plugin_sdk_models_invoke: {
    title: 'Use AI models (may incur costs)',
    description: "May call AI models through N.E.K.O's model gateway, which can incur usage costs.",
  },
  system_network_connect: {
    title: 'Connect to the internet',
    description: 'May connect to network services.',
  },
  system_network_listen: {
    title: 'Open a network port',
    description: 'May open a network port on this computer so other programs can connect to it.',
  },
  system_fs_read: {
    title: 'Read files',
    description: 'May read files outside its own data folder.',
  },
  system_fs_write: {
    title: 'Write files',
    description: 'May create or change files outside its own data folder.',
  },
  system_process_spawn: {
    title: 'Start programs',
    description: 'May start other programs or commands on this computer.',
  },
  system_env_read: {
    title: 'Read environment variables',
    description: "May read this computer's environment variables, which can contain API keys.",
  },
} satisfies Record<string, Label>

export type PermissionLabels = Record<keyof typeof labels, Label>

export default labels
