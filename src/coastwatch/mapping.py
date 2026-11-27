"""`coastwatch map --year 2025`: one self-contained HTML map in maps/."""

from __future__ import annotations

import html
import json
from collections import defaultdict

import folium
from folium.plugins import HeatMap, MarkerCluster

from .config import EEZ_FILE, MAPS_DIR
from .summary import build_summary, load

ATTRIBUTION = (
    'Powered by <a href="https://globalfishingwatch.org" target="_blank">Global Fishing Watch</a> '
    '(CC BY-NC 4.0) · EEZ: <a href="https://www.marineregions.org" target="_blank">Marine Regions</a> '
    "(CC BY 4.0)"
)

COLOURS = {
    "eez": "#c8102e",
    "dark": "#ff3b3b",
    "matched": "#8a9bb0",
    "gaps": "#ff9f1c",
    "encounters": "#b56cff",
    "loitering": "#ffd23f",
    "fishing": "#2ec4b6",
}


def _cells(rows: list[dict], value_key: str) -> list[tuple[float, float, float]]:
    """Sum a value per grid cell across months and flags."""
    totals: dict[tuple[float, float], float] = defaultdict(float)
    for row in rows:
        totals[(row["lat"], row["lon"])] += row.get(value_key) or 0
    return [(lat, lon, value) for (lat, lon), value in totals.items() if value > 0]


def _event_popup(event: dict, label: str) -> str:
    v = event["vessel"]
    esc = lambda x: html.escape(str(x)) if x not in (None, "") else "—"  # noqa: E731
    lines = [
        f"<b>{esc(label)}</b>",
        f"Vessel: {esc(v.get('name'))} ({esc(v.get('flag'))})",
        f"MMSI: {esc(v.get('mmsi'))} · type: {esc(v.get('type'))}",
        f"{esc(event.get('start'))[:16]} → {esc(event.get('end'))[:16]}",
    ]
    if event.get("mpa"):
        lines.append("<b>Inside a marine protected area</b>")
    return "<br>".join(lines)


def build_map(year: int) -> str:
    if not EEZ_FILE.exists():
        raise SystemExit("Run `coastwatch eez` first to download the EEZ boundary.")

    m = folium.Map(location=[20.6, 90.6], zoom_start=7, tiles=None, control_scale=True)
    esri = "https://server.arcgisonline.com/ArcGIS/rest/services/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
    folium.TileLayer(esri.format("Canvas/World_Dark_Gray_Base"), name="Dark basemap",
                     attr="Basemap © Esri · " + ATTRIBUTION).add_to(m)
    folium.TileLayer(esri.format("Ocean/World_Ocean_Base"), name="Ocean basemap",
                     attr="Basemap © Esri, GEBCO, NOAA · " + ATTRIBUTION, show=False).add_to(m)

    folium.GeoJson(
        json.loads(EEZ_FILE.read_text()),
        name="Bangladesh EEZ",
        style_function=lambda _: {"color": COLOURS["eez"], "weight": 2, "fillOpacity": 0.03},
        tooltip="Bangladeshi Exclusive Economic Zone (MRGID 8481)",
    ).add_to(m)

    effort = _cells(load(year, "fishing_effort"), "hours")
    if effort:
        peak = max(v for *_, v in effort)
        HeatMap([(lat, lon, v / peak) for lat, lon, v in effort], name="Fishing effort (AIS)",
                radius=14, blur=18, min_opacity=0.25).add_to(m)

    # A year of radar passes is tens of thousands of detections: as circles it
    # becomes one blob, so show density instead. Red = dark, grey = all.
    gradients = {
        "sar_dark": {0.2: "#3a0008", 0.5: "#8b0015", 0.8: "#e0102a", 1.0: "#ff6b6b"},
        "sar_all": {0.2: "#20242c", 0.6: "#6b7a90", 1.0: "#dfe6ee"},
    }
    for name, label in (("sar_dark", "Dark vessels: radar, no AIS (density)"),
                        ("sar_all", "Radar detections, all (density)")):
        cells = _cells(load(year, name), "detections")
        if not cells:
            continue
        peak = max(v for *_, v in cells)
        # sqrt keeps a few huge anchorage cells from washing out the rest
        HeatMap([(lat, lon, (v / peak) ** 0.5) for lat, lon, v in cells], name=label,
                radius=12, blur=16, min_opacity=0.2, gradient=gradients[name],
                show=(name == "sar_dark")).add_to(m)

    labels = {"gaps": "AIS gap (transponder off)", "encounters": "Encounter (two vessels met)",
              "loitering": "Loitering", "fishing": "Fishing event"}
    for kind, label in labels.items():
        events = load(year, f"events_{kind}")
        if not events:
            continue
        # Rare events (gaps, encounters) stay as single markers; plentiful ones are
        # clustered and hidden by default so they don't bury the rare ones.
        plentiful = len(events) > 200
        group = folium.FeatureGroup(name=f"{label} ({len(events):,})", show=not plentiful)
        target = MarkerCluster().add_to(group) if plentiful else group
        for event in events:
            folium.CircleMarker(
                [event["lat"], event["lon"]], radius=7 if not plentiful else 5, color=COLOURS[kind], weight=1,
                fill=True, fill_opacity=0.85,
                popup=folium.Popup(_event_popup(event, label), max_width=280),
            ).add_to(target)
        group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.get_root().html.add_child(folium.Element(_panel(year)))

    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    out = MAPS_DIR / f"coastwatch-{year}.html"
    m.save(out)
    return str(out)


def _panel(year: int) -> str:
    s = build_summary(year)
    pct = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    suspect = s["likely_bgd_flags"]
    flags = "".join(
        f"<tr><td>{html.escape(f)}{'*' if f in suspect else ''}</td>"
        f"<td style='text-align:right'>{h:,} h</td></tr>"
        for f, h in s["fishing_hours_by_flag"][:6]
    )
    suspect_note = (
        f"<div style='color:#ff9f1c;font-size:10px;margin-bottom:6px'>* {', '.join(suspect)}: "
        "MMSIs are 405 (BGD) with digits transposed, so probably Bangladeshi. "
        f"Foreign share excluding them: <b>{pct(s['foreign_share_adjusted'])}</b></div>"
        if suspect else "")
    counts = s["event_counts"]
    return f"""
<div style="position:fixed;bottom:44px;left:12px;z-index:9999;width:270px;padding:12px 14px;
            background:rgba(10,10,12,.88);color:#e8e8e8;border:1px solid {COLOURS['eez']};
            font:12px/1.45 ui-monospace,monospace;border-radius:4px">
  <div style="font-size:14px;letter-spacing:.08em;color:{COLOURS['eez']}">COASTWATCH · {year}</div>
  <div style="margin:4px 0 8px;color:#9a9a9a">Bangladesh EEZ</div>
  <div>Fishing (AIS): <b>{s['fishing_hours_total']:,} h</b><br>
       foreign-flagged: <b>{pct(s['foreign_share_of_fishing_hours'])}</b></div>
  <table style="width:100%;margin:4px 0 4px;color:#c8c8c8">{flags}</table>
  {suspect_note}
  <div>Radar detections: <b>{s['sar_detections_total']:,}</b><br>
       <span style="color:{COLOURS['dark']}">no AIS match: <b>{s['sar_detections_dark']:,}</b>
       ({pct(s['dark_share_of_sar'])})</span></div>
  <div style="margin-top:6px">Gaps {counts['gaps']:,} · encounters {counts['encounters']:,} ·
       loitering {counts['loitering']:,}</div>
  <div style="margin-top:8px;color:#777;font-size:10px">Radar sees vessels over roughly 15–20 m;
       small wooden boats are mostly invisible to both radar and AIS.</div>
</div>"""
