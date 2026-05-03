@echo off
:: ServerPulse Agent Installer — Windows launcher
:: Double-click this file to install the ServerPulse Agent.
:: It will automatically request administrator privileges.

setlocal

:: Check if already running as administrator
net session >nul 2>&1
if %errorlevel% == 0 goto :run

:: Not admin — relaunch with UAC prompt
echo Requesting administrator privileges...
powershell -Command "Start-Process -FilePath 'powershell.exe' -ArgumentList '-ExecutionPolicy Bypass -File ""%~dp0install-windows.ps1""' -Verb RunAs"
exit /b 0

:run
powershell -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
