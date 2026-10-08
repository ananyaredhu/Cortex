"""The HTTP API (docs/02-contracts/api.md 0.2). `create_app(runtime)` builds it; `brain.api.main` serves it."""
from .app import create_app

__all__ = ["create_app"]
