"""CSV for downloads: UTF-8 with a BOM, so Excel shows Arabic and Urdu names correctly."""

import csv
import io
from collections.abc import Iterable
from typing import Any


def to_csv(header: list[str], rows: Iterable[list[Any]]) -> str:
    """CSV text (``None`` becomes an empty cell)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if value is None else _safe(value) for value in row])
    return "﻿" + buffer.getvalue()


def _safe(value: Any) -> Any:
    """Stop spreadsheet formula injection: text starting with = + - @ is prefixed with '."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        if not _is_number(value):
            return "'" + value
    return value


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True
