@echo off
REM Build single-file PicoPhone-Py.exe for Windows.
REM Usage: scripts\build_windows.bat
setlocal

cd /d "%~dp0\.."

python -m pip install --upgrade pip pyinstaller >nul
python -m pip install -e . || goto :error

for /f "delims=" %%I in ('python -c "import os, pyogg; print(os.path.dirname(pyogg.__file__))"') do set PYOGG_DIR=%%I
if "%PYOGG_DIR%"=="" (
    echo ERROR: pyogg not installed
    goto :error
)

set NAME=PicoPhone-Py
if exist build  rmdir /s /q build
if exist dist\%NAME%.exe del /q dist\%NAME%.exe

python -m PyInstaller ^
    --noconfirm --windowed --onefile --clean ^
    --name %NAME% ^
    --add-data "picophone\ui\skin.qss;picophone\ui" ^
    --add-binary "%PYOGG_DIR%\opus.dll;." ^
    --collect-submodules zeroconf ^
    --collect-submodules cryptography ^
    -m picophone

if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Built: dist\%NAME%.exe
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
