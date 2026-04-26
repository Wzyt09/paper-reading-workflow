@echo off
setlocal
pushd "%~dp0" || exit /b 1

set "PY=..\03-tools\pdf_tools\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo Refreshing Paper Dashboard ...
"%PY%" ".\generate_dashboard.py"
set "CODE=%ERRORLEVEL%"

echo.
if %CODE% equ 0 (
    echo Dashboard refreshed successfully.
) else (
    echo Dashboard refresh failed with code %CODE%.
)
echo.
pause
popd
exit /b %CODE%
