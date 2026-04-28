@echo off
REM Build PicoPhone-Py as a true single-file Windows executable using Nuitka.
REM
REM Nuitka compiles Python to C, then builds a native .exe with all
REM Python modules and Qt DLLs embedded. The result is one .exe — no
REM sibling DLLs, no PyInstaller-style bootloader (which Avast flags as
REM Win64:Malware-gen). On startup the launcher unpacks bundled DLLs to
REM a private %TEMP%\onefile_* directory and loads them; that's a
REM Nuitka implementation detail, not user-visible.
REM
REM Toolchain requirement: Python 3.12 (Nuitka refuses to use the
REM auto-downloaded MinGW64 on Python 3.13). Install the official
REM python-3.12.x-amd64.exe; this script picks it up automatically.
REM
REM Output: dist\nuitka\PicoPhone-Py.exe   (~45 MB single file)
REM
REM Usage: scripts\build_windows.bat
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

REM ------------------------------------------------------------------
echo === Step 1: locate Python 3.12 =====================================
set PY312=
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%P set PY312=%%~P
)
if "%PY312%"=="" (
    echo Python 3.12 not found.  Install python-3.12.x-amd64.exe from python.org
    echo and re-run.  Nuitka requires Python 3.12 for auto-MinGW64 builds.
    goto :error
)
echo Using: %PY312%

REM ------------------------------------------------------------------
echo === Step 2: install build tools + runtime deps in Py 3.12 ==========
"%PY312%" -m pip install --upgrade pip --quiet
"%PY312%" -m pip install --quiet --no-warn-script-location ^
    nuitka ordered-set zstandard ^
    PySide6 sounddevice numpy opuslib pyogg cryptography zeroconf tomli-w || goto :error
REM DeepFilterNet (AI mode) is optional; only bundle if it's already installed.
set DFN_FLAGS=
"%PY312%" -c "import df" 2>nul
if errorlevel 1 goto :no_dfn
echo Bundling DeepFilterNet3 (AI mode) into the exe.
set DFN_FLAGS=--include-package=df --include-package=libdf --include-package=torch --include-package=torchaudio --nofollow-import-to=torch.testing --nofollow-import-to=torch.distributed --nofollow-import-to=torch.fx --nofollow-import-to=torch.jit --nofollow-import-to=torch.onnx --nofollow-import-to=torch.optim --nofollow-import-to=torch.profiler --nofollow-import-to=torch._inductor --nofollow-import-to=torch._dynamo --nofollow-import-to=torch.utils.tensorboard --nofollow-import-to=torch.utils.benchmark --nofollow-import-to=torch.nn.qat --nofollow-import-to=torch.nn.quantized --nofollow-import-to=torch.nn.intrinsic --nofollow-import-to=torch.ao --nofollow-import-to=torch.quantization --nofollow-import-to=sympy --nofollow-import-to=networkx
:no_dfn

REM ------------------------------------------------------------------
echo === Step 3: locate bundled opus.dll (from pyogg) ===================
"%PY312%" -c "import os, pyogg; open('build_opus_path.tmp','w').write(os.path.join(os.path.dirname(pyogg.__file__),'opus.dll'))" || goto :error
set /p OPUS_DLL=<build_opus_path.tmp
del build_opus_path.tmp
if not exist "%OPUS_DLL%" (
    echo opus.dll not found at "%OPUS_DLL%"
    goto :error
)

REM ------------------------------------------------------------------
echo === Step 4: Nuitka onefile build (this takes 5-15 min on first run) ==
REM Don't wipe dist\nuitka wholesale: a Linux ELF (PicoPhone-Py without .exe)
REM may live alongside us from a build_mageia.sh / WSL build.
if exist dist\nuitka\PicoPhone-Py.exe        del /q dist\nuitka\PicoPhone-Py.exe
if exist dist\nuitka\__main__.build          rmdir /s /q dist\nuitka\__main__.build
if exist dist\nuitka\__main__.dist           rmdir /s /q dist\nuitka\__main__.dist
if exist dist\nuitka\__main__.onefile-build  rmdir /s /q dist\nuitka\__main__.onefile-build
if not exist dist\nuitka                     mkdir dist\nuitka

REM Use all CPU cores for compilation.
REM NUMBER_OF_PROCESSORS is a built-in Windows env variable (logical CPU count).
if "%NUMBER_OF_PROCESSORS%"=="" set NUMBER_OF_PROCESSORS=4
echo Using %NUMBER_OF_PROCESSORS% parallel jobs

"%PY312%" -m nuitka ^
    --onefile ^
    --jobs=%NUMBER_OF_PROCESSORS% ^
    --mingw64 ^
    --assume-yes-for-downloads ^
    --windows-console-mode=disable ^
    --enable-plugin=pyside6 ^
    --include-data-files="picophone\ui\skin.qss=picophone\ui\skin.qss" ^
    --include-data-files="%OPUS_DLL%=opus.dll" ^
    --include-data-files="assets\icons\picophone.ico=assets\icons\picophone.ico" ^
    --include-data-files="assets\ringin.wav=assets\ringin.wav" ^
    --include-package=picophone ^
    --include-module=picophone.autostart ^
    --include-package=cryptography ^
    --include-package=opuslib ^
    --include-package=numpy ^
    --windows-icon-from-ico="assets\icons\picophone.ico" ^
    --output-dir=dist\nuitka ^
    --output-filename=PicoPhone-Py.exe ^
    %DFN_FLAGS% ^
    picophone\__main__.py || goto :error

REM ------------------------------------------------------------------
if not exist "dist\nuitka\PicoPhone-Py.exe" goto :error

echo.
echo ============================================================
echo  Built true single-file portable exe:
echo    dist\nuitka\PicoPhone-Py.exe
echo  Double-click to run.  No sibling DLLs needed.
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
