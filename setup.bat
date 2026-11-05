@echo off
cd /d "C:\AI-Stream-Clipper"

echo ============================================
echo  AI Stream Clipper - Setup
echo ============================================
echo.
echo Installing Python dependencies (this takes a few minutes the first time)...
echo.

python -m pip install --upgrade pip setuptools wheel

:: Pillow is needed for caption rendering (ImageDraw/ImageFont)
pip install pillow

:: Core dependencies
pip install -r requirements.txt

echo.
echo ============================================
echo  Setup complete!
echo.
echo  Double-click START_APP.bat to run the app.
echo ============================================
pause