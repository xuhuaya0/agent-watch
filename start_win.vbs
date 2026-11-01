' Windows 一键无黑框启动脚本（双击即可在右下角系统托盘驻留）
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "pythonw.exe """ & dir & "\tray.py""", 0, False
