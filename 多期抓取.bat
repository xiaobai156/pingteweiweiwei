@echo off
cd /d "%~dp0"

set /p ISSUES=Input issue numbers, example 187 188 189 190: 
if "%ISSUES%"=="" (
  echo Issue numbers cannot be empty.
  pause
  exit /b 1
)

where py >nul 2>nul
if errorlevel 1 (
  python multi_issue_fetcher.py "%ISSUES%"
) else (
  py -3 multi_issue_fetcher.py "%ISSUES%"
)
set EXIT_CODE=%ERRORLEVEL%
echo.
pause
exit /b %EXIT_CODE%
