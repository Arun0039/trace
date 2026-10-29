"""Root entry point so Render's Python runtime can start the backend."""

from pathlib import Path
import runpy
import sys


BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
BACKEND_ENTRYPOINT = BACKEND_ROOT / "app.py"

if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND_ROOT))
    runpy.run_path(str(BACKEND_ENTRYPOINT), run_name="__main__")
