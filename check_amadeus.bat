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

rem Locate Amadeus's Python (its conda environment) without hardcoding a
rem user profile: try the usual install roots, then the active env.
set "PY="
if exist "%USERPROFILE%\miniconda3\envs\amadeus\python.exe" set "PY=%USERPROFILE%\miniconda3\envs\amadeus\python.exe"
if not defined PY if exist "%USERPROFILE%\anaconda3\envs\amadeus\python.exe" set "PY=%USERPROFILE%\anaconda3\envs\amadeus\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\miniconda3\envs\amadeus\python.exe" set "PY=%LOCALAPPDATA%\miniconda3\envs\amadeus\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\anaconda3\envs\amadeus\python.exe" set "PY=%LOCALAPPDATA%\anaconda3\envs\amadeus\python.exe"
if not defined PY if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PY=%CONDA_PREFIX%\python.exe"
if not defined PY (
  echo [PROBLEM] Could not find a Python environment named "amadeus" in the
  echo           usual conda install locations.
  echo           Edit the detection block in check_amadeus.bat and point
  echo           PY at your Amadeus conda environment's python.exe.
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
