' N.E.K.O silent desktop launcher (kept in repo root).
' Desktop only has a .lnk -> wscript.exe //Nologo "<this file>"
Option Explicit
Dim sh, fso, dir, exe, py, clearPy, postBat, logDir, logFile, postLog

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
exe = dir & "\desktop_release\N.E.K.O.exe"
py = dir & "\.venv\Scripts\python.exe"
clearPy = dir & "\scripts\clear_stale_neko_runtime.py"
postBat = dir & "\scripts\_silent_post_start.bat"
logDir = dir & "\logs"
logFile = logDir & "\start_desktop_silent.log"
postLog = logDir & "\start_desktop_post.log"

If Not fso.FileExists(exe) Then
  MsgBox "Missing desktop_release\N.E.K.O.exe" & vbCrLf & exe, vbCritical, "N.E.K.O"
  WScript.Quit 1
End If
If Not fso.FileExists(py) Then
  MsgBox "Missing .venv\Scripts\python.exe", vbCritical, "N.E.K.O"
  WScript.Quit 1
End If

If Not fso.FolderExists(logDir) Then
  On Error Resume Next
  fso.CreateFolder logDir
  On Error GoTo 0
End If

ResetLog logFile
AppendLog logFile, "==== silent start (vbs) " & Now & " ===="
AppendLog logFile, "NEKO_SRC_ROOT=" & dir

sh.CurrentDirectory = dir
On Error Resume Next
sh.Environment("PROCESS")("NEKO_SRC_ROOT") = dir
If Trim(sh.Environment("PROCESS")("HF_ENDPOINT")) = "" Then sh.Environment("PROCESS")("HF_ENDPOINT") = "https://hf-mirror.com"
If Trim(sh.Environment("PROCESS")("NEKO_WHISPER_DEVICE")) = "" Then sh.Environment("PROCESS")("NEKO_WHISPER_DEVICE") = "cuda"
If Trim(sh.Environment("PROCESS")("NEKO_WHISPER_MODEL")) = "" Then sh.Environment("PROCESS")("NEKO_WHISPER_MODEL") = "medium"
On Error GoTo 0

If fso.FileExists(clearPy) Then
  AppendLog logFile, "[0] clear stale runtime"
  sh.Run """" & py & """ """ & clearPy & """ --force", 0, True
End If

' Edge TTS must be up BEFORE Electron; otherwise custom TTS fails open onto
' free/native Japanese voices while the LLM still replies in Chinese.
Dim edgePyw, edgePy, edgeBridge, seedMarker
edgePyw = dir & "\.venv\Scripts\pythonw.exe"
edgePy = dir & "\.venv\Scripts\python.exe"
edgeBridge = dir & "\scripts\edge_tts_bridge.py"
seedMarker = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\N.E.K.O\config\.neko_local_ai_seeded"
If fso.FileExists(seedMarker) And fso.FileExists(edgeBridge) Then
  AppendLog logFile, "[0.5] ensure Edge TTS bridge :19000"
  ' curl exit 0 = already healthy; otherwise start bridge (port-in-use is harmless).
  Dim edgeRc
  edgeRc = sh.Run("cmd.exe /c curl -s -m 1 http://127.0.0.1:19000/health >nul 2>&1", 0, True)
  If edgeRc <> 0 Then
    If fso.FileExists(edgePyw) Then
      sh.Run """" & edgePyw & """ """ & edgeBridge & """ --port 19000", 0, False
    Else
      sh.Run """" & edgePy & """ """ & edgeBridge & """ --port 19000", 0, False
    End If
    WScript.Sleep 1500
    AppendLog logFile, "[0.5] Edge TTS bridge start requested"
  Else
    AppendLog logFile, "[0.5] Edge TTS bridge already up"
  End If
End If

AppendLog logFile, "[1] Starting Electron..."
sh.Run """" & exe & """", 1, False
AppendLog logFile, "[OK] N.E.K.O.exe launch requested"

If fso.FileExists(postBat) Then
  AppendLog logFile, "[post] spawn _silent_post_start.bat -> start_desktop_post.log"
  ' Quote-safe: /c with one quoted command string, then redirect the whole cmd.
  sh.Run "cmd.exe /c call """ & postBat & """ > """ & postLog & """ 2>&1", 0, False
End If

WScript.Quit 0

Sub ResetLog(path)
  Dim f
  On Error Resume Next
  Set f = fso.CreateTextFile(path, True)
  If Err.Number = 0 Then
    f.Close
  End If
  Err.Clear
  On Error GoTo 0
End Sub

Sub AppendLog(path, line)
  Dim f
  On Error Resume Next
  Set f = fso.OpenTextFile(path, 8, True)
  If Err.Number = 0 Then
    f.WriteLine line
    f.Close
  End If
  Err.Clear
  On Error GoTo 0
End Sub
