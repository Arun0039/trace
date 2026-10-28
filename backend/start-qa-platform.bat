@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="
set "PYTHON_ARGS="

where py >nul 2>nul
if errorlevel 1 goto try_python_command
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10) or sys.version_info.releaselevel != 'final')" >nul 2>nul
if errorlevel 1 goto try_python_command
set "PYTHON_EXE=py"
set "PYTHON_ARGS=-3"
goto run_app

:try_python_command
where python >nul 2>nul
if errorlevel 1 goto find_python
python -c "import sys; raise SystemExit(sys.version_info < (3, 10) or sys.version_info.releaselevel != 'final')" >nul 2>nul
if errorlevel 1 goto find_python
set "PYTHON_EXE=python"
goto run_app

:find_python
call :find_installed_python
if defined PYTHON_EXE goto run_app

where winget >nul 2>nul
if errorlevel 1 goto no_python
echo Stable Python 3.10 or newer was not found on PATH or in common install locations.
set /p "INSTALL_PYTHON=Install Python 3.13 for your Windows user with winget now? [Y/N] "
if /I not "%INSTALL_PYTHON%"=="Y" goto no_python
winget install --id Python.Python.3.13 --exact --scope user --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto install_failed
call :find_installed_python
if defined PYTHON_EXE goto run_app

echo Python was installed, but its executable could not be found in the usual locations.
echo Close and reopen this window, then run this launcher again.
pause
exit /b 1

:run_app
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import cryptography, pypdf, psycopg" >nul 2>nul
if errorlevel 1 (
  echo Required Python packages are missing. Installing from requirements.txt...
  "%PYTHON_EXE%" %PYTHON_ARGS% -m pip --version >nul 2>nul
  if errorlevel 1 (
    echo Bootstrapping pip for the selected Python interpreter...
    "%PYTHON_EXE%" %PYTHON_ARGS% -m ensurepip --upgrade
    if errorlevel 1 goto dependencies_failed
  )
  "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r requirements.txt
  if errorlevel 1 goto dependencies_failed
  "%PYTHON_EXE%" %PYTHON_ARGS% -c "import cryptography, pypdf, psycopg"
  if errorlevel 1 goto dependencies_failed
)
echo Starting the QA Platform with "%PYTHON_EXE%"...
"%PYTHON_EXE%" %PYTHON_ARGS% app.py
set "APP_EXIT_CODE=%errorlevel%"
echo.
echo The QA Platform process stopped with exit code %APP_EXIT_CODE%.
echo If an error was printed above, copy it to your support request.
pause
exit /b %APP_EXIT_CODE%

:dependencies_failed
echo.
echo ERROR: The QA Platform dependencies could not be installed for the selected Python interpreter.
echo Check the pip error above, then run:
echo   "%PYTHON_EXE%" %PYTHON_ARGS% -m pip install -r requirements.txt
pause
exit /b 1

:find_installed_python
for %%V in (314 313 312 311 310) do (
  if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    "%LocalAppData%\Programs\Python\Python%%V\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 10) or sys.version_info.releaselevel != 'final')" >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python%%V\python.exe"
  )
  if not defined PYTHON_EXE if exist "%ProgramFiles%\Python%%V\python.exe" (
    "%ProgramFiles%\Python%%V\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 10) or sys.version_info.releaselevel != 'final')" >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=%ProgramFiles%\Python%%V\python.exe"
  )
  if not defined PYTHON_EXE if exist "C:\Python%%V\python.exe" (
    "C:\Python%%V\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 10) or sys.version_info.releaselevel != 'final')" >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=C:\Python%%V\python.exe"
  )
)
exit /b 0

:install_failed
echo Python installation did not complete. Install Python manually from https://www.python.org/downloads/.
pause
exit /b 1

:no_python
echo Stable Python 3.10 or newer was not found. Pre-release Python versions are not supported.
echo Install Python from https://www.python.org/downloads/ and enable "Add Python to PATH".
echo Then run this file again, or start the app with your stable Python interpreter.
pause
exit /b 1
