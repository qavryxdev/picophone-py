@echo off
REM Build PicoPhone-Py as a single-file Windows executable using PyInstaller.
REM
REM PyInstaller bundles the Python interpreter, compiled .pyc modules and
REM native extensions into a single .exe with a small bootloader.  Build
REM completes in ~30 seconds (vs ~30 minutes for a full Nuitka C-transpile).
REM
REM Known issue: Avast (and some other AV vendors) flag the PyInstaller
REM bootloader as IDP.Generic.  Whitelist the exe locally or sign it with a
REM code-signing certificate before distribution.
REM
REM Output: dist\PicoPhone-Py.exe   (~110 MB single file, no UPX)
REM
REM Usage: scripts\build_windows.bat
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

REM ------------------------------------------------------------------
echo === Step 1: locate Python =========================================
set PY=
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%P set PY=%%~P
)
if "%PY%"=="" (
    echo Python 3.11 or 3.12 not found.  Install python-3.12.x-amd64.exe from
    echo python.org and re-run.
    goto :error
)
echo Using: %PY%

REM ------------------------------------------------------------------
echo === Step 2: install PyInstaller + runtime deps ====================
"%PY%" -m pip install --upgrade pip --quiet
"%PY%" -m pip install --quiet --no-warn-script-location ^
    pyinstaller ^
    PySide6 sounddevice numpy opuslib pyogg cryptography zeroconf tomli-w ^
    onnxruntime || goto :error
REM AI mode (DeepFilterNet3) runs via onnxruntime + libdf, no PyTorch.
"%PY%" -m pip install --quiet --no-warn-script-location deepfilterlib 2>nul

set DFN_FLAGS=
"%PY%" -c "import libdf, onnxruntime" 2>nul
if errorlevel 1 goto :no_dfn
if not exist assets\dfn3\enc.onnx goto :no_dfn
echo Bundling DeepFilterNet3 (ONNX, no torch) into the exe.
REM We collect onnxruntime *binaries+data* but NOT every submodule —
REM `--collect-all` recurses into onnxruntime.transformers, .training,
REM .tools, .quantization which we never load and which add ~1 minute
REM of hidden-import analysis to the build.  We import only the top-level
REM ort + the C extension at runtime.
set DFN_FLAGS=--collect-all libdf --collect-binaries onnxruntime --collect-data onnxruntime --add-data "assets\dfn3;assets\dfn3"
:no_dfn

REM ------------------------------------------------------------------
echo === Step 3: locate bundled opus.dll (from pyogg) ==================
"%PY%" -c "import os, pyogg; open('build_opus_path.tmp','w').write(os.path.join(os.path.dirname(pyogg.__file__),'opus.dll'))" || goto :error
set /p OPUS_DLL=<build_opus_path.tmp
del build_opus_path.tmp
if not exist "%OPUS_DLL%" (
    echo opus.dll not found at "%OPUS_DLL%"
    goto :error
)

REM ------------------------------------------------------------------
echo === Step 4: parallel bytecode pre-compile (all CPU cores) =========
REM PyInstaller's bundling itself is mostly serial I/O.  The one CPU-bound
REM step it does is bytecode compilation, which we warm here with all
REM cores so PyInstaller hits cache hits during the freeze step.
"%PY%" -m compileall -j 0 -q picophone

REM ------------------------------------------------------------------
echo === Step 5: PyInstaller onefile build =============================
if exist dist\PicoPhone-Py.exe del /q dist\PicoPhone-Py.exe
if exist build                  rmdir /s /q build
if exist PicoPhone-Py.spec      del /q PicoPhone-Py.spec

REM Optional UPX compression — keep tools/upx-*/upx.exe in the repo to enable.
set UPX_FLAGS=--noupx
for /d %%U in ("tools\upx-*-win64") do (
    if exist "%%~U\upx.exe" set UPX_FLAGS=--upx-dir="%%~U"
)
echo UPX: %UPX_FLAGS%

"%PY%" -m PyInstaller ^
    --onefile ^
    --noconsole ^
    %UPX_FLAGS% ^
    --name PicoPhone-Py ^
    --icon "assets\icons\picophone.ico" ^
    --add-data "picophone\ui\skin.qss;picophone\ui" ^
    --add-data "%OPUS_DLL%;." ^
    --add-data "assets\icons\picophone.ico;assets\icons" ^
    --add-data "assets\ringin.wav;assets" ^
    --hidden-import picophone.autostart ^
    --hidden-import picophone.audio.dfn_onnx ^
    --collect-submodules picophone ^
    %DFN_FLAGS% ^
    --distpath dist ^
    --workpath build ^
    picophone\__main__.py || goto :error

if not exist "dist\PicoPhone-Py.exe" goto :error

echo.
echo ============================================================
echo  Built single-file Windows exe:
echo    dist\PicoPhone-Py.exe
echo  Note: Avast may quarantine the bootloader as IDP.Generic;
echo  whitelist the file or code-sign it before distribution.
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
