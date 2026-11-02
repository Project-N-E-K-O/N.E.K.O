import { projectDirectoryHost, type PluginProjectBundle } from '@/api/pluginProjects'

export type ProjectDirectorySelection =
  { kind: 'native'; path: string } | { kind: 'browser'; handle: FileSystemDirectoryHandle }

interface DirectoryPickerWindow extends Window {
  showDirectoryPicker?: (options: {
    id: string
    mode: 'readwrite'
    startIn?: FileSystemDirectoryHandle
  }) => Promise<FileSystemDirectoryHandle>
}

export class ProjectDirectoryError extends Error {
  constructor(
    public readonly code: string,
    message = code
  ) {
    super(message)
  }
}

// Invoked directly by the click handler: native browser pickers need the
// original user activation, before any network call or permission await.
export async function pickProjectParent(
  path: string,
  title: string,
  previous?: FileSystemDirectoryHandle
): Promise<ProjectDirectorySelection | null> {
  const bridge = projectDirectoryHost()
  if (bridge?.pickDirectory) {
    const result = await bridge.pickDirectory({ title, startPath: path })
    return result.cancelled || !result.selected_root
      ? null
      : { kind: 'native', path: result.selected_root }
  }
  const browser = window as DirectoryPickerWindow
  if (!browser.showDirectoryPicker) throw new ProjectDirectoryError('PICKER_UNAVAILABLE')
  try {
    const handle = await browser.showDirectoryPicker({
      id: 'neko-plugin-parent',
      mode: 'readwrite',
      ...(previous ? { startIn: previous } : {}),
    })
    return { kind: 'browser', handle }
  } catch (error) {
    if ((error as DOMException).name === 'AbortError') return null
    throw error
  }
}

export async function ensureDirectoryWritable(handle: FileSystemDirectoryHandle): Promise<void> {
  const writable = handle as FileSystemDirectoryHandle & {
    requestPermission: (options: { mode: 'readwrite' }) => Promise<PermissionState>
  }
  if ((await writable.requestPermission({ mode: 'readwrite' })) !== 'granted')
    throw new ProjectDirectoryError('WRITE_DENIED')
}

export async function assertProjectAbsent(parent: FileSystemDirectoryHandle, id: string) {
  try {
    await parent.getDirectoryHandle(id)
  } catch (error) {
    if ((error as DOMException).name === 'NotFoundError') return
    if ((error as DOMException).name !== 'TypeMismatchError') throw error
  }
  throw new ProjectDirectoryError('PROJECT_EXISTS')
}

function relativeParts(path: string): string[] {
  const parts = path.split('/')
  if (parts.some((part) => !part || part === '.' || part === '..' || /[\\:]/.test(part)))
    throw new ProjectDirectoryError('INVALID_BUNDLE')
  return parts
}

async function directory(root: FileSystemDirectoryHandle, parts: string[]) {
  let current = root
  for (const part of parts) current = await current.getDirectoryHandle(part, { create: true })
  return current
}

export async function writeProjectBundle(
  parent: FileSystemDirectoryHandle,
  bundle: PluginProjectBundle
): Promise<void> {
  if (!/^[a-z][a-z0-9_]*$/.test(bundle.plugin_id)) throw new ProjectDirectoryError('INVALID_BUNDLE')
  // Validate and decode before creating anything in the selected directory.
  const directories = bundle.directories.map(relativeParts)
  const files = bundle.contents.map((file) => ({
    parts: relativeParts(file.path),
    bytes: Uint8Array.from(atob(file.content_base64), (char) => char.charCodeAt(0)),
  }))
  await assertProjectAbsent(parent, bundle.plugin_id)
  const root = await parent.getDirectoryHandle(bundle.plugin_id, { create: true })
  try {
    for (const parts of directories) await directory(root, parts)
    for (const { parts, bytes } of files) {
      const folder = await directory(root, parts.slice(0, -1))
      const file = await folder.getFileHandle(parts.at(-1)!, { create: true })
      const writer = await file.createWritable()
      try {
        await writer.write(bytes)
        await writer.close()
      } catch (error) {
        await writer.abort().catch(() => {})
        throw error
      }
    }
  } catch (error) {
    // Retain partial output so an interrupted write cannot remove user files.
    throw new ProjectDirectoryError('PROJECT_WRITE_FAILED', String(error))
  }
}
