#!/usr/bin/env python3
"""
MA Community College Course Scraper
For Brandeis University transfer credit evaluation.

Colleges implemented
--------------------
  Acalog / Modern Campus Catalog:
    - Bunker Hill Community College    (catalog.bhcc.edu)
    - Middlesex Community College      (catalog.middlesex.mass.edu)
    - MassBay Community College        (catalog.massbay.edu)  *self-signed SSL*
    - Holyoke Community College        (catalog.hcc.edu)
    - Springfield Technical CC         (catalog.stcc.edu)

  Clean Catalog:
    - Bristol Community College        (catalog.bristolcc.edu)

  CourseDog CSV export:
    - Greenfield Community College     (catalog.gcc.mass.edu)

  Static HTML:
    - Roxbury Community College        (rcc.mass.edu)

  Server-rendered HTML (GET form):
    - Massasoit Community College          (massasoit.edu/academics/course-search.html)

  Static HTML catalog (Acalog-style):
    - Mount Wachusett Community College    (catalog.mwcc.edu/coursedescriptions/)

  Internal JSON API:
    - North Shore Community College        (northshore.edu/_course-api/v1/courses.php)

  Drupal Views HTML + detail pages:
    - Quinsigamond Community College       (qcc.edu/classes)

  Course Search Tool XML API:
    - Northern Essex Community College     (cst.necc.mass.edu/get_courses)

  Stubs (catalog URL research needed):
    - Berkshire Community College
    - Cape Cod Community College

Setup:
    pip install -r requirements.txt
    playwright install chromium   # one-time browser download, needed for
                                   # the Acalog/Modern Campus WAF challenge

    Optional: copy .env.example to .env and fill in NOTION_TOKEN to mirror
    scrape_history.json into a Notion "Scraper Health Tracker" database
    after every run. Skip this file entirely to run without Notion.

Run all colleges:
    python scrape_courses.py

Run a single college (useful for testing):
    python scrape_courses.py --college bhcc
"""

import argparse
import csv
import html
import io
import json
import os
import re
import string
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

# ── CONFIG ────────────────────────────────────────────────────────────────────

REQUEST_DELAY = 0.5   # seconds between HTTP requests
OUTPUT_FILE   = "transfer_courses.xlsx"

HISTORY_FILE       = "scrape_history.json"
REGRESSION_DROP_PCT = 0.15   # warn if a college's count falls >15% below its last good run


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader (KEY=VALUE per line) so secrets like NOTION_TOKEN
    don't need to be hardcoded or exported as real shell environment vars."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; TransferResearchBot/1.0; "
        "+https://brandeis.edu/transfer)"
    )
}

# Modern Campus/Acalog catalog hosts sit behind an AWS WAF JS challenge that
# blocks plain HTTP clients outright (empty 202 response). A real browser UA
# is required for the headless-browser challenge solve in _get_waf_session().
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def get(url: str, verify: bool = True, session: "requests.Session | None" = None, **kwargs) -> requests.Response:
    """Rate-limited GET. Uses `session` (with its own cookies/headers) if given,
    otherwise a plain request with the shared bot headers. Raises on HTTP error."""
    if session is not None:
        r = session.get(url, timeout=30, verify=verify, **kwargs)
    else:
        r = requests.get(url, headers=HEADERS, timeout=30, verify=verify, **kwargs)
    r.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return r


_waf_sessions: dict[str, requests.Session] = {}


def _get_waf_session(url: str, verify_ssl: bool = True, force_refresh: bool = False) -> requests.Session:
    """
    Solve a host's AWS WAF JS challenge once via headless Chromium, then reuse
    the resulting cookies (aws-waf-token, ALB stickiness, etc.) in a plain
    requests.Session for the rest of that host's crawl — much faster than
    driving every page through the browser. Cached per host.

    The token appears to expire after a few hundred requests; when that
    happens the site doesn't error, it silently re-serves an earlier page.
    Pass force_refresh=True to re-solve and replace the cached session.
    """
    host = _host(url)
    if not force_refresh and host in _waf_sessions:
        return _waf_sessions[host]

    from playwright.sync_api import sync_playwright

    print(f"    (solving WAF challenge for {host} …)")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=BROWSER_HEADERS["User-Agent"],
            ignore_https_errors=not verify_ssl,
        )
        page.goto(url, wait_until="networkidle", timeout=30000)
        cookies = page.context.cookies()
        browser.close()

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    for c in cookies:
        name = c.get("name")
        if name is None:
            continue
        session.cookies.set(
            name, c.get("value", ""), domain=c.get("domain", ""), path=c.get("path", "/"),
        )

    _waf_sessions[host] = session
    return session


def make_soup(r: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(r.text, "lxml")


def _host(url: str) -> str:
    return url.split("/")[2]


def _extract_code(text: str) -> str:
    """Pull a course code like 'ACC 101' or 'ACC-101' from arbitrary text."""
    m = re.search(r"([A-Z]{2,5}[-\s]\d{3,4}[A-Z]?)", text)
    return m.group(1) if m else ""


def _extract_credits(text: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:Credit(?:\s*Hour)?|Unit)s?\b", text, re.I)
    return m.group(1) if m else ""


def _empty_course(college: str, code: str = "", title: str = "") -> dict:
    return {
        "College": college, "Code": code, "Title": title,
        "Credits": "", "Description": "", "Prerequisites": "",
    }


# ── ACALOG / MODERN CAMPUS CATALOG ───────────────────────────────────────────

def _scrape_acalog(
    college_name: str,
    base_catalog_url: str,
    catoid: str,
    navoid: str,
    verify_ssl: bool = True,
) -> list[dict]:
    """
    Generic scraper for all Acalog / Modern Campus Catalog sites.

    NOTE: filter[prefix]=X no longer restricts results on these sites — every
    prefix value (confirmed live, including in a real rendered browser) returns
    the same default listing. So instead of looping per-prefix, we just paginate
    the unfiltered course listing via filter[cpage]=1,2,3... until a page yields
    no new links, which reliably walks the entire catalog in one pass.
    """
    host     = _host(base_catalog_url)
    base_url = f"https://{host}/content.php?catoid={catoid}&navoid={navoid}"

    try:
        session = _get_waf_session(base_url, verify_ssl=verify_ssl)
    except Exception as e:
        print(f"  [{college_name}] WAF challenge failed: {e}")
        return []

    base_params = "&filter[item_type]=3&filter[only_active]=1&filter[3]=1"

    courses    = []
    seen_coids = set()
    page = 1
    while True:
        # The WAF token seems to expire after a few hundred requests; refresh
        # proactively every few pages so it doesn't go stale mid-crawl.
        if page > 1 and (page - 1) % 4 == 0:
            session = _get_waf_session(base_url, verify_ssl=verify_ssl, force_refresh=True)

        page_param = f"&filter[cpage]={page}" if page > 1 else ""
        list_url   = base_url + base_params + page_param

        try:
            r = get(list_url, verify=verify_ssl, session=session)
            s = make_soup(r)
        except Exception as e:
            print(f"  [{college_name}] page {page}: {e}")
            break

        def _extract_new_links(soup: BeautifulSoup) -> list:
            found = (
                soup.select("a[href*='preview_course_nopop.php']") or
                soup.select("a[href*='preview_course.php']")
            )
            out = []
            for a in found:
                href = str(a["href"])
                m    = re.search(r"coid=(\d+)", href)
                coid = m.group(1) if m else href
                if coid not in seen_coids:
                    seen_coids.add(coid)
                    out.append(a)
            return out

        new_links = _extract_new_links(s)

        if not new_links:
            # Could be genuine end-of-catalog, or a stale/expired WAF token
            # silently re-serving an earlier page — that looks identical to
            # "no new courses". Refresh and retry once before trusting it.
            print(f"  [{college_name}] p{page}: 0 new — refreshing WAF session to confirm end of catalog …")
            session = _get_waf_session(base_url, verify_ssl=verify_ssl, force_refresh=True)
            try:
                r = get(list_url, verify=verify_ssl, session=session)
                new_links = _extract_new_links(make_soup(r))
            except Exception as e:
                print(f"  [{college_name}] refresh retry failed: {e}")

        print(f"  [{college_name}] p{page}: {len(new_links)} new courses ({len(seen_coids)} total)")

        for i, a in enumerate(new_links, 1):
            href       = a["href"]
            raw_title  = a.get_text(strip=True)
            detail_url = (
                href if href.startswith("http")
                else f"https://{host}/{href.lstrip('/')}"
            )

            print(f"    [{i}/{len(new_links)}] {raw_title[:60]}", flush=True)

            try:
                rd = get(detail_url, verify=verify_ssl, session=session)
                code, credits, desc, prereqs = _parse_acalog_detail(make_soup(rd))
            except Exception as e:
                print(f"    ✗ detail error: {e}")
                code = credits = desc = prereqs = ""

            courses.append({
                "College":       college_name,
                "Code":          code or _extract_code(raw_title),
                "Title":         raw_title,
                "Credits":       credits,
                "Description":   desc,
                "Prerequisites": prereqs,
            })

        # Stop once a page yields no new links (catalog exhausted)
        if not new_links:
            break
        page += 1

    print(f"  [{college_name}] total courses: {len(courses)}")
    return courses


def _parse_acalog_detail(s: BeautifulSoup) -> tuple:
    # Acalog pages have multiple <h1>s (site name, then the course title) —
    # pick the one that actually looks like a course code.
    h1 = next(
        (h for h in s.find_all(["h1", "h2"]) if _extract_code(h.get_text(" ", strip=True))),
        None,
    )
    raw = h1.get_text(" ", strip=True) if h1 else ""

    code = _extract_code(raw)

    desc_el = (
        s.select_one("td.block_content_popup") or
        s.select_one("div.block_content") or
        s.select_one("td.block_content")
    )
    desc = desc_el.get_text(" ", strip=True) if desc_el else ""

    # Acalog renders credits label-first ("Credits: 3" / "Credits 3 Lecture …"),
    # not "3 Credits" — match that first before falling back to the generic
    # number-before-label heuristic used by other catalog platforms.
    m_credits = re.search(r"Credits?:?\s*(\d+(?:\.\d+)?)\b", desc, re.I)
    credits = m_credits.group(1) if m_credits else (_extract_credits(raw) or _extract_credits(desc))

    m       = re.search(r"Prerequisite[s]?[:\s]+(.+?)(?:\n|Corequisite|$)", desc, re.I | re.DOTALL)
    prereqs = m.group(1).strip() if m else ""

    return code, credits, desc, prereqs


# ── ACALOG COLLEGE WRAPPERS ───────────────────────────────────────────────────

def scrape_bhcc() -> list[dict]:
    return _scrape_acalog(
        "Bunker Hill Community College",
        "https://catalog.bhcc.edu/", "15", "787",
    )


def scrape_middlesex() -> list[dict]:
    return _scrape_acalog(
        "Middlesex Community College",
        "https://catalog.middlesex.mass.edu/", "28", "2539",
    )


def scrape_massbay() -> list[dict]:
    # verify_ssl=False: catalog.massbay.edu has a self-signed certificate
    return _scrape_acalog(
        "MassBay Community College",
        "http://catalog.massbay.edu/", "15", "574",
        verify_ssl=False,
    )


def scrape_hcc() -> list[dict]:
    return _scrape_acalog(
        "Holyoke Community College",
        "https://catalog.hcc.edu/", "13", "564",
    )


def scrape_stcc() -> list[dict]:
    return _scrape_acalog(
        "Springfield Technical Community College",
        "https://catalog.stcc.edu/", "32", "6958",
    )


# ── COURSEDOG (Bristol CC) ───────────────────────────────────────────────────

def scrape_bristol() -> list[dict]:
    """
    Bristol CC migrated its catalog platform from Clean Catalog to CourseDog
    (coursedog.com) — the old /classes/{letter} pages 404 now, and course
    data is loaded client-side from a JSON search API instead of server-
    rendered HTML. This replicates that API call directly, so the whole
    catalog comes back in one or two requests instead of one page per
    course. catalogId/school-id/filter body captured from the live SPA's
    network traffic (https://catalog.bristolcc.edu/courses).

    Note: the API 401s without an explicit Origin header — browsers send it
    automatically for cross-site fetches, but requests does not.
    """
    API = "https://app.coursedog.com/api/v1/cm/bristolcc_banner/courses/search/%24filters"
    CD_HEADERS = {
        **BROWSER_HEADERS,
        "Content-Type": "application/json",
        "Accept":       "application/json",
        "Referer":      "https://catalog.bristolcc.edu/",
        "Origin":       "https://catalog.bristolcc.edu",
        "x-requested-with": "catalog",
    }
    FILTER_BODY = {
        "condition": "AND",
        "filters": [{
            "condition": "and",
            "id": "lVmUFvOl",
            "filters": [
                {"id": "departments-course", "condition": "field", "name": "departments",
                 "inputType": "select", "group": "course", "type": "isNotEmpty"},
                {"id": "status-course", "condition": "field", "name": "status",
                 "inputType": "select", "group": "course", "type": "is",
                 "value": "Active", "customField": False},
                {"id": "description-course", "condition": "field", "name": "description",
                 "inputType": "text", "group": "course", "type": "isNotEmpty", "customField": False},
                {"id": "departments-course", "condition": "field", "name": "departments",
                 "inputType": "select", "group": "course", "type": "isNot", "value": ["NONC"]},
                {"id": "departments-course", "condition": "field", "name": "departments",
                 "inputType": "select", "group": "course", "type": "isNot", "value": ["ADED"]},
            ],
        }],
    }

    courses    = []
    seen_codes = set()
    skip  = 0
    limit = 300
    total = None

    print("  [Bristol] fetching CourseDog course search API …")
    while total is None or skip < total:
        params = {
            "catalogId": "rNbZiyg17Ifkj0v0AUE8",
            "skip": skip,
            "limit": limit,
            "orderBy": "code",
            "formatDependents": "false",
            "ignoreEffectiveDating": "false",
            "ignoreTotalCount": "false",
            "columns": "name,longName,courseNumber,subjectCode,code,description,credits,status",
        }
        try:
            r = requests.post(API, params=params, json=FILTER_BODY, headers=CD_HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  [Bristol] skip={skip}: {e}")
            break
        time.sleep(REQUEST_DELAY)

        total = data.get("listLength", 0)
        rows  = data.get("data", [])
        if not rows:
            break

        new_this_page = 0
        for item in rows:
            code = item.get("code", "")
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)
            new_this_page += 1

            subject, number = item.get("subjectCode", ""), item.get("courseNumber", "")
            display_code = f"{subject} {number}".strip() or code
            title = item.get("longName") or item.get("name", "")
            desc  = item.get("description", "")

            credit_hours = ((item.get("credits") or {}).get("creditHours") or {})
            cmin, cmax = credit_hours.get("min"), credit_hours.get("max")
            if cmin is None:
                credits = ""
            elif cmax is None or cmax == cmin:
                credits = str(cmin)
            else:
                credits = f"{cmin}-{cmax}"

            m = re.search(r"Pre-?requisite[s]?:?\s*(.+?)(?:\.\s|\.$|$)", desc, re.I)
            prereqs = m.group(1).strip() if m else ""

            courses.append({
                "College":       "Bristol Community College",
                "Code":          display_code,
                "Title":         title,
                "Credits":       credits,
                "Description":   desc,
                "Prerequisites": prereqs,
            })

        print(f"  [Bristol] skip={skip}: {new_this_page} new courses ({len(seen_codes)} total of {total})")
        skip += limit

    print(f"  [Bristol] total courses: {len(courses)}")
    return courses


def _parse_cleancatalog_detail(s: BeautifulSoup) -> dict:
    h1  = s.find("h1")
    raw = h1.get_text(" ", strip=True) if h1 else ""

    # Typical heading: "ACC 101 : Principles of Accounting I"
    m     = re.match(r"([A-Z]{2,5}[-\s]?\d{3,4}[A-Z]?)\s*[:\-]\s*(.*)", raw)
    code  = m.group(1).strip() if m else _extract_code(raw)
    title = m.group(2).strip() if m else raw

    # Credits — Clean Catalog renders "Credits" as a label with the number
    # in the next sibling element (label-value layout, not "4 Credits" inline)
    full_text = s.get_text(" ")

    credits = ""
    for lbl in s.find_all(string=re.compile(r"^\s*Credits?\s*$", re.I)):
        parent = lbl.parent
        if parent is None:
            continue
        grandparent = parent.parent
        val_el = parent.find_next_sibling() or (
            grandparent.find_next_sibling() if grandparent is not None else None
        )
        if val_el is not None:
            val = val_el.get_text(strip=True)
            if re.match(r"^\d+(?:\.\d+)?$", val):
                credits = val
                break
    if not credits:
        mc = re.search(r"\bCredits?\b\s*:?\s*(\d+(?:\.\d+)?)", full_text, re.I)
        credits = mc.group(1) if mc else _extract_credits(full_text)

    # Description — largest paragraph block
    paras = s.find_all("p")
    desc  = max((p.get_text(" ", strip=True) for p in paras), key=len, default="")

    m2      = re.search(r"Prerequisite[s]?[:\s]+(.+?)(?:\.|$)", full_text, re.I)
    prereqs = m2.group(1).strip() if m2 else ""

    return {
        "Code": code, "Title": title, "Credits": credits,
        "Description": desc, "Prerequisites": prereqs,
    }


# ── CSV EXPORT (Greenfield CC) ────────────────────────────────────────────────

def scrape_gcc() -> list[dict]:
    """
    Greenfield CC (CourseDog catalog) exposes an "Export all results as CSV"
    button. Try that URL directly; fall back to the CourseDog REST API.
    """
    BASE    = "https://catalog.gcc.mass.edu"
    courses = []

    # CourseDog catalogs serve CSV exports at this endpoint
    csv_candidates = [
        f"{BASE}/courses?format=csv",
        f"{BASE}/courses?export=csv",
        f"{BASE}/courses/export.csv",
    ]

    for url in csv_candidates:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ("csv" in ct or "text/plain" in ct or r.text.startswith('"') or "," in r.text[:200]):
                reader = csv.DictReader(io.StringIO(r.text))
                for row in reader:
                    # CourseDog CSV columns vary; try common field names
                    code  = row.get("Course Code") or row.get("Code") or row.get("courseNumber", "")
                    title = row.get("Course Title") or row.get("Title") or row.get("name", "")
                    cred  = row.get("Credits") or row.get("Units") or row.get("credits", "")
                    desc  = row.get("Description") or row.get("description", "")
                    prereq = row.get("Prerequisites") or row.get("prerequisites", "")
                    if code or title:
                        courses.append({
                            "College":       "Greenfield Community College",
                            "Code":          code.strip(),
                            "Title":         title.strip(),
                            "Credits":       str(cred).strip(),
                            "Description":   desc.strip(),
                            "Prerequisites": prereq.strip(),
                        })
                if courses:
                    print(f"  [GCC] CSV export: {len(courses)} courses")
                    return courses
            time.sleep(REQUEST_DELAY)
        except Exception:
            pass

    # Fall back to CourseDog REST API
    print("  [GCC] CSV not available; trying API")
    api_candidates = [
        f"{BASE}/api/v1/courses?skip=0&limit=2000",
        f"{BASE}/api/v1/courses/search?skip=0&limit=2000",
        "https://app.coursedog.com/api/v1/cm/gcc/courses/$all?skip=0&limit=2000",
    ]
    for endpoint in api_candidates:
        try:
            r = requests.get(endpoint, headers=HEADERS, timeout=30)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                data  = r.json()
                items = data if isinstance(data, list) else data.get("courses", data.get("data", []))
                for item in items:
                    courses.append({
                        "College":       "Greenfield Community College",
                        "Code":          item.get("courseNumber") or item.get("code", ""),
                        "Title":         item.get("name")         or item.get("title", ""),
                        "Credits":       str(item.get("credits", item.get("units", ""))),
                        "Description":   item.get("description", ""),
                        "Prerequisites": item.get("prerequisites", ""),
                    })
                print(f"  [GCC] API: {len(courses)} courses")
                return courses
            time.sleep(REQUEST_DELAY)
        except Exception:
            pass

    print("  [GCC] all methods failed — no courses returned")
    return courses


# ── STATIC HTML (Roxbury CC) ──────────────────────────────────────────────────

RCC_SUBJECTS = [
    "acs", "bmt", "bus", "cjp", "ece", "egr", "eng",
    "hlt", "hum", "ist", "lan", "mat", "nur", "sci", "ssi",
]
RCC_BASE = "https://www.rcc.mass.edu/catalog/current/courses"


RCC_COURSE_HEADER_RE = re.compile(
    r"([A-Z]{2,5}\s+\d{3,4}[A-Z]?)\.\s+(.+?)\s*\((\d+(?:\.\d+)?)\s*Credits?\)", re.I
)
# Roxbury crams the prereq note and the real description into ONE paragraph,
# e.g. "PREREQUISITE: ENG 101<br><br>The fundamental principles of...". This
# only ever extracts a short prereq snippet for the separate Prerequisites
# column -- it never removes anything from the description text, since a
# wrong guess here should degrade gracefully, not delete real content.
RCC_PREREQ_SNIPPET_RE = re.compile(
    r"^(Pre-?requisites?(?:\s+or\s+Corequisites?)?:?\s*[^.]*\.)", re.I
)


def scrape_rcc() -> list[dict]:
    """Roxbury CC publishes one static HTML page per subject area, each a
    Bootstrap accordion (one <div class="accordion-item"> per course)."""
    courses = []

    for subj in RCC_SUBJECTS:
        url = f"{RCC_BASE}/{subj}.html"
        try:
            r = get(url)
            s = make_soup(r)
        except Exception as e:
            print(f"  [RCC] {subj}: {e}")
            continue

        # select("h2") also matches the accordion's own subject-banner <h2>
        # (e.g. a bare "BUS" heading, sibling of every accordion-item rather
        # than nested inside one) -- filtered out below by requiring a full
        # "CODE. TITLE. (N Credits)" match, which that banner never has.
        headers = s.select("h2")

        kept = 0
        for h in headers:
            raw = h.get_text(" ", strip=True)
            m   = RCC_COURSE_HEADER_RE.match(raw)
            if not m:
                continue
            code, title, credits = m.group(1), m.group(2), m.group(3)
            kept += 1

            # Siblings of a real course's h2 are its own accordion-collapse
            # content only (find_next_siblings() is scoped to h's parent,
            # the accordion-item div) -- unlike the banner h2 above, whose
            # siblings are every OTHER course's accordion-item div on the
            # page, which is exactly why that one has to be filtered out
            # rather than handled here.
            desc_parts = []
            prereqs    = ""
            for sib in h.find_next_siblings():
                if sib.name in ("h2", "h3"):
                    break
                text = sib.get_text(" ", strip=True)
                if not text:
                    continue
                desc_parts.append(text)
                if not prereqs:
                    pm = RCC_PREREQ_SNIPPET_RE.match(text)
                    if pm:
                        prereqs = pm.group(1)

            courses.append({
                "College":       "Roxbury Community College",
                "Code":          code.strip(),
                "Title":         title.strip(),
                "Credits":       credits.strip(),
                "Description":   " ".join(desc_parts).strip(),
                "Prerequisites": prereqs.strip(),
            })

        print(f"  [RCC] {subj.upper()}: {kept}/{len(headers)} course headers")

    return courses


# ── STUBS (catalog research needed) ──────────────────────────────────────────
# Each stub prints guidance and returns an empty list so the rest of the
# script continues. Fill in the correct URLs / selectors and uncomment.

def scrape_berkshire() -> list[dict]:
    """
    TODO: Berkshire CC (berkshirecc.edu)
    Find catalog at https://catalog.berkshirecc.edu/
    Identify catalog software and update this function.
    """
    print("  [Berkshire CC] stub — catalog URL research needed")
    return []


def scrape_capecod() -> list[dict]:
    """
    Cape Cod CC uses Clean Catalog at live-capecod.cleancatalog.io.
    All courses are at /classes with ?page=N pagination (no letter sub-paths).
    Course hrefs use no hyphen: /accounting/acc100.
    """
    BASE    = "https://live-capecod.cleancatalog.io"
    courses = []
    seen    = set()
    page    = 0

    while True:
        url = f"{BASE}/classes" + (f"?page={page}" if page else "")
        try:
            r = get(url)
            s = make_soup(r)
        except Exception as e:
            print(f"  [Cape Cod CC] page {page}: {e}")
            break

        new_on_page = 0
        for a in s.select("a[href]"):
            href = str(a["href"])
            if (
                re.match(r"^/[a-z][a-z0-9-]+/[a-z]{2,5}\d{3,4}", href)
                and href not in seen
            ):
                seen.add(href)
                new_on_page += 1
                detail_url = BASE + href
                try:
                    rd = get(detail_url)
                    c  = _parse_cleancatalog_detail(make_soup(rd))
                    c["College"] = "Cape Cod Community College"
                    courses.append(c)
                except Exception as e:
                    print(f"  [Cape Cod CC] detail error {href}: {e}")

        print(f"  [Cape Cod CC] page {page}: {new_on_page} new courses ({len(seen)} total)")
        if not new_on_page:
            break
        page += 1

    return courses


def scrape_massasoit() -> list[dict]:
    """
    Massasoit CC — server-rendered course search.
    The form at /academics/course-search.html submits via GET to the same URL.
    We discover available term values from the radio buttons, then scrape each
    term for credit courses, deduplicating by course code so catalog entries
    aren't repeated when a course runs in multiple terms.
    """
    BASE = "https://www.massasoit.edu/academics/course-search.html"
    courses = []
    seen_codes: set[str] = set()

    # Discover available terms from the page's radio buttons
    try:
        r = get(BASE)
        s = make_soup(r)
    except Exception as e:
        print(f"  [Massasoit CC] failed to load base page: {e}")
        return []

    terms = [
        str(inp["value"])
        for inp in s.select('input[name="term"]')
        if inp.get("value")
    ]
    if not terms:
        print("  [Massasoit CC] no term options found")
        return []

    print(f"  [Massasoit CC] found terms: {terms}")

    for term in terms:
        url = f"{BASE}?term={str(term).replace(' ', '+')}&credits=GTZ"
        try:
            r = get(url)
            s = make_soup(r)
        except Exception as e:
            print(f"  [Massasoit CC] term '{term}' fetch error: {e}")
            continue

        items = s.select("div.result-item")
        new_this_term = 0
        for item in items:
            box = item.select_one("div.course-box")
            if not box:
                continue

            strongs = box.select("p > strong")
            if len(strongs) < 2:
                continue

            # First strong: "ACCT 104 - Fundamentals of Financial Reporting"
            raw_title = strongs[0].get_text(" ", strip=True)
            m = re.match(r"([A-Z]{2,6}\s*\d{3,4}[A-Z]?)\s*[-–]\s*(.*)", raw_title)
            if m:
                code  = m.group(1).strip()
                title = m.group(2).strip()
            else:
                code  = ""
                title = raw_title

            if code in seen_codes:
                continue

            # Second strong: "Fall 2026 - 4 Credit Course"
            raw_meta = strongs[1].get_text(" ", strip=True)
            cred_m = re.search(r"(\d+(?:\.\d+)?)\s+Credit", raw_meta, re.I)
            credits = cred_m.group(1) if cred_m else ""

            desc_el = box.select_one("p.description")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""

            # Strip trailing prerequisite labels added by JS (Pre/Co-requisites:)
            description = re.sub(r"\s*(Pre/Co-|Pre|Co)requisites?:.*", "", description, flags=re.I | re.DOTALL).strip()

            seen_codes.add(code)
            new_this_term += 1
            courses.append({
                "College":       "Massasoit Community College",
                "Code":          code,
                "Title":         title,
                "Credits":       credits,
                "Description":   description,
                "Prerequisites": "",
            })

        print(f"  [Massasoit CC] term '{term}': {new_this_term} new courses")

    print(f"  [Massasoit CC] total unique courses: {len(courses)}")
    return courses


def scrape_mwcc() -> list[dict]:
    """
    Mount Wachusett CC — Acalog-style static HTML catalog.
    Index at /coursedescriptions/ lists ~59 department slugs (acc, bio, ...).
    Each department page contains div.courseblock entries with:
      p.courseblocktitle  → "CODE\xa0NUM.  Title.  N Credits."
      p.courseblockdesc   → description text (may include prereqs inline)
    """
    BASE  = "https://catalog.mwcc.edu"
    INDEX = f"{BASE}/coursedescriptions/"
    courses = []

    # Discover department slugs from the index page
    try:
        r = get(INDEX)
        s = make_soup(r)
    except Exception as e:
        print(f"  [Mount Wachusett CC] failed to load index: {e}")
        return []

    dept_links = sorted({
        str(a["href"])
        for a in s.select('a[href^="/coursedescriptions/"]')
        if re.match(r"^/coursedescriptions/[a-z]{2,4}/$", str(a["href"]))
    })
    print(f"  [Mount Wachusett CC] found {len(dept_links)} department pages")

    for href in dept_links:
        url = BASE + str(href)
        try:
            r = get(url)
            s = make_soup(r)
        except Exception as e:
            print(f"  [Mount Wachusett CC] {href} error: {e}")
            continue

        for block in s.select("div.courseblock"):
            title_el = block.select_one("p.courseblocktitle strong")
            if not title_el:
                continue

            raw = title_el.get_text(" ", strip=True)
            # Format: "ACC 101.  Principles of Accounting I.  3 Credits."
            # Non-breaking spaces (\xa0) appear between code letters and number
            raw = raw.replace("\xa0", " ")
            m = re.match(
                r"([A-Z]{2,6}\s+\d{3,4}[A-Z]?)\.\s+(.*?)\.\s+(\d+(?:\.\d+)?)\s+Credits?\.",
                raw, re.I
            )
            if not m:
                continue

            code    = m.group(1).strip()
            title   = m.group(2).strip()
            credits = m.group(3).strip()

            desc_el = block.select_one("p.courseblockdesc")
            description = desc_el.get_text(" ", strip=True) if desc_el else ""

            courses.append({
                "College":       "Mount Wachusett Community College",
                "Code":          code,
                "Title":         title,
                "Credits":       credits,
                "Description":   description,
                "Prerequisites": "",
            })

    print(f"  [Mount Wachusett CC] total courses: {len(courses)}")
    return courses


def scrape_northshore() -> list[dict]:
    """
    North Shore Community College (northshore.edu)
    Uses the internal JSON API at /_course-api/v1/courses.php which returns
    all credit and non-credit courses in one request.
    """
    API_URL = "https://www.northshore.edu/_course-api/v1/courses.php"

    print("  [North Shore CC] fetching course catalog …")
    try:
        r = get(API_URL)
        data = r.json()
    except Exception as e:
        print(f"  [North Shore CC] API request failed: {e}")
        return []

    courses = []
    for course in data.values():
        if course.get("credits_type") != "credit":
            continue

        code   = f"{course['subject_code']} {course['number']}"
        title  = course.get("title", "").strip()
        credits = str(course.get("credits", "")).strip()
        desc   = course.get("description", "").strip()

        # Prerequisites are stored per-session but are consistent across them
        sessions = course.get("sessions", [])
        prereq = sessions[0].get("prerequisite", "").strip() if sessions else ""

        courses.append({
            "College":       "North Shore Community College",
            "Code":          code,
            "Title":         title,
            "Credits":       credits,
            "Description":   desc,
            "Prerequisites": prereq,
        })

    print(f"  [North Shore CC] total credit courses: {len(courses)}")
    return courses


def scrape_qcc() -> list[dict]:
    """
    Quinsigamond Community College (qcc.edu)
    The /classes page is a Drupal views table listing all courses with links
    to individual detail pages at /courses/<slug>. Each detail page has
    structured field__label / field__item elements for credits, description,
    and prerequisites.
    """
    BASE = "https://www.qcc.edu"
    INDEX_URL = BASE + "/classes"

    print("  [Quinsigamond CC] loading course index …")
    try:
        r = get(INDEX_URL)
        soup = make_soup(r)
    except Exception as e:
        print(f"  [Quinsigamond CC] index failed: {e}")
        return []

    # Collect (code, title, detail_href) from all views tables on the page
    entries = []
    for row in soup.select("table.views-table tr"):
        code_td  = row.select_one("td.views-field-field-course-number")
        title_td = row.select_one("td.views-field-title")
        if not (code_td and title_td):
            continue
        a = title_td.select_one("a[href]")
        if not a:
            continue
        entries.append((
            code_td.get_text(strip=True),
            a.get_text(strip=True),
            a["href"],
        ))

    print(f"  [Quinsigamond CC] {len(entries)} courses found, fetching details …")

    courses = []
    for i, (code, title, href) in enumerate(entries, 1):
        url = BASE + href if href.startswith("/") else href
        try:
            r = get(url)
            detail = make_soup(r)
        except Exception as e:
            print(f"    [{i}/{len(entries)}] {code} error: {e}")
            courses.append({
                "College": "Quinsigamond Community College",
                "Code": code, "Title": title,
                "Credits": "", "Description": "", "Prerequisites": "",
            })
            continue

        # Build a label→value map from Drupal field widgets
        fields: dict[str, str] = {}
        for field_div in detail.select("div.field"):
            label_el = field_div.select_one(".field__label")
            item_el  = field_div.select_one(".field__item")
            if label_el and item_el:
                fields[label_el.get_text(strip=True).lower()] = item_el.get_text(" ", strip=True)

        # Description: the body div is itself the field__item (label-hidden pattern)
        body_div = detail.select_one("div.field--name-body")
        desc = body_div.get_text(" ", strip=True) if body_div else ""

        if i % 50 == 0:
            print(f"    {i}/{len(entries)} done …")

        courses.append({
            "College":       "Quinsigamond Community College",
            "Code":          fields.get("course number", code),
            "Title":         title,
            "Credits":       fields.get("credits", ""),
            "Description":   desc,
            "Prerequisites": fields.get("prerequisites", ""),
        })

    print(f"  [Quinsigamond CC] total courses: {len(courses)}")
    return courses

def scrape_necc() -> list[dict]:
    """
    Northern Essex Community College (necc.edu)
    Uses the Course Search Tool XML API at cst.necc.mass.edu/get_courses.
    Queries every subject across all available terms, then deduplicates by
    course code to build the most complete catalog.
    """
    BASE = "https://cst.necc.mass.edu"
    import xml.etree.ElementTree as ET
    import random

    # Fetch subject codes
    print("  [Northern Essex CC] loading subjects …")
    try:
        r = get(f"{BASE}/get_courses?rndData={random.random()}")
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"  [Northern Essex CC] subject list failed: {e}")
        return []

    subjects = [
        (row.findtext("STVSUBJ_CODE", "").strip(), row.findtext("STVSUBJ_DESC", "").strip())
        for row in root.findall("ROW")
        if row.findtext("STVSUBJ_CODE", "").strip()
    ]
    print(f"  [Northern Essex CC] {len(subjects)} subjects, querying all terms …")

    # Query every subject across all available terms; deduplicate by code
    TERMS = ["202601", "202509", "202609", "202605", "202605-11"]
    seen: set[str] = set()
    courses: list[dict] = []

    for subj_code, subj_desc in subjects:
        for term in TERMS:
            try:
                r = get(f"{BASE}/get_courses?rndData={random.random()}&term={term}&subject={subj_code}")
                root = ET.fromstring(r.text)
            except Exception as e:
                print(f"    {subj_code} term={term} error: {e}")
                continue

            for row in root.iter("COURSES_ROW"):
                code = (
                    (row.findtext("SCBCRSE_SUBJ_CODE") or "").strip()
                    + " "
                    + (row.findtext("SCBCRSE_CRSE_NUMB") or "").strip()
                ).strip()
                if not code or code in seen:
                    continue
                seen.add(code)

                raw_title = (row.findtext("COURSE_TITLE") or "").strip()
                # Format: "ACC101 - Intro Accounting I | 3 Credit Course"
                title = re.sub(r"^[A-Z]{2,6}\d{3,4}[A-Z]?\s*[-–]\s*", "", raw_title)
                title = re.sub(r"\s*\|\s*\d+(?:\.\d+)?\s*Credit\s+Course\s*$", "", title, flags=re.I).strip()

                credits = (row.findtext("CREDIT_HOURS") or row.findtext("SCBCRSE_CREDIT_HR_LOW") or "").strip()
                desc    = (row.findtext("COURSE_DESCRIPTION") or "").strip()
                prereq  = (row.findtext("PREREQUISITES") or "").strip()

                courses.append({
                    "College":       "Northern Essex Community College",
                    "Code":          code,
                    "Title":         title,
                    "Credits":       credits,
                    "Description":   desc,
                    "Prerequisites": prereq,
                })

        print(f"    {subj_code}: {sum(1 for c in courses if c['Code'].startswith(subj_code + ' '))} unique courses so far total={len(courses)}")

    print(f"  [Northern Essex CC] total courses: {len(courses)}")
    return courses

# ── UMASS BOSTON (courses.umb.edu) ───────────────────────────────────────────

UMB_TERM = "2026 Fall"


def scrape_umb() -> list[dict]:
    """
    UMass Boston course catalog (courses.umb.edu).

    /course_catalog/subjects/{term} lists all subjects (both ugrd and grd)
    as links to /course_catalog/courses/{career}_{subject}_{term}.  Each
    subject page links to individual course detail pages at
    /course_catalog/course_info/{career}_{subject}_{term}_{number}.
    """
    BASE = "https://courses.umb.edu"

    # Step 1: collect all subject page URLs for the term
    print(f"  [UMB] loading subjects for {UMB_TERM} …")
    try:
        r = get(f"{BASE}/course_catalog/subjects/{UMB_TERM}")
        s = make_soup(r)
    except Exception as e:
        print(f"  [UMB] subjects page failed: {e}")
        return []

    content = s.find(id="content") or s
    subject_urls = list({
        (a["href"] if a["href"].startswith("http") else BASE + a["href"])
        for a in content.find_all("a", href=re.compile(r"/course_catalog/courses/"))
    })
    print(f"  [UMB] {len(subject_urls)} subjects found, collecting course links …")

    # Step 2: for each subject page, collect course_info links
    seen_urls: set = set()
    for subject_url in subject_urls:
        try:
            rs = get(subject_url)
            ss = make_soup(rs)
        except Exception as e:
            print(f"  [UMB] subject page failed ({subject_url}): {e}")
            continue
        for a in ss.find_all("a", href=re.compile(r"/course_catalog/course_info/")):
            href = a["href"]
            seen_urls.add(href if href.startswith("http") else BASE + href)

    detail_list = list(seen_urls)
    print(f"  [UMB] {len(detail_list)} unique courses found, fetching details …")

    # Step 3: fetch each course detail page
    courses = []
    for i, detail_url in enumerate(detail_list, 1):
        try:
            rd = get(detail_url)
            sd = make_soup(rd)
        except Exception as e:
            print(f"    [{i}/{len(detail_list)}] error: {e}")
            continue

        content_div = sd.find(id="content") or sd
        full_text = content_div.get_text("\n", strip=True)

        # Title is the last breadcrumb segment before the h1
        h1 = content_div.find("h1") or content_div.find("h2")
        title = h1.get_text(" ", strip=True) if h1 else ""

        # "Course #: CS 105" or "Course #: SPE G 601"
        code = ""
        m = re.search(r"Course\s*#:\s*([A-Z]{2,8}(?:\s[A-Z]+)?\s*\d{3,4}[A-Z]?)", full_text)
        if m:
            code = m.group(1).strip()

        desc = ""
        m2 = re.search(r"Description:\s*\n(.+?)(?:\nPre Requisites:|\nSection\b)", full_text, re.S)
        if m2:
            desc = m2.group(1).strip()

        prereqs = ""
        m3 = re.search(r"Pre Requisites:\s*\n(.+?)(?:\nSection\b|\nCourse Attributes:|\Z)", full_text, re.S)
        if m3:
            prereqs = m3.group(1).strip()

        credits = ""
        m4 = re.search(r"Credits:\s*(\d+)", full_text)
        if m4:
            credits = m4.group(1)

        if i % 50 == 0:
            print(f"    {i}/{len(detail_list)} done …")

        courses.append({
            "College":       "University of Massachusetts Boston",
            "Code":          code,
            "Title":         title,
            "Credits":       credits,
            "Description":   desc,
            "Prerequisites": prereqs,
        })

    print(f"  [UMB] total courses: {len(courses)}")
    return courses


# ── UMASS LOWELL — Online & Professional Studies (gps.uml.edu) ──────────────

UML_GPS_TERMS = [
    "2026/fall",
    "2026/summer",
    # add more term slugs as needed, e.g. "2026/spring"
]


def scrape_uml_gps() -> list[dict]:
    """
    UMass Lowell, Division of Graduate, Online & Professional Studies
    (gps.uml.edu). This is the continuing-ed / online catalog (it also
    includes some on-campus evening sections), NOT the full day-school
    undergraduate catalog — see scrape_uml_catalog() for that (stub).

    /catalog/search/{year}/{term}/ lists all sections.  We dedupe by course
    code (e.g. "acct.2010") so each course is only fetched once across all
    sections and terms.  Each detail page has an h2 structure:
      h2 "Course Description" → p (description)
      h2 "Prerequisites, Notes & Instructor" → ul > li items
    """
    BASE = "https://gps.uml.edu"
    seen_courses: dict = {}  # course_code -> first section detail URL

    for term_slug in UML_GPS_TERMS:
        list_url = f"{BASE}/catalog/search/{term_slug}/"
        print(f"  [UML GPS] loading {term_slug} …")
        try:
            r = get(list_url)
            s = make_soup(r)
        except Exception as e:
            print(f"  [UML GPS] {term_slug} failed: {e}")
            continue

        for a in s.select("a[href*='/catalog/search/']"):
            href = str(a["href"])
            m = re.search(
                r"/catalog/search/\d{4}/[a-z]+/([a-z]+\.\d{3,4}[a-z]?)/[a-z0-9]+/?$",
                href, re.I,
            )
            if not m:
                continue
            course_id = m.group(1).lower()
            if course_id not in seen_courses:
                seen_courses[course_id] = href if href.startswith("http") else BASE + href

        print(f"  [UML GPS] {term_slug}: {len(seen_courses)} unique courses so far")

    detail_list = list(seen_courses.values())
    print(f"  [UML GPS] {len(detail_list)} unique courses, fetching details …")

    courses = []
    for i, detail_url in enumerate(detail_list, 1):
        try:
            rd = get(detail_url)
            sd = make_soup(rd)
        except Exception as e:
            print(f"    [{i}/{len(detail_list)}] error: {e}")
            continue

        h1 = sd.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else ""

        full_text = sd.get_text("\n", strip=True)

        # "Course No: ACCT.2010-001" → "ACCT.2010"
        code = ""
        m = re.search(r"Course No:\s*([A-Z]{2,8}\.\d{3,4}[A-Z]?)-", full_text, re.I)
        if m:
            code = m.group(1).upper()

        # Description: <p> directly after the "Course Description" h2
        desc = ""
        desc_h2 = next(
            (h for h in sd.find_all("h2") if re.search(r"Course Description", h.get_text(), re.I)),
            None,
        )
        if desc_h2:
            p = desc_h2.find_next_sibling("p")
            if p:
                desc = p.get_text(" ", strip=True)

        # Credits and prereqs: <li> items in the ul after "Prerequisites, Notes & Instructor"
        credits = ""
        prereqs = ""
        prereq_h2 = next(
            (h for h in sd.find_all("h2") if re.search(r"Prerequisites", h.get_text(), re.I)),
            None,
        )
        if prereq_h2:
            ul = prereq_h2.find_next_sibling("ul")
            if ul:
                for li in ul.find_all("li"):
                    li_text = li.get_text(" ", strip=True)
                    if li_text.lower().startswith("credits:"):
                        mc = re.search(r"Credits:\s*(\S+)", li_text, re.I)
                        if mc:
                            credits = mc.group(1).rstrip(";")
                    elif li_text.lower().startswith("prerequisite"):
                        prereqs = re.sub(r"^Prerequisites?:\s*", "", li_text, flags=re.I).strip()

        if i % 50 == 0:
            print(f"    {i}/{len(detail_list)} done …")

        courses.append({
            "College":       "University of Massachusetts Lowell (GPS)",
            "Code":          code,
            "Title":         title,
            "Credits":       credits,
            "Description":   desc,
            "Prerequisites": prereqs,
        })

    print(f"  [UML GPS] total unique courses: {len(courses)}")
    return courses


# ── UMASS AMHERST — University+ (universityplus.umass.edu) ───────────────────

UMA_TERM_TID = "1144"  # Drupal internal TID for Fall 2026 (SPIRE code 1267)


def scrape_uma() -> list[dict]:
    """
    UMass Amherst University+ continuing-education catalog.
    Data comes from the Drupal REST API backing the React explore page at
    https://www.umass.edu/universityplus/classes/explore

    The API returns one JSON object per unique course (already deduplicated by
    course_id). Prerequisites are embedded in the description field as
    "Prerequisite: ..." prose; credits are not exposed by the API.
    """
    API = "https://www.umass.edu/universityplus/api/courses"

    print("  [UMA] fetching course list from University+ API …")
    courses: list[dict] = []
    page = 0
    total_pages: int | None = None

    while True:
        try:
            r = get(API, params={
                "spire_term":     UMA_TERM_TID,
                "items_per_page": "50",
                "page":           str(page),
            })
            data = r.json()
        except Exception as e:
            print(f"  [UMA] page {page} failed: {e}")
            break

        rows = data.get("rows", [])
        pager = data.get("pager", {})

        if total_pages is None:
            total_pages = int(pager.get("total_pages", 1))
            print(f"  [UMA] {pager.get('total_items', '?')} courses, {total_pages} pages …")

        for row in rows:
            title = html.unescape(row.get("title", ""))
            raw_desc = html.unescape(row.get("description", ""))
            subject = row.get("subject_raw", "")
            catalog = row.get("catalog", "")
            code = f"{subject} {catalog}".strip()

            prereqs = ""
            m = re.search(r"Prerequisite[s]?:\s*(.+)", raw_desc, re.I | re.S)
            if m:
                prereqs = m.group(1).strip()

            courses.append({
                "College":       "University of Massachusetts Amherst",
                "Code":          code,
                "Title":         title,
                "Credits":       "",
                "Description":   raw_desc,
                "Prerequisites": prereqs,
            })

        print(f"  [UMA] page {page + 1}/{total_pages} ({len(rows)} courses)")
        page += 1
        if page >= (total_pages or 1):
            break

    print(f"  [UMA] total courses: {len(courses)}")
    return courses

def scrape_umd() -> list[dict]:
    """
    UMass Dartmouth (catalog.umassd.edu) — same Acalog / Modern Campus
    Catalog platform as BHCC, Middlesex, MassBay, HCC, and STCC, so this
    just delegates to the shared _scrape_acalog() helper.

    catoid=86 & navoid=6824 come from the "Course Descriptions" link at
    https://catalog.umassd.edu/content.php?catoid=86&navoid=6824
    """
    return _scrape_acalog(
        "University of Massachusetts Dartmouth",
        "https://catalog.umassd.edu/", "86", "6824",
    )


# ── REGISTRY ──────────────────────────────────────────────────────────────────

ALL_SCRAPERS: dict[str, tuple[str, callable]] = {
    "bhcc":       ("Bunker Hill Community College",          scrape_bhcc),
    "middlesex":  ("Middlesex Community College",            scrape_middlesex),
    "massbay":    ("MassBay Community College",              scrape_massbay),
    "hcc":        ("Holyoke Community College",              scrape_hcc),
    "stcc":       ("Springfield Technical CC",               scrape_stcc),
    "bristol":    ("Bristol Community College",              scrape_bristol),
    #"gcc":        ("Greenfield Community College",           scrape_gcc),
    "rcc":        ("Roxbury Community College",              scrape_rcc),
    #"berkshire":  ("Berkshire Community College",            scrape_berkshire),
    "capecod":    ("Cape Cod Community College",             scrape_capecod),
    "massasoit":  ("Massasoit Community College",            scrape_massasoit),
    "mwcc":       ("Mount Wachusett Community College",      scrape_mwcc),
    "northshore": ("North Shore Community College",          scrape_northshore),
    "qcc":        ("Quinsigamond Community College",         scrape_qcc),
    "necc":       ("Northern Essex Community College",         scrape_necc),
    "umb":        ("University of Massachusetts Boston",     scrape_umb),
    "uml_gps":    ("University of Massachusetts Lowell (GPS)", scrape_uml_gps),
    "uma":        ("University of Massachusetts Amherst",   scrape_uma),
    "umd":        ("University of Massachusetts Dartmouth",   scrape_umd),
}


# ── EXCEL WRITER ──────────────────────────────────────────────────────────────

COLS   = ["College", "Code", "Title", "Credits", "Description", "Prerequisites"]
WIDTHS = [32,        14,     42,      9,         80,            50]


def _header_style(cell) -> None:
    cell.font      = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    cell.fill      = PatternFill("solid", fgColor="1F4E79")
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _body_style(cell, even: bool) -> None:
    cell.font      = Font(name="Arial", size=10)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    if even:
        cell.fill = PatternFill("solid", fgColor="D9E1F2")


def _write_sheet(ws, rows: list[dict]) -> None:
    for ci, (col, w) in enumerate(zip(COLS, WIDTHS), 1):
        _header_style(ws.cell(row=1, column=ci, value=col))
        ws.column_dimensions[ws.cell(row=1, column=ci).column_letter].width = w
    ws.row_dimensions[1].height = 22

    for ri, course in enumerate(rows, 2):
        for ci, col in enumerate(COLS, 1):
            _body_style(
                ws.cell(row=ri, column=ci, value=str(course.get(col, ""))),
                ri % 2 == 0,
            )


def _group_by_college(courses: list[dict]) -> dict:
    groups: dict[str, list] = {}
    for c in courses:
        groups.setdefault(c["College"], []).append(c)
    return groups


def write_xlsx(all_courses: list[dict], path: str) -> None:
    wb = openpyxl.Workbook()

    # All-courses sheet
    ws_all       = wb.active
    ws_all.title = "All Courses"
    _write_sheet(ws_all, all_courses)

    # Per-college sheets
    groups = _group_by_college(all_courses)
    for college, rows in groups.items():
        short = re.sub(r"[^A-Za-z0-9 ]", "", college)[:28]
        _write_sheet(wb.create_sheet(title=short), rows)

    # Summary sheet
    ws_sum       = wb.create_sheet(title="Summary")
    _header_style(ws_sum.cell(row=1, column=1, value="College"))
    _header_style(ws_sum.cell(row=1, column=2, value="Courses Scraped"))
    ws_sum.column_dimensions["A"].width = 40
    ws_sum.column_dimensions["B"].width = 18
    for ri, (college, rows) in enumerate(groups.items(), 2):
        ws_sum.cell(row=ri, column=1, value=college)
        ws_sum.cell(row=ri, column=2, value=len(rows))

    wb.save(path)
    print(f"\n✓  Saved {len(all_courses)} courses → {path}")


# ── REGRESSION TRACKING ───────────────────────────────────────────────────────
# Catalog sites change out from under us (WAF rollouts, platform migrations,
# dead URLs) with no error of their own — a scraper can "succeed" with 0 or a
# suspiciously low count and look identical to a normal run in the console.
# This compares each run against the last known-good count per college and
# raises a loud warning when something looks broken, instead of relying on
# someone noticing a number looked off while scrolling the log.

def _load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_history(history: dict) -> None:
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)


def _check_regression(key: str, name: str, count: int, history: dict) -> "str | None":
    """Update history[key] in place; return a warning string if this run looks broken."""
    entry     = history.setdefault(key, {})
    prev_good = entry.get("last_good_count")
    now       = datetime.now().isoformat(timespec="seconds")

    warning = None
    if count == 0:
        warning = f"{name}: 0 courses collected" + (
            f" (last good run had {prev_good}, on {entry.get('last_good_at', '?')})" if prev_good else ""
        )
    elif prev_good and count < prev_good * (1 - REGRESSION_DROP_PCT):
        pct = 100 * (1 - count / prev_good)
        warning = f"{name}: {count} courses, down {pct:.0f}% from last good run of {prev_good} (on {entry.get('last_good_at', '?')})"

    entry["last_run_count"] = count
    entry["last_run_at"]    = now
    # Only refresh the "good" baseline on a run that isn't itself flagged —
    # otherwise a broken run's low count becomes tomorrow's baseline and the
    # next broken run looks like a non-event.
    if count > 0 and warning is None:
        entry["last_good_count"] = count
        entry["last_good_at"]    = now

    return warning


# ── NOTION SYNC (optional) ────────────────────────────────────────────────────
# Mirrors scrape_history.json into a Notion database so the tracker is visible
# without opening the JSON file. Fully opt-in: no-ops silently unless both
# NOTION_TOKEN and NOTION_DATABASE_ID are set (e.g. via a local .env file —
# see .env.example). Get a token at https://www.notion.so/my-integrations and
# share the "Scraper Health Tracker" database with it.

NOTION_API     = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _notion_headers() -> "dict | None":
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        return None
    return {
        "Authorization":  f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    }


def _push_history_to_notion(history: dict, warning_keys: set) -> None:
    headers = _notion_headers()
    db_id   = os.environ.get("NOTION_DATABASE_ID")
    if not headers or not db_id:
        return

    print("\nSyncing scrape_history.json to Notion …")
    for key, entry in history.items():
        name   = ALL_SCRAPERS[key][0] if key in ALL_SCRAPERS else key
        status = "WARNING" if key in warning_keys else "OK"

        properties = {
            "College":         {"title": [{"text": {"content": name}}]},
            "Key":             {"rich_text": [{"text": {"content": key}}]},
            "Status":          {"select": {"name": status}},
            "Last Run Count":  {"number": entry.get("last_run_count")},
            "Last Good Count": {"number": entry.get("last_good_count")},
        }
        if entry.get("last_run_at"):
            properties["Last Run At"] = {"date": {"start": entry["last_run_at"]}}
        if entry.get("last_good_at"):
            properties["Last Good At"] = {"date": {"start": entry["last_good_at"]}}

        try:
            q = requests.post(
                f"{NOTION_API}/databases/{db_id}/query",
                headers=headers,
                json={"filter": {"property": "Key", "rich_text": {"equals": key}}},
                timeout=15,
            )
            q.raise_for_status()
            existing = q.json().get("results", [])

            if existing:
                page_id = existing[0]["id"]
                r = requests.patch(
                    f"{NOTION_API}/pages/{page_id}",
                    headers=headers, json={"properties": properties}, timeout=15,
                )
            else:
                r = requests.post(
                    f"{NOTION_API}/pages",
                    headers=headers,
                    json={"parent": {"database_id": db_id}, "properties": properties},
                    timeout=15,
                )
            r.raise_for_status()
        except Exception as e:
            print(f"  [Notion sync] {key}: {e}")
            continue

    print("✓  Notion sync done")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape MA community college courses")
    parser.add_argument(
        "--college",
        choices=list(ALL_SCRAPERS.keys()),
        help="Scrape only one college (omit to scrape all)",
    )
    parser.add_argument(
        "--colleges",
        nargs="+",
        choices=list(ALL_SCRAPERS.keys()),
        metavar="KEY",
        help="Scrape multiple colleges, e.g. --colleges bhcc middlesex",
    )
    parser.add_argument(
        "--output", default=OUTPUT_FILE,
        help=f"Output .xlsx path (default: {OUTPUT_FILE})",
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="Scrape colleges concurrently in threads (faster for multiple colleges; "
             "output from different colleges will interleave in the console)",
    )
    args = parser.parse_args()

    if args.colleges:
        targets = {k: ALL_SCRAPERS[k] for k in args.colleges}
    elif args.college:
        targets = {args.college: ALL_SCRAPERS[args.college]}
    else:
        targets = ALL_SCRAPERS

    all_courses: list[dict] = []
    history  = _load_history()
    warnings: list[str] = []
    warning_keys: set[str] = set()

    if args.parallel and len(targets) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        print(f"Running {len(targets)} colleges in parallel…\n")
        results: dict[str, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=len(targets)) as executor:
            future_to_key = {
                executor.submit(fn): key
                for key, (_, fn) in targets.items()
            }
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                name = targets[key][0]
                try:
                    courses = future.result()
                    results[key] = courses
                    print(f"  ✓  {name}: {len(courses)} courses collected")
                except Exception as e:
                    print(f"  ✗  {name}: scraper failed: {e}")
                    results[key] = []
                warning = _check_regression(key, name, len(results[key]), history)
                if warning:
                    warnings.append(warning)
                    warning_keys.add(key)
        for key in targets:
            all_courses.extend(results.get(key, []))
    else:
        for key, (name, fn) in targets.items():
            print(f"\n{'─' * 60}")
            print(f"Scraping: {name}")
            print(f"{'─' * 60}")
            try:
                courses = fn()
                print(f"  ✓  {len(courses)} courses collected")
                all_courses.extend(courses)
            except Exception as e:
                print(f"  ✗  Scraper failed: {e}")
                courses = []
            warning = _check_regression(key, name, len(courses), history)
            if warning:
                warnings.append(warning)
                warning_keys.add(key)

    _save_history(history)
    _push_history_to_notion(history, warning_keys)

    if warnings:
        print(f"\n{'!' * 60}")
        print("⚠  REGRESSION WARNINGS — these colleges may need attention:")
        for w in warnings:
            print(f"  ⚠  {w}")
        print(f"{'!' * 60}")

    if not all_courses:
        print("\nNo courses collected — nothing to write.")
        return

    print(f"\n{'=' * 60}")
    print(f"Total: {len(all_courses)} courses from {len(targets)} college(s)")

    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    base, ext = os.path.splitext(args.output)
    path      = f"{base}_{ts}{ext}"

    write_xlsx(all_courses, path)


if __name__ == "__main__":
    main()