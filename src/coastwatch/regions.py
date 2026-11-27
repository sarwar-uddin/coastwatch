"""Reference boundaries. For now: the Bangladesh EEZ from Marine Regions.

Marine Regions data is CC BY 4.0. Cite: Flanders Marine Institute (2023).
Maritime Boundaries Geodatabase, version 12. https://www.marineregions.org/
"""

from __future__ import annotations

import json

import httpx
from shapely.geometry import shape

from .config import BANGLADESH_EEZ_MRGID, EEZ_FILE

WFS_URL = "https://geo.vliz.be/geoserver/MarineRegions/wfs"


def download_eez(force: bool = False) -> dict:
    if EEZ_FILE.exists() and not force:
        return json.loads(EEZ_FILE.read_text())

    response = httpx.get(
        WFS_URL,
        params={
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": "MarineRegions:eez",
            "cql_filter": f"mrgid={BANGLADESH_EEZ_MRGID}",
            "outputFormat": "application/json",
        },
        timeout=120,
    )
    response.raise_for_status()
    geojson = response.json()
    if not geojson.get("features"):
        raise SystemExit(f"Marine Regions returned no EEZ for MRGID {BANGLADESH_EEZ_MRGID}")

    EEZ_FILE.parent.mkdir(parents=True, exist_ok=True)
    EEZ_FILE.write_text(json.dumps(geojson))
    return geojson


def eez_summary(geojson: dict) -> str:
    feature = geojson["features"][0]
    props = feature["properties"]
    geom = shape(feature["geometry"])
    minx, miny, maxx, maxy = geom.bounds
    return (
        f"{props.get('geoname')} (MRGID {props.get('mrgid')}): "
        f"{props.get('area_km2'):,} km², bounds lon {minx:.2f}–{maxx:.2f}, lat {miny:.2f}–{maxy:.2f}"
    )
