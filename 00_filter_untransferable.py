"""
PRE-PROCESSING: Filter untransferable CC courses before matching.

Removes courses that will never have a Brandeis equivalent:
  - Remedial/developmental (course numbers below 100)
  - ESL / English Language Learner courses
  - Vocational/workforce programs (automotive, culinary, HVAC, cosmetology...)
  - Allied health / clinical programs (nursing, dental, radiology, EMT...)
  - Graduate-level CC courses (MBA, CECS, BMEN...)
  - Non-credit / orientation / college success courses
  - Lab-only sections (course codes ending in L)

Run BEFORE 04_match_equivalencies.py:
    python 00_filter_untransferable.py
    python 04_match_equivalencies.py

OUTPUT:
    Adds a `transferable` column (1/0) to community_courses_clean
    Creates a filtered view `cc_transferable` used by the matcher
    Exports data/processed/filtered_out.csv for review
"""

import os
import sqlite3
import pandas as pd

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "db", "courses.db")
FILTER_CSV  = os.path.join(BASE_DIR, "data", "processed", "filtered_out.csv")

# ── DEPARTMENT-LEVEL FILTERS ──────────────────────────────────────────────────
# Entire departments with zero Brandeis equivalents

VOCATIONAL_DEPTS = {
    # Culinary / Hospitality
    "CUL","CULA","FNS","FSC","FSD","FSM","FSR","FST","FOU","HOS","HOSP","HM",
    # Automotive
    "AUT","AUT",
    # HVAC / Building trades
    "HVAC","HVC","BLDG","PLB","EUT","ELT","ELE","MNT",
    # Cosmetology / Barbering
    "COS","COSM","BAR",
    # Welding / Manufacturing tech
    "WEL","MET","AMT","MNE","CAD","CET","EET","EGT",
    # Dental / Dental Hygiene
    "DEN","DENT","DHY","DHG",
    # Veterinary
    "VET","VTSC","AVS",
    # Funeral
    "FPS",
    # Horticulture / Agriculture
    "HRT","AGR","AGH","ANS",
    # Diagnostic / Radiologic / Surgical tech
    "RAD","RADT","SRG","DMS","MRT","SON","MLT","MLS",
    # Respiratory / Physical therapy support
    "RCP","RSP","PTA","OTA",
    # Fire science / Emergency medical
    "FIR","FIRE","EMS","EMT",
    # Office tech / Admin
    "OFC","OFT","OIM","OIT","BIT",
    # Fashion / Apparel
    "FIT","AXD",
    # Casino / Gaming
    "GAT",
    # Legal (paralegal — not same as legal studies/philosophy)
    "PAR","PLG",
    # Massage / Physical fitness tech
    "MAS","AT",
    # Pharmacy tech
    "PHA","PHRM",
    # Medical assisting / Clinical
    "MED","MDA","MEDA","HSC","HCC","MIG","MLS","MLT",
    # Electrical / Electronics tech
    "EET","ETE","ETEC","EE","CSO",
    # Aviation
    "AVS",
    # Interior design
    "IAD","GID",
    # Automotive / Diesel
    "DIES","AUT",
    # Opticianry
    "OPT","VISN",
}

REMEDIAL_DEPTS = {
    # ESL / ELL
    "ESL","ELL","ENSL","FFL","LXM",
    # Developmental reading
    "RDG","RDL","RDT",
    # Developmental writing
    "RWR","WRT",
}

HEALTHCARE_CLINICAL_DEPTS = {
    # Allied Health / Medical Interpreting (e.g. Bunker Hill CC "AHE")
    "AHE",
    # Nursing programs
    "NUR","NURS","NURSING","NURSNG","NSG","NU","PN","PNP","PNR","PNS",
    # Respiratory therapy
    "RSC","RSP","RT","RTA","RCP","RESP",
    # Radiology / Imaging
    "RAD","RADT","IMG","DMS","SON",
    # Surgical tech
    "SRG","SUR",
    # Physical / Occupational therapy assistant
    "PTA","OTA",
    # Medical lab
    "MLT","MLS","MLSC",
    # Phlebotomy
    "PHM",
    # Health information tech
    "HIT",
    # Dental
    "DEN","DENT","DHY","DHG",
    # EMT / Paramedic
    "EMT","EMS",
    # Community health worker
    "CHW",
}

GRADUATE_CC_DEPTS = {
    # MBA programs at UMass schools
    "MBA","MBAACM","MBAMGT","MBAMKT","MBAMS",
    # Graduate engineering
    "CECS","BMEN","CHEN","MECH","EECE","DPTH","PLAS","ENGY","IENG","ENGN",
    "CIVE","GLST","UMLO","UTCH","MPAD","GISD","GGHS",
    # Graduate business
    "SCH","STOCKSCH","SCSM","HPP","LLC",
    # Graduate education
    "EDLDRS","HIGHED","INSDSG","CONRES","CRCRTH","COUNSL",
    # Graduate health
    "VISN","PUBH","EHS","NUTR",
    # Graduate social work / counseling
    "CSP","GERON","APLING","ECHD","PSYCLN","PSYDBS","PSYDBS","SPHHS",
    # Graduate interdisciplinary
    "UPCD","CW","DACSS","LLC","LLARCH","LLAMS","LLMUS",
    # Graduate IT
    "MSIS","MSIT","MLSC","INFO","DPTH",
}

NON_CREDIT_ORIENTATION_DEPTS = {
    # College success / first year experience
    "FYE","FYS","UNV","COL","PRO","FRS","HON","HNR","HONR","HONORS",
    # Study skills / tutoring lab
    "SSI","SSC",
    # Military science
    "MIL",
    # Co-op / internship (standalone dept)
    "INT","INTR","CO",
    # Physical education
    "PED","HES","PE","ATH",
}

ALL_FILTERED_DEPTS = (
    VOCATIONAL_DEPTS |
    REMEDIAL_DEPTS |
    HEALTHCARE_CLINICAL_DEPTS |
    GRADUATE_CC_DEPTS |
    NON_CREDIT_ORIENTATION_DEPTS
)

# ── COURSE-LEVEL FILTERS ──────────────────────────────────────────────────────

def is_remedial_by_number(course_code: str) -> bool:
    """Courses numbered below 100 are typically developmental/remedial."""
    if not isinstance(course_code, str):
        return False
    import re
    m = re.search(r"(\d+)", course_code)
    if m:
        num = int(m.group(1))
        return num < 100
    return False


def is_lab_only(course_code: str) -> bool:
    """Lab sections like BIO 111L are usually co-requisites, not standalone."""
    if not isinstance(course_code, str):
        return False
    return bool(course_code.strip().upper().endswith("L"))


def is_transferable(row: pd.Series) -> int:
    dept = str(row.get("department") or "").upper().strip()
    code = str(row.get("course_code") or "").upper().strip()
    title = str(row.get("course_title") or "").lower()

    # Filter by department
    if dept in ALL_FILTERED_DEPTS:
        return 0

    # Filter remedial by course number
    if is_remedial_by_number(code):
        return 0

    # Filter lab-only sections
    if is_lab_only(code):
        return 0

    # Filter by title keywords (catch anything missed by dept)
    non_transfer_keywords = [
        "practicum","clinical","externship","internship seminar",
        "cooperative education","work experience","capstone project",
        "directed study","independent study","special topics",
        "orientation","college success","study skills","tutoring",
        "developmental","basic skills","pre-college",
    ]
    if any(kw in title for kw in non_transfer_keywords):
        return 0

    return 1


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM community_courses_clean", conn)
    print(f"Loaded {len(df)} courses")

    df["transferable"] = df.apply(is_transferable, axis=1)

    transferable   = df[df["transferable"] == 1]
    non_transferable = df[df["transferable"] == 0]

    print(f"\n  Transferable courses   : {len(transferable):>5} ({len(transferable)/len(df)*100:.1f}%)")
    print(f"  Non-transferable       : {len(non_transferable):>5} ({len(non_transferable)/len(df)*100:.1f}%)")

    # Show breakdown of what was filtered
    print(f"\n  Filtered by department (top 20):")
    filtered_depts = non_transferable.groupby("department").size().sort_values(ascending=False).head(20)
    for dept, count in filtered_depts.items():
        print(f"    {str(dept):<12} {count}")

    # Add column if it doesn't exist yet (must happen before any UPDATE touches it)
    try:
        conn.execute("ALTER TABLE community_courses_clean ADD COLUMN transferable INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Save transferable flag back to DB
    df[["id","transferable"]].to_sql("_transferable_flags", conn, if_exists="replace", index=False)
    conn.execute("UPDATE community_courses_clean SET transferable = 0")
    conn.execute("""
        UPDATE community_courses_clean
        SET transferable = 1
        WHERE id IN (SELECT id FROM _transferable_flags WHERE transferable = 1)
    """)
    conn.commit()

    # Create a clean view for the matcher
    conn.execute("DROP VIEW IF EXISTS cc_transferable")
    conn.execute("""
        CREATE VIEW cc_transferable AS
        SELECT * FROM community_courses_clean
        WHERE transferable = 1
    """)
    conn.commit()

    # Export filtered out for review
    os.makedirs(os.path.dirname(FILTER_CSV), exist_ok=True)
    non_transferable.to_csv(FILTER_CSV, index=False)

    print(f"\n  Filtered-out CSV -> {FILTER_CSV}")
    print(f"  View `cc_transferable` created in database")
    print(f"\n  Next: run 04_match_equivalencies.py -- it now auto-detects and uses cc_transferable")
    print(f"\n  Expected improvement: matcher now works on {len(transferable)} courses")
    print(f"  instead of {len(df)} — reducing noise by {(1 - len(transferable)/len(df))*100:.0f}%")

    conn.close()


if __name__ == "__main__":
    main()
