@echo off
REM Build script for PFR Sentinel executable
REM Creates a Windows executable using PyInstaller

echo ========================================
echo   PFR Sentinel - Build Executable
echo ========================================
echo.

REM Activate virtual environment if it exists
if exist venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo WARNING: Virtual environment not found
    echo Continuing with system Python...
)

echo.
echo Cleaning old build artifacts...
if exist build rmdir /s /q build
if exist dist\PFRSentinel rmdir /s /q dist\PFRSentinel

echo.
echo Building executable with PyInstaller...
venv\Scripts\python.exe -m PyInstaller PFRSentinel.spec

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Build failed!
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Build completed successfully!
echo ========================================
echo.
echo Executable location:
echo   dist\PFRSentinel\PFRSentinel.exe
echo.
echo You can now run:
echo   dist\PFRSentinel\PFRSentinel.exe
echo.
echo Or build the installer with:
echo   build_sentinel_installer.bat
echo.

pause
