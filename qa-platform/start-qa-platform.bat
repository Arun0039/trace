@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="

where py >nul 2>nul
if errorlevel 1 goto try_python_command
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 goto try_python_command
goto run_py

:try_python_command
where python >nul 2>nul
if errorlevel 1 goto find_python
python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 goto find_python
set "PYTHON_EXE=python"
goto run_python

:find_python
call :find_installed_python
if defined PYTHON_EXE goto run_python

where winget >nul 2>nul
if errorlevel 1 goto no_python
echo Python 3.10 or newer was not found on PATH or in common install locations.
set /p "INSTALL_PYTHON=Install Python 3.13 for your Windows user with winget now? [Y/N] "
if /I not "%INSTALL_PYTHON%"=="Y" goto no_python
winget install --id Python.Python.3.13 --exact --scope user --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto install_failed
call :find_installed_python
if defined PYTHON_EXE goto run_python

echo Python was installed, but its executable could not be found in the usual locations.
echo Close and reopen this window, then run this launcher again.
pause
exit /b 1

:run_py
echo Starting the QA Platform with Python...
py -3 app.py
set "APP_EXIT_CODE=%errorlevel%"
echo.
echo The QA Platform process stopped with exit code %APP_EXIT_CODE%.
echo If an error was printed above, copy it to your support request.
pause
exit /b %APP_EXIT_CODE%

:run_python
echo Starting the QA Platform with "%PYTHON_EXE%"...
"%PYTHON_EXE%" app.py
set "APP_EXIT_CODE=%errorlevel%"
echo.
echo The QA Platform process stopped with exit code %APP_EXIT_CODE%.
echo If an error was printed above, copy it to your support request.
pause
exit /b %APP_EXIT_CODE%

:find_installed_python
for %%V in (314 313 312 311 310) do (
  if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python%%V\python.exe"
  if not defined PYTHON_EXE if exist "%ProgramFiles%\Python%%V\python.exe" set "PYTHON_EXE=%ProgramFiles%\Python%%V\python.exe"
  if not defined PYTHON_EXE if exist "C:\Python%%V\python.exe" set "PYTHON_EXE=C:\Python%%V\python.exe"
)
if defined PYTHON_EXE "%PYTHON_EXE%" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>nul
if errorlevel 1 set "PYTHON_EXE="
exit /b 0

:install_failed
echo Python installation did not complete. Install Python manually from https://www.python.org/downloads/.
pause
exit /b 1

:no_python
echo Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/ and enable "Add Python to PATH".
echo Then run this file again, or open a terminal here and run: py -3 app.py
pause
exit /b 1
