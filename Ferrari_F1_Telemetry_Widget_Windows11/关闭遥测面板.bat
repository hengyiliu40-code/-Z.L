@echo off
chcp 65001 >nul
rem Stop the telemetry panel using its pid file
set "PIDFILE=%APPDATA%\FerrariF1Telemetry\widget.pid"
if not exist "%PIDFILE%" (
    echo Telemetry panel is not running.
    ping -n 3 127.0.0.1 >nul
    exit /b
)
set /p WPID=<"%PIDFILE%"
taskkill /PID %WPID% /F >nul 2>nul
del "%PIDFILE%" >nul 2>nul
echo Telemetry panel closed.
ping -n 3 127.0.0.1 >nul
