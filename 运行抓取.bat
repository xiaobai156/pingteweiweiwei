@echo off
cd /d "%~dp0"

set /p ISSUE=Input issue number, example 167: 
if "%ISSUE%"=="" (
  echo Issue number cannot be empty.
  pause
  exit /b 1
)

where py >nul 2>nul
if errorlevel 1 (
  python tail_fetcher.py "%ISSUE%"
) else (
  py -3 tail_fetcher.py "%ISSUE%"
)
set EXIT_CODE=%ERRORLEVEL%
echo.
pause
exit /b %EXIT_CODE%
