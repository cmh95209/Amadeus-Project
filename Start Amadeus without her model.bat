@echo off
rem Starts Amadeus WITHOUT loading her built-in model - for when a game or
rem another program needs the graphics memory. Everything else starts as
rem usual; load her model later in Settings -> Connection.
call "%~dp0start_windows.bat" --no-model %*
