@echo off
rem ==========================================================================
rem  Update Amadeus - double-click this file to update Amadeus to the latest
rem  version. No administrator needed.
rem
rem  It downloads the latest Amadeus installer from GitHub into this same
rem  folder (install_windows.ps1, next to this file) and runs it in this
rem  window. The installer keeps everything already installed, adds only
rem  what is new, and keeps your own files: your chats and her memories,
rem  your saved connection, and her personality if you edited it.
rem  (An install made with a pinned installer for one version moves to the
rem  latest version too.) Close Amadeus before updating.
rem
rem  "Install Amadeus.bat" is the same installer, for setting up a new PC.
rem
rem  Advanced (from a command prompt):
rem    "Update Amadeus.bat" <branch> [installer options]
rem ==========================================================================
setlocal
title Amadeus update
if not exist "%~dp0backend\main.py" (
    echo This file updates the Amadeus copy it sits in, but this folder does
    echo not hold Amadeus. To install Amadeus on a new PC, use
    echo "Install Amadeus.bat" instead.
    echo.
    pause
    exit /b 1
)
rem The folder ABOVE this one holds Amadeus-Project: the installer updates
rem this copy wherever it was installed.
for %%I in ("%~dp0..") do set "AMADEUS_HOME=%%~fI"
set "AMADEUS_BRANCH=main"
if not "%~1"=="" set "AMADEUS_BRANCH=%~1"
set AMADEUS_OPTS=%2 %3 %4 %5 %6 %7 %8 %9
set "AMADEUS_PS1=%~dp0install_windows.ps1"
set "AMADEUS_URL=https://raw.githubusercontent.com/cmh95209/Amadeus-Project/%AMADEUS_BRANCH%/scripts/install_windows.ps1"

echo Downloading the latest Amadeus installer to update Amadeus (branch %AMADEUS_BRANCH%)...
powershell -NoProfile -ExecutionPolicy RemoteSigned -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri $env:AMADEUS_URL -OutFile $env:AMADEUS_PS1; exit 0 } catch { Write-Host ('  ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo.
    echo Could not download the installer. Check your internet connection,
    echo then double-click this file again.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%AMADEUS_PS1%" -Branch "%AMADEUS_BRANCH%" -InstallDir "%AMADEUS_HOME%" %AMADEUS_OPTS%
