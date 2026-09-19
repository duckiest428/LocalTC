@echo off
rem Installs (or updates) LocalTC: double-click this file. Safe to run again.
rem Options, e.g. from a console:  "Install LocalTC.cmd" -Quality light -NoOllama
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "install\install.ps1" %*
pause
