"""Experience API (Fast API). `uvicorn neutail.api:app` serves it."""

from neutail.api.app import app  # noqa: F401

__all__ = ["app"]