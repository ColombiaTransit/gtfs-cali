"""
Runs the full pipeline: download -> enrich_stops -> enrich_routes -> fix -> validate.
Usage: python run_all.py
"""
import subprocess
import sys

OPTIONAL_STEPS = {"enrich_stops.py", "enrich_routes.py"}  # best-effort: failure doesn't stop the pipeline

STEPS = ["download.py", "enrich_stops.py", "enrich_routes.py", "fix.py", "validate.py"]

for step in STEPS:
    print(f"\n{'='*60}\nRunning {step}\n{'='*60}")
    result = subprocess.run([sys.executable, step])
    if result.returncode != 0:
        if step in OPTIONAL_STEPS:
            print(f"\n{step} failed (code {result.returncode}) but is optional - continuing. "
                  f"stops.txt will use fix.py's placeholder names instead.")
            continue
        print(f"\n{step} exited with an error (code {result.returncode}). Stopping.")
        sys.exit(result.returncode)

print("\nDone. Final feed: build/gtfs.zip")
