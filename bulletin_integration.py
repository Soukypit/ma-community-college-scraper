"""
Official Bulletin links + core-requirement badges for the Course
Equivalency Simulator.

Reads the bulletin_subjects / bulletin_requirements tables populated by
06_scrape_bulletin.py -- no live network calls at click-time, everything
here is a cached local lookup.

Used by streamlit_app.py:
    from bulletin_integration import (
        get_bulletin_url,
        get_requirement_codes,
        REQUIREMENT_LABELS,
        REQCODES_URL,
    )
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "db" / "courses.db"

# The Bulletin links point at the CURRENT edition -- see 06_scrape_bulletin.py
# for why the *data* comes from the provisional edition instead (current
# edition's course-listing pages are broken on Brandeis's site right now).
# The subject-page numbering is identical between editions, so this link
# will simply start working once Brandeis fixes the current edition.
LINK_EDITION  = "2026-2027"
BULLETIN_BASE = f"https://www.brandeis.edu/registrar/bulletin/{LINK_EDITION}/courses/subjects"
REQCODES_URL  = f"https://www.brandeis.edu/registrar/bulletin/{LINK_EDITION}/courses/reqcodes.html"

# From reqcodes.html. "fys" isn't in Brandeis's published glossary but shows
# up on course listings -- its meaning (First-Year Seminar) is unambiguous.
REQUIREMENT_LABELS = {
    "ca":      "Creative Arts",
    "deis-us": "Diversity, Equity and Inclusion Studies in the U.S.",
    "djw":     "Difference and Justice in the World",
    "dl":      "Digital Literacy",
    "fl":      "Foreign Language",
    "fys":     "First-Year Seminar",
    "hum":     "Humanities",
    "hwl1":    "Health, Wellness, and Life Skills: Navigating Health and Safety",
    "hwl2":    "Health, Wellness, and Life Skills: Mind and Body Balance",
    "hwl3":    "Health, Wellness, and Life Skills: Life Skills",
    "nw":      "Non-Western and Comparative Studies",
    "oc":      "Oral Communications",
    "pe-1":    "Physical Education",
    "qr":      "Quantitative Reasoning",
    "sn":      "Science",
    "ss":      "Social Science",
    "uws":     "University Writing Seminar",
    "wi":      "Writing Intensive",
}


@st.cache_resource
def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data
def _load_subject_pages() -> dict:
    """department prefix -> Bulletin subject-page number, e.g. 'BIOL' -> '700'."""
    conn = _get_connection()
    rows = conn.execute("SELECT prefix, subject_number FROM bulletin_subjects").fetchall()
    return {r["prefix"]: r["subject_number"] for r in rows}


@st.cache_data
def _load_requirement_codes() -> dict:
    """brd_code -> [req_code, ...], e.g. 'BIOL 14A' -> ['qr', 'sn']."""
    conn = _get_connection()
    rows = conn.execute("SELECT brd_code, req_code FROM bulletin_requirements").fetchall()
    result = defaultdict(list)
    for r in rows:
        result[r["brd_code"]].append(r["req_code"])
    return dict(result)


def get_bulletin_url(brd_code: str, brd_department: str) -> str | None:
    """Official Bulletin URL for the department a course belongs to (the
    Bulletin has no per-course anchor, so this lands on the department's
    course listing, not the exact course -- see 06_scrape_bulletin.py).
    Returns None if the department can't be resolved (e.g. a cross-listed
    course code like 'AAPI/HIS 163A' that doesn't match a single Bulletin
    subject page)."""
    number = _load_subject_pages().get(brd_department)
    if not number:
        return None
    return f"{BULLETIN_BASE}/{number}.html"


def get_requirement_codes(brd_code: str) -> list[str]:
    """Core requirement codes this Brandeis course satisfies, e.g. ['qr', 'sn']."""
    return _load_requirement_codes().get(brd_code, [])
