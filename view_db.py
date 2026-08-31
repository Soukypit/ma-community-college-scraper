"""
Quick SQLite viewer for db/courses.db.

INSTRUCTIONS:
    python view_db.py                          List all tables with row counts
    python view_db.py --table equivalencies    Preview a table (first 20 rows)
    python view_db.py --table equivalencies --limit 50
    python view_db.py --table equivalencies --where "match_type = 'strong'"
"""

import argparse
import os
import sqlite3

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "courses.db")


def list_tables(conn: sqlite3.Connection) -> None:
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"Tables in {DB_PATH}:\n")
    for (name,) in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name:<28} {count} rows")
    print("\nRun with --table <name> to preview one.")


def preview_table(conn: sqlite3.Connection, table: str, limit: int, where: str | None) -> None:
    query = f"SELECT * FROM {table}"
    if where:
        query += f" WHERE {where}"
    query += f" LIMIT {limit}"

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.max_colwidth", 60)

    df = pd.read_sql(query, conn)
    print(f"{table}  ({len(df)} rows shown)\n")
    print(df)


def main():
    parser = argparse.ArgumentParser(description="Preview tables in db/courses.db")
    parser.add_argument("--table", default=None, help="Table name to preview")
    parser.add_argument("--limit", type=int, default=20, help="Row limit (default: 20)")
    parser.add_argument("--where", default=None, help="Optional SQL WHERE clause (no 'WHERE' keyword)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    if args.table:
        preview_table(conn, args.table, args.limit, args.where)
    else:
        list_tables(conn)

    conn.close()


if __name__ == "__main__":
    main()
