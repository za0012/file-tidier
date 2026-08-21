Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
appDir = fso.BuildPath(scriptDir, "electron-app")

shell.CurrentDirectory = appDir
shell.Run "cmd.exe /c pnpm.cmd start", 1, False
