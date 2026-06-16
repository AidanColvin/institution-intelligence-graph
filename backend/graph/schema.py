"""
Pydantic models for the UNC research graph.
All nodes and edges use string IDs with a type prefix.
"""
from __future__ import annotations
from typing import Any, Literal, Optional
from pydantic import BaseModel


Confidence = Literal["confirmed", "probable"]
EdgeType = Literal["grant", "paper", "trial", "contract", "patent"]


class RawRecord(BaseModel):
    id: str
    source: str                 # 'nih' | 'nsf' | 'usaspending' | 'clinicaltrials' | 'crossref' | 'ror'
    source_url: str
    fetched_at: str             # ISO 8601
    raw_json: dict[str, Any]


class UNCUnit(BaseModel):
    id: str                     # 'unc:gillings', 'unc:som', ...
    canonical_name: str
    short_name: Optional[str] = None
    ror_id: Optional[str] = None
    parent_id: Optional[str] = None   # e.g. 'unc:root'
    aliases: list[str] = []
    topic_keywords: list[str] = []    # populated by profiler


class Faculty(BaseModel):
    id: str                     # 'orcid:0000-...' or 'faculty:<hash>'
    full_name: str
    orcid: Optional[str] = None
    unc_unit_id: Optional[str] = None
    department: Optional[str] = None
    confidence: Confidence = "probable"


class Company(BaseModel):
    id: str                     # 'cik:<cik>' (confirmed) or 'co:<hash>' (probable)
    canonical_name: str
    normalized_name: str        # lowercase, legal suffixes stripped
    sec_cik: Optional[str] = None
    ror_id: Optional[str] = None
    confidence: Confidence = "probable"


class Edge(BaseModel):
    id: str                     # sha256(edge_type+source+target+record_id)
    edge_type: EdgeType
    source_node: str            # company id or unc unit id
    target_node: str            # unc unit id
    faculty_id: Optional[str] = None
    record_id: str
    source_table: str           # which raw_* table
    source_url: str
    fetched_at: str
    record_date: Optional[str] = None   # fiscal year / pub year / trial start
    confidence: Confidence
    amount_usd: Optional[int] = None
    edge_metadata: dict[str, Any] = {}


class TopicProfile(BaseModel):
    unit_id: str
    keywords: list[str]         # top-50 by TF-IDF
    concept_codes: list[str] = []   # MeSH codes
    updated_at: str
