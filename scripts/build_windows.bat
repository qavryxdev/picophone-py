@echo off
REM Build a single-file portable PicoPhone-Py-portable.exe.
REM
REM Pipeline:
REM   1. cx_Freeze: build dist\PicoPhone-Py\ (~640 MB with all of Qt)
REM   2. prune_dist.py: strip unused PySide6 modules (~134 MB)
REM   3. 7-Zip SFX: pack the pruned tree behind a tiny self-extractor
REM      stub, producing a single ~34 MB .exe that extracts to %TEMP%
REM      and launches the GUI on double-click.
REM
REM Output: dist\PicoPhone-Py-portable.exe       (~34 MB, single file)
REM
REM cx_Freeze's loader exe + 7-Zip SFX stub are not flagged as
REM Win64:Malware-gen the way PyInstaller's --onefile bundles are.
REM
REM Usage: scripts\build_windows.bat
setlocal

cd /d "%~dp0\.."

set NAME=PicoPhone-Py
set SFX_DIR=%USERPROFILE%\bin\7z\installer-content
set SEVENZR=%USERPROFILE%\bin\7z\7zr.exe

REM ------------------------------------------------------------------
echo === Step 1: install build tools ====================================
python -m pip install --upgrade pip "cx_Freeze>=7.2" >nul
python -m pip install -e . || goto :error

REM ------------------------------------------------------------------
echo === Step 2: cx_Freeze build ========================================
if exist build              rmdir /s /q build
if exist "dist\%NAME%"      rmdir /s /q "dist\%NAME%"
if exist "dist\%NAME%-portable.exe" del /q "dist\%NAME%-portable.exe"

python setup_cxfreeze.py build_exe || goto :error
if not exist "dist\%NAME%\%NAME%.exe" (
    echo BUILD did not produce dist\%NAME%\%NAME%.exe
    goto :error
)

REM ------------------------------------------------------------------
echo === Step 3: prune unused Qt modules ================================
python scripts\prune_dist.py "dist\%NAME%" || goto :error

REM ------------------------------------------------------------------
echo === Step 4: ensure 7-Zip SFX is available ==========================
if not exist "%SFX_DIR%\7z.sfx" (
    echo Fetching 7-Zip SFX module...
    if not exist "%SFX_DIR%\.." mkdir "%SFX_DIR%\.."
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.7-zip.org/a/7zr.exe' -OutFile '%SEVENZR%' -UseBasicParsing" || goto :error
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.7-zip.org/a/7z2501-x64.exe' -OutFile '%SFX_DIR%\..\7z-installer.exe' -UseBasicParsing" || goto :error
    "%SEVENZR%" x "%SFX_DIR%\..\7z-installer.exe" -o"%SFX_DIR%" -y >nul || goto :error
)

REM ------------------------------------------------------------------
echo === Step 5: pack into single-file SFX exe ==========================
python scripts\build_sfx.py "%SFX_DIR%\7z.sfx" "%SEVENZR%" || goto :error

REM ------------------------------------------------------------------
echo.
echo ============================================================
echo  Built single-file portable exe:
echo    dist\%NAME%-portable.exe
echo  Double-click to run.
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
