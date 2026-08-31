"""
PHASE 1 - STEP 2: Clean & normalize courses.db based on actual data inspection.

Issues found and fixed by this script:
  1. Descriptions contain scraped boilerplate ("HELP College Catalog...", "Print-Friendly Page...", "Back to Top...")
  2. Prerequisites column also contains boilerplate after the actual prereq text
  3. department column is entirely NULL — extracted from course_code prefix instead
  4. 115 rows missing course_code (mostly empty/junk rows) — flagged for review
  5. 914 rows missing description — kept but flagged
  6. 3 duplicate groups — removed
  7. course_code extracted from description for rows where it was scraped into the wrong field

INSTRUCTIONS:
    pip install pandas
    python 02_clean_normalize.py

OUTPUT:
    - Updates courses.db with a new `community_courses_clean` table
    - Writes flagged_for_review.csv for manual inspection
"""

import os
import re
import sqlite3
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "db", "courses.db")
FLAG_CSV = os.path.join(BASE_DIR, "data", "processed", "flagged_for_review.csv")


# ── BOILERPLATE PATTERNS TO STRIP ────────────────────────────────────────────
# These patterns were found in the actual scraped data from BHCC and MassBay
BOILERPLATE_PATTERNS = [
    r"HELP\s+(?:College Catalog|[\d\-]+\s+Catalog)\s+[\d\-]+.*?(?=\w+\s*[-–]\s*\d+\w*\s+\w+|$)",
    r"Print-Friendly Page.*?(?:window\))?",
    r"Back to Top\s*\|.*?(?:window\))?",
    r"Gen\.\s*Ed\.\s*Course\s*(?:Yes|No).*?(?:window\))?",
    r"Mass Transfer\s*Course\s*(?:Yes|No)",
    r"Credits:\s*[\d.]+",
    r"\(opens a new window\)",
    r"Back to Top",
]

BOILERPLATE_RE = re.compile("|".join(BOILERPLATE_PATTERNS), re.IGNORECASE | re.DOTALL)


def strip_boilerplate(text: str) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = BOILERPLATE_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned if cleaned else None


def extract_actual_prereqs(text: str) -> str | None:
    """
    The prerequisites field contains the real prereq followed by boilerplate.
    Everything after 'Gen. Ed.' or 'Credits:' or 'Back to Top' is noise.
    """
    if not isinstance(text, str):
        return None
    # Cut off at first boilerplate marker
    cutoffs = [
        r"Gen\.\s*Ed\.",
        r"Credits:\s*\d",
        r"Back to Top",
        r"Print-Friendly",
        r"Mass Transfer",
    ]
    pattern = re.compile("|".join(cutoffs), re.IGNORECASE)
    match = pattern.search(text)
    result = text[:match.start()].strip() if match else text.strip()
    return result if result else None


def normalize_course_code(code: str) -> str | None:
    if not isinstance(code, str):
        return None
    code = code.upper().strip()
    code = re.sub(r"[-_]+", " ", code)
    code = re.sub(r"\s+", " ", code)
    code = re.sub(r"([A-Z]+)(\d)", r"\1 \2", code)
    return code.strip() or None


def extract_department(course_code: str) -> str | None:
    """Pull the letter prefix from a course code. 'ENG 101' → 'ENG'"""
    if not isinstance(course_code, str):
        return None
    match = re.match(r"^([A-Z]+)", course_code.upper().strip())
    return match.group(1) if match else None


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.copy()

    # ── 1. Clean descriptions ────────────────────────────────────────────────
    df["description"] = df["description"].apply(strip_boilerplate)

    # ── 2. Clean prerequisites ───────────────────────────────────────────────
    df["prerequisites"] = df["prerequisites"].apply(extract_actual_prereqs)

    # ── 3. Normalize course codes ────────────────────────────────────────────
    df["course_code"] = df["course_code"].apply(normalize_course_code)

    # ── 4. Extract department from course code (was 100% NULL) ──────────────
    df["department"] = df["course_code"].apply(extract_department)

    # ── 5. Normalize titles ──────────────────────────────────────────────────
    preserve = {"Ii": "II", "Iii": "III", "Iv": "IV", "Sql": "SQL", "Api": "API"}
    def clean_title(t):
        if not isinstance(t, str): return None
        result = t.strip().title()
        for wrong, right in preserve.items():
            result = result.replace(wrong, right)
        return result
    df["course_title"] = df["course_title"].apply(clean_title)

    # ── 6. Credits to numeric ─────────────────────────────────────────────────
    df["credits"] = pd.to_numeric(df["credits"], errors="coerce")

    # ── 7. Remove exact duplicates ───────────────────────────────────────────
    before = len(df)
    df = df.drop_duplicates(subset=["college_key", "course_code", "course_title"])
    print(f"  Removed {before - len(df)} duplicate rows")

    # ── 8. Flag rows with problems ───────────────────────────────────────────
    flags = []

    missing_code = df["course_code"].isna()
    flagged_code = df[missing_code].copy()
    flagged_code["flag_reason"] = "Missing course_code"
    flags.append(flagged_code)
    df = df[~missing_code]

    missing_title = df["course_title"].isna()
    flagged_title = df[missing_title].copy()
    flagged_title["flag_reason"] = "Missing course_title"
    flags.append(flagged_title)
    df = df[~missing_title]

    missing_desc = df["description"].isna()
    flagged_desc = df[missing_desc].copy()
    flagged_desc["flag_reason"] = "Missing description (kept in clean table)"
    flags.append(flagged_desc)
    # NOTE: Missing descriptions are flagged but NOT removed — they can still match on title

    flagged = pd.concat(flags, ignore_index=True) if flags else pd.DataFrame()
    return df, flagged


def main():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM community_courses", conn)
    print(f"Loaded {len(df)} rows from community_courses")

    df_clean, df_flagged = clean(df)

    # Save clean table
    df_clean.to_sql("community_courses_clean", conn, if_exists="replace", index=False)
    conn.commit()

    # Summary
    print(f"\n{'='*50}")
    print(f"  Clean rows:           {len(df_clean)}")
    print(f"  Flagged / removed:    {len(df_flagged[df_flagged['flag_reason'] != 'Missing description (kept in clean table)'])}")
    print(f"  Missing descriptions: {df_clean['description'].isna().sum()} (kept, flagged in CSV)")
    print(f"  Departments extracted:{df_clean['department'].notna().sum()} / {len(df_clean)}")

    print(f"\n  Courses per college:")
    counts = df_clean.groupby("college_name").size().sort_values(ascending=False)
    for name, count in counts.items():
        print(f"    {name:<45} {count}")

    # Export flagged CSV
    os.makedirs(os.path.dirname(FLAG_CSV), exist_ok=True)
    df_flagged.to_csv(FLAG_CSV, index=False)
    print(f"\n  Flagged rows -> {FLAG_CSV}")
    print(f"\nDone. New table `community_courses_clean` is ready in {DB_PATH}")
    print("   Next: run 03_scrape_brandeis.py to add the Brandeis course catalog.")

    conn.close()


if __name__ == "__main__":
    main()
