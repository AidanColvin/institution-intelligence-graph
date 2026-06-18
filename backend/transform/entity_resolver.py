"""
Entity resolution for UNC org tree, companies, and faculty.

Resolution rules:
  - UNC units: alias table match (exact, then normalized). Never fuzzy.
  - Companies: normalize → SEC EDGAR CIK lookup (optional, slow). Confirmed = CIK found.
  - Faculty: ORCID primary key; fallback = sha256(last+first_initial+unit_id).
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_DATA = Path(__file__).parent.parent / "data"
_ORG_TREE_PATH = _DATA / "unc_org_tree.json"
_OVERRIDES_PATH = _DATA / "company_overrides.json"

# Legal suffix pattern for company normalization
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|llc\.?|corp\.?|co\.?|ltd\.?|plc\.?|lp\.?|l\.p\.?|"
    r"incorporated|corporation|limited|company)\b",
    re.IGNORECASE
)


# ------------------------------------------------------------------
# Text normalization helpers  (must come before _load_org_tree)
# ------------------------------------------------------------------

def _norm_text(s: str) -> str:
    """Lowercase, strip punctuation (except &), compress whitespace."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s&]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ------------------------------------------------------------------
# Load static data once at module import time
# ------------------------------------------------------------------

def _load_org_tree() -> tuple[dict, dict]:
    """Returns (tree_dict, alias_index).
    alias_index maps normalized_alias → unit_id.
    """
    tree = json.loads(_ORG_TREE_PATH.read_text())
    index: dict[str, str] = {}
    for unit_id, unit in tree.items():
        for alias in unit.get("aliases", []):
            index[_norm_text(alias)] = unit_id
    return tree, index


def _load_overrides() -> dict:
    try:
        return json.loads(_OVERRIDES_PATH.read_text())
    except FileNotFoundError:
        logger.warning(
            "entity_resolver: company_overrides.json not found at %s, "
            "using empty overrides",
            _OVERRIDES_PATH,
        )
        return {}
    except Exception as e:
        logger.error("entity_resolver: failed to load company_overrides: %s", e)
        return {}


_ORG_TREE, _ALIAS_INDEX = _load_org_tree()
_COMPANY_OVERRIDES = _load_overrides()


def normalize_company_name(name: str) -> str:
    """Apply overrides, strip legal suffixes, normalize text."""
    name = name.strip()
    lower = name.lower()

    # Check override table (already lowercased and stripped of suffixes)
    for pattern, canonical in _COMPANY_OVERRIDES.items():
        if pattern in lower:
            return canonical

    # Strip legal suffixes
    clean = _LEGAL_SUFFIXES.sub("", name).strip().rstrip(",").strip()
    return _norm_text(clean)


# ------------------------------------------------------------------
# UNC unit resolution
# ------------------------------------------------------------------

def resolve_unc_unit(text: str) -> Optional[str]:
    """
    Match a department/org text string to a UNC unit ID.
    Returns unit_id if matched, None otherwise.
    Uses exact normalized match only — no fuzzy matching.
    """
    if not text:
        return None
    normed = _norm_text(text)
    return _ALIAS_INDEX.get(normed)


def get_unit(unit_id: str) -> Optional[dict]:
    return _ORG_TREE.get(unit_id)


def all_units() -> list[dict]:
    return list(_ORG_TREE.values())


# ------------------------------------------------------------------
# Faculty ID construction
# ------------------------------------------------------------------

def faculty_id_from_orcid(orcid: str) -> str:
    """Canonical faculty ID when ORCID is known."""
    return f"orcid:{orcid.strip()}"


def faculty_id_from_name(last: str, first_initial: str, unit_id: str) -> str:
    """Probabilistic faculty ID when ORCID is absent."""
    key = f"{last.lower()}:{first_initial.lower()}:{unit_id}"
    h = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"faculty:{h}"


def parse_nih_pi(pi: dict) -> tuple[str, str, Optional[str], str]:
    """
    Extract (full_name, faculty_id, orcid, confidence) from a NIH PI dict.
    NIH PI shape: {"first_name": ..., "last_name": ..., "profile_id": ...,
                   "orcid": ...}
    """
    first = pi.get("first_name", "").strip()
    last = pi.get("last_name", "").strip()
    full_name = f"{first} {last}".strip() or pi.get("full_name", "Unknown PI")
    orcid = pi.get("orcid") or None
    if orcid:
        return full_name, faculty_id_from_orcid(orcid), orcid, "confirmed"
    first_initial = first[0] if first else "x"
    return full_name, faculty_id_from_name(last, first_initial, "unc:root"), None, "probable"


# ------------------------------------------------------------------
# Company ID construction
# ------------------------------------------------------------------

def company_id_from_cik(cik: str) -> str:
    return f"cik:{cik.zfill(10)}"


def company_id_from_name(normalized: str) -> str:
    h = hashlib.sha256(normalized.encode()).hexdigest()[:12]
    return f"co:{h}"


# ------------------------------------------------------------------
# Company → CIK resolution via SEC EDGAR (optional, slow)
# ------------------------------------------------------------------

_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index?q=%22{name}%22&dateRange=custom&startdt=2000-01-01&forms=10-K"
_EDGAR_COMPANY = "https://www.sec.gov/cgi-bin/browse-edgar?company={name}&CIK=&type=10-K&dateb=&owner=include&count=5&search_text=&action=getcompany&output=atom"

def resolve_company_cik(normalized_name: str, session: requests.Session | None = None) -> Optional[str]:
    """
    Try to find SEC CIK for a normalized company name.
    Returns CIK string or None. Very best-effort; failures are silently swallowed.
    Rate-limited: call sparingly (once per company during build, not per query).
    """
    try:
        s = session or requests.Session()
        s.headers.setdefault("User-Agent", "UNC-Research-Graph/1.0 mailto:aidanacolvin@gmail.com")
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{requests.utils.quote(normalized_name)}%22&forms=10-K"
        resp = s.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        hits = resp.json().get("hits", {}).get("hits", [])
        if hits:
            # Extract CIK from the first hit's _source
            src = hits[0].get("_source", {})
            cik = src.get("entity_id") or src.get("file_num", "")
            if cik:
                return str(cik).lstrip("0")
    except Exception:
        pass
    return None
