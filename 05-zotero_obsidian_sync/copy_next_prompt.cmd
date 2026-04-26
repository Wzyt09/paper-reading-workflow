@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul 2>nul
if errorlevel 1 (
  echo Failed to access "%SCRIPT_DIR%"
  exit /b 1
)

set "SCRIPT_DIR=%CD%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\copy_next_prompt.ps1" %*
set "ERR=%ERRORLEVEL%"
popd
exit /b %ERR%
