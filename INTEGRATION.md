# Integration with `map-elt-research-graph-3`

How the UNC partnership inventory (this repo, **IIG**) joins to the live
per-company engine in
[`map-elt-research-graph-3`](https://github.com/AidanColvin/map-elt-research-graph-3)
(**MEL**). IIG is read-only with respect to MEL — nothing here modifies that
repo; this document only records how to line the two up.

## Company identifier fields on each side

| Concern | IIG (`unc_partnerships`) | MEL (`aria_pi`) |
|---|---|---|
| Primary key passed around | `company_name` (display string, e.g. `Merck Sharp & Dohme LLC`, `Google`) | `company_name: str` — `CompanyProfilerStage.run(company_name)` is keyed entirely on the raw name |
| Normalized form | `nodes_companies.normalized_name` (lowercase, legal suffixes stripped) via `backend/partnerships/common.norm_company()` | none persisted — MEL resolves names live through `sec_edgar_client` each run |
| Strong identifier | `nodes_companies.sec_cik` (10-digit CIK; ~84 of 308 companies SEC-matched) | `cik` (most-referenced id, 202 hits) + `ticker`, resolved from SEC EDGAR `get_company_facts` |
| Legal name | not stored | `facts.legal_name` from SEC |

IIG company names come from three real sources, each with a slightly different
shape:

- **ClinicalTrials.gov lead sponsors** — full legal names (`Merck Sharp & Dohme LLC`, `GlaxoSmithKline`).
- **OpenAlex co-author institutions** — display names with a country suffix that
  IIG strips at ingest (`Google (United States)` → `Google`).
- **SEC `company_tickers.json`** — title-case filer names, the source of `sec_cik`.

## The mismatch

`company_name` is **not** directly joinable across the two systems because the
strings differ in form:

- Legal suffixes: IIG keeps `… LLC`/`… Inc.` on trial sponsors; MEL's SEC lookup
  generally resolves to a shorter common name.
- Parenthetical qualifiers: OpenAlex names carry `(United States)` etc. (IIG
  removes these; MEL never sees them).
- Subsidiary vs parent: `Merck Sharp & Dohme LLC` (IIG, from a trial) vs `Merck & Co.`
  (MEL, from SEC) describe the same partner under different legal entities.

## Proposed join key

Two-tier join, strongest first:

1. **CIK (authoritative).** When an IIG company has `nodes_companies.sec_cik` and
   MEL has resolved a `cik` for the same name, join on the zero-padded 10-digit
   CIK. This is exact and survives all the name-form differences above. Covers
   the SEC-matched subset (~84 IIG companies today).

2. **Normalized name (fallback).** Otherwise join on a shared normalization of
   `company_name`. Both sides should apply the **same** rule — IIG already
   implements it in `common.norm_company()`:

   ```python
   # lowercase, drop legal suffixes (inc/llc/corp/co/ltd/plc/lp/…),
   # drop punctuation except &, collapse whitespace
   norm = norm_company(company_name)
   # "Merck Sharp & Dohme LLC" -> "merck sharp & dohme"
   # "Google (United States)"  -> "google"   (suffix already stripped at ingest)
   ```

   Recommend MEL expose the same `norm_company` output (or import it) so the
   fallback key is computed identically on both sides.

### Suggested crosswalk column

Add a derived `join_key` to IIG's exported partnership rows:

```
join_key = sec_cik  if sec_cik else "name:" + norm_company(company_name)
```

MEL computes the same value from its resolved `cik` / `company_name`, and the
two inventories join 1:1 on `join_key`. CIK-keyed rows are exact; `name:`-keyed
rows are high-confidence but should be treated as `probable` until a CIK confirms
them — mirroring IIG's existing confirmed/probable confidence tiers.

## Open items

- IIG only has CIKs for the SEC-matched subset; widening CIK coverage (e.g.
  resolving trial-sponsor and OpenAlex names through SEC EDGAR, as MEL already
  does) would raise the share of exact joins.
- Subsidiary→parent rollups (`Merck Sharp & Dohme LLC` → `Merck & Co.`) need a
  small alias table; `backend/data/company_overrides.json` is the place to add it.
