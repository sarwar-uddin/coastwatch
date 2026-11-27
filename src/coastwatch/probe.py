"""`coastwatch probe`: confirm the API details the docs left ambiguous.

We ask the live API instead of guessing, using one small month:
  1. Does a 4Wings report accept region-dataset=public-eez-areas with the
     Marine Regions MRGID (8481) as region-id?
  2. Is the Events path /v3/events or /api/v3/events?
     (First run: /v3/events. It answers 201, not 200.)
  3. Which region body shape does POST /events honour?
     (First run: {"region": {...}} works. {"regionIds": [...]} is silently
     ignored and returns the whole world, so "HTTP OK" alone proves nothing.
     We check that the response metadata names our region.)
  4. Is the encounters dataset "encounters" or "encounter"?
  5. Does the SAR `matched` filter really filter? Compare against the
     unfiltered total: unmatched + matched should add up to all.
Results go to data/probe.json; `coastwatch fetch` reads them.
"""

from __future__ import annotations

import json

from .config import BANGLADESH_EEZ_MRGID, GFW_EEZ_DATASET, PROBE_FILE, gfw_token
from .gfw import GfwClient, flatten_report_entries

START, END = "2025-01-01", "2025-02-01"
EVENTS_PATHS = ["/v3/events", "/api/v3/events"]
REGION_BODIES = {
    "region-object": {"region": {"dataset": GFW_EEZ_DATASET, "id": BANGLADESH_EEZ_MRGID}},
    "region-ids": {"regionDatasets": [GFW_EEZ_DATASET], "regionIds": [str(BANGLADESH_EEZ_MRGID)]},
}
ENCOUNTER_IDS = ["public-global-encounters-events:latest", "public-global-encounter-events:latest"]
SAR_DARK_FILTERS = ["matched='false'", "matched in ('false')"]
SAR_MATCHED_FILTER = "matched='true'"


def _report_params(dataset: str, filters: str | None = None) -> dict:
    params = {
        "datasets[0]": dataset,
        "date-range": f"{START},{END}",
        "temporal-resolution": "ENTIRE",
        "spatial-resolution": "LOW",
        "group-by": "FLAG",
        "format": "JSON",
        "region-id": BANGLADESH_EEZ_MRGID,
        "region-dataset": GFW_EEZ_DATASET,
    }
    if filters:
        params["filters[0]"] = filters
    return params


def _region_in_metadata(data: dict) -> dict | None:
    """The events response echoes the region geometry it applied in
    metadata.geometry. Return that feature's properties if it is Bangladesh."""
    geometry = (data.get("metadata") or {}).get("geometry") or {}
    for feature in geometry.get("features", []):
        props = feature.get("properties") or {}
        mrgid = props.get("MRGID") or props.get("mrgid")
        if str(mrgid) == str(BANGLADESH_EEZ_MRGID) or props.get("ISO3") == "BGD":
            return {k: props.get(k) for k in ("MRGID", "mrgid", "GEONAME", "ISO3", "AREA_KM2") if k in props}
    return None


def _sar_detections(client: GfwClient, filters: str | None) -> tuple[int | None, int]:
    r = client.report(_report_params("public-global-sar-presence:latest", filters), refresh=True)
    if not r.ok:
        print(f"     HTTP {r.status}: {r.text[:200]}")
        return None, 0
    rows = flatten_report_entries(r.data)
    return round(sum(row.get("detections") or 0 for row in rows)), len(rows)


def run_probe() -> dict:
    client = GfwClient(gfw_token(), max_retries=3)
    result: dict = {"checked_range": [START, END]}
    try:
        # 1. 4Wings report scoped to the EEZ -------------------------------
        print("1. 4Wings report, fishing effort in EEZ 8481…")
        r = client.report(_report_params("public-global-fishing-effort:latest"), refresh=True)
        rows = flatten_report_entries(r.data) if r.ok else []
        print(f"   HTTP {r.status}, {len(rows)} grid cells")
        if not r.ok:
            print(f"   body: {r.text[:500]}")
        result["report_ok"] = r.ok
        result["report_sample_keys"] = sorted(rows[0].keys()) if rows else []

        # 2 + 3. Events path and region body shape --------------------------
        print("2/3. Events path and region filter…")
        result["events"] = None
        for path in EVENTS_PATHS:
            for shape_name, region_body in REGION_BODIES.items():
                body = {"datasets": ["public-global-fishing-events:latest"],
                        "startDate": START, "endDate": END, **region_body}
                page = client.events_page(path, body, offset=0, limit=5, refresh=True)
                if not page.ok:
                    print(f"   {path} [{shape_name}] → HTTP {page.status}")
                    continue
                region = _region_in_metadata(page.data)
                total = page.data.get("total")
                print(f"   {path} [{shape_name}] → HTTP {page.status}, total={total}, "
                      f"region applied={'yes ' + json.dumps(region) if region else 'NO (global results)'}")
                if region and result["events"] is None:
                    result["events"] = {
                        "path": path,
                        "region_shape": shape_name,
                        "region_body": region_body,
                        "total_in_probe_month": total,
                        "region_metadata": region,
                        "mrgid_confirmed": True,
                    }

        # 4. Encounters dataset id ------------------------------------------
        print("4. Encounters dataset id…")
        result["encounters_dataset"] = None
        if result["events"]:
            ev = result["events"]
            for dataset in ENCOUNTER_IDS:
                body = {"datasets": [dataset], "startDate": START, "endDate": END, **ev["region_body"]}
                page = client.events_page(ev["path"], body, offset=0, limit=1, refresh=True)
                print(f"   {dataset} → HTTP {page.status}"
                      + (f", total={page.data.get('total')}" if page.ok else f": {page.text[:150]}"))
                if page.ok and result["encounters_dataset"] is None:
                    result["encounters_dataset"] = dataset

        # 5. SAR "dark" filter: does it really filter? -----------------------
        print("5. SAR detections: all vs matched vs unmatched…")
        all_det, all_cells = _sar_detections(client, None)
        matched_det, _ = _sar_detections(client, SAR_MATCHED_FILTER)
        print(f"   all: {all_det} detections in {all_cells} cells; matched to AIS: {matched_det}")
        result["sar_dark_filter"] = None
        for filters in SAR_DARK_FILTERS:
            dark_det, dark_cells = _sar_detections(client, filters)
            adds_up = None not in (all_det, matched_det, dark_det) and abs(
                (matched_det + dark_det) - all_det) <= max(2, all_det * 0.01)
            filtered = dark_det is not None and dark_det != all_det
            print(f"   {filters!r}: {dark_det} detections in {dark_cells} cells → "
                  f"{'filter works' if filtered and adds_up else 'NOT confirmed'}")
            if filtered and adds_up and result["sar_dark_filter"] is None:
                result["sar_dark_filter"] = filters
        result["sar_check"] = {"all": all_det, "matched": matched_det}
    finally:
        client.close()

    PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_FILE.write_text(json.dumps(result, indent=2))
    print(f"\nSaved {PROBE_FILE}")
    print(json.dumps(result, indent=2))
    return result
