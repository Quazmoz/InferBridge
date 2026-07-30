@echo off
REM ============================================
REM  InferBridge - First-Time Setup
REM ============================================
REM Creates a Python venv, installs OpenVINO GenAI + server deps, and (optionally)
REM the model-conversion deps. Runs the PowerShell setup with execution-policy
REM bypass so no manual policy change is needed.
REM
REM   .\setup.bat                 Install runtime & model-conversion deps
REM   .\setup.bat -Minimal        Install runtime deps only (skip conversion tools)
REM   .\setup.bat -SkipHardwareCheck

echo ==========================================
echo   InferBridge - Setup
echo ==========================================
echo.

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\windows\setup_all.ps1" %*

if errorlevel 1 (
    echo.
    echo Setup did not complete successfully.
    echo Review the messages above, fix any errors, then run setup.bat again.
    pause
    exit /b 1
)

pause
