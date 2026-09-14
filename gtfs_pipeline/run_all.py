"""
Runs the full pipeline: download -> fix -> validate.
Usage: python run_all.py
"""
import subprocess
import sys

STEPS = ["download.py", "fix.py", "validate.py"]

for step in STEPS:
    print(f"\n{'='*60}\nRunning {step}\n{'='*60}")
    result = subprocess.run([sys.executable, step])
    if result.returncode != 0:
        print(f"\n{step} exited with an error (code {result.returncode}). Stopping.")
        sys.exit(result.returncode)

print("\nDone. Final feed: build/gtfs.zip")
