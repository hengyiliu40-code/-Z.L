@echo off
chcp 65001 >nul
set "PIDFILE=%APPDATA%\FerrariF1Timeline\widget.pid"
if not exist "%PIDFILE%" (
    echo Panel is not running.
    ping -n 3 127.0.0.1 >nul
    exit /b
)
set /p PID=<"%PIDFILE%"
taskkill /PID %PID% /F >nul 2>nul
del "%PIDFILE%" >nul 2>nul
echo Race timeline panel closed.
ping -n 3 127.0.0.1 >nul
