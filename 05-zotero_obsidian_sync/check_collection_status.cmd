@echo off
setlocal
pushd "%~dp0" || exit /b 1

set "SCRIPT_DIR=%CD%"
set "PY=..\03-tools\pdf_tools\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" ".\report_collection_status.py" --config ".\config.json" --open-report
set "CODE=%ERRORLEVEL%"

echo.
echo Report path: "%SCRIPT_DIR%\.state\reports\collection-status-latest.md"
echo.
pause
popd
exit /b %CODE%
