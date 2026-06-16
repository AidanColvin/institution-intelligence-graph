# UNC Research Footprint Graph

A **precomputed graph of UNC–Chapel Hill's public research footprint** that maps
companies and sectors to the specific UNC units and people they connect to — via
concrete public records (grants, papers, clinical trials, federal awards).

Built for **research-intelligence mapping and report enrichment** — not outreach.

- **100% free, public, keyless data.** No API keys, no paid services, no LLM in the request path.
- **Anchor:** UNC–Chapel Hill, ROR [`0130frc33`](https://ror.org/0130frc33).
- Every edge carries a `source_url` + `fetched_at`. Matching is on structured fields, never free text.
- Two confidence tiers: `confirmed` (a structured identifier matched) vs `probable` (normalized-name match).

## Architecture (ELT)

```
Extract (public APIs) → Load (DuckDB raw tables) → Transform (nodes/edges + topic profiles) → Serve (static JSON or FastAPI)
```

Expensive harvesting/normalizing happens at **build time**; every user query is a
millisecond lookup against the precomputed graph.

| Layer | Path |
|-------|------|
| Raw store + schema | `backend/graph/` (DuckDB) |
| Extractors (one per source) | `backend/extractors/` |
| Entity resolution + graph builder | `backend/transform/` |
| Topic profiler | `backend/topic/` |
| Two-mode matcher (evidence + topical) | `backend/matcher/` |
| FastAPI service | `backend/serve/` |
| Static browser frontend | `frontend/` |

### Data sources (all keyless, public)
NIH RePORTER · NSF Awards · USAspending · ClinicalTrials.gov · Crossref · SEC EDGAR (company_tickers) · ROR (anchor `0130frc33`) · ORCID · OpenAlex snapshot (optional).

**Patents** are a defined edge type in the schema but are intentionally **not populated**:
the only patent APIs (USPTO PatentsView Search, EPO OPS) now require an API key, which
violates the hard "no API keys" constraint. The edge type is reserved for a future
keyless source (e.g. a bulk snapshot).

## Build the graph

```bash
pip install -r requirements.txt

# Full build (phases 1–7). Use --limit for a fast test build.
python scripts/build_graph.py --phases 1,2,3,4,5,6,7 --limit 2500

# Phase reference:
#  0 faculty roster (ORCID)   1 NIH grants   2 NSF awards   3 USAspending
#  4 ClinicalTrials           5 Crossref     6 company CIK resolution
#  7 topic profiles           8 OpenAlex authors snapshot (optional)
```

## Test

```bash
python scripts/phase1_test.py --limit 200   # NIH end-to-end smoke test
```

## Serve

**Static (what's deployed):** export the graph to JSON and open the frontend.

```bash
python scripts/export_graph.py --db graph.db --out frontend/graph.json --built-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python -m http.server 8077 --directory frontend   # open http://localhost:8077
```

**API (programmatic):**

```bash
python scripts/serve.py --port 8001
# GET /match/{company}?sector_hint=...   GET /unit/{id}/edges   GET /unit/{id}/profile   GET /stats
```

## The two-mode matcher

- **Evidence mode** — weighted count of typed edges between a company and a UNC unit
  (`trial=4, grant=3, contract=2, paper=2, patent=2`).
- **Topical mode** — cosine-style Jaccard between query tokens and a unit's TF-IDF
  keyword profile (built from grant + paper titles/abstracts).

`final_score = 0.7 · normalize(evidence) + 0.3 · topical`

## Live site

The `frontend/` directory is deployed as a static site to GitHub Pages — it loads
`graph.json` and runs both matcher modes entirely in the browser. No backend required.
