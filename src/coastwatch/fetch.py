"""`coastwatch fetch --year 2025`: pull a year of GFW data for the Bangladesh EEZ.

Writes normalised JSON to data/processed/<year>/:
  fishing_effort.json  AIS-based apparent fishing hours per grid cell, month, flag
  sar_all.json         Sentinel-1 radar vessel detections per cell and month
  sar_dark.json        …the subset not matched to any AIS broadcast ("dark")
  events_<kind>.json   fishing / gaps / encounters / loitering events
"""

from __future__ import annotations

import json
from datetime import date

from .config import BANGLADESH_EEZ_MRGID, GFW_EEZ_DATASET, PROBE_FILE, PROCESSED_DIR, gfw_token
from .gfw import GfwClient, flatten_report_entries

EVENT_DATASETS = {
    "fishing": "public-global-fishing-events:latest",
    "gaps": "public-global-gaps-events:latest",
    "loitering": "public-global-loitering-events:latest",
    # "encounters" comes from the probe (the docs give two spellings)
}


def _load_probe() -> dict:
    if not PROBE_FILE.exists():
        raise SystemExit("Run `coastwatch probe` first: it confirms the API details fetch relies on.")
    probe = json.loads(PROBE_FILE.read_text())
    if not probe.get("report_ok") or not probe.get("events"):
        raise SystemExit(f"The probe did not find working report/events settings; see {PROBE_FILE}")
    return probe


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _report_rows(client: GfwClient, dataset: str, year: int, filters: str | None = None) -> list[dict]:
    end = min(date(year + 1, 1, 1), date.today())
    params = {
        "datasets[0]": dataset,
        "date-range": f"{year}-01-01,{end.isoformat()}",
        "temporal-resolution": "MONTHLY",
        "spatial-resolution": "LOW",
        "group-by": "FLAG",
        "format": "JSON",
        "region-id": BANGLADESH_EEZ_MRGID,
        "region-dataset": GFW_EEZ_DATASET,
    }
    if filters:
        params["filters[0]"] = filters
    response = client.report(params)
    if not response.ok:
        raise RuntimeError(f"Report {dataset} failed ({response.status}): {response.text}")

    rows = []
    for row in flatten_report_entries(response.data):
        lat, lon = _num(row.get("lat")), _num(row.get("lon"))
        if lat is None or lon is None:
            continue
        rows.append({
            "lat": lat,
            "lon": lon,
            "date": row.get("date"),
            "flag": row.get("flag") or None,
            "hours": _num(row.get("hours")),
            "detections": _num(row.get("detections")),
        })
    return rows


def _normalise_event(event: dict) -> dict | None:
    position = event.get("position") or {}
    lat, lon = _num(position.get("lat")), _num(position.get("lon"))
    if lat is None or lon is None:
        return None
    vessel = event.get("vessel") or {}
    normalised = {
        "id": event.get("id"),
        "type": event.get("type"),
        "start": event.get("start"),
        "end": event.get("end"),
        "lat": lat,
        "lon": lon,
        "vessel": {
            "id": vessel.get("id"),
            "name": vessel.get("name"),
            "flag": vessel.get("flag"),
            "mmsi": vessel.get("ssvid"),
            "type": vessel.get("type"),
        },
        "distances": event.get("distances"),
        "mpa": (event.get("regions") or {}).get("mpa") or [],
    }
    # Type-specific detail (gap duration, the other vessel in an encounter…)
    # lives in a sub-object named after the type. Keep it as-is.
    for key in ("gap", "encounter", "loitering", "fishing"):
        if key in event:
            normalised[key] = event[key]
    return normalised


def run_fetch(year: int) -> None:
    probe = _load_probe()
    events_cfg = probe["events"]
    if not events_cfg.get("mrgid_confirmed"):
        print("! Warning: the probe couldn't confirm the events were limited to the Bangladesh "
              "EEZ; check data/probe.json.")

    out_dir = PROCESSED_DIR / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = GfwClient(gfw_token())

    def save(name: str, rows: list) -> None:
        (out_dir / f"{name}.json").write_text(json.dumps(rows))
        print(f"   {name}: {len(rows):,} rows")

    try:
        print(f"Gridded reports for {year} (one at a time; GFW allows one running report per user)…")
        save("fishing_effort", _report_rows(client, "public-global-fishing-effort:latest", year))
        save("sar_all", _report_rows(client, "public-global-sar-presence:latest", year))
        if probe.get("sar_dark_filter"):
            save("sar_dark", _report_rows(
                client, "public-global-sar-presence:latest", year, probe["sar_dark_filter"]))

        datasets = dict(EVENT_DATASETS)
        if probe.get("encounters_dataset"):
            datasets["encounters"] = probe["encounters_dataset"]

        print("Events…")
        end = min(date(year + 1, 1, 1), date.today()).isoformat()
        for kind, dataset in datasets.items():
            body = {
                "datasets": [dataset],
                "startDate": f"{year}-01-01",
                "endDate": end,
                **events_cfg["region_body"],
            }
            try:
                raw = client.all_events(events_cfg["path"], body)
            except RuntimeError as error:
                print(f"   {kind}: skipped: {error}")
                continue
            save(f"events_{kind}", [e for e in map(_normalise_event, raw) if e])
    finally:
        client.close()

    print(f"Done → {out_dir}")
