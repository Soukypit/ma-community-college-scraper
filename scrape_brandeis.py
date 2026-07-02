#!/usr/bin/env python3
"""
Brandeis University Course Scraper
Scrapes the class schedule from https://www.brandeis.edu/registrar/schedule/search

Strategy
--------
Phase 1 — paginate through search results (50 rows/page), collecting unique
           courses by crse_id (the popup ID).  Multiple sections of the same
           course share a crse_id, so each course is only fetched once.
Phase 2 — fetch the definition popup for each unique course to extract the
           description (and prerequisites, when present).

Note: the search form requires a day/time filter.  Mon–Fri 7 AM–10:30 PM
covers all regular sections.  Async/online-only courses with no scheduled
meeting day will not appear.  Credits are not exposed by this interface.

Run
---
    python scrape_brandeis.py                   # Fall 2026 (default)
    python scrape_brandeis.py --term 1261       # Spring 2026
    python scrape_brandeis.py --out my_file.xlsx

Term codes (shown in the Term dropdown on the search page):
    1263  Fall 2026
    1262  Summer 2026
    1261  Spring 2026
    1253  Fall 2025
"""

import argparse
import re
import time
from datetime import datetime

import openpyxl
import requests
from bs4 import BeautifulSoup
from openpyxl.styles import Alignment, Font, PatternFill

# ── CONFIG ────────────────────────────────────────────────────────────────────

BASE_URL     = "https://www.brandeis.edu/registrar/schedule"
DEFAULT_TERM = "1263"   # Fall 2026
REQUEST_DELAY = 0.5     # seconds between HTTP requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TransferResearchBot/1.0; "
        "+https://brandeis.edu/transfer)"
    )
}

# ── HTTP HELPER ───────────────────────────────────────────────────────────────

def get(url: str, **kwargs) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
    r.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return r


def make_soup(r: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(r.text, "lxml")


# ── SCRAPER ───────────────────────────────────────────────────────────────────

def scrape(term: str) -> list[dict]:
    base_params: dict = {
        "strm":       term,
        "order":      "class",
        "time":       "time",
        "day":        ["mon", "tues", "wed", "thurs", "fri"],
        "start_time": "07:00:00",
        "end_time":   "22:30:00",
        "search":     "Search",
        "subsequent": "1",
    }

    # ── Phase 1: collect unique courses across all result pages ────────────────
    seen: dict = {}  # crse_id -> (code, title, popup_path)
    page = 1
    total_pages: int | None = None

    while True:
        params = {**base_params, "page": "" if page == 1 else str(page)}
        try:
            r = get(f"{BASE_URL}/search", params=params)
            s = make_soup(r)
        except Exception as e:
            print(f"  [BRD] page {page} error: {e}")
            break

        tables = s.find_all("table")
        if len(tables) < 5:
            print(f"  [BRD] unexpected page structure on page {page}")
            break
        results_table = tables[4]

        if total_pages is None:
            page_nums = []
            for a in s.find_all("a"):
                mm = re.search(r"changePage\('(\d+)'\)", str(a.get("href", "")))
                if mm:
                    page_nums.append(int(mm.group(1)))
            total_pages = max(page_nums) if page_nums else 1
            print(f"  [BRD] {total_pages} result pages …")

        for row in results_table.find_all("tr")[1:]:  # skip header
            a = row.find("a", class_="def")
            if not a:
                continue
            code = a.get("name", "").strip()
            href = str(a.get("href", ""))
            m = re.search(r"popUp\('(course\?[^']+)'", href)
            if not m:
                continue
            popup_path = m.group(1)
            m2 = re.search(r"crse_id=([^&]+)", popup_path)
            if not m2:
                continue
            crse_id = m2.group(1)
            if crse_id in seen:
                continue

            tds = row.find_all("td")
            strong = tds[1].find("strong") if len(tds) >= 2 else None
            title = strong.get_text(" ", strip=True) if strong else ""
            seen[crse_id] = (code, title, popup_path)

        print(f"  [BRD] page {page}/{total_pages}: {len(seen)} unique courses so far")
        if page >= total_pages:
            break
        page += 1

    # ── Phase 2: fetch description popup for each unique course ────────────────
    total = len(seen)
    print(f"  [BRD] fetching descriptions for {total} unique courses …")
    courses = []

    for i, (crse_id, (code, title, popup_path)) in enumerate(seen.items(), 1):
        desc = ""
        prereqs = ""
        try:
            rd = get(f"{BASE_URL}/{popup_path}")
            sd = make_soup(rd)
            coursepage = sd.find(id="coursepage")
            if coursepage:
                raw = " ".join(
                    p.get_text(" ", strip=True)
                    for p in coursepage.find_all("p")
                    if p.get_text(strip=True)
                )
                desc = raw
                mp = re.search(r"Prerequisite[s]?:\s*(.+)", raw, re.I | re.S)
                if mp:
                    prereq_text = mp.group(1).strip()
                    end = re.search(r"\.\s+[A-Z(]", prereq_text)
                    prereqs = prereq_text[: end.start()].strip() if end else prereq_text
        except Exception as e:
            print(f"    [{i}/{total}] {code} error: {e}")

        if i % 50 == 0:
            print(f"    {i}/{total} done …")

        courses.append({
            "Code":          code,
            "Title":         title,
            "Credits":       "",
            "Description":   desc,
            "Prerequisites": prereqs,
        })

    print(f"  [BRD] total courses scraped: {len(courses)}")
    return courses


# ── EXCEL WRITER ──────────────────────────────────────────────────────────────

COLS   = ["Code", "Title", "Credits", "Description", "Prerequisites"]
WIDTHS = [14,     42,      9,         80,            50]


def _header_style(cell) -> None:
    cell.font      = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    cell.fill      = PatternFill("solid", fgColor="003478")   # Brandeis blue
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _body_style(cell, even: bool) -> None:
    cell.font      = Font(name="Arial", size=10)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    if even:
        cell.fill = PatternFill("solid", fgColor="D6E4F0")


def write_xlsx(courses: list[dict], path: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Brandeis Courses"

    for ci, (col, w) in enumerate(zip(COLS, WIDTHS), 1):
        _header_style(ws.cell(row=1, column=ci, value=col))
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = w
    ws.row_dimensions[1].height = 22

    for ri, course in enumerate(courses, 2):
        for ci, col in enumerate(COLS, 1):
            _body_style(
                ws.cell(row=ri, column=ci, value=str(course.get(col, ""))),
                ri % 2 == 0,
            )

    wb.save(path)
    print(f"\n✓  Saved {len(courses)} courses → {path}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Brandeis course schedule")
    parser.add_argument(
        "--term", default=DEFAULT_TERM,
        help=f"SPIRE term code (default: {DEFAULT_TERM} = Fall 2026)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output .xlsx filename (default: brandeis_courses_<timestamp>.xlsx)",
    )
    args = parser.parse_args()

    out_path = args.out or f"brandeis_courses_{datetime.now():%Y%m%d_%H%M%S}.xlsx"

    print(f"Scraping Brandeis University — term {args.term}")
    print("-" * 60)
    courses = scrape(args.term)
    write_xlsx(courses, out_path)


if __name__ == "__main__":
    main()
