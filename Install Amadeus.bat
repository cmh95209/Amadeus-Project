@echo off
rem ==========================================================================
rem  Install Amadeus - double-click this file. No administrator needed.
rem
rem  It downloads the latest Amadeus installer from GitHub and runs it in
rem  this window: the same installer as the PowerShell line in the README.
rem  Safe to run again: the installer continues where it stopped.
rem
rem  Windows may first say "Windows protected your PC" (this file is not
rem  signed by a company): click "More info", then "Run anyway".
rem
rem  Advanced (from a command prompt):
rem    "Install Amadeus.bat" <branch> [installer options]
rem  e.g. "Install Amadeus.bat" main -InstallDir "D:\Amadeus"
rem ==========================================================================
setlocal
title Amadeus installer
set "AMADEUS_BRANCH=main"
if not "%~1"=="" set "AMADEUS_BRANCH=%~1"
set AMADEUS_OPTS=%2 %3 %4 %5 %6 %7 %8 %9
set "AMADEUS_PS1=%TEMP%\amadeus_install_windows.ps1"
set "AMADEUS_URL=https://raw.githubusercontent.com/cmh95209/Amadeus-Project/%AMADEUS_BRANCH%/scripts/install_windows.ps1"

echo Downloading the latest Amadeus installer (branch %AMADEUS_BRANCH%)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri $env:AMADEUS_URL -OutFile $env:AMADEUS_PS1; exit 0 } catch { Write-Host ('  ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo.
    echo Could not download the installer. Check your internet connection,
    echo then double-click this file again.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%AMADEUS_PS1%" -Branch "%AMADEUS_BRANCH%" %AMADEUS_OPTS%
