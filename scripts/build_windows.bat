@echo off
REM Build PicoPhone-Py for Windows as a one-folder distribution.
REM
REM Why one-folder (not one-file)?  PyInstaller's --onefile produces a
REM self-extracting bootloader pattern that Avast/AVG/Windows Defender
REM frequently flag as Win64:Malware-gen (false positive).  One-folder
REM avoids that signature and runs identically.
REM
REM If your AV still complains, add this folder to its exclusion list:
REM   Avast:    Settings > General > Exceptions > Add Exception > Folder
REM   Defender: Windows Security > Virus & threat protection >
REM             Manage settings > Exclusions > Add or remove exclusions
REM
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
if exist build       rmdir /s /q build
if exist dist\%NAME% rmdir /s /q dist\%NAME%

python -m PyInstaller ^
    --noconfirm --windowed --noupx --clean ^
    --name %NAME% ^
    --add-data "picophone\ui\skin.qss;picophone\ui" ^
    --add-binary "%PYOGG_DIR%\opus.dll;." ^
    --collect-submodules zeroconf ^
    --collect-submodules cryptography ^
    picophone\__main__.py

if errorlevel 1 goto :error

echo.
echo ============================================================
echo  Built:  dist\%NAME%\%NAME%.exe
echo  Run:    dist\%NAME%\%NAME%.exe
echo  Bundle: dist\%NAME%\         (zip the folder to share)
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
echo If Avast/Defender deleted the EXE, add %CD% to AV exclusions
echo or run from source:  python -m picophone
exit /b 1
