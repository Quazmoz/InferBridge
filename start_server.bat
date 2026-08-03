@echo off
setlocal enabledelayedexpansion
REM ============================================
REM   InferBridge - Server Launcher
REM ============================================
REM Activates the local venv and starts the server. All arguments are passed
REM straight through to the Python CLI.
REM
REM Usage:
REM   start_server.bat                              Auto-load OV_LLM_MODEL (or none)
REM   start_server.bat --model tinyllama-1.1b-chat-fp16 --device NPU
REM   start_server.bat --model qwen2.5-1.5b-fp16 --device NPU
REM   start_server.bat --list                       List catalog models and exit
REM   start_server.bat --check-devices              Show OpenVINO devices and exit
REM   start_server.bat --port 8001
REM   start_server.bat --mock                       Force the mock engine (no OpenVINO)

echo ========================================
echo   InferBridge
echo ========================================
echo.

set "VENV=%~dp0.venv"
if not exist "%VENV%\Scripts\activate.bat" (
    echo ERROR: virtual environment not found at "%VENV%".
    echo Run setup first:
    echo   .\setup.bat
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM Keep the source package importable from arbitrary working directories. This is
REM required by Windows startup registration and by direct console-script usage.
set "PROJECT_FILE=%~dp0pyproject.toml"
set "PROJECT_MARKER=%~dp0.source_package_installed"
set "PROJECT_CURRENT="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$r=$env:PROJECT_FILE; $m=$env:PROJECT_MARKER; if (-not (Test-Path -LiteralPath $r) -or -not (Test-Path -LiteralPath $m)) { exit 1 }; $actual=(Get-FileHash -LiteralPath $r -Algorithm SHA256).Hash.Trim(); $saved=(Get-Content -LiteralPath $m -Raw).Trim(); if ($actual -ceq $saved) { exit 0 }; exit 1" >nul 2>&1
if not errorlevel 1 set "PROJECT_CURRENT=1"

if not defined PROJECT_CURRENT (
    echo Registering the InferBridge source package...
    python -m pip install --no-deps --editable "%~dp0"
    if errorlevel 1 (
        echo ERROR: Failed to register the InferBridge source package.
        pause
        exit /b 1
    )
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$hash=(Get-FileHash -LiteralPath $env:PROJECT_FILE -Algorithm SHA256).Hash; Set-Content -LiteralPath $env:PROJECT_MARKER -Value $hash -NoNewline -Encoding ascii"
    if errorlevel 1 (
        echo WARNING: Source package registered, but its version marker could not be updated.
        echo          Startup may register it again next time.
    )
)

REM Reinstall runtime dependencies only when requirements.txt changed. The old
REM boolean marker could leave upgraded checkouts missing newly declared packages.
set "REQ_FILE=%~dp0requirements.txt"
set "DEPS_MARKER=%~dp0.deps_installed"
set "DEPS_CURRENT="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$r=$env:REQ_FILE; $m=$env:DEPS_MARKER; if (-not (Test-Path -LiteralPath $r) -or -not (Test-Path -LiteralPath $m)) { exit 1 }; $actual=(Get-FileHash -LiteralPath $r -Algorithm SHA256).Hash.Trim(); $saved=(Get-Content -LiteralPath $m -Raw).Trim(); if ($actual -ceq $saved) { exit 0 }; exit 1" >nul 2>&1
if not errorlevel 1 set "DEPS_CURRENT=1"

if not defined DEPS_CURRENT (
    echo Installing updated runtime dependencies...
    python -m pip install -r "%REQ_FILE%"
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$hash=(Get-FileHash -LiteralPath $env:REQ_FILE -Algorithm SHA256).Hash; Set-Content -LiteralPath $env:DEPS_MARKER -Value $hash -NoNewline -Encoding ascii"
    if errorlevel 1 (
        echo WARNING: Dependencies installed, but their version marker could not be updated.
        echo          Startup may check them again next time.
    )
)

REM Keep conversion dependencies aligned with the source checkout only when this
REM environment includes the conversion toolchain. Minimal setups remain minimal.
set "CONVERT_REQ_FILE=%~dp0requirements-convert.txt"
set "CONVERT_DEPS_MARKER=%~dp0.convert_deps_installed"
set "CONVERT_PROFILE="
if exist "%CONVERT_DEPS_MARKER%" set "CONVERT_PROFILE=1"
if not defined CONVERT_PROFILE (
    python -m pip show optimum-intel >nul 2>&1
    if not errorlevel 1 set "CONVERT_PROFILE=1"
)

if defined CONVERT_PROFILE (
    if not exist "%CONVERT_REQ_FILE%" (
        echo ERROR: conversion requirements not found at "%CONVERT_REQ_FILE%".
        pause
        exit /b 1
    )

    set "CONVERT_DEPS_CURRENT="
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$r=$env:CONVERT_REQ_FILE; $m=$env:CONVERT_DEPS_MARKER; if (-not (Test-Path -LiteralPath $r) -or -not (Test-Path -LiteralPath $m)) { exit 1 }; $actual=(Get-FileHash -LiteralPath $r -Algorithm SHA256).Hash.Trim(); $saved=(Get-Content -LiteralPath $m -Raw).Trim(); if ($actual -ceq $saved) { exit 0 }; exit 1" >nul 2>&1
    if not errorlevel 1 set "CONVERT_DEPS_CURRENT=1"

    if not defined CONVERT_DEPS_CURRENT (
        echo Installing updated model conversion dependencies...
        python -m pip install -r "%CONVERT_REQ_FILE%"
        if errorlevel 1 (
            echo ERROR: Failed to install model conversion dependencies.
            pause
            exit /b 1
        )
        "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$hash=(Get-FileHash -LiteralPath $env:CONVERT_REQ_FILE -Algorithm SHA256).Hash; Set-Content -LiteralPath $env:CONVERT_DEPS_MARKER -Value $hash -NoNewline -Encoding ascii"
        if errorlevel 1 (
            echo WARNING: Conversion dependencies installed, but their version marker could not be updated.
            echo          Startup may check them again next time.
        )
    )
)

cd /d "%~dp0"
python -m app.server %*
exit /b %errorlevel%