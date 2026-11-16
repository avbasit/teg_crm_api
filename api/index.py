"""Vercel serverless entry point.

``vercel.json`` uses ``builds`` + ``routes`` (not ``rewrites``) to send every
request here: with a rewrite, Vercel passes ``/api/index`` as the path and
FastAPI can't match any route.
"""

from app.main import app

__all__ = ["app"]
