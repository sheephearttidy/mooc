@echo off
chcp 65001 >nul
rem 只结束命令行里带 moocpy 或 main.py 的 Python 进程，避免误杀其它 Python 程序
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^pythonw?\.exe$' -and ($_.CommandLine -like '*moocpy*' -or $_.CommandLine -like '*main.py*') }; if ($p) { $p | ForEach-Object { Write-Host ('结束进程 ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force } } else { Write-Host '没有正在运行的 moocpy 进程' }"
pause
