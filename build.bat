@echo off
title GTA 5 Shader Manager Build
color 0A

echo ========================================================
echo                PREPARING BUILD
echo ========================================================

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    pause
    exit /b
)

REM Install required libraries
echo [1/4] Installing/Updating libraries...
pip install --upgrade pyinstaller ttkbootstrap configparser

echo.
echo ========================================================
echo                CLEANING OLD FILES
echo ========================================================
echo [2/4] Removing build and dist folders...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "*.spec" del /q "*.spec"

echo.
echo ========================================================
echo                COMPILING TO .EXE
echo ========================================================
echo [3/4] Running PyInstaller...

REM --noconsole: hides the black console window
REM --onefile: compiles everything into a single .exe file
REM --name: output file name
REM --hidden-import: ensures ttkbootstrap is included

pyinstaller --noconsole --onefile --clean --name "GTA5ShaderManager" --hidden-import=ttkbootstrap main.py

if %errorlevel% neq 0 (
    color 0C
    echo.
    echo [CRITICAL ERROR] Build failed!
    pause
    exit /b
)

echo.
echo ========================================================
echo                COPYING RESOURCES
echo ========================================================
echo [4/4] Creating folder structure and copying files...

REM Switch to dist folder
cd dist

REM Copy hash.txt (Required)
if exist "..\hash.txt" copy "..\hash.txt" .

REM Copy compilers folder (fxc.exe etc.)
if exist "..\dxcompilers" xcopy "..\dxcompilers" "dxcompilers\" /E /I /Y

REM Create empty working directories so the app starts smoothly
if not exist "source" mkdir "source"
if not exist "compiled" mkdir "compiled"
if not exist "decompiled" mkdir "decompiled"
if not exist "fxc_files" mkdir "fxc_files"

echo.
echo ========================================================
echo                     DONE!
echo ========================================================
echo Your program is located in the folder: dist
echo.
pause