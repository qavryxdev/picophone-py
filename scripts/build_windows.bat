@echo off
REM Build a portable Windows distribution by bundling python.org's signed
REM embeddable Python with our source.  No PyInstaller bootloader = no
REM Win64:Malware-gen false positive from Avast / AVG / Defender.
REM
REM Output: dist\PicoPhone-Py\
REM   PicoPhone-Py.bat            (launcher)
REM   python\python.exe           (signed by Python Software Foundation)
REM   python\Lib\site-packages\*  (all deps incl. opus.dll via pyogg)
REM   picophone\*                 (our source)
REM   assets\*
REM
REM Distribute by zipping the whole dist\PicoPhone-Py folder.
REM
REM Usage: scripts\build_windows.bat
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

set PYVER=3.13.7
set NAME=PicoPhone-Py
set DIST=dist\%NAME%

REM ------------------------------------------------------------------
echo.
echo === Cleaning previous build ===========================================
if exist "%DIST%" rmdir /s /q "%DIST%"
mkdir "%DIST%\python" 2>nul
mkdir "%DIST%\picophone" 2>nul
mkdir "%DIST%\assets" 2>nul

REM ------------------------------------------------------------------
echo.
echo === Downloading embeddable Python %PYVER% ============================
set EMBED_URL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip
set EMBED_ZIP=dist\python-embed.zip
if not exist "%EMBED_ZIP%" (
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%EMBED_URL%' -OutFile '%EMBED_ZIP%' -UseBasicParsing" || goto :error
)
powershell -NoProfile -Command "Expand-Archive -Force '%EMBED_ZIP%' '%DIST%\python'" || goto :error

REM Enable site-packages and add the dist root to sys.path so the picophone
REM package (sibling of python/) is importable.
for %%P in ("%DIST%\python\python*._pth") do (
    powershell -NoProfile -Command "$f='%%P'; $c=Get-Content $f; $c=$c -replace '^#import site','import site'; if (-not ($c -match '^\.\.$')) { $c += '..' }; Set-Content $f $c" || goto :error
)

REM ------------------------------------------------------------------
echo.
echo === Bootstrapping pip inside embed Python ============================
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%DIST%\python\get-pip.py' -UseBasicParsing" || goto :error
"%DIST%\python\python.exe" "%DIST%\python\get-pip.py" --no-warn-script-location || goto :error
del "%DIST%\python\get-pip.py"

REM ------------------------------------------------------------------
echo.
echo === Installing PicoPhone-Py runtime deps =============================
"%DIST%\python\python.exe" -m pip install --no-warn-script-location ^
    PySide6 sounddevice numpy opuslib pyogg cryptography zeroconf tomli-w || goto :error

REM ------------------------------------------------------------------
echo.
echo === Copying project source ===========================================
xcopy /e /y /q picophone "%DIST%\picophone\" >nul
if exist assets xcopy /e /y /q assets "%DIST%\assets\" >nul

REM ------------------------------------------------------------------
echo.
echo === Writing launcher =================================================
> "%DIST%\PicoPhone-Py.bat" (
    echo @echo off
    echo REM PicoPhone-Py portable launcher
    echo set HERE=%%~dp0
    echo "%%HERE%%python\pythonw.exe" -m picophone %%*
)

> "%DIST%\PicoPhone-Py-debug.bat" (
    echo @echo off
    echo REM PicoPhone-Py portable launcher with console for debugging
    echo set HERE=%%~dp0
    echo "%%HERE%%python\python.exe" -m picophone %%*
    echo pause
)

REM ------------------------------------------------------------------
echo.
echo ============================================================
echo  Built portable distribution: %DIST%
echo  Run:    %DIST%\PicoPhone-Py.bat
echo  Debug:  %DIST%\PicoPhone-Py-debug.bat   (shows console output)
echo  Share:  zip the whole %DIST% folder
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
