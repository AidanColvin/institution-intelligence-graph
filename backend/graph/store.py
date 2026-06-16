"""
DuckDB connection, table initialization, and upsert helpers.
All data lives in a single .db file specified by DB_PATH env var (default: graph.db).
"""
from __future__ import annotations
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

DB_PATH = os.environ.get("GRAPH_DB_PATH", str(Path(__file__).parent.parent.parent / "graph.db"))

_DDL = """
CREATE TABLE IF NOT EXISTS raw_nih_grants (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_nsf_awards (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_usaspending (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_clinical_trials (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_crossref_papers (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_ror_orgs (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    raw_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes_unc_units (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    short_name TEXT,
    ror_id TEXT,
    parent_id TEXT,
    aliases JSON NOT NULL DEFAULT '[]',
    topic_keywords JSON NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS nodes_faculty (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    orcid TEXT,
    unc_unit_id TEXT,
    department TEXT,
    confidence TEXT NOT NULL DEFAULT 'probable'
);

CREATE TABLE IF NOT EXISTS nodes_companies (
    id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    sec_cik TEXT,
    ror_id TEXT,
    confidence TEXT NOT NULL DEFAULT 'probable'
);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    edge_type TEXT NOT NULL,
    source_node TEXT NOT NULL,
    target_node TEXT NOT NULL,
    faculty_id TEXT,
    record_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    record_date TEXT,
    confidence TEXT NOT NULL,
    amount_usd BIGINT,
    edge_metadata JSON NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS topic_profiles (
    unit_id TEXT PRIMARY KEY,
    keywords JSON NOT NULL DEFAULT '[]',
    concept_codes JSON NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
"""


# A single process-wide connection is reused across all upserts. Opening a new
# DuckDB connection per row is correct but slow; a persistent handle makes large
# builds fast. The serve layer opens its own read-only handle in a separate process.
_CONN = None
_CONN_RO = None


def _rw():
    global _CONN
    if _CONN is None:
        _CONN = duckdb.connect(DB_PATH, read_only=False)
    return _CONN


@contextmanager
def connection(read_only: bool = False):
    """
    Yield a DuckDB connection.

    DuckDB forbids holding a read-write and a read-only handle to the same file
    in one process. So: if a read-write handle is already open (a build is in
    progress), reuse it for reads too. Only a pure-reader process (the serve
    layer, which never writes) opens a dedicated read-only handle.
    """
    global _CONN_RO
    if read_only and _CONN is None:
        if _CONN_RO is None:
            _CONN_RO = duckdb.connect(DB_PATH, read_only=True)
        yield _CONN_RO
    else:
        yield _rw()


def close() -> None:
    """Close cached connections (call at the end of a build)."""
    global _CONN, _CONN_RO
    if _CONN is not None:
        _CONN.close()
        _CONN = None
    if _CONN_RO is not None:
        _CONN_RO.close()
        _CONN_RO = None


def init_schema() -> None:
    with connection() as conn:
        conn.execute(_DDL)


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Raw record upserts
# ---------------------------------------------------------------------------

def upsert_raw(table: str, id_: str, source_url: str, fetched_at: str, raw: dict) -> None:
    sql = f"""
        INSERT INTO {table} (id, source_url, fetched_at, raw_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            source_url = excluded.source_url,
            fetched_at = excluded.fetched_at,
            raw_json   = excluded.raw_json
    """
    with connection() as conn:
        conn.execute(sql, [id_, source_url, fetched_at, _j(raw)])


def upsert_raw_batch(table: str, records: list[tuple[str, str, str, dict]]) -> None:
    """records: list of (id, source_url, fetched_at, raw_json_dict)"""
    if not records:
        return
    sql = f"""
        INSERT INTO {table} (id, source_url, fetched_at, raw_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            source_url = excluded.source_url,
            fetched_at = excluded.fetched_at,
            raw_json   = excluded.raw_json
    """
    rows = [(r[0], r[1], r[2], _j(r[3])) for r in records]
    with connection() as conn:
        conn.executemany(sql, rows)


# ---------------------------------------------------------------------------
# Node upserts
# ---------------------------------------------------------------------------

def upsert_unc_unit(id_: str, canonical_name: str, short_name: str | None,
                    ror_id: str | None, parent_id: str | None,
                    aliases: list[str], topic_keywords: list[str]) -> None:
    sql = """
        INSERT INTO nodes_unc_units
            (id, canonical_name, short_name, ror_id, parent_id, aliases, topic_keywords)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            short_name     = excluded.short_name,
            ror_id         = excluded.ror_id,
            parent_id      = excluded.parent_id,
            aliases        = excluded.aliases,
            topic_keywords = excluded.topic_keywords
    """
    with connection() as conn:
        conn.execute(sql, [id_, canonical_name, short_name, ror_id, parent_id,
                           _j(aliases), _j(topic_keywords)])


def upsert_faculty(id_: str, full_name: str, orcid: str | None,
                   unc_unit_id: str | None, department: str | None,
                   confidence: str) -> None:
    sql = """
        INSERT INTO nodes_faculty (id, full_name, orcid, unc_unit_id, department, confidence)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            full_name   = excluded.full_name,
            orcid       = COALESCE(excluded.orcid, nodes_faculty.orcid),
            unc_unit_id = COALESCE(excluded.unc_unit_id, nodes_faculty.unc_unit_id),
            department  = COALESCE(excluded.department, nodes_faculty.department),
            confidence  = CASE
                WHEN excluded.confidence = 'confirmed' THEN 'confirmed'
                ELSE nodes_faculty.confidence
            END
    """
    with connection() as conn:
        conn.execute(sql, [id_, full_name, orcid, unc_unit_id, department, confidence])


def upsert_company(id_: str, canonical_name: str, normalized_name: str,
                   sec_cik: str | None, ror_id: str | None, confidence: str) -> None:
    sql = """
        INSERT INTO nodes_companies
            (id, canonical_name, normalized_name, sec_cik, ror_id, confidence)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            canonical_name  = excluded.canonical_name,
            normalized_name = excluded.normalized_name,
            sec_cik         = COALESCE(excluded.sec_cik, nodes_companies.sec_cik),
            ror_id          = COALESCE(excluded.ror_id, nodes_companies.ror_id),
            confidence      = CASE
                WHEN excluded.confidence = 'confirmed' THEN 'confirmed'
                ELSE nodes_companies.confidence
            END
    """
    with connection() as conn:
        conn.execute(sql, [id_, canonical_name, normalized_name, sec_cik, ror_id, confidence])


# ---------------------------------------------------------------------------
# Edge upserts
# ---------------------------------------------------------------------------

def upsert_edge(id_: str, edge_type: str, source_node: str, target_node: str,
                faculty_id: str | None, record_id: str, source_table: str,
                source_url: str, fetched_at: str, record_date: str | None,
                confidence: str, amount_usd: int | None, metadata: dict) -> None:
    sql = """
        INSERT INTO edges
            (id, edge_type, source_node, target_node, faculty_id, record_id,
             source_table, source_url, fetched_at, record_date, confidence,
             amount_usd, edge_metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
    """
    with connection() as conn:
        conn.execute(sql, [id_, edge_type, source_node, target_node, faculty_id,
                           record_id, source_table, source_url, fetched_at,
                           record_date, confidence, amount_usd, _j(metadata)])


def upsert_edge_batch(rows: list[dict]) -> None:
    if not rows:
        return
    sql = """
        INSERT INTO edges
            (id, edge_type, source_node, target_node, faculty_id, record_id,
             source_table, source_url, fetched_at, record_date, confidence,
             amount_usd, edge_metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
    """
    params = [
        [r["id"], r["edge_type"], r["source_node"], r["target_node"],
         r.get("faculty_id"), r["record_id"], r["source_table"], r["source_url"],
         r["fetched_at"], r.get("record_date"), r["confidence"],
         r.get("amount_usd"), _j(r.get("edge_metadata", {}))]
        for r in rows
    ]
    with connection() as conn:
        conn.executemany(sql, params)


# ---------------------------------------------------------------------------
# Topic profiles
# ---------------------------------------------------------------------------

def upsert_topic_profile(unit_id: str, keywords: list[str],
                         concept_codes: list[str], updated_at: str) -> None:
    sql = """
        INSERT INTO topic_profiles (unit_id, keywords, concept_codes, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (unit_id) DO UPDATE SET
            keywords      = excluded.keywords,
            concept_codes = excluded.concept_codes,
            updated_at    = excluded.updated_at
    """
    with connection() as conn:
        conn.execute(sql, [unit_id, _j(keywords), _j(concept_codes), updated_at])


# ---------------------------------------------------------------------------
# Simple query helpers used by scripts/tests
# ---------------------------------------------------------------------------

def count_edges_by_unit() -> list[tuple[str, int]]:
    sql = """
        SELECT target_node, COUNT(*) AS cnt
        FROM edges
        GROUP BY target_node
        ORDER BY cnt DESC
    """
    with connection(read_only=True) as conn:
        return conn.execute(sql).fetchall()


def count_raw(table: str) -> int:
    with connection(read_only=True) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
