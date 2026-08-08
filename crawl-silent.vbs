' Runs crawl-hourly.cmd with NO visible window (Task Scheduler points here).
' Window style 0 = hidden; True = wait for completion.
Set sh = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir
sh.Run """" & scriptDir & "\crawl-hourly.cmd""", 0, True
