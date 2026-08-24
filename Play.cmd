@echo off
rem Double-click this to play. It starts the game and opens your browser.
rem Closing this window stops the game.
title AI Dungeon Master
cd /d "%~dp0"

rem Find a Python that actually runs. Don't just check the command exists: `py -3` can
rem point at a registered install whose files are gone, and `python` on a clean Windows
rem is often the Store stub that opens a shop page. So ask each candidate its version
rem and take the first that answers 3.10 or newer.
set "PY="
call :try python
if not defined PY call :try py -3
if not defined PY call :try py

if not defined PY (
  echo.
  echo   No working Python 3.10+ was found.
  echo.
  echo   Get it from https://python.org/downloads and tick
  echo   "Add Python to PATH" in the installer, then run this again.
  echo.
  pause
  exit /b 1
)

%PY% launch.py %*
exit /b %errorlevel%

:try
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=%*"
goto :eof
