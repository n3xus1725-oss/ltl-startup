"""FastAPI Application Root Entrypoint for Production and Vercel."""

import sys
from pathlib import Path

# Ensure root directory is on sys.path
_root = str(Path(__file__).resolve().parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from apps.api.main import app
except Exception as e:
    import os
    import traceback

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    _err_name = type(e).__name__
    _err_msg = str(e)
    _tb = traceback.format_exc()

    app = FastAPI(title="Freight Platform - Diagnostic")

    @app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def diagnostic_fallback(path_name: str = ""):
        return JSONResponse(
            status_code=500,
            content={
                "error": "Startup initialization failed",
                "exception_type": _err_name,
                "exception_message": _err_msg,
                "traceback": _tb.splitlines(),
                "python_version": sys.version,
                "cwd": os.getcwd(),
            },
        )

__all__ = ["app"]
