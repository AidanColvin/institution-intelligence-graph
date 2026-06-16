"""
OpenAlex bulk snapshot extractor — authors only, filtered to UNC ROR.
Downloads from s3://openalex/data/authors/ (public, no AWS credentials needed).
Uses the public HTTP endpoint: https://openalex.s3.amazonaws.com/data/authors/

We stream the manifest to find JSONL files, download each, and filter inline.
Only authors whose last_known_institutions includes ROR 0130frc33 are kept.

This is Phase 8 (supplemental ORCID enrichment) and is designed to run once.
Estimated filtered output for UNC: ~5,000–20,000 authors.
"""
from __future__ import annotations
import gzip
import io
import json
import logging
from typing import Iterator

from .base import BaseExtractor, now_iso

logger = logging.getLogger(__name__)

_UNC_ROR = "https://ror.org/0130frc33"
_MANIFEST_URL = "https://openalex.s3.amazonaws.com/data/authors/manifest"
_S3_BASE = "https://openalex.s3.amazonaws.com/"
_ORCID_PAGE = "https://orcid.org/{orcid}"

# Number of S3 author parts to scan per run (each is ~100MB compressed).
# Set to None to scan all (slow). Set to a number during testing.
_MAX_PARTS: int | None = None


class OpenAlexAuthorsExtractor(BaseExtractor):
    name = "openalex"
    min_interval = 0.0   # streaming download; throttle is per S3 object, not per record

    def extract(self) -> Iterator[tuple[str, str, dict]]:
        ck = self.load_checkpoint()
        completed_parts: list[str] = ck.get("completed_parts", [])

        logger.info("Fetching OpenAlex authors manifest…")
        try:
            manifest_resp = self.get(_MANIFEST_URL)
            manifest = manifest_resp.json()
        except Exception as exc:
            logger.error("OpenAlex manifest fetch failed: %s", exc)
            raise

        parts = [e["url"] for e in manifest.get("entries", [])]
        logger.info("OpenAlex: %d author parts in manifest", len(parts))

        if _MAX_PARTS:
            parts = parts[:_MAX_PARTS]

        for i, part_url in enumerate(parts):
            if part_url in completed_parts:
                logger.info("OpenAlex: skipping already-processed part %d/%d", i + 1, len(parts))
                continue

            logger.info("OpenAlex: downloading part %d/%d: %s", i + 1, len(parts), part_url)
            yield from self._process_part(part_url)

            completed_parts.append(part_url)
            self.save_checkpoint({"completed_parts": completed_parts})

        self.clear_checkpoint()

    def _process_part(self, url: str) -> Iterator[tuple[str, str, dict]]:
        """Stream a gzipped JSONL author part and yield UNC-matching records."""
        try:
            resp = self._session.get(url, stream=True, timeout=120)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("OpenAlex part download failed %s: %s", url, exc)
            return

        fetched_at = now_iso()
        buf = b""
        with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as gz:
            for line in gz:
                line = line.strip()
                if not line:
                    continue
                try:
                    author = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Filter: keep only authors whose last_known_institution is UNC
                institutions = author.get("last_known_institutions", []) or []
                unc_match = any(
                    inst.get("ror") == _UNC_ROR
                    for inst in institutions
                )
                if not unc_match:
                    continue

                orcid_raw = author.get("orcid", "") or ""
                orcid = orcid_raw.replace("https://orcid.org/", "").strip() or None

                record_id = author.get("id", "").split("/")[-1]   # OpenAlex ID like 'A12345'
                if not record_id:
                    continue

                source_url = _ORCID_PAGE.format(orcid=orcid) if orcid else author.get("id", "")
                yield record_id, source_url, author
