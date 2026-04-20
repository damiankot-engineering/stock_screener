@echo off
setlocal enabledelayedexpansion
set N=10

for /L %%i in (1,1,%N%) do (
    echo Running iteration %%i
    python main.py --strategy deep_value --n 80 --multi-shot
    timeout /t 2
)
pause