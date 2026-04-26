@echo off
setlocal
pushd "%~dp0"
"..\03-tools\pdf_tools\.venv\Scripts\python.exe" ".\sync_existing_packages.py" --config ".\config.json"
popd
