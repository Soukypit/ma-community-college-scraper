"""
Transfer List Feature for the Course Equivalency Simulator
------------------------------------------------------------
Lets a student build a short list of course matches across searches (max 2
added per search) and export it as a PDF for their advisor.

Used by streamlit_app.py:
    from transfer_list_feature import (
        initialize_session_state,
        reset_search_tracking,
        render_sidebar,
    )

INSTALL:
    pip install reportlab
"""

import io
from datetime import datetime

import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── BRAND COLORS (reportlab) ─────────────────────────────────────────────────
BRANDEIS_BLUE = colors.HexColor("#003478")
LIGHT_GRAY    = colors.HexColor("#f8f9fa")
MID_GRAY      = colors.HexColor("#e2e8f0")


# ── SESSION STATE ─────────────────────────────────────────────────────────────

def initialize_session_state():
    """Call this at the TOP of main() before anything else. Initializes the
    transfer list in session state."""
    if "transfer_list" not in st.session_state:
        st.session_state.transfer_list = []
    if "added_this_search" not in st.session_state:
        st.session_state.added_this_search = set()


def reset_search_tracking():
    """Call this when a new search is performed so the 'max 2 per search'
    counter resets correctly."""
    st.session_state.added_this_search = set()


# ── SIDEBAR ───────────────────────────────────────────────────────────────────

def render_sidebar():
    """Renders the Transfer List sidebar: every added course, remove
    buttons, PDF export, and clear-list."""
    with st.sidebar:
        st.markdown("## 📋 My Transfer List")

        if not st.session_state.transfer_list:
            st.markdown(
                '<div style="background:#f8f9fa; border-radius:8px; '
                'padding:1rem; text-align:center; color:#94a3b8; font-size:13px">'
                '🔍 Search for a course and click<br/><b>➕ Add to list</b><br/>'
                'to build your transfer plan.'
                '</div>',
                unsafe_allow_html=True
            )
            return

        n = len(st.session_state.transfer_list)
        st.markdown(
            f'<div style="font-size:13px; color:#64748b; margin-bottom:12px">'
            f'{n} course{"s" if n != 1 else ""} selected for advisor review</div>',
            unsafe_allow_html=True
        )

        badge_colors = {"exact": "#15803d", "strong": "#1d4ed8", "partial": "#a16207"}
        badge_labels = {"exact": "Exact", "strong": "Strong", "partial": "Partial"}

        for idx, item in enumerate(st.session_state.transfer_list):
            mt    = item["match_type"]
            color = badge_colors.get(mt, "#94a3b8")
            label = badge_labels.get(mt, mt)
            pct   = int(item["similarity_score"] * 100)

            st.markdown(
                f"""
                <div style="background:white; border-radius:8px; padding:10px 12px;
                            margin-bottom:8px; border-left:4px solid {color};
                            box-shadow:0 1px 4px rgba(0,0,0,0.06)">
                  <div style="font-size:11px; color:{color}; font-weight:700;
                              margin-bottom:2px">{label} · {pct}%</div>
                  <div style="font-size:12px; color:#64748b">{item['cc_code']}</div>
                  <div style="font-size:10px; color:#94a3b8">↓</div>
                  <div style="font-size:13px; font-weight:600;
                              color:#003478">{item['brd_code']}</div>
                  <div style="font-size:11px; color:#94a3b8">{item['brd_title']}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

            if st.button(
                "✕ Remove",
                key=f"remove_{idx}_{item['brd_code']}",
                use_container_width=True,
            ):
                st.session_state.transfer_list.pop(idx)
                st.session_state.added_this_search.discard(item["brd_code"])
                st.rerun()

        st.markdown("---")

        pdf_bytes = generate_consolidated_pdf(st.session_state.transfer_list)
        st.download_button(
            label="📄 Export PDF for advisor",
            data=pdf_bytes,
            file_name="my_brandeis_transfer_plan.pdf",
            mime="application/pdf",
            use_container_width=True,
            type="primary",
        )

        st.markdown("")
        if st.button("🗑️ Clear entire list", use_container_width=True):
            st.session_state.transfer_list = []
            st.session_state.added_this_search = set()
            st.rerun()


# ── CONSOLIDATED PDF ──────────────────────────────────────────────────────────

def generate_consolidated_pdf(transfer_list: list[dict]) -> bytes:
    """Generates a clean, one-page PDF summarizing every course in the
    transfer list, formatted for a Brandeis academic advisor to sign off on."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.65*inch, rightMargin=0.65*inch,
        topMargin=0.5*inch,  bottomMargin=0.65*inch,
    )
    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(
            '<font color="white" size="14"><b>Brandeis University</b></font><br/>'
            '<font color="#FFCC00" size="9">Transfer Course Equivalency Plan</font>',
            ParagraphStyle("h", fontName="Helvetica-Bold", leading=18)
        ),
        Paragraph(
            f'<font color="white" size="8">Generated: {datetime.now().strftime("%B %d, %Y")}<br/>'
            f'Courses selected: {len(transfer_list)}</font>',
            ParagraphStyle("hr", fontName="Helvetica", alignment=TA_RIGHT)
        ),
    ]]
    ht = Table(header_data, colWidths=[4.5*inch, 2.7*inch])
    ht.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), BRANDEIS_BLUE),
        ("TOPPADDING",    (0,0),(-1,-1), 14),
        ("BOTTOMPADDING", (0,0),(-1,-1), 14),
        ("LEFTPADDING",   (0,0),(0,-1),  16),
        ("RIGHTPADDING",  (-1,0),(-1,-1),16),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]))
    story.append(ht)
    story.append(Spacer(1, 0.2*inch))

    # ── Intro line ────────────────────────────────────────────────────────────
    story.append(Paragraph(
        '<font size="9" color="#475569">The following courses were selected by the student '
        'as potential transfer equivalencies. All matches are AI-generated and require '
        'official advisor confirmation before enrollment decisions are made.</font>',
        ParagraphStyle("note", fontName="Helvetica", leading=13)
    ))
    story.append(Spacer(1, 0.2*inch))

    # ── Course rows ───────────────────────────────────────────────────────────
    header_row = [
        Paragraph('<font size="8" color="white"><b>YOUR COURSE</b></font>',
                  ParagraphStyle("th", fontName="Helvetica-Bold")),
        Paragraph('<font size="8" color="white"><b></b></font>',
                  ParagraphStyle("th", fontName="Helvetica-Bold")),
        Paragraph('<font size="8" color="white"><b>BRANDEIS COURSE</b></font>',
                  ParagraphStyle("th", fontName="Helvetica-Bold")),
        Paragraph('<font size="8" color="white"><b>DEPT</b></font>',
                  ParagraphStyle("th", fontName="Helvetica-Bold", alignment=TA_CENTER)),
        Paragraph('<font size="8" color="white"><b>MATCH</b></font>',
                  ParagraphStyle("th", fontName="Helvetica-Bold", alignment=TA_CENTER)),
        Paragraph('<font size="8" color="white"><b>SCORE</b></font>',
                  ParagraphStyle("th", fontName="Helvetica-Bold", alignment=TA_CENTER)),
    ]

    table_data = [header_row]

    badge_colors_hex = {"exact": "#15803d", "strong": "#1d4ed8", "partial": "#a16207"}
    badge_labels = {"exact": "Exact", "strong": "Strong", "partial": "Partial"}

    for item in transfer_list:
        mt    = item["match_type"]
        color = badge_colors_hex.get(mt, "#94a3b8")
        label = badge_labels.get(mt, mt)
        pct   = int(item["similarity_score"] * 100)

        row = [
            Paragraph(
                f'<font size="9"><b>{item["cc_code"]}</b></font><br/>'
                f'<font size="8" color="#475569">{item["cc_title"][:45]}</font>',
                ParagraphStyle("cc", fontName="Helvetica", leading=13)
            ),
            Paragraph('<font size="12" color="#94a3b8">→</font>',
                      ParagraphStyle("arr", fontName="Helvetica", alignment=TA_CENTER)),
            Paragraph(
                f'<font size="9" color="#003478"><b>{item["brd_code"]}</b></font><br/>'
                f'<font size="8" color="#475569">{item["brd_title"][:45]}</font>',
                ParagraphStyle("brd", fontName="Helvetica", leading=13)
            ),
            Paragraph(
                f'<font size="8" color="#475569">'
                f'{item.get("brd_department","—")}</font>',
                ParagraphStyle("dept", fontName="Helvetica", alignment=TA_CENTER)
            ),
            Paragraph(
                f'<font size="8" color="{color}"><b>{label}</b></font>',
                ParagraphStyle("badge", fontName="Helvetica-Bold", alignment=TA_CENTER)
            ),
            Paragraph(
                f'<font size="9"><b>{pct}%</b></font>',
                ParagraphStyle("score", fontName="Helvetica-Bold", alignment=TA_CENTER)
            ),
        ]
        table_data.append(row)

    course_table = Table(
        table_data,
        colWidths=[1.7*inch, 0.3*inch, 1.9*inch, 0.7*inch, 0.85*inch, 0.85*inch],
        repeatRows=1,
    )

    style = [
        ("BACKGROUND",    (0,0),(-1,0),   BRANDEIS_BLUE),
        ("TOPPADDING",    (0,0),(-1,-1),  8),
        ("BOTTOMPADDING", (0,0),(-1,-1),  8),
        ("LEFTPADDING",   (0,0),(-1,-1),  8),
        ("RIGHTPADDING",  (0,0),(-1,-1),  8),
        ("VALIGN",        (0,0),(-1,-1),  "MIDDLE"),
        ("GRID",          (0,0),(-1,-1),  0.5, MID_GRAY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),  [colors.white, LIGHT_GRAY]),
    ]
    course_table.setStyle(TableStyle(style))
    story.append(course_table)
    story.append(Spacer(1, 0.2*inch))

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_data = [[
        Paragraph('<font size="8" color="#15803d"><b>■ Exact</b></font>'
                  '<font size="8" color="#475569">  ≥90% similarity</font>',
                  ParagraphStyle("l", fontName="Helvetica")),
        Paragraph('<font size="8" color="#1d4ed8"><b>■ Strong</b></font>'
                  '<font size="8" color="#475569">  87–90%</font>',
                  ParagraphStyle("l", fontName="Helvetica")),
        Paragraph('<font size="8" color="#a16207"><b>■ Partial</b></font>'
                  '<font size="8" color="#475569">  80–87%</font>',
                  ParagraphStyle("l", fontName="Helvetica")),
    ]]
    lt = Table(legend_data, colWidths=[2.4*inch, 1.8*inch, 3.0*inch])
    lt.setStyle(TableStyle([
        ("BACKGROUND",    (0,0),(-1,-1), LIGHT_GRAY),
        ("TOPPADDING",    (0,0),(-1,-1), 6),
        ("BOTTOMPADDING", (0,0),(-1,-1), 6),
        ("LEFTPADDING",   (0,0),(-1,-1), 8),
        ("BOX",           (0,0),(-1,-1), 0.5, MID_GRAY),
    ]))
    story.append(lt)

    # ── Signature line for advisor ─────────────────────────────────────────────
    story.append(Spacer(1, 0.25*inch))
    sig_data = [[
        Paragraph('<font size="8" color="#475569">Advisor signature: ___________________________</font>',
                  ParagraphStyle("sig", fontName="Helvetica")),
        Paragraph('<font size="8" color="#475569">Date: _________________</font>',
                  ParagraphStyle("sig", fontName="Helvetica", alignment=TA_RIGHT)),
    ]]
    st_sig = Table(sig_data, colWidths=[4.0*inch, 3.2*inch])
    st_sig.setStyle(TableStyle([
        ("TOPPADDING",    (0,0),(-1,-1), 4),
        ("BOTTOMPADDING", (0,0),(-1,-1), 4),
    ]))
    story.append(st_sig)

    # ── Disclaimer footer ─────────────────────────────────────────────────────
    story.append(Spacer(1, 0.1*inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GRAY))
    story.append(Spacer(1, 0.08*inch))
    story.append(Paragraph(
        '<font size="7.5" color="#64748b">'
        '<b>⚠️ Advisory use only.</b> '
        'Matches are AI-generated and have not been officially reviewed by Brandeis University. '
        'Similarity scores reflect semantic overlap between course descriptions — '
        'they do not guarantee transfer credit approval. '
        'All equivalencies must be confirmed by a Brandeis academic advisor.<br/>'
        'Office of the Registrar · Brandeis University · registrar@brandeis.edu'
        '</font>',
        ParagraphStyle("disc", fontName="Helvetica", leading=11)
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer.read()
