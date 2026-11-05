@echo off
cd /d "C:\AI-Stream-Clipper"

:: Double-click this file to run the app. It starts Streamlit in the
:: background with NO terminal window and shows a small GUI with a big
:: STOP button, so nobody needs command-line knowledge.

setlocal

:: Locate pythonw.exe (the windowless Python) — this is what makes
:: the terminal stay hidden. Search several likely places.
set "PYW="
for %%p in ("%~dp0" "..\..") do (
    for %%q in ("%%~fp\pythonw.exe" "%%~fp\Scripts\pythonw.exe") do (
        if exist "%%~q" set "PYW=%%~q"
    )
)
if not defined PYW (
    for %%p in (
        "%PYTHON_EXE%"
        "C:\Python314\pythonw.exe"
        "C:\Python311\pythonw.exe"
        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python314\pythonw.exe"
        "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\pythonw.exe"
    ) do (
        if exist "%%~p" set "PYW=%%~p"
    )
)

if defined PYW (
    "%PYW%" launcher.py
) else (
    echo pythonw.exe not found - running with a console window instead.
    python launcher.py
)
exit /b