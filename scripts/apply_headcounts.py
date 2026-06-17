"""
Apply published faculty/student counts (and any remaining description fills) to
frontend/partnerships.json, from UNC-sourced research in /tmp/hc/b*.json.

Only fills cells that have a real, sourced value — null stays null where no
official figure was found (no invented numbers). Each filled headcount records
its provenance in a `headcount_source` field and, if the unit's notes are empty,
a short sourced note.

Idempotent. Usage: python scripts/apply_headcounts.py [--dry-run]
"""
from __future__ import annotations
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PARTNERSHIPS = ROOT / "frontend" / "partnerships.json"
HC_GLOB = "/tmp/hc/b*.json"


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    dry = "--dry-run" in sys.argv
    data = json.loads(PARTNERSHIPS.read_text())
    by_id = {u["unit_id"]: u for u in data.get("units", [])}

    records = []
    for f in sorted(glob.glob(HC_GLOB)):
        records.extend(json.loads(Path(f).read_text()))
    print(f"Loaded {len(records)} headcount records")

    fac_set = stu_set = desc_set = url_fix = 0
    for r in records:
        u = by_id.get(r.get("unit_id"))
        if not u:
            continue
        fc = _int(r.get("faculty_count"))
        sc = _int(r.get("student_count"))
        if fc is not None:
            u["faculty_count"] = fc; fac_set += 1
        if sc is not None:
            u["student_count"] = sc; stu_set += 1
        if (fc is not None or sc is not None):
            src = (r.get("headcount_source") or "").strip()
            note = (r.get("headcount_note") or "").strip()
            if src:
                u["headcount_source"] = src
            if note and not (u.get("notes") or "").strip():
                u["notes"] = f"Headcount: {note}" + (f" ({src})" if src else "")
        # description fill (only where the agent returned one — flagged units)
        if (r.get("description") or "").strip():
            u["description"] = r["description"].strip(); desc_set += 1
            if (r.get("focus_areas") or "").strip():
                u["focus_areas"] = r["focus_areas"].strip()
            if (r.get("disciplines") or "").strip():
                u["disciplines"] = r["disciplines"].strip()
            u["research_by"] = "UNC official website"
        # url fix
        wu = (r.get("website_url") or "").strip()
        if wu.startswith("http") and wu != (u.get("website_url") or ""):
            u["website_url"] = wu; url_fix += 1

    print(f"faculty_count filled: {fac_set}")
    print(f"student_count filled: {stu_set}")
    print(f"descriptions filled:  {desc_set}")
    print(f"website_url updated:   {url_fix}")

    if dry:
        print("\n(dry run — no file written)")
        return
    PARTNERSHIPS.write_text(json.dumps(data, ensure_ascii=False))
    print(f"\nWrote {PARTNERSHIPS}")


if __name__ == "__main__":
    main()
