# server/app.py — OpenEnv multi-mode deployment entry point
# This re-exports the FastAPI app from app.main for openenv validate compatibility.
from app.main import app

__all__ = ["app"]
