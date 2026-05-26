$ErrorActionPreference = "Continue"

$ports = @(48911, 3001, 5173)
$windowTitles = @(
  "N.E.K.O Main Server - 48911",
  "Neko Battle Arena Match Server - 3001",
  "Neko Battle Arena Frontend - 5173"
)

foreach ($port in $ports) {
  $connections = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
  if (-not $connections) {
    Write-Host ("[skip] Port {0} is not listening." -f $port)
    continue
  }

  $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($processId in $processIds) {
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
