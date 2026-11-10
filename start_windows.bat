@echo off
setlocal EnableExtensions DisableDelayedExpansion

rem The Amadeus help channel (one constant per script): the error message
rem below ends with this line + the exact folder to send.
set "DISCORD_URL=https://discord.gg/YxYkZPSVf3"

cd /d "%~dp0"

rem ---- Amadeus Python -------------------------------------------------------
rem Locate Amadeus's Python (its conda environment) without hardcoding a
rem profile path. The launcher is run with THIS python directly instead of
rem `conda run -n amadeus python`: without the conda middleman, Ctrl+C is
rem handled cleanly by the launcher (no "CondaError: KeyboardInterrupt" and no
rem "Terminate batch job (Y/N)?" prompt on exit). GPT-SoVITS still starts
rem through `conda run -n GPTSoVITS` from inside the launcher, unchanged.
rem An install made with the installer's -EnvSuffix option has its own
rem environments (e.g. "amadeus-test"); the suffix is recorded here. Such an
rem install never falls back to the active conda env, which could belong to
rem another install on this PC.
set "ENV_SUFFIX="
if exist ".runtime\env_suffix.txt" set /p ENV_SUFFIX=<".runtime\env_suffix.txt"
set "AMADEUS_ENV=amadeus%ENV_SUFFIX%"
set "AMADEUS_PY="
rem 1) Where the installer (v4.13.3+) recorded it - no guessing.
set "RECORDED_PY="
if exist ".runtime\app_python.txt" set /p RECORDED_PY=<".runtime\app_python.txt"
if defined RECORDED_PY if exist "%RECORDED_PY%" set "AMADEUS_PY=%RECORDED_PY%"
rem 2) Every place the installer accepts a Miniconda/Anaconda, plus the
rem    per-user folder conda uses when its own folder is not writable
rem    (an all-users install in C:\ProgramData - 2026-11-08 tester report).
if not defined AMADEUS_PY if exist "%USERPROFILE%\.amadeus-tools\miniconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%USERPROFILE%\.amadeus-tools\miniconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%USERPROFILE%\miniconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%USERPROFILE%\miniconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%USERPROFILE%\anaconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%USERPROFILE%\anaconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%LOCALAPPDATA%\miniconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%LOCALAPPDATA%\miniconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%LOCALAPPDATA%\anaconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%LOCALAPPDATA%\anaconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%LOCALAPPDATA%\Continuum\miniconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%LOCALAPPDATA%\Continuum\miniconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%ProgramData%\miniconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%ProgramData%\miniconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%ProgramData%\anaconda3\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%ProgramData%\anaconda3\envs\%AMADEUS_ENV%\python.exe"
if not defined AMADEUS_PY if exist "%USERPROFILE%\.conda\envs\%AMADEUS_ENV%\python.exe" set "AMADEUS_PY=%USERPROFILE%\.conda\envs\%AMADEUS_ENV%\python.exe"
if not defined ENV_SUFFIX if not defined AMADEUS_PY if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "AMADEUS_PY=%CONDA_PREFIX%\python.exe"
if defined AMADEUS_PY goto :found_python

echo ERROR: Amadeus's Python (conda env "%AMADEUS_ENV%") could not be found.
echo Most often the install did not finish. Run the Amadeus installer again
echo (Install Amadeus.bat) - it continues where it stopped.
echo If this message comes back, send your helper the file install_log.txt
echo from the folder that holds Amadeus-Project.
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

"%AMADEUS_PY%" "%CD%\scripts\launcher.py" %*

set "EXIT_CODE=%errorlevel%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo Amadeus launcher exited with an error.
    echo The details are in the logs folder: %CD%\logs
    echo Need help? Ask on our Discord: %DISCORD_URL% - and send the logs folder %CD%\logs.
    pause
)

exit /b %EXIT_CODE%
