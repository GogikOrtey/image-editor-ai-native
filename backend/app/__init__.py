"""Expose the Image Editor backend package.

The package is imported by Uvicorn and the test suite through ``app.main``.
Runtime state is intentionally kept in request scope; provider API keys are never stored here.
"""

__all__ = []
