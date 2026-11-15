@echo off
chcp 65001 >nul
title Build Ferrari F1 Widget EXE
py -m pip install --upgrade pyinstaller
py -m PyInstaller --noconfirm --clean --onefile --windowed --name Ferrari_F1_Cyber_Widget Ferrari_F1_Cyber_Widget.py
echo.
echo Build finished. EXE is in the dist folder.
pause
