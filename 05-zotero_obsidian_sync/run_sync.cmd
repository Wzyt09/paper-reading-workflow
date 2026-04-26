@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul 2>nul
if errorlevel 1 (
  echo Failed to access "%SCRIPT_DIR%"
  exit /b 1
)

set "SCRIPT_DIR=%CD%"
set "REPO_ROOT=%SCRIPT_DIR%\.."
set "PYTHON=%REPO_ROOT%\03-tools\pdf_tools\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo Python not found at "%PYTHON%"
  popd
  exit /b 1
)

"%PYTHON%" ".\sync_pipeline.py" --config ".\config.json" %*
set "ERR=%ERRORLEVEL%"
popd
exit /b %ERR%
