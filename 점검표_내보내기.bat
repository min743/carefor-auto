@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -X utf8 "점검표_내보내기.py"
pause
