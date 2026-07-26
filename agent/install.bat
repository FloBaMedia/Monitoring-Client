@echo off
:: ServerMetry Agent Installer — Windows launcher
:: Double-click this file to install the ServerMetry Agent.
:: It will automatically request administrator privileges.

setlocal EnableExtensions

:: Best-effort: map -ApiUrl / -ApiKey into env vars for elevation forwarding
:parse_args
if "%~1"=="" goto :after_parse
if /I "%~1"=="-ApiUrl" (
  if not "%~2"=="" set "SERVERMETRY_URL=%~2"
  shift
  shift
  goto :parse_args
)
if /I "%~1"=="-ApiKey" (
  if not "%~2"=="" set "SERVERMETRY_KEY=%~2"
  shift
  shift
  goto :parse_args
)
shift
goto :parse_args

:after_parse

:: Check if already running as administrator
net session >nul 2>&1
if %errorlevel% == 0 goto :run

:: Not admin — relaunch elevated; pass URL/key as explicit parameters
:: (UAC-elevated processes do not reliably inherit parent env vars)
echo Requesting administrator privileges...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$script = Join-Path '%~dp0' 'install-windows.ps1';" ^
  "$argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$script);" ^
  "if ($env:SERVERMETRY_URL) { $argList += @('-ApiUrl', $env:SERVERMETRY_URL) };" ^
  "if ($env:SERVERMETRY_KEY) { $argList += @('-ApiKey', $env:SERVERMETRY_KEY) };" ^
  "Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argList"
exit /b 0

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1" %*
