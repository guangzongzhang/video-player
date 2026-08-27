@echo off
python -m pip show PyQt6 >nul 2>&1 || pip install PyQt6 -q
python main.py
