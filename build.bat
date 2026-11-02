@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -U pyinstaller -r requirements.txt
python build_exe.py
pause
