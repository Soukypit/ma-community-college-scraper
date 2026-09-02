"""
Brandeis University — Course Equivalency Simulator
Streamlit App

INSTRUCTIONS:
    1. Install dependencies:
           pip install streamlit anthropic pandas

    2. Set your Claude API key:
           Windows: set ANTHROPIC_API_KEY=sk-ant-...
           Mac/Linux: export ANTHROPIC_API_KEY=sk-ant-...

    3. Run:
           streamlit run streamlit_app.py

    4. Open browser at:
           http://localhost:8501

DEPLOYING TO STREAMLIT COMMUNITY CLOUD (free):
    1. Push this file + courses.db to a GitHub repo
    2. Go to share.streamlit.io
    3. Connect your GitHub repo
    4. Add ANTHROPIC_API_KEY in the Secrets section
    5. Deploy — you get a free shareable URL instantly
"""

import os
import sqlite3
import anthropic
import pandas as pd
import streamlit as st
from pathlib import Path

from transfer_list_feature import (
    initialize_session_state,
    reset_search_tracking,
    render_sidebar,
)
from bulletin_integration import (
    get_bulletin_url,
    get_requirement_codes,
    REQUIREMENT_LABELS,
    REQCODES_URL,
)

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Resolved relative to this file's location (not the shell's cwd), so it works
# whether you run `streamlit run streamlit_app.py` from the repo root or
# somewhere else.
DB_PATH = Path(__file__).resolve().parent / "db" / "courses.db"

# Brandeis brand colors
BRANDEIS_BLUE = "#003478"
BRANDEIS_GREY = "#9BA4B7"

# ── PAGE CONFIG ─────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Brandeis Transfer Course Equivalency Simulator",
    page_icon="🔀",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ── CUSTOM CSS ──────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
  /* Header */
  .brandeis-header {{
    background: {BRANDEIS_BLUE};
    color: white;
    padding: 1.25rem 1.5rem;
    border-radius: 10px;
    border-bottom: 4px solid {BRANDEIS_GREY};
    margin-bottom: 1.5rem;
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .brandeis-header h1 {{
    font-size: 1.4rem;
    margin: 0;
    font-weight: 700;
  }}
  .brandeis-header p {{
    margin: 0;
    font-size: 0.85rem;
    opacity: 0.8;
  }}

  /* Result cards */
  .result-card {{
    background: white;
    border-radius: 10px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }}
  .card-exact   {{ border-left: 5px solid #15803d; }}
  .card-strong  {{ border-left: 5px solid #1d4ed8; }}
  .card-partial {{ border-left: 5px solid #a16207; }}

  /* Badges */
  .badge {{
    display: inline-block;
    font-size: 11px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 99px;
    margin-left: 8px;
    vertical-align: middle;
  }}
  .badge-exact   {{ background: #dcfce7; color: #15803d; }}
  .badge-strong  {{ background: #dbeafe; color: #1d4ed8; }}
  .badge-partial {{ background: #fef9c3; color: #a16207; }}

  /* Score bar */
  .score-bar-wrap {{
    margin: 8px 0;
  }}
  .score-bar-bg {{
    background: #f0f2f5;
    border-radius: 99px;
    height: 5px;
    overflow: hidden;
    margin-top: 4px;
  }}

  /* Disclaimer */
  .disclaimer {{
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 8px;
    padding: 12px 16px;
    font-size: 13px;
    color: #92400e;
    margin-top: 1.5rem;
    line-height: 1.5;
  }}

  /* Arrow */
  .arrow {{ color: #94a3b8; font-size: 1.2rem; margin: 4px 0; }}

  /* Hide streamlit branding. Deliberately NOT hiding the whole <header> --
     the sidebar's collapse/expand toggle lives inside that same element,
     so doing that leaves a collapsed sidebar with no way to reopen it.
     Target only the menu/deploy button instead. */
  #MainMenu {{ visibility: hidden; }}
  footer {{ visibility: hidden; }}
  [data-testid="stMainMenu"] {{ visibility: hidden; }}
  [data-testid="stToolbarActions"] {{ visibility: hidden; }}
  [data-testid="stAppDeployButton"] {{ visibility: hidden; }}
</style>
""", unsafe_allow_html=True)


# ── DATABASE FUNCTIONS ──────────────────────────────────────────────────────────

@st.cache_resource
def get_connection():
    """Cached database connection."""
    if not DB_PATH.exists():
        st.error(f"Database not found at: {DB_PATH}")
        st.stop()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data
def load_colleges():
    """Load all college names — cached so it only runs once."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT college_name
        FROM community_courses_clean
        WHERE college_name IS NOT NULL
        ORDER BY college_name
    """).fetchall()
    return [r["college_name"] for r in rows]


@st.cache_data
def load_courses(college: str) -> list[dict]:
    """Load every course offered at one college — cached per college so
    switching between colleges doesn't re-hit the DB on every rerun."""
    if not college:
        return []
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT community_course_id, cc_code, cc_title
        FROM equivalencies_v3
        WHERE cc_college = ?
        ORDER BY cc_code
    """, (college,)).fetchall()
    return [dict(r) for r in rows]


def search_equivalencies(community_course_id: int) -> list[dict]:
    """Fetch the top-3 ranked Brandeis matches (rank 1-3, as stored by
    04_match_equivalencies_v3.py) for one specific CC course, best first."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            e.cc_code,
            e.cc_title,
            e.brd_code,
            e.brd_title,
            e.brd_department,
            e.similarity_score,
            e.match_type,
            cc.description  AS cc_description,
            b.description   AS brd_description,
            b.credits       AS brd_credits
        FROM equivalencies_v3 e
        JOIN community_courses_clean cc ON e.community_course_id = cc.id
        JOIN brandeis_courses b         ON e.brandeis_course_id  = b.id
        WHERE e.community_course_id = ?
          AND e.match_type != 'weak'
        ORDER BY e.rank ASC
        LIMIT 3
    """, (community_course_id,)).fetchall()

    return [dict(r) for r in rows]


# ── AI EXPLANATION ───────────────────────────────────────────────────────────────

def get_ai_explanation(cc_title, cc_desc, brd_title, brd_desc, match_type, score):
    """Call Claude API to explain the match."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or st.secrets.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        return "⚠️ Claude API key not configured. Please set ANTHROPIC_API_KEY to enable explanations."

    try:
        client = anthropic.Anthropic(api_key=api_key)

        confidence_context = {
            "exact":   "This is a high-confidence match.",
            "strong":  "This is a good match; advisor review is still recommended.",
            "partial": "This is a possible match that should be confirmed with an advisor.",
        }.get(match_type, "")

        prompt = f"""You are an academic advisor at Brandeis University helping a transfer student understand course equivalencies with this simulator.

A community college course has been matched to a Brandeis course:

COMMUNITY COLLEGE COURSE:
Title: {cc_title}
Description: {cc_desc or "No description available"}

BRANDEIS COURSE:
Title: {brd_title}
Description: {brd_desc or "No description available"}

Match confidence: {match_type} ({score:.0%} similarity)

In 2-3 sentences explain:
1. Why these courses are considered equivalent (what content they share)
2. Whether a Brandeis advisor would likely accept this transfer credit
3. Any important caveats the student should know

Be honest — if the match is partial, say so. Keep it conversational, helpful and friendly, as if you were speaking directly to the student. Avoid generic filler phrases like "Please consult your advisor" or "This is for informational purposes only.
Also in your explanation take into account that this is a simulation, possible macthes are based on course titles and descriptions, and the final decision is made by Brandeis University.

"""

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text

    except Exception as e:
        return f"Unable to generate explanation: {str(e)}"


# ── HELPER FUNCTIONS ─────────────────────────────────────────────────────────────

def badge_html(match_type):
    labels = {"exact": "Exact match", "strong": "Strong match", "partial": "Partial match"}
    return f'<span class="badge badge-{match_type}">{labels.get(match_type, match_type)}</span>'


def match_description(match_type):
    desc = {
        "exact":   "✅ High confidence: likely equivalent",
        "strong":  "🔵 Good match: advisor review recommended",
        "partial": "⚠️ Possible match: confirm with advisor",
    }
    return desc.get(match_type, "")


def score_bar_html(score, match_type):
    colors = {"exact": "#15803d", "strong": "#1d4ed8", "partial": "#a16207"}
    color = colors.get(match_type, "#94a3b8")
    pct   = int(score * 100)
    # Built as one flat line (no newlines/indentation) — Streamlit's markdown
    # renderer treats indented multi-line HTML as a code block / literal text
    # instead of parsing it, even with unsafe_allow_html=True.
    return (
        f'<div class="score-bar-wrap">'
        f'<small style="color:#64748b">Similarity: <strong>{pct}%</strong></small>'
        f'<div class="score-bar-bg">'
        f'<div style="width:{pct}%;height:100%;background:{color};border-radius:99px;"></div>'
        f'</div></div>'
    )


# ── MAIN APP ───────────────────────────────────────────────────────────────────

def main():
    initialize_session_state()
    render_sidebar()

    # ── HEADER ────────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="brandeis-header">
      <div style="background:#FFCC00;color:#003478;font-weight:800;
                  font-size:1.3rem;width:42px;height:42px;border-radius:8px;
                  display:flex;align-items:center;justify-content:center;
                  flex-shrink:0">B</div>
      <div>
        <h1>Brandeis University</h1>
        <p>Course Equivalency Simulator</p>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("#### Find your Brandeis equivalents")
    st.markdown(
        "Search for a course from your community college and discover "
        "which Brandeis courses it may transfer as.",
        unsafe_allow_html=False
    )
    st.divider()

    # ── SEARCH FORM ───────────────────────────────────────────────────────────
    colleges = load_colleges()

    col1, col2 = st.columns([1, 1])

    with col1:
        selected_college = st.selectbox(
            "Your community college",
            options=["— Select your college —"] + colleges,
            index=0,
        )

    with col2:
        courses = load_courses(selected_college) if selected_college != "— Select your college —" else []

        # Maps the human-readable label shown in the dropdown back to the
        # community_course_id search_equivalencies() needs. A plain dict
        # (not a DB round-trip) since `courses` is already cached per college.
        course_options = {"— Select your course —": None}
        course_options.update({
            f"{c['cc_code']} — {c['cc_title']}": c["community_course_id"]
            for c in courses
        })

        # st.selectbox is searchable by default — typing filters the list,
        # so students don't have to scroll to find their course. Keying it
        # on selected_college resets the pick whenever the college changes,
        # instead of leaving a stale selection from the previous college.
        selected_course_label = st.selectbox(
            "Course code or title",
            options=list(course_options.keys()),
            index=0,
            key=f"course_select_{selected_college}",
            disabled=not courses,
            help="Start typing to filter by course code or title.",
        )
        selected_course_id = course_options[selected_course_label]

    search_clicked = st.button(
        "🔍 Search",
        type="primary",
        use_container_width=True,
    )

    # ── SEARCH STATE ──────────────────────────────────────────────────────────
    # Streamlit reruns the whole script on every widget interaction, and
    # st.button() only returns True on the exact rerun right after it was
    # clicked. If results were rendered straight from `if search_clicked:`,
    # then clicking anything else afterward (like "Generate explanation")
    # would make search_clicked False again and the entire results block —
    # including the button just clicked — would vanish. Persisting the
    # search in session_state means results stay on screen across reruns
    # triggered by other widgets.
    st.session_state.setdefault("search_results", None)
    st.session_state.setdefault("search_query", "")
    st.session_state.setdefault("search_college", "")
    st.session_state.setdefault("explanations", {})

    if search_clicked:
        # Validation
        if selected_college == "— Select your college —":
            st.warning("Please select your community college.")
        elif not selected_course_id:
            st.warning("Please select a course.")
        else:
            reset_search_tracking()  # new search — resets the 2-per-search add cap
            with st.spinner("Searching..."):
                results = search_equivalencies(selected_course_id)
            st.session_state["search_results"] = results
            st.session_state["search_query"]   = selected_course_label
            st.session_state["search_college"] = selected_college
            st.session_state["explanations"]   = {}  # new search, clear old explanations

    # ── RESULTS ───────────────────────────────────────────────────────────────
    results = st.session_state["search_results"]

    if results is not None:
        if not results:
            st.info(
                f"No courses found for **{st.session_state['search_query']}** "
                f"at **{st.session_state['search_college']}**. "
                "Try a different course code or title."
            )
        else:
            st.markdown(
                f"**{len(results)} Brandeis equivalent{'s' if len(results) != 1 else ''}** "
                f"for **{st.session_state['search_query']}** at {st.session_state['search_college']}:"
            )
            st.markdown("")

        # Render each result card
        for i, r in enumerate(results or []):
            match_type = r["match_type"]
            score      = r["similarity_score"]

            # Card — built as one flat HTML string (no embedded newlines).
            # Streamlit's markdown renderer mis-parses multi-line/indented
            # HTML as literal text or a code block even with
            # unsafe_allow_html=True, so every nested piece here is joined
            # on a single line instead.
            dept_html    = f"Dept: {r['brd_department']}" if r['brd_department'] else ""
            credits_html = f"&nbsp;·&nbsp; {r['brd_credits']} credits" if r['brd_credits'] else ""

            # Core requirement chips (Quantitative Reasoning, Science, etc.) —
            # a Brandeis match that also knocks out a gen-ed requirement is a
            # real point in its favor, not just decoration. Codes/labels come
            # from 06_scrape_bulletin.py via bulletin_integration.py.
            req_codes = get_requirement_codes(r["brd_code"])
            if req_codes:
                chips = "".join(
                    f'<span style="display:inline-block;font-size:10px;font-weight:700;'
                    f'background:#eef2ff;color:#4338ca;border-radius:99px;padding:2px 8px;'
                    f'margin-left:4px" title="{REQUIREMENT_LABELS.get(c, c.upper())}">{c.upper()}</span>'
                    for c in sorted(req_codes)
                )
                req_html = (
                    f'&nbsp;·&nbsp;Fulfills:{chips}'
                    f' <a href="{REQCODES_URL}" target="_blank" style="font-size:10px;'
                    f'color:#94a3b8;text-decoration:none" title="What do these mean?">ⓘ</a>'
                )
            else:
                req_html = ""

            card_html = (
                f'<div class="result-card card-{match_type}">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">'
                f'<div style="flex:1">'
                f'<div style="font-size:13px;color:#64748b;margin-bottom:4px">'
                f'<strong>{r["cc_code"]}</strong> &nbsp;{r["cc_title"]}</div>'
                f'<div class="arrow">→</div>'
                f'<div style="font-size:16px;font-weight:700;color:{BRANDEIS_BLUE}">'
                f'<strong>{r["brd_code"]}</strong> &nbsp;{r["brd_title"]}</div>'
                f'<div style="font-size:12px;color:#94a3b8;margin-top:2px">{dept_html}{credits_html}{req_html}</div>'
                f'</div>'
                f'<div>{badge_html(match_type)}</div>'
                f'</div>'
                f'{score_bar_html(score, match_type)}'
                f'<div style="font-size:12px;color:#64748b;margin-top:6px">{match_description(match_type)}</div>'
                f'</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

            # ── ADD TO TRANSFER LIST ─────────────────────────────────────────
            already_in_list = any(
                item["cc_code"] == r["cc_code"] and item["brd_code"] == r["brd_code"]
                for item in st.session_state.transfer_list
            )
            already_added_this_search = r["brd_code"] in st.session_state.added_this_search
            limit_reached = len(st.session_state.added_this_search) >= 2

            link_col, add_col = st.columns([3, 1])
            with link_col:
                bulletin_url = get_bulletin_url(r["brd_code"], r.get("brd_department", ""))
                if bulletin_url:
                    # Links to the department's page in the Bulletin, not the
                    # exact course — the Bulletin has no per-course anchor to
                    # link to (see 06_scrape_bulletin.py for why).
                    st.markdown(
                        f'<a href="{bulletin_url}" target="_blank" style="font-size:13px;'
                        f'color:#1d4ed8;text-decoration:none;padding:6px 0;display:inline-block">'
                        f'📖 See {r.get("brd_department", "")} courses in the Bulletin</a>',
                        unsafe_allow_html=True,
                    )
            with add_col:
                if already_in_list:
                    st.markdown(
                        '<div style="text-align:right;font-size:13px;color:#15803d;padding:6px 0">'
                        '✅ In your list</div>',
                        unsafe_allow_html=True,
                    )
                elif limit_reached and not already_added_this_search:
                    st.markdown(
                        '<div style="text-align:right;font-size:12px;color:#94a3b8;padding:6px 0">'
                        'Max 2 per search</div>',
                        unsafe_allow_html=True,
                    )
                elif st.button("➕ Add to list", key=f"add_{i}_{r['brd_code']}", use_container_width=True):
                    st.session_state.transfer_list.append({
                        "cc_college":       st.session_state["search_college"],
                        "cc_code":          r["cc_code"],
                        "cc_title":         r["cc_title"],
                        "brd_code":         r["brd_code"],
                        "brd_title":        r["brd_title"],
                        "match_type":       match_type,
                        "similarity_score": score,
                        "brd_department":   r.get("brd_department", ""),
                        "brd_credits":      r.get("brd_credits"),
                    })
                    st.session_state.added_this_search.add(r["brd_code"])
                    st.rerun()

            # Course descriptions — always visible, no need to trigger the
            # AI explanation first.
            with st.expander(f"📄 Course descriptions — {r['cc_code']} → {r['brd_code']}"):
                st.markdown(f"**{selected_college} — {r['cc_code']}:** "
                            f"{r.get('cc_description') or 'No description available'}")
                st.markdown("---")
                st.markdown(f"**Brandeis — {r['brd_code']}:** "
                            f"{r.get('brd_description') or 'No description available'}")

            # AI explanation expander — the generated text is stored in
            # session_state and re-displayed from there on every rerun, so
            # it doesn't disappear once you interact with anything else.
            with st.expander(f"✨ Why this match? ({r['brd_code']})"):
                explain_key = f"explain_{i}_{r['brd_code']}"
                if st.button("Generate explanation", key=f"btn_{explain_key}"):
                    with st.spinner("Generating explanation..."):
                        st.session_state["explanations"][explain_key] = get_ai_explanation(
                            cc_title   = r["cc_title"],
                            cc_desc    = r.get("cc_description", ""),
                            brd_title  = r["brd_title"],
                            brd_desc   = r.get("brd_description", ""),
                            match_type = match_type,
                            score      = score,
                        )
                if explain_key in st.session_state["explanations"]:
                    st.info(st.session_state["explanations"][explain_key])

        if results and len(st.session_state.added_this_search) >= 2:
            st.info(
                "✅ You've added 2 courses from this search. "
                "Search for another course to add more to your list."
            )

    # ── DISCLAIMER ────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="disclaimer">
      <strong>⚠️ Advisory tool only.</strong>
      These matches are estimates and simulates the potential of each course for transfer credit.
      The matches should be confirmed with a Brandeis
      academic advisor before making enrollment decisions. Match confidence
      scores are provided to help prioritize advisor review — they do not
      guarantee transfer credit approval.
    </div>
    """, unsafe_allow_html=True)

    # ── FOOTER ────────────────────────────────────────────────────────────────
    st.divider()
    st.markdown(
        "<div style='text-align:center;font-size:12px;color:#94a3b8'>"
        "Brandeis University — Office of the Registrar &nbsp;|&nbsp; "
        "AI-powered course matching &nbsp;|&nbsp; "
        "<a href='mailto:registrar@brandeis.edu'>Contact an Advisor</a>"
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
