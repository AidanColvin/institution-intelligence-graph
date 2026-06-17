"""
Shared constants and helpers for the UNC partnership sector scan (Tiers 1-3 +
evidence passes).

No API keys, no LLM. All HTTP goes through RateLimitedClient (1 req/sec, retry
3x with exponential backoff on 429/503). HTML is parsed with the stdlib
html.parser so we add no scraping dependency (no BeautifulSoup).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# ── identity / anchors ──────────────────────────────────────────────────────
UNC_ROR_ID = "https://ror.org/0130frc33"
UNC_ROR_BARE = "0130frc33"
USER_AGENT = "UNC-Partnership-Scan/1.0 mailto:aidanacolvin@gmail.com"
# Some public JSON APIs (e.g. ClinicalTrials.gov) reject the mailto: UA above.
# A standard browser UA is used only for those public-data endpoints.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# ── source endpoints (all free, keyless, public) ────────────────────────────
NIH_SEARCH_URL = "https://api.reporter.nih.gov/v2/projects/search"
NIH_PROJECT_URL = "https://reporter.nih.gov/project-details/"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_AUTHORS_URL = "https://api.openalex.org/authors"
CLINICALTRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
CLINICALTRIALS_VIEW = "https://clinicaltrials.gov/study/"

# Tier 1 seed pages. Only research.unc.edu/units is reliably live; the other two
# are kept for completeness but are 404 at time of writing, so Tier 1 also seeds
# from the curated org tree (real, verified UNC schools) and never depends on a
# single page being up.
UNC_SEED_URLS = (
    "https://research.unc.edu/units/",
    "https://www.unc.edu/academics/schools-colleges/",
    "https://www.unc.edu/about/schools-and-colleges/",
)

# ── controlled vocabularies (named constants — no magic strings) ────────────
AREA_EVENTS = "Events"
AREA_SCHOLARSHIPS = "Scholarships"
AREA_TALENT = "Talent Pipeline"
AREA_PROGRAMS = "Programs"
AREA_RESEARCH_GRANT = "Research Grant"
AREA_CLINICAL_TRIAL = "Clinical Trial"

TIER_VERIFIED = "Verified"
TIER_REPORTED = "Reported"
TIER_INFERRED = "Inferred"

UNIT_SCHOOL = "School"
UNIT_DEPARTMENT = "Department"
UNIT_LAB = "Lab"
UNIT_CENTER = "Center"
UNIT_INSTITUTE = "Institute"

STATUS_ACTIVE = "Active"
STATUS_PAST = "Past"
STATUS_DISCUSSION = "In Discussion"
STATUS_LAPSED = "Lapsed"

RECURRING_ONE_TIME = "one-time"
RECURRING_ANNUAL = "annual"
RECURRING_ONGOING = "ongoing"

FUNDING_GRANT = "grant"
FUNDING_GIFT = "gift"
FUNDING_SPONSORSHIP = "sponsorship"
FUNDING_INKIND = "in-kind"
FUNDING_NONE = "none"

RESEARCH_BY = "automated-scan"

# Existing matching-graph edge_type -> partnership semantics. Lets the evidence
# pass turn already-collected, already-sourced company<->unit edges into honest
# partnership rows instead of re-fabricating them.
EDGE_TYPE_AREA = {
    "trial": AREA_CLINICAL_TRIAL,
    "grant": AREA_RESEARCH_GRANT,
    "paper": AREA_RESEARCH_GRANT,
    "contract": AREA_RESEARCH_GRANT,
    "patent": AREA_PROGRAMS,
}
EDGE_TYPE_TIER = {
    "trial": TIER_VERIFIED,
    "grant": TIER_VERIFIED,
    "paper": TIER_REPORTED,
    "contract": TIER_VERIFIED,
    "patent": TIER_REPORTED,
}
EDGE_TYPE_FUNDING = {
    "trial": FUNDING_SPONSORSHIP,
    "grant": FUNDING_GRANT,
    "paper": FUNDING_NONE,
    "contract": FUNDING_GRANT,
    "patent": FUNDING_NONE,
}

# Pass A web-scrape signal keywords (lowercased).
PARTNERSHIP_KEYWORDS = (
    "sponsor", "partner", "funded by", "supported by", "in collaboration with",
    "scholarship", "fellowship", "named gift", "internship", "recruiting",
    "donor", "gift from", "endowed by",
)
NAMED_AWARD_KEYWORDS = ("scholarship", "fellowship", "award", "prize")
PROGRAM_KEYWORDS = ("program", "initiative", "academy", "institute")

# ── rate limiting / retry ───────────────────────────────────────────────────
RATE_LIMIT_SECONDS = 1.0          # 1 req/sec per source (mission requirement)
RETRY_MAX = 3
RETRY_STATUS = frozenset({429, 503})
BACKOFF_BASE_SECONDS = 1.0        # backoff = BACKOFF_BASE * 2**attempt


def iso_now() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    """Return today's date as YYYY-MM-DD (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def make_id(prefix: str, *parts: Any) -> str:
    """
    Build a stable, collision-resistant id from a prefix and any number of parts.

    Args:
        prefix: short namespace, e.g. "pp" (partnership) or "fac".
        parts: values hashed together to form the suffix.
    Returns:
        "<prefix>:<12-hex-char digest>".
    """
    raw = "|".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def norm_company(name: str) -> str:
    """
    Normalize a company name for matching: lowercase, drop legal suffixes and
    punctuation, collapse whitespace.

    Args:
        name: raw company name.
    Returns:
        Normalized token string ("" if name is falsy).
    """
    if not name:
        return ""
    s = name.lower()
    s = re.sub(r"\b(inc|llc|corp|co|ltd|plc|lp|incorporated|corporation|limited|company)\b\.?", " ", s)
    s = re.sub(r"[^\w\s&]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── HTML parsing (stdlib only) ──────────────────────────────────────────────

class _LinkTextParser(HTMLParser):
    """Collect (href, anchor_text) pairs and visible page text from raw HTML."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._text_parts: list[str] = []
        self._href: str | None = None
        self._anchor: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            self._href = href
            self._anchor = []
        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._text_parts.append(alt)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._anchor).strip()))
            self._href = None
            self._anchor = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        self._text_parts.append(text)
        if self._href is not None:
            self._anchor.append(text)

    def text(self) -> str:
        return " ".join(self._text_parts)


def parse_html(html: str, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Parse raw HTML into visible text and absolute links.

    Args:
        html: raw HTML markup.
        base_url: URL the markup came from (for resolving relative hrefs).
    Returns:
        (visible_text, [(absolute_url, anchor_text), ...]).
    """
    if not html:
        return "", []
    parser = _LinkTextParser()
    try:
        parser.feed(html)
    except (AssertionError, ValueError) as exc:
        logger.warning("HTML parse failed for %s: %s", base_url, exc)
        return parser.text(), []
    links: list[tuple[str, str]] = []
    for href, anchor in parser.links:
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append((urljoin(base_url, href), anchor))
    return parser.text(), links


def same_host(url: str, host: str) -> bool:
    """Return True if `url`'s host equals or is a subdomain of `host`."""
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return netloc == host or netloc.endswith("." + host)


def scan_for_partnerships(text: str, company_names: dict[str, str],
                          page_url: str) -> list[dict]:
    """
    Find honest Pass-A partnership signals in scraped page text.

    Only emits a signal when a KNOWN company name (from the matching graph)
    appears within a short window of a partnership keyword on a real UNC page —
    so nothing is invented. Tier is Reported when a named award/program word is
    adjacent, otherwise Inferred (name + keyword proximity).

    Args:
        text: visible page text.
        company_names: {normalized_name: canonical_name} of known companies.
        page_url: the source page (becomes source_url).
    Returns:
        List of partial partnership dicts (company_name, area, verification_tier,
        description, source_url).
    """
    if not text or not company_names:
        return []
    low = text.lower()
    signals: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for norm, canonical in company_names.items():
        if len(norm) < 4:               # skip ultra-short names (false positives)
            continue
        idx = low.find(norm)
        if idx < 0:
            continue
        window = low[max(0, idx - 120): idx + len(norm) + 120]
        if not any(kw in window for kw in PARTNERSHIP_KEYWORDS):
            continue
        if any(kw in window for kw in NAMED_AWARD_KEYWORDS):
            area, tier = AREA_SCHOLARSHIPS, TIER_REPORTED
        elif any(kw in window for kw in ("intern", "recruit", "career", "pipeline")):
            area, tier = AREA_TALENT, TIER_INFERRED
        elif any(kw in window for kw in PROGRAM_KEYWORDS):
            area, tier = AREA_PROGRAMS, TIER_INFERRED
        else:
            area, tier = AREA_PROGRAMS, TIER_INFERRED
        key = (canonical, area)
        if key in seen:
            continue
        seen.add(key)
        snippet = re.sub(r"\s+", " ", window).strip()[:240]
        signals.append({
            "company_name": canonical,
            "area": area,
            "verification_tier": tier,
            "description": f"…{snippet}…",
            "source_url": page_url,
        })
    return signals


# ── rate-limited async HTTP ─────────────────────────────────────────────────

class RateLimitedClient:
    """
    Async httpx client that throttles to one request per `min_interval` seconds
    and retries 429/503 (and transport errors) up to RETRY_MAX times with
    exponential backoff. Use as an async context manager.
    """

    def __init__(self, min_interval: float = RATE_LIMIT_SECONDS,
                 timeout: float = 30.0, headers: dict[str, str] | None = None) -> None:
        self._min_interval = min_interval
        self._timeout = timeout
        self._headers = {"User-Agent": USER_AGENT, **(headers or {})}
        self._client: httpx.AsyncClient | None = None
        self._last = 0.0

    async def __aenter__(self) -> "RateLimitedClient":
        self._client = httpx.AsyncClient(
            timeout=self._timeout, headers=self._headers, follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response | None:
        """Issue one throttled request with retry. Returns None on final failure."""
        if self._client is None:
            raise RuntimeError("RateLimitedClient used outside its async context")
        for attempt in range(RETRY_MAX):
            await self._throttle()
            try:
                resp = await self._client.request(method, url, **kwargs)
                if resp.status_code in RETRY_STATUS:
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}", request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                return resp
            except (httpx.HTTPError, httpx.TransportError) as exc:
                if attempt == RETRY_MAX - 1:
                    logger.warning("%s %s failed after %d tries: %s", method, url, RETRY_MAX, exc)
                    return None
                await asyncio.sleep(BACKOFF_BASE_SECONDS * (2 ** attempt))
        return None

    async def get_text(self, url: str) -> str | None:
        """GET a page and return its text, or None if it could not be fetched."""
        resp = await self._request("GET", url)
        return resp.text if resp is not None else None

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any | None:
        """GET JSON, or None on failure / non-JSON body."""
        resp = await self._request("GET", url, params=params)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning("non-JSON response from %s: %s", url, exc)
            return None

    async def post_json(self, url: str, body: dict[str, Any]) -> Any | None:
        """POST a JSON body and return parsed JSON, or None on failure."""
        resp = await self._request("POST", url, json=body)
        if resp is None:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning("non-JSON response from %s: %s", url, exc)
            return None


def load_org_tree() -> dict[str, dict]:
    """
    Load the curated UNC org tree (16 real, verified schools/centers).

    Returns:
        Mapping of unit_id -> unit dict (id, canonical_name, short_name,
        parent_id, aliases).
    Raises:
        FileNotFoundError: if the curated tree is missing.
    """
    path = Path(__file__).parent.parent / "data" / "unc_org_tree.json"
    if not path.exists():
        raise FileNotFoundError(f"curated org tree not found at {path}")
    import json
    return json.loads(path.read_text())


def known_company_names() -> dict[str, str]:
    """
    Load known companies from the matching graph for honest Pass-A name matching.

    Returns:
        {normalized_name: canonical_name}. Empty dict if the table is absent.
    """
    from backend.graph import store
    try:
        with store.connection(read_only=True) as conn:
            rows = conn.execute(
                "SELECT canonical_name, normalized_name FROM nodes_companies"
            ).fetchall()
    except (RuntimeError, ValueError) as exc:
        logger.warning("could not load known companies: %s", exc)
        return {}
    out: dict[str, str] = {}
    for canonical, norm in rows:
        key = norm or norm_company(canonical)
        if key:
            out[key] = canonical
    return out
