"""
Apply UNC-sourced enrichment (accurate descriptions, focus areas, disciplines,
fixed source URLs) to frontend/partnerships.json, and prune scrape-artifact
"units" flagged for removal.

Enrichment lives in /tmp/enr/b*.json — each record was compiled by fetching the
unit's official UNC page (see scripts/seed_unc_units.py for provenance). Every
applied unit carries verified=true; unverified/empty records are skipped so we
never overwrite real data with blanks.

Idempotent: re-running applies the same verified values and removes the same
artifacts. Removal is SAFE — a flagged unit is only dropped if it has no
partnerships (otherwise it is kept and reported, so no real data is lost).

Usage:
    python scripts/apply_unit_enrichment.py [--dry-run]
"""
from __future__ import annotations
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PARTNERSHIPS = ROOT / "frontend" / "partnerships.json"
ENRICH_GLOB = "/tmp/enr/b*.json"


def main() -> None:
    dry = "--dry-run" in sys.argv
    data = json.loads(PARTNERSHIPS.read_text())
    units = data.get("units", [])
    parts = data.get("partnerships", [])
    by_id = {u["unit_id"]: u for u in units}

    records = []
    for f in sorted(glob.glob(ENRICH_GLOB)):
        records.extend(json.loads(Path(f).read_text()))
    print(f"Loaded {len(records)} enrichment records from {ENRICH_GLOB}")

    # partnership counts per unit (to protect units that carry real data)
    pcount: dict = {}
    for p in parts:
        pcount[p.get("unit_id")] = pcount.get(p.get("unit_id"), 0) + 1

    updated = 0
    remove_ids = set()
    kept_despite_flag = []

    for r in records:
        uid = r.get("unit_id")
        u = by_id.get(uid)
        if not u:
            continue
        if r.get("remove"):
            if pcount.get(uid, 0) > 0:
                kept_despite_flag.append((uid, u["unit_name"], pcount[uid]))
            else:
                remove_ids.add(uid)
            continue
        if not r.get("verified"):
            continue
        desc = (r.get("description") or "").strip()
        if not desc:
            continue
        u["description"] = desc
        if (r.get("focus_areas") or "").strip():
            u["focus_areas"] = r["focus_areas"].strip()
        if (r.get("disciplines") or "").strip():
            u["disciplines"] = r["disciplines"].strip()
        if (r.get("website_url") or "").strip():
            u["website_url"] = r["website_url"].strip()
        u["research_by"] = "UNC official website"
        updated += 1

    # prune flagged artifacts (none have partnerships, by construction above)
    if remove_ids:
        data["units"] = [u for u in units if u["unit_id"] not in remove_ids]
        before_p = len(parts)
        data["partnerships"] = [p for p in parts if p.get("unit_id") not in remove_ids]
        dropped_p = before_p - len(data["partnerships"])
    else:
        dropped_p = 0

    meta = data.setdefault("meta", {})
    meta["n_units"] = len(data["units"])
    meta["n_partnerships"] = len(data["partnerships"])

    print(f"Descriptions/focus updated: {updated}")
    print(f"Artifacts removed: {len(remove_ids)} -> {sorted(remove_ids)}")
    if dropped_p:
        print(f"  (also dropped {dropped_p} partnerships tied to removed artifacts)")
    if kept_despite_flag:
        print("Flagged-but-kept (had partnerships, not removed):")
        for uid, name, n in kept_despite_flag:
            print(f"  - {uid} ({name}) — {n} partnerships")
    print(f"Total units after: {len(data['units'])}")

    if dry:
        print("\n(dry run — no file written)")
        return
    PARTNERSHIPS.write_text(json.dumps(data, ensure_ascii=False))
    print(f"\nWrote {PARTNERSHIPS}")


if __name__ == "__main__":
    main()
