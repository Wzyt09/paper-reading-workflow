@echo off
setlocal
pushd "%~dp0" || exit /b 1

set "SCRIPT_DIR=%CD%"
set "PY=..\03-tools\pdf_tools\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" ".\sync_existing_packages.py" --config ".\config.json"
set "CODE=%ERRORLEVEL%"

echo.
echo Obsidian papers dir: "%SCRIPT_DIR%\obsidian_vault\papers"
echo State file: "%SCRIPT_DIR%\.state\sync_state.json"
echo.
pause
popd
exit /b %CODE%
