"""Split a large CSV export into parts of at most N megabytes.

Jaldi exports have multi-line quoted fields (the Notes column), so the file is
split on parsed records, never mid-row. Every part repeats the header row and
is a valid CSV on its own.

Usage (from apps/api):
    uv run python scripts/split_csv.py ../../projectDocument/LeadExport-2026-11-10-200542.csv
    uv run python scripts/split_csv.py <file.csv> --max-mb 1 --out-dir ../../projectDocument/parts

Output files contain customer PII; keep them inside projectDocument/ (git-ignored).
"""

import argparse
import csv
import io
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize if sys.maxsize < 2**31 else 2**31 - 1)


def encode_row(row: list[str]) -> bytes:
    """Return one CSV record encoded exactly as it will be written."""
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerow(row)
    return buffer.getvalue().encode("utf-8")


def split_csv(source: Path, out_dir: Path, max_bytes: int) -> list[Path]:
    """Split ``source`` into numbered parts no larger than ``max_bytes`` each.

    Args:
        source: CSV file to split (UTF-8, optional BOM).
        out_dir: Folder for the parts; created if missing.
        max_bytes: Size limit per part, header included.

    Returns:
        Paths of the parts written, in order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = encode_row(next(reader))
        current: bytearray | None = None
        for row in reader:
            record = encode_row(row)
            if current is not None and len(current) + len(record) > max_bytes:
                parts.append(_write_part(out_dir, source.stem, len(parts) + 1, current))
                current = None
            if current is None:
                current = bytearray(b"\xef\xbb\xbf" + header)
            current += record
        if current is not None:
            parts.append(_write_part(out_dir, source.stem, len(parts) + 1, current))
    return parts


def _write_part(out_dir: Path, stem: str, number: int, data: bytearray) -> Path:
    path = out_dir / f"{stem}_part{number:02d}.csv"
    path.write_bytes(data)
    return path


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--max-mb", type=float, default=1.0)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out_dir or args.source.parent / f"{args.source.stem}_parts"
    parts = split_csv(args.source, out_dir, int(args.max_mb * 1024 * 1024))
    for part in parts:
        rows = sum(1 for _ in csv.reader(part.open(encoding="utf-8-sig", newline=""))) - 1
        print(f"{part}  {part.stat().st_size / 1024:,.0f} KB  {rows:,} rows")


if __name__ == "__main__":
    main()
