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
set "ERR=0"

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

REM -- find a compatible Python (need 3.10-3.13; 3.12 recommended) ----
REM PyInstaller 6.11.1 and the pinned deps do NOT support Python 3.14.
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
  if not defined PY (
    py -%%V --version >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  REM last resort: bare "python", but only if it is in [3.10, 3.14)
  python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info<(3,14) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo ERROR: No compatible Python found.
  echo        Need Python 3.10-3.13 ^(3.12 recommended^); 3.14 is NOT supported
  echo        by pyinstaller==6.11.1 or the pinned wheels.
  echo        Install Python 3.12: https://www.python.org/downloads/release/python-3128/
  set "ERR=1" & goto fail
)
echo [build_win] using interpreter: %PY%
%PY% --version

REM -- venv ------------------------------------------------------------
set "VENV=%ROOT%\.venv_win"
REM Recreate the venv if it was built on an incompatible Python (e.g. 3.14).
if exist "%VENV%\Scripts\python.exe" (
  "%VENV%\Scripts\python.exe" -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info<(3,14) else 1)" >nul 2>&1
  if errorlevel 1 (
    echo [build_win] existing .venv_win uses an incompatible Python -- recreating ...
    rmdir /s /q "%VENV%"
  )
)
if not exist "%VENV%" (
  echo [build_win] creating %VENV% ...
  %PY% -m venv "%VENV%"
  if errorlevel 1 ( echo ERROR: venv creation failed & set "ERR=1" & goto fail )
)
set "VPY=%VENV%\Scripts\python.exe"
echo [build_win] venv python:
"%VPY%" --version
"%VPY%" -m pip install --upgrade pip wheel >nul
echo [build_win] installing deps from requirements.txt ...
"%VPY%" -m pip install -r "%ROOT%\requirements.txt" pyinstaller==6.11.1
if errorlevel 1 ( echo ERROR: pip install failed & set "ERR=1" & goto fail )

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
  --exclude-module tkinter ^
  --exclude-module _tkinter ^
  --exclude-module Tkinter ^
  --exclude-module tcl ^
  --exclude-module tk ^
  "%ROOT%\runway_app.py"
set "PYI_ERR=%errorlevel%"

REM -- revert license.py no matter what -------------------------------
if "%KEY_EMBEDDED%"=="1" (
  echo [build_win] reverting license.py to dev mode ...
  "%VPY%" "%ROOT%\scripts\embed_key.py" --revert
)

if not "%PYI_ERR%"=="0" (
  echo ERROR: PyInstaller failed with code %PYI_ERR%
  set "ERR=%PYI_ERR%" & goto fail
)

set "APPDIR=%ROOT%\dist\win\RunwayAutomation"
if not exist "%APPDIR%\RunwayAutomation.exe" (
  echo ERROR: PyInstaller did not produce %APPDIR%\RunwayAutomation.exe
  set "ERR=1" & goto fail
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
goto done

:fail
echo.
echo ============================================================
echo  BUILD FAILED (code %ERR%). Scroll up to the FIRST red error.
echo ============================================================
popd
echo.
pause
endlocal
exit /b %ERR%

:done
popd
echo.
pause
endlocal
exit /b 0
