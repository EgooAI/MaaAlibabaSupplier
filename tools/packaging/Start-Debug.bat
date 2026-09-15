@echo off
rem Debug launcher: starts the backend with a visible console.
rem Frontend, MaaFW agent and Yak MITM are all served/managed by this process.
cd /d "%~dp0"
"backend\python\python.exe" -m backend.app.main
pause
