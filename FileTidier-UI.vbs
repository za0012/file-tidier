Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
appPath = fso.BuildPath(scriptDir, "ui\index.html")
appUrl = "file:///" & Replace(appPath, "\", "/")

shell.Run "msedge.exe --app=""" & appUrl & """", 1, False
