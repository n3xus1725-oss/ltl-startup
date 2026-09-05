"""Vercel Serverless Entrypoint for FastAPI Application."""

import os
import sys
import traceback
from pathlib import Path

# Ensure repository root is on sys.path
_repo_root = str(Path(__file__).resolve().parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

try:
    from apps.api.main import app
except Exception as e:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    _err_name = type(e).__name__
    _err_str = str(e)
    _tb = traceback.format_exc()

    app = FastAPI(title="Freight Platform - Initialization Error")

    @app.api_route("/{path_name:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def fallback_route(path_name: str):
        return JSONResponse(
            status_code=500,
            content={
                "error": "Serverless function initialization error",
                "exception": _err_name,
                "message": _err_str,
                "traceback": _tb.splitlines(),
                "cwd": os.getcwd(),
                "sys_path": sys.path,
            },
        )

__all__ = ["app"]
