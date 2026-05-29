@echo off
REM Build the Windows .exe bundle for RunwayAutomation (onedir).
REM
REM Output:
REM   dist\win\RunwayAutomation\RunwayAutomation.exe
REM
REM Flow:
REM   1. ensure a Python 3.12 .venv_win with deps installed
REM   2. bake keys\public.pem into license.py (if present)
REM   3. run PyInstaller -> dist\win\RunwayAutomation\
REM   4. copy Playwright Chromium next to the .exe (browser-login fallback)
REM   5. revert license.py to dev mode
REM
REM Always wipes build\runway_app_win\ and dist\win\ before building.
REM
REM Usage:
REM   scripts\build_win.bat                  full build
REM   scripts\build_win.bat --no-playwright  skip ~250 MB browser bundle
setlocal enabledelayedexpansion

REM -- locate project root (parent of scripts\) ------------------------
set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.."
set "ROOT=%CD%"

REM -- parse flags -----------------------------------------------------
set "WITH_PLAYWRIGHT=1"
:parseargs
if "%~1"=="" goto afterargs
if /I "%~1"=="--no-playwright" set "WITH_PLAYWRIGHT=0"
if /I "%~1"=="-h" goto showhelp
if /I "%~1"=="--help" goto showhelp
shift
goto parseargs
:showhelp
echo Usage: scripts\build_win.bat [--no-playwright]
popd & exit /b 0
:afterargs

REM -- find Python 3.12 ------------------------------------------------
set "PY="
py -3.12 --version >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo ERROR: Python not found. Install Python 3.12 from python.org
  echo        ^(make sure "py launcher" / "Add to PATH" is checked^).
  popd & exit /b 1
)
echo [build_win] using interpreter: %PY%
%PY% --version

REM -- venv ------------------------------------------------------------
set "VENV=%ROOT%\.venv_win"
if not exist "%VENV%" (
  echo [build_win] creating %VENV% ...
  %PY% -m venv "%VENV%"
  if errorlevel 1 ( echo ERROR: venv creation failed & popd & exit /b 1 )
)
set "VPY=%VENV%\Scripts\python.exe"
"%VPY%" -m pip install --upgrade pip wheel >nul
echo [build_win] installing deps from requirements.txt ...
"%VPY%" -m pip install -r "%ROOT%\requirements.txt" pyinstaller==6.11.1
if errorlevel 1 ( echo ERROR: pip install failed & popd & exit /b 1 )

REM -- playwright browser cache ---------------------------------------
if "%WITH_PLAYWRIGHT%"=="1" (
  echo [build_win] ensuring Playwright Chromium is installed ...
  "%VPY%" -m playwright install chromium
)

REM -- clean previous build -------------------------------------------
echo [build_win] cleaning build\runway_app_win\ and dist\win\ ...
if exist "%ROOT%\build\runway_app_win" rmdir /s /q "%ROOT%\build\runway_app_win"
if exist "%ROOT%\dist\win" rmdir /s /q "%ROOT%\dist\win"
mkdir "%ROOT%\dist\win"

REM -- embed public key (if available) --------------------------------
set "KEY_EMBEDDED=0"
if exist "%ROOT%\keys\public.pem" (
  echo [build_win] embedding keys\public.pem into license.py ...
  "%VPY%" "%ROOT%\scripts\embed_key.py"
  set "KEY_EMBEDDED=1"
) else (
  echo [build_win] WARNING: keys\public.pem not found -- building in DEV MODE
  echo              ^(anyone can run the .exe without a license.lic file^)
)

REM -- PyInstaller ----------------------------------------------------
echo [build_win] running PyInstaller ...
"%VPY%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --name RunwayAutomation ^
  --windowed ^
  --workpath "%ROOT%\build\runway_app_win" ^
  --distpath "%ROOT%\dist\win" ^
  --specpath "%ROOT%\build" ^
  --collect-all flet ^
  --collect-data certifi ^
  --hidden-import aiosqlite ^
  --hidden-import sqlmodel ^
  --hidden-import anthropic ^
  --hidden-import winotify ^
  --hidden-import playwright ^
  "%ROOT%\runway_app.py"
set "PYI_ERR=%errorlevel%"

REM -- revert license.py no matter what -------------------------------
if "%KEY_EMBEDDED%"=="1" (
  echo [build_win] reverting license.py to dev mode ...
  "%VPY%" "%ROOT%\scripts\embed_key.py" --revert
)

if not "%PYI_ERR%"=="0" (
  echo ERROR: PyInstaller failed with code %PYI_ERR%
  popd & exit /b %PYI_ERR%
)

set "APPDIR=%ROOT%\dist\win\RunwayAutomation"
if not exist "%APPDIR%\RunwayAutomation.exe" (
  echo ERROR: PyInstaller did not produce %APPDIR%\RunwayAutomation.exe
  popd & exit /b 1
)

REM -- bundle Playwright Chromium next to the .exe --------------------
REM app\__init__.py looks at  Path(sys.executable).parent / "ms-playwright"
if "%WITH_PLAYWRIGHT%"=="1" (
  set "PW_CACHE=%USERPROFILE%\AppData\Local\ms-playwright"
  if exist "!PW_CACHE!" (
    echo [build_win] copying Playwright Chromium into the bundle ...
    if exist "%APPDIR%\ms-playwright" rmdir /s /q "%APPDIR%\ms-playwright"
    mkdir "%APPDIR%\ms-playwright"
    for /d %%D in ("!PW_CACHE!\chromium-*" "!PW_CACHE!\ffmpeg-*") do (
      echo   copying %%~nxD
      xcopy /e /i /q /y "%%D" "%APPDIR%\ms-playwright\%%~nxD" >nul
    )
  ) else (
    echo [build_win] WARNING: !PW_CACHE! missing -- browser login fallback won't work
  )
)

echo.
echo [build_win] done
echo [build_win]   bundle: %APPDIR%
echo [build_win]   exe:    %APPDIR%\RunwayAutomation.exe
echo.
echo Run locally:  "%APPDIR%\RunwayAutomation.exe"
echo Distribute:   zip the whole RunwayAutomation\ folder (the .exe needs _internal\ next to it)

popd
endlocal
exit /b 0
