@echo off
echo Fixing Python Dependencies and installing latest NumPy...
python -m pip install --upgrade pip setuptools wheel
pip install "numpy>=2.0.0" --only-binary=:all:
pip install -r requirements.txt
echo Setup complete! You can now run python clipper.py
pause
