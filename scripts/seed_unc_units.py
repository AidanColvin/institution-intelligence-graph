"""
Seed/expand the UNC unit master list in frontend/partnerships.json.

Adds every UNC–Chapel Hill School, Center, Institute and Department that isn't
already present, sourced from UNC's own public websites (names + URLs below were
compiled from unc.edu / college.unc.edu / med.unc.edu / sph.unc.edu /
research.unc.edu). 100% free, public, keyless — no APIs called at runtime.

Idempotent: re-running adds nothing new. Existing units (and their partnership
data) are never overwritten. Unknown numeric fields are left null — no invented
headcounts.

Usage:
    python scripts/seed_unc_units.py            # merge into partnerships.json
    python scripts/seed_unc_units.py --dry-run  # report what would be added
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PARTNERSHIPS = ROOT / "frontend" / "partnerships.json"
TODAY = "2026-06-17"
RESEARCH_BY = "UNC public catalog"

# ── source data (compiled from UNC public pages) ──────────────────────────────

SCHOOLS = [
    ("College of Arts and Sciences", "https://college.unc.edu/"),
    ("School of Medicine", "https://www.med.unc.edu/"),
    ("Gillings School of Global Public Health", "https://sph.unc.edu/"),
    ("Eshelman School of Pharmacy", "https://pharmacy.unc.edu/"),
    ("School of Nursing", "https://nursing.unc.edu/"),
    ("Adams School of Dentistry", "https://www.dentistry.unc.edu/"),
    ("School of Law", "https://law.unc.edu/"),
    ("Kenan-Flagler Business School", "https://www.kenan-flagler.unc.edu/"),
    ("School of Education", "https://ed.unc.edu/"),
    ("School of Government", "https://www.sog.unc.edu/"),
    ("School of Information and Library Science", "https://sils.unc.edu/"),
    ("School of Social Work", "https://ssw.unc.edu/"),
    ("Hussman School of Journalism and Media", "https://hussman.unc.edu/"),
    ("School of Data Science and Society", "https://datascience.unc.edu/"),
    ("School of Civic Life and Leadership", "https://civiclife.unc.edu/"),
    ("The Graduate School", "https://gradschool.unc.edu/"),
]

ARTS_SCIENCES_DEPTS = [
    ("American Studies", "https://americanstudies.unc.edu/"),
    ("Art and Art History", "https://art.unc.edu/"),
    ("Classics", "https://classics.unc.edu/"),
    ("Communication", "https://comm.unc.edu/"),
    ("Dramatic Art", "https://drama.unc.edu/"),
    ("English and Comparative Literature", "https://englishcomplit.unc.edu/"),
    ("Germanic and Slavic Languages and Literatures", "https://gsll.unc.edu/"),
    ("Linguistics", "https://linguistics.unc.edu/"),
    ("Music", "https://music.unc.edu/"),
    ("Philosophy", "https://philosophy.unc.edu/"),
    ("Religious Studies", "https://religion.unc.edu/"),
    ("Romance Studies", "https://romancestudies.unc.edu/"),
    ("Women's and Gender Studies", "https://womensandgenderstudies.unc.edu/"),
    ("Applied Physical Sciences", "https://aps.unc.edu/"),
    ("Biology", "https://bio.unc.edu/"),
    ("Chemistry", "https://chem.unc.edu/"),
    ("Computer Science", "https://cs.unc.edu/"),
    ("Earth, Marine and Environmental Sciences", "https://emes.unc.edu/"),
    ("Environment, Ecology and Energy Program", "https://e3p.unc.edu/"),
    ("Exercise and Sport Science", "https://exss.unc.edu/"),
    ("Mathematics", "https://math.unc.edu/"),
    ("Physics and Astronomy", "https://physics.unc.edu/"),
    ("Psychology and Neuroscience", "https://psychology.unc.edu/"),
    ("Statistics and Operations Research", "https://stat-or.unc.edu/"),
    ("African, African American and Diaspora Studies", "https://aaad.unc.edu/"),
    ("Anthropology", "https://anthropology.unc.edu/"),
    ("Asian and Middle Eastern Studies", "https://asianstudies.unc.edu/"),
    ("City and Regional Planning", "https://planning.unc.edu/"),
    ("Economics", "https://econ.unc.edu/"),
    ("Geography and Environment", "https://geography.unc.edu/"),
    ("Global Studies", "https://globalstudies.unc.edu/"),
    ("History", "https://history.unc.edu/"),
    ("Peace, War and Defense", "https://pwad.unc.edu/"),
    ("Political Science", "https://politicalscience.unc.edu/"),
    ("Public Policy", "https://publicpolicy.unc.edu/"),
    ("Sociology", "https://sociology.unc.edu/"),
]

# School of Medicine departments (basic science + clinical)
SOM_DEPTS = [
    ("Anesthesiology", "https://www.med.unc.edu/anesthesiology"),
    ("Biochemistry and Biophysics", "https://www.med.unc.edu/biochem/"),
    ("Biomedical Engineering", "https://www.bme.unc.edu/"),
    ("Cell Biology and Physiology", "https://www.med.unc.edu/cellbiophysio"),
    ("Dermatology", "https://www.med.unc.edu/derm/"),
    ("Emergency Medicine", "https://www.med.unc.edu/emergmed"),
    ("Family Medicine", "https://www.med.unc.edu/fammed"),
    ("Genetics", "https://www.med.unc.edu/genetics/"),
    ("Health Sciences", "https://www.med.unc.edu/healthsciences/"),
    ("Medicine", "https://www.med.unc.edu/medicine/"),
    ("Microbiology and Immunology", "https://www.med.unc.edu/microimm/"),
    ("Neurology", "https://www.med.unc.edu/neurology"),
    ("Neurosurgery", "https://www.med.unc.edu/neurosurgery"),
    ("Obstetrics and Gynecology", "https://www.med.unc.edu/obgyn/"),
    ("Ophthalmology", "https://www.med.unc.edu/ophth/"),
    ("Orthopaedics", "https://www.med.unc.edu/ortho/"),
    ("Otolaryngology/Head and Neck Surgery", "https://www.med.unc.edu/ent"),
    ("Pathology and Laboratory Medicine", "https://www.med.unc.edu/pathology/"),
    ("Pediatrics", "https://www.med.unc.edu/pediatrics/"),
    ("Pharmacology", "https://www.med.unc.edu/pharm/"),
    ("Physical Medicine and Rehabilitation", "https://www.med.unc.edu/phyrehab/"),
    ("Psychiatry", "https://www.med.unc.edu/psych"),
    ("Radiation Oncology", "https://www.med.unc.edu/radonc/"),
    ("Radiology", "https://www.med.unc.edu/radiology"),
    ("Social Medicine", "https://www.med.unc.edu/socialmed"),
    ("Surgery", "https://www.med.unc.edu/surgery"),
    ("Urology", "https://www.med.unc.edu/urology/"),
]

# (department name, parent school name, url)
OTHER_DEPTS = [
    ("Biostatistics", "Gillings School of Global Public Health", "https://sph.unc.edu/bios/biostatistics/"),
    ("Environmental Sciences and Engineering", "Gillings School of Global Public Health", "https://sph.unc.edu/envr/"),
    ("Epidemiology", "Gillings School of Global Public Health", "https://sph.unc.edu/epid/"),
    ("Health Behavior", "Gillings School of Global Public Health", "https://sph.unc.edu/hb/"),
    ("Health Policy and Management", "Gillings School of Global Public Health", "https://sph.unc.edu/hpm/"),
    ("Maternal and Child Health", "Gillings School of Global Public Health", "https://sph.unc.edu/mch/"),
    ("Nutrition", "Gillings School of Global Public Health", "https://sph.unc.edu/nutr/"),
    ("Public Health Leadership and Practice", "Gillings School of Global Public Health", "https://sph.unc.edu/phlp/"),
    ("Chemical Biology and Medicinal Chemistry", "Eshelman School of Pharmacy", "https://pharmacy.unc.edu/divisions/"),
    ("Pharmacoengineering and Molecular Pharmaceutics", "Eshelman School of Pharmacy", "https://pharmacy.unc.edu/divisions/"),
    ("Pharmacotherapy and Experimental Therapeutics", "Eshelman School of Pharmacy", "https://pharmacy.unc.edu/divisions/"),
    ("Practice Advancement and Clinical Education", "Eshelman School of Pharmacy", "https://pharmacy.unc.edu/divisions/"),
    ("Pharmaceutical Outcomes and Policy", "Eshelman School of Pharmacy", "https://pharmacy.unc.edu/divisions/"),
]

# (name, parent name [school or 'UNC-Chapel Hill'], url)
CENTERS = [
    ("Lineberger Comprehensive Cancer Center", "School of Medicine", "https://unclineberger.org/"),
    ("Bowles Center for Alcohol Studies", "School of Medicine", "https://www.med.unc.edu/alcohol/"),
    ("Blood Research Center", "School of Medicine", "https://www.med.unc.edu/bloodresearchcenter/"),
    ("Carolina Institute for Developmental Disabilities", "School of Medicine", "https://www.cidd.unc.edu/"),
    ("Center for AIDS Research", "School of Medicine", "https://unccfar.org/"),
    ("Center for Bioethics", "School of Medicine", "https://bioethics.unc.edu/"),
    ("Center for Gastrointestinal Biology and Disease", "School of Medicine", "https://www.med.unc.edu/cgibd/"),
    ("Center for Molecular Medicine", "School of Medicine", "https://www.med.unc.edu/molecularmedicine/"),
    ("Center for Women's Health Research", "School of Medicine", "https://www.med.unc.edu/cwhr/"),
    ("Institute for Global Health and Infectious Diseases", "School of Medicine", "https://globalhealth.unc.edu/"),
    ("Marsico Lung Institute", "School of Medicine", "https://www.med.unc.edu/marsicolunginstitute/"),
    ("McAllister Heart Institute", "School of Medicine", "https://www.med.unc.edu/mhi"),
    ("UNC Neuroscience Center", "School of Medicine", "https://www.med.unc.edu/neuroscience"),
    ("NC Translational and Clinical Sciences Institute (TraCS)", "School of Medicine", "https://tracs.unc.edu/"),
    ("Thurston Arthritis Research Center", "School of Medicine", "https://www.med.unc.edu/tarc/"),
    ("Kidney Center", "School of Medicine", "https://unckidneycenter.org/"),
    ("Center for Maternal and Infant Health", "School of Medicine", "https://www.mombaby.org/"),
    ("Carolina Population Center", "UNC-Chapel Hill", "https://www.cpc.unc.edu/"),
    ("Cecil G. Sheps Center for Health Services Research", "UNC-Chapel Hill", "https://www.shepscenter.unc.edu/"),
    ("Frank Porter Graham Child Development Institute", "UNC-Chapel Hill", "https://fpg.unc.edu/"),
    ("Renaissance Computing Institute (RENCI)", "UNC-Chapel Hill", "https://renci.org/"),
    ("Howard W. Odum Institute for Research in Social Science", "UNC-Chapel Hill", "https://odum.unc.edu/"),
    ("UNC Institute for the Environment", "UNC-Chapel Hill", "https://ie.unc.edu/"),
    ("Institute for Convergent Science", "UNC-Chapel Hill", "https://convergent.unc.edu/"),
    ("Highway Safety Research Center", "UNC-Chapel Hill", "https://www.hsrc.unc.edu/"),
    ("Injury Prevention Research Center", "UNC-Chapel Hill", "https://iprc.unc.edu/"),
    ("Nutrition Research Institute", "UNC-Chapel Hill", "https://www.uncnri.org/"),
    ("Center for Galapagos Studies", "UNC-Chapel Hill", "https://galapagos.unc.edu/"),
    ("Carolina Center for Public Service", "UNC-Chapel Hill", "https://ccps.unc.edu/"),
    ("Center for Urban and Regional Studies", "UNC-Chapel Hill", "https://curs.unc.edu/"),
    ("American Indian Center", "UNC-Chapel Hill", "https://americanindiancenter.unc.edu/"),
    ("Asian American Center", "UNC-Chapel Hill", "https://aac.unc.edu/"),
    ("Institute of Marine Sciences", "College of Arts and Sciences", "https://ims.unc.edu/"),
    ("Institute for the Arts and Humanities", "College of Arts and Sciences", "https://iah.unc.edu/"),
    ("Institute for the Study of the Americas", "College of Arts and Sciences", "https://isa.unc.edu/"),
    ("Parr Center for Ethics", "College of Arts and Sciences", "https://parrcenter.unc.edu/"),
    ("Carolina Asia Center", "College of Arts and Sciences", "https://carolinaasiacenter.unc.edu/"),
    ("Center for European Studies", "College of Arts and Sciences", "https://europe.unc.edu/"),
    ("Carolina Center for Jewish Studies", "College of Arts and Sciences", "https://jewishstudies.unc.edu/"),
    ("Center for the Study of the American South", "College of Arts and Sciences", "https://south.unc.edu/"),
    ("Frank Hawkins Kenan Institute of Private Enterprise", "Kenan-Flagler Business School", "https://kenaninstitute.unc.edu/"),
    ("Institute for Private Capital", "Kenan-Flagler Business School", "https://uncipc.org/"),
    ("Environmental Finance Center", "School of Government", "https://efc.sog.unc.edu/"),
    ("North Carolina Institute for Public Health", "Gillings School of Global Public Health", "https://sph.unc.edu/nciph/"),
    ("Water Institute", "Gillings School of Global Public Health", "https://waterinstitute.unc.edu/"),
    ("Collaborative Studies Coordinating Center", "Gillings School of Global Public Health", "https://www.cscc.unc.edu/"),
    ("Global Social Development Innovations", "School of Social Work", "https://gsdi.unc.edu/"),
]

# ── helpers ───────────────────────────────────────────────────────────────────

_WS = re.compile(r"\s+")


def slug(name: str) -> str:
    s = name.lower()
    s = re.sub(r"\(.*?\)", " ", s)            # drop parentheticals for the id
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def norm(name: str) -> str:
    """Normalized name for dedup: lowercase, drop a leading 'unc'/'the', punct out."""
    s = name.lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9& ]", " ", s)
    s = _WS.sub(" ", s).strip()
    for pre in ("unc ", "the "):
        if s.startswith(pre):
            s = s[len(pre):]
    return s.strip()


def make_unit(unit_id, parent_id, name, unit_type, url):
    return {
        "unit_id": unit_id,
        "parent_unit_id": parent_id,
        "unit_name": name,
        "unit_type": unit_type,
        "description": f"{name}, University of North Carolina at Chapel Hill.",
        "focus_areas": "",
        "disciplines": "",
        "faculty_count": None,
        "student_count": None,
        "website_url": url,
        "research_by": RESEARCH_BY,
        "date_of_research": TODAY,
        "notes": "",
        "partnership_count": 0,
        "top_companies": [],
    }


def main() -> None:
    dry = "--dry-run" in sys.argv
    data = json.loads(PARTNERSHIPS.read_text())
    units = data.get("units", [])

    existing_ids = {u["unit_id"] for u in units}
    # global name index (for schools + centers) and per-parent index (for depts)
    school_by_norm = {norm(u["unit_name"]): u["unit_id"] for u in units if u.get("unit_type") == "School"}
    center_norms = {norm(u["unit_name"]) for u in units if u.get("unit_type") in ("Center", "Institute", "Lab")}
    dept_keys = {(u.get("parent_unit_id"), norm(u["unit_name"])) for u in units}

    added = []

    def add(unit):
        units.append(unit)
        existing_ids.add(unit["unit_id"])
        added.append(unit)

    # 1) Schools — resolve to an existing school id when the name already exists
    for name, url in SCHOOLS:
        n = norm(name)
        if n in school_by_norm:
            continue
        uid = "unc:" + slug(name.replace("School of ", "").replace("School", "")) if False else "unc:" + slug(name)
        if uid in existing_ids:
            continue
        u = make_unit(uid, "unc:root", name, "School", url)
        add(u)
        school_by_norm[n] = uid

    def school_id(name: str) -> str:
        return school_by_norm.get(norm(name), "unc:root")

    # 2) Arts & Sciences departments
    for name, url in ARTS_SCIENCES_DEPTS:
        parent = school_id("College of Arts and Sciences")
        if (parent, norm(name)) in dept_keys:
            continue
        uid = f"{parent}:{slug(name)}"
        if uid in existing_ids:
            continue
        add(make_unit(uid, parent, name, "Department", url))
        dept_keys.add((parent, norm(name)))

    # 3) School of Medicine departments
    for name, url in SOM_DEPTS:
        parent = school_id("School of Medicine")
        if (parent, norm(name)) in dept_keys:
            continue
        uid = f"{parent}:{slug(name)}"
        if uid in existing_ids:
            continue
        add(make_unit(uid, parent, name, "Department", url))
        dept_keys.add((parent, norm(name)))

    # 4) Other professional-school departments
    for name, parent_name, url in OTHER_DEPTS:
        parent = school_id(parent_name)
        if (parent, norm(name)) in dept_keys:
            continue
        uid = f"{parent}:{slug(name)}"
        if uid in existing_ids:
            continue
        add(make_unit(uid, parent, name, "Department", url))
        dept_keys.add((parent, norm(name)))

    # 5) Centers & institutes (dedup globally by normalized name)
    for name, parent_name, url in CENTERS:
        n = norm(name)
        if n in center_norms:
            continue
        parent = "unc:root" if parent_name == "UNC-Chapel Hill" else school_id(parent_name)
        uid = "unc:ctr:" + slug(name)
        if uid in existing_ids:
            continue
        utype = "Institute" if re.search(r"\binstitute\b", name, re.I) else "Center"
        add(make_unit(uid, parent, name, utype, url))
        center_norms.add(n)

    # report
    from collections import Counter
    by_type = Counter(u["unit_type"] for u in added)
    print(f"Existing units: {len(units) - len(added)}")
    print(f"Would add: {len(added)}  ({dict(by_type)})" if dry else f"Added: {len(added)}  ({dict(by_type)})")
    print(f"Total units after: {len(units)}")
    for u in added:
        print("  +", u["unit_id"], "—", u["unit_name"])

    if dry:
        print("\n(dry run — no file written)")
        return

    data["units"] = units
    meta = data.setdefault("meta", {})
    meta["n_units"] = len(units)
    PARTNERSHIPS.write_text(json.dumps(data, ensure_ascii=False))
    print(f"\nWrote {PARTNERSHIPS} (n_units={len(units)})")


if __name__ == "__main__":
    main()
