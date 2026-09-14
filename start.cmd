@echo off
cd /d "%~dp0"
python codestudio.py
if errorlevel 1 pause
