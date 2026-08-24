@echo off
rem Double-click this once to put an "AI Dungeon Master" icon on your Desktop.
rem The shortcut points back here, so don't move this folder afterwards - or just
rem run this again if you do.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1"
pause
