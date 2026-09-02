# Brandeis Course Equivalency Simulator

A tool that helps Massachusetts community college students — and Brandeis
transfer advisors — see which Brandeis courses their community college
coursework is likely to satisfy, without waiting on a manual, course-by-course
review.

## The problem

Transfer credit articulation is normally a manual process: an advisor
compares a community college's course catalog against Brandeis's catalog by
hand, one course at a time, for every one of the ~15 MA community colleges
that send transfer students to Brandeis. That doesn't scale, and it leaves
prospective transfer students with no way to explore their options before
they apply.

This project automates the comparison. It scrapes every MA community
college's course catalog plus Brandeis's own catalog, computes a semantic
similarity between every community college course and every Brandeis course
(so "Intro to Psych" and "Introduction to Psychology" match even when the
wording differs), and stores the resulting equivalencies in a database that
a self-serve web app can query instantly.

## What the app does

**Course Equivalency Simulator** ([streamlit_app.py](streamlit_app.py)) is
the student-facing half of the project:

1. Pick your community college and search for a course by code or title.
2. The app looks up the precomputed equivalencies and shows up to three
   likely Brandeis matches, each labeled **exact / strong / partial** with a
   similarity score.
3. Click **"Why this match?"** on any result and Claude generates a short,
   plain-language explanation of what the two courses have in common and
   what to confirm with an advisor.
4. **"📖 See [DEPT] courses in the Bulletin"** links out to the matched
   course's department page in Brandeis's official Bulletin, and any core
   requirements the course fulfills (Quantitative Reasoning, Writing
   Intensive, etc.) show as small badges, linked to the Bulletin's
   requirement-code glossary.
5. **"➕ Add to list"** builds a running shortlist (up to 2 courses per
   search) in the sidebar, which exports as a one-page PDF summary for an
   advisor meeting — see [transfer_list_feature.py](transfer_list_feature.py).
6. A persistent disclaimer makes clear this is an advisory estimate, not an
   official transfer credit determination.

## How it works — the pipeline

| Step | Script | Purpose |
|---|---|---|
| 1 | [scrape_courses.py](scrape_courses.py) | Scrapes course catalogs (code, title, description, credits) from ~15 MA community colleges, each with a different site architecture (Acalog, Clean Catalog, CourseDog, static HTML, JSON/XML APIs). |
| 1 | [scrape_brandeis.py](scrape_brandeis.py) | Scrapes Brandeis's own course catalog the same way. |
| 2 | [00_merge_excel.py](00_merge_excel.py), [03b_load_brandeis.py](03b_load_brandeis.py) | Consolidates the scraped Excel exports into a single SQLite database ([db/courses.db](db/courses.db)). |
| 3 | [01_clean_normalize.py](01_clean_normalize.py) | Strips scraped boilerplate out of descriptions, backfills department from course code, dedupes rows, flags rows missing key fields. |
| 4 | [00_filter_untransferable.py](00_filter_untransferable.py), [02_filter_untransferable_v2.py](02_filter_untransferable_v2.py) | Removes courses that can never have a Brandeis equivalent (remedial, ESL, vocational/clinical programs, non-credit, orientation), so the matcher isn't wasting matches on courses that will never transfer. |
| 5 | [04_match_equivalencies_v3.py](04_match_equivalencies_v3.py) | Embeds every course's title + description with an instruction-tuned sentence-transformer (`hkunlp/instructor-base`) and finds each community college course's top-3 most similar Brandeis courses by cosine similarity, tagging each as exact/strong/partial/weak. |
| 6 | [05_evaluate_model.py](05_evaluate_model.py) | Measures matching accuracy/precision/recall against a hand-labeled test set, broken down by match type and by college, and surfaces the worst failures — this is what drives tuning of the matching thresholds. |
| 7 | [06_scrape_bulletin.py](06_scrape_bulletin.py) | Scrapes the Brandeis Bulletin's ~90 subject pages for each course's core-requirement codes (QR, WI, OC, etc.) and builds a department → Bulletin subject-page lookup, both used for the "Official Bulletin" link and requirement badges in the app. See the script's docstring for why it reads the *provisional* Bulletin edition while the app links to the *current* one. |
| 8 | [streamlit_app.py](streamlit_app.py) | Serves the resulting database through the Course Equivalency Simulator, with Claude generating a human-friendly explanation for each match on demand. |

Other utilities: [view_db.py](view_db.py) is a quick CLI for inspecting
`db/courses.db`; [title_cleaning](title_cleaning) normalizes course title
formatting across the per-college exports.

## Running the app locally

```bash
pip install -r requirements.txt

# Windows
set ANTHROPIC_API_KEY=sk-ant-...
# Mac/Linux
export ANTHROPIC_API_KEY=sk-ant-...

streamlit run streamlit_app.py
```

Open http://localhost:8501.

## Re-running the data pipeline

Only needed when catalogs change or a new college is added. Run in order:

```bash
python scrape_courses.py          # omit --college/--colleges to scrape all
python scrape_brandeis.py
python 00_merge_excel.py
python 03b_load_brandeis.py
python 01_clean_normalize.py
python 00_filter_untransferable.py
python 02_filter_untransferable_v2.py
python 04_match_equivalencies_v3.py
python 05_evaluate_model.py
python 06_scrape_bulletin.py
```

`06_scrape_bulletin.py` is the odd one out: it doesn't depend on the scrape
above it and can be re-run independently whenever the Bulletin's content
changes. If it fails partway through the database write (e.g. `courses.db`
open in another program), the scrape itself is checkpointed — retry with
`python 06_scrape_bulletin.py --from-cache` instead of re-scraping.

## Deployment

Hosted for free on [Streamlit Community Cloud](https://share.streamlit.io),
deployed straight from this repo's `main` branch (`streamlit_app.py` as the
entry point). The `ANTHROPIC_API_KEY` used for match explanations is
configured as a Streamlit Cloud secret, not committed to the repo.

## Disclaimer

This is an advisory tool only. Match scores and AI-generated explanations
are estimates meant to help prioritize what to bring to a Brandeis academic
advisor — they do not guarantee transfer credit approval.
