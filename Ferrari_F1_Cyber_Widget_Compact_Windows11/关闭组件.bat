@echo off
chcp 65001 >nul
rem Stop the desktop widget using its pid file
set "PIDFILE=%APPDATA%\FerrariF1WidgetCompact\widget.pid"
if not exist "%PIDFILE%" (
    echo Widget is not running.
    ping -n 3 127.0.0.1 >nul
    exit /b
)
set /p WPID=<"%PIDFILE%"
taskkill /PID %WPID% /F >nul 2>nul
del "%PIDFILE%" >nul 2>nul
echo Widget closed.
ping -n 3 127.0.0.1 >nul
