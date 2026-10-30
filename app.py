"""Root entry point for Render's native Python runtime.

The application lives in ``backend/``. This shim lets the Render Blueprint use the
repository root as the service root while keeping ``backend/`` canonical.
"""

from pathlib import Path
import runpy
import sys


BACKEND_ROOT = Path(__file__).resolve().parent / "backend"
BACKEND_ENTRYPOINT = BACKEND_ROOT / "app.py"

if __name__ == "__main__":
    sys.path.insert(0, str(BACKEND_ROOT))
    runpy.run_path(str(BACKEND_ENTRYPOINT), run_name="__main__")
