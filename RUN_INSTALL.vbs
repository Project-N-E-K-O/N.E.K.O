' Double-click this file to install N.E.K.O env (opens cmd, no encoding issues).
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
bat = dir & "\install_env.bat"
If Not fso.FileExists(bat) Then
  MsgBox "Missing install_env.bat in:" & vbCrLf & dir, vbCritical, "N.E.K.O"
  WScript.Quit 1
End If
' 1 = show window, False = wait until finished
sh.Run "cmd.exe /k cd /d """ & dir & """ && install_env.bat", 1, False
