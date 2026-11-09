@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto python
py -3 tools\preview.py --open %*
goto done
:python
python tools\preview.py --open %*
:done
if errorlevel 1 pause
