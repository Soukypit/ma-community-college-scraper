"""
PHASE 1 - STEP 3b: Load scraped Brandeis courses into courses.db.

This script reads the Excel file produced by scrape_brandeis.py
(columns: Code, Title, Credits, Description, Prerequisites)
and writes it to the `brandeis_courses` table in courses.db.

INSTRUCTIONS:
    1. Place your brandeis_courses_YYYYMMDD_HHMMSS.xlsx file in data/raw/
    2. Run:
           python 03b_load_brandeis.py
       Or pass the file path explicitly:
           python 03b_load_brandeis.py --file data/raw/brandeis_courses_20260717_143022.xlsx
"""

import argparse
import glob
import os
import re
import sqlite3

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "db", "courses.db")
RAW_DIR  = os.path.join(BASE_DIR, "Brandeis Courses")


# ── CLEANING ──────────────────────────────────────────────────────────────────

def normalize_course_code(code: str) -> str | None:
    """'COSI12B' or 'COSI-12B' → 'COSI 12B'"""
    if not isinstance(code, str):
        return None
    code = code.upper().strip()
    code = re.sub(r"[-_]+", " ", code)
    code = re.sub(r"([A-Z]+)(\d)", r"\1 \2", code)
    code = re.sub(r"\s+", " ", code)
    return code.strip() or None


def extract_department(code: str) -> str | None:
    if not isinstance(code, str):
        return None
    m = re.match(r"^([A-Z]+)", code.upper().strip())
    return m.group(1) if m else None


def clean_description(text: str) -> str | None:
    """Strip any residual boilerplate that crept into popup descriptions."""
    if not isinstance(text, str) or not text.strip():
        return None
    # The popup page sometimes prepends the course code + title before the body
    # e.g. "COSI 12B Data Structures This course covers…"
    # We keep everything — it's mostly clean from the scraper already.
    return text.strip()


def extract_prereqs(text: str) -> str | None:
    """
    The scraper already extracts Prerequisites into its own column,
    but the description sometimes embeds them too.  We trust the
    dedicated column; this just cleans whitespace.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    return text.strip()


def clean_credits(val) -> float | None:
    """Credits come back as empty string from the scraper (not exposed by the UI)."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── LOADER ────────────────────────────────────────────────────────────────────

def find_brandeis_file(raw_dir: str) -> str | None:
    """Auto-detect the most recent Brandeis .xlsx in raw_dir (any naming convention)."""
    patterns = ["brandeis_courses*.xlsx", "*randeis*.xlsx"]
    matches = []
    for pattern in patterns:
        matches.extend(glob.glob(os.path.join(raw_dir, pattern)))
    matches = sorted(set(matches), key=os.path.getmtime, reverse=True)
    return matches[0] if matches else None


def load(filepath: str) -> pd.DataFrame:
    df = pd.read_excel(filepath, dtype=str)
    df.columns = df.columns.str.strip()
    print(f"  Read {len(df)} rows from {os.path.basename(filepath)}")
    print(f"  Columns found: {list(df.columns)}")

    # Rename scraper columns → our standard schema
    rename = {
        "Code":          "course_code",
        "Title":         "course_title",
        "Credits":       "credits",
        "Description":   "description",
        "Prerequisites": "prerequisites",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    # Apply cleaning
    df["course_code"]   = df["course_code"].apply(normalize_course_code)
    df["department"]    = df["course_code"].apply(extract_department)
    df["course_title"]  = df.get("course_title", pd.Series(dtype=str)).str.strip().str.title()
    df["description"]   = df.get("description",  pd.Series(dtype=str)).apply(clean_description)
    df["prerequisites"] = df.get("prerequisites", pd.Series(dtype=str)).apply(extract_prereqs)
    df["credits"]       = df.get("credits",       pd.Series(dtype=str)).apply(clean_credits)

    # Drop fully empty rows and duplicates
    df = df.dropna(subset=["course_code", "course_title"], how="all")
    before = len(df)
    df = df.drop_duplicates(subset=["course_code"])
    print(f"  Removed {before - len(df)} duplicate course codes")

    # Keep only the standard columns
    cols = ["course_code", "course_title", "credits", "description", "prerequisites", "department"]
    df = df[[c for c in cols if c in df.columns]]
    return df


def save(df: pd.DataFrame, db_path: str) -> None:
    conn = sqlite3.connect(db_path)

    conn.execute("DROP TABLE IF EXISTS brandeis_courses")
    conn.execute("""
        CREATE TABLE brandeis_courses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            course_code   TEXT,
            course_title  TEXT,
            credits       REAL,
            description   TEXT,
            prerequisites TEXT,
            department    TEXT
        )
    """)

    df.to_sql("brandeis_courses", conn, if_exists="append", index=False)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM brandeis_courses").fetchone()[0]
    dept_count = conn.execute("SELECT COUNT(DISTINCT department) FROM brandeis_courses").fetchone()[0]

    print(f"\n{'='*50}")
    print(f"  {count} Brandeis courses saved to `brandeis_courses`")
    print(f"  {dept_count} unique departments")

    # Quick sample
    print("\n  Sample rows:")
    rows = conn.execute(
        "SELECT course_code, course_title, department, credits FROM brandeis_courses LIMIT 6"
    ).fetchall()
    for r in rows:
        print(f"    {r[0]:<12} {r[1]:<40} {r[2]:<8} {r[3]}")

    # Coverage check
    missing_desc = conn.execute(
        "SELECT COUNT(*) FROM brandeis_courses WHERE description IS NULL OR description = ''"
    ).fetchone()[0]
    print(f"\n  Missing descriptions: {missing_desc} / {count}")
    print(f"\n  Tables now in database:")
    for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
        print(f"    {t[0]:<35} {n} rows")

    conn.close()


# ── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Load Brandeis scraped Excel -> SQLite")
    parser.add_argument("--file", default=None, help=f"Path to the Brandeis .xlsx file (default: newest match in {RAW_DIR})")
    parser.add_argument("--db",   default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    args = parser.parse_args()

    filepath = args.file or find_brandeis_file(RAW_DIR)
    if not filepath or not os.path.exists(filepath):
        print(f"No Brandeis Excel file found.")
        print(f"   Place brandeis_courses_*.xlsx in {RAW_DIR}/")
        print(f"   Or pass --file path/to/file.xlsx")
        return

    print(f"Loading Brandeis courses from: {filepath}")
    df = load(filepath)
    save(df, args.db)
    print(f"\nPhase 1 complete! Database is ready for Phase 2 (embeddings matching).")


if __name__ == "__main__":
    main()
