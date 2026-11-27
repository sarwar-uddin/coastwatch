"""Small Global Fishing Watch API v3 client.

Design choices:
- Every successful response is cached on disk (data/raw/gfw/) and reused, so
  re-running a fetch never spends the rate limit twice, and the raw data stays
  inspectable. Pass refresh=True to ignore the cache.
- 429 (rate limit / one-report-at-a-time) and 5xx/524 get exponential backoff.
- 4Wings reports may come back as a zip (data file + caveats); we unpack it.

Licence: GFW data is CC BY-NC 4.0: non-commercial use, with attribution
"Powered by Global Fishing Watch".
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from typing import Any

import httpx

from .config import GFW_BASE_URL, RAW_DIR

CACHE_DIR = RAW_DIR / "gfw"
RETRY_STATUSES = {429, 500, 502, 503, 504, 524}
# POST /v3/events answers 201 Created (not 200) even though it is a query.
OK_STATUSES = {200, 201}


@dataclass
class GfwResponse:
    status: int
    data: Any  # parsed JSON, or None
    text: str  # raw text, for error reporting
    cached: bool

    @property
    def ok(self) -> bool:
        return self.status in OK_STATUSES and self.data is not None


class GfwClient:
    def __init__(self, token: str, max_retries: int = 4):
        self._http = httpx.Client(
            base_url=GFW_BASE_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=180,
        )
        self.max_retries = max_retries

    def close(self) -> None:
        self._http.close()

    def request(
        self,
        method: str,
        path: str,
        params: dict | list | None = None,
        body: dict | None = None,
        refresh: bool = False,
    ) -> GfwResponse:
        key = _cache_key(method, path, params, body)
        cache_file = CACHE_DIR / f"{key}.json"
        if cache_file.exists() and not refresh:
            return GfwResponse(200, json.loads(cache_file.read_text()), "", cached=True)

        delay = 5.0
        for attempt in range(self.max_retries + 1):
            response = self._http.request(method, path, params=params, json=body)
            if response.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                break
            print(f"    GFW {response.status_code}, retrying in {delay:.0f}s…")
            time.sleep(delay)
            delay *= 2

        data = _parse_body(response)
        if response.status_code in OK_STATUSES and data is not None:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))
            (CACHE_DIR / f"{key}.request.json").write_text(
                json.dumps({"method": method, "path": path, "params": params, "body": body}, indent=2)
            )
        return GfwResponse(response.status_code, data, response.text[:2000], cached=False)

    # Convenience wrappers -------------------------------------------------

    def report(self, params: dict, refresh: bool = False) -> GfwResponse:
        """4Wings report (gridded fishing effort / presence / SAR detections)."""
        return self.request("GET", "/v3/4wings/report", params=params, refresh=refresh)

    def events_page(
        self, path: str, body: dict, offset: int, limit: int, refresh: bool = False
    ) -> GfwResponse:
        return self.request(
            "POST", path, params={"offset": offset, "limit": limit}, body=body, refresh=refresh
        )

    def all_events(self, path: str, body: dict, limit: int = 500) -> list[dict]:
        """Follow nextOffset until every page is fetched."""
        entries: list[dict] = []
        offset = 0
        while True:
            page = self.events_page(path, body, offset, limit)
            if not page.ok:
                raise RuntimeError(f"Events request failed ({page.status}): {page.text}")
            entries.extend(page.data.get("entries", []))
            next_offset = page.data.get("nextOffset")
            if not next_offset or next_offset <= offset:
                return entries
            offset = next_offset


def _cache_key(method: str, path: str, params: Any, body: Any) -> str:
    blob = json.dumps([method, path, params, body], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _parse_body(response: httpx.Response) -> Any:
    content = response.content
    if content[:2] == b"PK":  # zip archive
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".json"):
                    return json.loads(archive.read(name))
        return None
    try:
        return response.json()
    except ValueError:
        return None


def flatten_report_entries(data: Any) -> list[dict]:
    """4Wings JSON nests rows under the dataset name, e.g.
    {"entries": [{"public-global-fishing-effort:v5.0": [{...}, ...]}]}.
    Accept that, a flat list, or a bare list, and return plain rows."""
    if isinstance(data, dict):
        entries = data.get("entries", [])
    elif isinstance(data, list):
        entries = data
    else:
        return []

    rows: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict) and entry and all(isinstance(v, list) for v in entry.values()):
            for dataset_rows in entry.values():
                rows.extend(r for r in dataset_rows if isinstance(r, dict))
        elif isinstance(entry, dict):
            rows.append(entry)
    return rows
