"""
BaseExtractor: shared HTTP session, rate-limiting, retry, and checkpoint cursor.
Every extractor subclasses this. No API keys — only public endpoints.
"""
from __future__ import annotations
import json
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import requests

logger = logging.getLogger(__name__)

# Checkpoint directory (one JSON file per extractor)
_CHECKPOINT_DIR = Path(__file__).parent.parent.parent / ".checkpoints"
_CHECKPOINT_DIR.mkdir(exist_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BaseExtractor(ABC):
    name: str = "base"
    # seconds between requests (overridden per-extractor for rate-sensitive APIs)
    min_interval: float = 0.35

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "UNC-Research-Graph/1.0 mailto:aidanacolvin@gmail.com"
        })
        self._last_request: float = 0.0
        self._checkpoint_path = _CHECKPOINT_DIR / f"{self.name}.json"

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def get(self, url: str, params: dict | None = None, **kwargs) -> requests.Response:
        self._throttle()
        resp = self._session.get(url, params=params, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def post(self, url: str, json_body: dict, **kwargs) -> requests.Response:
        self._throttle()
        resp = self._session.post(url, json=json_body, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.min_interval - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get_json(self, url: str, params: dict | None = None, retries: int = 3) -> Any:
        for attempt in range(retries):
            try:
                return self.get(url, params).json()
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("GET %s failed (%s), retry in %ds", url, exc, wait)
                time.sleep(wait)

    def post_json(self, url: str, body: dict, retries: int = 3) -> Any:
        for attempt in range(retries):
            try:
                return self.post(url, body).json()
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("POST %s failed (%s), retry in %ds", url, exc, wait)
                time.sleep(wait)

    # ------------------------------------------------------------------
    # Checkpoint cursor (simple JSON file per extractor)
    # ------------------------------------------------------------------

    def load_checkpoint(self) -> dict:
        if self._checkpoint_path.exists():
            return json.loads(self._checkpoint_path.read_text())
        return {}

    def save_checkpoint(self, data: dict) -> None:
        self._checkpoint_path.write_text(json.dumps(data, indent=2))

    def clear_checkpoint(self) -> None:
        if self._checkpoint_path.exists():
            self._checkpoint_path.unlink()

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    @abstractmethod
    def extract(self) -> Iterator[tuple[str, str, dict]]:
        """
        Yield (record_id, source_url, raw_json_dict) for each record.
        Implementations must handle pagination, rate-limiting, and
        checkpoint resumption internally.
        """
