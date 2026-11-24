import { withPermissions } from './shared'
import permissionLabels from '../plugin-permissions/en-US'

const messages = {
  title: 'Permissions',
  dialogTitle: 'Confirm plugin capabilities',
  introduction:
    'This plugin can use the capabilities below while it runs. Confirm them to start it.',
  analysisPending:
    'Capability analysis is not finished yet. Click start again to start directly: {names}',
  incomplete: 'Capability analysis is incomplete. You can still confirm and start this plugin.',
  upgraded: 'Added after upgrade',
  pending: 'Pending confirmation',
  decision: {
    approved: 'Approved',
    denied: 'Denied',
  },
  banner: {
    confirm: '{n} capabilities need confirmation',
    upgradedConfirm: '{n} new capabilities added after upgrade need confirmation',
    capabilitiesDesc:
      'The plugin may read your conversations or memory, or send content to N.E.K.O. Once confirmed you will not be asked again until an upgrade adds new capabilities.',
    incompleteTitle: 'Capability analysis did not finish completely',
    incompleteDesc:
      'Results may be incomplete. You can still approve and keep using the plugin.',
    deniedDesc: 'Autostart will skip this plugin; starting it manually asks again.',
    approvedAt: 'Confirmed {date}',
    analyzingTitle: 'Analyzing plugin capabilities…',
    analyzingDesc: 'Results will appear here automatically once the analysis finishes.',
  },
  approve: 'Approve',
  reject: 'Deny',
  approveAndStart: 'Approve and start',
  revoke: 'Revoke confirmation',
  revokeDecision: 'Withdraw decision',
  evidenceToggle: 'Code locations ({n})',
  meta: {
    analyzed: 'Analyzed {date}',
    version: 'Version {version}',
    files: '{n} files',
  },
  noticesTitle: 'Plugin capabilities need confirmation',
  loading: 'Loading capability information…',
  loadFailed: 'Capability information could not be loaded.',
  saveFailed: 'The decision could not be saved. Please retry.',
  unknown:
    'Its exact data use is not yet known. Plugin SDKs can read conversations and memory, and send content to N.E.K.O.',
  noneFound: 'No issues found: the code scan found no permission use and no code it could not confirm.',
  retry: 'Analyze again',
  findings: {
    usedTitle: 'Permissions this plugin uses',
    usedBanner: 'This plugin uses {n} permission(s)',
    declared: 'Declared by the author',
    detected: 'Found in code',
    blindSpotsTitle: '{n} place(s) could not be confirmed by static analysis',
    blindSpotsDesc: 'These places load code dynamically, use reflection or run generated code, or were not analyzed. What they do cannot be determined without running the plugin.',
    unlocated: 'Part of the plugin (no single file)',
    partialTag: 'Partly unconfirmable',
    partialTagHint: 'Some code could not be analyzed statically. See the details page.',
  },
  errors: {
    sourceUnknown: 'The plugin\'s current install source could not be determined, so the replacement was stopped to protect the permissions you granted. The plugin was left unchanged. Uninstall the plugin and install it again to recover.',
    readFailed: 'Plugin permission records could not be read right now. Nothing was changed. Please try again in a moment.',
    corrupt: 'The plugin permission records file (plugin_permissions.json) is damaged and cannot be read, so the operation was stopped. Move that file out of the N.E.K.O config folder and restart N.E.K.O; plugins may ask for their permissions again.',
    unsupportedVersion: 'Plugin permission records were saved by a newer version of N.E.K.O and cannot be read by this version, so the operation was stopped. Update N.E.K.O to the latest version and try again.',
    writeFailed: 'Plugin permission records could not be saved. Nothing was changed. Check free disk space and write access to the N.E.K.O config folder, then try again.',
  },
  warnings: {
    budget_exceeded: 'Analysis stopped at its work budget.',
    source_too_large: 'A source file exceeded the analysis budget.',
    source_unreadable: 'A source file could not be analyzed.',
    analysis_failed: 'The background analysis could not be completed.',
  },
  capabilities: {
    conversations_read: {
      title: 'Read conversations',
      description:
        'May read recent user messages, conversation records, or observe new conversations and completed replies.',
    },
    memory_read: {
      title: 'Read memory',
      description: 'May search long-term memory or read character memory and persona information.',
    },
    messages_push: {
      title: 'Send content to N.E.K.O',
      description: 'May send messages, display cards or content, or attach content to replies.',
    },
  },
}

export default withPermissions(messages, permissionLabels)
