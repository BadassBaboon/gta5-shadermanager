@echo off
setlocal enabledelayedexpansion
title GTA 5 Shader Manager - Build Script
color 0A

REM ========================================================
REM  Configuration
REM ========================================================
set "APP_NAME=GTA5ShaderManager"
set "MAIN_SCRIPT=src\main.py"
set "REQUIRED_PACKAGES=pyinstaller ttkbootstrap configparser"

echo ========================================================
echo            GTA 5 SHADER MANAGER - BUILD
echo ========================================================
echo.

REM ========================================================
REM  [0/4] Verify main script exists
REM ========================================================
if not exist "%MAIN_SCRIPT%" (
    color 0C
    echo [ERROR] %MAIN_SCRIPT% not found in current directory.
    echo Place build.bat next to %MAIN_SCRIPT% and try again.
    echo.
    pause
    exit /b 1
)

REM ========================================================
REM  [1/4] Check Python
REM ========================================================
echo [1/4] Checking Python...

set "PYTHON_CMD="

REM Try the py launcher first
py -3 --version >nul 2>&1
if !errorlevel! equ 0 set "PYTHON_CMD=py -3"

REM Fall back to plain "python"
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    color 0C
    echo.
    echo [ERROR] Python is not installed or not on PATH.
    echo.
    echo Please install Python 3.10 or newer from:
    echo     https://www.python.org/downloads/
    echo.
    echo IMPORTANT: During installation, check the box
    echo "Add Python to PATH" on the first screen.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('!PYTHON_CMD! --version 2^>^&1') do echo      Using: %%V  ^(!PYTHON_CMD!^)

REM ========================================================
REM  [2/4] Install required packages
REM ========================================================
echo.
echo [2/4] Installing/updating build dependencies...
!PYTHON_CMD! -m pip install --upgrade pip --quiet --disable-pip-version-check
!PYTHON_CMD! -m pip install --upgrade --disable-pip-version-check %REQUIRED_PACKAGES%
if !errorlevel! neq 0 (
    color 0C
    echo [ERROR] Failed to install required packages.
    echo Check your internet connection and try again.
    pause
    exit /b 1
)

REM ========================================================
REM  [3/4] Clean previous build artifacts
REM ========================================================
echo.
echo [3/4] Cleaning old build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec"

REM ========================================================
REM  [4/4] Run PyInstaller
REM ========================================================
echo.
echo [4/4] Compiling %MAIN_SCRIPT% to %APP_NAME%.exe...
echo.

set "PYI_ARGS=--noconsole --onefile --clean --name %APP_NAME% --hidden-import=ttkbootstrap"
if exist "icon.ico" set "PYI_ARGS=!PYI_ARGS! --icon=icon.ico"

!PYTHON_CMD! -m PyInstaller !PYI_ARGS! "%MAIN_SCRIPT%"
if !errorlevel! neq 0 (
    color 0C
    echo.
    echo [CRITICAL ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

REM ========================================================
REM  Copy resources next to the .exe
REM ========================================================
echo.
echo ========================================================
echo                COPYING RESOURCES
echo ========================================================

pushd dist >nul

if exist "..\hash.txt" (
    copy /y "..\hash.txt" . >nul
    echo      Copied: hash.txt
)

if exist "..\dxcompilers" (
    xcopy "..\dxcompilers" "dxcompilers\" /E /I /Y >nul
    echo      Copied: dxcompilers\
)

for %%D in (source compiled decompiled fxc_files) do (
    if not exist "%%D" (
        mkdir "%%D"
        echo      Created: %%D\
    )
)

popd >nul

REM ========================================================
REM  Done
REM ========================================================
echo.
echo ========================================================
echo                       DONE!
echo ========================================================
echo Output: %CD%\dist\%APP_NAME%.exe
echo.

choice /C YN /M "Open dist folder now"
if !errorlevel! equ 1 start "" "dist"

endlocal
exit /b 0