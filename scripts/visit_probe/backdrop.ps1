# Deterministic backdrop window for the visit T3/T4 screenshots (not product code).
# Shows a borderless, topmost (but below the Pet's screen-saver level) window with a fixed pattern
# at the given physical-pixel rect, so screenshots never depend on (or capture) the user's own windows.
# usage: powershell -NoProfile -ExecutionPolicy Bypass -File backdrop.ps1 X Y W H
param([int]$X, [int]$Y, [int]$W, [int]$H)
Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@
[Dpi]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$bmp = New-Object System.Drawing.Bitmap $W, $H
$gfx = [System.Drawing.Graphics]::FromImage($bmp)
$rect = New-Object System.Drawing.Rectangle 0, 0, $W, $H
$grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, ([System.Drawing.Color]::FromArgb(255, 30, 70, 200)), ([System.Drawing.Color]::FromArgb(255, 220, 160, 40)), 35.0
$gfx.FillRectangle($grad, $rect)
$checker = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(70, 255, 255, 255))
for ($yy = 0; $yy -lt $H; $yy += 32) { for ($xx = 0; $xx -lt $W; $xx += 32) { if (((($xx / 32) + ($yy / 32)) % 2) -eq 0) { $gfx.FillRectangle($checker, $xx, $yy, 32, 32) } } }
$gfx.Dispose()
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.ShowInTaskbar = $false
# not TopMost: on Windows every Electron always-on-top level maps to HWND_TOPMOST, so a later TopMost
# window would cover the Pet. A normal window shown last sits above the user's windows but below the Pet.
$form.TopMost = $false
$form.Location = New-Object System.Drawing.Point $X, $Y
$form.Size = New-Object System.Drawing.Size $W, $H
$form.BackgroundImage = $bmp
$form.BackgroundImageLayout = 'None'
$form.Text = 'visit-probe-backdrop'
$form.Add_Shown({ $form.Location = New-Object System.Drawing.Point $X, $Y; $form.Size = New-Object System.Drawing.Size $W, $H })
[System.Windows.Forms.Application]::Run($form)
