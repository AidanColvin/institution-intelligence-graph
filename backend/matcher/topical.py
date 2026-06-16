"""
Mode 2 — Topical scorer.
Jaccard-style overlap between company keyword set and UNC unit topic profiles.
Company keywords are extracted from SEC SIC text + normalized company name tokens.
"""
from __future__ import annotations
import json
import math
import re
from typing import Any

from ..graph import store

_PUNCT = re.compile(r"[^\w\s]")
_STOPWORDS = frozenset([
    "the", "a", "an", "and", "or", "of", "in", "to", "for", "with",
    "on", "at", "by", "from", "is", "are", "was", "inc", "llc", "corp",
    "co", "ltd", "company", "group", "holdings", "international",
])


def _tokenize(text: str) -> set[str]:
    text = _PUNCT.sub(" ", text.lower())
    return {t for t in text.split() if len(t) >= 3 and t not in _STOPWORDS}


def _load_unit_keywords() -> dict[str, set[str]]:
    sql = "SELECT unit_id, keywords FROM topic_profiles"
    with store.connection(read_only=True) as conn:
        rows = conn.execute(sql).fetchall()
    return {
        uid: set(json.loads(kw) if isinstance(kw, str) else kw)
        for uid, kw in rows
    }


def score_company(company_name: str, sector_hint: str = "") -> list[dict[str, Any]]:
    """
    Compute topical overlap score for a company name against all UNC unit profiles.
    Returns [{unit_id, topical_score}] sorted descending.
    sector_hint: optional free-text sector description to broaden company keywords.
    """
    unit_profiles = _load_unit_keywords()
    if not unit_profiles:
        return []

    company_keywords = _tokenize(company_name + " " + sector_hint)

    results = []
    for unit_id, unit_kw in unit_profiles.items():
        if not unit_kw or not company_keywords:
            score = 0.0
        else:
            intersection = company_keywords & unit_kw
            score = len(intersection) / math.sqrt(len(company_keywords) * len(unit_kw))
        results.append({"unit_id": unit_id, "topical_score": round(score, 4)})

    results.sort(key=lambda x: -x["topical_score"])
    return results
