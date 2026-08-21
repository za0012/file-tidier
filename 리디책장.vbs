Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
appDir = fso.BuildPath(scriptDir, "ridi-bookshelf")
electronExe = fso.BuildPath(appDir, "node_modules\.pnpm\electron@31.7.7\node_modules\electron\dist\electron.exe")

If Not fso.FolderExists(appDir) Then
  MsgBox "ridi-bookshelf 폴더를 찾을 수 없습니다." & vbCrLf & appDir, vbCritical, "리디 책장"
  WScript.Quit 1
End If

If Not fso.FileExists(electronExe) Then
  MsgBox "Electron 실행 파일을 찾을 수 없습니다." & vbCrLf & "Git Bash에서 pnpm install 후 다시 실행해주세요.", vbCritical, "리디 책장"
  WScript.Quit 1
End If

shell.CurrentDirectory = appDir
command = "cmd.exe /c start """" " & Chr(34) & electronExe & Chr(34) & " " & Chr(34) & appDir & Chr(34)
shell.Run command, 0, False
