"""Helper for migrations that execute a hand-written SQL file.

The SQL is sent through the raw psycopg connection (no bind parameters), so it
can contain ``$$`` function bodies, ``%`` in ``format()`` calls and ``:`` in
literals without SQLAlchemy treating them as placeholders. It runs inside the
same transaction Alembic opened.
"""

from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).resolve().parent / "sql"


def run_sql_file(file_name: str) -> None:
    """Execute ``migrations/sql/<file_name>`` on the current migration connection.

    Args:
        file_name: Name of the file inside ``migrations/sql``.
    """
    sql = (SQL_DIR / file_name).read_text(encoding="utf-8")
    op.get_bind().connection.driver_connection.execute(sql)
