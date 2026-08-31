"""
PRE-PROCESSING v2: Filter untransferable CC courses.
Built from full analysis of your actual equivalencies_v3.csv data.

Run BEFORE the matcher:
    python scripts/01_filter_untransferable_v2.py
    python scripts/04_match_equivalencies_v3.py
"""

import os
import re
import sqlite3
import pandas as pd

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "db", "courses.db")
FILTER_CSV  = os.path.join(BASE_DIR, "data", "processed", "filtered_out.csv")

# ── DEPARTMENTS TO FILTER ENTIRELY ───────────────────────────────────────────
# Every dept code found in your data that has no Brandeis equivalent

FILTER_DEPTS = {
    # ── Automotive / Transportation ──────────────────────────────────────────
    "AB","AI","AS","AY",            # Automotive fundamentals
    "AUTO","AUT",

    # ── Allied Health / Medical Assisting ────────────────────────────────────
    "AHE","AHP","ALH",              # Allied Health Education/Professional
    "HCA","HLS","HLT","HLTH",       # Health Administration/Studies
    "HSCI","HSRV","HSP","HSV",      # Health Sciences/Services
    "HTH","HEA","HL","HC",          # Health
    "MS","MSS","MO","MRC",          # Medical Assisting/Skills
    "MLA","MAC","MAA","MC",         # Medical Lab/Mammography
    "NHP","EM","EMR",               # Emergency/Paramedic
    "PM","PMT",                     # Paramedic Technology
    "DSC","DTC","CU","CVT",         # Dental/Cardio tech
    "CTC","TO","MN",                # CT/Imaging/Medical admin
    "SGT","SX","SG",                # Surgical tech
    "PSG","LEO","RCH",              # Polysomnography/Lab safety
    "PHP","IND","HLC",              # Pre-health/Patient care
    "REHAB","SAC","SOS",            # Rehab/Counseling tech
    "BST","MAA","HL",               # Medical terminology

    # ── Nursing ──────────────────────────────────────────────────────────────
    "NUR","NURS","NSG","PN","CU",

    # ── Dental ───────────────────────────────────────────────────────────────
    "DAS","DSC","DHY","DHG","DEN","DENT",

    # ── Veterinary ───────────────────────────────────────────────────────────
    "VEA","VTE","VTSC",

    # ── Culinary / Hospitality / Tourism ─────────────────────────────────────
    "CLN","HRM","HOS","HOSP","HM","TLT",
    "UTT", "CUL",                       # Utilities tech

    # ── Building / Construction / HVAC ───────────────────────────────────────
    "ABT","CON",                    # Building/Construction tech
    "EKG",                          # EKG technician
    "DLT","UTT",                    # Digital/Utilities tech

    # ── Cosmetology / Barbering ───────────────────────────────────────────────
    "COS","COSM","BAR",

    # ── Office Technology / Keyboarding ──────────────────────────────────────
    "BSS","CTIM","BIT","OA",        # Business/Office skills
    "CAP",                          # Computer applications (basic)

    # ── ESL / Developmental ──────────────────────────────────────────────────
    "ESL","ELL","ES",               # English as Second Language
    "RDG","RDL",                    # Developmental Reading

    # ── Physical Education / Athletics ───────────────────────────────────────
    "PED","HES","PE","ATH",
    "EXER","KIN","DANCE","DAN","DANC",  # Dance/Kinesiology PE courses
    "SPM","SPO",                    # Sport Management internship/basic

    # ── College Success / Orientation ────────────────────────────────────────
    "AAC","ACS","IDS","FYE","FYS",
    "UNV","COL","HON","HNR",
    "FOÚN","FOUN","CPT",
    "CAS",                          # Program fee / orientation
    "GSY",                          # Professional etiquette

    # ── Cooperative Ed / Internship-only depts ───────────────────────────────
    "COP","INT","INTR","EXP",
    "ITR","MUSR","MUSB","BUSI",
    "MUBÚ","MUBU",
    "BTC","BMT",                    # Biotech co-op
    "LEO","LGS","LGST",             # Legal internship/co-op
    "ENT",                          # Entrepreneurship internship
    "HUS",                          # Human Services (vocational/practicum-heavy program)

    # ── Radiology / Imaging ──────────────────────────────────────────────────
    "RAD","RADT","DMS","SON","MRT",
    "MLT","MLS","MLX",
    "MAC",                          # Mammography

    # ── EMT / Fire / Emergency ───────────────────────────────────────────────
    "EMT","EMS","FIR","FIRE",

    # ── Pharmacy / Phlebotomy ────────────────────────────────────────────────
    "PHA","PHRM","PHM","MLA",

    # ── Paralegal (workforce focused) ────────────────────────────────────────
    "PAR","PLG","PA","LEG",

    # ── Funeral / Gaming ─────────────────────────────────────────────────────
    "FPS","GAT",

    # ── Graduate-level CC programs ───────────────────────────────────────────
    "MBA","PHYSIC",                 # Graduate physics at UMass
    "PUBAMD","PUBADM","REGIONPL",

    # ── Misc workforce / no academic equivalent ──────────────────────────────
    "ADM","LOG","PRM",              # Manufacturing/logistics/project mgmt tech
    "GIS","PUBPOL",                 # GIS tech / public policy tech
    "UWW",                          # Wellness
    "NRC","ECR",                    # Arboriculture/Forestry
    "SACH",                         # Unknown single-course dept
    "SLR","SEM","SEMINR",           # Service learning/seminar
    "SUSTCOMM","SUS",               # Sustainability tech
    "VET","VETT","VETC","VETN",     # Veterinary tech/assistant
    "WELD","WLD",                   # Welding
    # ── Miscellaneous single-course depts (no Brandeis equivalent) ─────────────
    "ECE","ENL","AER","AEROSPACE","AERO",       # Aerospace
    "AG","AGRICULTURE","AGR",       # Agriculture
    "ECE", "VISN","CSP","RTA",      # Early Childhood Education
    "FSC","FIRESCI","FIRESCIENCE",      # Fire Science
    "OTA","RCP","PTA","PNS","PNP","RT","RSC","HRT","HRT","HPT","HPTA",  # Rehab/therapist/physical therapy
    "ANS","SRG","SURG","SURGERY",  # Animal surgery
    "HVC","HVAC","HVACR",             # Heating/ventilation/air conditioning
    "AVS","AMT","AV","AET",             # Aviation
    "HIT","LLMUS","PRO","ECHD","ECHO","ECH", "RSP","MBA", # Health Information/echocardiography
    # Graduate CC programs (wrong level)
    "MBAMKT", "MSIS", "CONRES", "PSYDBS", "MLSP", "SCSM",

    # ── ADD THESE based on dept quality analysis ─────────────────────────────
    # Radiology / Imaging
    "RDT",

    # Food / Nutrition workforce
    "FNS", "FSR", "FST",

    # Healthcare / Clinical
    "NURSING", "HSC", "SUR", "MEDA", "GERON",
    "HCC", "PNR", "RESP",

    # Military Science
    "MIL",

    # Office / Admin tech
    "OFC", "OIM",

    # Specific workforce with wrong mappings
    "DIES", "LXM", "EUT", "CULA", "SSN", "MNT",
    "PUBHTH", "CY", "AT", "FRS", "SOA", "LLC",
}


# ── TITLE KEYWORD FILTER (catch anything missed by dept) ─────────────────────
NON_TRANSFER_TITLE_KEYWORDS = [
    "practicum","clinical externship","cooperative education",
    "work experience","college experience","college success",
    "study skills","tutoring","developmental","basic skills",
    "pre-college","orientation","keyboarding","typing",
    "phlebotomy","ekg technician","computed tomography",
    "surgical technology","polysomnography","mammography",
    "funeral","cosmetology","barbering","automotive fundamentals",
    "welding","hvac","culinary","dental science",
    "veterinary assistant","veterinary technician",
    "emergency medical","paramedic","first responder",
    "medical assistant skills","patient care skills",
    "nurse aide","nursing assistant",
    "internship","co-op","cooperative ed",
    "thesis","dissertation","capstone",
    "independent study","directed study",
    "senior design","community health worker",

    # ── Standalone lab sections (separate from the lecture course) ──────────
    "organic chemistry laboratory",
    "chemistry laboratory i",
    "chemistry laboratory ii",
    "bio-organic chemistry laboratory",
    "physics laboratory",
    "biology laboratory",
]

# ── COURSE NUMBER FILTER ──────────────────────────────────────────────────────
def is_remedial_by_number(course_code: str) -> bool:
    """Courses numbered below 100 are typically developmental."""
    if not isinstance(course_code, str):
        return False
    m = re.search(r"(\d+)", course_code)
    if m:
        return int(m.group(1)) < 100
    return False


def is_lab_only(course_code: str) -> bool:
    """Lab-only sections like BIO 111L."""
    if not isinstance(course_code, str):
        return False
    return course_code.strip().upper().endswith("L")


def is_standalone_lab_title(title: str) -> bool:
    """
    Standalone lab sections that exist separately from the lecture course,
    e.g. "Organic Chemistry Laboratory I", "Bio-Organic Chemistry Laboratory I".
    Titles ending in "Laboratory <roman numeral>" are lab-only sections, not
    full lecture+lab courses.
    """
    if not isinstance(title, str):
        return False
    return bool(re.search(r"\blaboratory\s+(i|ii|iii|iv)$", title.strip(), re.IGNORECASE))


def is_transferable(row: pd.Series) -> tuple[int, str]:
    """Returns (1/0, reason_if_filtered)."""
    dept  = str(row.get("department") or "").upper().strip()
    code  = str(row.get("course_code") or "").upper().strip()
    title = str(row.get("course_title") or "").lower()

    if dept in FILTER_DEPTS:
        return 0, f"Department filtered: {dept}"

    if is_remedial_by_number(code):
        return 0, f"Remedial course number: {code}"

    if is_lab_only(code):
        return 0, f"Lab-only section: {code}"

    if is_standalone_lab_title(title):
        return 0, "Standalone lab section"

    for kw in NON_TRANSFER_TITLE_KEYWORDS:
        if kw in title:
            return 0, f"Title keyword: '{kw}'"

    return 1, ""


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)
    df   = pd.read_sql("SELECT * FROM community_courses_clean", conn)
    print(f"Loaded {len(df)} courses from community_courses_clean")

    results       = df.apply(is_transferable, axis=1)
    df["transferable"]   = results.apply(lambda x: x[0])
    df["filter_reason"]  = results.apply(lambda x: x[1])

    transferable     = df[df["transferable"] == 1]
    non_transferable = df[df["transferable"] == 0]

    print(f"\n  Transferable     : {len(transferable):>5}  ({len(transferable)/len(df)*100:.1f}%)")
    print(f"  Non-transferable : {len(non_transferable):>5}  ({len(non_transferable)/len(df)*100:.1f}%)")

    print(f"\n  Top filtered departments:")
    top_filtered = (
        non_transferable.groupby("department")
        .size().sort_values(ascending=False).head(25)
    )
    for dept, count in top_filtered.items():
        print(f"    {str(dept):<12} {count}")

    # Update transferable column in DB
    try:
        conn.execute(
            "ALTER TABLE community_courses_clean ADD COLUMN transferable INTEGER DEFAULT 1"
        )
    except Exception:
        pass  # column already exists

    try:
        conn.execute(
            "ALTER TABLE community_courses_clean ADD COLUMN filter_reason TEXT"
        )
    except Exception:
        pass

    conn.execute("UPDATE community_courses_clean SET transferable = 1, filter_reason = ''")
    for _, row in df[df["transferable"] == 0][["id","filter_reason"]].iterrows():
        conn.execute(
            "UPDATE community_courses_clean SET transferable = 0, filter_reason = ? WHERE id = ?",
            (row["filter_reason"], int(row["id"]))
        )
    conn.commit()

    # Create/replace view
    conn.execute("DROP VIEW IF EXISTS cc_transferable")
    conn.execute("""
        CREATE VIEW cc_transferable AS
        SELECT * FROM community_courses_clean
        WHERE transferable = 1
    """)
    conn.commit()

    # Export filtered for review
    os.makedirs("data/processed", exist_ok=True)
    non_transferable.to_csv(FILTER_CSV, index=False)

    print(f"\n  View `cc_transferable` created -> {len(transferable)} courses")
    print(f"  Filtered-out CSV -> {FILTER_CSV}")


    conn.close()



if __name__ == "__main__":
    main()
