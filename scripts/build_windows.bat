@echo off
REM Build a single-binary Windows distribution with cx_Freeze.
REM
REM Output: dist\PicoPhone-Py\PicoPhone-Py.exe   (double-click to run)
REM         + python313.dll, opus.dll, lib\ (sibling DLLs/resources)
REM
REM cx_Freeze produces a small native loader exe that links against
REM python313.dll directly — no self-extracting bootloader, so Avast /
REM AVG / Defender don't flag it as Win64:Malware-gen the way they do
REM with PyInstaller's --onefile bundles.
REM
REM Distribute by zipping the dist\PicoPhone-Py folder.
REM
REM Usage: scripts\build_windows.bat
setlocal

cd /d "%~dp0\.."

python -m pip install --upgrade pip "cx_Freeze>=7.2" >nul
python -m pip install -e . || goto :error

set NAME=PicoPhone-Py
if exist build              rmdir /s /q build
if exist "dist\%NAME%"      rmdir /s /q "dist\%NAME%"

python setup_cxfreeze.py build_exe || goto :error

if not exist "dist\%NAME%\%NAME%.exe" (
    echo BUILD did not produce dist\%NAME%\%NAME%.exe
    goto :error
)

echo.
echo ============================================================
echo  Built single-binary distribution:
echo    dist\%NAME%\%NAME%.exe       ^<-- double-click this
echo  Bundle for sharing: zip the whole dist\%NAME%\ folder
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
