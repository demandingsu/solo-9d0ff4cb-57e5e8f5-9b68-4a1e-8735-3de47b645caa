"""ASGI entry point compatible with ``uvicorn main:app`` and ``app.main:app``."""

from app.main import app, create_app

__all__ = ["app", "create_app"]
