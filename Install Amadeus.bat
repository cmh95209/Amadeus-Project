@echo off
rem ==========================================================================
rem  Install Amadeus - double-click this file. No administrator needed.
rem
rem  It downloads the latest Amadeus installer from GitHub into this same
rem  folder (install_windows.ps1, next to this file) and runs it in this
rem  window: the same installer as the PowerShell line in the README.
rem  Safe to run again: the installer continues where it stopped.
rem
rem  Windows may first say "Windows protected your PC" (this file is not
rem  signed by a company): click "More info", then "Run anyway".
rem
rem  Advanced (from a command prompt):
rem    "Install Amadeus.bat" <branch> [installer options]
rem  e.g. "Install Amadeus.bat" main -InstallDir "D:\Amadeus"
rem
rem  (2026-10-25: the installer is no longer saved in %TEMP% and runs with
rem  RemoteSigned instead of Bypass - Kaspersky's heuristics deleted the old
rem  version of this file as "Trojan.BAT.Alien.gen", a false positive.)
rem ==========================================================================
setlocal
title Amadeus installer
set "AMADEUS_BRANCH=main"
if not "%~1"=="" set "AMADEUS_BRANCH=%~1"
set AMADEUS_OPTS=%2 %3 %4 %5 %6 %7 %8 %9
set "AMADEUS_PS1=%~dp0install_windows.ps1"
set "AMADEUS_URL=https://raw.githubusercontent.com/cmh95209/Amadeus-Project/%AMADEUS_BRANCH%/scripts/install_windows.ps1"

echo Downloading the latest Amadeus installer (branch %AMADEUS_BRANCH%)...
powershell -NoProfile -ExecutionPolicy RemoteSigned -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri $env:AMADEUS_URL -OutFile $env:AMADEUS_PS1; exit 0 } catch { Write-Host ('  ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo.
    echo Could not download the installer. Check your internet connection,
    echo then double-click this file again. If it still fails, move this file
    echo to a normal folder first ^(for example Downloads^) - not inside a zip.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%AMADEUS_PS1%" -Branch "%AMADEUS_BRANCH%" %AMADEUS_OPTS%
