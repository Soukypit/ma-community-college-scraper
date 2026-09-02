"""
PHASE 3: Scrape the Brandeis Bulletin for requirement codes and build a
department-prefix -> Bulletin subject-page lookup.

Feeds two things in streamlit_app.py:
  - The "Official Bulletin" link on each result card (needs to know which
    numbered subject page a department's courses live on).
  - The "Fulfills: QR, SN" requirement badges (needs each course's
    requirement codes, e.g. Quantitative Reasoning, Science, Writing
    Intensive -- see reqcodes.html for the full glossary).

WHY THE PROVISIONAL EDITION:
    Brandeis publishes two live Bulletin editions: the current one
    (2026-2027) and a provisional draft of next year's (2027-2028). As of
    this writing, the CURRENT edition's course-listing pages are broken on
    Brandeis's own site -- every subject page serves a raw, unrendered
    <TMPL_INCLUDE NAME="../includes/subject.html"> tag instead of actual
    course content (verified across multiple departments, not a one-off).
    The provisional edition is the only one that actually renders course
    descriptions and requirement codes right now, so that's what this
    script reads.

    The subject-number scheme itself (e.g. Biology = 700, Computer Science
    = 1400) is identical between editions -- confirmed by diffing both
    editions' subjects/index.html. So the lookup built here is used to
    construct CURRENT-edition Bulletin links in the app (LINK_EDITION
    below), even though the requirement-code *data* comes from provisional.
    Once Brandeis fixes the current edition, flip SCRAPE_EDITION to match
    and re-run.

INSTRUCTIONS:
    python 06_scrape_bulletin.py

OUTPUT:
    bulletin_subjects table     : department prefix -> Bulletin subject-page number
    bulletin_requirements table : Brandeis course code -> requirement code(s)
"""

import argparse
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict

import requests

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "db", "courses.db")
CACHE_PATH = os.path.join(BASE_DIR, "data", "processed", "bulletin_scrape_cache.json")

SCRAPE_EDITION = "provisional"    # only edition with working course content right now
LINK_EDITION   = "2026-2027"      # edition the in-app "Official Bulletin" links point to

BASE_URL  = f"https://www.brandeis.edu/registrar/bulletin/{SCRAPE_EDITION}/courses/subjects"
INDEX_URL = f"{BASE_URL}/index.html"

HEADERS       = {"User-Agent": "Mozilla/5.0 (compatible; ma-college-scraper/1.0)"}
DELAY_SECONDS = 0.4

# Subject index rows: <a href="700.html">Biology</a>
SUBJECT_LINK_RE = re.compile(r'<a[^>]*href="(\d+)\.html"[^>]*>([^<]+)</a>')

# One course entry, e.g.:
#   <strong>BIOL 14a Genetics and Genomics</strong><br/>
#   [ <span class="requirement" title="Quantitative Reasoning">qr</span>
#     <span class="requirement" title="Science">sn</span> ]<br/>
COURSE_RE = re.compile(
    r'<strong>\s*([A-Z]{2,6})\s+(\d+[a-zA-Z]?)\s*([^<]*?)\s*</strong>\s*<br\s*/?>\s*(\[(.*?)\])?',
    re.DOTALL,
)
REQ_SPAN_RE = re.compile(r'<span class="requirement"[^>]*>\s*([a-zA-Z0-9\-]+)\s*</span>')


def normalize_course_code(prefix: str, number: str) -> str:
    """Match the 'PREFIX NUM' format brandeis_courses/equivalencies_v3 already
    use (see normalize_course_code() in 03b_load_brandeis.py)."""
    return f"{prefix.upper()} {number.upper()}"


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def get_subject_list() -> list[tuple[str, str]]:
    """Returns [(subject_number, subject_name), ...] from the subjects index."""
    return SUBJECT_LINK_RE.findall(fetch(INDEX_URL))


def parse_subject_page(html: str):
    """Yields (prefix, number, title, [req_codes]) for every course entry on
    a subject page. A page can list courses from OTHER departments too --
    cross-listed electives, or wholesale on the special requirement
    'subjects' like Oral Communication (9400) -- that's fine, every
    appearance is collected and reconciled by frequency in main()."""
    for m in COURSE_RE.finditer(html):
        prefix, number, title, _bracket, req_block = m.groups()
        codes = [c.lower() for c in REQ_SPAN_RE.findall(req_block)] if req_block else []
        yield prefix, number, title.strip(), codes


def main() -> None:
    print(f"Fetching subject index ({SCRAPE_EDITION} edition)...")
    subjects = get_subject_list()
    print(f"  Found {len(subjects)} subjects")

    course_reqs: dict[str, set] = defaultdict(set)           # "BIOL 14A" -> {"qr", "sn"}
    prefix_page_counts: dict[str, Counter] = defaultdict(Counter)  # "BIOL" -> Counter({"700": 45, "9400": 1})

    for i, (number, name) in enumerate(subjects, 1):
        url = f"{BASE_URL}/{number}.html"
        print(f"  [{i}/{len(subjects)}] {name} ({number}.html)...", end=" ")
        try:
            html = fetch(url)
        except requests.RequestException as e:
            print(f"FAILED: {e}")
            continue

        count = 0
        for prefix, number_part, _title, codes in parse_subject_page(html):
            code = normalize_course_code(prefix, number_part)
            course_reqs[code].update(codes)
            prefix_page_counts[prefix][number] += 1
            count += 1
        print(f"{count} course entries")

        time.sleep(DELAY_SECONDS)

    # Resolve each prefix's "home" subject page: the page it appears on most
    # often. A real department's own page lists every one of its courses; a
    # requirement/category page (Oral Communication, etc.) only lists a
    # scattered handful borrowed from many departments, so it never wins.
    home_page = {prefix: counts.most_common(1)[0][0] for prefix, counts in prefix_page_counts.items()}
    subject_names = dict(subjects)

    print(f"\nResolved {len(home_page)} department prefixes to a home subject page")
    print(f"Collected requirement codes for {sum(1 for v in course_reqs.values() if v)} courses "
          f"(of {len(course_reqs)} distinct courses seen)")

    # Checkpoint to disk before touching the DB -- the scrape (93 requests to
    # Brandeis) is the expensive part; if the DB write fails (e.g. courses.db
    # is open in another program), write_db_from_cache() below can retry the
    # write alone without re-scraping.
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "home_page": home_page,
            "subject_names": subject_names,
            "course_reqs": {code: sorted(reqs) for code, reqs in course_reqs.items()},
        }, f, indent=2)
    print(f"Checkpointed scrape results to {CACHE_PATH}")

    try:
        write_db(home_page, subject_names, course_reqs)
    except sqlite3.OperationalError as e:
        print(f"\nDB write failed ({e}) -- results are safely cached, though.")
        print(f"Close whatever has {DB_PATH} open (DB viewer, another script, "
              f"cloud sync) and retry with:\n    python {os.path.basename(__file__)} --from-cache")


def write_db(home_page: dict, subject_names: dict, course_reqs: dict) -> None:
    # ── WRITE TO DB ───────────────────────────────────────────────────────
    conn = sqlite3.connect(DB_PATH)

    conn.execute("DROP TABLE IF EXISTS bulletin_subjects")
    conn.execute("""
        CREATE TABLE bulletin_subjects (
            prefix         TEXT PRIMARY KEY,
            subject_number TEXT NOT NULL,
            subject_name   TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO bulletin_subjects (prefix, subject_number, subject_name) VALUES (?, ?, ?)",
        [(prefix, number, subject_names.get(number)) for prefix, number in home_page.items()],
    )

    conn.execute("DROP TABLE IF EXISTS bulletin_requirements")
    conn.execute("""
        CREATE TABLE bulletin_requirements (
            brd_code TEXT NOT NULL,
            req_code TEXT NOT NULL,
            PRIMARY KEY (brd_code, req_code)
        )
    """)
    conn.executemany(
        "INSERT INTO bulletin_requirements (brd_code, req_code) VALUES (?, ?)",
        [(code, req) for code, reqs in course_reqs.items() for req in reqs],
    )

    conn.commit()
    n_req_rows = sum(len(v) for v in course_reqs.values())
    print(f"\nSaved bulletin_subjects ({len(home_page)} rows) and "
          f"bulletin_requirements ({n_req_rows} rows) to {DB_PATH}")
    conn.close()


def write_db_from_cache() -> None:
    """Retries only the DB write from the last checkpointed scrape (e.g.
    after 'database is locked' / the file being open elsewhere) without
    re-hitting Brandeis's servers."""
    with open(CACHE_PATH, encoding="utf-8") as f:
        cache = json.load(f)
    course_reqs = {code: set(reqs) for code, reqs in cache["course_reqs"].items()}
    write_db(cache["home_page"], cache["subject_names"], course_reqs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Brandeis Bulletin requirement codes")
    parser.add_argument(
        "--from-cache", action="store_true",
        help=f"Skip scraping and retry only the DB write from {CACHE_PATH}",
    )
    args = parser.parse_args()

    if args.from_cache:
        write_db_from_cache()
    else:
        main()
