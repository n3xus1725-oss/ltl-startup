"""FastAPI Application Root Entrypoint for Production and Vercel."""

import sys
from pathlib import Path

# Ensure root directory is on sys.path
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from apps.api.main import app

__all__ = ["app"]
