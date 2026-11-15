"""Vercel serverless entry point. ``vercel.json`` routes every request here."""

from app.main import app

__all__ = ["app"]
