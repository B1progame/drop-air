@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv || goto :error
)

echo Installing/updating dependencies...
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error

echo Starting Drop Air...
call ".venv\Scripts\python.exe" app.py
goto :eof

:error
echo.
echo Failed to start Drop Air.
pause
exit /b 1
