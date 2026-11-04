@echo off
rem Scheduled entry: collect -> send due archive -> purge expired
cd /d "%~dp0"
"D:\Users\Quach\anaconda3\python.exe" "%~dp0cfip.py" run >> "%~dp0logs\task.log" 2>&1