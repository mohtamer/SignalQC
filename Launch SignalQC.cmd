@echo off
cd /d "%~dp0"
if exist "runtime\python\pythonw.exe" (
  start "" "runtime\python\pythonw.exe" -B -E -s "%~dp0app.py" %*
  exit /b 0
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo The included Python runtime is missing. Copy the complete SignalQC_App folder, including runtime.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -B "%~dp0app.py" %*
