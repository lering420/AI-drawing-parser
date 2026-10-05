@echo off
title AI Drawing Inspector
cd /d "%~dp0"

set "PY=%~dp0runtime\python.exe"
if exist "%PY%" goto run
where python >nul 2>nul
if errorlevel 1 goto nopy
set "PY=python"

:run
echo Starting AI Drawing Inspector, browser will open automatically...
"%PY%" server.py
if errorlevel 1 (
  echo.
  echo Failed to start. See error message above.
  pause
)
goto :eof

:nopy
echo [ERROR] Python runtime not found.
echo Please make sure the "runtime" folder is complete,
echo or install Python 3.10+ and add it to PATH.
pause
