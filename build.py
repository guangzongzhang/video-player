#!/usr/bin/env python3
"""Build script: generates icon.ico then packages VideoPlayer.exe via PyInstaller."""

import sys
import subprocess
import shutil
import os


def run(cmd: str, label: str = ""):
    if label:
        print(f"[{label}]")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"\nFailed. Command was:\n  {cmd}")
        sys.exit(1)


print("=== VideoPlayer Build ===\n")

# 1. Dependencies
run("pip install PyQt6 Pillow keyboard pyinstaller -q", "1/4  Installing dependencies")

# 2. Icon
run("python icon_gen.py", "2/4  Generating icon")

# 3. PyInstaller
run(
    "pyinstaller --onefile --windowed "
    "--icon=icon.ico --name=VideoPlayer --noconfirm main.py",
    "3/4  Packaging (please wait...)",
)

# 4. Cleanup
print("[4/4  Cleaning up]")
for p in ["build", "__pycache__"]:
    if os.path.isdir(p):
        shutil.rmtree(p)
if os.path.isfile("VideoPlayer.spec"):
    os.remove("VideoPlayer.spec")

print("\n=== Done!  Output: dist\\VideoPlayer.exe ===\n")

if sys.platform == "win32":
    os.startfile("dist")
