@echo off
chcp 65001 >nul
rem Silent launcher (pythonw = no console window).
rem NOTE: official Python install is preferred; some bundled Python builds ship without tkinter.
set "SCRIPT=%~dp0Ferrari_Telemetry_Widget.py"

for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
    "%ProgramFiles%\Python314\pythonw.exe"
    "%ProgramFiles%\Python313\pythonw.exe"
) do (
    if exist %%P (
        start "" %%P "%SCRIPT%"
        exit /b
    )
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%SCRIPT%"
    exit /b
)

echo pythonw.exe with tkinter was not found.
echo Install Python 3.9+ from python.org and enable "Add Python to PATH".
pause
