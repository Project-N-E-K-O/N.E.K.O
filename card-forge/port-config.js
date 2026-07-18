import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

function validPort(value) {
  const port = Number(String(value ?? '').trim())
  return Number.isInteger(port) && port >= 1 && port <= 65535 ? port : null
}

function portConfigPath({ env, platform, homeDir }) {
  let base
  if (platform === 'win32') {
    base = env.APPDATA || path.join(homeDir, 'AppData', 'Roaming')
  } else if (platform === 'darwin') {
    base = path.join(homeDir, 'Library', 'Application Support')
  } else {
    base = env.XDG_CONFIG_HOME || path.join(homeDir, '.config')
  }
  return path.join(base, 'N.E.K.O', 'port_config.json')
}

export function resolveConfiguredPort(
  portName,
  defaultPort,
  {
    env = process.env,
    platform = process.platform,
    homeDir = os.homedir(),
    readFileSync = fs.readFileSync,
  } = {},
) {
  for (const key of [`NEKO_${portName}`, portName]) {
    const port = validPort(env[key])
    if (port !== null) return port
  }

  try {
    const config = JSON.parse(
      readFileSync(portConfigPath({ env, platform, homeDir }), 'utf8'),
    )
    const port = validPort(config?.[portName])
    if (port !== null) return port
  } catch {
    // Missing or malformed desktop config falls back to the source default.
  }
  return defaultPort
}
