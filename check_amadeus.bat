@echo off
rem ---------------------------------------------------------------------
rem  Amadeus health check
rem
rem  Double-click this to run Amadeus's backend self-tests. It does NOT
rem  need a model loaded or a model server running - it tests the code
rem  with a built-in stand-in. You only need a model to actually chat.
rem
rem  It reports a plain-English pass/fail at the end:
rem    [OK]      All checks passed - Amadeus is healthy.
rem    [PROBLEM] Some checks failed - show the lines above for help.
rem ---------------------------------------------------------------------
setlocal
rem Find this folder (wherever the project is on this computer).
set "BACKEND_DIR=%~dp0backend"

rem Locate Amadeus's Python (its conda environment).
set "PY=C:\Users\xlhhm\miniconda3\envs\amadeus\python.exe"
if not exist "%PY%" (
  echo [PROBLEM] Could not find the Amadeus Python at:
  echo           %PY%
  echo           Check the PY line in check_amadeus.bat and point it at
  echo           your Amadeus conda environment's python.exe.
  pause
  exit /b 1
)

echo ================================================
echo  Amadeus health check
echo ================================================
echo.
cd /d "%BACKEND_DIR%"
"%PY%" -m unittest discover -s tests
echo.
if %ERRORLEVEL% EQU 0 (
  echo  [OK]      All checks passed - Amadeus is healthy.
) else (
  echo  [PROBLEM] Some checks failed - show the lines above to whoever helps with Amadeus.
)
echo.
pause
