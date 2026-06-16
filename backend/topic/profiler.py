"""
Topic profiler — build keyword profiles per UNC unit.
Reads grant titles/abstracts from raw_nih_grants and raw_nsf_awards in DuckDB.
Outputs TF-IDF-weighted top-50 keywords per unit to topic_profiles table.

No external libraries required — implements a simple TF-IDF over the DuckDB corpus.
"""
from __future__ import annotations
import json
import logging
import math
import re
import string
from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..graph import store

logger = logging.getLogger(__name__)

# Minimal English stopword list (no NLTK dependency)
_STOPWORDS = frozenset([
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "not", "this",
    "that", "these", "those", "we", "our", "us", "their", "they", "it",
    "its", "as", "into", "than", "more", "such", "using", "used", "use",
    "based", "including", "study", "studies", "research", "project",
    "grant", "award", "program", "university", "north", "carolina", "unc",
    "also", "both", "between", "within", "during", "through", "over",
    "under", "after", "before", "among", "about", "other", "each",
    "which", "who", "when", "how", "what", "all", "any", "two", "three",
    "new", "high", "low", "large", "small", "number", "one", "well",
    "results", "data", "methods", "aim", "aims", "specific", "trial",
    "clinical", "patients", "patient", "health", "disease", "human",
])

_PUNCT = re.compile(r"[^\w\s-]")
_DIGITS = re.compile(r"\b\d+\b")


def _tokenize(text: str) -> list[str]:
    text = _PUNCT.sub(" ", text.lower())
    text = _DIGITS.sub(" ", text)
    tokens = text.split()
    return [t for t in tokens if len(t) >= 3 and t not in _STOPWORDS]


def _fetch_texts_per_unit() -> dict[str, list[str]]:
    """
    Pull grant titles + abstracts from DuckDB, grouped by the UNC unit
    the edge points to.  Returns {unit_id: [text, ...]}.
    """
    sql = """
        SELECT
            e.target_node AS unit_id,
            json_extract_string(r.raw_json, '$.project_title') AS title,
            json_extract_string(r.raw_json, '$.abstract_text') AS abstract
        FROM edges e
        JOIN raw_nih_grants r ON e.record_id = r.id
        WHERE e.edge_type = 'grant' AND e.source_table = 'raw_nih_grants'
        UNION ALL
        SELECT
            e.target_node AS unit_id,
            json_extract_string(r.raw_json, '$.title') AS title,
            json_extract_string(r.raw_json, '$.abstractText') AS abstract
        FROM edges e
        JOIN raw_nsf_awards r ON e.record_id = r.id
        WHERE e.edge_type = 'grant' AND e.source_table = 'raw_nsf_awards'
        UNION ALL
        SELECT
            e.target_node AS unit_id,
            json_extract_string(r.raw_json, '$.title[0]') AS title,
            json_extract_string(r.raw_json, '$.abstract') AS abstract
        FROM edges e
        JOIN raw_crossref_papers r ON e.record_id = r.id
        WHERE e.edge_type = 'paper' AND e.source_table = 'raw_crossref_papers'
    """
    with store.connection(read_only=True) as conn:
        rows = conn.execute(sql).fetchall()

    unit_texts: dict[str, list[str]] = defaultdict(list)
    for unit_id, title, abstract in rows:
        combined = " ".join(filter(None, [title, abstract]))
        if combined.strip():
            unit_texts[unit_id].append(combined)
    return dict(unit_texts)


def build_profiles() -> None:
    """Compute and store topic profiles for all UNC units with data."""
    logger.info("Building topic profiles…")
    unit_texts = _fetch_texts_per_unit()

    if not unit_texts:
        logger.warning("No grant/paper texts found — run Phase 1 first.")
        return

    # TF per unit (term → count in unit corpus)
    unit_tf: dict[str, Counter] = {}
    for unit_id, texts in unit_texts.items():
        counts: Counter = Counter()
        for text in texts:
            counts.update(_tokenize(text))
        unit_tf[unit_id] = counts

    # IDF: how many units contain each term
    num_units = len(unit_tf)
    df: Counter = Counter()
    for counts in unit_tf.values():
        for term in counts:
            df[term] += 1

    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for unit_id, counts in unit_tf.items():
        n_docs = len(unit_texts[unit_id])
        tfidf: dict[str, float] = {}
        for term, tf_count in counts.items():
            tf = tf_count / max(n_docs, 1)
            idf = math.log(1 + num_units / (1 + df[term]))
            tfidf[term] = tf * idf

        top50 = [t for t, _ in sorted(tfidf.items(), key=lambda x: -x[1])[:50]]

        # MeSH concept codes from NIH grant terms — extract from raw if available
        concept_codes = _extract_mesh_codes(unit_id)

        store.upsert_topic_profile(unit_id, top50, concept_codes, updated_at)
        logger.info("Profile built for %s: %d terms", unit_id, len(top50))

    logger.info("Topic profiles complete for %d units.", len(unit_tf))


def _extract_mesh_codes(unit_id: str) -> list[str]:
    """Extract unique NIH grant term codes for a unit (best-effort)."""
    sql = """
        SELECT DISTINCT json_extract_string(r.raw_json, '$.phr_text') AS terms
        FROM edges e
        JOIN raw_nih_grants r ON e.record_id = r.id
        WHERE e.edge_type = 'grant'
          AND e.source_table = 'raw_nih_grants'
          AND e.target_node = ?
          AND json_extract_string(r.raw_json, '$.phr_text') IS NOT NULL
        LIMIT 500
    """
    try:
        with store.connection(read_only=True) as conn:
            rows = conn.execute(sql, [unit_id]).fetchall()
    except Exception:
        return []

    mesh_terms: set[str] = set()
    for (terms_str,) in rows:
        if terms_str:
            for t in terms_str.split(";"):
                t = t.strip()
                if t:
                    mesh_terms.add(t[:100])
    return sorted(mesh_terms)[:100]
