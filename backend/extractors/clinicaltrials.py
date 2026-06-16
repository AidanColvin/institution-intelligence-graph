"""
ClinicalTrials.gov v2 extractor — trials where UNC-CH is sponsor, collaborator, or site.
Endpoint: https://clinicaltrials.gov/api/v2/studies
No API key required.

Valid query params (v2): query.spons (sponsor/collaborator), query.locn (location),
query.lead (lead sponsor). We use query.spons to catch UNC as lead OR collaborator,
and query.locn to catch UNC as a trial site.
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_API = "https://clinicaltrials.gov/api/v2/studies"
_PAGE_SIZE = 500
_STUDY_BASE = "https://clinicaltrials.gov/study/{nct_id}"

# Searches that surface UNC-CH involvement. query.spons matches sponsor + collaborator names.
_SPONSOR_QUERIES = [
    "University of North Carolina, Chapel Hill",
    "University of North Carolina at Chapel Hill",
    "UNC Lineberger Comprehensive Cancer Center",
]
_LOCATION_QUERIES = [
    "University of North Carolina, Chapel Hill",
    "UNC Hospitals",
]


class ClinicalTrialsExtractor(BaseExtractor):
    name = "clinicaltrials"
    min_interval = 0.4

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        seen: set[str] = set()
        for q in _SPONSOR_QUERIES:
            yield from self._paginate({"query.spons": q}, f"spons:{q}", seen)
        for q in _LOCATION_QUERIES:
            yield from self._paginate({"query.locn": q}, f"locn:{q}", seen)

    def _paginate(self, query: dict, ck_key: str, seen: set[str]) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        params = {**query, "pageSize": _PAGE_SIZE, "format": "json"}
        token = ck.get(ck_key)
        if token:
            params["pageToken"] = token
        logger.info("ClinicalTrials: %s token=%s", ck_key, token)

        while True:
            try:
                data = self.get_json(_API, params)
            except Exception as exc:
                logger.error("ClinicalTrials %s fetch failed: %s", ck_key, exc)
                ck[ck_key] = params.get("pageToken")
                self.save_checkpoint(ck)
                raise

            studies = data.get("studies", []) or []
            for study in studies:
                nct_id = (
                    study.get("protocolSection", {})
                    .get("identificationModule", {})
                    .get("nctId", "")
                )
                if not nct_id or nct_id in seen:
                    continue
                seen.add(nct_id)
                yield nct_id, _STUDY_BASE.format(nct_id=nct_id), study

            token = data.get("nextPageToken")
            if not token or not studies:
                ck.pop(ck_key, None)
                self.save_checkpoint(ck)
                break
            params["pageToken"] = token
            ck[ck_key] = token
            self.save_checkpoint(ck)
