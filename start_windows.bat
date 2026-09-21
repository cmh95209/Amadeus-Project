@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"

rem ---- Amadeus Python -------------------------------------------------------
rem Locate Amadeus's Python (its conda environment) without hardcoding a
rem profile path. The launcher is run with THIS python directly instead of
rem `conda run -n amadeus python`: without the conda middleman, Ctrl+C is
rem handled cleanly by the launcher (no "CondaError: KeyboardInterrupt" and no
rem "Terminate batch job (Y/N)?" prompt on exit). GPT-SoVITS still starts
rem through `conda run -n GPTSoVITS` from inside the launcher, unchanged.
set "AMADEUS_PY="
if exist "%USERPROFILE%\miniconda3\envs\amadeus\python.exe" set "AMADEUS_PY=%USERPROFILE%\miniconda3\envs\amadeus\python.exe"
if not defined AMADEUS_PY if exist "%USERPROFILE%\anaconda3\envs\amadeus\python.exe" set "AMADEUS_PY=%USERPROFILE%\anaconda3\envs\amadeus\python.exe"
if not defined AMADEUS_PY if exist "%LOCALAPPDATA%\miniconda3\envs\amadeus\python.exe" set "AMADEUS_PY=%LOCALAPPDATA%\miniconda3\envs\amadeus\python.exe"
if not defined AMADEUS_PY if exist "%LOCALAPPDATA%\anaconda3\envs\amadeus\python.exe" set "AMADEUS_PY=%LOCALAPPDATA%\anaconda3\envs\amadeus\python.exe"
if not defined AMADEUS_PY if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "AMADEUS_PY=%CONDA_PREFIX%\python.exe"
if defined AMADEUS_PY goto :found_python

echo ERROR: Amadeus's Python (conda env "amadeus") could not be found.
echo Install it with scripts\install_windows.ps1 (or: conda env create -f
echo backend\environment.yml -n amadeus), then try again.
pause
exit /b 1
:found_python

rem ---- WebUI fix (added by your helper) ------------------------------------
rem Windows is set to block PowerShell from running local scripts, which stops
rem the web interface (vite) from starting. These two lines fix it:
rem   1) tell npm to start vite with cmd.exe instead of PowerShell (instant fix)
rem   2) relax your user's PowerShell script policy as a backup
set "npm_config_script_shell=cmd.exe"
powershell -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned" >nul 2>&1

echo Using Amadeus Python: %AMADEUS_PY%
"%AMADEUS_PY%" "%CD%\scripts\launcher.py"

set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Amadeus launcher exited with an error.
    echo Check .runtime\logs for service logs.
    pause
)

exit /b %EXIT_CODE%
