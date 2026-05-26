$ErrorActionPreference = "Continue"

$ports = @(48911, 3001, 5173)
$windowTitles = @(
  "N.E.K.O Main Server - 48911",
  "Neko Card Forge Server - 3001",
  "Neko Card Forge Frontend - 5173"
)

# Only kill processes whose command line matches a card-forge launcher signature,
# so an unrelated app that happens to be listening on 48911/3001/5173 (e.g.
# another developer's Vite project on 5173) isn't taken down with us.
$cardForgePatterns = @(
  'launcher\.py',
  'card_forge_server',
  'card-forge'
)

function Get-ProcessCommandLine {
  param([int]$ProcId)
  try {
    return (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction Stop).CommandLine
  } catch {
    return ""
  }
}

function Test-CardForgeProcess {
  param([string]$CommandLine)
  if (-not $CommandLine) { return $false }
  foreach ($pattern in $cardForgePatterns) {
    if ($CommandLine -match $pattern) { return $true }
  }
  return $false
}

# 不能在 [skip] 日志里写出被跳过进程的完整 CommandLine —— 那是别人家的进程,
# 参数里可能含 API token、--password=*、私密文件路径等。
# 两步处理:先用正则把常见敏感参数替换成 <redacted>,再截到 60 字符。
# 截短前先脱敏,否则敏感值落在前 60 字内仍会进日志。
$sensitiveParamPatterns = @(
  '(?i)(--?(?:token|password|secret|api[-_]?key|access[-_]?key|auth)\s*[=: ]\s*)\S+',
  '(?i)(Bearer\s+)\S+',
  '(?i)(Authorization\s*[:=]\s*)\S+'
)

function Get-SafeCommandPreview {
  param([string]$CommandLine, [int]$MaxLength = 60)
  if (-not $CommandLine) { return "(unknown)" }
  $sanitized = $CommandLine
  foreach ($pattern in $sensitiveParamPatterns) {
    $sanitized = [regex]::Replace($sanitized, $pattern, '${1}<redacted>')
  }
  $trimmed = $sanitized.Trim()
  if ($trimmed.Length -le $MaxLength) { return $trimmed }
  return $trimmed.Substring(0, $MaxLength) + "…"
}

foreach ($port in $ports) {
  $connections = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
  if (-not $connections) {
    Write-Host ("[skip] Port {0} is not listening." -f $port)
    continue
  }

  $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
    $cmdLine = Get-ProcessCommandLine -ProcId $processId
    if (-not (Test-CardForgeProcess -CommandLine $cmdLine)) {
      $preview = Get-SafeCommandPreview -CommandLine $cmdLine
      Write-Host ("[skip] Port {0} PID {1} does not look like a card-forge process; leaving it alone. Preview: {2}" -f $port, $processId, $preview)
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

$windowProcesses = Get-Process cmd,powershell -ErrorAction SilentlyContinue |
  Where-Object { $windowTitles -contains $_.MainWindowTitle }

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
