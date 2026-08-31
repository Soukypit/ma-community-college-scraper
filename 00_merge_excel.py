"""
PHASE 1 - STEP 1: Merge all college Excel files in "Final data pipeline/" into a single SQLite database.

INSTRUCTIONS:
1. Install dependencies:
       pip install pandas openpyxl

2. Excel files live in ./Final data pipeline/, e.g.:
       transfer_courses_20260729_122544 ( Bunker Hill Community College).xlsx

   college_key is derived from the "College" column inside each file, not the
   filename — so naming/timestamp variations across scrapes don't matter.

3. Run this script:
       python 01_merge_excel.py

4. Output: ./db/courses.db (SQLite database with a `community_courses` table)

5. Open the DB in DB Browser for SQLite (free tool) or query it with Python to verify.
"""

import os
import re
import sqlite3
import pandas as pd

# ── CONFIG ────────────────────────────────────────────────────────────────
RAW_DIR = os.path.join(os.path.dirname(__file__), "Final data pipeline")
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "courses.db")

# Map filename suffixes → human-readable college names (fallback only —
# the "College" column inside each file is used first when present).
COLLEGE_NAME_MAP = {
    "bhcc":            "Bunker Hill Community College",
    "bristol":         "Bristol Community College",
    "capecod":         "Cape Cod Community College",
    "holyoke":         "Holyoke Community College",
    "massasoit":       "Massasoit Community College",
    "massbay":         "MassBay Community College",
    "mccc":            "Middlesex Community College",
    "mwcc":            "Mount Wachusett Community College",
    "northernessex":   "Northern Essex Community College",
    "northshore":      "North Shore Community College",
    "quinsigamond":    "Quinsigamond Community College",
    "roxbury":         "Roxbury Community College",
    "springfieldtech": "Springfield Technical Community College",
    "umassboston":     "University of Massachusetts Boston",
    "umasslowell":     "University of Massachusetts Lowell (GPS)",
    "umassamherst":    "University of Massachusetts Amherst",
    "umassdartmouth":  "University of Massachusetts Dartmouth",
}

# Reverse lookup: full college name (lowercased) -> short key.
# Built from COLLEGE_NAME_MAP so the key is driven by the "College" column
# inside each file, not by fragile filename parsing.
NAME_TO_KEY = {name.lower(): key for key, name in COLLEGE_NAME_MAP.items()}


def slugify(name: str) -> str:
    """Fallback key for a college name with no entry in COLLEGE_NAME_MAP."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())

# Actual columns found in every "Final Version" file:
# ['College', 'Code', 'Title', 'Credits', 'Description', 'Prerequisites']
COLUMN_MAP = {
    "course_code":   ["code", "course_code", "course id", "id"],
    "course_title":  ["title", "course_title", "name", "course name"],
    "description":   ["description", "desc", "course description"],
    "credits":       ["credits", "credit hours", "units", "hrs"],
    "department":    ["department", "dept", "subject", "division"],
    "prerequisites": ["prerequisites", "prereqs", "prereq"],
}
# ─────────────────────────────────────────────────────────────────────────


def extract_college_key(filename: str) -> str:
    """Pull the college suffix out of a filename like transfer_courses_title_cleaned_bristol.xlsx"""
    name = os.path.splitext(filename)[0]                   # strip .xlsx
    parts = name.split("_")
    return parts[-1].lower()                               # last segment = college key


def resolve_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first candidate column name that exists in df (case-insensitive)."""
    lower_cols = {c.lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lower_cols:
            return lower_cols[candidate.lower()]
    return None


def load_and_normalize(filepath: str, fallback_key: str) -> pd.DataFrame:
    """Read one Excel file, rename columns to a standard schema, add college info."""
    df = pd.read_excel(filepath, dtype=str)
    df.columns = df.columns.str.strip()

    normalized = pd.DataFrame(index=df.index)

    college_col = resolve_column(df, ["college", "college_name", "institution"])
    if college_col:
        college_name = df[college_col].str.strip()
        # Every row in a single file shares the same college, so this is one value.
        resolved_name = college_name.dropna().iloc[0] if college_name.notna().any() else None
    else:
        resolved_name = COLLEGE_NAME_MAP.get(fallback_key, fallback_key)
        college_name = resolved_name

    college_key = NAME_TO_KEY.get((resolved_name or "").lower()) or slugify(resolved_name or fallback_key)

    normalized["college_key"]  = college_key
    normalized["college_name"] = college_name

    for standard_col, candidates in COLUMN_MAP.items():
        matched = resolve_column(df, candidates)
        normalized[standard_col] = df[matched].str.strip() if matched else None

    return normalized


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Basic cleaning: normalizes course codes & titles. See 02_clean_normalize.py for the full pipeline."""
    if "course_code" in df.columns:
        df["course_code"] = (
            df["course_code"]
            .str.upper()
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )

    if "course_title" in df.columns:
        df["course_title"] = df["course_title"].str.strip().str.title()

    if "credits" in df.columns:
        df["credits"] = pd.to_numeric(df["credits"], errors="coerce")

    df = df.dropna(how="all")
    return df


def save_to_sqlite(df: pd.DataFrame, db_path: str) -> None:
    """Write the merged dataframe to SQLite."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)

    conn.execute("DROP TABLE IF EXISTS community_courses")
    conn.execute("""
        CREATE TABLE community_courses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            college_key   TEXT,
            college_name  TEXT,
            course_code   TEXT,
            course_title  TEXT,
            description   TEXT,
            credits       REAL,
            department    TEXT,
            prerequisites TEXT
        )
    """)

    df.to_sql("community_courses", conn, if_exists="append", index=False)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM community_courses").fetchone()[0]
    print(f"Saved {count} total rows to {db_path}")
    conn.close()


def main():
    all_frames = []

    excel_files = [f for f in os.listdir(RAW_DIR) if f.endswith(".xlsx") and not f.startswith("~$")]
    if not excel_files:
        print(f"No Excel files found in {RAW_DIR}. Did you copy them there?")
        return

    for filename in sorted(excel_files):
        fallback_key = extract_college_key(filename)
        filepath     = os.path.join(RAW_DIR, filename)

        df = load_and_normalize(filepath, fallback_key)
        resolved_name = df["college_name"].iloc[0] if len(df) else fallback_key
        print(f"  Loading {filename}  ->  {resolved_name}  [{df['college_key'].iloc[0] if len(df) else fallback_key}]")

        df = clean_dataframe(df)
        all_frames.append(df)

    merged = pd.concat(all_frames, ignore_index=True)
    print(f"\nTotal rows merged: {len(merged)}")

    key_counts = merged.groupby(["college_key", "college_name"]).size()
    print(f"\nColleges merged ({len(key_counts)}):")
    for (key, name), n in key_counts.items():
        print(f"  {key:<16} {name:<45} {n}")

    save_to_sqlite(merged, DB_PATH)


if __name__ == "__main__":
    main()
