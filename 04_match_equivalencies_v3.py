"""
PHASE 2: Course Equivalency Matcher
Uses sentence-transformers to embed every course (title + description),
then finds the top-3 most similar Brandeis courses for each community
college course via cosine similarity.

Embedding model: hkunlp/instructor-base — an instruction-tuned model, so
every text is embedded together with an instruction describing the task
(INSTRUCTION below) rather than embedded raw. sentence-transformers loads
it directly and replicates INSTRUCTOR's pooling (instruction tokens are
excluded from the mean-pool via `prompt=`), so no separate InstructorEmbedding
package is needed.

INSTRUCTIONS:
    1. Install dependencies (one-time):
           pip install sentence-transformers numpy pandas

    2. Run:
           python 04_match_equivalencies.py

    First run downloads the embedding model (~840 MB, cached after that).
    Runtime: ~10–20 minutes depending on your machine (slower than a small
    model like MiniLM since instructor-base is a much larger 768-dim model).

OUTPUT:
    Populates the `equivalencies` table in db/courses.db
    Exports data/processed/equivalencies.csv for review
"""

import os
import re
import sqlite3
import time
import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "db", "courses.db")
OUT_CSV    = os.path.join(BASE_DIR, "data", "processed", "equivalencies_v3.csv")
MODEL      = "hkunlp/instructor-base"   # instruction-tuned, 768-dim, ~840 MB download
INSTRUCTION = ("Represent this undergraduate college course title and description "
               "for the purpose of finding equivalent courses at a transfer university:")
TOP_K      = 3                     # top matches per CC course


# ── HELPERS ───────────────────────────────────────────────────────────────────

# Boilerplate words that show up in nearly every course description regardless
# of subject (e.g. "This course introduces students to ..."). Left in, they
# dilute the embedding with noise common to every course instead of the
# subject-specific content that actually distinguishes one course from
# another, so they're stripped before embedding.
ACADEMIC_STOPWORDS = {
    "this", "course", "students", "will", "upon", "completion",
    "introduction", "study", "topics", "including", "basic",
    "covers", "examines", "explores", "provides", "overview",
    "designed", "focus", "focuses", "emphasis", "areas",
}

def clean_description(desc: str) -> str:
    if not isinstance(desc, str): return ""
    words = desc.lower().split()
    return " ".join(w for w in words
                    if w not in ACADEMIC_STOPWORDS and len(w) > 2)


def clean_title(title: str) -> str:
    if not isinstance(title, str): return ""
    return " ".join(title.strip().split())


def detect_level(course_code: str, title: str, is_cc: bool = True) -> str:
    """
    Classify a course's level from its number, so the embedding can tell an
    intro course apart from an advanced one on the same topic instead of
    matching on subject words alone.
    Numbering conventions differ by side: Brandeis is 1-99 = intro undergrad,
    100-199 = advanced undergrad (200+ is already excluded from the matching
    pool entirely -- see the graduate-level filter below). CC numbering runs
    higher before it's "advanced", so it gets its own cutoff.
    """
    code    = str(course_code or "").upper()
    title_l = str(title or "").lower()
    m   = re.search(r"(\d+)", code)
    num = int(m.group(1)) if m else 0
    if "graduate" in title_l:
        return "graduate level"
    cutoff = 200 if is_cc else 100
    if num >= cutoff:
        return "advanced undergraduate"
    return "introductory undergraduate"


def build_text(row: pd.Series, is_cc: bool = True) -> str:
    """
    Combine title + description into one string for embedding. Description
    is weighted first and repeated (title alone was found to dominate the
    embedding otherwise), title follows, and the detected course level is
    appended so the embedding captures level as well as topic. Description
    has generic academic boilerplate stripped out first (see
    ACADEMIC_STOPWORDS) so the embedding focuses on subject-specific content.
    """
    title = clean_title(str(row.get("course_title") or ""))
    desc  = clean_description(str(row.get("description") or ""))
    level = detect_level(str(row.get("course_code") or ""), title, is_cc)
    if title and desc:
        return f"{desc}. {desc}. {title}. {level}"[:650]
    return f"{title}. {level}"


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    Compute cosine similarity between every row of a and every row of b.
    Returns an (len(a), len(b)) matrix. Much faster than looping.
    """
    a_norm = a / np.linalg.norm(a, axis=1, keepdims=True)
    b_norm = b / np.linalg.norm(b, axis=1, keepdims=True)
    return np.dot(a_norm, b_norm.T)


NO_MATCH_LABEL = "No direct Brandeis equivalent found"


#def score_label(score: float) -> str:
   # if score >= 0.90: return "exact"
#if score >= 0.75: return "strong"
    #if score >= 0.60: return "partial"
    #return NO_MATCH_LABEL

def score_label(score: float) -> str:
    if score >= 0.90: return "exact"
    if score >= 0.87: return "strong"   # was 0.75
    if score >= 0.80: return "partial"  # was 0.60
    return "weak"                        # below 0.80 = not shown in web app


# ── DEPARTMENT PRE-FILTER ────────────────────────────────────────────────────
# Community college course codes come from 17 different registrars and use
# wildly inconsistent department abbreviations (e.g. accounting shows up as
# ACC, ACCT, ACCOUNTG, BUSADM, ...). Each group below maps the CC-side
# variants of one subject area to the Brandeis department code(s) that cover
# it, so a course is only compared against Brandeis courses in a relevant
# field instead of the entire 563-course catalog. CC departments with no
# entry here fall back to comparing against all Brandeis courses — an
# imperfect match beats silently excluding a course from matching entirely.
DEPARTMENT_CATEGORIES: list[tuple[set[str], set[str]]] = [
    ({"ACC", "ACCT", "ACCOUNTG", "BUS", "BUSN", "BUSADM", "BUSI", "MGT", "MGMT",
      "MANAGMNT", "MKT", "MKTG", "MARKETNG", "HRM", "ENTR", "OIM", "POM", "POMS",
      "MIS", "MSIS", "MSIT"},
     {"BUS"}),
    ({"FIN", "FINA", "FINANCE"}, {"FIN", "BUS"}),
    ({"ECO", "ECON", "ECN"}, {"ECON", "BUS"}),
    ({"CS", "CSC", "CIS", "CIT", "COMP", "COMPSCI", "COSC", "IT", "IST", "INFO",
      "CSE", "CSI", "CSP", "CSS", "DGMD", "DACSS", "BIT", "MSIT"},
     {"COSI"}),
    ({"MAT", "MATH", "MTH", "MA"}, {"MATH"}),
    ({"BIO", "BIOL", "BIOCHM", "BIOCH", "MICRO"},
     {"BIOL", "BCHM"}),
    ({"CHM", "CHEM", "CHE"}, {"CHEM"}),
    ({"PHY", "PHYS", "PHYSIC"}, {"PHYS", "NPHY"}),
    ({"ENGR", "EGR", "MNE", "EET", "CET", "CIVE", "MECH", "ENGIN",
      "ENGT", "ENGN", "EECE", "CEN", "IENG", "BMEN", "BME", "EEE",
      "ELM", "EPU", "EGT", "ENR"},
     {"ENGR"}),
    # removed ECE (Early Childhood Ed collision), CHEN (grad-level Chemical Eng)
    # added EEE, ELM, EPU, EGT; ENR moved here from ENVIRONMENTAL below (was miscategorized)
    ({"PSY", "PSYC", "PSYCH", "NPSY"}, {"PSYC", "NPSY", "NBIO"}),
    # removed PSYCLN, PSYDBS (graduate -- should be filtered); added NBIO (neuroscience belongs with psych)
    ({"SOC", "SOCI", "SOCIOL", "SO"}, {"SOC"}),
    ({"ANT", "ANTH", "ANTHRO"}, {"ANTH"}),
    ({"POL", "POLI", "POLSCI", "POLISCI", "PSC", "GOV", "GOVT", "PLS", "PUBADM",
      "PUBPOL"}, {"POL"}),
    ({"HIS", "HIST", "HISTORY"}, {"HIST"}),
    ({"PHL", "PHI", "PHIL"}, {"PHIL"}),
    ({"ENG", "ENGL", "ENGLISH", "LIT"}, {"ENG"}),
    # removed WRT, RDG, RDL, RDT -- developmental/remedial, not academic English
    ({"ART", "ARTG", "ARTS", "ARH", "ARHI", "VMA", "GRFX", "IMD", "DAS", "VISN"},
     {"FA"}),
    ({"MUS", "MUSIC", "MUSC", "MUED", "MUBU"}, {"MUS"}),
    ({"THE", "THA", "THR", "THET", "THEA", "THRART", "DAN", "DANCE", "DANC"},
     {"THA"}),
    ({"FLM", "FILM", "CINE"}, {"FILM"}),
    ({"COM", "COMM", "JRN", "JOUR", "JOURNAL"}, {"JOUR", "AMST"}),
    ({"PUBH", "PUBHTH", "PUBHLTH", "HEA", "HSCI", "HSV"},
     {"HSSP", "HS"}),
    # everything else (NUR, RAD, EMT, OTA, PTA, DHY, DEN, VET, RCP, MRT, GERON,
    # ALH, HIT, MLSP, ...) is vocational/clinical -- belongs in FILTER_DEPTS
    # (02_filter_untransferable_v2.py), not matched here
    ({"EDU", "EDUC", "ED", "EDC", "EDLDRS"}, {"ED"}),
    ({"CRJ", "CRIM", "CJ", "CJS", "CJP", "CJU", "CJUS", "LAW", "LGL", "LEGAL"},
     {"LGLS", "SOC"}),
    ({"WGS", "GWS", "GNDR"}, {"WGS"}),
    ({"ENV", "ENVS", "ENVSCI", "ENVSTY", "SUS", "SUSTCOMM"}, {"ENVS"}),
    # ENR moved to ENGINEERING category above (was miscategorized)
    ({"SPA", "SPN", "SPAN"}, {"HISP"}),
    ({"FRN", "FRE", "FRENCH", "FR", "FRC", "FRH"}, {"FREN"}),
    ({"GER", "GERMAN"}, {"GER"}),
    ({"ITL", "ITAL", "ITA"}, {"ITAL"}),
    ({"CHN", "CHINSE", "CHINESE"}, {"CHIN"}),
    ({"JPN", "JAPAN", "JAPN"}, {"JAPN"}),
    ({"RUS"}, {"RUS"}),
    ({"KOR"}, {"KOR"}),
    ({"LING", "LINGUIST"}, {"LING"}),
    ({"REL", "RELG", "RELSTY"}, {"REL"}),
    ({"LAT", "LATIN", "GREEK", "GRK", "CLSICS", "CLAS"}, {"CLAS", "LAT", "GRK"}),
    ({"AFRSTY", "AFROAM", "BLST", "BLS", "AAC"}, {"AAAS"}),
    ({"AMST", "ASAMST"}, {"AMST"}),

    # ── Migration / Ethnic Studies ────────────────────────────────────────
    ({"MIG", "MIGR"}, {"SOC", "HIST", "ANTH"}),

    # ── Disability Studies ────────────────────────────────────────────────
    ({"DST", "DIS"}, {"HSSP", "SOC"}),

    # ── Black / African American Studies variants (supersedes the narrower
    #    entry above -- broadens Brandeis target to include AMST) ──────────
    ({"AFRSTY", "AFROAM", "BLST", "BLS", "AAC", "AFS", "AAS"},
     {"AAAS"}),

    # ── Portuguese ─────────────────────────────────────────────────────────
    ({"POR", "PORT", "PORTUG"}, {"PORT"}),

    # ── Arabic ─────────────────────────────────────────────────────────────
    ({"ARB", "ARAB", "ARBC"}, {"ARBC"}),

    # ── Hebrew / Judaic ────────────────────────────────────────────────────
    ({"HEB", "HEBR", "JUD", "JUDST"}, {"HBRW", "NEJS"}),

    # ── Data Science ───────────────────────────────────────────────────────
    ({"DAT", "DATA", "DS"}, {"COSI", "MATH"}),

    # ── Statistics ─────────────────────────────────────────────────────────
    ({"STA", "STAT", "STATS"}, {"MATH", "QR"}),

    # ── Astronomy ──────────────────────────────────────────────────────────
    ({"AST", "ASTR", "ASTRO"}, {"PHYS"}),

    # ── Geography / Earth Science ─────────────────────────────────────────
    ({"GEO", "GEOG", "ESC", "ESM"}, {"ENVS"}),

    # ── Social Work ────────────────────────────────────────────────────────
    ({"SWK", "SOWK"}, {"SOC"}),

    # ── Visual Media / Digital Arts (supersedes the narrower ART entry
    #    above for these codes -- broadens target to include COSI) ─────────
    ({"VMA", "DGMD", "IMD", "GRFX"}, {"FA", "COSI"}),

    # ── American Studies (supersedes the narrower entry above -- broadens
    #    target to include HIST, AAAS; adds AMSTUD variant) ─────────────────
    ({"AMST", "ASAMST", "AMSTUD"}, {"AMST", "HIST", "AAAS"}),
]

CC_DEPT_TO_BRANDEIS: dict[str, set[str]] = {}
for _cc_codes, _brandeis_codes in DEPARTMENT_CATEGORIES:
    for _code in _cc_codes:
        CC_DEPT_TO_BRANDEIS[_code] = _brandeis_codes


def department_boost_mask(cc_departments, brd_departments) -> tuple[np.ndarray, int]:
    """
    Build a (len(cc), len(brd)) boolean matrix flagging which Brandeis
    courses fall in a CC course's mapped department category. This is used
    only to bias which Brandeis courses get selected as top-K matches —
    the *stored* similarity score always stays the true cosine similarity,
    so match_type thresholds and the reported score stay meaningful even
    when a department-relevant match doesn't exist.
    """
    brd_departments = np.asarray(brd_departments)
    boost_mask = np.zeros((len(cc_departments), len(brd_departments)), dtype=bool)
    filtered_rows = 0
    for i, cc_dept in enumerate(cc_departments):
        if not cc_dept or pd.isna(cc_dept):
            continue
        allowed = CC_DEPT_TO_BRANDEIS.get(str(cc_dept).upper())
        if not allowed:
            continue
        row_mask = np.isin(brd_departments, list(allowed))
        if row_mask.any() and not row_mask.all():
            boost_mask[i] = row_mask
            filtered_rows += 1
    return boost_mask, filtered_rows


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)

    # ── 1. Load data ──────────────────────────────────────────────────────────
    print("Loading courses from database ...")
    # cc_transferable (built by 00_filter_untransferable.py) excludes courses
    # that will never have a Brandeis equivalent (remedial, vocational,
    # clinical, non-credit, etc.). Falls back to the unfiltered table if that
    # step hasn't been run yet.
    has_filter_view = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='cc_transferable'"
    ).fetchone()
    cc_source = "cc_transferable" if has_filter_view else "community_courses_clean"
    if not has_filter_view:
        print("  Note: cc_transferable view not found -- run 00_filter_untransferable.py first "
              "to exclude non-transferable courses. Using community_courses_clean for now.")

    cc = pd.read_sql(f"""
        SELECT id, college_name, college_key, course_code, course_title, description, department
        FROM {cc_source}
    """, conn)

    brd = pd.read_sql("""
        SELECT id, course_code, course_title, description, department
        FROM brandeis_courses
        WHERE course_code     NOT LIKE '%L'
        AND course_title  NOT LIKE '%Laboratory%'
        AND course_title  NOT LIKE '% Lab %'
        AND course_title  NOT LIKE '% Lab I'
        AND course_title  NOT LIKE '% Lab II'
        AND course_title  NOT LIKE '%Project Lab%'
        AND course_title  NOT LIKE '%Lab in %'
        AND course_title  NOT LIKE '%Graduate%'
        AND course_title  NOT LIKE '%Internship%'
        AND course_title  NOT LIKE '%Practicum%'
        AND course_title  NOT LIKE '%T.A.%'
        AND course_title  NOT LIKE '%Independent Study%'
    """, conn)

    # Brandeis numbering convention: 1-99 = intro undergrad, 100-199 = advanced
    # undergrad, 200+ = graduate (MA/MBA/PhD -- Heller School, GSAS, MBA
    # electives, etc.). CC courses are all undergrad, so exclude 200+ from the
    # target pool -- they can never be a legitimate transfer equivalent.
    brd_course_num = brd["course_code"].str.extract(r"(\d+)")[0].astype(float)
    n_before = len(brd)
    brd = brd[brd_course_num < 200].reset_index(drop=True)
    print(f"  Excluded {n_before - len(brd)} graduate-level (200+) Brandeis courses from the target pool")

    print(f"  Community college courses : {len(cc)}")
    print(f"  Brandeis courses          : {len(brd)}")

    # ── 2. Build text fields for embedding ────────────────────────────────────
    cc["_text"]  = cc.apply(build_text,  axis=1, is_cc=True)
    brd["_text"] = brd.apply(build_text, axis=1, is_cc=False)

    # ── 3. Generate embeddings ────────────────────────────────────────────────
    print(f"\nLoading embedding model '{MODEL}' ...")
    print("(First run downloads ~840 MB - cached after that)\n")
    model = SentenceTransformer(MODEL)

    print("Embedding community college courses ...")
    t0 = time.time()
    cc_embeddings = model.encode(
        cc["_text"].tolist(),
        prompt=INSTRUCTION,
        batch_size=32,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    print(f"  Done in {time.time()-t0:.1f}s")

    print("Embedding Brandeis courses ...")
    t0 = time.time()
    brd_embeddings = model.encode(
        brd["_text"].tolist(),
        prompt=INSTRUCTION,
        batch_size=32,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    print(f"  Done in {time.time()-t0:.1f}s")

    brd_reset = brd.reset_index(drop=True)
    cc_reset  = cc.reset_index(drop=True)

    # ── 4. Compute similarity & find top-K matches ────────────────────────────
    print(f"\nComputing similarity matrix ({len(cc)} x {len(brd)}) ...")
    t0 = time.time()
    sim_matrix = cosine_similarity_matrix(cc_embeddings, brd_embeddings)
    print(f"  Done in {time.time()-t0:.1f}s")

    print("Applying department pre-filter ...")
    boost_mask, filtered_rows = department_boost_mask(
        cc_reset["department"].tolist(), brd_reset["department"].tolist()
    )
    print(f"  Narrowed {filtered_rows}/{len(cc_reset)} courses ({filtered_rows/len(cc_reset)*100:.1f}%) to a relevant Brandeis department")
    print(f"  {len(cc_reset) - filtered_rows} courses had no department mapping and were compared against the full catalog")

    print(f"Finding top-{TOP_K} matches per course ...")
    # Ranking-only matrix: in-department candidates are boosted above the max
    # possible cosine similarity (1.0) so they're always selected first when
    # they exist, without altering the true similarity scores we store below.
    ranking_matrix = sim_matrix + boost_mask.astype(np.float32)
    top_k_indices = np.argsort(ranking_matrix, axis=1)[:, -TOP_K:][:, ::-1]

    # ── 5. Build results dataframe ────────────────────────────────────────────
    rows = []

    for cc_idx in range(len(cc_reset)):
        cc_row = cc_reset.iloc[cc_idx]
        for rank, brd_idx in enumerate(top_k_indices[cc_idx], start=1):
            brd_row = brd_reset.iloc[brd_idx]
            score   = float(sim_matrix[cc_idx, brd_idx])
            rows.append({
                "community_course_id" : int(cc_row["id"]),
                "brandeis_course_id"  : int(brd_row["id"]),
                "rank"                : rank,
                "similarity_score"    : round(score, 4),
                "match_type"          : score_label(score),
                "cc_college"          : cc_row["college_name"],
                "cc_code"             : cc_row["course_code"],
                "cc_title"            : cc_row["course_title"],
                "brd_code"            : brd_row["course_code"],
                "brd_title"           : brd_row["course_title"],
                "brd_department"      : brd_row["department"],
            })

    equiv_df = pd.DataFrame(rows)

    # ── 6. Save to database ───────────────────────────────────────────────────
    print("\nSaving equivalencies to database ...")
    conn.execute("DROP TABLE IF EXISTS equivalencies_v3")
    conn.execute("""
        CREATE TABLE equivalencies_v3 (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            community_course_id  INTEGER,
            brandeis_course_id   INTEGER,
            rank                 INTEGER,
            similarity_score     REAL,
            match_type           TEXT,
            cc_college           TEXT,
            cc_code              TEXT,
            cc_title             TEXT,
            brd_code             TEXT,
            brd_title            TEXT,
            brd_department       TEXT,
            FOREIGN KEY (community_course_id) REFERENCES community_courses_clean(id),
            FOREIGN KEY (brandeis_course_id)  REFERENCES brandeis_courses(id)
        )
    """)
    equiv_df.to_sql("equivalencies_v3", conn, if_exists="append", index=False)
    conn.commit()

    # ── 7. Export CSV ─────────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    equiv_df.to_csv(OUT_CSV, index=False)

    # ── 8. Summary report ─────────────────────────────────────────────────────
    rank1 = equiv_df[equiv_df["rank"] == 1]

    print(f"\n{'='*55}")
    print(f"  Total equivalency pairs stored : {len(equiv_df)}")
    print(f"  CC courses matched             : {len(rank1)}")
    print(f"\n  Match quality breakdown (rank-1 only):")
    breakdown = rank1["match_type"].value_counts()
    for label in ["exact", "strong", "partial", NO_MATCH_LABEL]:
        count = breakdown.get(label, 0)
        pct   = count / len(rank1) * 100
        bar   = "#" * int(pct / 2)
        print(f"    {label:<38} {count:>5}  ({pct:4.1f}%)  {bar}")

    print(f"\n  Top 10 strongest matches:")
    top10 = rank1.nlargest(10, "similarity_score")[
        ["cc_college", "cc_code", "cc_title", "brd_code", "brd_title", "similarity_score"]
    ]
    for _, r in top10.iterrows():
        print(f"    [{r['similarity_score']:.3f}] {r['cc_code']:<12} '{r['cc_title'][:35]}'")
        print(f"           -> {r['brd_code']:<12} '{r['brd_title'][:35]}'")

    print(f"\n  CSV exported -> {OUT_CSV}")
    print(f"  Phase 2 complete. Check the results and evaluate the model in evaluation.")

    conn.close()


if __name__ == "__main__":
    main()
