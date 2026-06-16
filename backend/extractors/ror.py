"""
ROR org tree extractor — fetch UNC-CH's ROR entry and related org metadata.
Endpoint: https://api.ror.org/organizations/{ror_id}
No API key required.
"""
from __future__ import annotations
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_API = "https://api.ror.org/organizations/{ror_id}"
_ROR_BASE = "https://ror.org/{ror_id}"
_UNC_ROR = "0130frc33"


class RORExtractor(BaseExtractor):
    name = "ror"
    min_interval = 0.5

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        """Fetch UNC-CH's ROR record (and any related orgs listed in it)."""
        fetched_at = now_iso()

        url = _API.format(ror_id=_UNC_ROR)
        source_url = _ROR_BASE.format(ror_id=_UNC_ROR)
        try:
            data = self.get_json(url)
        except Exception as exc:
            logger.error("ROR fetch failed for %s: %s", _UNC_ROR, exc)
            raise

        yield _UNC_ROR, source_url, data

        # Also fetch any related orgs (campus hospitals, affiliates) listed in ROR
        for rel in data.get("relationships", []) or []:
            rel_ror = rel.get("id", "").replace("https://ror.org/", "")
            if not rel_ror:
                continue
            rel_url = _API.format(ror_id=rel_ror)
            rel_source = _ROR_BASE.format(ror_id=rel_ror)
            try:
                rel_data = self.get_json(rel_url)
                yield rel_ror, rel_source, rel_data
            except Exception as exc:
                logger.warning("ROR related org %s fetch failed: %s", rel_ror, exc)
