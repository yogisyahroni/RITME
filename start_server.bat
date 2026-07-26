@echo off
echo Mengaktifkan Python 3.11 Environment...
call venv_311\Scripts\activate.bat

:loop
echo Menjalankan RITME Server on port %PORT%...
set PORT=8585
python -m uvicorn server:app --port %PORT% --reload

if %ERRORLEVEL% NEQ 0 (
    echo Server crash! Restarting in 5 seconds...
    ping 127.0.0.1 -n 6 > nul
    goto loop
)
pause
