$ErrorActionPreference = "Continue"

function ConvertTo-ValidPort {
  param([object]$Value)
  $parsed = 0
  if ([int]::TryParse([string]$Value, [ref]$parsed) -and $parsed -ge 1 -and $parsed -le 65535) {
    return $parsed
  }
  return $null
}

function Get-DesktopMainServerPort {
  foreach ($value in @($env:NEKO_MAIN_SERVER_PORT, $env:MAIN_SERVER_PORT)) {
    $port = ConvertTo-ValidPort -Value $value
    if ($null -ne $port) { return $port }
  }

  $appDataRoot = if ($env:APPDATA) {
    $env:APPDATA
  } else {
    Join-Path $HOME "AppData\Roaming"
  }
  $configPath = Join-Path $appDataRoot "N.E.K.O\port_config.json"
  try {
    $config = Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop | ConvertFrom-Json
    $port = ConvertTo-ValidPort -Value $config.MAIN_SERVER_PORT
    if ($null -ne $port) { return $port }
  } catch {
    Write-Verbose ("Could not read main-server port config at {0}: {1}" -f $configPath, $_.Exception.Message)
  }
  return 48911
}

function Get-DesktopCardForgePort {
  foreach ($value in @($env:NEKO_CARD_FORGE_PORT, $env:CARD_FORGE_PORT)) {
    $port = ConvertTo-ValidPort -Value $value
    if ($null -ne $port) { return $port }
  }

  $appDataRoot = if ($env:APPDATA) {
    $env:APPDATA
  } else {
    Join-Path $HOME "AppData\Roaming"
  }
  $configPath = Join-Path $appDataRoot "N.E.K.O\port_config.json"
  try {
    $config = Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop | ConvertFrom-Json
    $port = ConvertTo-ValidPort -Value $config.CARD_FORGE_PORT
    if ($null -ne $port) { return $port }
  } catch {
    Write-Verbose ("Could not read Card Forge port config at {0}: {1}" -f $configPath, $_.Exception.Message)
  }
  return 3001
}

$mainServerPort = Get-DesktopMainServerPort
$cardForgePort = Get-DesktopCardForgePort
$ports = @($mainServerPort, $cardForgePort, 5173) | Select-Object -Unique
$windowTitles = @(
  ("N.E.K.O Main Server - {0}" -f $mainServerPort),
  ("Neko Card Forge Server - {0}" -f $cardForgePort),
  "Neko Card Forge Frontend - 5173"
)

function Get-CardForgeProcessIds {
  param([System.Diagnostics.Process[]]$LauncherProcesses)

  $owned = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($proc in $LauncherProcesses) {
    [void]$owned.Add([int]$proc.Id)
  }
  try {
    $snapshot = @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)
  } catch {
    Write-Verbose ("Could not inspect Card Forge process tree: {0}" -f $_.Exception.Message)
    return ,$owned
  }

  do {
    $added = $false
    foreach ($proc in $snapshot) {
      if ($owned.Contains([int]$proc.ParentProcessId) -and $owned.Add([int]$proc.ProcessId)) {
        $added = $true
      }
    }
  } while ($added)

  return ,$owned
}

$windowProcesses = @(
  Get-Process cmd,powershell -ErrorAction SilentlyContinue |
    Where-Object { $windowTitles -contains $_.MainWindowTitle }
)
$ownedProcessIds = Get-CardForgeProcessIds -LauncherProcesses $windowProcesses

foreach ($port in $ports) {
  $connections = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
  if (-not $connections) {
    Write-Host ("[skip] Port {0} is not listening." -f $port)
    continue
  }

  $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    if (-not $ownedProcessIds.Contains([int]$processId)) {
      Write-Host ("[skip] Port {0} PID {1} is not owned by a Card Forge launch window; leaving it alone." -f $port, $processId)
      continue
    }
    try {
      $proc = Get-Process -Id $processId -ErrorAction Stop
      Write-Host ("[stop] Port {0}: {1} ({2})" -f $port, $proc.ProcessName, $processId)
      Stop-Process -Id $processId -Force -ErrorAction Stop
    } catch {
      Write-Host ("[warn] Could not stop process {0} for port {1}: {2}" -f $processId, $port, $_.Exception.Message)
    }
  }
}

Start-Sleep -Milliseconds 500

foreach ($proc in $windowProcesses) {
  try {
    Write-Host ("[close] Window: {0} ({1})" -f $proc.MainWindowTitle, $proc.Id)
    Stop-Process -Id $proc.Id -Force -ErrorAction Stop
  } catch {
    Write-Host ("[warn] Could not close window {0}: {1}" -f $proc.Id, $_.Exception.Message)
  }
}

foreach ($port in $ports) {
  $stillListening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
  if ($stillListening) {
    Write-Host ("[warn] Port {0} is still listening." -f $port)
  } else {
    Write-Host ("[ok] Port {0} is stopped." -f $port)
  }
}
